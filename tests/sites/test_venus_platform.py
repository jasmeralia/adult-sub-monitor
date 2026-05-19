from unittest.mock import AsyncMock, MagicMock, patch

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


def _make_card(
    href: str | None = "/members/content/item/abc123-video-title",
    title_text: str | None = "Video Title",
    src: str | None = "https://cdn.example/thumb.jpg",
    performers: list[str] | None = None,
) -> MagicMock:
    if performers is None:
        performers = ["Performer A", "Performer B"]

    link = MagicMock()
    link.get_attribute = AsyncMock(return_value=href)
    link_locator = MagicMock()
    link_locator.count = AsyncMock(return_value=1 if href else 0)
    link_locator.first = link

    title_el = MagicMock()
    title_el.inner_text = AsyncMock(return_value=title_text or "")
    title_locator = MagicMock()
    title_locator.count = AsyncMock(return_value=1 if title_text is not None else 0)
    title_locator.first = title_el

    thumb = MagicMock()
    thumb.get_attribute = AsyncMock(return_value=src)
    thumb_locator = MagicMock()
    thumb_locator.count = AsyncMock(return_value=1 if src else 0)
    thumb_locator.first = thumb

    def performer_item(name: str) -> MagicMock:
        m = MagicMock()
        m.inner_text = AsyncMock(return_value=name)
        return m

    perf_mocks = [performer_item(p) for p in performers]
    perf_locator = MagicMock()
    perf_locator.count = AsyncMock(return_value=len(performers))
    perf_locator.nth = MagicMock(side_effect=lambda i: perf_mocks[i])

    def locator_factory(selector: str) -> MagicMock:
        if "the-tile" in selector:
            return link_locator
        if selector == ".metadata .title":
            return title_locator
        if selector == "img.thumb":
            return thumb_locator
        if selector == ".model-tag-label":
            return perf_locator
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


def _no_tags() -> AsyncMock:
    return AsyncMock(return_value=[])


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
    card1 = _make_card("/members/content/item/abc-first", "First Video")
    card2 = _make_card("/members/content/item/def-second", "Second Video")
    page = _make_page(card1, card2)

    with patch.object(site, "_fetch_tags", _no_tags()):
        items = await site.get_latest_items(page)

    assert len(items) == 2
    assert all(isinstance(item, Item) for item in items)
    assert items[0].title == "First Video"
    assert items[1].title == "Second Video"
    assert items[0].site_name == "venus-test"


@pytest.mark.asyncio
async def test_get_latest_items_deduplicates_repeated_tiles() -> None:
    site = make_site()
    href = "/members/content/item/abc-video"
    card1 = _make_card(href, "My Video")
    card2 = _make_card(href, "My Video")
    card3 = _make_card(href, "My Video")
    page = _make_page(card1, card2, card3)

    with patch.object(site, "_fetch_tags", _no_tags()):
        items = await site.get_latest_items(page)

    assert len(items) == 1
    assert items[0].title == "My Video"


@pytest.mark.asyncio
async def test_get_latest_items_includes_performers() -> None:
    site = make_site()
    card = _make_card(performers=["Chanel X", "Vixi Rafi"])
    page = _make_page(card)

    with patch.object(site, "_fetch_tags", _no_tags()):
        items = await site.get_latest_items(page)

    assert items[0].performers == ["Chanel X", "Vixi Rafi"]


@pytest.mark.asyncio
async def test_get_latest_items_includes_tags() -> None:
    site = make_site()
    card = _make_card()
    page = _make_page(card)

    with patch.object(
        site, "_fetch_tags", AsyncMock(return_value=["Solo", "Lingerie"])
    ):
        items = await site.get_latest_items(page)

    assert items[0].tags == ["Solo", "Lingerie"]


@pytest.mark.asyncio
async def test_get_latest_items_absolute_url() -> None:
    site = make_site()
    card = _make_card(
        "/members/content/item/abc123-naughty-sweetness", "Naughty Sweetness"
    )
    page = _make_page(card)

    with patch.object(site, "_fetch_tags", _no_tags()):
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
async def test_get_latest_items_falls_back_to_img_alt_for_title() -> None:
    site = make_site()
    thumb = MagicMock()
    thumb.get_attribute = AsyncMock(
        side_effect=["Alt Title", "https://cdn.example/t.jpg"]
    )
    thumb_locator = MagicMock()
    thumb_locator.count = AsyncMock(return_value=1)
    thumb_locator.first = thumb

    title_locator = MagicMock()
    title_locator.count = AsyncMock(return_value=0)

    link = MagicMock()
    link.get_attribute = AsyncMock(return_value="/members/content/item/abc")
    link_locator = MagicMock()
    link_locator.count = AsyncMock(return_value=1)
    link_locator.first = link

    perf_locator = MagicMock()
    perf_locator.count = AsyncMock(return_value=0)

    def locator_factory(selector: str) -> MagicMock:
        if "the-tile" in selector:
            return link_locator
        if selector == ".metadata .title":
            return title_locator
        if selector == "img.thumb":
            return thumb_locator
        return perf_locator

    card = MagicMock()
    card.locator = MagicMock(side_effect=locator_factory)
    page = _make_page(card)

    with patch.object(site, "_fetch_tags", _no_tags()):
        items = await site.get_latest_items(page)

    assert len(items) == 1
    assert items[0].title == "Alt Title"


@pytest.mark.asyncio
async def test_get_latest_items_skips_card_without_title() -> None:
    site = make_site()
    card = _make_card(title_text=None, src=None)
    page = _make_page(card)

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_get_latest_items_no_thumbnail_url() -> None:
    site = make_site()
    card = _make_card(src=None)
    page = _make_page(card)

    with patch.object(site, "_fetch_tags", _no_tags()):
        items = await site.get_latest_items(page)

    assert len(items) == 1
    assert items[0].thumbnail_url is None


@pytest.mark.asyncio
async def test_get_latest_items_tags_empty() -> None:
    site = make_site()
    card = _make_card()
    page = _make_page(card)

    with patch.object(site, "_fetch_tags", _no_tags()):
        items = await site.get_latest_items(page)

    assert items[0].tags == []


@pytest.mark.asyncio
async def test_fetch_tags_navigates_and_extracts() -> None:
    site = make_site()

    def tag_item(name: str) -> MagicMock:
        m = MagicMock()
        m.inner_text = AsyncMock(return_value=name)
        return m

    tag_mocks = [tag_item("Solo"), tag_item("Lingerie")]
    tag_locator = MagicMock()
    tag_locator.count = AsyncMock(return_value=2)
    tag_locator.nth = MagicMock(side_effect=lambda i: tag_mocks[i])

    page = AsyncMock()
    page.locator = MagicMock(return_value=tag_locator)

    tags = await site._fetch_tags(
        page, "https://venus.example/members/content/item/abc"
    )

    page.goto.assert_awaited_once_with(
        "https://venus.example/members/content/item/abc", wait_until="domcontentloaded"
    )
    assert tags == ["Solo", "Lingerie"]


@pytest.mark.asyncio
async def test_fetch_tags_returns_empty_when_none_present() -> None:
    site = make_site()

    tag_locator = MagicMock()
    tag_locator.count = AsyncMock(return_value=0)

    page = AsyncMock()
    page.locator = MagicMock(return_value=tag_locator)

    tags = await site._fetch_tags(
        page, "https://venus.example/members/content/item/abc"
    )

    assert tags == []
