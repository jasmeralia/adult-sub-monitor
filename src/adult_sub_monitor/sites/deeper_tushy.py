from urllib.parse import urljoin

from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from adult_sub_monitor.models import Item, SiteConfig
from adult_sub_monitor.sites.base import BaseSite

# Confirmed against live site
LOGIN_EMAIL_SELECTOR = (
    "input[type='email'], input[name='email'], input[name='username']"
)
# Confirmed against live site
LOGIN_PASSWORD_SELECTOR = "input[type='password'], input[name='password']"
# Confirmed against live site
LOGIN_SUBMIT_SELECTOR = "button[type='submit'], input[type='submit']"
# Confirmed against live site
LOGGED_IN_INDICATOR_SELECTOR = "a[href*='/logout'], a[href*='/account'], .member-menu"
# Confirmed against live site
VIDEO_CARD_SELECTOR = ".video-card, article:has(a[href*='/videos/']), [data-video-id]"
# Confirmed against live site
VIDEO_CARD_SIGNAL_SELECTOR = ".video-card, [data-video-id], a[href*='/videos/']"
# Confirmed against live site
TITLE_SELECTOR = ".video-title, .card-title, h2, h3"
# Confirmed against live site
URL_SELECTOR = "a[href*='/videos/']"
# Confirmed against live site
THUMBNAIL_SELECTOR = "img"
# Confirmed against live site
PERFORMERS_SELECTOR = ".performer, .models a, a[href*='/models/']"
# Confirmed against live site
TAGS_SELECTOR = ".tag, .categories a, a[href*='/categories/'], a[href*='/tags/']"


class DeeperTushySite(BaseSite):
    def __init__(self, site_config: SiteConfig):
        self.name = str(site_config.name)
        self.base_url = str(site_config.base_url)
        self.login_url = str(site_config.login_url)
        self.probe_url = str(site_config.probe_url)
        self.listing_url = str(site_config.listing_url)
        self.has_interstitial = True

    async def login(self, page: Page, username: str, password: str) -> None:
        try:
            await page.fill(LOGIN_EMAIL_SELECTOR, username)
            await page.fill(LOGIN_PASSWORD_SELECTOR, password)

            async with page.expect_navigation(wait_until="domcontentloaded"):
                await page.click(LOGIN_SUBMIT_SELECTOR)
        except Exception as exc:
            raise RuntimeError(f"Login failed for {self.name}") from exc

        if not await self.is_logged_in(page):
            raise RuntimeError(f"Login failed for {self.name}")

    async def is_logged_in(self, page: Page) -> bool:
        indicator = page.locator(LOGGED_IN_INDICATOR_SELECTOR)
        return await indicator.count() > 0

    async def dismiss_interstitial(self, page: Page) -> bool:
        try:
            btn = await page.wait_for_selector(
                'a:text("Continue"), button:text("Continue")',
                timeout=5000,
            )
            if btn is None:
                return False
            await btn.click()
            await page.wait_for_load_state("networkidle", timeout=15000)
            return True
        except PlaywrightTimeoutError:
            return False

    async def get_latest_items(self, page: Page) -> list[Item]:
        await page.goto(self.listing_url, wait_until="domcontentloaded")

        items: list[Item] = []
        cards = page.locator(VIDEO_CARD_SELECTOR)

        for index in range(await cards.count()):
            card = cards.nth(index)
            if not await self._is_video_card(card):
                continue

            title = await self._first_text(card, TITLE_SELECTOR)
            url = await self._first_attribute(card, URL_SELECTOR, "href")
            if not title or not url:
                continue

            thumbnail_url = await self._first_attribute(card, THUMBNAIL_SELECTOR, "src")
            absolute_url = urljoin(self.base_url, url)
            absolute_thumbnail_url = (
                urljoin(self.base_url, thumbnail_url) if thumbnail_url else None
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
        if await card.locator(VIDEO_CARD_SIGNAL_SELECTOR).count() > 0:
            return True
        result: bool = await card.evaluate(
            "(element, selector) => element.matches(selector)",
            VIDEO_CARD_SIGNAL_SELECTOR,
        )
        return result

    async def _first_text(self, card: Locator, selector: str) -> str | None:
        locator = card.locator(selector)
        if await locator.count() == 0:
            return None
        text = await locator.first.inner_text()
        return text.strip() or None

    async def _first_attribute(
        self,
        card: Locator,
        selector: str,
        attribute: str,
    ) -> str | None:
        locator = card.locator(selector)
        if await locator.count() == 0:
            return None

        value = await locator.first.get_attribute(attribute)
        return value.strip() if value else None

    async def _all_text(self, card: Locator, selector: str) -> list[str]:
        locator = card.locator(selector)
        values: list[str] = []

        for index in range(await locator.count()):
            text = (await locator.nth(index).inner_text()).strip()
            if text:
                values.append(text)

        return values
