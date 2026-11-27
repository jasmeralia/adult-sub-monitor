import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adult_sub_monitor.db import Database, MigrationSummary
from adult_sub_monitor.models import Item
from adult_sub_monitor.pricing import PriceObservation


def build_item(
    site_name: str = "test_site",
    item_id: str = "item-1",
    *,
    duration: str | None = None,
    price: str | None = None,
    video_type: str | None = None,
    creator: str | None = None,
    video_id: str | None = None,
    pricing_model: str = "subscription",
    price_cents: int | None = None,
    list_price_cents: int | None = None,
    url: str | None = None,
) -> Item:
    return Item(
        site_name=site_name,
        item_id=item_id,
        title=f"Test Item {item_id}",
        url=url or f"https://example.com/videos/{item_id}",
        thumbnail_url=f"https://example.com/thumbs/{item_id}.jpg",
        performers=["Performer One"],
        tags=["tag-one", "tag-two"],
        duration=duration,
        price=price,
        video_type=video_type,
        creator=creator,
        video_id=video_id,
        pricing_model=pricing_model,
        price_cents=price_cents,
        list_price_cents=list_price_cents,
    )


@pytest.mark.asyncio
async def test_mark_seen_new_item(db: Database) -> None:
    assert await db.mark_seen(build_item()) is True


@pytest.mark.asyncio
async def test_mark_seen_duplicate(db: Database) -> None:
    item = build_item()

    assert await db.mark_seen(item) is True
    assert await db.mark_seen(item) is False


@pytest.mark.asyncio
async def test_mark_seen_different_sites(db: Database) -> None:
    assert await db.mark_seen(build_item(site_name="site_one")) is True
    assert await db.mark_seen(build_item(site_name="site_two")) is True


@pytest.mark.asyncio
async def test_record_and_retry_failed_notification(db: Database) -> None:
    item = build_item()

    await db.mark_seen(item)
    await db.record_failed_notification(item, "webhook failed")

    assert await db.get_pending_retries() == []
    row = db.conn.execute(
        """
        SELECT site_name, item_id, attempt_count, last_error
        FROM failed_notifications
        WHERE site_name = ? AND item_id = ?
        """,
        (item.site_name, item.item_id),
    ).fetchone()
    assert row == (item.site_name, item.item_id, 1, "webhook failed")


@pytest.mark.asyncio
async def test_record_failed_notification_increments_count(db: Database) -> None:
    item = build_item()

    await db.mark_seen(item)
    await db.record_failed_notification(item, "first failure")
    await db.record_failed_notification(item, "second failure")

    row = db.conn.execute(
        """
        SELECT attempt_count, last_error
        FROM failed_notifications
        WHERE site_name = ? AND item_id = ?
        """,
        (item.site_name, item.item_id),
    ).fetchone()
    assert row == (2, "second failure")


@pytest.mark.asyncio
async def test_mark_notified_clears_failed(db: Database) -> None:
    item = build_item()

    await db.mark_seen(item)
    await db.record_failed_notification(item, "webhook failed")
    await db.mark_notified(item)

    failed_count = db.conn.execute(
        """
        SELECT COUNT(*)
        FROM failed_notifications
        WHERE site_name = ? AND item_id = ?
        """,
        (item.site_name, item.item_id),
    ).fetchone()[0]
    notified_at = db.conn.execute(
        """
        SELECT notified_at
        FROM seen_items
        WHERE site_name = ? AND item_id = ?
        """,
        (item.site_name, item.item_id),
    ).fetchone()[0]

    assert failed_count == 0
    assert notified_at is not None


def test_apply_migrations_idempotent(db: Database) -> None:
    db._apply_migrations()
    db._apply_migrations()


@pytest.mark.asyncio
async def test_pending_retries_include_item_metadata(db: Database) -> None:
    item = build_item(
        duration="12:34",
        price="5.99",
        video_type="mobile",
        creator="Creator Name",
    )

    await db.mark_seen(item)
    await db.record_failed_notification(item, "webhook failed")
    db.conn.execute(
        """
        UPDATE failed_notifications
        SET last_attempted_at = datetime('now', '-10 minutes')
        WHERE site_name = ? AND item_id = ?
        """,
        (item.site_name, item.item_id),
    )
    db.conn.commit()

    assert await db.get_pending_retries() == [item]


