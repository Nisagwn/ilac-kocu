from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "İlaç Uyumu Koçu API"
    timezone: str = "Europe/Istanbul"  # ilaç saatleri bu saat diliminde yorumlanır
    database_url: str = "postgresql+psycopg://ilac:ilac@localhost:5432/ilac_kocu"

    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 15  # erişim token'ı kısa ömürlü; oturum yenileme token'ıyla sürer
    refresh_token_days: int = 14  # yenileme token'ı ömrü (her kullanımda döndürülür)

    # --- Giriş/kayıt istek sınırı ---
    login_ip_limit: int = 5  # IP başına, login_ip_window_seconds içinde
    login_ip_window_seconds: int = 60
    login_email_limit: int = 10  # e-posta başına, login_email_window_seconds içinde
    login_email_window_seconds: int = 900
    register_ip_limit: int = 5  # IP başına saatlik kayıt
    register_ip_window_seconds: int = 3600
    # X-Forwarded-For yalnızca bu adreslerden (IP ya da CIDR) gelen isteklerde dikkate alınır.
    # Varsayılan: aynı makinedeki Next.js sunucusu. Docker'da web konteynerinin ağı eklenmeli
    # (ör. ["172.16.0.0/12"]) ve API portu dışarıya açılmamalıdır.
    trusted_proxies: list[str] = ["127.0.0.1", "::1"]

    # --- API platform (app/middleware.py) ---
    # Token bucket: kimlik (giriş yapmışsa kullanıcı, değilse IP) başına `capacity` jetonluk kova,
    # saniyede `refill` jeton dolar. Redis varsa tüm API kopyaları ortak kovayı kullanır.
    api_rate_limit_enabled: bool = True
    api_rate_limit_capacity: int = 120
    api_rate_limit_refill_per_second: float = 2.0
    idempotency_ttl_hours: int = 24  # Idempotency-Key kayıtlarının saklanma süresi
    idempotency_max_body_bytes: int = 256_000  # bundan büyük yanıtlar saklanmaz (tekrar isteği yeniden işlenir)
    health_check_migrations: bool = True  # /health/ready veritabanının alembic head'de olmasını da ister

    # --- Devre kesiciler (app/services/circuit_breaker.py) ---
    # window_seconds içinde failure_threshold hata -> open_seconds boyunca istek atılmaz (hemen yedek yanıt)
    llm_breaker_failure_threshold: int = 5
    llm_breaker_window_seconds: int = 60
    llm_breaker_open_seconds: int = 30
    openfda_breaker_failure_threshold: int = 5
    openfda_breaker_window_seconds: int = 60
    openfda_breaker_open_seconds: int = 60

    openfda_base_url: str = "https://api.fda.gov/drug/label.json"
    openfda_api_key: str | None = None
    redis_url: str | None = None  # varsa openFDA etiket önbelleği Redis'te, yoksa süreç içi

    # --- M4: RAG (eğitim içeriği) ---
    # "local" (çevrimdışı, deterministik) | "voyage" (VOYAGE_API_KEY gerekir)
    # | "fastembed" (anlamsal, çok dilli; requirements-ml.txt gerekir, yoksa yerel embedding'e düşer)
    embedding_provider: str = "local"
    voyage_api_key: str | None = None
    voyage_model: str = "voyage-3.5-lite"
    fastembed_model: str = "intfloat/multilingual-e5-small"  # 384 boyut (vector(384) sütunu, migration yok)
    fastembed_cache_dir: str | None = None  # model dosyalarının indirileceği dizin (boş = fastembed varsayılanı)
    rag_min_score: float = 0.25  # bu hibrit skorun altındaki sonuçlar döndürülmez

    # --- Gözlemlenebilirlik (app/observability.py) ---
    metrics_enabled: bool = True  # /metrics (Prometheus); false ise 404
    # Worker metrikleri: worker süreci (koç LLM çağrıları burada) bu portta Prometheus sunar; yalnız iç ağ
    worker_metrics_port: int = 9101
    metrics_token: str | None = None  # tanımlıysa /metrics için "Authorization: Bearer <token>" zorunlu

    cors_origins: list[str] = ["http://localhost:3000"]

    # Web push (VAPID) — anahtar üretimi: README "VAPID anahtarı üretimi"
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:admin@example.com"
    reminders_enabled: bool = True  # doz hatırlatma zamanlayıcısı (VAPID anahtarları da gerekir)
    # Zamanlayıcı normalde kuyruk işçisinde (python -m app.worker) çalışır; tek süreçli geliştirme için True
    run_scheduler_in_api: bool = False

    # --- Asenkron koç işleri (POST /coach/jobs, app/worker.py) ---
    coach_job_stale_minutes: int = 10  # bu süreden uzun "running" kalan işler işçi açılışında kurtarılır
    coach_job_max_attempts: int = 3  # bu kadar denemeden sonra iş DLQ'ya (ölü mektup kuyruğu) düşer
    coach_job_retry_base_seconds: float = 2.0  # yeniden deneme beklemesi: taban * 2^(deneme-1)
    coach_job_retry_max_seconds: float = 60.0
    dlq_retention_days: int = 7  # DLQ'daki işlerin mesajı en fazla bu kadar tutulur (dlq purge)

    # --- Zamanlanmış işler (yalnızca lider işçi; app/worker.py) ---
    weekly_summary_enabled: bool = True  # Pazartesi 07:00 (TIMEZONE) haftalık özet + hekime bildirim
    weekly_summary_cron: str = "0 7 * * mon"  # dakika saat gün ay haftanın-günü
    leader_retry_seconds: int = 15  # lider olmayan işçi bu aralıkla liderliği dener

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
