# AGENTS.md

## 1. Project overview

adult-sub-monitor is a Dockerized Python service that logs into authenticated subscription video sites, persists sessions, detects new videos, deduplicates them in SQLite, and sends Discord webhook notifications; see [docs/DESIGN.md](docs/DESIGN.md) for the full design.

## 2. Repo conventions

Use dashes in the repository name: `adult-sub-monitor`. Use underscores in the Python package name: `adult_sub_monitor`. Keep the `VERSION` file at the repository root.

## 3. Code style

Use spaces only, no tabs. Do not leave trailing whitespace. Linting runs through ruff, mypy, and pylint. mypy strict mode is enabled.

## 4. Required workflow

> After any code change, always run:
> ```
> make lint-fix && make lint && make test
> ```
> Run `lint-fix` first — it auto-corrects import order, formatting, and other fixable issues before `lint` checks them. All three targets must pass before committing.

## 5. Architecture pointers

`config.py` defines the Pydantic application and site configuration models, including per-site credential environment variable names and runtime defaults.

`db.py` owns the SQLite layer, applies idempotent migrations, enables WAL mode, tracks seen items, and records failed notifications for retry.

`discord.py` builds and sends Discord webhook embeds for newly detected videos, handles webhook failures, and respects rate limiting.

`browser.py` manages Playwright Chromium state, per-site browser contexts, persisted storage state, and the authentication probe/login flow.

`sites/base.py` defines the `BaseSite` abstract interface that every site implementation must satisfy, including login, authentication checks, interstitial handling, and video scraping.

`sites/venus_platform.py` implements the shared Venus platform scraper for the four `venus.*` sites, using `/videos` listings plus per-card video checks to exclude photo sets.

`sites/deeper_tushy.py` implements the shared Deeper/Tushy scraper, including the intermittent post-login interstitial dismissal flow.

`main.py` orchestrates config loading, logging, scheduling, per-site checks, deduplication, notifications, retry handling, and graceful shutdown.

## 6. Adding a new site

1. Create a subclass of the appropriate base class.
2. Register it in `_build_site()`.
3. Add tests mocking browser interactions.
4. Update the README sites table.

## 7. Testing

No real Playwright browser should be launched in unit tests. All browser interaction is mocked. pytest uses `asyncio_mode=auto` in `pyproject.toml`.

## 8. Secrets

Never commit `.env`. Never log credentials. Use the environment variable pattern from `SiteConfig` for per-site usernames and passwords.

## 9. Release process

Bump the `VERSION` file, merge to `master`, and let CI handle tagging and Docker image push.

## 10. Branch/PR conventions

Create feature branches off `master`. Use squash merge. There are 0 required reviewers, but CI must pass and conversation resolution is required before merge.

After **creating or merging a PR**, monitor the GitHub Actions run to confirm lint and test pass. Do not report success until CI is green. Use `gh pr checks <number>` to poll status, or `gh run view <run-id> --log-failed` to diagnose failures.
