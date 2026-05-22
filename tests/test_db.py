import sqlite3
from pathlib import Path

import pytest

from adult_sub_monitor.db import Database
from adult_sub_monitor.models import Item


def build_item(
    site_name: str = "test_site",
    item_id: str = "item-1",
    *,
    duration: str | None = None,
    price: str | None = None,
    video_type: str | None = None,
    creator: str | None = None,
) -> Item:
    return Item(
        site_name=site_name,
        item_id=item_id,
        title=f"Test Item {item_id}",
        url=f"https://example.com/videos/{item_id}",
        thumbnail_url=f"https://example.com/thumbs/{item_id}.jpg",
        performers=["Performer One"],
        tags=["tag-one", "tag-two"],
        duration=duration,
        price=price,
        video_type=video_type,
        creator=creator,
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
async def test_get_known_titles_returns_titles_for_site(db: Database) -> None:
    await db.mark_seen(build_item(site_name="site_one", item_id="1"))
    await db.mark_seen(build_item(site_name="site_one", item_id="2"))
    await db.mark_seen(build_item(site_name="site_two", item_id="3"))

    assert await db.get_known_titles("site_one") == {"Test Item 1", "Test Item 2"}


def test_reopening_database_applies_migrations_idempotently(tmp_db_path: Path) -> None:
    db = Database(tmp_db_path)
    db.conn.close()

    reopened_db = Database(tmp_db_path)
    reopened_db.conn.close()


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
    assert 1 in schema_versions
