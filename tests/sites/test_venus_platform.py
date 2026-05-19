from unittest.mock import AsyncMock, MagicMock

import pytest

from adult_sub_monitor.models import Item, SiteConfig
from adult_sub_monitor.sites.venus_platform import VenusPlatformSite


def make_site() -> VenusPlatformSite:
    return VenusPlatformSite(
        SiteConfig(
            name="venus-test",
            type="venus_platform",
            base_url="https://venus.example",
            login_url="https://venus.example/login",
            probe_url="https://venus.example/members/content",
            listing_url="https://venus.example/members/content",
            credentials_env_user="VENUS_USER",
            credentials_env_pass="VENUS_PASS",
        )
    )


def _make_thumb(
    alt: str | None = "Video Title",
    src: str | None = "https://cdn.example/thumb.jpg",
) -> MagicMock:
    thumb = MagicMock()
    thumb.get_attribute = AsyncMock(side_effect=[alt, src])
    return thumb


def _make_card(
    href: str | None = "/members/content/item/abc123-video-title",
    alt: str | None = "Video Title",
    src: str | None = "https://cdn.example/thumb.jpg",
) -> MagicMock:
    thumb = _make_thumb(alt, src)
    thumb_locator = MagicMock()
    thumb_locator.count = AsyncMock(return_value=1 if alt is not None else 0)
    thumb_locator.first = thumb

    card = MagicMock()
    card.get_attribute = AsyncMock(return_value=href)
    card.locator = MagicMock(return_value=thumb_locator)
    return card


def _make_page(*cards: MagicMock) -> AsyncMock:
    card_list = MagicMock()
    card_list.count = AsyncMock(return_value=len(cards))
    card_list.nth = MagicMock(side_effect=lambda i: cards[i])

    page = AsyncMock()
    page.locator = MagicMock(return_value=card_list)
    return page


@pytest.mark.asyncio
async def test_login_navigates_fills_and_waits() -> None:
    site = make_site()
    page = AsyncMock()

    await site.login(page, "user@example.test", "secret")

    page.goto.assert_awaited_once_with(
        "https://venus.example/login", wait_until="domcontentloaded"
    )
    page.fill.assert_any_await("#inputEmail", "user@example.test")
    page.fill.assert_any_await("#inputPassword", "secret")
    page.click.assert_awaited_once_with(".submit-button")
    page.wait_for_function.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_logged_in_true_when_not_on_login_page() -> None:
    site = make_site()
    page = MagicMock()
    page.url = "https://venus.example/members/content"

    assert await site.is_logged_in(page) is True


@pytest.mark.asyncio
async def test_is_logged_in_false_when_on_login_page() -> None:
    site = make_site()
    page = MagicMock()
    page.url = "https://venus.example/login"

    assert await site.is_logged_in(page) is False


@pytest.mark.asyncio
async def test_get_latest_items_returns_video_cards() -> None:
    site = make_site()
    card1 = _make_card(
        "/members/content/item/abc-first-video",
        "First Video",
        "https://cdn.example/1.jpg",
    )
    card2 = _make_card(
        "/members/content/item/def-second-video",
        "Second Video",
        "https://cdn.example/2.jpg",
    )
    page = _make_page(card1, card2)

    items = await site.get_latest_items(page)

    assert len(items) == 2
    assert all(isinstance(item, Item) for item in items)
    assert items[0].title == "First Video"
    assert items[1].title == "Second Video"
    assert items[0].site_name == "venus-test"


@pytest.mark.asyncio
async def test_get_latest_items_absolute_url() -> None:
    site = make_site()
    card = _make_card(
        "/members/content/item/abc123-naughty-sweetness", "Naughty Sweetness"
    )
    page = _make_page(card)

    items = await site.get_latest_items(page)

    assert len(items) == 1
    assert (
        str(items[0].url)
        == "https://venus.example/members/content/item/abc123-naughty-sweetness"
    )
    assert (
        items[0].item_id
        == "https://venus.example/members/content/item/abc123-naughty-sweetness"
    )


@pytest.mark.asyncio
async def test_get_latest_items_empty_listing() -> None:
    site = make_site()
    page = _make_page()

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_get_latest_items_skips_card_without_href() -> None:
    site = make_site()
    card = _make_card(href=None)
    page = _make_page(card)

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_get_latest_items_skips_card_without_thumbnail() -> None:
    site = make_site()
    card = _make_card(alt=None)
    page = _make_page(card)

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_get_latest_items_skips_card_without_title() -> None:
    site = make_site()
    thumb = MagicMock()
    thumb.get_attribute = AsyncMock(side_effect=[None, "https://cdn.example/thumb.jpg"])
    thumb_locator = MagicMock()
    thumb_locator.count = AsyncMock(return_value=1)
    thumb_locator.first = thumb

    card = MagicMock()
    card.get_attribute = AsyncMock(return_value="/members/content/item/abc")
    card.locator = MagicMock(return_value=thumb_locator)
    page = _make_page(card)

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_get_latest_items_no_thumbnail_url() -> None:
    site = make_site()
    thumb = MagicMock()
    thumb.get_attribute = AsyncMock(side_effect=["My Video", None])
    thumb_locator = MagicMock()
    thumb_locator.count = AsyncMock(return_value=1)
    thumb_locator.first = thumb

    card = MagicMock()
    card.get_attribute = AsyncMock(return_value="/members/content/item/abc")
    card.locator = MagicMock(return_value=thumb_locator)
    page = _make_page(card)

    items = await site.get_latest_items(page)

    assert len(items) == 1
    assert items[0].thumbnail_url is None


@pytest.mark.asyncio
async def test_get_latest_items_performers_and_tags_empty() -> None:
    site = make_site()
    card = _make_card()
    page = _make_page(card)

    items = await site.get_latest_items(page)

    assert items[0].performers == []
    assert items[0].tags == []
