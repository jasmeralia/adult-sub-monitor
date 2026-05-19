from urllib.parse import urljoin

from playwright.async_api import Locator, Page

from adult_sub_monitor.models import Item, SiteConfig
from adult_sub_monitor.sites.base import BaseSite

LOGIN_EMAIL_SELECTOR = "#user-email"
LOGIN_PASSWORD_SELECTOR = "#user-password"
LOGIN_SUBMIT_SELECTOR = ".loginform-submit-button"
VIDEO_CARD_SELECTOR = ".content_item.ct_video"
VIDEO_URL_SELECTOR = "a.icon[href*='/film/']"
VIDEO_TITLE_SELECTOR = "a.title"
THUMBNAIL_SELECTOR = "span.thumb img"
PERFORMERS_SELECTOR = "span.models a"
TAGS_SELECTOR = "span.genres a"


class WowgirlsPlatformSite(BaseSite):
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
        cards = page.locator(VIDEO_CARD_SELECTOR)
        count = await cards.count()

        for index in range(count):
            card = cards.nth(index)

            url_locator = card.locator(VIDEO_URL_SELECTOR)
            if await url_locator.count() == 0:
                continue
            href = await url_locator.first.get_attribute("href")
            if not href:
                continue

            title_locator = card.locator(VIDEO_TITLE_SELECTOR)
            title: str | None = None
            if await title_locator.count() > 0:
                title = (await title_locator.first.inner_text()).strip() or None

            img_locator = card.locator(THUMBNAIL_SELECTOR)
            thumbnail_src: str | None = None
            if await img_locator.count() > 0:
                if not title:
                    title = await img_locator.first.get_attribute("alt")
                thumbnail_src = await img_locator.first.get_attribute("src")

            if not title:
                continue

            absolute_url = urljoin(self.base_url, href)
            absolute_thumbnail = (
                urljoin(self.base_url, thumbnail_src) if thumbnail_src else None
            )

            items.append(
                Item(
                    site_name=self.name,
                    item_id=absolute_url,
                    title=title,
                    url=absolute_url,
                    thumbnail_url=absolute_thumbnail,
                    performers=await self._all_text(card, PERFORMERS_SELECTOR),
                    tags=await self._all_text(card, TAGS_SELECTOR),
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