@pytest.mark.asyncio
async def test_get_known_video_ids_returns_ids_for_site(db: Database) -> None:
    await db.mark_seen(build_item(site_name="site_one", item_id="1", video_id="101"))
    await db.mark_seen(build_item(site_name="site_one", item_id="2", video_id="102"))
    await db.mark_seen(build_item(site_name="site_two", item_id="3", video_id="103"))
    await db.mark_seen(build_item(site_name="site_one", item_id="4"))

    assert await db.get_known_video_ids("site_one") == {"101", "102"}


def test_reopening_database_applies_migrations_idempotently(tmp_db_path: Path) -> None:
    db = Database(tmp_db_path)
    db.conn.close()

    reopened_db = Database(tmp_db_path)
    reopened_db.conn.close()


def test_database_creates_parent_directory(tmp_path: Path) -> None:
    db_path = tmp_path / "db" / "monitor.db"

    db = Database(db_path)

    assert db_path.is_file()
    db.conn.close()


def test_existing_seen_items_table_without_metadata_columns_is_upgraded(
    tmp_db_path: Path,
) -> None:
    conn = sqlite3.connect(tmp_db_path)
    conn.executescript(
        """
        CREATE TABLE schema_version (
            version INTEGER PRIMARY KEY
        );

        CREATE TABLE seen_items (
            site_name TEXT NOT NULL,
            item_id TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            thumbnail_url TEXT,
            performers TEXT,
            tags TEXT,
            first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notified_at TIMESTAMP,
            PRIMARY KEY (site_name, item_id)
        );
        """
    )
    conn.close()

    db = Database(tmp_db_path)
    columns = {
        row[1] for row in db.conn.execute("PRAGMA table_info(seen_items)").fetchall()
    }
    schema_versions = {
        row[0] for row in db.conn.execute("SELECT version FROM schema_version")
    }
    db.conn.close()

    assert {"duration", "price", "video_type", "creator"} <= columns
    assert {1, 2} <= schema_versions
    assert "video_id" in columns


