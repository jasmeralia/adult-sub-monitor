# adult-sub-monitor — Design Document

## Overview

A Dockerized Python service that periodically monitors subscription and public
creator video sites, detects newly published videos, and notifies a Discord
webhook. Photo sets and other non-video content are ignored. Sessions are
persisted to disk for sites that require authentication and re-used until they
expire, at which point the service re-authenticates via Playwright. Public sites
such as ManyVids run anonymously.

**Repo name:** `adult-sub-monitor` (dashes — see naming convention notes).
**Status:** Current implementation scope (this document).

---

## Goals & Non-Goals

### Goals

- Authenticate to subscription sites via form login where required (Playwright).
- Scrape configured public creator stores anonymously where authentication is
  not required.
- Persist session cookies between runs; re-authenticate only when sessions expire.
- Detect new videos (only — never photo sets) on each site every N hours.
- Notify a Discord webhook with title, URL, thumbnail, performers, and tags.
- Deduplicate via SQLite so each video notifies exactly once.
- Run as a single Docker container with mounted config + data volumes.
- Comprehensive unit tests via pytest with mocked Playwright/HTTP layers.
- CI: lint on all branches, auto-tag and publish Docker image to GHCR on merge
  to master.

### Non-Goals

- Automatic video downloading.
- Plex/Jellyfin library integration.
- Photo set notifications (explicitly excluded).
- Sites beyond the listed families below (extensible, but not in scope).
- Multi-recipient or multi-channel notifications (Discord webhook only).
- Web UI or admin dashboard (CLI/config-file only).

---

## Sites in Scope

| Site / Source              | Family            | Notes                                      |
|----------------------------|-------------------|--------------------------------------------|
| `venus.angels.love`        | Venus platform    | Authenticated; mixes videos and photo sets |
| `venus.sensual.love`       | Venus platform    | Authenticated; mixes videos and photo sets |
| `venus.ultrafilms.com`     | Venus platform    | Authenticated; mixes videos and photo sets |
| `venus.wowgirls.com`       | WowGirls platform | Authenticated; updates listing             |
| ManyVids creator stores    | ManyVids          | Anonymous public creator-store scraping    |

The shared Venus sites use `VenusPlatformSite`; WowGirls has a dedicated
listing scraper; ManyVids uses a single site config with a nested creator list.
Vixen Media Group support was removed because Cloudflare anti-bot protections
made the scraper unreliable and it is no longer in scope.

---

## Architecture

### Directory Structure

```
adult-sub-monitor/
├── .github/
│   ├── workflows/
│   │   ├── lint.yml              # Runs on all branches
│   │   ├── test.yml              # Runs on all branches
│   │   └── release.yml           # Master only: tag + build + push GHCR
│   ├── dependabot.yml
│   └── CODEOWNERS
├── config/
│   └── config.example.yaml
├── src/
│   └── adult_sub_monitor/
│       ├── __init__.py
│       ├── __main__.py           # Entry point
│       ├── main.py               # Scheduler / orchestrator
│       ├── browser.py            # Playwright session manager
│       ├── db.py                 # SQLite layer
│       ├── discord.py            # Webhook notifier
│       ├── config.py             # Pydantic config models
│       ├── models.py             # Domain types (Item, Site, etc.)
│       └── sites/
│           ├── __init__.py
│           ├── base.py           # BaseSite ABC
│           ├── manyvids.py       # ManyVids public creator-store scraper
│           ├── venus_platform.py
│           └── wowgirls_platform.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_db.py
│   ├── test_discord.py
│   ├── test_config.py
│   ├── test_browser.py
│   ├── test_main.py
│   └── sites/
│       ├── __init__.py
│       ├── test_base.py
│       ├── test_manyvids.py
│       ├── test_venus_platform.py
│       └── test_wowgirls_platform.py
├── .dockerignore
├── .gitignore
├── .python-version              # 3.13
├── AGENTS.md                    # Detailed agent guidance
├── CLAUDE.md                    # Stub pointing at AGENTS.md
├── Dockerfile
├── docker-compose.yml
├── LICENSE
├── Makefile
├── pyproject.toml
├── README.md
└── VERSION                       # Read by release workflow
```

