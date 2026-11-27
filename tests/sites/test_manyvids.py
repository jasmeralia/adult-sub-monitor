import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeout

from adult_sub_monitor.models import (
    Item,
    ManyVidsCreator,
    ManyVidsScrapingConfig,
    SiteConfig,
)
from adult_sub_monitor.sites.manyvids import (
    _DESCRIPTION_PATTERN,
    TAGS_SELECTOR,
    WEBDRIVER_MASK_SCRIPT,
    CreatorResult,
    ManyVidsSite,
    ScraperBlockedError,
    ScraperError,
    ScraperNotFoundError,
    StorePayloadError,
    VideoData,
    _build_page_url,
    _dump_debug_html,
    _format_iso_duration,
    _normalize_tag,
    _parse_store_page,
)
from tests.sites.manyvids_payload import build_store_html, video_spec

FIXTURES = Path(__file__).parents[1] / "fixtures" / "manyvids"


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _mutate_store_records(html: str, mutation) -> str:
    encoded = html.split("self.__next_f.push([1,", 1)[1].split("])</script>", 1)[0]
    chunk_text = json.loads(encoded)
    payload = json.loads(chunk_text[3:])
    records = payload[3]["children"][3]["recordMap"]
    mutation(records)
    return (
        "<script>self.__next_f.push([1,"
        + json.dumps("6b:" + json.dumps(payload, separators=(",", ":")))
        + "])</script>"
    )


def _creator() -> ManyVidsCreator:
    return ManyVidsCreator(
        creator_id="1002990973",
        creator_name="creator_slug",
        display_name="Creator Name",
    )


def test_parse_store_page_reads_stripped_captured_payload() -> None:
    store = _parse_store_page(_fixture("relay_store_captured_excerpt.html"))
    assert store.regular_total == 359
    assert store.mobile_total == 49
    assert store.total_pages == 6
    assert len(store.videos) == 2
    assert all(video.video_id.isdigit() for video in store.videos)
    assert all(video.price_cents is not None for video in store.videos)


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


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("18And19YrsOld", "18 And 19 Yrs Old"),
        ("Teens18Plus", "Teens 18 Plus"),
        ("Nylons", "Nylons"),
        ("PantyFetish", "Panty Fetish"),
        ("Pantyhose", "Pantyhose"),
        ("POVBlowjob", "POV Blowjob"),
        ("naughty", "Naughty"),
        ("deeppenetration", "Deep Penetration"),
        ("bigdildo", "Big Dildo"),
        ("#naughty girl", "Naughty Girl"),
    ],
)
def test_normalize_tag(raw: str, expected: str) -> None:
    assert _normalize_tag(raw) == expected


@pytest.mark.asyncio
async def test_fetch_video_details_parses_fixture_and_normalizes() -> None:
    site = _site()
    page = AsyncMock()
    locator = MagicMock()
    locator.all_inner_texts = AsyncMock(return_value=[])
    page.locator = MagicMock(return_value=locator)
    page.content = AsyncMock(return_value=_fixture("video_detail_with_tags.html"))

    tags, description = await site._fetch_video_details(
        page, "https://www.manyvids.com/Video/101/slug"
    )

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
    assert description == "A steamy POV scene."


def test_description_pattern_matches_og_meta() -> None:
    html = '<meta property="og:description" content="She needed to pee badly." />'
    m = _DESCRIPTION_PATTERN.search(html)
    assert m is not None
    assert m.group("value") == "She needed to pee badly."


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        (
            '<meta property="og:description" content="It\'s huge, isn\'t it?" />',
            "It's huge, isn't it?",
        ),
        (
            "<meta property='og:description' content='Say \"hello\" to her' />",
            'Say "hello" to her',
        ),
    ],
)
def test_description_pattern_keeps_embedded_quotes(html: str, expected: str) -> None:
    m = _DESCRIPTION_PATTERN.search(html)
    assert m is not None
    assert m.group("value") == expected


def test_description_pattern_returns_none_on_missing() -> None:
    assert _DESCRIPTION_PATTERN.search("<html><body>no meta</body></html>") is None


