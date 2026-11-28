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

## Data storage and database migration

The SQLite database defaults to `/data/db/monitor.db`. The Docker Compose file
mounts `./data` at `/data`, so the database is stored on the host at
`data/db/monitor.db`. Playwright browser sessions remain under
`/data/sessions/`. Keep `data/db/` dedicated to the database and SQLite's
transient `-wal` and `-shm` files; store backups elsewhere.

To move an existing database, stop the service first. Move `monitor.db` and,
if present, `monitor.db-wal` and `monitor.db-shm` together into `data/db/`,
preserving ownership. Set `db_path: /data/db/monitor.db` in
`config/config.yaml`, then start the service. The planned read-only web UI can
then mount only `data/db/` without exposing browser session state.

## Supported Sites

| Platform type | Sites | Auth | Notes |
|---|---|---|---|
| `venus_platform` | `venus.angels.love`, `venus.sensual.love`, `venus.ultrafilms.com` | Required | Scrapes video listings, filters out photo sets, and fetches per-video detail pages for tags. |
| `wowgirls_platform` | `venus.wowgirls.com` | Required | Uses an authenticated session and the WowGirls-specific updates listing. |
| `manyvids` | Public ManyVids creator stores | Anonymous | Parses Relay store payloads, fetches per-video detail pages for tags, and re-scans each store every 72h (default) to record price changes. |

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `CONFIG_PATH` | No | Path to the config file. Defaults to `/config/config.yaml`. |
| `DISCORD_WEBHOOK_URL` | Yes | Discord webhook used for video notifications. |
| `<SITE>_USER` / `<SITE>_PASS` | For authenticated sites | Credentials matched by `credentials_env_user` and `credentials_env_pass`. Not used by ManyVids. |
| `RUN_ONCE` | No | Set to `1` to run one check cycle and exit. |
| `RUN_PRICE_REFRESH` | No | With `RUN_ONCE=1`, also run a full price re-scan for every ManyVids creator after the checks. |
| `DRY_RUN` | No | Set to `1` to skip DB writes and Discord notifications. |
| `LOG_LEVEL` | No | Override log level. Defaults to `INFO`. |
| `MANYVIDS_TAG_WORDS` | No | Comma-separated ManyVids tag word entries layered over the shipped defaults (see [Tag formatting](#manyvids-tag-formatting)). |
| `MANYVIDS_TAG_WORDS_FILE` | No | Path to a tag word override file. Defaults to `/config/manyvids_tag_words.txt`; ignored if the file does not exist. Layered over the shipped defaults. |
| `MANYVIDS_DEBUG_DUMP_DIR` | No | Optional directory; when set, store pages whose payload cannot be parsed are saved there as HTML for debugging. |

## ManyVids Tag Formatting

ManyVids tags often arrive as one lowercase run (`deeppenetration`) or all lowercase (`naughty`). Notifications show them split into words with each word capitalized (`Deep Penetration`, `Naughty`), using the [`wordsegment`](https://pypi.org/project/wordsegment/) English word-frequency list. Capitalized run-together tags such as `Peefetish` are split too. A split is skipped when it would leave fragments (`satisfyer` is not "Satisfy Er") or pieces too rare to be words (a creator name like `Tantaly`).

The splitter still needs help with domain terms and acronyms, so the image ships a default word list (`src/adult_sub_monitor/data/tag_words.txt`) covering `Deepthroat`, `Blowjob`, `POV`, `BBC`, `Stepmom` and similar. You can layer your own list on top without copying the defaults. Layers apply in order and later ones win:

1. The shipped defaults.
2. A file with one entry per line (`#` starts a comment), read if present at `/config/manyvids_tag_words.txt` (bind-mounted with the rest of `config/`) or at the path in `MANYVIDS_TAG_WORDS_FILE`. See `config/manyvids_tag_words.example.txt`.
3. `MANYVIDS_TAG_WORDS`: comma-separated entries, e.g. `Kinkster,Pet Play,-Deepthroat`.

Entry forms:

- `Deepthroat` or `POV` (a single word) is never split and is displayed exactly as written.
- `Solo Girl` (a phrase with spaces) is used verbatim whenever a tag matches it with the spaces removed (`sologirl`).
- `-Deepthroat` (leading dash) removes an entry from an earlier layer, e.g. one of the shipped defaults.

The lists are read once at startup, so restart the container after changing them.

Blocked keywords (`blocked_keywords`) are matched whatever the spacing, so a keyword written `deeppenetration`, `deep penetration`, or `Deep-Penetration` blocks all three spellings.

## ManyVids price tracking

The monitor records each ManyVids video's current effective price and list
price, plus change-only observations in SQLite. It does not send price-drop
notifications. Each creator is fully re-scanned every 72 hours by default;
first scans are staggered after startup, page loads use the configured page
delays, and creators are paced using `delay_between_creators_min` and
`delay_between_creators_max`.

The `manyvids` config block supports:

| Key | Default | Purpose |
|---|---:|---|
| `price_refresh_enabled` | `true` | Enable scheduled full-store price scans. |
| `price_refresh_interval_hours` | `72` | Hours between successful refresh cycles per creator. |
| `price_refresh_jitter_seconds` | `21600` | Maximum random delay added to each scheduled scan (APScheduler jitter only delays, never advances). |
| `price_refresh_stagger_seconds` | `600` | Added spacing between creators' first scheduled scans. |

Consumers can derive sale state from the observation history. A current price
below list price is a list-price sale; otherwise a price below the 90-day high
is a price drop, then a price below first seen is a historical low. See
[`docs/DESIGN.md`](docs/DESIGN.md#price-tracking-manyvids) for the rules and
reference SQL.

Before upgrading to schema v2, stop the service and back up the database outside
`data/db/`:

```sh
sqlite3 data/db/monitor.db ".backup '/some/other/dir/monitor.db.pre-v2'"
```

Start the new image and check for the `Database migrated to schema v2` log
line, then run the read-only verification queries in
[`docs/DESIGN.md`](docs/DESIGN.md#price-tracking-manyvids).

## Make Targets

| Target | Description |
|---|---|
| `venv` | Create the local virtual environment and upgrade pip. |
| `install` | Install the project with development dependencies and Chromium for Playwright. |
| `lint` | Run ruff checks, ruff format check, mypy, and pylint. |
| `lintfix` | Run ruff auto-fixes and formatting. |
| `test` | Run pytest. |
| `test-cov` | Run pytest with an 80% threshold and write `coverage.xml` for Codecov. |
| `clean` | Remove local caches, coverage output, and the virtual environment. |
| `docker-build` | Build the local development Docker image. |
| `docker-run` | Start the service with Docker Compose. |
