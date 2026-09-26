"""
Result cache: avoid repeating expensive network calls for the same address.

Two backends are provided:

* :class:`InMemoryCache` — stores results in a plain dict, lives only for
  the duration of the process.  Good for a single CLI run with many
  candidates on the same domain.

* :class:`SqliteCache` — writes results to a local SQLite file so they
  survive across runs.  Good for repeated look-ups over time.

Both implement :class:`BaseCache` so they are interchangeable.

Cached types
------------
The cache stores plain JSON-serialisable dicts.  Callers are responsible
for serialising domain-specific objects before storing and deserialising
after retrieval.  :mod:`email_finder.finder` provides the helpers for
:class:`~email_finder.smtp_verifier.SmtpResult` and
:class:`~email_finder.api_verifier.ApiResult`.

Thread safety
-------------
:class:`InMemoryCache` and :class:`SqliteCache` are both thread-safe.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseCache(ABC):
    """Common interface for all cache backends."""

    @abstractmethod
    def get(self, key: str) -> Any | None:
        """Return the cached value for *key*, or ``None`` on miss/expiry."""

    @abstractmethod
    def set(self, key: str, value: Any, *, ttl: float | None = None) -> None:
        """Store *value* under *key*.

        Args:
            key:   Cache key.
            value: JSON-serialisable value.
            ttl:   Time-to-live in seconds.  ``None`` → backend default.
                   ``0`` → never expires.
        """

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove *key* from the cache (no-op if absent)."""

    @abstractmethod
    def clear(self) -> None:
        """Evict all entries."""

    @abstractmethod
    def __len__(self) -> int:
        """Number of non-expired entries currently in the cache."""

    def __bool__(self) -> bool:
        """A cache instance is always truthy, even when empty."""
        return True

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


# ---------------------------------------------------------------------------
# In-memory implementation
# ---------------------------------------------------------------------------

@dataclass
class _Entry:
    value:      Any
    created_at: float   # time.monotonic() timestamp
    ttl:        float   # 0 = never expire

    def is_expired(self) -> bool:
        return self.ttl > 0 and (time.monotonic() - self.created_at) > self.ttl


class InMemoryCache(BaseCache):
    """Thread-safe in-memory cache with per-entry TTL.

    Args:
        default_ttl: Default time-to-live in seconds.
                     ``0`` means entries never expire.
    """

    def __init__(self, *, default_ttl: float = 3600.0) -> None:
        self._store: dict[str, _Entry] = {}
        self._default_ttl = default_ttl
        self._lock = threading.Lock()

    # ── BaseCache interface ────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if entry.is_expired():
                del self._store[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, *, ttl: float | None = None) -> None:
        actual_ttl = self._default_ttl if ttl is None else ttl
        with self._lock:
            self._store[key] = _Entry(
                value=value,
                created_at=time.monotonic(),
                ttl=actual_ttl,
            )

    def delete(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return sum(1 for e in self._store.values() if not e.is_expired())

    # ── Extra helpers ─────────────────────────────────────────────────────────

    def evict_expired(self) -> int:
        """Remove all expired entries and return the count removed."""
        with self._lock:
            expired = [k for k, e in self._store.items() if e.is_expired()]
            for k in expired:
                del self._store[k]
            return len(expired)


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT    PRIMARY KEY,
    value_json TEXT    NOT NULL,
    created_at REAL    NOT NULL,
    ttl        REAL    NOT NULL
)
"""

_UPSERT = """
INSERT OR REPLACE INTO cache (key, value_json, created_at, ttl)
VALUES (?, ?, ?, ?)
"""


class SqliteCache(BaseCache):
    """Persistent cache backed by a local SQLite database file.

    Results survive across process restarts.  Useful for CLI usage where the
    same addresses or domains are queried repeatedly over days or weeks.

    Args:
        db_path:     Path to the SQLite file.  Created if it does not exist.
        default_ttl: Default TTL in seconds.  ``0`` = never expire.
    """

    def __init__(
        self,
        db_path: str | Path,
        *,
        default_ttl: float = 3600.0,
    ) -> None:
        self._db_path    = Path(db_path)
        self._default_ttl = default_ttl
        self._lock        = threading.Lock()
        self._init_db()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path), check_same_thread=False)

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.execute(_CREATE_TABLE)

    # ── BaseCache interface ────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT value_json, created_at, ttl FROM cache WHERE key = ?",
                (key,),
            ).fetchone()

        if row is None:
            return None

        value_json, created_at, ttl = row
        if ttl > 0 and (time.time() - created_at) > ttl:
            self.delete(key)
            return None

        try:
            return json.loads(value_json)
        except (ValueError, TypeError):
            return None

    def set(self, key: str, value: Any, *, ttl: float | None = None) -> None:
        actual_ttl = self._default_ttl if ttl is None else ttl
        value_json = json.dumps(value, ensure_ascii=False)
        with self._lock, self._connect() as conn:
            conn.execute(_UPSERT, (key, value_json, time.time(), actual_ttl))

    def delete(self, key: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM cache WHERE key = ?", (key,))

    def clear(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM cache")

    def __len__(self) -> int:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE ttl = 0 OR (? - created_at) <= ttl",
                (now,),
            ).fetchone()
        return row[0] if row else 0

    def evict_expired(self) -> int:
        """Delete expired entries and return the count removed."""
        now = time.time()
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM cache WHERE ttl > 0 AND (? - created_at) > ttl",
                (now,),
            )
        return cursor.rowcount


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def make_cache(
    backend: str = "memory",
    *,
    default_ttl: float = 3600.0,
    db_path: str | Path | None = None,
) -> BaseCache:
    """Instantiate a cache backend by name.

    Args:
        backend:     ``'memory'`` or ``'sqlite'``.
        default_ttl: Default entry TTL in seconds.
        db_path:     Path to the SQLite file (required for ``'sqlite'``).

    Returns:
        A configured :class:`BaseCache` instance.

    Raises:
        ValueError: For unknown *backend* values or a missing *db_path*
                    when ``backend='sqlite'``.
    """
    b = backend.lower().strip()

    if b == "memory":
        return InMemoryCache(default_ttl=default_ttl)

    if b == "sqlite":
        if db_path is None:
            raise ValueError(
                "db_path is required for the 'sqlite' cache backend."
            )
        return SqliteCache(db_path=db_path, default_ttl=default_ttl)

    raise ValueError(
        f"Unknown cache backend {backend!r}.  Supported: 'memory', 'sqlite'."
    )
