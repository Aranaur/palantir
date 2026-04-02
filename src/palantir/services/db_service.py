from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    unique_key     TEXT PRIMARY KEY,
    source_id      TEXT NOT NULL,
    post_id        TEXT NOT NULL,
    short_key      TEXT,
    score          INTEGER,
    sent           INTEGER NOT NULL DEFAULT 0,
    rewritten_text TEXT,
    url            TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_posts_short_key ON posts (short_key);
CREATE TABLE IF NOT EXISTS feedback (
    unique_key TEXT NOT NULL,
    reaction   TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (unique_key, reaction)
);
CREATE TABLE IF NOT EXISTS published (
    unique_key   TEXT PRIMARY KEY,
    post_text    TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS user_ratings (
    unique_key TEXT PRIMARY KEY,
    priority   INTEGER NOT NULL CHECK (priority IN (1, 2, 3)),
    rated_at   TEXT NOT NULL DEFAULT (datetime('now'))
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
        await self._migrate()
        logger.info("DB connected: %s", self._db_path)

    async def _migrate(self) -> None:
        """Add columns that may be missing in older databases."""
        cursor = await self.conn.execute("PRAGMA table_info(posts)")
        columns = {row[1] for row in await cursor.fetchall()}
        for col, col_type in [("rewritten_text", "TEXT"), ("url", "TEXT")]:
            if col not in columns:
                await self.conn.execute(f"ALTER TABLE posts ADD COLUMN {col} {col_type}")
                logger.info("Migrated: added column posts.%s", col)
        await self.conn.commit()

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

    @staticmethod
    def make_short_key(unique_key: str) -> str:
        """16-char hex hash of unique_key — safe for Telegram callback_data."""
        return hashlib.sha256(unique_key.encode()).hexdigest()[:16]

    async def mark_seen(self, unique_key: str, source_id: str, post_id: str) -> None:
        short_key = self.make_short_key(unique_key)
        await self.conn.execute(
            "INSERT OR IGNORE INTO posts (unique_key, source_id, post_id, short_key) VALUES (?, ?, ?, ?)",
            (unique_key, source_id, post_id, short_key),
        )
        await self.conn.commit()

    async def unique_key_by_short(self, short_key: str) -> str | None:
        cursor = await self.conn.execute(
            "SELECT unique_key FROM posts WHERE short_key = ?", (short_key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def mark_sent(
        self,
        unique_key: str,
        score: int,
        rewritten_text: str = "",
        url: str = "",
    ) -> None:
        await self.conn.execute(
            "UPDATE posts SET sent = 1, score = ?, rewritten_text = ?, url = ? "
            "WHERE unique_key = ?",
            (score, rewritten_text, url, unique_key),
        )
        await self.conn.commit()
        logger.info("Post marked as sent: %s (score=%d)", unique_key, score)

    async def weekly_stats(self, days: int = 7) -> dict:
        """Gather stats for the last N days."""
        c = self.conn

        # Total posts seen
        row = await (await c.execute(
            "SELECT COUNT(*) FROM posts WHERE created_at >= datetime('now', ?)",
            (f"-{days} days",),
        )).fetchone()
        total_seen = row[0] if row else 0

        # Total sent (recommended)
        row = await (await c.execute(
            "SELECT COUNT(*) FROM posts WHERE sent = 1 AND created_at >= datetime('now', ?)",
            (f"-{days} days",),
        )).fetchone()
        total_sent = row[0] if row else 0

        # Score distribution
        rows = await (await c.execute(
            "SELECT score, COUNT(*) FROM posts "
            "WHERE score IS NOT NULL AND created_at >= datetime('now', ?) "
            "GROUP BY score ORDER BY score DESC",
            (f"-{days} days",),
        )).fetchall()
        score_dist = {r[0]: r[1] for r in rows}

        # Top sources by sent count
        rows = await (await c.execute(
            "SELECT source_id, COUNT(*) as cnt FROM posts "
            "WHERE sent = 1 AND created_at >= datetime('now', ?) "
            "GROUP BY source_id ORDER BY cnt DESC LIMIT 5",
            (f"-{days} days",),
        )).fetchall()
        top_sources = [(r[0], r[1]) for r in rows]

        # Feedback counts
        rows = await (await c.execute(
            "SELECT reaction, COUNT(*) FROM feedback "
            "WHERE created_at >= datetime('now', ?) "
            "GROUP BY reaction",
            (f"-{days} days",),
        )).fetchall()
        feedback = {r[0]: r[1] for r in rows}

        return {
            "days": days,
            "total_seen": total_seen,
            "total_sent": total_sent,
            "score_dist": score_dist,
            "top_sources": top_sources,
            "feedback": feedback,
        }

    async def get_unpublished_saved(self) -> list[dict]:
        """Get posts queued for publication, sorted by user priority then AI score.

        Includes:
        - Posts with user_priority set via digest buttons (high/medium/low)
        - Legacy posts with reaction='save' and no priority (sorted last)
        Excludes posts already published.
        """
        rows = await (await self.conn.execute(
            "SELECT p.unique_key, p.source_id, p.score, ur.priority "
            "FROM posts p "
            "LEFT JOIN user_ratings ur ON p.unique_key = ur.unique_key "
            "WHERE p.unique_key NOT IN (SELECT unique_key FROM published) "
            "  AND ("
            "    ur.priority IS NOT NULL "
            "    OR (p.unique_key IN (SELECT unique_key FROM feedback WHERE reaction = 'save')"
            "        AND ur.priority IS NULL)"
            "  ) "
            "ORDER BY COALESCE(ur.priority, 0) DESC, p.score DESC, p.created_at DESC",
        )).fetchall()
        return [
            {"unique_key": r[0], "source_id": r[1], "score": r[2], "user_priority": r[3]}
            for r in rows
        ]

    async def save_user_priority(self, unique_key: str, priority: str) -> None:
        """Save or update user priority ('high'|'medium'|'low') for a post.

        Priority is stored as: 3=high, 2=medium, 1=low
        """
        priority_map = {"high": 3, "medium": 2, "low": 1}
        if priority not in priority_map:
            raise ValueError(f"Invalid priority: {priority}")

        await self.conn.execute(
            "INSERT OR REPLACE INTO user_ratings (unique_key, priority) VALUES (?, ?)",
            (unique_key, priority_map[priority]),
        )
        await self.conn.commit()
        logger.info("User priority saved: %s → %s", unique_key, priority)

    async def mark_published(self, unique_key: str, post_text: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO published (unique_key, post_text) VALUES (?, ?)",
            (unique_key, post_text),
        )
        await self.conn.commit()
        logger.info("Post marked as published: %s", unique_key)

    async def get_rewritten_text(self, unique_key: str) -> str | None:
        """Get the rewritten_text that was sent in the digest for this post."""
        cursor = await self.conn.execute(
            "SELECT rewritten_text FROM posts WHERE unique_key = ?", (unique_key,)
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    async def save_feedback(self, unique_key: str, reaction: str) -> None:
        await self.conn.execute(
            "INSERT OR IGNORE INTO feedback (unique_key, reaction) VALUES (?, ?)",
            (unique_key, reaction),
        )
        await self.conn.commit()
        logger.info("Feedback saved: %s → %s", unique_key, reaction)