### Component Responsibilities

**`main.py` — Orchestrator**
- Loads config, sets up logging, creates DB connection.
- Schedules per-site checks via `apscheduler.AsyncIOScheduler` with jitter.
- Optional `RUN_ONCE=1` mode for one-shot execution (useful for testing).
- Optional `DRY_RUN=1` mode skips DB writes and notifications.
- Handles graceful shutdown on SIGTERM (drains in-flight checks).

**`browser.py` — Playwright session manager**
- Owns one shared Chromium instance; creates per-site `BrowserContext`.
- Loads/saves storage state (`cookies` + `localStorage`) to
  `data/sessions/<site>.json`.
- Skips the authentication probe/login path for sites with
  `requires_auth = False`.
- Forwards `site.context_options()` and `site.init_scripts()` into each
  browser context.
- Provides `ensure_authenticated(site, page)` helper that:
  1. Navigates to `site.probe_url`.
  2. If redirected to login, calls `site.login(page)`.
  3. After login, calls `site.dismiss_interstitial(page)` (no-op if absent).
  4. Re-probes to confirm authenticated state.
  5. Persists fresh storage state.

**`db.py` — SQLite layer**
- Schema in `_apply_migrations()`; idempotent on startup.
- Tables: `seen_items`, `failed_notifications`, `schema_version`.
- `seen_items` includes optional metadata columns for `duration`, `price`,
  `video_type`, and `creator` so ManyVids-specific details survive retries.
- WAL mode enabled.
- All access via methods on a `Database` class (easy to mock in tests).

**`discord.py` — Webhook notifier**
- Single `send_video_notification(webhook_url, item)` async function.
- Builds Discord embed with title, URL, thumbnail, performers, tags, and
  optional creator/type/duration/price fields.
- Returns success/failure; caller logs failures to `failed_notifications` table.
- Respects Discord rate limits (1 req/sec per webhook, retry on 429).

**`sites/base.py` — BaseSite ABC**

```python
class BaseSite(ABC):
    name: str
    base_url: str
    login_url: str | None
    probe_url: str | None
    listing_url: str | None
    has_interstitial: bool = False
    requires_auth: bool = True

    def context_options(self) -> dict[str, object]:
        return {}

    def init_scripts(self) -> list[str]:
        return []

    async def login(self, page: Page, username: str, password: str) -> None: ...

    async def dismiss_interstitial(self, page: Page) -> bool:
        """Override in subclasses that need it. Default: no-op."""
        return False

    async def is_logged_in(self, page: Page) -> bool: ...

    @abstractmethod
    async def get_latest_items(
        self, page: Page, db: Database | None = None
    ) -> list[Item]: ...
```

Items returned from `get_latest_items` MUST already be filtered to videos only
(the base class does not re-filter; site classes are responsible for excluding
photo sets at scrape time).

**`sites/venus_platform.py` — VenusPlatformSite**

Single class parameterised by `base_url`. The Venus platform exposes a
dedicated `/videos` listing URL distinct from `/photos`, which is the primary
mechanism for video-only filtering. As a defence-in-depth measure, the scraper
also checks each card's content-type indicator (DOM attribute or class name)
before yielding it.

Venus also opens each video detail page to collect tags for Discord
notifications.

**`sites/wowgirls_platform.py` — WowgirlsPlatformSite**

Dedicated scraper for the WowGirls updates listing. It shares the authenticated
browser/session behavior with the Venus platform sites but has its own listing
selectors.

**`sites/manyvids.py` — ManyVidsSite**

