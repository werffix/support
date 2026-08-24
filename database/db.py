import asyncio
import logging
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id           INTEGER NOT NULL,
    thread_id         INTEGER,
    status            TEXT    NOT NULL DEFAULT 'open',
    created_at        TEXT    NOT NULL,
    closed_at         TEXT,
    last_user_msg_at  TEXT,
    last_admin_msg_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_user_id   ON tickets (user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_thread_id ON tickets (thread_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status    ON tickets (status);

CREATE TABLE IF NOT EXISTS mutes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    admin_id    INTEGER,
    reason      TEXT    NOT NULL DEFAULT '',
    until_at    TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mutes_user_id ON mutes (user_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Обёртка над aiosqlite. Один коннектор на процесс, WAL-режим, индекс на все ходовые поля."""

    def __init__(self) -> None:
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def init(self, path: str) -> None:
        self._conn = await aiosqlite.connect(path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=NORMAL")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.executescript(_SCHEMA)
        await self._migrate()
        await self._conn.commit()
        logger.info("Database initialized at %s", path)

    async def _migrate(self) -> None:
        """Мягкие миграции: добавляет недостающие колонки в старые базы."""
        cursor = await self._conn.execute("PRAGMA table_info(tickets)")
        columns = {row[1] for row in await cursor.fetchall()}
        migrations = {
            "last_user_msg_at": "ALTER TABLE tickets ADD COLUMN last_user_msg_at TEXT",
            "last_admin_msg_at": "ALTER TABLE tickets ADD COLUMN last_admin_msg_at TEXT",
        }
        for column, statement in migrations.items():
            if column not in columns:
                await self._conn.execute(statement)
                logger.info("Migration applied: added column %s", column)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def create_ticket(self, user_id: int, thread_id: int | None = None) -> int:
        async with self._lock:
            now = _now()
            cursor = await self._conn.execute(
                "INSERT INTO tickets (user_id, thread_id, status, created_at, last_user_msg_at) "
                "VALUES (?, ?, 'open', ?, ?)",
                (user_id, thread_id, now, now),
            )
            await self._conn.commit()
            return int(cursor.lastrowid)

    async def set_thread(self, ticket_id: int, thread_id: int) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE tickets SET thread_id = ? WHERE id = ?",
                (thread_id, ticket_id),
            )
            await self._conn.commit()

    async def delete_ticket(self, ticket_id: int) -> None:
        async with self._lock:
            await self._conn.execute("DELETE FROM tickets WHERE id = ?", (ticket_id,))
            await self._conn.commit()

    async def get_open_ticket(self, user_id: int) -> dict | None:
        async with self._lock:
            cursor = await self._conn.execute(
                "SELECT * FROM tickets WHERE user_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_ticket_by_thread(self, thread_id: int) -> dict | None:
        async with self._lock:
            cursor = await self._conn.execute(
                "SELECT * FROM tickets WHERE thread_id = ? ORDER BY id DESC LIMIT 1",
                (thread_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_ticket(self, ticket_id: int) -> dict | None:
        async with self._lock:
            cursor = await self._conn.execute(
                "SELECT * FROM tickets WHERE id = ?",
                (ticket_id,),
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def touch_user_activity(self, ticket_id: int) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE tickets SET last_user_msg_at = ? WHERE id = ?",
                (_now(), ticket_id),
            )
            await self._conn.commit()

    async def touch_admin_activity(self, ticket_id: int) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE tickets SET last_admin_msg_at = ? WHERE id = ?",
                (_now(), ticket_id),
            )
            await self._conn.commit()

    async def get_stale_tickets(self, hours: int) -> list[dict]:
        """Открытые тикеты, где последнее сообщение — от администратора и прошло больше `hours` часов."""
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=hours)
        ).isoformat(timespec="seconds")
        async with self._lock:
            cursor = await self._conn.execute(
                "SELECT * FROM tickets "
                "WHERE status = 'open' "
                "AND last_admin_msg_at IS NOT NULL "
                "AND last_admin_msg_at <= ? "
                "AND (last_user_msg_at IS NULL OR last_admin_msg_at > last_user_msg_at)",
                (cutoff,),
            )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def close_ticket(self, ticket_id: int) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE tickets SET status = 'closed', closed_at = ? WHERE id = ?",
                (_now(), ticket_id),
            )
            await self._conn.commit()

    async def reopen_ticket(self, ticket_id: int) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE tickets SET status = 'open', closed_at = NULL WHERE id = ?",
                (ticket_id,),
            )
            await self._conn.commit()

    async def mute_user(self, user_id: int, admin_id: int, until_at: str, reason: str = "") -> None:
        async with self._lock:
            await self._conn.execute("DELETE FROM mutes WHERE user_id = ?", (user_id,))
            await self._conn.execute(
                "INSERT INTO mutes (user_id, admin_id, reason, until_at, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, admin_id, reason, until_at, _now()),
            )
            await self._conn.commit()

    async def unmute_user(self, user_id: int) -> None:
        async with self._lock:
            await self._conn.execute("DELETE FROM mutes WHERE user_id = ?", (user_id,))
            await self._conn.commit()

    async def is_muted(self, user_id: int) -> dict | None:
        async with self._lock:
            cursor = await self._conn.execute(
                "SELECT * FROM mutes WHERE user_id = ? ORDER BY id DESC LIMIT 1",
                (user_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            mute = dict(row)
            until = datetime.fromisoformat(mute["until_at"])
            if until <= datetime.now(timezone.utc):
                return None
            return mute

    async def get_stats(self) -> dict:
        async with self._lock:
            total = await self._fetch_scalar("SELECT COUNT(*) FROM tickets")
            open_count = await self._fetch_scalar(
                "SELECT COUNT(*) FROM tickets WHERE status = 'open'"
            )
            today = await self._fetch_scalar(
                "SELECT COUNT(*) FROM tickets WHERE created_at LIKE ?",
                (f"{_now()[:10]}%",),
            )
            users = await self._fetch_scalar(
                "SELECT COUNT(DISTINCT user_id) FROM tickets"
            )
        return {
            "total": total,
            "open": open_count,
            "closed": total - open_count,
            "today": today,
            "users": users,
        }

    async def _fetch_scalar(self, query: str, params: tuple = ()) -> int:
        cursor = await self._conn.execute(query, params)
        row = await cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else 0


db = Database()
