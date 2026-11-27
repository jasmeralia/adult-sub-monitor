# Task 678: ManyVids price tracking (with #682 parser fix and #590 ID dedup)

Status: locked implementation spec. Every design decision is made here. If this
document and your intuition disagree, follow the document. If you find a real
contradiction between this document and the code, stop and report it. Do not
invent a third design.

Odoo references:

- #678: track ManyVids prices over time (this plan's goal).
- #682: ManyVids store-page parser broken by the new Relay payload. This is a
  production outage that Part A below fixes.
- #590: ManyVids dedup should use numeric video IDs. Part B below implements it.
- #592: type attribution by title. Part A fixes this for newly scraped items as
  a side effect. Existing rows are not corrected (non-goal).

## 0. Read this first

### 0.1 Ground rules for the implementer

- Read `AGENTS.md` and follow it. Spaces only, no trailing whitespace, ruff,
  mypy strict and pylint must pass.
- After all changes, `make lintfix && make lint && make test` must pass. Also
  run `make test-cov`, because CI enforces 80% line coverage.
- Do **not** run any git write commands (branch, add, commit, push, PR). Leave
  all changes in the working tree.
- Do not launch a real browser and do not make network requests in tests. All
  Playwright interaction is mocked, per `AGENTS.md` §7.
- Do not add dependencies.
- Implement the parts in order: A, then B, then C. Each part must leave
  `make lint && make test` green, so the orchestrator can split PRs at part
  boundaries. Part A is urgent and may ship on its own.

### 0.2 Why Part A exists (live findings, 2026-11-27)

ManyVids changed the creator store page around 2026-11-24/25. Production logs
show every creator check since then logging `RSC video payload not found in
page HTML` and `scraped 0 unique videos across 2 pages`. The current
`_extract_rsc_video_data()` matches nothing, returns `[]`, and `_scrape_creator`
treats that as the end of pagination, so the failure is silent. Price tracking
depends on parsing store pages, so the parser rewrite is a hard prerequisite.

I captured the public creator store listed in `config/config.example.yaml`
anonymously with curl (`?sort=newest&page=N`, pages 1 through 7). Findings:

1. The inline RSC chunk that contains `"isVideosStore":true` embeds a normalized
   **Relay record store** under the key `"recordMap"`. Records are keyed by ID
   and link to each other with `{"__ref": "<key>"}`. Plural links use
   `{"__refs": [...]}`. The chunk no longer contains `swrFallback` or
   `totalPages`.
2. `recordMap["client:root"]` has a key `profile(id:"<creator_id>")` that points
   to the `Profile` record.
3. The `Profile` record has these connection keys (`<N>` = `(page - 1) * first`,
   and the `offset` part is absent on page 1):
   - `storeVideos(filter:{},first:70,offset:<N>,sort:"NEWEST")`: **regular**
     (horizontal) videos, 70 per page.
   - `storeVideos(filter:{"vertical":true},first:14,offset:<N>,sort:"NEWEST")`:
     **mobile** (vertical) videos, 14 per page.
   - `__ProfileStoreVideoGrid_storeVideos_connection(...)` and
     `__ProfileMobileVidsCarousel_mobileVids_connection(...)`: Relay
     connection-handle duplicates of the two connections above. **Ignore** them,
     because they share the same edges.
4. Each connection record:
   `{"__typename":"ProfileStoreVideoConnection","totalCount":359,"edges":{"__refs":[...edge keys...]},"pageInfo":{"__ref":...},"totalWithoutFilters":359}`.
   Each edge record: `{"__typename":"ProfileStoreVideoEdge","node":{"__ref":"<video key>"},"cursor":"..."}`.
5. A video record (sanitized):

   ```json
   {"__id":"BAB0001","__typename":"Video","localId":"7500001",
    "slug":"example-video-title","title":"Example Video Title",
    "price":{"__ref":"client:BAB0001:price"},"creator":{"__ref":"AzP0001"},
    "id":"BAB0001","views":331,"duration":"PT14M30S",
    "screenshot":{"__ref":"client:BAB0001:screenshot"},
    "teaser":{"__ref":"client:BAB0001:teaser"}, "...": "other flags"}
   ```

   - `localId` is the stable numeric ManyVids video ID, the one used in
     `/Video/<id>/<slug>`. `id` is an opaque Relay global ID. Never use `id`.
   - `duration` is ISO-8601 (`PT14M30S`, `PT1H2M36S`).
   - Screenshot record: `{"__typename":"Image","url":"https://ods.manyvids.com/.../screenshots/....jpg"}`.
6. **The price record, which resolves step 3 of task 678:**

   ```json
   {"__id":"client:BAB0001:price","__typename":"Price","free":false,
    "onSale":true,"regular":"24.99","discountedPrice":"21.24","promoRate":15}
   ```

   When a video is not on sale: `"onSale":false,"discountedPrice":"0.00","promoRate":0`.
   Across 408 captured videos, 406 were not on sale and 2 were (15% and 20%).
   `discountedPrice` equals `regular * (1 - promoRate/100)` rounded to the cent.
   No `free:true` example was observed.
7. Pagination: `?page=N` works for both connections at once. The `vertical` URL
   parameter is now **ignored**: `vertical=1` and `vertical=2` return identical
   payloads. The regular and mobile connections never overlapped (0 shared
   IDs). Over a complete scan of all 6 pages, unique IDs matched `totalCount`
   exactly (359 regular + 49 mobile). The mobile connection ran out after page 4
   (7 edges on page 4, 0 on pages 5 and 6), while the regular connection
   continued to page 6 (9 edges). Page 7, past both, returned the Next.js 404
   page (`NEXT_HTTP_ERROR_FALLBACK;404`, no `isVideosStore`).
8. Video detail pages still work with the current `_fetch_video_details()`:
   `og:description` matches `_DESCRIPTION_PATTERN`, and tags are present as
   `mavTag` anchors.

### 0.3 Decisions summary

| Topic | Decision |
|---|---|
| Parser | Parse `recordMap` with `json.JSONDecoder().raw_decode`, not regex, and walk refs. |
| Video type | `regular` if the video came from the non-vertical connection, `mobile` if from the vertical one. DOM title enrichment is removed. |
| Unparseable payload | Raise `StorePayloadError` (a `ScraperError`), which is retried and logged as an error. An optional HTML dump goes to `MANYVIDS_DEBUG_DUMP_DIR`. |
| List/sale mapping | `list_price_cents = regular`, always when parseable. `price_cents = discountedPrice` when `onSale` and `0 < discounted < regular`, otherwise `regular`. `free` gives `price_cents = 0`. |
| `seen_items.price` TEXT | Unchanged semantics: the regular price string at first sight. Discord is unchanged. |
| Identity | New `seen_items.video_id` (numeric ManyVids ID). Partial unique index on `(site_name, video_id)`. Known-item detection and early stop use IDs, not titles. |
| Existing duplicate IDs | Migration v2 keeps the earliest row per `(site_name, video_id)`, archives the others in `seen_items_merged_duplicates`, then deletes them. |
| Schema versioning | `PRAGMA user_version` gates migration v2 (`< 2` runs it). A `schema_version` row `2` is also inserted. Every step is itself idempotent. Everything runs in one transaction. |
| Backfills | `pricing_model` from URL host, `video_id` from URL, `first_price_cents` from `price` text. Counts are logged as `MigrationSummary`. |
| Price history | `price_observations`, written on change only, plus a first-observation row on new-item insert. `price_checked_at` is the heartbeat. No observations are synthesized from backfilled data. |
| Re-scan | A separate APScheduler job per creator (`price-refresh-<site.name>`), default every 72h, jitter 6h. The first run is computed from the persisted `price_refresh_state.last_success_at`, so restarts do not trigger re-scans. Refreshes are serialized globally, with a creator delay between them. |
| Partial scans | A full scan fails (no writes) on any page error, a 404 or empty page before the last expected page, or a unique-count mismatch with page 1's `totalCount`. |
| Sale state | Derived by consumers. Rules and reference SQL go in `docs/DESIGN.md`. No Python implementation and no persisted `on_sale`. |

## 1. Non-goals

- Price-drop Discord notifications, and any change to Discord embeds.
- Correcting `video_type`, `title`, `url` or `thumbnail_url` of existing rows
  during re-scans. The re-scan writes only price columns, observations and refresh
  state.
- Ingesting or notifying videos discovered during a price re-scan. Only the
  normal check ingests.
