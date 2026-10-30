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
| `MANYVIDS_TAG_WORDS` | No | Comma-separated ManyVids tag word entries layered over the shipped defaults (see [Tag formatting](#manyvids-tag-formatting)). |
| `MANYVIDS_TAG_WORDS_FILE` | No | Path to a tag word override file. Defaults to `/config/manyvids_tag_words.txt`; ignored if the file does not exist. Layered over the shipped defaults. |

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