def _create_v1_database(path: Path, rows: list[tuple]) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
        INSERT INTO schema_version (version) VALUES (1);
        CREATE TABLE seen_items (
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
            first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            notified_at TIMESTAMP,
            PRIMARY KEY (site_name, item_id)
        );
        CREATE TABLE failed_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_name TEXT NOT NULL,
            item_id TEXT NOT NULL,
            attempt_count INTEGER NOT NULL DEFAULT 1,
            last_error TEXT,
            last_attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (site_name, item_id) REFERENCES seen_items(site_name, item_id)
        );
        """
    )
    conn.executemany(
        """INSERT INTO seen_items (
            site_name, item_id, title, url, price, first_seen_at, notified_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    conn.close()


def test_fresh_database_is_schema_v2(db: Database) -> None:
    assert db.conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert {
        row[0] for row in db.conn.execute("SELECT version FROM schema_version")
    } == {1, 2}
    columns = {row[1] for row in db.conn.execute("PRAGMA table_info(seen_items)")}
    assert {
        "video_id",
        "pricing_model",
        "first_price_cents",
        "current_price_cents",
        "list_price_cents",
        "price_checked_at",
    } <= columns
    objects = {row[0] for row in db.conn.execute("SELECT name FROM sqlite_master")}
    assert {
        "price_observations",
        "price_refresh_state",
        "seen_items_merged_duplicates",
    } <= objects
    assert {"idx_price_obs_item_time", "idx_seen_items_site_video"} <= objects
    assert db.last_migration_summary == MigrationSummary(0, 0, 0, 0, 0, 0, 0, 0)


def test_migration_v2_backfills_legacy_rows(tmp_path: Path) -> None:
    path = tmp_path / "legacy.db"
    rows = [
        (
            "ManyVids",
            "a",
            "a",
            "https://www.manyvids.com/Video/101/a",
            "5.99",
            "2026-01-01",
            None,
        ),
        (
            "ManyVids",
            "b",
            "b",
            "https://www.manyvids.com/Video/102/b",
            "24.99",
            "2026-01-02",
            None,
        ),
        (
            "ManyVids",
            "c",
            "c",
            "https://www.manyvids.com/Video/103/c",
            "abc",
            "2026-01-03",
            None,
        ),
        (
            "ManyVids",
            "d",
            "d",
            "https://www.manyvids.com/Video/104/d",
            None,
            "2026-01-04",
            None,
        ),
        (
            "Venus",
            "e",
            "e",
            "https://venus.example/Video/105/e",
            "1.00",
            "2026-01-05",
            None,
        ),
        (
            "Venus",
            "f",
            "f",
            "https://venus.example/Video/106/f",
            "2.00",
            "2026-01-06",
            None,
        ),
    ]
    _create_v1_database(path, rows)
    db = Database(path)
    migrated = db.conn.execute(
        "SELECT url, price, pricing_model, video_id, first_price_cents, "
        "current_price_cents, list_price_cents, price_checked_at "
        "FROM seen_items ORDER BY item_id"
    ).fetchall()
    assert [row[2:5] for row in migrated] == [
        ("per_video", "101", 599),
        ("per_video", "102", 2499),
        ("per_video", "103", None),
        ("per_video", "104", None),
        ("subscription", None, None),
        ("subscription", None, None),
    ]
    assert all(row[1] == rows[index][4] for index, row in enumerate(migrated))
    assert all(row[5:] == (None, None, None) for row in migrated)
    assert db.last_migration_summary == MigrationSummary(4, 2, 4, 0, 2, 1, 0, 0)
    db.conn.close()


def test_migration_v2_merges_duplicate_video_ids(tmp_path: Path) -> None:
    path = tmp_path / "duplicates.db"
    first_url = "https://www.manyvids.com/Video/7774614/blake-blossom"
    second_url = "https://www.manyvids.com/Video/7774614/blake-blosson"
    _create_v1_database(
        path,
        [
            (
                "ManyVids",
                first_url,
                "first",
                first_url,
                "5.99",
                "2026-06-01 10:00:00",
                None,
            ),
            (
                "ManyVids",
                second_url,
                "second",
                second_url,
                "6.99",
                "2026-06-02 10:00:00",
                "2026-06-02 10:00:00",
            ),
        ],
    )
    conn = sqlite3.connect(path)
    conn.execute(
        "INSERT INTO failed_notifications (site_name, item_id, last_error) "
        "VALUES (?, ?, ?)",
        ("ManyVids", second_url, "failed"),
    )
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.conn.execute(
        "SELECT item_id, notified_at FROM seen_items"
    ).fetchall() == [(first_url, "2026-06-02 10:00:00")]
    assert db.conn.execute(
        "SELECT item_id, merged_into_item_id FROM seen_items_merged_duplicates"
    ).fetchall() == [(second_url, first_url)]
    assert (
        db.conn.execute("SELECT COUNT(*) FROM failed_notifications").fetchone()[0] == 0
    )
    assert db.last_migration_summary.duplicate_groups == 1
    assert db.last_migration_summary.duplicate_rows_archived == 1
    db.conn.close()


def test_migration_v2_is_idempotent_on_reopen(tmp_path: Path) -> None:
    path = tmp_path / "idempotent.db"
    first_url = "https://www.manyvids.com/Video/7774614/blake-blossom"
    second_url = "https://www.manyvids.com/Video/7774614/blake-blosson"
    venus_url = "https://venus.sensual.love/members/content/1"
    _create_v1_database(
        path,
        [
            ("ManyVids", first_url, "a", first_url, "5.99", "2026-06-01", None),
            ("ManyVids", second_url, "b", second_url, "6.99", "2026-06-02", None),
            ("sensual.love", venus_url, "c", venus_url, None, "2026-06-03", None),
        ],
    )
    first = Database(path)
    assert first.last_migration_summary is not None
    assert first.last_migration_summary.duplicate_rows_archived == 1
    before = {
        name: first.conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for name in ("seen_items", "price_observations", "seen_items_merged_duplicates")
    }
    assert before == {
        "seen_items": 2,
        "price_observations": 0,
        "seen_items_merged_duplicates": 1,
    }
    first_columns = first.conn.execute(
        "SELECT item_id, video_id, pricing_model, first_price_cents "
        "FROM seen_items ORDER BY item_id"
    ).fetchall()
    first.conn.close()
    reopened = Database(path)
    assert reopened.last_migration_summary is None
    reopened._apply_migrations()
    reopened._apply_migrations()
    after = {
        name: reopened.conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
        for name in before
    }
    assert after == before
    assert (
        reopened.conn.execute(
            "SELECT item_id, video_id, pricing_model, first_price_cents "
            "FROM seen_items ORDER BY item_id"
        ).fetchall()
        == first_columns
    )
    assert reopened.conn.execute("PRAGMA user_version").fetchone()[0] == 2
    reopened.conn.close()


def test_migration_v2_rolls_back_on_failure(tmp_path: Path) -> None:
    from unittest.mock import patch

    path = tmp_path / "rollback.db"
    _create_v1_database(path, [])
    with (
        patch.object(
            Database, "_merge_duplicate_video_ids", side_effect=RuntimeError("boom")
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        Database(path)
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "video_id" not in {
        row[1] for row in conn.execute("PRAGMA table_info(seen_items)")
    }
    conn.close()
    migrated = Database(path)
    assert migrated.conn.execute("PRAGMA user_version").fetchone()[0] == 2
    migrated.conn.close()


@pytest.mark.asyncio
async def test_mark_seen_rejects_duplicate_video_id_under_new_slug(
    db: Database,
) -> None:
    one = build_item(
        site_name="ManyVids",
        item_id="/a",
        video_id="7774614",
        pricing_model="per_video",
        url="https://www.manyvids.com/Video/7774614/a",
    )
    two = build_item(
        site_name="ManyVids",
        item_id="/b",
        video_id="7774614",
        pricing_model="per_video",
        url="https://www.manyvids.com/Video/7774614/b",
    )
    other_site = build_item(
        site_name="Other",
        item_id="/c",
        video_id="7774614",
        pricing_model="per_video",
        url="https://www.manyvids.com/Video/7774614/c",
    )
    assert await db.mark_seen(one) is True
    assert await db.mark_seen(two) is False
    assert await db.mark_seen(other_site) is True
    assert (
        db.conn.execute(
            "SELECT COUNT(*) FROM seen_items WHERE video_id = '7774614'"
        ).fetchone()[0]
        == 2
    )


@pytest.mark.asyncio
async def test_mark_seen_per_video_item_records_first_observation(db: Database) -> None:
    item = build_item(
        site_name="ManyVids",
        video_id="101",
        pricing_model="per_video",
        price_cents=2124,
        list_price_cents=2499,
    )
    assert await db.mark_seen(item) is True
    row = db.conn.execute(
        "SELECT first_price_cents, current_price_cents, list_price_cents, "
        "price_checked_at "
        "FROM seen_items WHERE site_name = ? AND video_id = ?",
        ("ManyVids", "101"),
    ).fetchone()
    assert row[:3] == (2124, 2124, 2499)
    assert row[3] is not None
    observations = db.conn.execute(
        "SELECT site_name, video_id, price_cents, list_price_cents, currency "
        "FROM price_observations"
    ).fetchall()
    assert observations == [("ManyVids", "101", 2124, 2499, "USD")]
    await db.mark_seen(item)
    assert db.conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_mark_seen_subscription_item_writes_no_price_data(db: Database) -> None:
    item = build_item(price_cents=999)
    await db.mark_seen(item)
    row = db.conn.execute(
        "SELECT first_price_cents, current_price_cents, list_price_cents, "
        "price_checked_at "
        "FROM seen_items WHERE site_name = ? AND item_id = ?",
        (item.site_name, item.item_id),
    ).fetchone()
    assert row == (None, None, None, None)
    assert db.conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_mark_seen_per_video_without_price(db: Database) -> None:
    item = build_item(video_id="101", pricing_model="per_video")
    await db.mark_seen(item)
    row = db.conn.execute(
        "SELECT first_price_cents, current_price_cents, list_price_cents, "
        "price_checked_at "
        "FROM seen_items WHERE video_id = '101'"
    ).fetchone()
    assert row == (None, None, None, None)
    assert db.conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_record_price_scan_is_change_only(db: Database) -> None:
    item = build_item(
        site_name="ManyVids",
        video_id="101",
        pricing_model="per_video",
        price_cents=1199,
        list_price_cents=1199,
    )
    await db.mark_seen(item)
    db.conn.execute("UPDATE seen_items SET price_checked_at = '2000-01-01 00:00:00'")
    db.conn.commit()
    summary = await db.record_price_scan(
        "ManyVids", "creator", [PriceObservation("101", 1199, 1199)]
    )
    assert (summary.changed, summary.matched) == (0, 1)
    assert db.conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 1
    assert (
        db.conn.execute("SELECT price_checked_at FROM seen_items").fetchone()[0]
        != "2000-01-01 00:00:00"
    )
    summary = await db.record_price_scan(
        "ManyVids", "creator", [PriceObservation("101", 999, 1199)]
    )
    assert summary.changed == 1
    assert db.conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 2
    assert (
        db.conn.execute("SELECT current_price_cents FROM seen_items").fetchone()[0]
        == 999
    )
    summary = await db.record_price_scan(
        "ManyVids", "creator", [PriceObservation("101", 999, 1099)]
    )
    assert summary.changed == 1
    assert db.conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 3
    summary = await db.record_price_scan(
        "ManyVids", "creator", [PriceObservation("101", 999, 1099)]
    )
    assert summary.changed == 0


@pytest.mark.asyncio
async def test_record_price_scan_first_observation_when_none_exists(
    db: Database,
) -> None:
    await db.mark_seen(
        build_item(site_name="ManyVids", video_id="101", pricing_model="per_video")
    )
    summary = await db.record_price_scan(
        "ManyVids", "creator", [PriceObservation("101", 999, 1299)]
    )
    assert summary.changed == 1
    assert db.conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_record_price_scan_ignores_unknown_and_subscription_rows(
    db: Database,
) -> None:
    await db.mark_seen(build_item(item_id="sub", video_id="202"))
    summary = await db.record_price_scan(
        "test_site",
        "creator",
        [PriceObservation("missing", 999, 999), PriceObservation("202", 999, 999)],
    )
    assert (summary.unmatched, summary.matched) == (2, 0)
    assert db.conn.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_record_price_scan_keys_on_numeric_video_id(db: Database) -> None:
    url = "https://www.manyvids.com/Video/101/old-slug"
    await db.mark_seen(
        build_item(
            site_name="ManyVids",
            item_id=url,
            video_id="101",
            pricing_model="per_video",
            url=url,
        )
    )
    summary = await db.record_price_scan(
        "ManyVids", "creator", [PriceObservation("101", 799, 999)]
    )
    assert summary.matched == 1
    assert (
        db.conn.execute(
            "SELECT current_price_cents FROM seen_items WHERE item_id = ?", (url,)
        ).fetchone()[0]
        == 799
    )


@pytest.mark.asyncio
async def test_record_price_scan_updates_refresh_state(db: Database) -> None:
    await db.record_price_scan("ManyVids", "creator", [])
    last = await db.get_price_refresh_last_success("ManyVids", "creator")
    assert last is not None
    assert last.tzinfo is UTC
    assert abs((datetime.now(UTC) - last).total_seconds()) < 60
    assert (
        db.conn.execute("SELECT last_error FROM price_refresh_state").fetchone()[0]
        is None
    )


@pytest.mark.asyncio
async def test_record_price_scan_failure_keeps_last_success(db: Database) -> None:
    await db.record_price_scan("ManyVids", "creator", [])
    last = await db.get_price_refresh_last_success("ManyVids", "creator")
    await db.record_price_scan_failure("ManyVids", "creator", "scan failed")
    assert await db.get_price_refresh_last_success("ManyVids", "creator") == last
    row = db.conn.execute(
        "SELECT last_attempt_at, last_error FROM price_refresh_state"
    ).fetchone()
    assert row[0] is not None
    assert row[1] == "scan failed"
    await db.record_price_scan_failure("Other", "creator", "first failure")
    assert await db.get_price_refresh_last_success("Other", "creator") is None


@pytest.mark.asyncio
async def test_record_price_scan_rolls_back_on_error(db: Database) -> None:
    original = db.conn

    class FailingConnection:
        def execute(self, sql, *args):
            if sql.lstrip().startswith("UPDATE seen_items"):
                raise sqlite3.OperationalError("forced update failure")
            return original.execute(sql, *args)

        def commit(self):
            return original.commit()

        def rollback(self):
            return original.rollback()

    await db.mark_seen(
        build_item(
            site_name="ManyVids",
            video_id="101",
            pricing_model="per_video",
            price_cents=1000,
            list_price_cents=1000,
        )
    )
    original.execute("DELETE FROM price_observations")
    original.commit()
    db.conn = FailingConnection()
    with pytest.raises(sqlite3.OperationalError):
        await db.record_price_scan(
            "ManyVids", "creator", [PriceObservation("101", 900, 1000)]
        )
    db.conn = original
    assert (
        original.execute("SELECT COUNT(*) FROM price_observations").fetchone()[0] == 0
    )
    assert (
        original.execute("SELECT COUNT(*) FROM price_refresh_state").fetchone()[0] == 0
    )


@pytest.mark.asyncio
async def test_pending_retries_include_price_fields(db: Database) -> None:
    item = build_item(
        site_name="ManyVids",
        item_id="retry",
        video_id="101",
        pricing_model="per_video",
        price_cents=599,
        list_price_cents=999,
    )
    await db.mark_seen(item)
    await db.record_failed_notification(item, "failed")
    db.conn.execute(
        "UPDATE failed_notifications SET last_attempted_at = "
        "datetime('now', '-10 minutes')"
    )
    db.conn.commit()
    retry = (await db.get_pending_retries())[0]
    assert retry.video_id == "101"
    assert retry.pricing_model == "per_video"
    assert retry.price_cents == 599
    assert retry.list_price_cents == 999
