from pathlib import Path

import pytest
from pydantic import ValidationError

from adult_sub_monitor.config import load_config
from adult_sub_monitor.models import (
    AppConfig,
    ManyVidsCreator,
    ManyVidsScrapingConfig,
    SiteConfig,
)


def test_load_config_valid(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
sites:
  - name: test_site
    type: venus_platform
    base_url: https://example.com
    login_url: https://example.com/login
    probe_url: https://example.com/account
    listing_url: https://example.com/videos
    credentials_env_user: TEST_USER
    credentials_env_pass: TEST_PASS
discord_webhook_env: DISCORD_WEBHOOK_URL
db_path: /tmp/monitor.db
sessions_dir: /tmp/sessions
""",
        encoding="utf-8",
    )

    config = load_config(config_path)

    assert isinstance(config, AppConfig)
    assert len(config.sites) == 1


def test_load_example_config() -> None:
    config_path = Path(__file__).parents[1] / "config" / "config.example.yaml"

    config = load_config(config_path)

    assert isinstance(config, AppConfig)
    assert [site.name for site in config.sites] == [
        "sensual_love",
        "wowgirls",
        "ultrafilms",
        "angels_love",
    ]


def test_missing_required_field_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
sites:
  - name: test_site
    type: venus_platform
    base_url: https://example.com
    login_url: https://example.com/login
    probe_url: https://example.com/account
    listing_url: https://example.com/videos
    credentials_env_pass: TEST_PASS
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(config_path)


def test_existing_authenticated_site_config_validates() -> None:
    config = SiteConfig(
        name="test_site",
        type="venus_platform",
        base_url="https://example.com",
        login_url="https://example.com/login",
        probe_url="https://example.com/account",
        listing_url="https://example.com/videos",
        credentials_env_user="TEST_USER",
        credentials_env_pass="TEST_PASS",
    )

    assert config.type == "venus_platform"


def test_manyvids_site_config_does_not_require_auth_fields() -> None:
    config = SiteConfig(
        name="manyvids",
        display_name="ManyVids",
        type="manyvids",
        base_url="https://www.manyvids.com",
        creators=[
            ManyVidsCreator(
                creator_id="1002990973",
                creator_name="creator_slug",
                display_name="Creator Name",
            )
        ],
    )

    assert config.login_url is None
    assert config.creators[0].display_name == "Creator Name"


def test_app_config_accepts_manyvids_scraping_config() -> None:
    config = AppConfig(
        sites=[],
        manyvids=ManyVidsScrapingConfig(
            delay_between_creators_min=1,
            delay_between_creators_max=2,
        ),
    )

    assert config.manyvids is not None
    assert config.manyvids.delay_between_creators_min == 1


def test_non_manyvids_site_requires_login_url() -> None:
    with pytest.raises(ValidationError, match="login_url"):
        SiteConfig(
            name="test_site",
            type="venus_platform",
            base_url="https://example.com",
            probe_url="https://example.com/account",
            listing_url="https://example.com/videos",
            credentials_env_user="TEST_USER",
            credentials_env_pass="TEST_PASS",
        )


def test_invalid_typed_value_raises_validation_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
sites: not-a-list
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(config_path)


def test_unknown_type_raises(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
sites:
  - name: test_site
    type: unknown_type
    base_url: https://example.com
    login_url: https://example.com/login
    probe_url: https://example.com/account
    listing_url: https://example.com/videos
    credentials_env_user: TEST_USER
    credentials_env_pass: TEST_PASS
""",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_config(config_path)


def test_invalid_yaml_raises_value_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
sites:
  - name: [not closed
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Failed to parse YAML config file"):
        load_config(config_path)


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Config file not found"):
        load_config(tmp_path / "missing.yaml")
