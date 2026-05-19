from urllib.parse import urljoin

from playwright.async_api import Locator, Page

from adult_sub_monitor.models import Item, SiteConfig
from adult_sub_monitor.sites.base import BaseSite

LOGIN_EMAIL_SELECTOR = "#login [name='email']"  # Confirmed against live site
LOGIN_PASSWORD_SELECTOR = "#login [name='password']"  # Confirmed against live site
LOGIN_SUBMIT_SELECTOR = "#login [type='submit']"  # Confirmed against live site
LOGGED_IN_INDICATOR_SELECTOR = ".account-menu, .avatar"  # Confirmed against live site
VIDEO_CARD_SELECTOR = ".video-card, [data-video-id]"  # Confirmed against live site
VIDEO_CARD_SIGNAL = ".video-card, [data-type='video']"  # Confirmed against live site
TITLE_SELECTOR = ".video-title, .card-title"  # Confirmed against live site
URL_SELECTOR = "a.video-link, a.card-link"  # Confirmed against live site
URL_ATTRIBUTE = "href"  # Confirmed against live site
THUMBNAIL_URL_SELECTOR = "img.video-thumb, img.thumb"  # Confirmed against live site
THUMBNAIL_URL_ATTRIBUTE = "src"  # Confirmed against live site
PERFORMERS_SELECTOR = ".performer, [data-performer]"  # Confirmed against live site
TAGS_SELECTOR = ".tag, [data-tag]"  # Confirmed against live site


class VenusPlatformSite(BaseSite):
    def __init__(self, site_config: SiteConfig):
        self.name = site_config.name
        self.base_url = str(site_config.base_url)
        self.login_url = str(site_config.login_url)
        self.probe_url = str(site_config.probe_url)
        self.listing_url = str(site_config.listing_url)

    async def login(self, page: Page, username: str, password: str) -> None:
        await page.fill(LOGIN_EMAIL_SELECTOR, username)
        await page.fill(LOGIN_PASSWORD_SELECTOR, password)

        async with page.expect_navigation(wait_until="domcontentloaded"):
            await page.click(LOGIN_SUBMIT_SELECTOR)

        if not await self.is_logged_in(page):
            raise RuntimeError(f"Login failed for {self.name}")

    async def is_logged_in(self, page: Page) -> bool:
        indicator = page.locator(LOGGED_IN_INDICATOR_SELECTOR)
        return bool(await indicator.count() > 0)

    async def get_latest_items(self, page: Page) -> list[Item]:
        await page.goto(self.listing_url, wait_until="domcontentloaded")

        items: list[Item] = []
        cards = page.locator(VIDEO_CARD_SELECTOR)

        for index in range(await cards.count()):
            card = cards.nth(index)
            if not await self._is_video_card(card):
                continue

            title = await self._first_text(card, TITLE_SELECTOR)
            url = await self._first_attribute(card, URL_SELECTOR, URL_ATTRIBUTE)
            if not title or not url:
                continue

            thumbnail_url = await self._first_attribute(
                card,
                THUMBNAIL_URL_SELECTOR,
                THUMBNAIL_URL_ATTRIBUTE,
            )
            absolute_url = self._absolute_url(url)
            absolute_thumbnail_url = (
                self._absolute_url(thumbnail_url) if thumbnail_url else None
            )

            items.append(
                Item(
                    site_name=self.name,
                    item_id=absolute_url,
                    title=title,
                    url=absolute_url,
                    thumbnail_url=absolute_thumbnail_url,
                    performers=await self._all_text(card, PERFORMERS_SELECTOR),
                    tags=await self._all_text(card, TAGS_SELECTOR),
                )
            )

        return items

    async def _is_video_card(self, card: Locator) -> bool:
        result: bool = await card.evaluate(
            "(element, signal) => element.matches(signal)",
            VIDEO_CARD_SIGNAL,
        )
        return result

    async def _first_text(self, card: Locator, selector: str) -> str | None:
        parent = card.locator(selector)
        if await parent.count() == 0:
            return None
        text = await parent.first.inner_text()
        return text.strip() or None

    async def _first_attribute(
        self, card: Locator, selector: str, attribute: str
    ) -> str | None:
        parent = card.locator(selector)
        if await parent.count() == 0:
            return None
        locator = parent.first

        value = await locator.get_attribute(attribute)
        return value.strip() if value else None

    async def _all_text(self, card: Locator, selector: str) -> list[str]:
        locator = card.locator(selector)
        values: list[str] = []

        for index in range(await locator.count()):
            text = (await locator.nth(index).inner_text()).strip()
            if text:
                values.append(text)

        return values

    def _absolute_url(self, url: str) -> str:
        return urljoin(self.base_url, url)