Anonymous public creator-store scraper. A single `manyvids` site config contains
one or more creators. The scraper handles regular and mobile store pagination,
early-stop behavior against known titles, retry/backoff for blocked or failed
creator scrapes, per-video detail-page tag extraction, and metadata enrichment
for creator, type, duration, and price. It overrides `requires_auth`,
`context_options()`, and `init_scripts()` to run without stored credentials and
with ManyVids-specific browser context settings.

## Data Model

### SQLite Schema

```sql
CREATE TABLE schema_version (
    version INTEGER PRIMARY KEY
);

CREATE TABLE seen_items (
    site_name TEXT NOT NULL,
    item_id TEXT NOT NULL,           -- Site-native ID (preferred) or URL
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    thumbnail_url TEXT,
    performers TEXT,                 -- JSON array
    tags TEXT,                       -- JSON array
    duration TEXT,
    price TEXT,
    video_type TEXT,
    creator TEXT,
    first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    notified_at TIMESTAMP,
    PRIMARY KEY (site_name, item_id)
);

CREATE INDEX idx_seen_items_first_seen ON seen_items (first_seen_at DESC);

CREATE TABLE failed_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_name TEXT NOT NULL,
    item_id TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    last_error TEXT,
    last_attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (site_name, item_id) REFERENCES seen_items(site_name, item_id)
);

CREATE INDEX idx_failed_last_attempted ON failed_notifications (last_attempted_at);
```

### Domain Types (Pydantic)

```python
class Item(BaseModel):
    site_name: str
    item_id: str
    title: str
    url: HttpUrl
    thumbnail_url: HttpUrl | None = None
    performers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    duration: str | None = None
    price: str | None = None
    video_type: str | None = None
    creator: str | None = None

class ManyVidsCreator(BaseModel):
    creator_id: str
    creator_name: str
    display_name: str | None = None

class ManyVidsScrapingConfig(BaseModel):
    delay_between_creators_min: float = 30
    delay_between_creators_max: float = 60
    delay_between_pages_min: float = 3
    delay_between_pages_max: float = 8
    page_timeout: int = 30000
    max_retries: int = 3
    retry_backoff_base: float = 10
    user_agent: str = "Mozilla/5.0 (...)"

class SiteConfig(BaseModel):
    name: str
    display_name: str | None = None
    type: Literal["venus_platform", "wowgirls_platform", "manyvids"]
    base_url: HttpUrl
    login_url: HttpUrl | None = None
    probe_url: HttpUrl | None = None
    listing_url: HttpUrl | None = None
    interval_hours: float = 6.0
    credentials_env_user: str | None = None
    credentials_env_pass: str | None = None
    enabled: bool = True
    creators: list[ManyVidsCreator] = Field(default_factory=list)

class AppConfig(BaseModel):
    sites: list[SiteConfig]
    discord_webhook_env: str = "DISCORD_WEBHOOK_URL"
    db_path: Path = Path("/data/monitor.db")
    sessions_dir: Path = Path("/data/sessions")
    log_level: str = "INFO"
    headless: bool = True
    user_agent: str | None = None
    manyvids: ManyVidsScrapingConfig | None = None
```

---

## Configuration

### `config/config.example.yaml`

