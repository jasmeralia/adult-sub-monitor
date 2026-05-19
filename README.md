# adult-sub-monitor

Dockerized Python service that monitors authenticated subscription video sites, detects new videos, and sends Discord webhook notifications. Sister project to mv_video_monitor. Handles form-based login and session persistence.

## Prerequisites

- Docker
- Docker Compose
- Discord webhook URL
- Credentials for each monitored site

## Quick-start

1. Clone the repository.
2. Copy the example config:

   ```sh
   cp config/config.example.yaml config/config.yaml
   ```

3. Edit `config/config.yaml` and `.env`.
4. Start the service:

   ```sh
   docker compose up
   ```

## Sites

| Site | Family | Notes |
|---|---|---|
| `members.deeper.com` | Deeper/Tushy | Intermittent post-login interstitial |
| `members.tushy.com` | Deeper/Tushy | Intermittent post-login interstitial |
| `venus.angels.love` | Venus platform | Mixes videos and photo sets |
| `venus.sensual.love` | Venus platform | Mixes videos and photo sets |
| `venus.wowgirls.com` | Venus platform | Mixes videos and photo sets |
| `venus.ultrafilms.com` | Venus platform | Mixes videos and photo sets |

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `CONFIG_PATH` | No | Path to the config file. Defaults to `/config/config.yaml`. |
| `DISCORD_WEBHOOK_URL` | Yes | Discord webhook used for video notifications. |
| `<SITE>_USER` / `<SITE>_PASS` | Yes | Credentials for each configured site, matched by `credentials_env_user` and `credentials_env_pass`. |
| `RUN_ONCE` | No | Set to `1` to run one check cycle and exit. |
| `DRY_RUN` | No | Set to `1` to skip DB writes and Discord notifications. |
| `LOG_LEVEL` | No | Override log level. Defaults to `INFO`. |

## Make Targets

| Target | Description |
|---|---|
| `venv` | Create the local virtual environment and upgrade pip. |
| `install` | Install the project with development dependencies and Chromium for Playwright. |
| `lint` | Run ruff checks, ruff format check, mypy, and pylint. |
| `lint-fix` | Run ruff auto-fixes and formatting. |
| `test` | Run pytest. |
| `test-cov` | Run pytest with coverage reporting and the configured coverage threshold. |
| `clean` | Remove local caches, coverage output, and the virtual environment. |
| `docker-build` | Build the local development Docker image. |
| `docker-run` | Start the service with Docker Compose. |

## Phase 2

Deferred features include video downloads, Plex/Jellyfin integration, and multi-channel Discord notifications.
