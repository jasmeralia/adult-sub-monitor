from __future__ import annotations

import os
from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path
from typing import TYPE_CHECKING, cast

from camoufox.async_api import AsyncCamoufox
from playwright.async_api import Browser, BrowserContext

if TYPE_CHECKING:
    from adult_sub_monitor.models import SiteConfig
    from adult_sub_monitor.sites.base import BaseSite


class BrowserManager:
    def __init__(
        self, sessions_dir: Path, headless: bool, user_agent: str | None
    ) -> None:
        self.sessions_dir = sessions_dir
        self.headless = headless
        self.user_agent = user_agent
        self._camoufox: AsyncCamoufox | None = None
        self._exit_stack = AsyncExitStack()
        self._browser: Browser | None = None

        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        self._camoufox = cast(Callable[..., AsyncCamoufox], AsyncCamoufox)(
            headless=self.headless
        )
        self._browser = cast(
            Browser,
            await self._exit_stack.enter_async_context(self._camoufox),
        )

    async def stop(self) -> None:
        if self._camoufox is not None:
            await self._exit_stack.aclose()
            self._exit_stack = AsyncExitStack()
            self._camoufox = None
            self._browser = None

    async def ensure_authenticated(
        self,
        site: BaseSite,
        site_config: SiteConfig,
    ) -> BrowserContext:
        if self._browser is None:
            raise RuntimeError("BrowserManager.start() must be called before use")

        storage_state_path = self.sessions_dir / f"{site.name}.json"
        has_state = storage_state_path.exists()

        if has_state and self.user_agent is not None:
            context = await self._browser.new_context(
                storage_state=str(storage_state_path),
                user_agent=self.user_agent,
            )
        elif has_state:
            context = await self._browser.new_context(
                storage_state=str(storage_state_path),
            )
        elif self.user_agent is not None:
            context = await self._browser.new_context(user_agent=self.user_agent)
        else:
            context = await self._browser.new_context()
        page = await context.new_page()

        await page.goto(site.probe_url, wait_until="domcontentloaded")
        if not await site.is_logged_in(page):
            env_user = site_config.credentials_env_user
            env_pass = site_config.credentials_env_pass
            username = os.environ.get(env_user, env_user)
            password = os.environ.get(env_pass, env_pass)
            await site.login(page, username, password)
            await site.dismiss_interstitial(page)

            await page.goto(site.probe_url, wait_until="domcontentloaded")
            if not await site.is_logged_in(page):
                raise RuntimeError(f"Authentication failed for {site.name}")

        await context.storage_state(path=str(storage_state_path))
        return context