@pytest.mark.asyncio
async def test_load_page_does_not_wait_for_network_idle() -> None:
    site = _site()
    page = AsyncMock()
    page.title = AsyncMock(return_value="Creator videos")
    page.content = AsyncMock(return_value="<html></html>")
    url = "https://www.manyvids.com/Profile/1/creator/Store/Videos?sort=newest"

    await site._load_page(page, url)

    page.goto.assert_awaited_once_with(
        url,
        wait_until="domcontentloaded",
        timeout=site.scraping.page_timeout,
    )
    page.wait_for_function.assert_awaited_once()


@pytest.mark.asyncio
async def test_load_page_identifies_nextjs_not_found_page() -> None:
    site = _site()
    page = AsyncMock()
    page.title = AsyncMock(return_value="Creator videos")
    page.wait_for_function = AsyncMock(side_effect=PlaywrightTimeout("timeout"))
    page.content = AsyncMock(
        return_value='<script>"NEXT_HTTP_ERROR_FALLBACK;404"</script>'
    )

    with pytest.raises(ScraperNotFoundError, match="returned a 404 page"):
        await site._load_page(
            page,
            "https://www.manyvids.com/Profile/1/creator/Store/Videos?sort=newest",
        )


@pytest.mark.asyncio
async def test_load_page_reports_rsc_timeout_without_not_found_marker() -> None:
    site = _site()
    page = AsyncMock()
    page.title = AsyncMock(return_value="Creator videos")
    page.wait_for_function = AsyncMock(side_effect=PlaywrightTimeout("timeout"))
    page.content = AsyncMock(return_value="<html>temporarily unavailable</html>")

    with pytest.raises(ScraperError, match="RSC video payload never appeared"):
        await site._load_page(
            page,
            "https://www.manyvids.com/Profile/1/creator/Store/Videos?sort=newest",
        )


def test_parse_store_page_extracts_regular_and_mobile_videos() -> None:
    store = _parse_store_page(
        build_store_html(
            [video_spec("101", "First & Video"), video_spec("102", "Second")],
            [video_spec("201", "Mobile")],
        )
    )
    assert [video.video_id for video in store.videos] == ["101", "102", "201"]
    assert [video.video_type for video in store.videos] == [
        "regular",
        "regular",
        "mobile",
    ]
    assert store.videos[0].url.endswith("/Video/101/first-&-video")
    assert store.videos[0].thumbnail_url == "https://cdn.example/101.jpg"
    assert store.videos[0].duration == "02:13"
    assert store.videos[0].price_regular == "5.99"
    assert store.videos[0].price_cents == 599
    assert store.videos[0].list_price_cents == 599
    assert store.regular_total == 2
    assert store.mobile_total == 1
    assert store.regular_page_size == 70
    assert store.mobile_page_size == 14
    assert store.total_pages == 1
    assert "999999" not in [video.video_id for video in store.videos]


def test_parse_store_page_maps_on_sale_price() -> None:
    video = _parse_store_page(
        build_store_html(
            [
                video_spec(
                    "1",
                    regular="24.99",
                    discounted="21.24",
                    on_sale=True,
                    promo_rate=15,
                )
            ]
        )
    ).videos[0]
    assert (video.price_cents, video.list_price_cents, video.price_regular) == (
        2124,
        2499,
        "24.99",
    )


def test_parse_store_page_free_video() -> None:
    video = _parse_store_page(
        build_store_html([video_spec("1", free=True, regular="9.99")])
    ).videos[0]
    assert (video.price_cents, video.list_price_cents) == (0, 999)


def test_parse_store_page_total_pages_uses_larger_connection() -> None:
    store = _parse_store_page(
        build_store_html([], [], regular_total=10, mobile_total=50)
    )
    assert store.total_pages == 4
    assert store.expected_total == 60
    store = _parse_store_page(
        build_store_html([], [], regular_total=359, mobile_total=49)
    )
    assert store.total_pages == 6


def test_parse_store_page_without_mobile_connection() -> None:
    store = _parse_store_page(
        build_store_html([video_spec("1")], include_mobile_connection=False)
    )
    assert store.mobile_total == 0
    assert [v.video_id for v in store.videos] == ["1"]


def test_parse_store_page_dedupes_video_in_both_connections() -> None:
    store = _parse_store_page(build_store_html([video_spec("1")], [video_spec("1")]))
    assert len(store.videos) == 1
    assert store.videos[0].video_type == "regular"


