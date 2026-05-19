from pathlib import Path

import pytest
from pydantic import ValidationError

from adult_sub_monitor.config import load_config
from adult_sub_monitor.models import AppConfig


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
        "tushy",
        "deeper",
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
