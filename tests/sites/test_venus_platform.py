from unittest.mock import AsyncMock, MagicMock

import pytest

from adult_sub_monitor.models import Item, SiteConfig
from adult_sub_monitor.sites.venus_platform import VenusPlatformSite


class AsyncContextManager:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


def make_site() -> VenusPlatformSite:
    return VenusPlatformSite(
        SiteConfig(
            name="venus-test",
            type="venus_platform",
            base_url="https://venus.example",
            login_url="https://venus.example/login",
            probe_url="https://venus.example/account",
            listing_url="https://venus.example/videos",
            credentials_env_user="VENUS_USER",
            credentials_env_pass="VENUS_PASS",
        )
    )


def _page_with_cards(card_count: int) -> AsyncMock:
    cards = [MagicMock(name=f"card-{index}") for index in range(card_count)]
    card_locator = MagicMock()
    card_locator.count = AsyncMock(return_value=card_count)
    card_locator.nth = MagicMock(side_effect=lambda index: cards[index])

    page = AsyncMock()
    page.goto = AsyncMock()
    page.locator = MagicMock(return_value=card_locator)
    return page


@pytest.mark.asyncio
async def test_get_latest_items_all_videos(mocker) -> None:
    site = make_site()
    page = _page_with_cards(2)
    mocker.patch.object(site, "_is_video_card", AsyncMock(return_value=True))
    mocker.patch.object(
        site,
        "_first_text",
        AsyncMock(side_effect=["First Video", "Second Video"]),
    )
    mocker.patch.object(
        site,
        "_first_attribute",
        AsyncMock(
            side_effect=[
                "/videos/1",
                "/thumbs/1.jpg",
                "/videos/2",
                "/thumbs/2.jpg",
            ]
        ),
    )
    mocker.patch.object(
        site,
        "_all_text",
        AsyncMock(side_effect=[["Performer A"], ["Tag A"], ["Performer B"], ["Tag B"]]),
    )

    items = await site.get_latest_items(page)

    assert len(items) == 2
    assert all(isinstance(item, Item) for item in items)
    assert [item.site_name for item in items] == ["venus-test", "venus-test"]
    assert [item.title for item in items] == ["First Video", "Second Video"]


@pytest.mark.asyncio
async def test_get_latest_items_filters_non_video(mocker) -> None:
    site = make_site()
    page = _page_with_cards(3)
    mocker.patch.object(
        site,
        "_is_video_card",
        AsyncMock(side_effect=[True, False, True]),
    )
    mocker.patch.object(
        site,
        "_first_text",
        AsyncMock(side_effect=["First Video", "Third Video"]),
    )
    mocker.patch.object(
        site,
        "_first_attribute",
        AsyncMock(
            side_effect=[
                "/videos/1",
                "/thumbs/1.jpg",
                "/videos/3",
                "/thumbs/3.jpg",
            ]
        ),
    )
    mocker.patch.object(
        site,
        "_all_text",
        AsyncMock(side_effect=[["Performer A"], ["Tag A"], ["Performer C"], ["Tag C"]]),
    )

    items = await site.get_latest_items(page)

    assert len(items) == 2
    assert [item.title for item in items] == ["First Video", "Third Video"]
    assert [item.site_name for item in items] == ["venus-test", "venus-test"]


@pytest.mark.asyncio
async def test_get_latest_items_empty_listing() -> None:
    site = make_site()
    page = _page_with_cards(0)

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_is_logged_in_true() -> None:
    site = make_site()
    indicator = MagicMock()
    indicator.count = AsyncMock(return_value=1)
    page = AsyncMock()
    page.locator = MagicMock(return_value=indicator)

    assert await site.is_logged_in(page) is True


@pytest.mark.asyncio
async def test_is_logged_in_false() -> None:
    site = make_site()
    indicator = MagicMock()
    indicator.count = AsyncMock(return_value=0)
    page = AsyncMock()
    page.locator = MagicMock(return_value=indicator)

    assert await site.is_logged_in(page) is False


@pytest.mark.asyncio
async def test_login_success(mocker) -> None:
    site = make_site()
    page = AsyncMock()
    page.expect_navigation = MagicMock(return_value=AsyncContextManager())
    mocker.patch.object(site, "is_logged_in", AsyncMock(return_value=True))

    await site.login(page, "user@example.test", "secret")

    assert page.fill.await_count == 2
    page.click.assert_awaited_once()
    page.expect_navigation.assert_called_once_with(wait_until="domcontentloaded")


@pytest.mark.asyncio
async def test_login_failure_raises(mocker) -> None:
    site = make_site()
    page = AsyncMock()
    page.expect_navigation = MagicMock(return_value=AsyncContextManager())
    mocker.patch.object(site, "is_logged_in", AsyncMock(return_value=False))

    with pytest.raises(RuntimeError, match="Login failed for venus-test"):
        await site.login(page, "user@example.test", "secret")


@pytest.mark.asyncio
async def test_get_latest_items_skips_missing_title_or_url(mocker) -> None:
    site = make_site()
    page = _page_with_cards(2)
    mocker.patch.object(site, "_is_video_card", AsyncMock(return_value=True))
    mocker.patch.object(site, "_first_text", AsyncMock(side_effect=[None, "Has Title"]))
    mocker.patch.object(site, "_first_attribute", AsyncMock(return_value=None))

    assert await site.get_latest_items(page) == []


@pytest.mark.asyncio
async def test_is_video_card_uses_card_match_signal() -> None:
    site = make_site()
    card = MagicMock()
    card.evaluate = AsyncMock(return_value=True)

    assert await site._is_video_card(card) is True
    card.evaluate.assert_awaited_once()


@pytest.mark.asyncio
async def test_first_text_returns_stripped_text_or_none() -> None:
    site = make_site()
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.first.inner_text = AsyncMock(return_value="  Video Title  ")
    card = MagicMock()
    card.locator = MagicMock(return_value=locator)

    assert await site._first_text(card, ".title") == "Video Title"

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)
    card.locator = MagicMock(return_value=empty_locator)

    assert await site._first_text(card, ".missing") is None


@pytest.mark.asyncio
async def test_first_attribute_returns_stripped_attribute_or_none() -> None:
    site = make_site()
    locator = MagicMock()
    locator.count = AsyncMock(return_value=1)
    locator.first.get_attribute = AsyncMock(return_value="  /videos/1  ")
    card = MagicMock()
    card.locator = MagicMock(return_value=locator)

    assert await site._first_attribute(card, "a", "href") == "/videos/1"

    locator.first.get_attribute = AsyncMock(return_value=None)

    assert await site._first_attribute(card, "a", "href") is None

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)
    card.locator = MagicMock(return_value=empty_locator)

    assert await site._first_attribute(card, "a", "href") is None


@pytest.mark.asyncio
async def test_all_text_returns_non_empty_stripped_values() -> None:
    site = make_site()
    first = MagicMock()
    first.inner_text = AsyncMock(return_value="  Performer A  ")
    second = MagicMock()
    second.inner_text = AsyncMock(return_value="  ")
    third = MagicMock()
    third.inner_text = AsyncMock(return_value="Performer B")
    locator = MagicMock()
    locator.count = AsyncMock(return_value=3)
    locator.nth = MagicMock(side_effect=[first, second, third])
    card = MagicMock()
    card.locator = MagicMock(return_value=locator)

    assert await site._all_text(card, ".performer") == ["Performer A", "Performer B"]
