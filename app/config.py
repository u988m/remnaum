from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Remnaum"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    app_secret_key: str = "development-only-change-me"
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://remnaum:remnaum@localhost:5432/remnaum"
    redis_url: str = "redis://localhost:6379/0"
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = ""
    telegram_use_webhook: bool = False
    admin_ids: str = ""
    remnawave_base_url: str = ""
    remnawave_api_token: str = ""
    payment_provider: str = "stub"
    payment_webhook_secret: str = ""
    public_base_url: str = "http://localhost:8080"
    webapp_url: str = ""
    support_contact: str = ""
    tariff_min_days: int = 7
    tariff_max_days: int = 365
    tariff_min_traffic_gb: int = 10
    tariff_max_traffic_gb: int = 1000
    tariff_min_devices: int = 1
    tariff_max_devices: int = 10
    tariff_price_per_day_minor: int = 5
    tariff_price_per_gb_minor: int = 2
    tariff_price_per_device_minor: int = 100
    tariff_currency: str = "EUR"

    @property
    def admin_id_set(self) -> set[int]:
        return {int(value.strip()) for value in self.admin_ids.split(",") if value.strip().isdigit()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
