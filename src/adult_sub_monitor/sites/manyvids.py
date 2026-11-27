from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import os
import random
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from math import ceil
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlencode

from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from adult_sub_monitor.models import (
    Item,
    ManyVidsCreator,
    ManyVidsScrapingConfig,
    SiteConfig,
)
from adult_sub_monitor.pricing import (
    manyvids_video_id_from_url,
    resolve_manyvids_price,
)
from adult_sub_monitor.sites.base import BaseSite
from adult_sub_monitor.tags import format_tag

if TYPE_CHECKING:
    from adult_sub_monitor.db import Database

logger = logging.getLogger(__name__)

BASE_URL = "https://www.manyvids.com"
WEBDRIVER_MASK_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
)
TAGS_SELECTOR = (
    'a[class*="mavTag"], a[href^="/Vids?category="], '
    ".mv-hashtags a, [data-cy='tag'] a, .tags a"
)

_RSC_PATTERN = re.compile(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)', re.DOTALL)
_RECORD_MAP_KEY = re.compile(r'"recordMap"\s*:\s*')
_IS_VIDEOS_STORE = re.compile(r'"isVideosStore"\s*:\s*true')
_FIRST_ARG = re.compile(r"\bfirst:(\d+)")
_ISO_DURATION = re.compile(
    r"^PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?$"
)
_CLOCK_DURATION = re.compile(r"^\d{1,2}(?::\d{2}){1,2}$")
_MAX_STORE_PAGES = 200
DEBUG_DUMP_ENV = "MANYVIDS_DEBUG_DUMP_DIR"


@dataclass
class VideoData:  # pylint: disable=too-many-instance-attributes
    video_id: str
    title: str
    slug: str
    url: str
    video_type: str
    thumbnail_url: str | None
    price_regular: str | None
    duration: str | None
    price_cents: int | None = None
    list_price_cents: int | None = None


@dataclass
class StorePage:
    videos: list[VideoData]
    regular_total: int
    regular_page_size: int
    mobile_total: int
    mobile_page_size: int

    @property
    def total_pages(self) -> int:
        regular_pages = (
            ceil(self.regular_total / self.regular_page_size)
            if self.regular_page_size
            else 0
        )
        mobile_pages = (
            ceil(self.mobile_total / self.mobile_page_size)
            if self.mobile_page_size
            else 0
        )
        return max(regular_pages, mobile_pages, 1)

    @property
    def expected_total(self) -> int:
        return self.regular_total + self.mobile_total


@dataclass
class CreatorResult:
    creator_id: str
    creator_name: str
    videos: list[VideoData]
    total_pages: int
    error: str | None = None


class ScraperError(Exception):
    pass


class ScraperBlockedError(ScraperError):
    pass


class ScraperNotFoundError(ScraperError):
    pass


class StorePayloadError(ScraperError):
    pass


class _TagHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[str] = []
        self._seen: set[str] = set()
        self._container_depth = 0
        self._capture_anchor = False
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = (attr_map.get("class") or "").split()
        class_set = set(classes)
        is_tag_container = bool({"mv-hashtags", "tags"} & class_set) or (
            attr_map.get("data-cy") == "tag"
        )
        if is_tag_container or self._container_depth:
            self._container_depth += 1

        if tag == "a":
            href = attr_map.get("href") or ""
            is_tag_anchor = (
                self._container_depth > 0
                or attr_map.get("data-cy") == "tag"
                or any("mavTag" in cls for cls in classes)
                or href.startswith("/Vids?category=")
            )
            if is_tag_anchor:
                self._capture_anchor = True
                self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_anchor:
            text = "".join(self._anchor_text)
            normalized = _normalize_tag(text)
            if normalized and normalized not in self._seen:
                self._seen.add(normalized)
                self.tags.append(normalized)
            self._capture_anchor = False
            self._anchor_text = []

        if self._container_depth:
            self._container_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._capture_anchor:
            self._anchor_text.append(data)


_DESCRIPTION_PATTERN = re.compile(
    r'<meta[^>]+property=["\']og:description["\'][^>]+'
    r"content=(?P<quote>[\"'])(?P<value>(?:(?!(?P=quote)).){1,4096})(?P=quote)",
    re.IGNORECASE | re.DOTALL,
)

_CAMEL_BOUNDARY = re.compile(r"([a-z])([A-Z])")
_ACRONYM_BOUNDARY = re.compile(r"([A-Z]+)([A-Z][a-z])")
_DIGIT_TO_LETTER = re.compile(r"(\d)([A-Za-z])")
_LETTER_TO_DIGIT = re.compile(r"([A-Za-z])(\d)")


