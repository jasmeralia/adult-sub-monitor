from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class Item(BaseModel):
    site_name: str
    item_id: str
    title: str
    url: HttpUrl
    thumbnail_url: HttpUrl | None = None
    performers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SiteConfig(BaseModel):
    name: str
    display_name: str | None = None
    type: Literal["venus_platform", "wowgirls_platform"]
    base_url: HttpUrl
    login_url: HttpUrl
    probe_url: HttpUrl
    listing_url: HttpUrl
    interval_hours: float = 6.0
    credentials_env_user: str
    credentials_env_pass: str
    enabled: bool = True


class AppConfig(BaseModel):
    sites: list[SiteConfig]
    discord_webhook_env: str = "DISCORD_WEBHOOK_URL"
    db_path: Path = Path("/data/monitor.db")
    sessions_dir: Path = Path("/data/sessions")
    log_level: str = "INFO"
    headless: bool = True
    user_agent: str | None = None