```yaml
db_path: /data/monitor.db
discord_webhook_env: https://discord.com/api/webhooks/REPLACE_ME
headless: true
log_level: INFO
sessions_dir: /data/sessions

sites:
  - base_url: https://venus.sensual.love
    credentials_env_pass: CHANGE_ME_PASS
    credentials_env_user: CHANGE_ME_USER
    display_name: sensual.love
    enabled: true
    interval_hours: 6
    listing_url: https://venus.sensual.love/members/content
    login_url: https://venus.sensual.love/login
    name: sensual_love
    probe_url: https://venus.sensual.love/members/content
    type: venus_platform

  - name: wowgirls
    display_name: wowgirls.com
    type: wowgirls_platform
    base_url: https://venus.wowgirls.com
    login_url: https://venus.wowgirls.com/login
    probe_url: https://venus.wowgirls.com/updates/
    listing_url: https://venus.wowgirls.com/updates/
    interval_hours: 6
    credentials_env_user: CHANGE_ME_USER
    credentials_env_pass: CHANGE_ME_PASS

  - name: manyvids
    display_name: ManyVids
    type: manyvids
    base_url: https://www.manyvids.com
    interval_hours: 24
    enabled: true
    creators:
      - creator_id: "1002990973"
        creator_name: karneli_bandi
        display_name: Karneli Bandi

manyvids:
  delay_between_creators_min: 30
  delay_between_creators_max: 60
  delay_between_pages_min: 3
  delay_between_pages_max: 8
  page_timeout: 30000
  max_retries: 3
  retry_backoff_base: 10
  user_agent: Mozilla/5.0 (...)
```

### Environment Variables

| Variable                  | Default                  | Purpose                                  |
|---------------------------|--------------------------|------------------------------------------|
| `CONFIG_PATH`             | `/config/config.yaml`    | Path to config file                      |
| `DISCORD_WEBHOOK_URL`     | (required)               | Discord webhook                          |
| `<SITE>_USER` / `_PASS`   | required per authenticated site | Credentials per `credentials_env_*` |
| `RUN_ONCE`                | unset                    | Set `1` to run one cycle and exit        |
| `DRY_RUN`                 | unset                    | Skip DB writes + notifications           |
| `LOG_LEVEL`               | `INFO`                   | Override log level                       |

---

## Workflow

### Per-site check cycle

```
1. Acquire per-site asyncio lock (prevents overlapping runs)
2. Open browser context for site
3. ensure_authenticated(site, page):
     a. Create context with site.context_options() and init_scripts()
     b. If requires_auth is false, return anonymous context
     c. Navigate to probe_url
     d. If logged in -> goto step 4
     e. Else: site.login(page)
     f. site.dismiss_interstitial(page) (no-op for most sites)
     g. Re-probe; raise on failure
     h. Persist storage state
4. Navigate to listing_url when configured
5. items = site.get_latest_items(page) (videos only)
6. For each item:
     a. INSERT OR IGNORE into seen_items
     b. If newly inserted: send Discord notification
     c. On notification failure: insert into failed_notifications
7. Retry failed_notifications older than 5 minutes (cap 10 attempts)
8. Close browser context
9. Release lock
```

### Scheduling

`apscheduler.AsyncIOScheduler` with one job per site. `interval_hours` controls
spacing; a uniform random jitter of ±15 minutes is added per fire to avoid
all-sites-at-once thundering herds. On startup, each site's first run is
staggered by `index * 30s` to avoid simultaneous launches.

### Failure & retry

- **Login fails:** log error, abort site's run, retry on next scheduled fire.
- **Interstitial selector found but click fails:** log warning, continue (the
  scrape may still succeed; if not, normal failure path applies).
- **Listing scrape fails:** log error, abort site's run.
- **Notification fails:** insert into `failed_notifications`; retried every
  cycle until 10 attempts, then logged as permanently failed.

---

## Photo set exclusion

Two-layer defense:

1. **Listing URL is `/videos`** — never `/`, `/latest`, or anything that mixes
   content types. This is the primary filter.
2. **Per-item type check** — when iterating cards, the scraper verifies each is
   a video via DOM signal (e.g., a `.video-card` class, a `[data-type="video"]`
   attribute, or a `<video>` element/play-button selector). Cards without the
   signal are skipped.

Both layers are required. The listing URL filter alone is fragile (sites
sometimes mix content into video listings via "recommended for you" widgets);
the per-item check alone is fragile (selectors change). Together they're
robust.

ManyVids creator stores are public, but still use video-store URLs and
regular/mobile video payload extraction so photos and non-video products are not
emitted as notification items.

---

## Tooling

### Python

