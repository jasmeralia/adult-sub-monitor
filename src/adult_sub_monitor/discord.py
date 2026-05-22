import asyncio
import logging
from collections.abc import Mapping
from datetime import UTC, datetime

import aiohttp

from adult_sub_monitor.models import Item

logger = logging.getLogger(__name__)


def _truncate_field(value: str) -> str:
    return value[:1024]


def _build_embed(item: Item) -> dict[str, object]:
    title = item.title.strip() or "Untitled video"
    embed: dict[str, object] = {
        "title": title,
        "url": str(item.url),
        "fields": [],
        "footer": {
            "text": (
                f"{item.site_name} • {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}"
            )
        },
    }

    if item.thumbnail_url:
        embed["thumbnail"] = {"url": str(item.thumbnail_url)}

    fields: list[dict[str, object]] = []
    performers = ", ".join(item.performers)
    if performers:
        fields.append(
            {
                "name": "Performers",
                "value": _truncate_field(performers),
                "inline": False,
            }
        )

    tags = ", ".join(item.tags)
    if tags:
        fields.append(
            {
                "name": "Tags",
                "value": _truncate_field(tags),
                "inline": False,
            }
        )

    if item.creator:
        fields.append(
            {
                "name": "Creator",
                "value": _truncate_field(item.creator),
                "inline": True,
            }
        )

    if item.video_type:
        fields.append(
            {
                "name": "Type",
                "value": _truncate_field(item.video_type.title()),
                "inline": True,
            }
        )

    if item.duration:
        fields.append(
            {
                "name": "Duration",
                "value": _truncate_field(item.duration),
                "inline": True,
            }
        )

    if item.price is not None or item.creator or item.video_type or item.duration:
        price = item.price.strip() if item.price is not None else ""
        fields.append(
            {
                "name": "Price",
                "value": _truncate_field(price or "Free"),
                "inline": True,
            }
        )

    embed["fields"] = fields
    return embed


async def _post_embed(
    session: aiohttp.ClientSession,
    webhook_url: str,
    payload: Mapping[str, object],
) -> tuple[int, str, str | None]:
    async with session.post(webhook_url, json=payload) as response:
        error_body = "" if 200 <= response.status < 300 else await response.text()
        return response.status, error_body, response.headers.get("Retry-After")


async def send_video_notification(webhook_url: str, item: Item) -> bool:
    if not webhook_url.strip():
        raise ValueError("webhook_url must not be empty")

    payload = {"embeds": [_build_embed(item)]}

    try:
        async with aiohttp.ClientSession() as session:
            status, error_body, retry_after_header = await _post_embed(
                session,
                webhook_url,
                payload,
            )
            if 200 <= status < 300:
                return True

            if status == 429:
                retry_after = float(retry_after_header or "1.0")
                await asyncio.sleep(retry_after)
                retry_status, retry_error_body, _ = await _post_embed(
                    session,
                    webhook_url,
                    payload,
                )
                if 200 <= retry_status < 300:
                    return True

                logger.error(
                    "Discord webhook retry failed with HTTP %s: %s",
                    retry_status,
                    retry_error_body,
                )
                return False

            logger.error(
                "Discord webhook failed with HTTP %s: %s",
                status,
                error_body,
            )
            return False
    except (aiohttp.ClientError, TimeoutError, ValueError):
        logger.exception("Discord webhook notification failed")
        return False
