import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adult_sub_monitor.main import _check_site
from adult_sub_monitor.models import AppConfig, Item, SiteConfig


@pytest.fixture(autouse=True)
def clear_site_locks() -> None:
    from adult_sub_monitor import main

    main._site_locks.clear()


def build_item(site_name: str = "test_site", item_id: str = "item-1") -> Item:
    return Item(
        site_name=site_name,
        item_id=item_id,
        title=f"Test Item {item_id}",
        url=f"https://example.com/videos/{item_id}",
        thumbnail_url=f"https://example.com/thumbs/{item_id}.jpg",
        performers=["Performer One"],
        tags=["tag-one", "tag-two"],
    )


def build_site_config(name: str = "test_site") -> SiteConfig:
    return SiteConfig(
        name=name,
        type="venus_platform",
        base_url="https://example.com",
        login_url="https://example.com/login",
        probe_url="https://example.com/account",
        listing_url="https://example.com/videos",
        credentials_env_user="EXAMPLE_USERNAME",
        credentials_env_pass="EXAMPLE_PASSWORD",
    )


def build_config(site_configs: list[SiteConfig]) -> AppConfig:
    return AppConfig(
        sites=site_configs,
        discord_webhook_env="DISCORD_WEBHOOK_URL",
        db_path="/tmp/test-main.db",
        sessions_dir="/tmp/test-sessions",
    )


def build_context() -> SimpleNamespace:
    page = AsyncMock()
    page.goto = AsyncMock()
    return SimpleNamespace(
        new_page=AsyncMock(return_value=page),
        close=AsyncMock(),
    )


def build_site(name: str = "test_site", items: list[Item] | None = None) -> AsyncMock:
    site = AsyncMock()
    site.name = name
    site.get_latest_items = AsyncMock(
        return_value=items if items is not None else [build_item(name)]
    )
    return site


@pytest.mark.asyncio
async def test_run_once_runs_each_site_once(mocker, monkeypatch) -> None:
    monkeypatch.setenv("RUN_ONCE", "1")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    site_configs = [build_site_config("site_one"), build_site_config("site_two")]
    sites = [build_site("site_one"), build_site("site_two")]
    config = build_config(site_configs)
    browser_manager = AsyncMock()

    mocker.patch("adult_sub_monitor.main.load_config", return_value=config)
    mocker.patch("adult_sub_monitor.main.Database")
    mocker.patch("adult_sub_monitor.main.BrowserManager", return_value=browser_manager)
    mocker.patch("adult_sub_monitor.main.send_video_notification", new=AsyncMock())
    mocker.patch("adult_sub_monitor.main._build_site", side_effect=sites)
    check_site = mocker.patch("adult_sub_monitor.main._check_site", new=AsyncMock())

    from adult_sub_monitor.main import run

    await run()

    assert check_site.await_count == 2
    assert [call.args[0] for call in check_site.await_args_list] == sites
    browser_manager.start.assert_awaited_once()
    browser_manager.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_dry_run_skips_db_and_notifications(mocker, monkeypatch) -> None:
    monkeypatch.setenv("DRY_RUN", "1")
    monkeypatch.setenv("RUN_ONCE", "1")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    site_config = build_site_config()
    site = build_site(items=[build_item()])
    db = AsyncMock()
    browser_manager = AsyncMock()
    browser_manager.ensure_authenticated = AsyncMock(return_value=build_context())

    mocker.patch(
        "adult_sub_monitor.main.load_config",
        return_value=build_config([site_config]),
    )
    mocker.patch("adult_sub_monitor.main.Database", return_value=db)
    mocker.patch("adult_sub_monitor.main.BrowserManager", return_value=browser_manager)
    mocker.patch("adult_sub_monitor.main._build_site", return_value=site)
    send_video_notification = mocker.patch(
        "adult_sub_monitor.main.send_video_notification",
        new=AsyncMock(),
    )

    from adult_sub_monitor.main import run

    await run()

    db.mark_seen.assert_not_called()
    send_video_notification.assert_not_called()
    browser_manager.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_per_site_lock_prevents_overlap() -> None:
    site_config = build_site_config()
    site = build_site(items=[])
    db = AsyncMock()
    browser_manager = AsyncMock()
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    call_order: list[str] = []

    async def ensure_authenticated(_site, _site_config):
        if not call_order:
            call_order.append("first-start")
            first_started.set()
            await release_first.wait()
            call_order.append("first-release")
        else:
            call_order.append("second-start")
        return build_context()

    browser_manager.ensure_authenticated = AsyncMock(side_effect=ensure_authenticated)

    first = asyncio.create_task(
        _check_site(
            site,
            site_config,
            browser_manager,
            db,
            "https://discord.example/webhook",
            True,
        )
    )
    second = asyncio.create_task(
        _check_site(
            site,
            site_config,
            browser_manager,
            db,
            "https://discord.example/webhook",
            True,
        )
    )

    await first_started.wait()
    await asyncio.sleep(0)
    assert browser_manager.ensure_authenticated.await_count == 1

    release_first.set()
    await asyncio.gather(first, second)

    assert call_order == ["first-start", "first-release", "second-start"]
    assert browser_manager.ensure_authenticated.await_count == 2


@pytest.mark.asyncio
async def test_new_item_triggers_notification(mocker) -> None:
    item = build_item()
    site_config = build_site_config()
    site = build_site(items=[item])
    db = AsyncMock()
    db.mark_seen = AsyncMock(return_value=True)
    db.get_pending_retries = AsyncMock(return_value=[])
    browser_manager = AsyncMock()
    browser_manager.ensure_authenticated = AsyncMock(return_value=build_context())
    send_video_notification = mocker.patch(
        "adult_sub_monitor.main.send_video_notification",
        new=AsyncMock(return_value=True),
    )

    await _check_site(
        site,
        site_config,
        browser_manager,
        db,
        "https://discord.example/webhook",
        False,
    )

    send_video_notification.assert_awaited_once_with(
        "https://discord.example/webhook",
        item,
    )
    db.mark_notified.assert_awaited_once_with(item)


@pytest.mark.asyncio
async def test_duplicate_item_skips_notification(mocker) -> None:
    item = build_item()
    site_config = build_site_config()
    site = build_site(items=[item])
    db = AsyncMock()
    db.mark_seen = AsyncMock(return_value=False)
    db.get_pending_retries = AsyncMock(return_value=[])
    browser_manager = AsyncMock()
    browser_manager.ensure_authenticated = AsyncMock(return_value=build_context())
    send_video_notification = mocker.patch(
        "adult_sub_monitor.main.send_video_notification",
        new=AsyncMock(),
    )

    await _check_site(
        site,
        site_config,
        browser_manager,
        db,
        "https://discord.example/webhook",
        False,
    )

    send_video_notification.assert_not_called()
    db.mark_notified.assert_not_called()
