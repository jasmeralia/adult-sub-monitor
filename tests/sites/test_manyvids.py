from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adult_sub_monitor.models import (
    Item,
    ManyVidsCreator,
    ManyVidsScrapingConfig,
    SiteConfig,
)
from adult_sub_monitor.sites.manyvids import (
    TAGS_SELECTOR,
    WEBDRIVER_MASK_SCRIPT,
    CreatorResult,
    ManyVidsSite,
    ScraperBlockedError,
    VideoData,
    _enrich_videos_from_dom,
    _extract_rsc_video_data,
    _normalize_tag,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "manyvids"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _creator() -> ManyVidsCreator:
    return ManyVidsCreator(
        creator_id="1002990973",
        creator_name="creator_slug",
        display_name="Creator Name",
    )


def _scraping() -> ManyVidsScrapingConfig:
    return ManyVidsScrapingConfig(
        delay_between_creators_min=0,
        delay_between_creators_max=0,
        delay_between_pages_min=0,
        delay_between_pages_max=0,
        page_timeout=1000,
        max_retries=1,
        retry_backoff_base=0,
    )


def _site(creator: ManyVidsCreator | None = None) -> ManyVidsSite:
    target = creator or _creator()
    return ManyVidsSite(
        SiteConfig(
            name="manyvids",
            display_name="ManyVids",
            type="manyvids",
            base_url="https://www.manyvids.com",
            creators=[target],
        ),
        _scraping(),
        creator=target,
    )


def test_extract_rsc_video_data_on_rsc_fixture() -> None:
    videos, total_pages = _extract_rsc_video_data(
        _fixture("creator_store_regular_p1.html")
    )

    assert total_pages == 2
    assert [video.video_id for video in videos] == ["101", "102"]
    assert videos[0].title == "First & Video"
    assert videos[0].url == "https://www.manyvids.com/Video/101/first-video"
    assert videos[0].thumbnail_url == "https://cdn.example/dom-first.jpg"
    assert videos[0].price_regular == "5.99"
    assert videos[0].duration == "02:13"


def test_enrich_videos_from_dom_flips_mobile_and_overrides_thumbnail() -> None:
    videos = [
        VideoData(
            video_id="201",
            title="Mobile Video",
            slug="mobile-video",
            url="https://www.manyvids.com/Video/201/mobile-video",
            video_type="regular",
            thumbnail_url="https://cdn.example/rsc-mobile.jpg",
            price_regular="2.99",
            duration="00:59",
        )
    ]

    _enrich_videos_from_dom(_fixture("creator_store_mobile_p1.html"), videos)

    assert videos[0].video_type == "mobile"
    assert videos[0].thumbnail_url == "https://cdn.example/dom-mobile.jpg"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("18And19YrsOld", "18 And 19 Yrs Old"),
        ("Teens18Plus", "Teens 18 Plus"),
        ("Nylons", "Nylons"),
        ("PantyFetish", "Panty Fetish"),
        ("Pantyhose", "Pantyhose"),
        ("POVBlowjob", "POV Blowjob"),
    ],
)
def test_normalize_tag(raw: str, expected: str) -> None:
    assert _normalize_tag(raw) == expected


@pytest.mark.asyncio
async def test_fetch_tags_parses_fixture_and_normalizes() -> None:
    site = _site()
    page = AsyncMock()
    locator = MagicMock()
    locator.count = AsyncMock(return_value=0)
    page.locator = MagicMock(return_value=locator)
    page.content = AsyncMock(return_value=_fixture("video_detail_with_tags.html"))

    tags = await site._fetch_tags(page, "https://www.manyvids.com/Video/101/slug")

    page.goto.assert_awaited_once_with(
        "https://www.manyvids.com/Video/101/slug",
        wait_until="domcontentloaded",
        timeout=1000,
    )
    page.locator.assert_called_once_with(TAGS_SELECTOR)
    assert tags == [
        "Alternative Girl",
        "Big Dicks",
        "Cowgirl",
        "Cumshots",
        "Doggystyle",
        "POV",
        "POV Blowjob",
        "POV Sex",
        "Redhead",
        "Small Tits",
    ]


