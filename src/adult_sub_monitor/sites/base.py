from abc import ABC, abstractmethod

from playwright.async_api import Page

from adult_sub_monitor.models import Item


class BaseSite(ABC):
    name: str
    base_url: str
    login_url: str
    probe_url: str
    listing_url: str
    has_interstitial: bool = False
    requires_auth: bool = True

    def context_options(self) -> dict[str, object]:
        return {}

    async def login(self, _page: Page, _username: str, _password: str) -> None:
        return None

    async def dismiss_interstitial(self, _page: Page) -> bool:
        return False

    async def is_logged_in(self, _page: Page) -> bool:
        return True

    @abstractmethod
    async def get_latest_items(self, page: Page) -> list[Item]: ...
