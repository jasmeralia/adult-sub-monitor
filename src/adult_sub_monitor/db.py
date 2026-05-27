from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from adult_sub_monitor.models import Item


class Database:
    def __init__(self, db_path: Path) -> None:
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._apply_migrations()

    def _apply_migrations(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS seen_items (
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
                first_seen_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                notified_at TIMESTAMP,
                PRIMARY KEY (site_name, item_id)
            );

            CREATE INDEX IF NOT EXISTS idx_seen_items_first_seen
                ON seen_items (first_seen_at DESC);

            CREATE TABLE IF NOT EXISTS failed_notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_name TEXT NOT NULL,
                item_id TEXT NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 1,
                last_error TEXT,
                last_attempted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (site_name, item_id)
                    REFERENCES seen_items(site_name, item_id)
            );

            CREATE INDEX IF NOT EXISTS idx_failed_last_attempted
                ON failed_notifications (last_attempted_at);
            """
        )
        self._ensure_seen_item_metadata_columns()
        self.conn.commit()

    def _ensure_seen_item_metadata_columns(self) -> None:
        existing_columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(seen_items)")
        }
        metadata_columns = {
            "duration": "TEXT",
            "price": "TEXT",
            "video_type": "TEXT",
            "creator": "TEXT",
        }

        for column_name, column_type in metadata_columns.items():
            if column_name not in existing_columns:
                self.conn.execute(
                    f"ALTER TABLE seen_items ADD COLUMN {column_name} {column_type}"
                )

        self.conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (1)")

    async def mark_seen(self, item: Item) -> bool:
        cursor = self.conn.execute(
            """
            INSERT OR IGNORE INTO seen_items (
                site_name,
                item_id,
                title,
                url,
                thumbnail_url,
                performers,
                tags,
                duration,
                price,
                video_type,
                creator
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.site_name,
                item.item_id,
                item.title,
                str(item.url),
                str(item.thumbnail_url) if item.thumbnail_url is not None else None,
                json.dumps(item.performers),
                json.dumps(item.tags),
                item.duration,
                item.price,
                item.video_type,
                item.creator,
            ),
        )
        self.conn.commit()
        return cursor.rowcount == 1

    async def record_failed_notification(self, item: Item, error: str) -> None:
        cursor = self.conn.execute(
            """
            UPDATE failed_notifications
            SET
                attempt_count = attempt_count + 1,
                last_error = ?,
                last_attempted_at = CURRENT_TIMESTAMP
            WHERE site_name = ? AND item_id = ?
            """,
            (error, item.site_name, item.item_id),
        )
        if cursor.rowcount == 0:
            self.conn.execute(
                """
                INSERT INTO failed_notifications (
                    site_name,
                    item_id,
                    last_error
                )
                VALUES (?, ?, ?)
                """,
                (item.site_name, item.item_id, error),
            )
        self.conn.commit()

    async def get_pending_retries(self, max_attempts: int = 10) -> list[Item]:
        cursor = self.conn.execute(
            """
            SELECT
                seen_items.site_name,
                seen_items.item_id,
                seen_items.title,
                seen_items.url,
                seen_items.thumbnail_url,
                seen_items.performers,
                seen_items.tags,
                seen_items.duration,
                seen_items.price,
                seen_items.video_type,
                seen_items.creator
            FROM failed_notifications
            JOIN seen_items
                ON seen_items.site_name = failed_notifications.site_name
                AND seen_items.item_id = failed_notifications.item_id
            WHERE
                failed_notifications.attempt_count < ?
                AND failed_notifications.last_attempted_at
                    < datetime('now', '-5 minutes')
            ORDER BY failed_notifications.last_attempted_at
            """,
            (max_attempts,),
        )
        return [
            Item(
                site_name=row[0],
                item_id=row[1],
                title=row[2],
                url=row[3],
                thumbnail_url=row[4],
                performers=json.loads(row[5] or "[]"),
                tags=json.loads(row[6] or "[]"),
                duration=row[7],
                price=row[8],
                video_type=row[9],
                creator=row[10],
            )
            for row in cursor.fetchall()
        ]

    async def get_known_titles(self, site_name: str) -> set[str]:
        cursor = self.conn.execute(
            """
            SELECT title
            FROM seen_items
            WHERE site_name = ?
            """,
            (site_name,),
        )
        return {row[0] for row in cursor.fetchall()}

    async def mark_notified(self, item: Item) -> None:
        self.conn.execute(
            """
            UPDATE seen_items
            SET notified_at = CURRENT_TIMESTAMP
            WHERE site_name = ? AND item_id = ?
            """,
            (item.site_name, item.item_id),
        )
        self.conn.execute(
            """
            DELETE FROM failed_notifications
            WHERE site_name = ? AND item_id = ?
            """,
            (item.site_name, item.item_id),
        )
        self.conn.commit()
