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

    cors_origins: list[str] = ["http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()