- Python 3.13 (matches the rest of Jas's ecosystem).
- Dependencies pinned in `pyproject.toml`; resolved with `uv` locally,
  installed with `pip` in Docker for reproducibility.

### Linting

Three linters, all configured in `pyproject.toml`:

- **ruff** — formatting + fast linting. Replaces black, isort, flake8.
  - `tool.ruff.lint.select = ["E", "W", "F", "I", "B", "C4", "UP", "SIM",
    "RUF", "N", "PT", "TID"]`
  - `tool.ruff.format.indent-style = "space"`
- **mypy** — strict mode (`tool.mypy.strict = true`).
- **pylint** — secondary linter for issues ruff doesn't catch (cyclomatic
  complexity, design smells). Configured permissively to avoid duplicating
  ruff.

All output: spaces only, no trailing whitespace (enforced by ruff format).

### Testing

- **pytest** with `pytest-asyncio`, `pytest-cov`, `pytest-mock`.
- Coverage target: 80% line, fail CI if below.
- All Playwright interaction mocked via `pytest-mock` — no real browser in
  unit tests. (Functional tests against live sites are out of scope for v1
  and would not run in CI anyway.)
- Test layout mirrors `src/`.

### Makefile

```make
.PHONY: help venv install lint lintfix test test-cov clean docker-build docker-run

PYTHON := python3.13
VENV := .venv
VENV_BIN := $(VENV)/bin

help:
	@echo "Targets: venv install lint lintfix test test-cov clean docker-build docker-run"

venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV_BIN)/pip install --upgrade pip

install: venv
	$(VENV_BIN)/pip install -e ".[dev]"
	$(VENV_BIN)/playwright install chromium

lint:
	$(VENV_BIN)/ruff check src tests
	$(VENV_BIN)/ruff format --check src tests
	$(VENV_BIN)/mypy src
	$(VENV_BIN)/pylint src

lintfix:
	$(VENV_BIN)/ruff check --fix src tests
	$(VENV_BIN)/ruff format src tests

test:
	$(VENV_BIN)/pytest

test-cov:
	$(VENV_BIN)/pytest --cov=adult_sub_monitor --cov-report=term-missing --cov-fail-under=80

clean:
	rm -rf $(VENV) .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage
	find . -type d -name __pycache__ -exec rm -rf {} +

docker-build:
	docker build -t adult-sub-monitor:dev .

docker-run:
	docker compose up --build
```

---

## Docker

### `Dockerfile`

```dockerfile
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# System deps already in playwright image; just install Python deps
COPY pyproject.toml ./
RUN pip install --no-cache-dir .

COPY src/ ./src/

# Non-root user
RUN useradd -m -u 1000 monitor && \
    mkdir -p /data /config && \
    chown -R monitor:monitor /app /data /config

USER monitor

VOLUME ["/data", "/config"]

ENV PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/config/config.yaml \
    PYTHONDONTWRITEBYTECODE=1

CMD ["python", "-m", "adult_sub_monitor"]
```

### `docker-compose.yml`

```yaml
services:
  monitor:
    image: ghcr.io/jasmeralia/adult-sub-monitor:latest
    container_name: adult-sub-monitor
    restart: unless-stopped
    volumes:
      - ./config:/config:ro
      - ./data:/data
    environment:
      - LOG_LEVEL=INFO
```

---

## Unit Tests

### Coverage map

| Module                          | Test file                              | Approach                               |
|---------------------------------|----------------------------------------|----------------------------------------|
| `db.py`                         | `tests/test_db.py`                     | In-memory SQLite, fixtures             |
| `discord.py`                    | `tests/test_discord.py`                | `aioresponses` mocks                   |
| `config.py`                     | `tests/test_config.py`                 | YAML fixtures, Pydantic validation     |
| `browser.py`                    | `tests/test_browser.py`                | `AsyncMock` for Playwright objects     |
| `main.py`                       | `tests/test_main.py`                   | Mocked scheduler, mocked sites         |
| `sites/base.py`                 | `tests/sites/test_base.py`             | Concrete test subclass                 |
| `sites/manyvids.py`             | `tests/sites/test_manyvids.py`         | HTML fixtures + AsyncMock pages        |
| `sites/venus_platform.py`       | `tests/sites/test_venus_platform.py`   | HTML fixtures + AsyncMock pages        |
| `sites/wowgirls_platform.py`    | `tests/sites/test_wowgirls_platform.py` | HTML fixtures                        |

