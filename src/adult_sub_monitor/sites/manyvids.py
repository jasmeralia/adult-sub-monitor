from __future__ import annotations

import asyncio
import html as html_module
import json
import logging
import random
import re
from dataclasses import dataclass
from html.parser import HTMLParser
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
from adult_sub_monitor.sites.base import BaseSite

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
_VIDEO_PATTERN = re.compile(
    r'\{"id":"(\d+)"'
    r',"title":"(.*?)"'
    r'.*?"slug":"([^"]+)"'
    r'.*?"thumbnail":\{"url":"([^"]+)"\}'
    r'.*?"regular":"([^"]*)"'
    r'.*?"type":"([^"]+)"'
    r'.*?"duration":"([^"]*)"',
    re.DOTALL,
)
_TOTAL_PAGES_PATTERN = re.compile(r'"totalPages":(\d+)')
_CARD_PATTERN = re.compile(
    r"(?P<section>VerticalVideosSection|HorizontalVideosSection)[\s\S]{0,2000}?"
    r'aria-label="(?P<title>[^"]+?) by [^"]+ on ManyVids\."[\s\S]{0,2000}?'
    r'<img [^>]*?src="(?P<img>[^"]+)"',
    re.DOTALL,
)


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
    r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']{1,4096})["\']',
    re.IGNORECASE,
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
    return normalized


def _build_page_url(
    creator_id: str, creator_name: str, page: int, vertical: int
) -> str:
    base = f"{BASE_URL}/Profile/{creator_id}/{creator_name}/Store/Videos"
    params = {"sort": "newest", "vertical": str(vertical)}
    if page > 1:
        params["page"] = str(page)
    return f"{base}?{urlencode(params)}"


def _extract_rsc_video_data(html: str) -> tuple[list[VideoData], int]:
    for match in _RSC_PATTERN.finditer(html):
        raw = match.group(1)
        try:
            decoded = json.loads('"' + raw + '"')
        except (json.JSONDecodeError, ValueError):
            continue

        if "isVideosStore" not in decoded or "swrFallback" not in decoded:
            continue

        total_pages = 1
        tp_match = _TOTAL_PAGES_PATTERN.search(decoded)
        if tp_match:
            total_pages = int(tp_match.group(1))

        seen_ids: set[str] = set()
        videos: list[VideoData] = []
        for video_match in _VIDEO_PATTERN.finditer(decoded):
            video_id = video_match.group(1)
            if video_id in seen_ids:
                continue
            seen_ids.add(video_id)

            title = html_module.unescape(video_match.group(2))
            slug = video_match.group(3)
            thumbnail_raw = video_match.group(4).strip()
            price_raw = video_match.group(5).strip()
            duration_raw = video_match.group(7).strip()
            videos.append(
                VideoData(
                    video_id=video_id,
                    title=title,
                    slug=slug,
                    url=f"{BASE_URL}/Video/{video_id}/{slug}",
                    video_type=video_match.group(6).strip().lower() or "regular",
                    thumbnail_url=thumbnail_raw or None,
                    price_regular=price_raw or None,
                    duration=duration_raw or None,
                )
            )

        _enrich_videos_from_dom(html, videos)
        logger.debug(
            "RSC extraction: %s unique videos, %s total pages",
            len(videos),
            total_pages,
        )
        return videos, total_pages

    logger.warning("RSC video payload not found in page HTML")
    return [], 1


