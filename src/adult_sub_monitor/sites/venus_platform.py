from urllib.parse import urljoin

from playwright.async_api import Locator, Page

from adult_sub_monitor.models import Item, SiteConfig
from adult_sub_monitor.sites.base import BaseSite

LOGIN_EMAIL_SELECTOR = "#inputEmail"
LOGIN_PASSWORD_SELECTOR = "#inputPassword"
LOGIN_SUBMIT_SELECTOR = ".submit-button"
VIDEO_CARD_SELECTOR = ".content-grid-item-wrapper:has(.the-tile.video-content-tile)"
VIDEO_LINK_SELECTOR = "a:has(.the-tile.video-content-tile)"
THUMBNAIL_SELECTOR = "img.thumb"
TITLE_SELECTOR = ".metadata .title"
PERFORMERS_SELECTOR = ".model-tag-label"


class VenusPlatformSite(BaseSite):
    def __init__(self, site_config: SiteConfig):
        self.name = site_config.name
        self.base_url = str(site_config.base_url)
        self.login_url = str(site_config.login_url)
        self.probe_url = str(site_config.probe_url)
        self.listing_url = str(site_config.listing_url)

    async def login(self, page: Page, username: str, password: str) -> None:
        await page.goto(self.login_url, wait_until="domcontentloaded")
        await page.fill(LOGIN_EMAIL_SELECTOR, username)
        await page.fill(LOGIN_PASSWORD_SELECTOR, password)
        await page.click(LOGIN_SUBMIT_SELECTOR)
        await page.wait_for_function(
            "() => !window.location.href.includes('/login')", timeout=15000
        )

    async def is_logged_in(self, page: Page) -> bool:
        return "/login" not in page.url

    async def get_latest_items(self, page: Page) -> list[Item]:
        await page.goto(self.listing_url, wait_until="domcontentloaded")
        await page.wait_for_timeout(2000)

        items: list[Item] = []
        seen_hrefs: set[str] = set()
        cards = page.locator(VIDEO_CARD_SELECTOR)

        for index in range(await cards.count()):
            card = cards.nth(index)

            link_locator = card.locator(VIDEO_LINK_SELECTOR)
            if await link_locator.count() == 0:
                continue
            href = await link_locator.first.get_attribute("href")
            if not href or href in seen_hrefs:
                continue
            seen_hrefs.add(href)

            title_locator = card.locator(TITLE_SELECTOR)
            title: str | None = None
            if await title_locator.count() > 0:
                title = (await title_locator.first.inner_text()).strip() or None

            thumb_locator = card.locator(THUMBNAIL_SELECTOR)
            thumbnail_src: str | None = None
            if await thumb_locator.count() > 0:
                if not title:
                    title = await thumb_locator.first.get_attribute("alt")
                thumbnail_src = await thumb_locator.first.get_attribute("src")

            if not title:
                continue

            performers = await self._all_text(card, PERFORMERS_SELECTOR)
            item_url = urljoin(self.base_url, href)
            items.append(
                Item(
                    site_name=self.name,
                    item_id=item_url,
                    title=title,
                    url=item_url,
                    thumbnail_url=(
                        urljoin(self.base_url, thumbnail_src) if thumbnail_src else None
                    ),
                    performers=performers,
                    tags=[],
                )
            )

        return items

    async def _all_text(self, card: Locator, selector: str) -> list[str]:
        locator = card.locator(selector)
        values: list[str] = []
        for i in range(await locator.count()):
            text = (await locator.nth(i).inner_text()).strip()
            if text:
                values.append(text)
        return values
