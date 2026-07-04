from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, model_validator


class Item(BaseModel):
    site_name: str
    item_id: str
    title: str
    url: HttpUrl
    thumbnail_url: HttpUrl | None = None
    performers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    duration: str | None = None
    price: str | None = None
    video_type: str | None = None
    creator: str | None = None
    description: str | None = None


class ManyVidsCreator(BaseModel):
    creator_id: str
    creator_name: str
    display_name: str | None = None
    notifications_enabled: bool | None = None  # None = inherit from SiteConfig
    discord_webhook: str | None = None  # None = inherit from SiteConfig


class ManyVidsScrapingConfig(BaseModel):
    delay_between_creators_min: float = 30
    delay_between_creators_max: float = 60
    delay_between_pages_min: float = 3
    delay_between_pages_max: float = 8
    page_timeout: int = 30000
    max_retries: int = 3
    retry_backoff_base: float = 10
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
    creator_interval_hours: float = 12.0
    creator_jitter_seconds: int = 21600


class SiteConfig(BaseModel):
    name: str
    display_name: str | None = None
    type: Literal["venus_platform", "wowgirls_platform", "manyvids"]
    base_url: HttpUrl
    login_url: HttpUrl | None = None
    probe_url: HttpUrl | None = None
    listing_url: HttpUrl | None = None
    interval_hours: float = 6.0
    credentials_env_user: str | None = None
    credentials_env_pass: str | None = None
    enabled: bool = True
    notifications_enabled: bool = True
    discord_webhook: str | None = None
    jitter_seconds: int | None = None
    creators: list[ManyVidsCreator] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_authenticated_site_fields(self) -> "SiteConfig":
        if self.type == "manyvids":
            return self

        required_fields = (
            "login_url",
            "probe_url",
            "listing_url",
            "credentials_env_user",
            "credentials_env_pass",
        )
        missing = [
            field_name
            for field_name in required_fields
            if getattr(self, field_name) is None
        ]
        if missing:
            raise ValueError(
                "Authenticated site configs require: " + ", ".join(missing)
            )
        return self


class AppConfig(BaseModel):
    sites: list[SiteConfig]
    discord_webhook_env: str = "DISCORD_WEBHOOK_URL"
    db_path: Path = Path("/data/monitor.db")
    sessions_dir: Path = Path("/data/sessions")
    log_level: str = "INFO"
    headless: bool = True
    user_agent: str | None = None
    manyvids: ManyVidsScrapingConfig | None = None
    blocked_keywords: list[str] = Field(default_factory=list)
