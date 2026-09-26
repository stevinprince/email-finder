"""Tests for the rate limiter (Step 7)."""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import pytest

from email_finder.rate_limiter import RateLimiter, no_limit


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------

class TestRateLimiterBasic:
    def test_zero_interval_never_sleeps(self):
        r = RateLimiter(min_interval=0.0)
        with patch("email_finder.rate_limiter.time.sleep") as mock_sleep:
            r.acquire()
            mock_sleep.assert_not_called()

    def test_acquire_returns_wait_time(self):
        r = RateLimiter(min_interval=0.0)
        waited = r.acquire()
        assert waited == 0.0

    def test_no_limit_factory(self):
        r = no_limit()
        assert r.min_interval == 0.0

    def test_call_count_increments(self):
        r = RateLimiter(min_interval=0.0)
        for _ in range(5):
            r.acquire()
        assert r.call_count == 5

    def test_total_wait_zero_for_no_limit(self):
        r = RateLimiter(min_interval=0.0)
        r.acquire()
        r.acquire()
        assert r.total_wait_seconds == 0.0

    def test_reset_stats_clears_counters(self):
        r = RateLimiter(min_interval=0.0)
        r.acquire()
        r.acquire()
        r.reset_stats()
        assert r.call_count == 0
        assert r.total_wait_seconds == 0.0


# ---------------------------------------------------------------------------
# Rate-limiting behaviour (mocked time)
# ---------------------------------------------------------------------------

class TestRateLimiterTiming:
    def test_sleeps_when_called_too_quickly(self):
        r = RateLimiter(min_interval=1.0)

        call_times = [0.0, 0.3]   # second call is 0.3s after first
        call_idx = [0]
        sleep_called_with = []

        def fake_monotonic():
            t = call_times[min(call_idx[0], len(call_times) - 1)]
            call_idx[0] += 1
            return t

        def fake_sleep(seconds):
            sleep_called_with.append(seconds)

        with patch("email_finder.rate_limiter.time.monotonic", side_effect=fake_monotonic), \
             patch("email_finder.rate_limiter.time.sleep",     side_effect=fake_sleep):
            r.acquire()  # first call — no sleep
            r.acquire()  # second call — should sleep ~0.7s

        # The first acquire uses monotonic twice (read + write)
        # We just assert sleep was called with a positive value on second acquire
        assert any(s > 0 for s in sleep_called_with), \
            f"Expected sleep to be called with >0; got {sleep_called_with}"

    def test_no_sleep_when_enough_time_has_passed(self):
        r = RateLimiter(min_interval=0.5)

        # Simulate second call happening 1.0s after first (> min_interval)
        times = iter([0.0, 0.0, 1.0, 1.0])

        with patch("email_finder.rate_limiter.time.monotonic", side_effect=times), \
             patch("email_finder.rate_limiter.time.sleep") as mock_sleep:
            r.acquire()
            r.acquire()

        mock_sleep.assert_not_called()

    def test_total_wait_accumulates(self):
        r = RateLimiter(min_interval=1.0)
        sleep_amounts = []

        # First acquire: no sleep (last_call=0, elapsed=huge)
        # Second acquire: sleep ~0.6s
        times_first  = [0.0, 0.1]
        times_second = [0.5, 0.5]  # only 0.5s has passed → sleep 0.5s
        all_times = iter(times_first + times_second)

        def fake_sleep(s):
            sleep_amounts.append(s)

        with patch("email_finder.rate_limiter.time.monotonic", side_effect=all_times), \
             patch("email_finder.rate_limiter.time.sleep", side_effect=fake_sleep):
            r.acquire()
            r.acquire()

        assert r.total_wait_seconds > 0


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------

class TestRateLimiterThreadSafety:
    def test_concurrent_acquires_dont_race(self):
        """Multiple threads acquiring the same limiter should not crash."""
        r = RateLimiter(min_interval=0.0)  # no sleep, just lock contention
        errors = []

        def worker():
            try:
                for _ in range(50):
                    r.acquire()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert not errors
        assert r.call_count == 8 * 50

    def test_call_count_is_atomic(self):
        r = RateLimiter(min_interval=0.0)
        n_threads, n_calls = 10, 100

        def worker():
            for _ in range(n_calls):
                r.acquire()

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert r.call_count == n_threads * n_calls
