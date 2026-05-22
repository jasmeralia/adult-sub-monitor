from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

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
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.headless,
            args=["--disable-blink-features=AutomationControlled"],
        )

    async def stop(self) -> None:
        if self._browser is not None:
            await self._browser.close()
            self._browser = None

        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def ensure_authenticated(
        self,
        site: BaseSite,
        site_config: SiteConfig,
    ) -> BrowserContext:
        if self._browser is None:
            raise RuntimeError("BrowserManager.start() must be called before use")

        context_options: dict[str, Any] = dict(site.context_options())
        if self.user_agent is not None:
            context_options = {**context_options, "user_agent": self.user_agent}

        if not site.requires_auth:
            return await self._browser.new_context(**context_options)

        storage_state_path = self.sessions_dir / f"{site.name}.json"
        has_state = storage_state_path.exists()
        if has_state:
            context_options = {
                **context_options,
                "storage_state": str(storage_state_path),
            }

        context = await self._browser.new_context(**context_options)
        page = await context.new_page()

        await page.goto(site.probe_url)
        if not await site.is_logged_in(page):
            env_user = site_config.credentials_env_user
            env_pass = site_config.credentials_env_pass
            if env_user is None or env_pass is None:
                raise RuntimeError(f"Credentials are not configured for {site.name}")
            username = os.environ.get(env_user, env_user)
            password = os.environ.get(env_pass, env_pass)
            await site.login(page, username, password)
            await site.dismiss_interstitial(page)

            await page.goto(site.probe_url)
            if not await site.is_logged_in(page):
                raise RuntimeError(f"Authentication failed for {site.name}")

        await context.storage_state(path=str(storage_state_path))
        return context
