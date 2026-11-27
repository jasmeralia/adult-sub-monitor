from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from adult_sub_monitor.models import Item
from adult_sub_monitor.pricing import (
    PriceObservation,
    PriceScanSummary,
    is_manyvids_url,
    manyvids_video_id_from_url,
    parse_price_cents,
)

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 2


@dataclass(frozen=True)
class MigrationSummary:  # pylint: disable=too-many-instance-attributes
    per_video_rows: int
    subscription_rows: int
    video_id_backfilled: int
    video_id_unparsed: int
    first_price_backfilled: int
    first_price_unparsed: int
    duplicate_groups: int
    duplicate_rows_archived: int


class Database:  # pylint: disable=too-many-instance-attributes
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.last_migration_summary: MigrationSummary | None = None
        try:
            self._apply_migrations()
        except Exception:
            self.conn.close()
            raise

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
        self.last_migration_summary = self._migrate_to_v2()

    def _ensure_seen_item_metadata_columns(self) -> None:
        existing_columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(seen_items)")
        }
        metadata_columns = {
            "duration": "TEXT",
            "price": "TEXT",
            "video_type": "TEXT",
            "creator": "TEXT",
            "description": "TEXT",
        }

        for column_name, column_type in metadata_columns.items():
            if column_name not in existing_columns:
                self.conn.execute(
                    f"ALTER TABLE seen_items ADD COLUMN {column_name} {column_type}"
                )

        self.conn.execute("INSERT OR IGNORE INTO schema_version (version) VALUES (1)")

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
            self.conn.execute(
                "INSERT OR IGNORE INTO schema_version (version) VALUES (2)"
            )
            per_video_rows = self.conn.execute(
                "SELECT COUNT(*) FROM seen_items WHERE pricing_model = 'per_video'"
            ).fetchone()[0]
            subscription_rows = self.conn.execute(
                "SELECT COUNT(*) FROM seen_items WHERE pricing_model = 'subscription'"
            ).fetchone()[0]
            video_id_unparsed = self.conn.execute(
                "SELECT COUNT(*) FROM seen_items "
                "WHERE pricing_model = 'per_video' AND video_id IS NULL"
            ).fetchone()[0]
            summary = MigrationSummary(
                per_video_rows=per_video_rows,
                subscription_rows=subscription_rows,
                video_id_backfilled=video_id_backfilled,
                video_id_unparsed=video_id_unparsed,
                first_price_backfilled=first_price_backfilled,
                first_price_unparsed=first_price_unparsed,
                duplicate_groups=duplicate_groups,
                duplicate_rows_archived=archived,
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        logger.info("Database migrated to schema v%s: %s", SCHEMA_VERSION, summary)
        return summary

    def _add_v2_columns_and_tables(self) -> None:
        existing = {
            row[1] for row in self.conn.execute("PRAGMA table_info(seen_items)")
        }
        columns = {
            "video_id": "TEXT",
            "pricing_model": "TEXT",
            "first_price_cents": "INTEGER",
            "current_price_cents": "INTEGER",
            "list_price_cents": "INTEGER",
            "price_checked_at": "TIMESTAMP",
        }
        for name, column_type in columns.items():
            if name not in existing:
                self.conn.execute(
                    f"ALTER TABLE seen_items ADD COLUMN {name} {column_type}"
                )
        statements = (
            """CREATE TABLE IF NOT EXISTS price_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                site_name TEXT NOT NULL,
                video_id TEXT NOT NULL,
                observed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                price_cents INTEGER NOT NULL,
                list_price_cents INTEGER,
                currency TEXT NOT NULL DEFAULT 'USD'
            )""",
            "CREATE INDEX IF NOT EXISTS idx_price_obs_item_time "
            "ON price_observations (site_name, video_id, observed_at)",
            """CREATE TABLE IF NOT EXISTS price_refresh_state (
                site_name TEXT NOT NULL,
                creator_id TEXT NOT NULL,
                last_success_at TIMESTAMP,
                last_attempt_at TIMESTAMP,
                last_error TEXT,
                PRIMARY KEY (site_name, creator_id)
            )""",
            """CREATE TABLE IF NOT EXISTS seen_items_merged_duplicates (
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
            )""",
        )
        for statement in statements:
            self.conn.execute(statement)

    def _backfill_identity(self) -> int:
        rows = self.conn.execute(
            "SELECT rowid, url FROM seen_items WHERE pricing_model IS NULL "
            "OR (pricing_model = 'per_video' AND video_id IS NULL)"
        ).fetchall()
        updates = []
        backfilled = 0
        for rowid, url in rows:
            if is_manyvids_url(url):
                video_id = manyvids_video_id_from_url(url)
                pricing_model = "per_video"
                backfilled += video_id is not None
            else:
                video_id = None
                pricing_model = "subscription"
            updates.append((pricing_model, video_id, rowid))
        self.conn.executemany(
            "UPDATE seen_items SET pricing_model = ?, video_id = ? WHERE rowid = ?",
            updates,
        )
        return backfilled

    def _backfill_first_price(self) -> tuple[int, int]:
        rows = self.conn.execute(
            "SELECT rowid, price FROM seen_items WHERE pricing_model = 'per_video' "
            "AND first_price_cents IS NULL AND price IS NOT NULL AND TRIM(price) <> ''"
        ).fetchall()
        updates = []
        unparsed = 0
        for rowid, price in rows:
            cents = parse_price_cents(price)
            if cents is None:
                unparsed += 1
            else:
                updates.append((cents, rowid))
        self.conn.executemany(
            "UPDATE seen_items SET first_price_cents = ? WHERE rowid = ?", updates
        )
        return len(updates), unparsed

    def _merge_duplicate_video_ids(self) -> tuple[int, int]:
        groups = self.conn.execute(
            "SELECT site_name, video_id FROM seen_items WHERE video_id IS NOT NULL "
            "GROUP BY site_name, video_id HAVING COUNT(*) > 1"
        ).fetchall()
        archived = 0
        for site_name, video_id in groups:
            rows = self.conn.execute(
                "SELECT rowid, item_id, notified_at FROM seen_items "
                "WHERE site_name = ? AND video_id = ? "
                "ORDER BY first_seen_at ASC, rowid ASC",
                (site_name, video_id),
            ).fetchall()
            winner = rows[0]
            notified = min(
                (row[2] for row in rows[1:] if row[2] is not None), default=None
            )
            if winner[2] is None and notified is not None:
                self.conn.execute(
                    "UPDATE seen_items SET notified_at = ? WHERE rowid = ?",
                    (notified, winner[0]),
                )
            for loser in rows[1:]:
                self.conn.execute(
                    """INSERT OR IGNORE INTO seen_items_merged_duplicates (
                        site_name, item_id, title, url, thumbnail_url, performers, tags,
                        duration, price, video_type, creator, description,
                        first_seen_at,
                        notified_at, video_id, merged_into_item_id
                    ) SELECT site_name, item_id, title, url, thumbnail_url, performers,
                        tags, duration, price, video_type, creator, description,
                        first_seen_at, notified_at, video_id, ?
                    FROM seen_items WHERE rowid = ?""",
                    (winner[1], loser[0]),
                )
                self.conn.execute(
                    "DELETE FROM failed_notifications "
                    "WHERE site_name = ? AND item_id = ?",
                    (site_name, loser[1]),
                )
                self.conn.execute("DELETE FROM seen_items WHERE rowid = ?", (loser[0],))
                archived += 1
        return len(groups), archived

    async def mark_seen(self, item: Item) -> bool:
        is_priced = item.pricing_model == "per_video"
        first_price = item.price_cents if is_priced else None
        current_price = item.price_cents if is_priced else None
        list_price = item.list_price_cents if is_priced else None
        try:
            cursor = self.conn.execute(
                """INSERT OR IGNORE INTO seen_items (
                    site_name, item_id, title, url, thumbnail_url, performers, tags,
                    duration, price, video_type, creator, description, video_id,
                    pricing_model, first_price_cents, current_price_cents,
                    list_price_cents, price_checked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    CASE WHEN ? = 'per_video' AND ? IS NOT NULL
                    THEN CURRENT_TIMESTAMP END)""",
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
                    item.description,
                    item.video_id,
                    item.pricing_model,
                    first_price,
                    current_price,
                    list_price,
                    item.pricing_model,
                    item.price_cents,
                ),
            )
            if (
                cursor.rowcount == 1
                and is_priced
                and item.video_id is not None
                and item.price_cents is not None
            ):
                self.conn.execute(
                    "INSERT INTO price_observations "
                    "(site_name, video_id, price_cents, list_price_cents) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        item.site_name,
                        item.video_id,
                        item.price_cents,
                        item.list_price_cents,
                    ),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
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
                seen_items.creator,
                seen_items.description,
                seen_items.video_id,
                COALESCE(seen_items.pricing_model, 'subscription'),
                seen_items.current_price_cents,
                seen_items.list_price_cents
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
                description=row[11],
                video_id=row[12],
                pricing_model=row[13],
                price_cents=row[14],
                list_price_cents=row[15],
            )
            for row in cursor.fetchall()
        ]

    async def record_price_scan(
        self,
        site_name: str,
        creator_id: str,
        observations: Sequence[PriceObservation],
    ) -> PriceScanSummary:
        matched = 0
        changed = 0
        unmatched = 0
        try:
            for observation in observations:
                row = self.conn.execute(
                    "SELECT 1 FROM seen_items WHERE site_name = ? AND video_id = ? "
                    "AND pricing_model = 'per_video'",
                    (site_name, observation.video_id),
                ).fetchone()
                if row is None:
                    unmatched += 1
                    continue
                latest = self.conn.execute(
                    "SELECT price_cents, list_price_cents FROM price_observations "
                    "WHERE site_name = ? AND video_id = ? "
                    "ORDER BY observed_at DESC, id DESC LIMIT 1",
                    (site_name, observation.video_id),
                ).fetchone()
                if (
                    latest is None
                    or latest[0] != observation.price_cents
                    or latest[1] != observation.list_price_cents
                ):
                    self.conn.execute(
                        "INSERT INTO price_observations "
                        "(site_name, video_id, price_cents, list_price_cents) "
                        "VALUES (?, ?, ?, ?)",
                        (
                            site_name,
                            observation.video_id,
                            observation.price_cents,
                            observation.list_price_cents,
                        ),
                    )
                    changed += 1
                self.conn.execute(
                    "UPDATE seen_items SET current_price_cents = ?, "
                    "list_price_cents = ?, price_checked_at = CURRENT_TIMESTAMP "
                    "WHERE site_name = ? AND video_id = ?",
                    (
                        observation.price_cents,
                        observation.list_price_cents,
                        site_name,
                        observation.video_id,
                    ),
                )
                matched += 1
            self.conn.execute(
                """INSERT INTO price_refresh_state (
                    site_name, creator_id, last_success_at, last_attempt_at, last_error
                ) VALUES (?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
                ON CONFLICT (site_name, creator_id) DO UPDATE SET
                    last_success_at = excluded.last_success_at,
                    last_attempt_at = excluded.last_attempt_at,
                    last_error = NULL""",
                (site_name, creator_id),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return PriceScanSummary(matched=matched, changed=changed, unmatched=unmatched)

    async def record_price_scan_failure(
        self, site_name: str, creator_id: str, error: str
    ) -> None:
        self.conn.execute(
            """INSERT INTO price_refresh_state (
                site_name, creator_id, last_success_at, last_attempt_at, last_error
            ) VALUES (?, ?, NULL, CURRENT_TIMESTAMP, ?)
            ON CONFLICT (site_name, creator_id) DO UPDATE SET
                last_attempt_at = excluded.last_attempt_at,
                last_error = excluded.last_error""",
            (site_name, creator_id, error),
        )
        self.conn.commit()

    async def get_price_refresh_last_success(
        self, site_name: str, creator_id: str
    ) -> datetime | None:
        row = self.conn.execute(
            "SELECT last_success_at FROM price_refresh_state "
            "WHERE site_name = ? AND creator_id = ?",
            (site_name, creator_id),
        ).fetchone()
        if row is None or row[0] is None:
            return None
        return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)

    async def get_known_video_ids(self, site_name: str) -> set[str]:
        cursor = self.conn.execute(
            """
            SELECT video_id
            FROM seen_items
            WHERE site_name = ? AND video_id IS NOT NULL
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