### Key test cases

**`test_db.py`**
- `seen_items` insert-and-detect-new flow
- Duplicate insert returns "not new"
- `failed_notifications` insert/increment/cap-at-10
- Metadata columns for duration, price, video type, and creator round-trip
- Schema migration is idempotent

**`test_discord.py`**
- Embed structure matches expected JSON shape
- Creator/type/duration/price fields render when present and are omitted when
  absent
- 429 response triggers retry with backoff
- Network error returns failure (caller logs)
- Webhook URL missing raises clear error

**`test_browser.py`**
- `ensure_authenticated`: probe says logged in → skips login
- `ensure_authenticated`: probe says not logged in → calls login, dismisses interstitial
- `ensure_authenticated`: anonymous site skips probe/login and receives context
  options/init scripts
- Cookie restore path: never calls `dismiss_interstitial`
- Storage state persisted after successful auth

**`test_venus_platform.py`**
- Listing with only videos → all returned
- Listing with mixed video + photo cards → only videos returned
- Listing empty → returns empty list
- Title/thumbnail/performers/tags extracted correctly

**`test_manyvids.py`**
- RSC payload extraction for regular/mobile store pages
- DOM enrichment of video type and thumbnails
- Detail-page tag extraction strips leading `#`
- Early-stop behavior when all page titles are already known
- Retry/backoff behavior for blocked or failed creator scrapes
- Anonymous `BaseSite` hooks and item metadata mapping

**`test_main.py`**
- `RUN_ONCE=1` runs each site once and exits
- `DRY_RUN=1` skips DB writes and notifications
- Per-site lock prevents overlapping runs
- New item triggers notification; duplicate does not

---

## CI / CD

### `.github/workflows/lint.yml`

Runs on all branches and PRs.

```yaml
name: lint
on:
  push:
  pull_request:

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -e ".[dev]"
      - run: make lint
```

### `.github/workflows/test.yml`

Runs on all branches and PRs.

```yaml
name: test
on:
  push:
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"
      - run: pip install -e ".[dev]"
      - run: make test-cov
```

### `.github/workflows/release.yml`

Runs only on push to `master`. Auto-tags from `VERSION` file and publishes
Docker image to GHCR with both `:latest` and `:<version-without-v-prefix>`.

```yaml
name: release
on:
  push:
    branches: [master]

permissions:
  contents: write
  packages: write

jobs:
  release:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Read version
        id: version
        run: |
          VERSION=$(cat VERSION | tr -d 'v\n')
          echo "version=$VERSION" >> "$GITHUB_OUTPUT"
          echo "tag=v$VERSION" >> "$GITHUB_OUTPUT"

      - name: Check tag does not exist
        id: tagcheck
        run: |
          if git rev-parse "${{ steps.version.outputs.tag }}" >/dev/null 2>&1; then
            echo "exists=true" >> "$GITHUB_OUTPUT"
          else
            echo "exists=false" >> "$GITHUB_OUTPUT"
          fi

      - name: Create and push tag
        if: steps.tagcheck.outputs.exists == 'false'
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git tag "${{ steps.version.outputs.tag }}"
          git push origin "${{ steps.version.outputs.tag }}"

      - name: Log in to GHCR
        if: steps.tagcheck.outputs.exists == 'false'
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up Buildx
        if: steps.tagcheck.outputs.exists == 'false'
        uses: docker/setup-buildx-action@v3

      - name: Build and push
        if: steps.tagcheck.outputs.exists == 'false'
        uses: docker/build-push-action@v6
        with:
          context: .
          push: true
          tags: |
            ghcr.io/${{ github.repository_owner }}/adult-sub-monitor:latest
            ghcr.io/${{ github.repository_owner }}/adult-sub-monitor:${{ steps.version.outputs.version }}
```

