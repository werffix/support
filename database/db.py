import asyncio
import logging
from datetime import datetime, timezone

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    thread_id   INTEGER,
    status      TEXT    NOT NULL DEFAULT 'open',
    created_at  TEXT    NOT NULL,
    closed_at   TEXT
);

CREATE INDEX IF NOT EXISTS idx_tickets_user_id   ON tickets (user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_thread_id ON tickets (thread_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status    ON tickets (status);
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
        await self._conn.commit()
        logger.info("Database initialized at %s", path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def create_ticket(self, user_id: int, thread_id: int | None = None) -> int:
        async with self._lock:
            cursor = await self._conn.execute(
                "INSERT INTO tickets (user_id, thread_id, status, created_at) VALUES (?, ?, 'open', ?)",
                (user_id, thread_id, _now()),
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

    async def close_ticket(self, ticket_id: int) -> None:
        async with self._lock:
            await self._conn.execute(
                "UPDATE tickets SET status = 'closed', closed_at = ? WHERE id = ?",
                (_now(), ticket_id),
            )
            await self._conn.commit()


db = Database()
