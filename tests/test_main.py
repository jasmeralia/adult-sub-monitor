import asyncio
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from adult_sub_monitor.main import (
    _build_site,
    _check_site,
    _resolve_webhook,
    _scheduler_jitter_seconds,
    _send_notification,
)
from adult_sub_monitor.models import AppConfig, Item, ManyVidsCreator, SiteConfig


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


def test_build_site_unknown_type_raises() -> None:
    site_config = cast(SiteConfig, SimpleNamespace(type="unknown", name="bad-site"))

    with pytest.raises(ValueError, match="Unsupported site type: unknown"):
        _build_site(site_config)


def test_build_site_known_types() -> None:
    venus_config = build_site_config()
    wowgirls_config = SiteConfig(
        name="wowgirls-test",
        type="wowgirls_platform",
        base_url="https://venus.wowgirls.com",
        login_url="https://venus.wowgirls.com/login",
        probe_url="https://venus.wowgirls.com/updates/",
        listing_url="https://venus.wowgirls.com/updates/",
        credentials_env_user="WOWGIRLS_USERNAME",
        credentials_env_pass="WOWGIRLS_PASSWORD",
    )

    assert _build_site(venus_config).name == "test_site"
    assert _build_site(wowgirls_config).name == "wowgirls-test"


def test_make_creator_site_config_inherits_notifications_when_none() -> None:
    from adult_sub_monitor.main import _make_creator_site_config
    from adult_sub_monitor.models import ManyVidsScrapingConfig

    parent = SiteConfig(
        name="manyvids",
        type="manyvids",
        base_url="https://www.manyvids.com",
        notifications_enabled=False,
    )
    creator = ManyVidsCreator(
        creator_id="1", creator_name="alice", notifications_enabled=None
    )
    synth = _make_creator_site_config(parent, creator, ManyVidsScrapingConfig())

    assert synth.notifications_enabled is False


def test_make_creator_site_config_creator_override_wins() -> None:
    from adult_sub_monitor.main import _make_creator_site_config
    from adult_sub_monitor.models import ManyVidsScrapingConfig

    parent = SiteConfig(
        name="manyvids",
        type="manyvids",
        base_url="https://www.manyvids.com",
        notifications_enabled=True,
    )
    creator = ManyVidsCreator(
        creator_id="2", creator_name="bob", notifications_enabled=False
    )
    synth = _make_creator_site_config(parent, creator, ManyVidsScrapingConfig())

    assert synth.notifications_enabled is False


def test_make_creator_site_config_webhook_inherits_when_none() -> None:
    from adult_sub_monitor.main import _make_creator_site_config
    from adult_sub_monitor.models import ManyVidsScrapingConfig

    parent = SiteConfig(
        name="manyvids",
        type="manyvids",
        base_url="https://www.manyvids.com",
        discord_webhook="https://discord.example/global",
    )
    creator = ManyVidsCreator(
        creator_id="3", creator_name="carol", discord_webhook=None
    )
    synth = _make_creator_site_config(parent, creator, ManyVidsScrapingConfig())

    assert synth.discord_webhook == "https://discord.example/global"


def test_make_creator_site_config_webhook_override_wins() -> None:
    from adult_sub_monitor.main import _make_creator_site_config
    from adult_sub_monitor.models import ManyVidsScrapingConfig

    parent = SiteConfig(
        name="manyvids",
        type="manyvids",
        base_url="https://www.manyvids.com",
        discord_webhook="https://discord.example/global",
    )
    creator = ManyVidsCreator(
        creator_id="4",
        creator_name="diana",
        discord_webhook="https://discord.example/creator-specific",
    )
    synth = _make_creator_site_config(parent, creator, ManyVidsScrapingConfig())

    assert synth.discord_webhook == "https://discord.example/creator-specific"


def test_build_site_rejects_manyvids_directly() -> None:
    manyvids_config = SiteConfig(
        name="manyvids-test",
        display_name="ManyVids",
        type="manyvids",
        base_url="https://www.manyvids.com",
        creators=[
            ManyVidsCreator(creator_id="1002990973", creator_name="creator_slug"),
        ],
    )

    with pytest.raises(ValueError, match="per-creator"):
        _build_site(manyvids_config)


def test_expand_manyvids_sites_one_per_creator() -> None:
    from adult_sub_monitor.main import _expand_manyvids_sites
    from adult_sub_monitor.models import ManyVidsScrapingConfig
    from adult_sub_monitor.sites.manyvids import ManyVidsSite

    manyvids_config = SiteConfig(
        name="manyvids",
        display_name="ManyVids",
        type="manyvids",
        base_url="https://www.manyvids.com",
        interval_hours=99,
        creators=[
            ManyVidsCreator(creator_id="1", creator_name="alice"),
            ManyVidsCreator(creator_id="2", creator_name="bob"),
            ManyVidsCreator(creator_id="3", creator_name="carol"),
        ],
    )
    app_config = build_config([manyvids_config])
    app_config = app_config.model_copy(
        update={
            "manyvids": ManyVidsScrapingConfig(
                creator_interval_hours=12,
                creator_jitter_seconds=21600,
            )
        }
    )

    expanded = _expand_manyvids_sites(manyvids_config, app_config)

    assert [synth.name for synth, _ in expanded] == [
        "manyvids:alice",
        "manyvids:bob",
        "manyvids:carol",
    ]
    for synth, site in expanded:
        assert synth.interval_hours == 12
        assert synth.jitter_seconds == 21600
        assert synth.display_name == "ManyVids"
        assert isinstance(site, ManyVidsSite)
        assert site.creator.creator_name in {"alice", "bob", "carol"}