def _normalize_tag(text: str) -> str | None:
    normalized = text.strip().lstrip("#").strip()
    if not normalized:
        return None
    normalized = _CAMEL_BOUNDARY.sub(r"\1 \2", normalized)
    normalized = _ACRONYM_BOUNDARY.sub(r"\1 \2", normalized)
    normalized = _DIGIT_TO_LETTER.sub(r"\1 \2", normalized)
    normalized = _LETTER_TO_DIGIT.sub(r"\1 \2", normalized)
    return format_tag(normalized)


def _format_iso_duration(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    match = _ISO_DURATION.fullmatch(value)
    if match and value != "PT":
        hours = int(match.group("h") or 0)
        minutes = int(match.group("m") or 0)
        seconds = int(float(match.group("s") or 0))
        total_seconds = hours * 3600 + minutes * 60 + seconds
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"
    if _CLOCK_DURATION.fullmatch(value):
        return value
    logger.debug("Unrecognized ManyVids duration: %r", value)
    return None


def _has_store_connection(records: Mapping[str, object]) -> bool:
    return any(
        isinstance(record, dict)
        and record.get("__typename") == "Profile"
        and any(
            isinstance(key, str) and key.startswith("storeVideos(") for key in record
        )
        for record in records.values()
    )


def _extract_record_map(html: str) -> dict[str, dict[str, object]] | None:
    """Return the store recordMap from the isVideosStore RSC chunk.

    One chunk can embed several recordMaps (a small profile-header map, the
    store map, and non-dict references), in render order. Prefer the first
    map whose Profile has a storeVideos connection; otherwise fall back to the
    first dict map so the caller reports what is missing.
    """
    fallback: dict[str, dict[str, object]] | None = None
    decoder = json.JSONDecoder()
    for match in _RSC_PATTERN.finditer(html):
        try:
            decoded = json.loads('"' + match.group(1) + '"')
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(decoded, str) or not _IS_VIDEOS_STORE.search(decoded):
            continue
        for record_map_match in _RECORD_MAP_KEY.finditer(decoded):
            try:
                obj, _ = decoder.raw_decode(decoded, record_map_match.end())
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            if _has_store_connection(obj):
                return obj
            if fallback is None:
                fallback = obj
    return fallback


def _deref(records: Mapping[str, object], link: object) -> dict[str, object] | None:
    if not isinstance(link, dict) or not isinstance(link.get("__ref"), str):
        return None
    record = records.get(link["__ref"])
    return record if isinstance(record, dict) else None


def _parse_store_page(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    html: str,
) -> StorePage:
    records = _extract_record_map(html)
    if records is None:
        raise StorePayloadError("ManyVids store recordMap not found in page HTML")
    root = records.get("client:root")
    profile = None
    if isinstance(root, dict):
        for key, value in root.items():
            if isinstance(key, str) and key.startswith("profile("):
                profile = _deref(records, value)
                if profile is not None:
                    break
    if profile is None:
        profile = next(
            (
                record
                for record in records.values()
                if isinstance(record, dict) and record.get("__typename") == "Profile"
            ),
            None,
        )
    if profile is None:
        raise StorePayloadError("ManyVids store payload has no Profile record")

    regular: tuple[str, dict[str, object]] | None = None
    mobile: tuple[str, dict[str, object]] | None = None
    for key, value in profile.items():
        if not isinstance(key, str) or not key.startswith("storeVideos("):
            continue
        connection = _deref(records, value)
        if connection is None:
            continue
        if '"vertical":true' in key:
            if mobile is None:
                mobile = (key, connection)
        elif regular is None:
            regular = (key, connection)
    if regular is None:
        raise StorePayloadError("ManyVids store payload has no storeVideos connection")

    videos: list[VideoData] = []
    totals: dict[str, int] = {"regular": 0, "mobile": 0}
    sizes: dict[str, int] = {"regular": 0, "mobile": 0}
    for video_type, entry in (("regular", regular), ("mobile", mobile)):
        if entry is None:
            continue
        key, connection = entry
        raw_total = connection.get("totalCount")
        try:
            total = int(raw_total) if isinstance(raw_total, (int, str)) else 0
        except ValueError as exc:
            raise StorePayloadError(
                f"ManyVids store connection has invalid totalCount {raw_total!r}"
            ) from exc
        totals[video_type] = total
        edges = connection.get("edges")
        edge_refs: list[str] = []
        if isinstance(edges, dict) and isinstance(edges.get("__refs"), list):
            edge_refs = [ref for ref in edges["__refs"] if isinstance(ref, str)]
        elif isinstance(edges, list):
            edge_refs = [
                ref["__ref"]
                for ref in edges
                if isinstance(ref, dict) and isinstance(ref.get("__ref"), str)
            ]
        first_match = _FIRST_ARG.search(key)
        sizes[video_type] = int(first_match.group(1)) if first_match else len(edge_refs)
        for edge_ref in edge_refs:
            edge = records.get(edge_ref)
            video = (
                _deref(records, edge.get("node")) if isinstance(edge, dict) else None
            )
            if video is None:
                logger.debug("Skipping malformed ManyVids edge %s", edge_ref)
                continue
            local_id, slug, title = (
                video.get("localId"),
                video.get("slug"),
                video.get("title"),
            )
            if (  # pylint: disable=too-many-boolean-expressions
                video.get("__typename") != "Video"
                or not isinstance(local_id, str)
                or not local_id.isdigit()
                or not isinstance(slug, str)
                or not slug
                or not isinstance(title, str)
                or not title
            ):
                logger.debug("Skipping malformed ManyVids video record %s", edge_ref)
                continue
            screenshot = _deref(records, video.get("screenshot"))
            thumbnail = screenshot.get("url") if screenshot else None
            thumbnail_url = (
                thumbnail.strip() or None if isinstance(thumbnail, str) else None
            )
            price = _deref(records, video.get("price")) or {}
            raw_regular = price.get("regular")
            regular_price = raw_regular if isinstance(raw_regular, str) else None
            raw_discounted = price.get("discountedPrice")
            discounted = raw_discounted if isinstance(raw_discounted, str) else None
            price_cents, list_price_cents = resolve_manyvids_price(
                regular_price,
                discounted,
                on_sale=price.get("onSale") is True,
                free=price.get("free") is True,
            )
            videos.append(
                VideoData(
                    video_id=local_id,
                    title=html_module.unescape(title),
                    slug=slug,
                    url=f"{BASE_URL}/Video/{local_id}/{slug}",
                    video_type=video_type,
                    thumbnail_url=thumbnail_url,
                    price_regular=regular_price.strip() or None
                    if regular_price is not None
                    else None,
                    duration=_format_iso_duration(video.get("duration")),
                    price_cents=price_cents,
                    list_price_cents=list_price_cents,
                )
            )
    unique: list[VideoData] = []
    seen_ids: set[str] = set()
    for candidate in videos:
        if candidate.video_id not in seen_ids:
            seen_ids.add(candidate.video_id)
            unique.append(candidate)
    logger.debug(
        "Parsed ManyVids store: regular=%s/%s mobile=%s/%s unique=%s",
        sum(v.video_type == "regular" for v in unique),
        totals["regular"],
        sum(v.video_type == "mobile" for v in unique),
        totals["mobile"],
        len(unique),
    )
    return StorePage(
        unique, totals["regular"], sizes["regular"], totals["mobile"], sizes["mobile"]
    )


def _build_page_url(creator_id: str, creator_name: str, page: int) -> str:
    base = f"{BASE_URL}/Profile/{creator_id}/{creator_name}/Store/Videos"
    params = {"sort": "newest"}
    if page > 1:
        params["page"] = str(page)
    return f"{base}?{urlencode(params)}"


def _dump_debug_html(html: str, creator_id: str, page_num: int) -> Path | None:
    directory_value = os.environ.get(DEBUG_DUMP_ENV)
    if not directory_value:
        return None
    try:
        directory = Path(directory_value)
        directory.mkdir(parents=True, exist_ok=True)
        path = (
            directory
            / f"store-{creator_id}-p{page_num}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.html"
        )
        path.write_text(html, encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write ManyVids debug HTML dump: %s", exc)
        return None
    logger.warning("Saved unparseable ManyVids store page to %s", path)
    return path


def _extract_tags_from_html(html: str) -> list[str]:
    parser = _TagHTMLParser()
    parser.feed(html)
    return parser.tags


class ManyVidsSite(BaseSite):
    requires_auth = False

    def __init__(
        self,
        site_config: SiteConfig,
        scraping: ManyVidsScrapingConfig,
        creator: ManyVidsCreator,
    ) -> None:
        self.name = site_config.name
        self.display_name = site_config.display_name or site_config.name
        self.base_url = str(site_config.base_url)
        self.creator = creator
        self.scraping = scraping

    def context_options(self) -> dict[str, object]:
        return {
            "user_agent": self.scraping.user_agent,
            "viewport": {"width": 1920, "height": 1080},
            "locale": "en-US",
            "timezone_id": "America/New_York",
        }

    def init_scripts(self) -> list[str]:
        return [WEBDRIVER_MASK_SCRIPT]

    async def get_latest_items(
        self, page: Page, db: Database | None = None
    ) -> list[Item]:
        known_titles = await db.get_known_titles(self.display_name) if db else set()
        # Notifications still dedupe on titles until IDs are stored (Odoo #590);
        # numeric IDs parsed from stored URLs only drive the pagination early stop.
        known_item_ids = await db.get_known_item_ids(self.display_name) if db else set()
        known_ids = {
            video_id
            for item_id in known_item_ids
            if (video_id := manyvids_video_id_from_url(item_id)) is not None
        }
        items: list[Item] = []
        creator = self.creator
        result = await self.scrape_creator_with_retry(page, creator, known_ids)
        if result.error:
            logger.error(
                "Creator %s failed during scrape: %s",
                creator.creator_name,
                result.error,
            )
            return items

        creator_name = creator.display_name or creator.creator_name
        for video in result.videos:
            if video.title in known_titles:
                continue
            tags, description = await self._fetch_video_details(page, video.url)
            items.append(
                Item(
                    site_name=self.display_name,
                    item_id=video.url,
                    title=video.title,
                    url=video.url,
                    thumbnail_url=video.thumbnail_url,
                    tags=tags,
                    description=description,
                    duration=video.duration,
                    price=video.price_regular,
                    video_type=video.video_type,
                    creator=creator_name,
                )
            )

        return items

    async def _load_page(self, page: Page, url: str) -> str:
        try:
            # The video payload is inline, so wait_for_function below is the real
            # readiness check; networkidle also waits on ~150 ad/tracker requests
            # and routinely takes 10-30s, which trips the page timeout.
            await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=self.scraping.page_timeout,
            )
        except PlaywrightTimeout as exc:
            raise ScraperError(f"Timeout loading {url}") from exc

        title = await page.title()
        title_lower = title.lower()
        waf_markers = ("access denied", "forbidden", "blocked")
        if any(marker in title_lower for marker in waf_markers):
            raise ScraperBlockedError(f"WAF block detected: page title={title!r}")

        try:
            await page.wait_for_function(
                """() => {
                    const scripts = document.querySelectorAll('script:not([src])');
                    for (const script of scripts) {
                        if (script.textContent.includes('isVideosStore')) {
                            return true;
                        }
                    }
                    return false;
                }""",
                timeout=self.scraping.page_timeout,
            )
        except PlaywrightTimeout as exc:
            html = await page.content()
            if "NEXT_HTTP_ERROR_FALLBACK;404" in html:
                raise ScraperNotFoundError(
                    f"ManyVids returned a 404 page at {url}"
                ) from exc
            raise ScraperError(
                f"RSC video payload never appeared at {url}; "
                "possible WAF block or page structure change"
            ) from exc

        return await page.content()

    async def _scrape_creator(  # pylint: disable=too-many-locals,too-many-branches
        self,
        page: Page,
        creator: ManyVidsCreator,
        known_ids: set[str],
        *,
        full_scan: bool = False,
    ) -> CreatorResult:
        all_videos: list[VideoData] = []
        total_pages = 1
        expected_total = 0
        page_num = 1
        pages_loaded = 0
        while page_num <= min(total_pages, _MAX_STORE_PAGES):
            if page_num > 1:
                delay = random.uniform(
                    self.scraping.delay_between_pages_min,
                    self.scraping.delay_between_pages_max,
                )
                logger.debug(
                    "Creator %s: waiting %.1fs before page %s",
                    creator.creator_name,
                    delay,
                    page_num,
                )
                await asyncio.sleep(delay)
            url = _build_page_url(creator.creator_id, creator.creator_name, page_num)
            logger.debug(
                "Creator %s: fetching page %s: %s", creator.creator_name, page_num, url
            )
            try:
                html = await self._load_page(page, url)
            except ScraperNotFoundError as exc:
                if page_num == 1:
                    raise
                if full_scan:
                    raise ScraperError(
                        f"Page {page_num} of {total_pages} "
                        "returned 404 during full scan"
                    ) from exc
                logger.info(
                    "Creator %s: stopping pagination at page %s after 404",
                    creator.creator_name,
                    page_num,
                )
                break
            try:
                store_page = _parse_store_page(html)
            except StorePayloadError:
                _dump_debug_html(html, creator.creator_id, page_num)
                raise
            pages_loaded += 1
            if page_num == 1:
                total_pages = store_page.total_pages
                expected_total = store_page.expected_total
            if not store_page.videos:
                if full_scan and page_num <= total_pages and expected_total > 0:
                    raise ScraperError(
                        f"Page {page_num} of {total_pages} "
                        "had no videos during full scan"
                    )
                logger.info(
                    "Creator %s: no videos on page %s, stopping",
                    creator.creator_name,
                    page_num,
                )
                break
            all_videos.extend(store_page.videos)
            if (
                not full_scan
                and {video.video_id for video in store_page.videos} <= known_ids
            ):
                logger.info(
                    "Creator %s: all videos on page %s already known",
                    creator.creator_name,
                    page_num,
                )
                break
            page_num += 1

        unique_videos: list[VideoData] = []
        seen_ids: set[str] = set()
        for video in all_videos:
            if video.video_id not in seen_ids:
                seen_ids.add(video.video_id)
                unique_videos.append(video)
        if full_scan and len(unique_videos) != expected_total:
            raise ScraperError(
                f"Full scan incomplete: {len(unique_videos)} of {expected_total} videos"
            )
        logger.info(
            "Creator %s: scraped %s unique videos across %s pages%s",
            creator.creator_name,
            len(unique_videos),
            pages_loaded,
            " (full scan)" if full_scan else "",
        )
        return CreatorResult(
            creator_id=creator.creator_id,
            creator_name=creator.creator_name,
            videos=unique_videos,
            total_pages=pages_loaded,
        )

    async def scrape_creator_with_retry(
        self,
        page: Page,
        creator: ManyVidsCreator,
        known_ids: set[str],
        *,
        full_scan: bool = False,
    ) -> CreatorResult:
        last_error: Exception | None = None
        for attempt in range(self.scraping.max_retries + 1):
            try:
                return await self._scrape_creator(
                    page, creator, known_ids, full_scan=full_scan
                )
            except ScraperBlockedError as exc:
                last_error = exc
                logger.warning(
                    "Creator %s: WAF block on attempt %s: %s",
                    creator.creator_name,
                    attempt + 1,
                    exc,
                )
            except ScraperNotFoundError as exc:
                logger.warning(
                    "Creator %s: profile unavailable: %s", creator.creator_name, exc
                )
                return CreatorResult(
                    creator_id=creator.creator_id,
                    creator_name=creator.creator_name,
                    videos=[],
                    total_pages=0,
                    error=str(exc),
                )
            except ScraperError as exc:
                last_error = exc
                logger.error(
                    "Creator %s: scrape error on attempt %s: %s",
                    creator.creator_name,
                    attempt + 1,
                    exc,
                )
            if attempt < self.scraping.max_retries:
                wait = self.scraping.retry_backoff_base * (2**attempt)
                wait += random.uniform(0, 5)
                logger.info("Creator %s: retrying in %.0fs", creator.creator_name, wait)
                await asyncio.sleep(wait)
        return CreatorResult(
            creator_id=creator.creator_id,
            creator_name=creator.creator_name,
            videos=[],
            total_pages=0,
            error=(
                f"Failed after {self.scraping.max_retries + 1} attempts: {last_error}"
            ),
        )

    async def _fetch_video_details(
        self, page: Page, url: str
    ) -> tuple[list[str], str | None]:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=self.scraping.page_timeout,
        )
        locator = page.locator(TAGS_SELECTOR)
        seen: set[str] = set()
        values: list[str] = []
        # One atomic read: per-index inner_text() waits (30s) on each nth()
        # element and raises if the page re-renders and the count shrinks.
        for text in await locator.all_inner_texts():
            normalized = _normalize_tag(text)
            if normalized and normalized not in seen:
                seen.add(normalized)
                values.append(normalized)

        html = await page.content()
        if not values:
            values = _extract_tags_from_html(html)

        description: str | None = None
        m = _DESCRIPTION_PATTERN.search(html)
        if m:
            description = html_module.unescape(m.group("value")).strip() or None

        return values, description