def test_parse_store_page_keeps_same_title_distinct_ids() -> None:
    store = _parse_store_page(
        build_store_html(
            [video_spec("7503619", "Same Title")],
            [video_spec("7503623", "Same Title")],
        )
    )
    assert [(v.video_id, v.video_type) for v in store.videos] == [
        ("7503619", "regular"),
        ("7503623", "mobile"),
    ]


def test_parse_store_page_skips_non_numeric_local_id() -> None:
    store = _parse_store_page(build_store_html([video_spec("abc")]))
    assert store.videos == []


def test_parse_store_page_tolerates_missing_price_and_screenshot() -> None:
    html = build_store_html([video_spec("1", thumbnail=None)])

    def remove_price(records):
        record = next(
            record
            for record in records.values()
            if isinstance(record, dict) and record.get("__typename") == "Video"
        )
        record.pop("price")

    video = _parse_store_page(_mutate_store_records(html, remove_price)).videos[0]
    assert video.price_cents is None
    assert video.price_regular is None
    assert video.thumbnail_url is None


@pytest.mark.parametrize(
    "html",
    [
        "<html></html>",
        '<script>self.__next_f.push([1,"6b:{"isVideosStore":true}"])</script>',
    ],
)
def test_parse_store_page_raises_when_record_map_missing(html: str) -> None:
    with pytest.raises(StorePayloadError, match="recordMap not found"):
        _parse_store_page(html)


def test_parse_store_page_raises_when_no_store_connection() -> None:
    html = build_store_html([])

    def remove_connections(records):
        profile = records["PROF1"]
        for key in list(profile):
            if key.startswith("storeVideos("):
                del profile[key]

    with pytest.raises(StorePayloadError, match="no storeVideos connection"):
        _parse_store_page(_mutate_store_records(html, remove_connections))


def test_parse_store_page_rejects_non_numeric_total_count() -> None:
    html = build_store_html([video_spec("101")])

    def corrupt_total(records):
        for record in records.values():
            if record.get("__typename") == "ProfileStoreVideoConnection":
                record["totalCount"] = "lots"

    with pytest.raises(StorePayloadError, match="invalid totalCount"):
        _parse_store_page(_mutate_store_records(html, corrupt_total))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PT14M30S", "14:30"),
        ("PT1H2M36S", "01:02:36"),
        ("PT45S", "00:45"),
        ("PT2H", "02:00:00"),
        ("PT7M5.5S", "07:05"),
        ("PT90M", "01:30:00"),
        ("16:41", "16:41"),
        ("PT", None),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_format_iso_duration(value: object, expected: str | None) -> None:
    assert _format_iso_duration(value) == expected


def test_build_page_url() -> None:
    first = parse_qs(urlparse(_build_page_url("1", "creator", 1)).query)
    third = parse_qs(urlparse(_build_page_url("1", "creator", 3)).query)
    assert first == {"sort": ["newest"]}
    assert third == {"sort": ["newest"], "page": ["3"]}


@pytest.mark.asyncio
async def test_scrape_creator_early_stops_when_page_ids_are_known() -> None:
    site = _site()
    payload = build_store_html(
        [video_spec(str(i)) for i in range(1, 71)], regular_total=140
    )
    load = AsyncMock(return_value=payload)
    with patch.object(site, "_load_page", load):
        result = await site._scrape_creator(
            AsyncMock(), _creator(), {str(i) for i in range(1, 71)}
        )
    assert load.await_count == 1
    assert len(result.videos) == 70


@pytest.mark.asyncio
async def test_scrape_creator_follows_pages_until_total_pages() -> None:
    site = _site()
    payload1 = build_store_html([video_spec("1")], regular_total=140)
    payload2 = build_store_html([video_spec("2")], page=2, regular_total=140)
    load = AsyncMock(side_effect=[payload1, payload2])
    with (
        patch.object(site, "_load_page", load),
        patch("adult_sub_monitor.sites.manyvids.asyncio.sleep", new=AsyncMock()),
    ):
        result = await site._scrape_creator(AsyncMock(), _creator(), set())
    assert load.await_count == 2
    assert "page=2" in load.await_args_list[1].args[1]
    assert [v.video_id for v in result.videos] == ["1", "2"]


@pytest.mark.asyncio
async def test_scrape_creator_keeps_videos_when_later_page_is_not_found() -> None:
    site = _site()
    payload = build_store_html([video_spec("1")], regular_total=140)
    load = AsyncMock(side_effect=[payload, ScraperNotFoundError("page unavailable")])
    with (
        patch.object(site, "_load_page", load),
        patch("adult_sub_monitor.sites.manyvids.asyncio.sleep", new=AsyncMock()),
    ):
        result = await site._scrape_creator(AsyncMock(), _creator(), set())
    assert [video.video_id for video in result.videos] == ["1"]
    assert result.error is None


