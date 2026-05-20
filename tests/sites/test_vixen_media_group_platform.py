from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from adult_sub_monitor.models import SiteConfig
from adult_sub_monitor.sites.vixen_media_group_platform import VixenMediaGroupSite


class AsyncContextManager:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_args: object) -> None:
        return None


def make_site() -> VixenMediaGroupSite:
    return VixenMediaGroupSite(
        SiteConfig(
            name="deeper-test",
            type="vixen_media_group_platform",
            base_url="https://deeper.example",
            login_url="https://deeper.example/login",
            probe_url="https://deeper.example/account",
            listing_url="https://deeper.example/videos",
            credentials_env_user="DEEPER_USER",
            credentials_env_pass="DEEPER_PASS",
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
async def test_dismiss_interstitial_button_found() -> None:
    site = make_site()
    button = AsyncMock()
    page = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=button)
    page.wait_for_load_state = AsyncMock()

    assert await site.dismiss_interstitial(page) is True
    button.click.assert_awaited_once_with()
    page.wait_for_load_state.assert_awaited_once_with("networkidle", timeout=15000)


@pytest.mark.asyncio
async def test_dismiss_interstitial_button_absent() -> None:
    site = make_site()
    page = AsyncMock()
    page.wait_for_selector = AsyncMock(
        side_effect=PlaywrightTimeoutError("continue button not found")
    )

    assert await site.dismiss_interstitial(page) is False


@pytest.mark.asyncio
async def test_dismiss_interstitial_selector_returns_none() -> None:
    site = make_site()
    page = AsyncMock()
    page.wait_for_selector = AsyncMock(return_value=None)

    assert await site.dismiss_interstitial(page) is False
    page.wait_for_load_state.assert_not_called()


@pytest.mark.asyncio
async def test_get_latest_items_video_only_filtering(mocker) -> None:
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
    assert [item.site_name for item in items] == ["deeper-test", "deeper-test"]


@pytest.mark.asyncio
async def test_is_logged_in_true() -> None:
    site = make_site()
    page = AsyncMock()
    page.url = "https://members.deeper.com/videos"

    assert await site.is_logged_in(page) is True


@pytest.mark.asyncio
async def test_is_logged_in_false() -> None:
    site = make_site()
    page = AsyncMock()
    page.url = "https://login.vixen.com/i/deeper/login?"

    assert await site.is_logged_in(page) is False


@pytest.mark.asyncio
async def test_login_success(mocker) -> None:
    site = make_site()
    page = AsyncMock()
    locator = AsyncMock()
    page.locator = MagicMock(return_value=locator)
    page.expect_navigation = MagicMock(return_value=AsyncContextManager())
    mocker.patch.object(site, "is_logged_in", AsyncMock(return_value=True))

    await site.login(page, "user@example.test", "secret")

    assert locator.press_sequentially.await_count == 2
    locator.click.assert_awaited_once()
    page.expect_navigation.assert_called_once_with(wait_until="domcontentloaded")


@pytest.mark.asyncio
async def test_login_navigation_failure_is_wrapped() -> None:
    site = make_site()
    page = AsyncMock()
    locator = AsyncMock()
    locator.press_sequentially = AsyncMock(side_effect=RuntimeError("field missing"))
    page.locator = MagicMock(return_value=locator)

    with pytest.raises(RuntimeError, match="Login failed for deeper-test"):
        await site.login(page, "user@example.test", "secret")


@pytest.mark.asyncio
async def test_login_not_logged_in_raises(mocker) -> None:
    site = make_site()
    page = AsyncMock()
    locator = AsyncMock()
    page.locator = MagicMock(return_value=locator)
    page.expect_navigation = MagicMock(return_value=AsyncContextManager())
    mocker.patch.object(site, "is_logged_in", AsyncMock(return_value=False))

    with pytest.raises(RuntimeError, match="Login failed for deeper-test"):
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
async def test_is_video_card_uses_nested_signal_or_card_match() -> None:
    site = make_site()
    signal = MagicMock()
    signal.count = AsyncMock(return_value=1)
    card = MagicMock()
    card.locator = MagicMock(return_value=signal)
    card.evaluate = AsyncMock()

    assert await site._is_video_card(card) is True
    card.evaluate.assert_not_called()

    signal.count = AsyncMock(return_value=0)
    card.evaluate = AsyncMock(return_value=False)

    assert await site._is_video_card(card) is False
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

    locator.first.inner_text = AsyncMock(return_value="  ")

    assert await site._first_text(card, ".title") is None

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

    empty_locator = MagicMock()
    empty_locator.count = AsyncMock(return_value=0)
    card.locator = MagicMock(return_value=empty_locator)

    assert await site._first_attribute(card, "a", "href") is None


@pytest.mark.asyncio
async def test_all_text_returns_non_empty_stripped_values() -> None:
    site = make_site()
    first = MagicMock()
    first.inner_text = AsyncMock(return_value="  Tag A  ")
    second = MagicMock()
    second.inner_text = AsyncMock(return_value="")
    locator = MagicMock()
    locator.count = AsyncMock(return_value=2)
    locator.nth = MagicMock(side_effect=[first, second])
    card = MagicMock()
    card.locator = MagicMock(return_value=locator)

    assert await site._all_text(card, ".tag") == ["Tag A"]
