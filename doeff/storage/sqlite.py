"""
SQLite-backed durable storage implementation.

Provides persistent storage that survives process restarts.
Thread-safe via connection-per-thread pattern.
"""

from __future__ import annotations

import pickle
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any


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
            self._local.conn = sqlite3.connect(self._db_path)
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

    def get(self, key: str) -> Any | None:
        """Get value by key. Returns None if not found."""
        cursor = self._get_conn().execute(
            "SELECT value FROM cache WHERE key = ?", (key,)
        )
        row = cursor.fetchone()
        return pickle.loads(row[0]) if row else None

    def put(self, key: str, value: Any) -> None:
        """Store value with key. Overwrites if exists."""
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

    def delete(self, key: str) -> bool:
        """Delete key. Returns True if key existed."""
        cursor = self._get_conn().execute(
            "DELETE FROM cache WHERE key = ?", (key,)
        )
        self._get_conn().commit()
        return cursor.rowcount > 0

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        cursor = self._get_conn().execute(
            "SELECT 1 FROM cache WHERE key = ? LIMIT 1", (key,)
        )
        return cursor.fetchone() is not None

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
        try:
            self.close()
        except Exception:
            pass  # Ignore errors during cleanup
