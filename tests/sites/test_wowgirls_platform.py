from unittest.mock import AsyncMock, MagicMock

import pytest

from adult_sub_monitor.models import Item, SiteConfig
from adult_sub_monitor.sites.wowgirls_platform import WowgirlsPlatformSite


def make_site() -> WowgirlsPlatformSite:
    return WowgirlsPlatformSite(
        SiteConfig(
            name="wowgirls-test",
            type="wowgirls_platform",
            base_url="https://venus.wowgirls.com",
            login_url="https://venus.wowgirls.com/login",
            probe_url="https://venus.wowgirls.com/updates/",
            listing_url="https://venus.wowgirls.com/updates/",
            credentials_env_user="WOWGIRLS_USER",
            credentials_env_pass="WOWGIRLS_PASS",
        )
    )


def _make_card(
    href: str | None = "/film/abc123/my-film",
    title_text: str | None = "My Film",
    src: str | None = "https://cdn.wowgirls.com/abc123/thumb.jpg",
    performers: list[str] | None = None,
    tags: list[str] | None = None,
) -> MagicMock:
    if performers is None:
        performers = ["Georgia"]
    if tags is None:
        tags = ["4K", "Solo"]

    def make_text_locator(texts: list[str]) -> MagicMock:
        loc = MagicMock()
        loc.count = AsyncMock(return_value=len(texts))
        items_mocks = []
        for t in texts:
            m = MagicMock()
            m.inner_text = AsyncMock(return_value=t)
            items_mocks.append(m)
        loc.nth = MagicMock(side_effect=lambda i: items_mocks[i])
        return loc

    url_link = MagicMock()
    url_link.get_attribute = AsyncMock(return_value=href)
    url_locator = MagicMock()
    url_locator.count = AsyncMock(return_value=1 if href else 0)
    url_locator.first = url_link

    title_locator = MagicMock()
    if title_text is not None:
        title_locator.count = AsyncMock(return_value=1)
        title_el = MagicMock()
        title_el.inner_text = AsyncMock(return_value=title_text)
        title_locator.first = title_el
    else:
        title_locator.count = AsyncMock(return_value=0)

    thumb_img = MagicMock()
    thumb_img.get_attribute = AsyncMock(return_value=src)
    thumb_locator = MagicMock()
    thumb_locator.count = AsyncMock(return_value=1 if src else 0)
    thumb_locator.first = thumb_img

    performer_locator = make_text_locator(performers)
    tag_locator = make_text_locator(tags)

    def locator_factory(selector: str) -> MagicMock:
        if "film" in selector:
            return url_locator
        if selector == "a.title":
            return title_locator
        if "thumb" in selector:
            return thumb_locator
        if "models" in selector:
            return performer_locator
        if "genres" in selector:
            return tag_locator
        return MagicMock()

    card = MagicMock()
    card.locator = MagicMock(side_effect=locator_factory)
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
        "https://venus.wowgirls.com/login", wait_until="domcontentloaded"
    )
    page.fill.assert_any_await("#user-email", "user@example.test")
    page.fill.assert_any_await("#user-password", "secret")
    page.click.assert_awaited_once_with(".loginform-submit-button")
    page.wait_for_function.assert_awaited_once()


@pytest.mark.asyncio
async def test_is_logged_in_true() -> None:
    site = make_site()
    page = MagicMock()
    page.url = "https://venus.wowgirls.com/updates/"

    assert await site.is_logged_in(page) is True


@pytest.mark.asyncio
async def test_is_logged_in_false() -> None:
    site = make_site()
    page = MagicMock()
    page.url = "https://venus.wowgirls.com/login"

    assert await site.is_logged_in(page) is False


@pytest.mark.asyncio
async def test_get_latest_items_returns_items() -> None:
    site = make_site()
    card = _make_card(
        "/film/abc123/my-film",
        "My Film",
        "https://cdn.wowgirls.com/thumb.jpg",
        ["Georgia"],
        ["4K"],
    )
    page = _make_page(card)

    items = await site.get_latest_items(page)

    assert len(items) == 1
    item = items[0]
    assert isinstance(item, Item)
    assert item.title == "My Film"
    assert item.site_name == "wowgirls-test"
    assert str(item.url) == "https://venus.wowgirls.com/film/abc123/my-film"
    assert item.performers == ["Georgia"]
    assert item.tags == ["4K"]


@pytest.mark.asyncio
async def test_get_latest_items_absolute_thumbnail() -> None:
    site = make_site()
    card = _make_card(src="https://content-cdn.wowgirls.com/abc123/icon.jpg")
    page = _make_page(card)

    items = await site.get_latest_items(page)

    assert items[0].thumbnail_url is not None
    assert "wowgirls.com" in str(items[0].thumbnail_url)


@pytest.mark.asyncio
async def test_get_latest_items_empty() -> None:
    site = make_site()
    page = _make_page()

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_get_latest_items_skips_missing_url() -> None:
    site = make_site()
    card = _make_card(href=None)
    page = _make_page(card)

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_get_latest_items_skips_missing_title() -> None:
    site = make_site()
    card = _make_card(title_text=None, src=None)
    page = _make_page(card)

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_get_latest_items_multiple_cards() -> None:
    site = make_site()
    card1 = _make_card("/film/a/film-one", "Film One")
    card2 = _make_card("/film/b/film-two", "Film Two")
    page = _make_page(card1, card2)

    items = await site.get_latest_items(page)

    assert len(items) == 2
    assert [i.title for i in items] == ["Film One", "Film Two"]
