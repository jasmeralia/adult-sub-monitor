from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from adult_sub_monitor.browser import BrowserManager


def _site(is_logged_in: AsyncMock) -> AsyncMock:
    site = AsyncMock()
    site.name = "example"
    site.probe_url = "https://example.test/account"
    site.is_logged_in = is_logged_in
    site.login = AsyncMock()
    site.dismiss_interstitial = AsyncMock()
    return site


def _site_config() -> SimpleNamespace:
    return SimpleNamespace(
        credentials_env_user="EXAMPLE_USERNAME",
        credentials_env_pass="EXAMPLE_PASSWORD",
        headless=True,
    )


async def _started_manager(
    mocker, tmp_path, mock_context, mock_page
) -> tuple[BrowserManager, AsyncMock]:
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.storage_state = AsyncMock()

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)

    mock_playwright = MagicMock()
    mock_playwright.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_playwright_manager = MagicMock()
    mock_playwright_manager.start = AsyncMock(return_value=mock_playwright)
    mock_playwright_manager.__aenter__ = AsyncMock(return_value=mock_playwright)
    mock_playwright_manager.__aexit__ = AsyncMock(return_value=None)
    mocker.patch(
        "adult_sub_monitor.browser.async_playwright",
        return_value=mock_playwright_manager,
    )

    manager = BrowserManager(tmp_path, headless=True, user_agent=None)
    await manager.start()
    return manager, mock_browser


@pytest.mark.asyncio
async def test_ensure_authenticated_cookie_restore(mocker, tmp_path, mock_page) -> None:
    site = _site(AsyncMock(return_value=True))
    site_config = _site_config()
    storage_state_path = tmp_path / f"{site.name}.json"
    storage_state_path.write_text("{}", encoding="utf-8")
    mock_context = AsyncMock()
    manager, mock_browser = await _started_manager(
        mocker, tmp_path, mock_context, mock_page
    )

    context = await manager.ensure_authenticated(site, site_config)

    assert context is mock_context
    mock_browser.new_context.assert_awaited_once_with(
        storage_state=str(storage_state_path)
    )
    site.login.assert_not_called()
    site.dismiss_interstitial.assert_not_called()
    mock_context.storage_state.assert_awaited_once_with(path=str(storage_state_path))


@pytest.mark.asyncio
async def test_ensure_authenticated_fresh_login(
    mocker, monkeypatch, tmp_path, mock_page
) -> None:
    monkeypatch.setenv("EXAMPLE_USERNAME", "user")
    monkeypatch.setenv("EXAMPLE_PASSWORD", "pass")
    site = _site(AsyncMock(side_effect=[False, True]))
    site_config = _site_config()
    storage_state_path = tmp_path / f"{site.name}.json"
    mock_context = AsyncMock()
    manager, _mock_browser = await _started_manager(
        mocker, tmp_path, mock_context, mock_page
    )

    context = await manager.ensure_authenticated(site, site_config)

    assert context is mock_context
    site.login.assert_awaited_once_with(mock_page, "user", "pass")
    site.dismiss_interstitial.assert_awaited_once_with(mock_page)
    mock_context.storage_state.assert_awaited_once_with(path=str(storage_state_path))


@pytest.mark.asyncio
async def test_ensure_authenticated_reauth_failure(
    mocker, monkeypatch, tmp_path, mock_page
) -> None:
    monkeypatch.setenv("EXAMPLE_USERNAME", "user")
    monkeypatch.setenv("EXAMPLE_PASSWORD", "pass")
    site = _site(AsyncMock(side_effect=[False, False]))
    site_config = _site_config()
    mock_context = AsyncMock()
    manager, _mock_browser = await _started_manager(
        mocker, tmp_path, mock_context, mock_page
    )

    with pytest.raises(RuntimeError, match="Authentication failed for example"):
        await manager.ensure_authenticated(site, site_config)


@pytest.mark.asyncio
async def test_ensure_authenticated_saves_storage_state(
    mocker, tmp_path, mock_page
) -> None:
    site = _site(AsyncMock(return_value=True))
    site_config = _site_config()
    storage_state_path = tmp_path / f"{site.name}.json"
    mock_context = AsyncMock()
    manager, _mock_browser = await _started_manager(
        mocker, tmp_path, mock_context, mock_page
    )

    await manager.ensure_authenticated(site, site_config)

    mock_context.storage_state.assert_awaited_once_with(path=str(storage_state_path))
