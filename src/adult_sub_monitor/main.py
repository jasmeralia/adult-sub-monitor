from __future__ import annotations

import asyncio
import logging
import os
import signal
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from adult_sub_monitor.browser import BrowserManager
from adult_sub_monitor.config import load_config
from adult_sub_monitor.db import Database
from adult_sub_monitor.discord import send_video_notification
from adult_sub_monitor.filters import (
    KeywordPatterns,
    compile_blocked_keywords,
    find_blocked_keyword,
)
from adult_sub_monitor.models import (
    AppConfig,
    Item,
    ManyVidsCreator,
    ManyVidsScrapingConfig,
    SiteConfig,
)
from adult_sub_monitor.sites.base import BaseSite
from adult_sub_monitor.sites.manyvids import ManyVidsSite
from adult_sub_monitor.sites.venus_platform import VenusPlatformSite
from adult_sub_monitor.sites.wowgirls_platform import WowgirlsPlatformSite

logger = logging.getLogger(__name__)
_site_locks: dict[str, asyncio.Lock] = {}


_DEFAULT_JITTER_SECONDS = 900


def _build_site(site_config: SiteConfig) -> BaseSite:
    if site_config.type == "venus_platform":
        return VenusPlatformSite(site_config)
    if site_config.type == "wowgirls_platform":
        return WowgirlsPlatformSite(site_config)
    if site_config.type == "manyvids":
        raise ValueError(
            "ManyVids sites are scheduled per-creator; use "
            "_expand_manyvids_sites instead of _build_site."
        )

    raise ValueError(f"Unsupported site type: {site_config.type}")


def _make_creator_site_config(
    parent: SiteConfig,
    creator: ManyVidsCreator,
    scraping: ManyVidsScrapingConfig,
) -> SiteConfig:
    updates: dict[str, object] = {
        "name": f"{parent.name}:{creator.creator_name}",
        "interval_hours": scraping.creator_interval_hours,
        "jitter_seconds": scraping.creator_jitter_seconds,
        "creators": [creator],
    }
    if creator.notifications_enabled is not None:
        updates["notifications_enabled"] = creator.notifications_enabled
    if creator.discord_webhook is not None:
        updates["discord_webhook"] = creator.discord_webhook
    return parent.model_copy(update=updates)


def _expand_manyvids_sites(
    site_config: SiteConfig,
    app_config: AppConfig,
) -> list[tuple[SiteConfig, BaseSite]]:
    scraping = app_config.manyvids or ManyVidsScrapingConfig()
    expanded: list[tuple[SiteConfig, BaseSite]] = []
    for creator in site_config.creators:
        synth = _make_creator_site_config(site_config, creator, scraping)
        expanded.append((synth, ManyVidsSite(synth, scraping, creator=creator)))
    return expanded


def _build_active_sites(
    config: AppConfig,
) -> list[tuple[SiteConfig, BaseSite]]:
    active: list[tuple[SiteConfig, BaseSite]] = []
    for site_config in config.sites:
        if not site_config.enabled:
            continue
        if site_config.type == "manyvids":
            active.extend(_expand_manyvids_sites(site_config, config))
        else:
            active.append((site_config, _build_site(site_config)))
    return active


def _scheduler_jitter_seconds(site_config: SiteConfig) -> int:
    if site_config.jitter_seconds is not None:
        return site_config.jitter_seconds
    return _DEFAULT_JITTER_SECONDS


async def _send_notification(webhook_url: str, item: Item) -> tuple[bool, str | None]:
    try:
        success = await send_video_notification(webhook_url, item)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Notification failed for %s:%s", item.site_name, item.item_id)
        return False, str(exc)

    if not success:
        return False, "Discord notification helper returned False"

    return True, None


def _resolve_webhook(site_config: SiteConfig, default_webhook: str) -> str:
    override = site_config.discord_webhook
    if override is not None and override.strip():
        return override
    return default_webhook