Tag-already-exists check means bumping `VERSION` is what triggers a release;
merging to master without a version bump produces no new tag/image (still
runs lint/test).

### `.github/dependabot.yml`

```yaml
version: 2
updates:
  - package-ecosystem: pip
    directory: /
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
    groups:
      dev-dependencies:
        dependency-type: development
      production-dependencies:
        dependency-type: production

  - package-ecosystem: github-actions
    directory: /
    schedule:
      interval: weekly
    open-pull-requests-limit: 5

  - package-ecosystem: docker
    directory: /
    schedule:
      interval: weekly
    open-pull-requests-limit: 5
```

### Branch protection on `master`

Configured via GitHub UI (or `gh api` script in `scripts/setup-branch-protection.sh`):

- **Require a pull request before merging:** yes
- **Required approving reviews:** **0** (per spec)
- **Dismiss stale pull request approvals:** N/A (0 required)
- **Require status checks to pass before merging:** yes
  - `lint`
  - `test`
- **Require branches to be up to date before merging:** yes
- **Require conversation resolution before merging:** yes
- **Allowed merge methods:** **squash only** (rebase + merge commit disabled)
- **Restrict who can push:** no
- **Allow force pushes:** no
- **Allow deletions:** no

A helper script committed to the repo (gitignored secrets):

```bash
#!/usr/bin/env bash
# scripts/setup-branch-protection.sh
set -euo pipefail
gh api -X PUT "repos/${OWNER}/${REPO}/branches/master/protection" \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["lint", "test"]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 0,
    "dismiss_stale_reviews": false
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

gh api -X PATCH "repos/${OWNER}/${REPO}" \
  -f allow_squash_merge=true \
  -f allow_merge_commit=false \
  -f allow_rebase_merge=false \
  -f delete_branch_on_merge=true
```

---

## Documentation

### `CLAUDE.md` (stub)

```markdown
# CLAUDE.md

See [AGENTS.md](./AGENTS.md) for full agent guidance, conventions, and
workflow notes.

## Quick reference

After any code change, always run:

```
make lintfix && make lint && make test
```

All three must pass before committing.
```

### `AGENTS.md` (detailed)

Sections:

1. **Project overview** — one paragraph summary + link to DESIGN.md
2. **Repo conventions** — dashes in repo name, underscores in Python package
3. **Code style** — spaces only, no trailing whitespace, ruff/mypy/pylint
4. **Required workflow** — `make lintfix && make lint && make test` after
   every change (verbatim, called out in a callout block)
5. **Architecture pointers** — short summary of each module + when to edit
6. **Adding a new site** — step-by-step (new subclass, register in config
   loader, add tests, update sites table in README)
7. **Testing** — no real Playwright; all browser interaction mocked
8. **Secrets** — never commit `.env`, never log credentials, env-var pattern
9. **Release process** — bump `VERSION`, merge to master, CI handles the rest
10. **Branch / PR conventions** — feature branches, squash merge, 0 reviewers
    but CI must pass

---

## Implementation Notes

Resolved during prior discussion:
- ✅ Interstitial is post-login only (not mid-session) → simple one-shot dismiss
- ✅ Photo set exclusion via `/videos` URL + per-item type check
- ✅ Notification target is Discord only

Still unknown (resolved during implementation by inspecting live sites):
- Exact DOM selectors for each platform's login form, interstitial button,
  video card type indicator, title/thumbnail/performer/tag fields. These
  belong in the per-platform scraper module as named constants near the top.
