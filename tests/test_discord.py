from contextlib import asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from adult_sub_monitor.discord import _build_embed, send_video_notification
from adult_sub_monitor.models import Item


def build_item(
    *,
    duration: str | None = None,
    price: str | None = None,
    video_type: str | None = None,
    creator: str | None = None,
    description: str | None = None,
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
        description=description,
    )


def _response(
    status: int, body: str = "", *, retry_after: str | None = None
) -> MagicMock:
    r = MagicMock()
    r.status = status
    r.text = AsyncMock(return_value=body)
    h: dict[str, str] = {}
    if retry_after is not None:
        h["Retry-After"] = retry_after
    r.headers = h
    return r


@asynccontextmanager  # type: ignore[arg-type]
async def _mock_session(*posts: MagicMock | Exception):  # type: ignore[misc]
    """Patch ClientSession so consecutive session.post() calls return posts in order."""
    call_idx = 0
    captured_kwargs: list[dict[str, Any]] = []

    class _PostCtx:
        def __init__(self, kw: dict[str, Any]) -> None:
            captured_kwargs.append(kw)

        async def __aenter__(self) -> MagicMock:
            nonlocal call_idx
            r = posts[call_idx]
            call_idx += 1
            if isinstance(r, Exception):
                raise r
            return r  # type: ignore[return-value]

        async def __aexit__(self, *_: object) -> None:
            pass

    mock_session = MagicMock()
    mock_session.post = lambda _url, **kw: _PostCtx(kw)
    mock_session._captured = captured_kwargs

    with patch("adult_sub_monitor.discord.aiohttp.ClientSession") as cls:
        cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        cls.return_value.__aexit__ = AsyncMock(return_value=None)
        yield mock_session


@pytest.mark.asyncio
async def test_successful_post() -> None:
    async with _mock_session(_response(200)):
        assert (
            await send_video_notification(
                "https://discord.com/api/webhooks/test/success", build_item()
            )
            is True
        )


@pytest.mark.asyncio
async def test_embed_structure() -> None:
    async with _mock_session(_response(200)) as sess:
        assert (
            await send_video_notification(
                "https://discord.com/api/webhooks/test/embed", build_item()
            )
            is True
        )

    payload = sess._captured[0]["json"]
    embed = cast(dict[str, Any], payload["embeds"][0])
    assert embed["title"] == "New test_site Video: Test Video"
    assert embed["url"] == "https://example.com/videos/item-1"
    assert embed["image"] == {"url": "https://example.com/thumbs/item-1.jpg"}
    assert "thumbnail" not in embed


@pytest.mark.asyncio
async def test_429_retry_success() -> None:
    async with _mock_session(_response(429, retry_after="0.01"), _response(200)):
        assert (
            await send_video_notification(
                "https://discord.com/api/webhooks/test/retry", build_item()
            )
            is True
        )


@pytest.mark.asyncio
async def test_non_rate_limited_http_error_returns_false() -> None:
    async with _mock_session(_response(500, "server failed")):
        assert (
            await send_video_notification(
                "https://discord.com/api/webhooks/test/server-error", build_item()
            )
            is False
        )


@pytest.mark.asyncio
async def test_429_retry_http_error_returns_false() -> None:
    async with _mock_session(
        _response(429, retry_after="0.01"), _response(503, "still unavailable")
    ):
        assert (
            await send_video_notification(
                "https://discord.com/api/webhooks/test/retry-error", build_item()
            )
            is False
        )


@pytest.mark.asyncio
async def test_network_error_returns_false() -> None:
    async with _mock_session(aiohttp.ClientError("network failed")):
        assert (
            await send_video_notification(
                "https://discord.com/api/webhooks/test/error", build_item()
            )
            is False
        )


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
        {"name": "Duration", "value": "12:34", "inline": True},
        {"name": "Price", "value": "5.99", "inline": True},
    ]


def test_embed_title_mv_includes_creator_and_video_type() -> None:
    embed = _build_embed(build_item(creator="Ashley Alban", video_type="regular"))

    assert (
        embed["title"] == "New test_site Video from Ashley Alban: Test Video (Regular)"
    )


def test_embed_title_mv_without_video_type_omits_parens() -> None:
    embed = _build_embed(build_item(creator="Ashley Alban"))

    assert embed["title"] == "New test_site Video from Ashley Alban: Test Video"


def test_embed_title_non_mv_uses_simple_format() -> None:
    embed = _build_embed(build_item())

    assert embed["title"] == "New test_site Video: Test Video"


def test_embed_title_falls_back_to_untitled() -> None:
    item = build_item().model_copy(update={"title": "   "})

    embed = _build_embed(item)

    assert embed["title"] == "New test_site Video: Untitled video"


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


def test_embed_includes_description_when_present() -> None:
    embed = _build_embed(build_item(description="A hot scene filmed outdoors."))

    assert embed["description"] == "A hot scene filmed outdoors."


def test_embed_omits_description_when_none() -> None:
    embed = _build_embed(build_item())

    assert "description" not in embed


def test_embed_truncates_description_at_4096_chars() -> None:
    long_desc = "x" * 5000
    embed = _build_embed(build_item(description=long_desc))

    assert len(cast(str, embed["description"])) == 4096