def _enrich_videos_from_dom(html: str, videos: list[VideoData]) -> None:
    title_meta: dict[str, tuple[str, str]] = {}
    for match in _CARD_PATTERN.finditer(html):
        section = match.group("section")
        title = html_module.unescape(match.group("title")).strip()
        img_url = html_module.unescape(match.group("img")).strip()
        if not title:
            continue
        video_type = "mobile" if section == "VerticalVideosSection" else "regular"
        title_meta[title] = (video_type, img_url)

    for video in videos:
        meta = title_meta.get(video.title.strip())
        if not meta:
            continue
        dom_type, dom_thumb = meta
        video.video_type = dom_type
        if dom_thumb:
            video.thumbnail_url = dom_thumb


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
        items: list[Item] = []
        creator = self.creator
        result = await self.scrape_creator_with_retry(page, creator, known_titles)
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
            await page.goto(
                url,
                wait_until="networkidle",
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
            raise ScraperError(
                f"RSC video payload never appeared at {url}; "
                "possible WAF block or page structure change"
            ) from exc

        return await page.content()

    # Ported pagination logic keeps the source scraper's local state explicit.
    async def _scrape_creator(  # pylint: disable=too-many-locals
        self,
        page: Page,
        creator: ManyVidsCreator,
        known_titles: set[str],
    ) -> CreatorResult:
        all_videos: list[VideoData] = []
        total_pages = 0
        sections = [("regular", 1), ("mobile", 2)]

        for section_name, section_vertical in sections:
            section_total_pages = 1
            for page_num in range(1, 201):
                if page_num > 1 or section_vertical > 1:
                    delay = random.uniform(
                        self.scraping.delay_between_pages_min,
                        self.scraping.delay_between_pages_max,
                    )
                    logger.debug(
                        "Creator %s: waiting %.1fs before %s page %s",
                        creator.creator_name,
                        delay,
                        section_name,
                        page_num,
                    )
                    await asyncio.sleep(delay)

                url = _build_page_url(
                    creator.creator_id,
                    creator.creator_name,
                    page_num,
                    vertical=section_vertical,
                )
                logger.debug(
                    "Creator %s: fetching %s page %s: %s",
                    creator.creator_name,
                    section_name,
                    page_num,
                    url,
                )
                html = await self._load_page(page, url)
                page_videos, section_total_pages = _extract_rsc_video_data(html)
                if not page_videos:
                    logger.info(
                        "Creator %s: no %s videos on page %s, stopping section",
                        creator.creator_name,
                        section_name,
                        page_num,
                    )
                    break

                all_videos.extend(page_videos)
                page_titles = {video.title for video in page_videos}
                if page_titles.issubset(known_titles):
                    logger.info(
                        "Creator %s: all %s videos on page %s already known",
                        creator.creator_name,
                        section_name,
                        page_num,
                    )
                    break

                if page_num >= section_total_pages:
                    break

            total_pages += section_total_pages

        seen: set[str] = set()
        unique_videos: list[VideoData] = []
        for video in all_videos:
            if video.video_id in seen:
                continue
            seen.add(video.video_id)
            unique_videos.append(video)

        logger.info(
            "Creator %s: scraped %s unique videos across %s pages",
            creator.creator_name,
            len(unique_videos),
            total_pages,
        )
        return CreatorResult(
            creator_id=creator.creator_id,
            creator_name=creator.creator_name,
            videos=unique_videos,
            total_pages=total_pages,
        )

    async def scrape_creator_with_retry(
        self,
        page: Page,
        creator: ManyVidsCreator,
        known_titles: set[str],
    ) -> CreatorResult:
        last_error: Exception | None = None
        for attempt in range(self.scraping.max_retries + 1):
            try:
                return await self._scrape_creator(page, creator, known_titles)
            except ScraperBlockedError as exc:
                last_error = exc
                logger.warning(
                    "Creator %s: WAF block on attempt %s: %s",
                    creator.creator_name,
                    attempt + 1,
                    exc,
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
        for index in range(await locator.count()):
            normalized = _normalize_tag(await locator.nth(index).inner_text())
            if normalized and normalized not in seen:
                seen.add(normalized)
                values.append(normalized)

        html = await page.content()
        if not values:
            values = _extract_tags_from_html(html)

        description: str | None = None
        m = _DESCRIPTION_PATTERN.search(html)
        if m:
            description = html_module.unescape(m.group(1)).strip() or None

        return values, description
