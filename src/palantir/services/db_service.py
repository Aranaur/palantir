from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    unique_key TEXT PRIMARY KEY,
    source_id  TEXT NOT NULL,
    post_id    TEXT NOT NULL,
    score      INTEGER,
    sent       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS feedback (
    unique_key TEXT NOT NULL,
    reaction   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (unique_key, reaction)
);
"""


class DBService:
    """Async SQLite wrapper for post deduplication and state tracking."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._db_path)
        await self._conn.executescript(_SCHEMA)
        logger.info("DB connected: %s", self._db_path)

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("DB connection closed")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("DBService is not connected. Call connect() first.")
        return self._conn

    async def is_seen(self, unique_key: str) -> bool:
        cursor = await self.conn.execute(
            "SELECT 1 FROM posts WHERE unique_key = ?", (unique_key,)
        )
        return await cursor.fetchone() is not None

    async def mark_seen(self, unique_key: str, source_id: str, post_id: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO posts (unique_key, source_id, post_id) VALUES (?, ?, ?)",
            (unique_key, source_id, post_id),
        )
        await self.conn.commit()

    async def mark_sent(self, unique_key: str, score: int) -> None:
        await self.conn.execute(
            "UPDATE posts SET sent = 1, score = ? WHERE unique_key = ?",
            (score, unique_key),
        )
        await self.conn.commit()
        logger.info("Post marked as sent: %s (score=%d)", unique_key, score)

    async def save_feedback(self, unique_key: str, reaction: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO feedback (unique_key, reaction) VALUES (?, ?)",
            (unique_key, reaction),
        )
        await self.conn.commit()
        logger.info("Feedback saved: %s → %s", unique_key, reaction)