def test_build_active_sites_mixes_venus_and_per_creator_manyvids() -> None:
    from adult_sub_monitor.main import _build_active_sites

    venus_config = build_site_config("venus_site")
    manyvids_config = SiteConfig(
        name="manyvids",
        display_name="ManyVids",
        type="manyvids",
        base_url="https://www.manyvids.com",
        creators=[
            ManyVidsCreator(creator_id="1", creator_name="alice"),
            ManyVidsCreator(creator_id="2", creator_name="bob"),
        ],
    )
    config = build_config([venus_config, manyvids_config])

    active = _build_active_sites(config)

    assert [sc.name for sc, _ in active] == [
        "venus_site",
        "manyvids:alice",
        "manyvids:bob",
    ]


@pytest.mark.asyncio
async def test_run_once_skips_disabled_sites(mocker, monkeypatch) -> None:
    monkeypatch.setenv("RUN_ONCE", "1")
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.example/webhook")
    enabled_config = build_site_config("enabled_site")
    disabled_config = SiteConfig(
        name="disabled_site",
        type="venus_platform",
        base_url="https://example.com",
        login_url="https://example.com/login",
        probe_url="https://example.com/account",
        listing_url="https://example.com/videos",
        credentials_env_user="X",
        credentials_env_pass="Y",
        enabled=False,
    )
    sites = [build_site("enabled_site")]
    config = build_config([enabled_config, disabled_config])
    browser_manager = AsyncMock()

    mocker.patch("adult_sub_monitor.main.load_config", return_value=config)
    mocker.patch("adult_sub_monitor.main.Database")
    mocker.patch("adult_sub_monitor.main.BrowserManager", return_value=browser_manager)
    mocker.patch("adult_sub_monitor.main.send_video_notification", new=AsyncMock())
    mocker.patch("adult_sub_monitor.main._build_site", side_effect=sites)
    check_site = mocker.patch("adult_sub_monitor.main._check_site", new=AsyncMock())

    from adult_sub_monitor.main import run

    await run()

    assert check_site.await_count == 1
    assert check_site.await_args_list[0].args[0].name == "enabled_site"


def test_scheduler_jitter_seconds_uses_default_when_unset() -> None:
    assert _scheduler_jitter_seconds(build_site_config()) == 900


def test_scheduler_jitter_seconds_honors_per_site_override() -> None:
    overridden = build_site_config().model_copy(update={"jitter_seconds": 21600})
    assert _scheduler_jitter_seconds(overridden) == 21600


@pytest.mark.asyncio
async def test_send_notification_success(mocker) -> None:
    item = build_item()
    mocker.patch(
        "adult_sub_monitor.main.send_video_notification",
        new=AsyncMock(return_value=True),
    )

    assert await _send_notification("https://discord.example/webhook", item) == (
        True,
        None,
    )


@pytest.mark.asyncio
async def test_send_notification_false_result(mocker) -> None:
    item = build_item()
    mocker.patch(
        "adult_sub_monitor.main.send_video_notification",
        new=AsyncMock(return_value=False),
    )

    assert await _send_notification("https://discord.example/webhook", item) == (
        False,
        "Discord notification helper returned False",
    )


@pytest.mark.asyncio
async def test_send_notification_exception(mocker) -> None:
    item = build_item()
    mocker.patch(
        "adult_sub_monitor.main.send_video_notification",
        new=AsyncMock(side_effect=RuntimeError("webhook exploded")),
    )

    assert await _send_notification("https://discord.example/webhook", item) == (
        False,
        "webhook exploded",
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


def test_resolve_webhook_uses_override_when_set() -> None:
    site_config = build_site_config()
    site_config = site_config.model_copy(
        update={"discord_webhook": "https://discord.example/per-site"}
    )

    assert (
        _resolve_webhook(site_config, "https://discord.example/global")
        == "https://discord.example/per-site"
    )


def test_resolve_webhook_falls_back_to_default_when_override_none() -> None:
    site_config = build_site_config()

    assert (
        _resolve_webhook(site_config, "https://discord.example/global")
        == "https://discord.example/global"
    )


def test_resolve_webhook_treats_empty_override_as_unset() -> None:
    site_config = build_site_config()
    site_config = site_config.model_copy(update={"discord_webhook": "   "})

    assert (
        _resolve_webhook(site_config, "https://discord.example/global")
        == "https://discord.example/global"
    )


@pytest.mark.asyncio
async def test_per_site_discord_webhook_override(mocker) -> None:
    item = build_item()
    site_config = build_site_config().model_copy(
        update={"discord_webhook": "https://discord.example/per-site"}
    )
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
        "https://discord.example/global",
        False,
    )

    send_video_notification.assert_awaited_once_with(
        "https://discord.example/per-site",
        item,
    )


@pytest.mark.asyncio
async def test_notifications_disabled_marks_seen_without_dispatch(mocker) -> None:
    item = build_item()
    site_config = build_site_config().model_copy(
        update={"notifications_enabled": False}
    )
    site = build_site(items=[item])
    db = AsyncMock()
    db.mark_seen = AsyncMock(return_value=True)
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
        "https://discord.example/global",
        False,
    )

    db.mark_seen.assert_awaited_once_with(item)
    send_video_notification.assert_not_called()
    db.mark_notified.assert_not_called()
    db.record_failed_notification.assert_not_called()


@pytest.mark.asyncio
async def test_notifications_disabled_skips_retry_loop(mocker) -> None:
    item = build_item()
    site_config = build_site_config().model_copy(
        update={"notifications_enabled": False}
    )
    site = build_site(items=[])
    db = AsyncMock()
    db.get_pending_retries = AsyncMock(return_value=[item])
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
        "https://discord.example/global",
        False,
    )

    db.get_pending_retries.assert_not_called()
    send_video_notification.assert_not_called()