async def _dispatch_new_items(
    items: list[Item],
    site_name: str,
    db: Database,
    webhook_url: str,
    notifications_enabled: bool,
    dry_run: bool,
    keyword_patterns: KeywordPatterns,
) -> None:
    for item in items:
        if dry_run:
            logger.info(
                "DRY_RUN: would mark seen and notify %s:%s",
                item.site_name,
                item.item_id,
            )
            continue

        is_new = await db.mark_seen(item)
        if not is_new:
            continue

        if not notifications_enabled:
            logger.debug(
                "Notifications disabled for %s; recorded %s as seen "
                "without dispatching",
                site_name,
                item.item_id,
            )
            continue

        matched = find_blocked_keyword(item, keyword_patterns)
        if matched:
            logger.info(
                "Suppressed notification for %s:%s — matched blocked keyword %r",
                site_name,
                item.item_id,
                matched,
            )
            continue

        success, error = await _send_notification(webhook_url, item)
        if success:
            await db.mark_notified(item)
        else:
            await db.record_failed_notification(
                item,
                error or "Unknown notification error",
            )


async def _retry_pending_notifications(
    db: Database,
    webhook_url: str,
    keyword_patterns: KeywordPatterns,
) -> None:
    for item in await db.get_pending_retries(max_attempts=10):
        matched = find_blocked_keyword(item, keyword_patterns)
        if matched:
            logger.info(
                "Suppressed retry for %s:%s — matched blocked keyword %r",
                item.site_name,
                item.item_id,
                matched,
            )
            continue
        success, error = await _send_notification(webhook_url, item)
        if success:
            await db.mark_notified(item)
        else:
            await db.record_failed_notification(
                item,
                error or "Unknown notification retry error",
            )


async def _check_site(
    site: BaseSite,
    site_config: SiteConfig,
    browser_manager: BrowserManager,
    db: Database,
    webhook_url: str,
    dry_run: bool,
    keyword_patterns: KeywordPatterns,
) -> None:
    lock = _site_locks.setdefault(site.name, asyncio.Lock())
    notifications_enabled = site_config.notifications_enabled
    effective_webhook = _resolve_webhook(site_config, webhook_url)

    async with lock:
        logger.info("Checking site %s", site.name)
        context = await browser_manager.ensure_authenticated(site, site_config)
        try:
            page = await context.new_page()
            try:
                if site_config.listing_url is not None:
                    await page.goto(str(site_config.listing_url))
                items = await site.get_latest_items(page, db)

                await _dispatch_new_items(
                    items,
                    site.name,
                    db,
                    effective_webhook,
                    notifications_enabled,
                    dry_run,
                    keyword_patterns,
                )

                if dry_run:
                    logger.info("DRY_RUN: skipping pending notification retries")
                    return

                if not notifications_enabled:
                    logger.debug(
                        "Notifications disabled for %s; skipping pending retries",
                        site.name,
                    )
                    return

                await _retry_pending_notifications(
                    db, effective_webhook, keyword_patterns
                )
            finally:
                await page.close()
        finally:
            await context.close()


async def run() -> None:
    config = load_config(Path(os.environ.get("CONFIG_PATH", "/config/config.yaml")))

    logging.basicConfig(level=config.log_level)

    db = Database(config.db_path)
    browser_manager = BrowserManager(
        config.sessions_dir,
        config.headless,
        config.user_agent,
    )
    await browser_manager.start()

    webhook_url = os.environ.get(config.discord_webhook_env, config.discord_webhook_env)
    run_once = os.environ.get("RUN_ONCE") == "1"
    dry_run = os.environ.get("DRY_RUN") == "1"
    active = _build_active_sites(config)
    keyword_patterns = compile_blocked_keywords(config.blocked_keywords)

    try:
        if run_once:
            for site_config, site in active:
                await _check_site(
                    site,
                    site_config,
                    browser_manager,
                    db,
                    webhook_url,
                    dry_run,
                    keyword_patterns,
                )
            return

        shutdown_event = asyncio.Event()
        scheduler = AsyncIOScheduler()
        now = datetime.now()

        for index, (site_config, site) in enumerate(active):
            scheduler.add_job(
                _check_site,
                trigger=IntervalTrigger(
                    hours=site_config.interval_hours,
                    jitter=_scheduler_jitter_seconds(site_config),
                    start_date=now + timedelta(seconds=30 + index * 30),
                ),
                args=[
                    site,
                    site_config,
                    browser_manager,
                    db,
                    webhook_url,
                    dry_run,
                    keyword_patterns,
                ],
                id=f"check-{site.name}",
                name=f"check-{site.name}",
                max_instances=1,
                replace_existing=True,
            )

        def handle_sigterm() -> None:
            logger.info("Received SIGTERM; shutting down scheduler")
            scheduler.shutdown(wait=True)
            shutdown_event.set()

        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, handle_sigterm)
        scheduler.start()
        await shutdown_event.wait()
    finally:
        await browser_manager.stop()
