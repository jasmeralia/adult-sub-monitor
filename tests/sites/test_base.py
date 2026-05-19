from unittest.mock import AsyncMock

import pytest

from adult_sub_monitor.sites.base import BaseSite


def test_abstract_method_enforcement() -> None:
    class IncompleteSite(BaseSite):
        async def login(self, page, username: str, password: str) -> None:
            pass

        async def is_logged_in(self, page) -> bool:
            return True

    with pytest.raises(TypeError):
        IncompleteSite()


@pytest.mark.asyncio
async def test_dismiss_interstitial_default_returns_false() -> None:
    class MinimalSite(BaseSite):
        async def login(self, page, username: str, password: str) -> None:
            pass

        async def is_logged_in(self, page) -> bool:
            return True

        async def get_latest_items(self, page) -> list:
            return []

    site = MinimalSite()

    assert await site.dismiss_interstitial(AsyncMock()) is False
