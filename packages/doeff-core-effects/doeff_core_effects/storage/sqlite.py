"""
SQLite-backed durable storage implementation.

Provides persistent storage that survives process restarts.
Thread-safe via connection-per-thread pattern.
"""


import asyncio
import pickle
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from doeff_core_effects.effects import Await


class SQLiteStorage:
    """
    SQLite-backed durable storage.

    Values are serialized using pickle. Thread-safe via connection-per-thread.

    Example:
        storage = SQLiteStorage("workflow.db")
        storage.put("step1_result", {"computed": 42})

        # Later, even after restart:
        result = storage.get("step1_result")  # {"computed": 42}

    Attributes:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str | Path) -> None:
        """
        Initialize SQLite storage.

        Args:
            db_path: Path to SQLite database file. Use ":memory:" for in-memory.
        """
        self._db_path = str(db_path)
        self._local = threading.local()
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get thread-local connection."""
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self._db_path)
            # Cache writes happen one entry at a time, so prefer WAL + NORMAL sync to avoid
            # paying a full fsync per commit while preserving crash-safe durability. Some
            # environments reject WAL, so fall back to FULL sync unless WAL is confirmed.
            journal_mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            if journal_mode and str(journal_mode[0]).lower() == "wal":
                conn.execute("PRAGMA synchronous=NORMAL")
            else:
                conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA busy_timeout=5000")
            self._local.conn = conn
        return self._local.conn

    def _init_schema(self) -> None:
        """Create table if not exists."""
        conn = self._get_conn()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                key TEXT PRIMARY KEY,
                value BLOB NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            )
            """
        )
        conn.commit()

    def _sync_get(self, key: str) -> Any | None:
        cursor = self._get_conn().execute(
            "SELECT value FROM cache WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return pickle.loads(row[0]) if row else None

    def _sync_put(self, key: str, value: Any) -> None:
        now = time.time()
        blob = pickle.dumps(value)
        self._get_conn().execute(
            """
            INSERT INTO cache (key, value, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?
            """,
            (key, blob, now, now, blob, now),
        )
        self._get_conn().commit()

    def _sync_delete(self, key: str) -> bool:
        cursor = self._get_conn().execute(
            "DELETE FROM cache WHERE key = ?", (key,)
        )
        self._get_conn().commit()
        return cursor.rowcount > 0

    def _sync_exists(self, key: str) -> bool:
        cursor = self._get_conn().execute(
            "SELECT 1 FROM cache WHERE key = ? LIMIT 1", (key,)
        )
        return cursor.fetchone() is not None

    def get(self, key: str):
        """Get value by key. Returns Program[Any | None] via Await."""
        return Await(asyncio.to_thread(self._sync_get, key))

    def put(self, key: str, value: Any):
        """Store value with key. Returns Program[None] via Await."""
        return Await(asyncio.to_thread(self._sync_put, key, value))

    def delete(self, key: str):
        """Delete key. Returns Program[bool] via Await."""
        return Await(asyncio.to_thread(self._sync_delete, key))

    def exists(self, key: str):
        """Check if key exists. Returns Program[bool] via Await."""
        return Await(asyncio.to_thread(self._sync_exists, key))

    def keys(self) -> Iterable[str]:
        """Return list of all keys."""
        cursor = self._get_conn().execute("SELECT key FROM cache")
        return [row[0] for row in cursor.fetchall()]

    def items(self) -> Iterable[tuple[str, Any]]:
        """Return list of all (key, value) pairs."""
        cursor = self._get_conn().execute("SELECT key, value FROM cache")
        return [(row[0], pickle.loads(row[1])) for row in cursor.fetchall()]

    def clear(self) -> None:
        """Delete all entries."""
        self._get_conn().execute("DELETE FROM cache")
        self._get_conn().commit()

    def close(self) -> None:
        """Close the thread-local connection if open."""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn

    def __len__(self) -> int:
        """Return number of entries."""
        cursor = self._get_conn().execute("SELECT COUNT(*) FROM cache")
        return cursor.fetchone()[0]

    def __repr__(self) -> str:
        return f"SQLiteStorage({self._db_path!r})"

    def __del__(self) -> None:
        """Close connection on garbage collection."""
        import logging

        try:
            self.close()
        except Exception as e:
            logging.debug(f"SQLiteStorage cleanup error: {e}")
