import re
from urllib.parse import urlsplit, urlunsplit

from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    app_name: str = "Remnaum"
    app_env: str = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8080
    app_secret_key: str = "development-only-change-me"
    admin_api_token: str = ""
    log_level: str = "INFO"
    database_url: str = "postgresql+asyncpg://remnaum:remnaum@localhost:5432/remnaum"
    redis_url: str = "redis://localhost:6379/0"
    telegram_bot_token: str = ""
    telegram_bot_username: str = ""
    telegram_webhook_url: str = ""
    telegram_webhook_secret: str = ""
    telegram_use_webhook: bool = False
    admin_ids: str = ""
    github_release_webhook_secret: str = ""
    remnawave_base_url: str = ""
    remnawave_api_token: str = ""
    remnawave_webhook_secret: str = ""
    referral_reward_days: int = Field(default=0, ge=0, le=365)
    remnawave_active_internal_squads: str = ""
    payment_provider: str = "stub"
    payment_webhook_secret: str = ""
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_currency: str = "RUB"
    yoomoney_receiver: str = ""
    yoomoney_notification_secret: str = ""
    cryptopay_api_token: str = ""
    cryptopay_api_base_url: str = "https://pay.crypt.bot/api"
    cryptopay_accepted_assets: str = "USDT,TON,BTC,ETH,TRX,USDC"
    telegram_terms_url: str = ""
    telegram_terms_version: str = "1"
    tariff_stars_price_per_day: int = Field(default=1, ge=0)
    tariff_stars_price_per_gb: int = Field(default=1, ge=0)
    tariff_stars_price_per_device: int = Field(default=10, ge=0)
    public_base_url: str = "http://localhost:8080"
    webapp_url: str = ""
    browser_webapp_url: str = ""
    support_contact: str = ""
    tariff_min_days: int = Field(default=7, ge=1)
    tariff_max_days: int = Field(default=365, ge=1)
    tariff_min_traffic_gb: int = Field(default=10, ge=1)
    tariff_max_traffic_gb: int = Field(default=1000, ge=1)
    tariff_min_devices: int = Field(default=1, ge=1)
    tariff_max_devices: int = Field(default=10, ge=1)
    tariff_price_per_day_minor: int = Field(default=500, ge=0)
    tariff_price_per_gb_minor: int = Field(default=10, ge=0)
    tariff_price_per_device_minor: int = Field(default=1000, ge=0)
    tariff_currency: str = Field(default="RUB", min_length=3, max_length=8)

    @model_validator(mode="after")
    def validate_tariff_ranges(self):
        ranges = (
            (self.tariff_min_days, self.tariff_max_days),
            (self.tariff_min_traffic_gb, self.tariff_max_traffic_gb),
            (self.tariff_min_devices, self.tariff_max_devices),
        )
        if any(minimum > maximum for minimum, maximum in ranges):
            raise ValueError("Tariff minimum values must not exceed maximum values")
        return self

    @model_validator(mode="after")
    def validate_production_secrets(self):
        if self.app_env.lower() in {"production", "prod"}:
            weak_secrets = {"development-only-change-me", "replace-with-a-long-random-secret"}
            if len(self.app_secret_key) < 32 or self.app_secret_key in weak_secrets:
                raise ValueError("APP_SECRET_KEY must be a strong unique secret in production")
            if self.telegram_use_webhook:
                if not re.fullmatch(r"[A-Za-z0-9_-]{24,256}", self.telegram_webhook_secret):
                    raise ValueError("TELEGRAM_WEBHOOK_SECRET must be configured in production")
                if not self.telegram_webhook_url.startswith("https://"):
                    raise ValueError("TELEGRAM_WEBHOOK_URL must be HTTPS in production")
        return self

    @property
    def webapp_root_url(self) -> str:
        url = urlsplit(self.webapp_url)
        if url.path.rstrip("/") == "/app":
            return urlunsplit((url.scheme, url.netloc, "", url.query, url.fragment))
        return self.webapp_url

    @property
    def remnawave_internal_squad_ids(self) -> list[str]:
        return [value.strip() for value in self.remnawave_active_internal_squads.split(",") if value.strip()]

    @property
    def admin_id_set(self) -> set[int]:
        return {int(value.strip()) for value in self.admin_ids.split(",") if value.strip().isdigit()}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
