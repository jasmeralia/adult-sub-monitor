from typing import Any, cast

import aiohttp
import pytest
from aioresponses import CallbackResult, aioresponses

from adult_sub_monitor.discord import _build_embed, send_video_notification
from adult_sub_monitor.models import Item


def build_item(
    *,
    duration: str | None = None,
    price: str | None = None,
    video_type: str | None = None,
    creator: str | None = None,
) -> Item:
    return Item(
        site_name="test_site",
        item_id="item-1",
        title="Test Video",
        url="https://example.com/videos/item-1",
        thumbnail_url="https://example.com/thumbs/item-1.jpg",
        performers=["Performer One"],
        tags=["tag-one", "tag-two"],
        duration=duration,
        price=price,
        video_type=video_type,
        creator=creator,
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
    assert embed["image"] == {"url": "https://example.com/thumbs/item-1.jpg"}
    assert "thumbnail" not in embed


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


def test_embed_includes_metadata_fields_when_present() -> None:
    embed = _build_embed(
        build_item(
            creator="Creator Name",
            video_type="regular",
            duration="12:34",
            price="5.99",
        )
    )

    fields = cast(list[dict[str, Any]], embed["fields"])
    assert fields[0] == {
        "name": "Performers",
        "value": "Performer One",
        "inline": False,
    }
    assert fields[1] == {
        "name": "Tags",
        "value": "tag-one, tag-two",
        "inline": False,
    }
    assert fields[2:] == [
        {"name": "Creator", "value": "Creator Name", "inline": True},
        {"name": "Type", "value": "Regular", "inline": True},
        {"name": "Duration", "value": "12:34", "inline": True},
        {"name": "Price", "value": "5.99", "inline": True},
    ]


def test_embed_omits_metadata_fields_when_absent() -> None:
    embed = _build_embed(build_item())

    fields = cast(list[dict[str, Any]], embed["fields"])
    field_names = {field["name"] for field in fields}
    assert field_names == {"Performers", "Tags"}


@pytest.mark.parametrize("price", [None, ""])
def test_embed_renders_missing_price_as_free(price: str | None) -> None:
    embed = _build_embed(build_item(creator="Creator Name", price=price))

    fields = cast(list[dict[str, Any]], embed["fields"])
    price_field = next(field for field in fields if field["name"] == "Price")
    assert price_field == {"name": "Price", "value": "Free", "inline": True}
