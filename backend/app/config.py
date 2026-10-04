from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "İlaç Uyumu Koçu API"
    timezone: str = "Europe/Istanbul"  # ilaç saatleri bu saat diliminde yorumlanır
    database_url: str = "postgresql+psycopg://ilac:ilac@localhost:5432/ilac_kocu"

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    openfda_base_url: str = "https://api.fda.gov/drug/label.json"
    openfda_api_key: str | None = None
    redis_url: str | None = None  # varsa openFDA etiket önbelleği Redis'te, yoksa süreç içi

    # --- M4: RAG (eğitim içeriği) ---
    embedding_provider: str = "local"  # "local" (çevrimdışı, deterministik) | "voyage" (VOYAGE_API_KEY gerekir)
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-3.5-lite"
    rag_min_score: float = 0.25  # bu hibrit skorun altındaki sonuçlar döndürülmez

    cors_origins: list[str] = ["http://localhost:3000"]

    # Web push (VAPID) — anahtar üretimi: README "VAPID anahtarı üretimi"
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:admin@example.com"
    reminders_enabled: bool = True  # doz hatırlatma zamanlayıcısı (VAPID anahtarları da gerekir)

    # --- M4: LLM koç ---
    # Sağlayıcı: "auto" (OPENROUTER_API_KEY varsa OpenRouter, yoksa ANTHROPIC_API_KEY varsa Anthropic),
    # "openrouter" ya da "anthropic"
    llm_provider: str = "auto"
    anthropic_api_key: str | None = None
    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Sağlık verisi: yalnızca istemleri saklamayan/eğitimde kullanmayan sağlayıcılara yönlendir ("allow" ile kapatılır)
    openrouter_data_collection: str = "deny"
    # "auto": anahtar varsa LLM, yoksa kural tabanlı; "claude": aynı (anahtar şart); "rule": daima kural tabanlı
    coach_provider: str = "auto"
    # Boşsa sağlayıcıya göre varsayılan: Anthropic "claude-opus-5-5", OpenRouter "anthropic/claude-sonnet-5.5"
    llm_model: str = ""
    llm_effort: str | None = "low"  # low|medium|high|xhigh|max; boş = model varsayılanı (Haiku 4.5'te boş bırakın)
    llm_max_tokens: int = 2048  # yanıt başına üst sınır (düşünme + metin)
    llm_max_tool_iterations: int = 4  # tool-use döngüsü en fazla bu kadar model çağrısı yapar
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 1
    llm_server_fallbacks: bool = True  # güvenlik reddinde sunucu tarafı yedek model (fallbacks="default")
    llm_history_messages: int = 6  # bağlam için gönderilen son koç sohbeti mesajı sayısı
    # Maliyet limitleri
    llm_daily_patient_requests: int = 30  # hasta başına günlük LLM koç isteği
    llm_daily_patient_tokens: int = 300_000  # hasta başına günlük token (girdi + önbellek yazma + çıktı)
    llm_daily_summary_requests: int = 3  # hasta başına günlük LLM özet üretimi
    llm_monthly_budget_usd: float = 50.0  # tüm LLM kullanımı için aylık tavan (USD)

    @property
    def vapid_configured(self) -> bool:
        return bool(self.vapid_public_key and self.vapid_private_key)

    @property
    def llm_backend(self) -> str | None:
        """Kullanılacak LLM sağlayıcısı ("openrouter" | "anthropic") ya da anahtar yoksa None."""
        p = self.llm_provider.lower()
        if p == "openrouter":
            return "openrouter" if self.openrouter_api_key else None
        if p == "anthropic":
            return "anthropic" if self.anthropic_api_key else None
        if self.openrouter_api_key:
            return "openrouter"
        if self.anthropic_api_key:
            return "anthropic"
        return None

    @property
    def model_name(self) -> str:
        if self.llm_model:
            return self.llm_model
        return "anthropic/claude-sonnet-5.5" if self.llm_backend == "openrouter" else "claude-opus-5-5"

    @property
    def llm_enabled(self) -> bool:
        return self.llm_backend is not None and self.coach_provider.lower() != "rule"


@lru_cache
def get_settings() -> Settings:
    return Settings()