@pytest.mark.asyncio
async def test_scrape_creator_early_stops_when_page_titles_are_known() -> None:
    site = _site()
    page = AsyncMock()
    load_page = AsyncMock(
        side_effect=[
            _fixture("creator_store_regular_p2_known.html"),
            _fixture("creator_store_mobile_p1.html"),
        ]
    )

    with (
        patch.object(site, "_load_page", load_page),
        patch("adult_sub_monitor.sites.manyvids.asyncio.sleep", new=AsyncMock()),
    ):
        result = await site._scrape_creator(page, _creator(), {"Known Video"})

    loaded_urls = [call.args[1] for call in load_page.await_args_list]
    assert len(loaded_urls) == 2
    assert "page=2" not in loaded_urls[0]
    assert "vertical=1" in loaded_urls[0]
    assert "vertical=2" in loaded_urls[1]
    assert [video.video_id for video in result.videos] == ["103", "201"]


@pytest.mark.asyncio
async def test_scrape_creator_with_retry_retries_block_then_succeeds() -> None:
    site = _site()
    page = AsyncMock()
    expected = CreatorResult(
        creator_id="1002990973",
        creator_name="creator_slug",
        videos=[],
        total_pages=1,
    )

    with (
        patch.object(
            site,
            "_scrape_creator",
            new=AsyncMock(side_effect=[ScraperBlockedError("blocked"), expected]),
        ) as scrape_creator,
        patch("adult_sub_monitor.sites.manyvids.asyncio.sleep", new=AsyncMock()),
    ):
        result = await site.scrape_creator_with_retry(page, _creator(), set())

    assert result is expected
    assert scrape_creator.await_count == 2


@pytest.mark.asyncio
async def test_get_latest_items_populates_manyvids_metadata() -> None:
    site = _site()
    page = AsyncMock()
    db = AsyncMock()
    db.get_known_titles = AsyncMock(return_value=set())
    video = VideoData(
        video_id="101",
        title="First Video",
        slug="first-video",
        url="https://www.manyvids.com/Video/101/first-video",
        video_type="mobile",
        thumbnail_url="https://cdn.example/thumb.jpg",
        price_regular="5.99",
        duration="02:13",
    )

    with (
        patch.object(
            site,
            "scrape_creator_with_retry",
            new=AsyncMock(
                return_value=CreatorResult(
                    creator_id="1002990973",
                    creator_name="creator_slug",
                    videos=[video],
                    total_pages=1,
                )
            ),
        ),
        patch.object(site, "_fetch_tags", new=AsyncMock(return_value=["POV"])),
    ):
        items = await site.get_latest_items(page, db)

    assert items == [
        Item(
            site_name="ManyVids",
            item_id="https://www.manyvids.com/Video/101/first-video",
            title="First Video",
            url="https://www.manyvids.com/Video/101/first-video",
            thumbnail_url="https://cdn.example/thumb.jpg",
            tags=["POV"],
            duration="02:13",
            price="5.99",
            video_type="mobile",
            creator="Creator Name",
        )
    ]
    db.get_known_titles.assert_awaited_once_with("ManyVids")


def test_requires_auth_is_false() -> None:
    assert _site().requires_auth is False


def test_context_options_returns_manyvids_browser_options() -> None:
    site = _site()
    assert site.context_options() == {
        "user_agent": site.scraping.user_agent,
        "viewport": {"width": 1920, "height": 1080},
        "locale": "en-US",
        "timezone_id": "America/New_York",
    }


def test_init_scripts_contains_webdriver_mask() -> None:
    assert _site().init_scripts() == [WEBDRIVER_MASK_SCRIPT]
