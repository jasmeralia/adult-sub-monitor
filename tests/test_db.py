import pytest

from adult_sub_monitor.db import Database
from adult_sub_monitor.models import Item


def build_item(site_name: str = "test_site", item_id: str = "item-1") -> Item:
    return Item(
        site_name=site_name,
        item_id=item_id,
        title=f"Test Item {item_id}",
        url=f"https://example.com/videos/{item_id}",
        thumbnail_url=f"https://example.com/thumbs/{item_id}.jpg",
        performers=["Performer One"],
        tags=["tag-one", "tag-two"],
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
