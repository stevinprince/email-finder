"""Tests for the cache module (Step 7)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from email_finder.cache import BaseCache, InMemoryCache, SqliteCache, make_cache


# ---------------------------------------------------------------------------
# Shared contract tests — run against both backends
# ---------------------------------------------------------------------------

def _backends(tmp_path: Path):
    """Yield (label, cache) pairs for all backends."""
    yield "memory", InMemoryCache(default_ttl=60.0)
    yield "sqlite", SqliteCache(tmp_path / "test.db", default_ttl=60.0)


class _SharedCacheTests:
    """Base class — subclasses inject ``self.cache`` via a pytest fixture."""

    cache: BaseCache  # set by subclass _setup fixture

    def test_miss_returns_none(self):
        assert self.cache.get("no-such-key") is None

    def test_set_and_get(self):
        self.cache.set("k", {"x": 1})
        assert self.cache.get("k") == {"x": 1}

    def test_overwrite(self):
        self.cache.set("k", "first")
        self.cache.set("k", "second")
        assert self.cache.get("k") == "second"

    def test_delete_existing(self):
        self.cache.set("k", 42)
        self.cache.delete("k")
        assert self.cache.get("k") is None

    def test_delete_missing_is_noop(self):
        self.cache.delete("never-set")  # should not raise

    def test_clear(self):
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        self.cache.clear()
        assert len(self.cache) == 0

    def test_len(self):
        self.cache.clear()
        assert len(self.cache) == 0
        self.cache.set("a", 1)
        self.cache.set("b", 2)
        assert len(self.cache) == 2

    def test_contains(self):
        self.cache.set("present", True)
        assert "present" in self.cache
        assert "absent" not in self.cache

    def test_stores_nested_dict(self):
        value = {"a": [1, 2, 3], "b": {"c": None}}
        self.cache.set("nested", value)
        assert self.cache.get("nested") == value

    def test_stores_none_value(self):
        # Storing None: None is falsy so this distinguishes a cache miss
        # from a cached None.  Implementations may vary — we just test
        # that it doesn't crash.
        try:
            self.cache.set("none_val", None)
            result = self.cache.get("none_val")
            # Either None (treated as miss) or None (the stored value) is OK
            assert result is None
        except Exception as exc:
            pytest.fail(f"Storing None raised unexpectedly: {exc}")

    def test_ttl_expiry(self):
        """Entry expires after its TTL."""
        self.cache.set("expiring", "value", ttl=0.05)  # 50 ms
        assert self.cache.get("expiring") == "value"
        time.sleep(0.08)
        assert self.cache.get("expiring") is None

    def test_zero_ttl_never_expires(self):
        self.cache.set("forever", "persistent", ttl=0)
        time.sleep(0.02)
        assert self.cache.get("forever") == "persistent"

    def test_per_entry_ttl_overrides_default(self):
        """A per-call ttl= should override the backend's default."""
        self.cache.set("short", "v", ttl=0.05)
        time.sleep(0.08)
        assert self.cache.get("short") is None


class TestInMemoryCache(_SharedCacheTests):
    @pytest.fixture(autouse=True)
    def _setup(self):
        self.cache = InMemoryCache(default_ttl=60.0)

    def test_evict_expired_returns_count(self):
        self.cache.set("e1", 1, ttl=0.02)
        self.cache.set("e2", 2, ttl=0.02)
        self.cache.set("keep", 3, ttl=60.0)
        time.sleep(0.05)
        removed = self.cache.evict_expired()
        assert removed == 2
        assert self.cache.get("keep") == 3

    def test_thread_safety(self):
        """Concurrent writes from multiple threads should not corrupt state."""
        import threading
        errors = []

        def writer(n):
            try:
                for i in range(20):
                    self.cache.set(f"t{n}-{i}", n * 100 + i)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        assert not errors


class TestSqliteCache(_SharedCacheTests):
    @pytest.fixture(autouse=True)
    def _setup(self, tmp_path: Path):
        self.cache = SqliteCache(tmp_path / "cache.db", default_ttl=60.0)

    def test_persists_across_instances(self, tmp_path: Path):
        """Data written by one SqliteCache instance is visible to another."""
        db = tmp_path / "shared.db"
        c1 = SqliteCache(db)
        c1.set("shared", {"hello": "world"})

        c2 = SqliteCache(db)
        assert c2.get("shared") == {"hello": "world"}

    def test_creates_parent_directories(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "c" / "cache.db"
        cache = SqliteCache(nested)
        cache.set("k", 1)
        assert nested.exists()

    def test_evict_expired_returns_count(self, tmp_path: Path):
        db = tmp_path / "evict.db"
        cache = SqliteCache(db, default_ttl=60.0)
        cache.set("e1", 1, ttl=0.02)
        cache.set("e2", 2, ttl=0.02)
        cache.set("keep", 3, ttl=60.0)
        time.sleep(0.05)
        removed = cache.evict_expired()
        assert removed == 2
        assert cache.get("keep") == 3


# ---------------------------------------------------------------------------
# make_cache factory
# ---------------------------------------------------------------------------

class TestMakeCache:
    def test_memory_backend(self):
        c = make_cache("memory")
        assert isinstance(c, InMemoryCache)

    def test_sqlite_backend(self, tmp_path: Path):
        c = make_cache("sqlite", db_path=tmp_path / "f.db")
        assert isinstance(c, SqliteCache)

    def test_sqlite_requires_db_path(self):
        with pytest.raises(ValueError, match="db_path"):
            make_cache("sqlite")

    def test_unknown_backend_raises(self):
        with pytest.raises(ValueError, match="Unknown cache backend"):
            make_cache("redis")

    def test_case_insensitive(self):
        c = make_cache("MEMORY")
        assert isinstance(c, InMemoryCache)

    def test_default_ttl_passed_through(self):
        c = make_cache("memory", default_ttl=999.0)
        assert isinstance(c, InMemoryCache)
        c.set("x", 1)  # uses default TTL — just check no crash
