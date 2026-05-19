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

    @abstractmethod
    async def login(self, page: Page, username: str, password: str) -> None: ...

    async def dismiss_interstitial(self, _page: Page) -> bool:
        return False

    @abstractmethod
    async def is_logged_in(self, page: Page) -> bool: ...

    @abstractmethod
    async def get_latest_items(self, page: Page) -> list[Item]: ...