- Synthesizing `price_observations` from backfilled `first_price_cents`.
- Persisting `on_sale`, `promoRate`, or a sale-state column.
- Merging the key-named `site_name` duplicates (Odoo #677). ManyVids rows are
  not affected by #677.
- Fixing the historical `video_type` errors from #592.
- Subscription sites (`venus_platform`, `wowgirls_platform`): no price columns
  are populated and no re-scans run.
- Removing the now-unused `delay_between_creators_*` config. Part C starts
  using it.
- A web UI. The consumer is the separate `storm-monitor-explorer` repo.

## 2. Part A: parse the new ManyVids store payload (#682)

### 2.1 New module `src/adult_sub_monitor/pricing.py`

Create it in Part A, because the parser needs `parse_price_cents` and
`resolve_manyvids_price`. Part C adds dataclasses to it.

```python
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_MANYVIDS_URL = re.compile(r"^https?://(?:www\.)?manyvids\.com/", re.IGNORECASE)
_MANYVIDS_VIDEO_ID = re.compile(
    r"^https?://(?:www\.)?manyvids\.com/Video/(\d+)(?:[/?#]|$)", re.IGNORECASE
)


def parse_price_cents(text: str | None) -> int | None:
    """Parse a dollar string ("24.99", "$5.99", "1,299.00") to integer cents.

    Returns None for None, empty or whitespace, unparseable, negative, or
    non-finite input. Rounds half-up to the cent ("5.999" -> 600).
    """


def resolve_manyvids_price(
    regular: str | None,
    discounted: str | None,
    *,
    on_sale: bool,
    free: bool,
) -> tuple[int | None, int | None]:
    """Return (price_cents, list_price_cents) for a ManyVids Price record."""


def is_manyvids_url(url: str) -> bool: ...


def manyvids_video_id_from_url(url: str) -> str | None: ...
```

`parse_price_cents` algorithm: if `text is None`, return None. `s =
text.strip().removeprefix("$").replace(",", "").strip()`. If `s` is empty,
return None. `Decimal(s)` (catch `InvalidOperation` and return None). If it is
not finite or is `< 0`, return None. Return
`int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))`.

`resolve_manyvids_price` rules, in order:

1. `list_cents = parse_price_cents(regular)`.
2. If `free`: return `(0, list_cents)`.
3. If `list_cents is None`: return `(None, None)`.
4. `disc = parse_price_cents(discounted)`. If `on_sale and disc is not None and
   0 < disc < list_cents`: return `(disc, list_cents)`.
5. Return `(list_cents, list_cents)`.

`manyvids_video_id_from_url`: return `_MANYVIDS_VIDEO_ID.match(url).group(1)`
or None. `is_manyvids_url`: `_MANYVIDS_URL.match(url) is not None`.

### 2.2 Rewrite the store parsing in `src/adult_sub_monitor/sites/manyvids.py`

**Remove:** `_VIDEO_PATTERN`, `_TOTAL_PAGES_PATTERN`, `_CARD_PATTERN`,
`_extract_rsc_video_data`, `_enrich_videos_from_dom`. Keep `_RSC_PATTERN`,
`_TagHTMLParser`, `_DESCRIPTION_PATTERN`, the tag-normalization helpers,
`_extract_tags_from_html`, `_load_page` and `_fetch_video_details` unchanged.

**Add constants:**

```python
_RECORD_MAP_KEY = re.compile(r'"recordMap"\s*:\s*')
_IS_VIDEOS_STORE = re.compile(r'"isVideosStore"\s*:\s*true')
_FIRST_ARG = re.compile(r"\bfirst:(\d+)")
_ISO_DURATION = re.compile(
    r"^PT(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+(?:\.\d+)?)S)?$"
)
_CLOCK_DURATION = re.compile(r"^\d{1,2}(?::\d{2}){1,2}$")
_MAX_STORE_PAGES = 200
DEBUG_DUMP_ENV = "MANYVIDS_DEBUG_DUMP_DIR"
```

**New exception:**

```python
class StorePayloadError(ScraperError):
    pass
```

**`VideoData`** (replace the existing dataclass; keep the pylint disable):

```python
@dataclass
class VideoData:  # pylint: disable=too-many-instance-attributes
    video_id: str
    title: str
    slug: str
    url: str
    video_type: str               # "regular" | "mobile"
    thumbnail_url: str | None
    price_regular: str | None     # raw "regular" string, e.g. "24.99"
    duration: str | None          # "MM:SS" or "HH:MM:SS"
    price_cents: int | None = None
    list_price_cents: int | None = None
```

**New dataclass:**

```python
@dataclass
class StorePage:
    videos: list[VideoData]
    regular_total: int
    regular_page_size: int
    mobile_total: int
    mobile_page_size: int

    @property
    def total_pages(self) -> int:
        """max(ceil(regular_total/regular_page_size), ceil(mobile_total/mobile_page_size), 1).
        A connection with page_size 0 contributes 0."""

    @property
    def expected_total(self) -> int:
        return self.regular_total + self.mobile_total
```

`CreatorResult` keeps its fields. `total_pages` now means "pages actually
loaded".

**Functions:**

```python
def _format_iso_duration(value: object) -> str | None:
```

Return None if `value` is not a non-empty `str`. If it matches `_ISO_DURATION`
(and is not just `"PT"`), compute `h, m, s` with `s = int(float(s))`, then
normalize: total seconds, then hours, minutes and seconds. Return
`f"{h:02d}:{m:02d}:{s:02d}"` if hours > 0, else `f"{m:02d}:{s:02d}"`. If it
matches `_CLOCK_DURATION`, return it unchanged. Otherwise return None and log
at DEBUG.

Expected: `PT14M30S` gives `14:30`, `PT1H2M36S` gives `01:02:36`, `PT45S`
gives `00:45`, `PT2H` gives `02:00:00`, `PT7M5.5S` gives `07:05`, `PT90M`
gives `01:30:00`, `16:41` gives `16:41`, and `"PT"`, `""`, `None`, `"abc"`
give None.

```python
def _extract_record_map(html: str) -> dict[str, dict[str, object]] | None:
```

For each `_RSC_PATTERN` match, `json.loads('"' + raw + '"')` (skip on
`json.JSONDecodeError`/`ValueError`). Skip chunks where `_IS_VIDEOS_STORE`
does not match. Find `_RECORD_MAP_KEY`, then
`obj, _ = json.JSONDecoder().raw_decode(decoded, match.end())` (skip on
`JSONDecodeError`). Return `obj` if it is a `dict`. Return None if nothing
qualifies.

```python
def _deref(records: Mapping[str, object], link: object) -> dict[str, object] | None:
```

If `link` is a dict with a str `"__ref"`, return `records.get(ref)` when that
is a dict, otherwise None.

```python
def _parse_store_page(html: str) -> StorePage:
```

1. `records = _extract_record_map(html)`. If None, raise
   `StorePayloadError("ManyVids store recordMap not found in page HTML")`.
2. Profile: `root = records.get("client:root")`. Take the first key of `root`
   that starts with `"profile("` and deref it. Fallback: the first record whose
   `__typename == "Profile"`. If there is none, raise
   `StorePayloadError("ManyVids store payload has no Profile record")`.
3. Connections: iterate `profile.items()`. Consider only keys that start with
   `"storeVideos("` (this skips the `__Profile...` handle keys). Deref each
   value. If the key contains `'"vertical":true'`, it is the mobile
   connection, otherwise the regular one. If there are several of one kind,
   keep the first. If no regular connection is found, raise
   `StorePayloadError("ManyVids store payload has no storeVideos connection")`.
   The mobile connection is optional (absent gives total 0, page size 0).
4. For each connection: `total = int(conn.get("totalCount") or 0)`. Edge refs
   come from `conn["edges"]["__refs"]` when that is a list of str. Also accept
   `conn["edges"]` being a list of `{"__ref": ...}` dicts. Page size comes
   from `_FIRST_ARG.search(key)`, falling back to `len(edge_refs)`. For each
   edge ref: `edge = records.get(ref)`, `video = _deref(records,
   edge.get("node"))`. Skip it unless `video["__typename"] == "Video"` and
   `localId`, `slug` and `title` are non-empty strings and `localId.isdigit()`
   (log skipped records at DEBUG).
5. Build `VideoData` for each video:
   - `video_id = localId`
   - `title = html_module.unescape(title)`
   - `url = f"{BASE_URL}/Video/{video_id}/{slug}"`
   - `video_type` is `"regular"` or `"mobile"` by connection
   - `thumbnail_url` is the deref'd `screenshot` record's `"url"` when it is a
     non-empty str, else None
   - `duration = _format_iso_duration(video.get("duration"))`
   - Price: `price = _deref(records, video.get("price")) or {}`.
     `regular = price.get("regular")` when it is a str, else None.
     `discounted` the same for `"discountedPrice"`.
     `price_cents, list_price_cents = resolve_manyvids_price(regular,
     discounted, on_sale=price.get("onSale") is True, free=price.get("free")
     is True)`.
     `price_regular = regular.strip() or None` when `regular` is a str, else None.
6. Process the regular connection first, then mobile. Dedupe by `video_id`
   (the first occurrence wins).
7. Log at DEBUG: the counts and totals. Return `StorePage`.

```python
def _build_page_url(creator_id: str, creator_name: str, page: int) -> str:
```

Base URL `f"{BASE_URL}/Profile/{creator_id}/{creator_name}/Store/Videos"`,
params `{"sort": "newest"}` plus `"page": str(page)` when `page > 1`. Drop the
`vertical` parameter.

```python
def _dump_debug_html(html: str, creator_id: str, page_num: int) -> Path | None:
```

Read `os.environ.get(DEBUG_DUMP_ENV)` at call time. If it is unset or empty,
return None. Otherwise `directory = Path(value)`, `directory.mkdir(parents=True,
exist_ok=True)`, and write `html` (UTF-8) to `directory /
f"store-{creator_id}-p{page_num}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}.html"`.
Log a WARNING with the path and return it. Catch `OSError`, log a WARNING, and
return None.

### 2.3 `_scrape_creator` and `scrape_creator_with_retry`

Replace the section loop with a single page loop over the combined page:

```python
async def _scrape_creator(  # pylint: disable=too-many-locals,too-many-branches
    self,
    page: Page,
    creator: ManyVidsCreator,
    known_ids: set[str],
    *,
    full_scan: bool = False,
) -> CreatorResult:
```

Algorithm:

```
all_videos = []; total_pages = 1; expected_total = 0; page_num = 1; pages_loaded = 0
while page_num <= min(total_pages, _MAX_STORE_PAGES):
    if page_num > 1: sleep random.uniform(delay_between_pages_min, max) (keep the debug log)
    url = _build_page_url(creator.creator_id, creator.creator_name, page_num)
    try: html = await self._load_page(page, url)
    except ScraperNotFoundError as exc:
        if page_num == 1: raise
        if full_scan: raise ScraperError(f"Page {page_num} of {total_pages} returned 404 during full scan") from exc
        logger.info("Creator %s: stopping pagination at page %s after 404", ...); break
    try: store_page = _parse_store_page(html)
    except StorePayloadError:
        _dump_debug_html(html, creator.creator_id, page_num)
        raise
    pages_loaded += 1
    if page_num == 1: total_pages = store_page.total_pages; expected_total = store_page.expected_total
    if not store_page.videos:
        if full_scan and page_num <= total_pages and expected_total > 0:
            raise ScraperError(f"Page {page_num} of {total_pages} had no videos during full scan")
        logger.info("Creator %s: no videos on page %s, stopping", ...); break
    all_videos.extend(store_page.videos)
    if not full_scan and {v.video_id for v in store_page.videos} <= known_ids:
        logger.info("Creator %s: all videos on page %s already known", ...); break
    page_num += 1
unique = dedupe all_videos by video_id (first wins)
if full_scan and len(unique) != expected_total:
    raise ScraperError(f"Full scan incomplete: {len(unique)} of {expected_total} videos")
logger.info("Creator %s: scraped %s unique videos across %s pages%s", ..., " (full scan)" if full_scan else "")
return CreatorResult(creator_id=..., creator_name=..., videos=unique, total_pages=pages_loaded)
```

Note: a creator whose page 1 has zero videos and `expected_total == 0` is a
successful empty scan in both modes.

`scrape_creator_with_retry(self, page, creator, known_ids: set[str], *,
full_scan: bool = False)` keeps its retry/backoff/`ScraperNotFoundError`
semantics and passes `full_scan` through. `StorePayloadError` is a
`ScraperError`, so it is retried by the existing `except ScraperError` branch.

### 2.4 `get_latest_items` in Part A

Keep the title-based known set in Part A, so Part A is shippable alone:
`known_titles` stays, but `_scrape_creator` now takes IDs. Bridge it as
follows:

- `scrape_creator_with_retry(page, creator, set())` (pass an empty
  `known_ids`; early stop is disabled in Part A).
- Filter with `if video.title in known_titles: continue` exactly as today.
- Build `Item` exactly as today (`price=video.price_regular`).

Part B replaces this with ID-based detection. **If Parts A and B are
implemented in one go, skip this bridge and go straight to §3.3.** The tests
listed for Part A that touch `get_latest_items` are then written in their
Part B form.

### 2.5 Part A tests

Delete `tests/fixtures/manyvids/creator_store_regular_p1.html`,
`creator_store_regular_p2_known.html` and `creator_store_mobile_p1.html`. They
are the obsolete format. Keep `video_detail_with_tags.html`.

Create a **test helper module** `tests/sites/manyvids_payload.py` (not
collected, because its name does not start with `test_`):

```python
def video_spec(
    video_id: str,
    title: str | None = None,
    *,
    slug: str | None = None,
    duration: str = "PT2M13S",
    regular: str = "5.99",
    discounted: str = "0.00",
    on_sale: bool = False,
    free: bool = False,
    promo_rate: int = 0,
    thumbnail: str | None = "https://cdn.example/{video_id}.jpg",
) -> dict[str, object]: ...

def build_store_html(
    regular: list[dict[str, object]],
    mobile: list[dict[str, object]] | None = None,
    *,
    regular_total: int | None = None,   # default len(regular)
    mobile_total: int | None = None,    # default len(mobile or [])
    page: int = 1,
    regular_first: int = 70,
    mobile_first: int = 14,
    creator_id: str = "1002990973",
    include_mobile_connection: bool = True,
    include_handle_connections: bool = True,
) -> str: ...
```

`build_store_html` must reproduce the real shape from §0.2 exactly:

- `records["client:root"] = {"__id": "client:root", "__typename": "__Root",
  f'profile(id:"{creator_id}")': {"__ref": "PROF1"}}`.
- `records["PROF1"]`: `__typename` `"Profile"`, `localId` `creator_id`. Add the
  regular connection key `f'storeVideos(filter:{{}},first:{regular_first}{off},sort:"NEWEST")'`,
  where `off = f",offset:{(page-1)*regular_first}"` if `page > 1` else `""`.
  Add the mobile key `f'storeVideos(filter:{{"vertical":true}},first:{mobile_first}{off_m},sort:"NEWEST")'`
  (only when `include_mobile_connection`). When `include_handle_connections`,
  also add `'__ProfileStoreVideoGrid_storeVideos_connection(filter:{},sort:"NEWEST")'`
  pointing at a **separate** connection record containing one extra edge to a
  Video record with `localId` `"999999"`. The parser must ignore it, and tests
  assert `"999999"` never appears.
- Each connection is keyed `"client:PROF1:" + key`, with `__typename`
  `ProfileStoreVideoConnection`, `totalCount`, `totalWithoutFilters`,
  `edges: {"__refs": [...]}` and `pageInfo` (a `ForwardOnlyPageInfo` record
  with `hasNextPage`).
- An edge record for each video, a `Video` record (`localId`, `slug` defaulting
  to `title` lowercased with spaces turned into dashes, `title` defaulting to
  `f"Video {video_id}"`, `duration`, and `price`/`screenshot` refs), a `Price`
  record (`free`, `onSale`, `regular`, `discountedPrice`, `promoRate`), and an
  `Image` screenshot record when `thumbnail` is not None (format `{video_id}`
  into it). When `thumbnail` is None, omit the `screenshot` key entirely.
- Chunk: `chunk = "6b:" + json.dumps(["$", "$L67", None, {"isVideosStore":
  True, "url": {"id": creator_id, "slug": "creator", "mainSection": "STORE",
  "subSection": "VIDEOS"}, "children": ["$", "$L68", None, {"recordMap":
  records}]}], separators=(",", ":"))`.
- HTML: `"<html><head><title>Store</title></head><body><script>self.__next_f.push([1,"
  + json.dumps(chunk) + "])</script></body></html>"`.

Tests in `tests/test_pricing.py` (new file):

- `test_parse_price_cents`, parametrized: `"24.99"`→2499, `"0.00"`→0,
  `"$5.99"`→599, `"1,299.00"`→129900, `" 7.5 "`→750, `"5.999"`→600,
  `""`→None, `"  "`→None, `None`→None, `"abc"`→None, `"-1.00"`→None,
  `"NaN"`→None, `"Infinity"`→None.
- `test_resolve_manyvids_price`, parametrized:
  `("24.99","21.24",on_sale=True,free=False)`→`(2124,2499)`;
  `("11.99","0.00",False,False)`→`(1199,1199)`;
  `("11.99","0.00",True,False)`→`(1199,1199)`;
  `("11.99","12.99",True,False)`→`(1199,1199)`;
  `("11.99","9.99",False,False)`→`(1199,1199)` (not on sale, so discounted is ignored);
  `("9.99","0.00",False,True)`→`(0,999)`; `(None,None,False,False)`→`(None,None)`;
  `("","0.00",False,False)`→`(None,None)`.
- `test_manyvids_video_id_from_url`, parametrized:
  `https://www.manyvids.com/Video/7774614/blake-blossom`→`"7774614"`,
  `https://www.manyvids.com/Video/123`→`"123"`,
  `https://www.manyvids.com/Video/123/x?y=1`→`"123"`,
  `http://manyvids.com/Video/55/x`→`"55"`,
  `https://www.manyvids.com/Profile/1/x/Store/Videos`→None,
  `https://venus.sensual.love/members/content/1`→None,
  `https://www.manyvids.com/Video/abc/x`→None.
- `test_is_manyvids_url`: a ManyVids video URL and profile URL give True. A
  Venus URL and `https://notmanyvids.com/Video/1/x` give False.

Tests in `tests/sites/test_manyvids.py`. Remove
`test_extract_rsc_video_data_on_rsc_fixture`,
`test_enrich_videos_from_dom_flips_mobile_and_overrides_thumbnail` and the
imports of removed names. Rewrite
`test_scrape_creator_early_stops_when_page_titles_are_known` and
`test_scrape_creator_keeps_videos_when_later_page_is_not_found` per below. Keep
all other existing tests passing (adjust only the call signatures that
changed).

- `test_parse_store_page_extracts_regular_and_mobile_videos`: 2 regular and 1
  mobile. Assert ids in order, `video_type`, `url`, `thumbnail_url`,
  `duration` (`"02:13"`), `price_regular`, `price_cents`, `list_price_cents`,
  `regular_total`, `mobile_total`, page sizes `70`/`14`, `total_pages == 1`,
  and that `"999999"` (the handle connection) is absent.
- `test_parse_store_page_maps_on_sale_price`: an on-sale spec
  (`regular="24.99", discounted="21.24", on_sale=True, promo_rate=15`) gives
  `price_cents == 2124`, `list_price_cents == 2499` and `price_regular == "24.99"`.
- `test_parse_store_page_free_video`: `free=True, regular="9.99"` gives
  `price_cents == 0` and `list_price_cents == 999`.
- `test_parse_store_page_total_pages_uses_larger_connection`:
  `regular_total=10, mobile_total=50` (page sizes 70/14) gives `total_pages == 4`
  and `expected_total == 60`. Also `regular_total=359, mobile_total=49` gives 6.
- `test_parse_store_page_without_mobile_connection`:
  `include_mobile_connection=False` gives `mobile_total == 0` and only regular
  videos.
- `test_parse_store_page_dedupes_video_in_both_connections`: the same id in
  both lists yields one `VideoData` with `video_type == "regular"`.
- `test_parse_store_page_keeps_same_title_distinct_ids` (#590/#592
  regression): ids `"7503619"` (regular) and `"7503623"` (mobile) with the
  identical title give two videos, each with its own type and thumbnail.
- `test_parse_store_page_skips_non_numeric_local_id`.
- `test_parse_store_page_tolerates_missing_price_and_screenshot`: delete the
  `price` key from one Video record (post-process the builder's records via a
  builder flag or by building records then editing them; pick whichever keeps
  the helper simple), and set `thumbnail=None`. That video has `price_cents is
  None`, `price_regular is None` and `thumbnail_url is None`.
- `test_parse_store_page_raises_when_record_map_missing`: plain HTML with no
  push, and an `isVideosStore` chunk without `recordMap`, both raise
  `StorePayloadError`.
- `test_parse_store_page_raises_when_no_store_connection`.
- `test_format_iso_duration`: parametrized with the expectations in §2.2.
- `test_build_page_url`: page 1 has `sort=newest` and no `page` or `vertical`
  parameter. Page 3 has `page=3`.
- `test_scrape_creator_early_stops_when_page_ids_are_known`: page 1 built with
  `regular_total=140` (2 pages) where every id is in `known_ids`. Only 1 load,
  and the result contains the page-1 videos.
- `test_scrape_creator_follows_pages_until_total_pages`: `regular_total=140`,
  page 1 has an unknown id, page 2 loaded and then stop. Exactly 2 loads, and
  the URL of load 2 contains `page=2`.
- `test_scrape_creator_keeps_videos_when_later_page_is_not_found`: incremental
  mode, page 2 raises `ScraperNotFoundError`. The page-1 videos are returned
  with no error.
- `test_scrape_creator_unparseable_payload_raises_and_retries`: `_load_page`
  returns `"<html></html>"`. `scrape_creator_with_retry` returns `result.error`
  containing `"recordMap not found"`, and `_load_page` was awaited
  `max_retries + 1` times (2 with the test `_scraping()`).
- `test_scrape_creator_writes_debug_dump_when_env_set`: with `monkeypatch.setenv(
  "MANYVIDS_DEBUG_DUMP_DIR", str(tmp_path))` and an unparseable page, exactly one
  file `store-1002990973-p1-*.html` exists with the HTML content after a single
  `_scrape_creator` call (`pytest.raises(StorePayloadError)`).
- `test_scrape_creator_no_debug_dump_when_env_unset`: `monkeypatch.delenv(...,
  raising=False)`. `_dump_debug_html` returns None and writes nothing.

### 2.6 Part A acceptance

- The parser handles the real shape from §0.2. Unparseable pages raise and are
  retried instead of silently returning nothing.
- The `vertical` URL parameter is gone. Type comes from connection membership.
- Docs: in `docs/DESIGN.md`, rewrite the ManyVidsSite paragraph (the one headed
  by `sites/manyvids.py`) to describe the Relay `recordMap` parsing, the two
  connections and their page sizes, `?page=N` pagination, the
  `totalCount`-based page count, type by connection, raise-on-unparseable, and
  `MANYVIDS_DEBUG_DUMP_DIR`. Update the `test_manyvids.py` key test cases
  bullet list. In `README.md` and the DESIGN env table, add
  `MANYVIDS_DEBUG_DUMP_DIR` ("Optional directory; when set, store pages whose
  payload cannot be parsed are saved there as HTML for debugging"). In
  `AGENTS.md` §5, update the `sites/manyvids.py` pointer (store pages parsed
  from the embedded Relay `recordMap`) and add a `pricing.py` pointer.
- If Part A ships alone, bump `VERSION` to `0.2.1`.

## 3. Part B: numeric video-ID identity (#590)

### 3.1 Model change (`src/adult_sub_monitor/models.py`)

Add `PricingModel = Literal["per_video", "subscription"]` at module level and
these fields to `Item` (after `description`):

```python
    video_id: str | None = None
    pricing_model: PricingModel = "subscription"
    price_cents: int | None = None
    list_price_cents: int | None = None
```

(The price fields are used in Part C. Add them now so the migration and the
item round trip are designed once.)

### 3.2 Schema migration v2 (`src/adult_sub_monitor/db.py`)

Parts B and C share **one** migration (v2). Implement all of §3.2 in Part B,
including the price columns and tables. They are inert until Part C. This
avoids a v3.

Add `import logging`, `from dataclasses import dataclass`, `logger =
logging.getLogger(__name__)`, and import `is_manyvids_url`,
`manyvids_video_id_from_url` and `parse_price_cents` from
`adult_sub_monitor.pricing`.

```python
SCHEMA_VERSION = 2

@dataclass(frozen=True)
class MigrationSummary:
    per_video_rows: int          # seen_items rows with pricing_model='per_video' after migration
    subscription_rows: int       # rows with pricing_model='subscription' after migration
    video_id_backfilled: int     # rows that received a video_id in this run
    video_id_unparsed: int       # per_video rows still lacking video_id (expected 0)
    first_price_backfilled: int  # rows that received first_price_cents in this run
    first_price_unparsed: int    # per_video rows with non-empty price text that failed to parse
    duplicate_groups: int        # (site_name, video_id) groups that had >1 row
    duplicate_rows_archived: int # rows moved to seen_items_merged_duplicates and deleted
```

`Database.__init__` sets `self.last_migration_summary: MigrationSummary | None
= None` **before** calling `_apply_migrations()`.

`_apply_migrations()` keeps its current body (create tables, ensure metadata
columns, `schema_version` row 1, commit) and then calls
`self.last_migration_summary = self._migrate_to_v2()`.

```python
def _migrate_to_v2(self) -> MigrationSummary | None:
    version = self.conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return None
    self.conn.execute("BEGIN IMMEDIATE")
    try:
        self._add_v2_columns_and_tables()
        video_id_backfilled = self._backfill_identity()
        first_price_backfilled, first_price_unparsed = self._backfill_first_price()
        duplicate_groups, archived = self._merge_duplicate_video_ids()
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_seen_items_site_video "
            "ON seen_items (site_name, video_id) WHERE video_id IS NOT NULL"
        )
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (2)")
        summary = MigrationSummary(...counts, with per_video_rows/subscription_rows/video_id_unparsed via SELECT COUNT...)
        self.conn.commit()
    except Exception:
        self.conn.rollback()
        raise
    logger.info("Database migrated to schema v%s: %s", SCHEMA_VERSION, summary)
    return summary
```

`_apply_migrations()` must have committed before `BEGIN IMMEDIATE`. The
existing code commits at its end, so call `_migrate_to_v2()` after that commit.
SQLite DDL and `PRAGMA user_version` are transactional, so a failure leaves the
DB at v1 with no new columns.

`_add_v2_columns_and_tables()` uses the same `PRAGMA table_info` pattern as
`_ensure_seen_item_metadata_columns`. It adds each missing column with
`ALTER TABLE seen_items ADD COLUMN ...`:

```sql
ALTER TABLE seen_items ADD COLUMN video_id TEXT;
ALTER TABLE seen_items ADD COLUMN pricing_model TEXT;          -- 'per_video' | 'subscription'
ALTER TABLE seen_items ADD COLUMN first_price_cents INTEGER;
ALTER TABLE seen_items ADD COLUMN current_price_cents INTEGER;
ALTER TABLE seen_items ADD COLUMN list_price_cents INTEGER;
ALTER TABLE seen_items ADD COLUMN price_checked_at TIMESTAMP;
```

Then it runs these statements, each with `self.conn.execute` (not
`executescript`, which would commit the open transaction):

```sql
CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site_name TEXT NOT NULL,
    video_id TEXT NOT NULL,
    observed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    price_cents INTEGER NOT NULL,
    list_price_cents INTEGER,
    currency TEXT NOT NULL DEFAULT 'USD'
);
CREATE INDEX IF NOT EXISTS idx_price_obs_item_time
    ON price_observations (site_name, video_id, observed_at);
CREATE TABLE IF NOT EXISTS price_refresh_state (
    site_name TEXT NOT NULL,
    creator_id TEXT NOT NULL,
    last_success_at TIMESTAMP,
    last_attempt_at TIMESTAMP,
    last_error TEXT,
    PRIMARY KEY (site_name, creator_id)
);
CREATE TABLE IF NOT EXISTS seen_items_merged_duplicates (
    site_name TEXT NOT NULL,
    item_id TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    thumbnail_url TEXT,
    performers TEXT,
    tags TEXT,
    duration TEXT,
    price TEXT,
    video_type TEXT,
    creator TEXT,
    description TEXT,
    first_seen_at TIMESTAMP,
    notified_at TIMESTAMP,
    video_id TEXT NOT NULL,
    merged_into_item_id TEXT NOT NULL,
    merged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (site_name, item_id)
);
```

`_backfill_identity() -> int`: `SELECT rowid, url FROM seen_items WHERE
pricing_model IS NULL OR (pricing_model = 'per_video' AND video_id IS NULL)`.
For each row: if `is_manyvids_url(url)`, then `pricing_model = 'per_video'`
and `video_id = manyvids_video_id_from_url(url)` (may be None). Otherwise
`pricing_model = 'subscription'` and `video_id = None`. Apply with
`executemany("UPDATE seen_items SET pricing_model = ?, video_id = ? WHERE
rowid = ?", ...)`. Return the number of rows that received a non-None
`video_id`.

`_backfill_first_price() -> tuple[int, int]`: `SELECT rowid, price FROM
seen_items WHERE pricing_model = 'per_video' AND first_price_cents IS NULL AND
price IS NOT NULL AND TRIM(price) <> ''`. Parse with `parse_price_cents` and
update the parseable rows. Return `(updated, unparsed)`. (Backfilled
`first_price_cents` comes from the regular price recorded at first sight,
because the old parser read only `regular`. Document this caveat in DESIGN.)

`_merge_duplicate_video_ids() -> tuple[int, int]`:

1. `groups = SELECT site_name, video_id FROM seen_items WHERE video_id IS NOT
   NULL GROUP BY site_name, video_id HAVING COUNT(*) > 1`.
2. For each group: `rows = SELECT rowid, item_id, notified_at FROM seen_items
   WHERE site_name = ? AND video_id = ? ORDER BY first_seen_at ASC, rowid ASC`.
   The winner is `rows[0]` and the losers are `rows[1:]`.
3. `min_loser_notified = min(non-None loser notified_at)` (the string `min` of
   `YYYY-MM-DD HH:MM:SS` is chronological). If the winner's `notified_at` is
   None and a loser value exists: `UPDATE seen_items SET notified_at = ? WHERE
   rowid = ?`.
4. For each loser:

   ```sql
   INSERT OR IGNORE INTO seen_items_merged_duplicates (
       site_name, item_id, title, url, thumbnail_url, performers, tags, duration,
       price, video_type, creator, description, first_seen_at, notified_at,
       video_id, merged_into_item_id)
   SELECT site_name, item_id, title, url, thumbnail_url, performers, tags, duration,
       price, video_type, creator, description, first_seen_at, notified_at,
       video_id, ?
   FROM seen_items WHERE rowid = ?;
   DELETE FROM failed_notifications WHERE site_name = ? AND item_id = ?;
   DELETE FROM seen_items WHERE rowid = ?;
   ```

5. Return `(len(groups), number of losers)`.

### 3.3 Known-item detection by ID

`db.py`: **remove** `get_known_titles`. Add:

```python
async def get_known_video_ids(self, site_name: str) -> set[str]:
    # SELECT video_id FROM seen_items WHERE site_name = ? AND video_id IS NOT NULL
```

`mark_seen(item)`: insert the new columns as well. Part C extends the price
part (§4.3). In Part B, insert `video_id = item.video_id` and `pricing_model =
item.pricing_model`, and leave the price columns NULL. With the partial unique
index, `INSERT OR IGNORE` returns rowcount 0 for an existing `(site_name,
video_id)` under a different URL/slug, so the item is reported "not new" and
is not notified.

`get_pending_retries`: also select `seen_items.video_id`,
`COALESCE(seen_items.pricing_model, 'subscription')`,
`seen_items.current_price_cents` and `seen_items.list_price_cents`, and map
them to `Item.video_id`, `pricing_model`, `price_cents` and
`list_price_cents`.

`sites/manyvids.py` `get_latest_items`:

```python
known_ids = await db.get_known_video_ids(self.display_name) if db else set()
result = await self.scrape_creator_with_retry(page, creator, known_ids)
...
for video in result.videos:
    if video.video_id in known_ids:
        continue
    tags, description = await self._fetch_video_details(page, video.url)
    items.append(Item(
        site_name=self.display_name, item_id=video.url, title=video.title, url=video.url,
        thumbnail_url=video.thumbnail_url, tags=tags, description=description,
        duration=video.duration, price=video.price_regular, video_type=video.video_type,
        creator=creator_name, video_id=video.video_id, pricing_model="per_video",
        price_cents=video.price_cents, list_price_cents=video.list_price_cents,
    ))
```

`item_id` stays the full video URL. The primary key identity is preserved, as
#590 requires.

Venus and WowGirls do not change. Their items default to
`pricing_model="subscription"` and `video_id=None`.

### 3.4 Part B tests

`tests/test_db.py`:

- Replace `test_get_known_titles_returns_titles_for_site` with
  `test_get_known_video_ids_returns_ids_for_site`. Insert ManyVids-style items
  (with `video_id`) for two sites plus one item with `video_id=None`, and assert
  only that site's non-null IDs are returned.
- `test_fresh_database_is_schema_v2`: `PRAGMA user_version == 2`,
  `schema_version` contains 1 and 2, all six new columns exist, the tables
  `price_observations`, `price_refresh_state` and `seen_items_merged_duplicates`
  exist, and the indexes `idx_price_obs_item_time` and
  `idx_seen_items_site_video` exist (query `sqlite_master`).
  `db.last_migration_summary` is a `MigrationSummary` with all-zero counts.
- Add a module-level helper `_create_v1_database(path: Path, rows: list[tuple])`
  that builds the exact v1 schema (the current `CREATE TABLE seen_items` plus
  the `description` column, `failed_notifications` and `schema_version` with
  row 1, `user_version` left at 0) and inserts rows with explicit
  `first_seen_at`/`notified_at`.
- `test_migration_v2_backfills_legacy_rows`: v1 rows are 3 ManyVids rows (prices
  `"5.99"`, `"24.99"` and `"abc"`), 1 ManyVids row with price `NULL`, and 2
  Venus rows. After `Database(path)`: the ManyVids rows have `pricing_model =
  'per_video'` and their `video_id` from the URL, `first_price_cents` is
  599/2499/NULL/NULL, and `current_price_cents`, `list_price_cents` and
  `price_checked_at` are NULL. The Venus rows are `subscription` with NULL
  `video_id`. The summary is `per_video_rows=4, subscription_rows=2,
  video_id_backfilled=4, video_id_unparsed=0, first_price_backfilled=2,
  first_price_unparsed=1, duplicate_groups=0, duplicate_rows_archived=0`. The
  `price` text column is unchanged for every row.
- `test_migration_v2_merges_duplicate_video_ids`: v1 rows `/Video/7774614/blake-blossom`
  (`first_seen_at` 2026-06-01, `notified_at` NULL) and `/Video/7774614/blake-blosson`
  (`first_seen_at` 2026-06-02, notified 2026-06-02 10:00:00), plus a
  `failed_notifications` row for the second. After migration: exactly one
  `seen_items` row for 7774614, which is the first URL, with `notified_at =
  '2026-06-02 10:00:00'`. `seen_items_merged_duplicates` has the second row,
  with `merged_into_item_id` equal to the first URL. No `failed_notifications`
  row remains for it. The summary has `duplicate_groups=1,
  duplicate_rows_archived=1`.
- `test_migration_v2_is_idempotent_on_reopen`: migrate, close, reopen. The
  second `last_migration_summary is None`, the row counts in all tables are
  unchanged, and `user_version` is still 2. Calling `db._apply_migrations()`
  twice more also changes nothing.
- `test_migration_v2_rolls_back_on_failure`: create a v1 DB. Patch
  `Database._merge_duplicate_video_ids` to raise `RuntimeError` and assert
  `Database(path)` raises. Then open with raw `sqlite3`: `user_version == 0`
  and `video_id` is not among the `seen_items` columns. Then `Database(path)`
  without the patch succeeds.
- `test_mark_seen_rejects_duplicate_video_id_under_new_slug`: `mark_seen` of an
  Item with `video_id="7774614"` at URL `/a` returns True, and the same
  `video_id` at URL `/b` for the same site returns False (1 row). The same
  `video_id` on a different `site_name` returns True.
- Update `build_item` in `test_db.py` to accept `video_id`, `pricing_model`,
  `price_cents` and `list_price_cents` keyword arguments (all defaulting to the
  `Item` defaults). The existing `test_pending_retries_include_item_metadata`
  must keep passing unchanged.
- Keep `test_existing_seen_items_table_without_metadata_columns_is_upgraded`
  passing. Also assert that `video_id` is in the columns and that 2 is in
  `schema_versions`.

`tests/sites/test_manyvids.py`:

- Update `test_get_latest_items_populates_manyvids_metadata`. Use
  `db.get_known_video_ids = AsyncMock(return_value=set())`, assert it was
  awaited with `"ManyVids"`, and give the expected `Item` `video_id="101"`,
  `pricing_model="per_video"`, `price_cents=599` and `list_price_cents=599`
  (construct the `VideoData` with those price fields).
- `test_get_latest_items_skips_known_ids_not_titles` (#590 regression): known
  IDs `{"101"}`, and the scrape result has `101 "Same Title"` and `102 "Same
  Title"`. Exactly one item comes back (`video_id == "102"`), and
  `_fetch_video_details` was awaited once.

### 3.5 Part B docs

- `docs/DESIGN.md` Data Model: add the new columns, the three new tables, both
  new indexes, and `PRAGMA user_version = 2`. Explain the duplicate merge
  (earliest `first_seen_at` wins, losers are archived in
  `seen_items_merged_duplicates`, `notified_at` is carried over). Note that
  `item_id` stays the URL, and that identity for ManyVids is `(site_name,
  video_id)`, enforced by `idx_seen_items_site_video`. Update the `Item`
  domain type block. Update the `test_db.py` key test list.
- `docs/DESIGN.md` `ManyVidsSite`: early stop and known-item detection use
  numeric video IDs.
- `AGENTS.md` §5 `db.py` pointer: mention `PRAGMA user_version` migrations, ID
  identity and price history.

## 4. Part C: price tracking (#678)

### 4.1 Config (`src/adult_sub_monitor/models.py`)

Add to `ManyVidsScrapingConfig`:

```python
    price_refresh_enabled: bool = True
    price_refresh_interval_hours: float = Field(default=72.0, gt=0)
    price_refresh_jitter_seconds: int = Field(default=21600, ge=0)
    price_refresh_stagger_seconds: int = Field(default=600, ge=0)
```

`config/config.example.yaml` `manyvids:` block: add the four keys with their
defaults and a short comment ("Full store re-scan to record price changes;
every N hours with jitter; first scans after startup are spaced
price_refresh_stagger_seconds apart"). Also add the existing
`creator_interval_hours: 12` and `creator_jitter_seconds: 21600`, which are
currently missing from the example.

### 4.2 `src/adult_sub_monitor/pricing.py` additions

```python
@dataclass(frozen=True)
class PriceObservation:
    video_id: str
    price_cents: int
    list_price_cents: int | None


@dataclass(frozen=True)
class PriceScanSummary:
    matched: int    # per_video seen_items rows updated (heartbeat)
    changed: int    # price_observations rows written
    unmatched: int  # observations with no per_video seen_items row (ignored)
```

### 4.3 Database (`src/adult_sub_monitor/db.py`)

`mark_seen(item)` final form. In one transaction, the `INSERT OR IGNORE`
includes:

- `video_id`, `pricing_model`
- `first_price_cents` and `current_price_cents`: `item.price_cents` when
  `item.pricing_model == "per_video"`, else NULL
- `list_price_cents`: `item.list_price_cents` under the same condition
- `price_checked_at`: use SQL `CASE WHEN ? = 'per_video' AND ? IS NOT NULL
  THEN CURRENT_TIMESTAMP END` with `(item.pricing_model, item.price_cents)`

If the row was inserted (`rowcount == 1`) and `item.pricing_model ==
"per_video"` and `item.video_id is not None` and `item.price_cents is not
None`:

```sql
INSERT INTO price_observations (site_name, video_id, price_cents, list_price_cents)
VALUES (?, ?, ?, ?)
```

Commit once at the end. The return value is unchanged.

New methods:

```python
async def record_price_scan(
    self,
    site_name: str,
    creator_id: str,
    observations: Sequence[PriceObservation],
) -> PriceScanSummary:
```

All in one transaction (`try: ... self.conn.commit() except Exception:
self.conn.rollback(); raise`). For each observation:

1. `row = SELECT 1 FROM seen_items WHERE site_name = ? AND video_id = ? AND
   pricing_model = 'per_video'`. If there is none, `unmatched += 1` and
   continue.
2. `latest = SELECT price_cents, list_price_cents FROM price_observations WHERE
   site_name = ? AND video_id = ? ORDER BY observed_at DESC, id DESC LIMIT 1`.
3. If `latest is None or latest[0] != obs.price_cents or latest[1] !=
   obs.list_price_cents` (Python comparison, so `None != 123` counts as a
   change and `None == None` does not), insert a `price_observations` row and
   `changed += 1`.
4. `UPDATE seen_items SET current_price_cents = ?, list_price_cents = ?,
   price_checked_at = CURRENT_TIMESTAMP WHERE site_name = ? AND video_id = ?`,
   then `matched += 1`.

Finally, upsert the refresh state:

```sql
INSERT INTO price_refresh_state (site_name, creator_id, last_success_at, last_attempt_at, last_error)
VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
ON CONFLICT (site_name, creator_id) DO UPDATE SET
    last_success_at = excluded.last_success_at,
    last_attempt_at = excluded.last_attempt_at,
    last_error = NULL
```

Return the summary.

```python
async def record_price_scan_failure(self, site_name: str, creator_id: str, error: str) -> None:
```

Upsert: insert `(site_name, creator_id, NULL, CURRENT_TIMESTAMP, error)`. On
conflict, update only `last_attempt_at` and `last_error`, keeping
`last_success_at`. Commit.

```python
async def get_price_refresh_last_success(self, site_name: str, creator_id: str) -> datetime | None:
```

Return `datetime.strptime(value, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)`,
or None if there is no row or the value is NULL.

### 4.4 Site hooks (`src/adult_sub_monitor/sites/base.py`)

```python
    tracks_prices: bool = False

    def price_scope(self) -> tuple[str, str] | None:
        """(site_name, creator_id) key for price_refresh_state, or None."""
        return None

    async def refresh_prices(
        self, _page: Page, _db: "Database", *, dry_run: bool = False
    ) -> None:
        return None
```

(Use a leading-underscore parameter style matching the existing no-op hooks.
If ruff/pylint object to an unused `dry_run`, add `del dry_run` in the body.)

`ManyVidsSite`:

```python
    tracks_prices = True

    def price_scope(self) -> tuple[str, str]:
        return (self.display_name, self.creator.creator_id)

    async def refresh_prices(self, page: Page, db: Database, *, dry_run: bool = False) -> None:
        creator = self.creator
        result = await self.scrape_creator_with_retry(page, creator, set(), full_scan=True)
        if result.error:
            logger.error("Creator %s: price refresh failed; no observations recorded: %s",
                         creator.creator_name, result.error)
            if not dry_run:
                await db.record_price_scan_failure(self.display_name, creator.creator_id, result.error)
            return
        observations = [PriceObservation(v.video_id, v.price_cents, v.list_price_cents)
                        for v in result.videos if v.price_cents is not None]
        skipped = len(result.videos) - len(observations)
        if dry_run:
            logger.info("DRY_RUN: would record %s price observations for creator %s",
                        len(observations), creator.creator_name)
            return
        summary = await db.record_price_scan(self.display_name, creator.creator_id, observations)
        logger.info("Creator %s: price refresh matched=%s changed=%s unmatched=%s no_price=%s",
                    creator.creator_name, summary.matched, summary.changed, summary.unmatched, skipped)
```

The refresh never calls `_fetch_video_details` and never creates `Item`s.

### 4.5 Orchestration (`src/adult_sub_monitor/main.py`)

Add `import random`, `from datetime import UTC`, and `ManyVidsScrapingConfig`
(already imported).

```python
_PRICE_REFRESH_STARTUP_DELAY_SECONDS = 600
_price_refresh_lock: asyncio.Lock | None = None


def _get_price_refresh_lock() -> asyncio.Lock:
    global _price_refresh_lock  # pylint: disable=global-statement
    if _price_refresh_lock is None:
        _price_refresh_lock = asyncio.Lock()
    return _price_refresh_lock


def _price_refresh_initial_delay_seconds(
    last_success: datetime | None,
    now_utc: datetime,
    interval_hours: float,
    index: int,
    stagger_seconds: int,
) -> float:
    remaining = 0.0
    if last_success is not None:
        remaining = max(0.0, (last_success + timedelta(hours=interval_hours) - now_utc).total_seconds())
    return remaining + _PRICE_REFRESH_STARTUP_DELAY_SECONDS + index * stagger_seconds


async def _refresh_site_prices(
    site: BaseSite,
    site_config: SiteConfig,
    browser_manager: BrowserManager,
    db: Database,
    scraping: ManyVidsScrapingConfig,
    dry_run: bool,
) -> None:
    if not site.tracks_prices:
        return
    async with _get_price_refresh_lock():
        lock = _site_locks.setdefault(site.name, asyncio.Lock())
        async with lock:
            logger.info("Refreshing prices for %s", site.name)
            context = await browser_manager.ensure_authenticated(site, site_config)
            try:
                page = await context.new_page()
                try:
                    await site.refresh_prices(page, db, dry_run=dry_run)
                finally:
                    await page.close()
            finally:
                await context.close()
        delay = random.uniform(scraping.delay_between_creators_min, scraping.delay_between_creators_max)
        logger.debug("Waiting %.1fs before the next price refresh", delay)
        await asyncio.sleep(delay)


async def _schedule_price_refresh_jobs(
    scheduler: AsyncIOScheduler,
    active: list[tuple[SiteConfig, BaseSite]],
    scraping: ManyVidsScrapingConfig,
    browser_manager: BrowserManager,
    db: Database,
    dry_run: bool,
    now_local: datetime,
    now_utc: datetime,
) -> int:
```

`_schedule_price_refresh_jobs` returns 0 immediately when
`scraping.price_refresh_enabled` is False. Otherwise, for `index, (site_config,
site)` in `enumerate` over the pairs where `site.tracks_prices is True`:

```
scope = site.price_scope()
last = await db.get_price_refresh_last_success(*scope) if scope else None
delay = _price_refresh_initial_delay_seconds(last, now_utc, scraping.price_refresh_interval_hours, index, scraping.price_refresh_stagger_seconds)
scheduler.add_job(_refresh_site_prices,
    trigger=IntervalTrigger(hours=scraping.price_refresh_interval_hours,
                            jitter=scraping.price_refresh_jitter_seconds,
                            start_date=now_local + timedelta(seconds=delay)),
    args=[site, site_config, browser_manager, db, scraping, dry_run],
    id=f"price-refresh-{site.name}", name=f"price-refresh-{site.name}",
    max_instances=1, replace_existing=True)
```

It returns the number of jobs added and logs at INFO "Scheduled %s price
refresh jobs".

Lock order is always the global price lock, then the site lock. `_check_site`
takes only the site lock, so there is no deadlock.

`run()` changes:

- `scraping = config.manyvids or ManyVidsScrapingConfig()`.
- `RUN_ONCE` branch: after the existing per-site check loop, if
  `os.environ.get("RUN_PRICE_REFRESH") == "1"`, then `for site_config, site in
  active: await _refresh_site_prices(site, site_config, browser_manager, db,
  scraping, dry_run)` (it returns early for non-tracking sites). Then return.
- Scheduler branch: after the check-job loop, call `await
  _schedule_price_refresh_jobs(scheduler, active, scraping, browser_manager,
  db, dry_run, now, datetime.now(UTC))`.

Pacing, as documented: a full re-scan of one creator loads
`ceil(regular_total/70)` or `ceil(mobile_total/14)` pages, whichever is larger,
with `delay_between_pages_*` between pages. Failures retry with the existing
`max_retries`/`retry_backoff_base`. Refreshes run one at a time across all
creators, separated by `delay_between_creators_*`. Each creator is re-scanned
every `price_refresh_interval_hours` (± jitter). After a fresh deploy, creator
*i*'s first refresh starts about `600 + i * price_refresh_stagger_seconds`
seconds after startup, and restarts never cause extra re-scans, because the
schedule resumes from `price_refresh_state.last_success_at`.

### 4.6 Part C tests

`tests/test_pricing.py`: nothing more is needed beyond the dataclass usage
covered elsewhere.

`tests/test_db.py`:

- `test_mark_seen_per_video_item_records_first_observation`: an Item with
  `pricing_model="per_video"`, `video_id="101"`, `price_cents=2124` and
  `list_price_cents=2499`. The row has `first_price_cents=2124`,
  `current_price_cents=2124`, `list_price_cents=2499` and a non-null
  `price_checked_at`. `price_observations` has exactly one row
  `("ManyVids", "101", 2124, 2499, "USD")`. A second `mark_seen` of the same
  item adds no observation.
- `test_mark_seen_subscription_item_writes_no_price_data`: a subscription item
  with `price_cents=999` set anyway. Every price column is NULL and there are 0
  observations.
- `test_mark_seen_per_video_without_price`: `price_cents=None` gives NULL
  price columns, NULL `price_checked_at` and 0 observations.
- `test_record_price_scan_is_change_only`. Seed via `mark_seen` (first
  observation 1199/1199), then:
  1. A scan with the same price gives `changed=0, matched=1`, still 1
     observation. `price_checked_at` was updated: before the scan, set it to
     `'2000-01-01 00:00:00'` and assert that it changed.
  2. A scan with 999/1199 gives `changed=1`, 2 observations, and
     `current_price_cents=999`.
  3. A scan with 999/1099 (list-only change) gives `changed=1`, 3 observations.
  4. A scan with 999/1099 again gives `changed=0`.
- `test_record_price_scan_first_observation_when_none_exists`: a row inserted
  with no price (`price_cents=None`) and then scanned gets one observation.
- `test_record_price_scan_ignores_unknown_and_subscription_rows`: an observation
  for a `video_id` not in the DB, and one for a subscription item that has a
  `video_id`, give `unmatched=2, matched=0` and 0 observations.
- `test_record_price_scan_keys_on_numeric_video_id`: an item stored at URL
  `/Video/101/old-slug` gets an observation for `video_id="101"`, which updates
  that row.
- `test_record_price_scan_updates_refresh_state`: `get_price_refresh_last_success`
  returns a UTC-aware datetime within 1 minute of `datetime.now(UTC)`, and the
  `last_error` column is NULL.
- `test_record_price_scan_failure_keeps_last_success`: record a success, then a
  failure. `last_success_at` is unchanged, and `last_error` and
  `last_attempt_at` are set. A failure with no prior row leaves
  `last_success_at` NULL, so `get_price_refresh_last_success` returns None.
- `test_record_price_scan_rolls_back_on_error`: patch the connection so the
  `UPDATE seen_items` raises (for example, wrap `db.conn` in a proxy whose
  `execute` raises `sqlite3.OperationalError` when the SQL starts with
  `UPDATE seen_items`, or use any approach that does not touch production
  code). Afterwards there are 0 new observations and no refresh-state row.
- `test_pending_retries_include_price_fields`: for a per_video item with
  prices, the retried `Item` has `video_id`, `pricing_model == "per_video"`,
  `price_cents` and `list_price_cents`.

`tests/sites/test_manyvids.py`:

- `test_full_scan_reads_every_page_without_early_stop`: 3 pages
  (`regular_total=150`, page size 70, with page 3 holding 10). All IDs are in
  `known_ids`. 3 loads, and all 150 videos are returned.
- `test_full_scan_404_before_last_page_is_an_error`: via
  `scrape_creator_with_retry(..., full_scan=True)`, page 2 raises
  `ScraperNotFoundError` on every attempt. `result.error` contains `"returned
  404 during full scan"`.
- `test_full_scan_empty_page_before_last_page_is_an_error`.
- `test_full_scan_count_mismatch_is_an_error`: page 1 declares
  `regular_total=3` but only 2 edges. `result.error` contains `"Full scan
  incomplete: 2 of 3"`.
- `test_full_scan_empty_store_succeeds`: `regular_total=0` with no edges gives
  `error is None` and `videos == []`.
- `test_refresh_prices_records_observations_on_success`: mock
  `scrape_creator_with_retry` returning 3 videos, one with `price_cents=None`.
  `db.record_price_scan` is awaited once with `("ManyVids", "1002990973",
  [PriceObservation(...), PriceObservation(...)])`, and it was called with
  `full_scan=True`.
- `test_refresh_prices_records_nothing_on_failed_scan`: `result.error` set.
  `record_price_scan` is not awaited, and `record_price_scan_failure` is
  awaited with the error.
- `test_refresh_prices_dry_run_writes_nothing`: for both the success and the
  failure result, neither DB method is awaited.
- `test_manyvids_tracks_prices_and_price_scope`: `tracks_prices is True` and
  `price_scope() == ("ManyVids", "1002990973")`.

`tests/sites/test_base.py`:

- `test_price_tracking_defaults`: `tracks_prices is False`, `price_scope() is
  None`, and `await refresh_prices(AsyncMock(), AsyncMock()) is None`.

`tests/test_main.py`. Extend the autouse `clear_site_locks` fixture to also set
`main._price_refresh_lock = None`. Add:

- `test_price_refresh_initial_delay_seconds`, parametrized:
  - `last=None`, index 0, stagger 600 gives 600.
  - `last=None`, index 2 gives 1800.
  - `last = now - 10h`, interval 72, index 0 gives `62*3600 + 600`.
  - `last = now - 100h` (overdue), index 1, stagger 600 gives 1200.
- `test_refresh_site_prices_skips_non_tracking_site`: a site `AsyncMock` with
  `tracks_prices = False`. `browser_manager.ensure_authenticated` is not
  awaited.
- `test_refresh_site_prices_runs_refresh_and_closes_context`: `tracks_prices =
  True`. `site.refresh_prices` is awaited with `(page, db, dry_run=False)`,
  then the page and context are closed. `asyncio.sleep` (patched
  `adult_sub_monitor.main.asyncio.sleep`) is awaited once with the value from
  patched `adult_sub_monitor.main.random.uniform`.
- `test_refresh_site_prices_closes_context_when_refresh_raises`.
- `test_schedule_price_refresh_jobs_adds_job_per_tracking_site`: two
  ManyVids-like `AsyncMock` sites (`tracks_prices = True`, `price_scope =
  MagicMock(return_value=("ManyVids", "1"))` etc.), one non-tracking site, and
  `db.get_price_refresh_last_success = AsyncMock(side_effect=[None, now_utc -
  timedelta(hours=10)])`. The scheduler is a `MagicMock`. 2 jobs are added,
  with ids `price-refresh-<name>`, `IntervalTrigger` with 72 hours (inspect
  `call.kwargs["trigger"]`), and args ending with `dry_run`. The second job's
  `start_date` is about `now_local + 62h + 600s + 600s`. Compare
  `trigger.start_date` with tolerance, stripping tzinfo as needed. If
  APScheduler makes `start_date` awkward to inspect, patch
  `adult_sub_monitor.main.IntervalTrigger` with a `MagicMock` and assert its
  call kwargs instead.
- `test_schedule_price_refresh_jobs_disabled`: `price_refresh_enabled=False`
  gives 0 jobs and `add_job` is not called.
- `test_run_once_runs_price_refresh_when_env_set`: with `RUN_ONCE=1` and
  `RUN_PRICE_REFRESH=1`, patch `_check_site` and `_refresh_site_prices` with
  `AsyncMock`. `_refresh_site_prices` is awaited once per active site, after
  the checks.
- `test_run_once_skips_price_refresh_by_default`: with `RUN_ONCE=1` only,
  `_refresh_site_prices` is not awaited.

`tests/test_config.py`:

- `test_manyvids_price_refresh_defaults`: `ManyVidsScrapingConfig()` has
  `enabled=True`, 72.0, 21600 and 600.
- `test_manyvids_price_refresh_interval_must_be_positive`:
  `price_refresh_interval_hours=0` raises `ValidationError`.
- If a test loads `config/config.example.yaml`, it must still validate. Add
  `test_example_config_validates` loading that file through `load_config` if
  no such test exists.

### 4.7 Part C docs

`docs/DESIGN.md`:

- Goals: add "Track ManyVids per-video prices over time (price history, list
  and sale price)". Non-Goals: add "Price-drop notifications". Keep "Web UI"
  as is, but add "(a separate read-only consumer, `storm-monitor-explorer`,
  reads `data/db/`)".
- `BaseSite` code block: add `tracks_prices`, `price_scope()` and
  `refresh_prices()`.
- Domain types: `Item` with the new fields. `ManyVidsScrapingConfig` with all
  current fields, including `creator_interval_hours`, `creator_jitter_seconds`
  and the four `price_refresh_*` fields.
- New section **"## Price tracking (ManyVids)"** containing:
  - Scope: `pricing_model = 'per_video'` rows only. Subscription rows never get
    observations and are never "on sale".
  - The list/sale mapping table from §2.1, with the raw Price record shape from
    §0.2 item 6.
  - Column semantics:
    - `price` (TEXT, regular price at first sight, unchanged legacy column)
    - `first_price_cents` (effective price at first sight for rows inserted
      after v2; backfilled rows hold the regular price parsed from `price`,
      because the pre-v2 parser read only `regular`)
    - `current_price_cents` / `list_price_cents` (latest successful
      observation)
    - `price_checked_at` (heartbeat: last successful full scan that saw the
      video, or insert time)
  - Change-only recording: `price_observations` gets a row only when `(price,
    list price)` differs from the item's latest observation. A new item
    always gets its first observation. No observations are synthesized for
    pre-v2 rows; their history starts at the first full re-scan.
  - Re-scan workflow and pacing (§4.5 text). Partial-scan rules: a 404 or empty
    page before the last expected page, or a unique-count mismatch with page
    1's `totalCount`, records nothing and stores `last_error` in
    `price_refresh_state`. Videos seen in a re-scan with no `seen_items` row
    are ignored, because only the normal check ingests and notifies.
  - **Sale state rules** (derived by consumers, evaluated in order):
    1. `pricing_model <> 'per_video'` or `current_price_cents IS NULL`: N/A.
    2. `list_price_cents > current_price_cents`: `list_price_sale`, discount =
       `(list - current) / list`.
    3. `current_price_cents < max(price_cents)` over observations in the last 90
       days: `price_drop`, discount relative to that max.
    4. `current_price_cents < first_price_cents`: `below_first_seen`, discount
       relative to the first price.
    5. Otherwise: `not_on_sale`.
  - Reference SQL:

    ```sql
    SELECT s.site_name, s.video_id, s.title,
           s.first_price_cents, s.current_price_cents, s.list_price_cents,
           m.max_90d_cents,
           CASE
               WHEN s.pricing_model <> 'per_video' OR s.current_price_cents IS NULL THEN NULL
               WHEN s.list_price_cents > s.current_price_cents THEN 'list_price_sale'
               WHEN s.current_price_cents < m.max_90d_cents THEN 'price_drop'
               WHEN s.current_price_cents < s.first_price_cents THEN 'below_first_seen'
               ELSE 'not_on_sale'
           END AS sale_state
    FROM seen_items AS s
    LEFT JOIN (
        SELECT site_name, video_id, MAX(price_cents) AS max_90d_cents
        FROM price_observations
        WHERE observed_at >= datetime('now', '-90 days')
        GROUP BY site_name, video_id
    ) AS m ON m.site_name = s.site_name AND m.video_id = s.video_id;
    ```

  - Migration verification queries (§6).
- Workflow: add a "Price refresh cycle" subsection (global lock, then site
  lock, full scan, `record_price_scan` or `record_price_scan_failure`, creator
  delay). Scheduling: describe the `price-refresh-<site>` jobs and the persisted
  start time.
- Env table: add `RUN_PRICE_REFRESH` ("With `RUN_ONCE=1`, also run a full price
  re-scan for every ManyVids creator after the checks").
- Unit-test key cases lists for `test_db.py`, `test_manyvids.py`,
  `test_main.py`, plus `tests/test_pricing.py` in the coverage map.
- Directory structure: add `pricing.py`, `tests/test_pricing.py`,
  `tests/sites/manyvids_payload.py` and `docs/plans/`.

`README.md`:

- Supported Sites `manyvids` row: add "Re-scans each store every 72h (default)
  to record price changes."
- Environment Variables: add `RUN_PRICE_REFRESH` and (from Part A)
  `MANYVIDS_DEBUG_DUMP_DIR`.
- New section **"## ManyVids price tracking"**: what is recorded, the four
  config keys with defaults, re-scan pacing, the sale-state rules (brief, with
  a link to `docs/DESIGN.md#price-tracking-manyvids`), and the "before
  upgrading" note: stop the service, back up `data/db/monitor.db` with
  `sqlite3 data/db/monitor.db ".backup '/some/other/dir/monitor.db.pre-v2'"`
  (outside `data/db/`), start the new image, and check the `Database migrated
  to schema v2` log line and the §6 queries.

`AGENTS.md` §5: add a `pricing.py` pointer (price parsing, ManyVids list/sale
mapping, video-ID extraction), and extend the `main.py` pointer with "price
refresh scheduling". §6 "Adding a new site": add "set `tracks_prices = True`
and implement `price_scope()`/`refresh_prices()` only for per-item-priced
sites".

`VERSION`: `0.3.0` (whether or not Part A shipped separately as `0.2.1`).

## 5. Acceptance criteria

1. `make lintfix && make lint && make test` passes, and `make test-cov` passes
   the 80% threshold.
2. All tests listed in §2.5, §3.4 and §4.6 exist and pass. No existing test is
   weakened. Tests removed or rewritten are only those named in this plan,
   because they target removed code or obsolete fixtures.
3. The parser handles the Relay `recordMap` store page as described in §0.2.
   An unparseable store page raises `StorePayloadError`, which is retried and
   logged as an error, and is dumped when `MANYVIDS_DEBUG_DUMP_DIR` is set.
4. Migration v2 is gated by `PRAGMA user_version`, atomic, idempotent on
   reopen, logs a `MigrationSummary`, merges duplicate `(site_name, video_id)`
   groups into `seen_items_merged_duplicates`, and creates the unique partial
   index.
5. Known-item detection and early stop use numeric video IDs. A second URL
   with an existing video ID is not inserted and not notified.
6. New per_video items store first/current/list cents and their first
   observation. Full re-scans record observations only on change, update the
   `price_checked_at` heartbeat, and never write anything on a failed or
   partial scan. Subscription rows never get price data.
7. Re-scan jobs are scheduled per creator with persisted next-due times,
   serialized globally, and paced by the existing page, creator and retry
   settings.
8. `README.md`, `docs/DESIGN.md`, `AGENTS.md`, `config/config.example.yaml` and
   `VERSION` are updated as specified.
9. No git write operations were performed.

## 6. Post-deploy verification (for the operator, not the implementer)

Run read-only against the live DB (for example `sqlite3 -readonly
data/db/monitor.db`) after the first start of the new image:

```sql
PRAGMA user_version;                                   -- 2
SELECT pricing_model, COUNT(*), COUNT(video_id), COUNT(price), COUNT(first_price_cents)
FROM seen_items GROUP BY pricing_model;
SELECT COUNT(*) FROM seen_items_merged_duplicates;
SELECT site_name, video_id, COUNT(*) FROM seen_items
WHERE video_id IS NOT NULL GROUP BY 1, 2 HAVING COUNT(*) > 1;   -- 0 rows
SELECT COUNT(*) FROM price_observations;
SELECT site_name, creator_id, last_success_at, last_error FROM price_refresh_state;
SELECT COUNT(*), COUNT(current_price_cents), COUNT(price_checked_at)
FROM seen_items WHERE pricing_model = 'per_video';
```

Expected from the 2026-11-26 snapshot described in task 678 (3,683 ManyVids
rows, all priced, and 7 numeric IDs stored twice; ManyVids ingestion has been
down since then, so the counts should not have moved): `seen_items_merged_duplicates`
= 7, `per_video` = 3,676 with `COUNT(video_id) = COUNT(price) =
COUNT(first_price_cents) = 3,676`, and `subscription` rows with
`COUNT(video_id) = 0`. These must match the logged `MigrationSummary`. After
every creator's first refresh has succeeded (about `600 + N *
price_refresh_stagger_seconds` seconds after startup, plus scan time),
`COUNT(current_price_cents)` should equal the number of per_video rows still
listed in a store.

Expect a one-time burst of notifications after Part A deploys: videos
published during the #682 outage, plus any distinct same-title videos that
title-based dedup used to suppress (Part B), when they fall on store page 1.
