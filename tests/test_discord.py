from typing import Any, cast

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses

from adult_sub_monitor.discord import send_video_notification
from adult_sub_monitor.models import Item


def build_item() -> Item:
    return Item(
        site_name="test_site",
        item_id="item-1",
        title="Test Video",
        url="https://example.com/videos/item-1",
        thumbnail_url="https://example.com/thumbs/item-1.jpg",
        performers=["Performer One"],
        tags=["tag-one", "tag-two"],
    )


@pytest.mark.asyncio
async def test_successful_post() -> None:
    webhook_url = "https://discord.com/api/webhooks/test/success"

    with aioresponses() as mocked:
        mocked.post(webhook_url, status=200)

        assert await send_video_notification(webhook_url, build_item()) is True


@pytest.mark.asyncio
async def test_embed_structure() -> None:
    webhook_url = "https://discord.com/api/webhooks/test/embed"
    captured_payload: dict[str, Any] = {}

    def capture_payload(_url: str, **kwargs: Any) -> CallbackResult:
        captured_payload.update(kwargs["json"])
        return CallbackResult(status=200)

    with aioresponses() as mocked:
        mocked.post(webhook_url, callback=capture_payload)

        assert await send_video_notification(webhook_url, build_item()) is True

    embed = cast(dict[str, Any], captured_payload["embeds"][0])
    assert embed["title"] == "Test Video"
    assert embed["url"] == "https://example.com/videos/item-1"
    assert embed["thumbnail"] == {"url": "https://example.com/thumbs/item-1.jpg"}


@pytest.mark.asyncio
async def test_429_retry_success() -> None:
    webhook_url = "https://discord.com/api/webhooks/test/retry"

    with aioresponses() as mocked:
        mocked.post(webhook_url, status=429, headers={"Retry-After": "0.01"})
        mocked.post(webhook_url, status=200)

        assert await send_video_notification(webhook_url, build_item()) is True


@pytest.mark.asyncio
async def test_non_rate_limited_http_error_returns_false() -> None:
    webhook_url = "https://discord.com/api/webhooks/test/server-error"

    with aioresponses() as mocked:
        mocked.post(webhook_url, status=500, body="server failed")

        assert await send_video_notification(webhook_url, build_item()) is False


@pytest.mark.asyncio
async def test_429_retry_http_error_returns_false() -> None:
    webhook_url = "https://discord.com/api/webhooks/test/retry-error"

    with aioresponses() as mocked:
        mocked.post(webhook_url, status=429, headers={"Retry-After": "0.01"})
        mocked.post(webhook_url, status=503, body="still unavailable")

        assert await send_video_notification(webhook_url, build_item()) is False


@pytest.mark.asyncio
async def test_network_error_returns_false() -> None:
    webhook_url = "https://discord.com/api/webhooks/test/error"

    with aioresponses() as mocked:
        mocked.post(webhook_url, exception=aiohttp.ClientError("network failed"))

        assert await send_video_notification(webhook_url, build_item()) is False


@pytest.mark.asyncio
async def test_empty_webhook_raises() -> None:
    with pytest.raises(ValueError, match="webhook_url must not be empty"):
        await send_video_notification("", build_item())