@pytest.mark.asyncio
async def test_scrape_creator_unparseable_payload_raises_and_retries() -> None:
    site = _site()
    load = AsyncMock(return_value="<html></html>")
    with (
        patch.object(site, "_load_page", load),
        patch("adult_sub_monitor.sites.manyvids.asyncio.sleep", new=AsyncMock()),
    ):
        result = await site.scrape_creator_with_retry(AsyncMock(), _creator(), set())
    assert "recordMap not found" in (result.error or "")
    assert load.await_count == site.scraping.max_retries + 1


@pytest.mark.asyncio
async def test_scrape_creator_writes_debug_dump_when_env_set(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("MANYVIDS_DEBUG_DUMP_DIR", str(tmp_path))
    site = _site()
    with (
        patch.object(site, "_load_page", new=AsyncMock(return_value="<bad/>")),
        pytest.raises(StorePayloadError),
    ):
        await site._scrape_creator(AsyncMock(), _creator(), set())
    files = list(tmp_path.glob("store-1002990973-p1-*.html"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == "<bad/>"


def test_scrape_creator_no_debug_dump_when_env_unset(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MANYVIDS_DEBUG_DUMP_DIR", raising=False)
    assert _dump_debug_html("<bad/>", "1", 1) is None
    assert list(tmp_path.iterdir()) == []


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
async def test_scrape_creator_with_retry_does_not_retry_missing_profile() -> None:
    site = _site()
    page = AsyncMock()
    load_page = AsyncMock(
        side_effect=ScraperNotFoundError("creator profile is unavailable")
    )

    with patch.object(site, "_load_page", load_page):
        result = await site.scrape_creator_with_retry(page, _creator(), set())

    assert result.error == "creator profile is unavailable"
    assert result.videos == []
    load_page.assert_awaited_once()


@pytest.mark.asyncio
async def test_scrape_creator_with_retry_retries_generic_scraper_error() -> None:
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
            new=AsyncMock(side_effect=[ScraperError("transient"), expected]),
        ),
        patch("adult_sub_monitor.sites.manyvids.asyncio.sleep", new=AsyncMock()),
    ):
        result = await site.scrape_creator_with_retry(page, _creator(), set())

    assert result is expected


@pytest.mark.asyncio
async def test_get_latest_items_populates_manyvids_metadata() -> None:
    site = _site()
    page = AsyncMock()
    db = AsyncMock()
    db.get_known_titles = AsyncMock(return_value=set())
    db.get_known_item_ids = AsyncMock(return_value=set())
    video = VideoData(
        video_id="101",
        title="First Video",
        slug="first-video",
        url="https://www.manyvids.com/Video/101/first-video",
        video_type="mobile",
        thumbnail_url="https://cdn.example/thumb.jpg",
        price_regular="5.99",
        duration="02:13",
        price_cents=599,
        list_price_cents=599,
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
        patch.object(
            site,
            "_fetch_video_details",
            new=AsyncMock(return_value=(["POV"], "A short description.")),
        ),
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
            description="A short description.",
        )
    ]
    db.get_known_titles.assert_awaited_once_with("ManyVids")
    db.get_known_item_ids.assert_awaited_once_with("ManyVids")


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


@pytest.mark.asyncio
async def test_fetch_video_details_reads_tags_without_per_element_waits() -> None:
    site = _site()
    page = AsyncMock()
    locator = MagicMock()
    locator.all_inner_texts = AsyncMock(
        return_value=["POVBlowjob", " ", "POVBlowjob", "Nylons"]
    )
    locator.nth = MagicMock(side_effect=AssertionError("per-index reads can time out"))
    page.locator = MagicMock(return_value=locator)
    page.content = AsyncMock(return_value="<html></html>")

    tags, _ = await site._fetch_video_details(
        page, "https://www.manyvids.com/Video/1/x"
    )

    assert tags == ["POV Blowjob", "Nylons"]


@pytest.mark.asyncio
async def test_full_scan_reads_every_page_without_early_stop() -> None:
    site = _site()
    payloads = [
        build_store_html([video_spec(str(i)) for i in range(1, 71)], regular_total=150),
        build_store_html(
            [video_spec(str(i)) for i in range(71, 141)], page=2, regular_total=150
        ),
        build_store_html(
            [video_spec(str(i)) for i in range(141, 151)], page=3, regular_total=150
        ),
    ]
    load = AsyncMock(side_effect=payloads)
    with (
        patch.object(site, "_load_page", load),
        patch("adult_sub_monitor.sites.manyvids.asyncio.sleep", new=AsyncMock()),
    ):
        result = await site._scrape_creator(
            AsyncMock(), _creator(), {str(i) for i in range(1, 151)}, full_scan=True
        )
    assert load.await_count == 3
    assert len(result.videos) == 150


@pytest.mark.asyncio
async def test_full_scan_404_before_last_page_is_an_error() -> None:
    site = _site()
    first = build_store_html(
        [video_spec(str(i)) for i in range(1, 71)], regular_total=140
    )
    load = AsyncMock(
        side_effect=[
            first,
            ScraperNotFoundError("page 2"),
            first,
            ScraperNotFoundError("page 2"),
        ]
    )
    with (
        patch.object(site, "_load_page", load),
        patch("adult_sub_monitor.sites.manyvids.asyncio.sleep", new=AsyncMock()),
    ):
        result = await site.scrape_creator_with_retry(
            AsyncMock(), _creator(), set(), full_scan=True
        )
    assert "returned 404 during full scan" in (result.error or "")


@pytest.mark.asyncio
async def test_full_scan_empty_page_before_last_page_is_an_error() -> None:
    site = _site()
    first = build_store_html(
        [video_spec(str(i)) for i in range(1, 71)], regular_total=140
    )
    empty = build_store_html([], page=2, regular_total=140)
    with (
        patch.object(site, "_load_page", new=AsyncMock(side_effect=[first, empty])),
        pytest.raises(ScraperError, match="had no videos during full scan"),
    ):
        await site._scrape_creator(AsyncMock(), _creator(), set(), full_scan=True)


@pytest.mark.asyncio
async def test_full_scan_count_mismatch_is_an_error() -> None:
    site = _site()
    payload = build_store_html([video_spec("1"), video_spec("2")], regular_total=3)
    with (
        patch.object(site, "_load_page", new=AsyncMock(return_value=payload)),
        pytest.raises(ScraperError, match="Full scan incomplete: 2 of 3"),
    ):
        await site._scrape_creator(AsyncMock(), _creator(), set(), full_scan=True)


@pytest.mark.asyncio
async def test_full_scan_empty_store_succeeds() -> None:
    site = _site()
    with patch.object(
        site, "_load_page", new=AsyncMock(return_value=build_store_html([]))
    ):
        result = await site._scrape_creator(
            AsyncMock(), _creator(), set(), full_scan=True
        )
    assert result.error is None
    assert result.videos == []


@pytest.mark.asyncio
async def test_get_latest_items_early_stops_on_ids_and_filters_on_titles() -> None:
    site = _site()
    db = AsyncMock()
    db.get_known_titles = AsyncMock(return_value={"Known Title"})
    db.get_known_item_ids = AsyncMock(
        return_value={
            "https://www.manyvids.com/Video/101/old-slug",
            "https://www.manyvids.com/Profile/1/not-a-video",
        }
    )
    videos = [
        VideoData(
            "101",
            "Known Title",
            "old-slug",
            "https://www.manyvids.com/Video/101/old-slug",
            "regular",
            None,
            "5.99",
            "02:00",
        ),
        VideoData(
            "102",
            "New Title",
            "new",
            "https://www.manyvids.com/Video/102/new",
            "mobile",
            None,
            "5.99",
            "02:00",
        ),
    ]
    scrape = AsyncMock(
        return_value=CreatorResult(
            creator_id="1002990973",
            creator_name="creator_slug",
            videos=videos,
            total_pages=1,
        )
    )
    with (
        patch.object(site, "scrape_creator_with_retry", new=scrape),
        patch.object(
            site, "_fetch_video_details", new=AsyncMock(return_value=([], None))
        ) as details,
    ):
        items = await site.get_latest_items(AsyncMock(), db)

    assert scrape.await_args.args[2] == {"101"}
    assert [item.title for item in items] == ["New Title"]
    details.assert_awaited_once()
