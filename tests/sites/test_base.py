from unittest.mock import AsyncMock

import pytest

from adult_sub_monitor.sites.base import BaseSite


def test_abstract_method_enforcement() -> None:
    class IncompleteSite(BaseSite):
        pass

    with pytest.raises(TypeError):
        IncompleteSite()


@pytest.mark.asyncio
async def test_dismiss_interstitial_default_returns_false() -> None:
    class MinimalSite(BaseSite):
        async def get_latest_items(self, page) -> list:
            return []

    site = MinimalSite()

    assert await site.dismiss_interstitial(AsyncMock()) is False


@pytest.mark.asyncio
async def test_auth_defaults_are_no_ops() -> None:
    class MinimalSite(BaseSite):
        async def get_latest_items(self, page) -> list:
            return []

    site = MinimalSite()

    assert site.requires_auth is True
    assert site.context_options() == {}
    assert await site.is_logged_in(AsyncMock()) is True
    assert await site.login(AsyncMock(), "user", "pass") is None
