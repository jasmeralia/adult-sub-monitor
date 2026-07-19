# adult-sub-monitor

Dockerized Python service that monitors subscription and public creator video sites, detects new videos, deduplicates them in SQLite, and sends Discord webhook notifications. Venus and WowGirls sites use authenticated Playwright sessions; ManyVids scrapes public creator stores anonymously.

## Prerequisites

- Docker
- Docker Compose
- Discord webhook URL
- Credentials for each authenticated monitored site

## Quick-start

1. Clone the repository.
2. Copy the example config:

   ```sh
   cp config/config.example.yaml config/config.yaml
   ```

3. Edit `config/config.yaml` and provide the required webhook and authenticated-site credentials.
4. Start the service:

   ```sh
   docker compose up
   ```

## Supported Sites

| Platform type | Sites | Auth | Notes |
|---|---|---|---|
| `venus_platform` | `venus.angels.love`, `venus.sensual.love`, `venus.ultrafilms.com` | Required | Scrapes video listings, filters out photo sets, and fetches per-video detail pages for tags. |
| `wowgirls_platform` | `venus.wowgirls.com` | Required | Uses an authenticated session and the WowGirls-specific updates listing. |
| `manyvids` | Public ManyVids creator stores | Anonymous | Scrapes configured creator stores and fetches per-video detail pages for tags. |

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `CONFIG_PATH` | No | Path to the config file. Defaults to `/config/config.yaml`. |
| `DISCORD_WEBHOOK_URL` | Yes | Discord webhook used for video notifications. |
| `<SITE>_USER` / `<SITE>_PASS` | For authenticated sites | Credentials matched by `credentials_env_user` and `credentials_env_pass`. Not used by ManyVids. |
| `RUN_ONCE` | No | Set to `1` to run one check cycle and exit. |
| `DRY_RUN` | No | Set to `1` to skip DB writes and Discord notifications. |
| `LOG_LEVEL` | No | Override log level. Defaults to `INFO`. |

## Make Targets

| Target | Description |
|---|---|
| `venv` | Create the local virtual environment and upgrade pip. |
| `install` | Install the project with development dependencies and Chromium for Playwright. |
| `lint` | Run ruff checks, ruff format check, mypy, and pylint. |
| `lintfix` | Run ruff auto-fixes and formatting. |
| `test` | Run pytest. |
| `test-cov` | Run pytest with coverage reporting and the configured coverage threshold. |
| `clean` | Remove local caches, coverage output, and the virtual environment. |
| `docker-build` | Build the local development Docker image. |
| `docker-run` | Start the service with Docker Compose. |
