from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
from datetime import datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from adult_sub_monitor.browser import BrowserManager
from adult_sub_monitor.config import load_config
from adult_sub_monitor.db import Database
from adult_sub_monitor.discord import send_video_notification
from adult_sub_monitor.models import Item, SiteConfig
from adult_sub_monitor.sites.base import BaseSite
from adult_sub_monitor.sites.venus_platform import VenusPlatformSite
from adult_sub_monitor.sites.vixen_media_group_platform import VixenMediaGroupSite

logger = logging.getLogger(__name__)
_site_locks: dict[str, asyncio.Lock] = {}


def _build_site(site_config: SiteConfig) -> BaseSite:
    if site_config.type == "venus_platform":
        return VenusPlatformSite(site_config)
    if site_config.type == "vixen_media_group_platform":
        return VixenMediaGroupSite(site_config)

    raise ValueError(f"Unsupported site type: {site_config.type}")


def _scheduler_jitter_seconds() -> int:
    return random.randrange(900, 901)


async def _send_notification(webhook_url: str, item: Item) -> tuple[bool, str | None]:
    try:
        success = await send_video_notification(webhook_url, item)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.exception("Notification failed for %s:%s", item.site_name, item.item_id)
        return False, str(exc)

    if not success:
        return False, "Discord notification helper returned False"

    return True, None


async def _check_site(
    site: BaseSite,
    site_config: SiteConfig,
    browser_manager: BrowserManager,
    db: Database,
    webhook_url: str,
    dry_run: bool,
) -> None:
    lock = _site_locks.setdefault(site.name, asyncio.Lock())

    async with lock:
        logger.info("Checking site %s", site.name)
        context = await browser_manager.ensure_authenticated(site, site_config)
        try:
            page = await context.new_page()
            await page.goto(str(site_config.listing_url))
            items = await site.get_latest_items(page)

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

                success, error = await _send_notification(webhook_url, item)
                if success:
                    await db.mark_notified(item)
                else:
                    await db.record_failed_notification(
                        item,
                        error or "Unknown notification error",
                    )

            if dry_run:
                logger.info("DRY_RUN: skipping pending notification retries")
                return

            for item in await db.get_pending_retries(max_attempts=10):
                success, error = await _send_notification(webhook_url, item)
                if success:
                    await db.mark_notified(item)
                else:
                    await db.record_failed_notification(
                        item,
                        error or "Unknown notification retry error",
                    )
        finally:
            await context.close()


async def run() -> None:
    config_path = os.environ.get("CONFIG_PATH", "/config/config.yaml")
    config = load_config(Path(config_path))

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
    sites = [_build_site(site_config) for site_config in config.sites]

    try:
        if run_once:
            for site, site_config in zip(sites, config.sites, strict=True):
                await _check_site(
                    site,
                    site_config,
                    browser_manager,
                    db,
                    webhook_url,
                    dry_run,
                )
            return

        shutdown_event = asyncio.Event()
        scheduler = AsyncIOScheduler()
        now = datetime.now()

        for index, (site, site_config) in enumerate(
            zip(sites, config.sites, strict=True),
        ):
            scheduler.add_job(
                _check_site,
                trigger=IntervalTrigger(
                    hours=site_config.interval_hours,
                    jitter=_scheduler_jitter_seconds(),
                    start_date=now + timedelta(seconds=index * 30),
                ),
                args=[
                    site,
                    site_config,
                    browser_manager,
                    db,
                    webhook_url,
                    dry_run,
                ],
                id=f"check-{site.name}",
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
