"""
Rate limiter: enforce a minimum interval between outgoing calls.

Unlike a plain ``time.sleep(delay)``, :class:`RateLimiter` tracks the
*actual* elapsed time since the last call and only sleeps for the
*remaining* time needed.  If the previous call itself took longer than
the minimum interval, no sleep occurs at all.

This avoids stacking delays on top of already-slow server responses.

Thread safety
-------------
:class:`RateLimiter` is thread-safe; multiple threads sharing a single
instance will be serialised correctly.

Usage
-----
>>> limiter = RateLimiter(min_interval=1.5)
>>> for email in many_addresses:
...     limiter.acquire()           # sleeps only if needed
...     result = verify_smtp(email)
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """Enforce a minimum interval between :meth:`acquire` calls.

    Args:
        min_interval: Minimum seconds between successive calls.
                      ``0`` means no rate limiting (acquire() returns
                      immediately).
    """

    min_interval: float

    # Internal state — excluded from repr / equality
    # Start at -inf so the very first acquire() never sleeps.
    _last_call:  float        = field(default=float("-inf"),         init=False, repr=False, compare=False)
    _lock:       threading.Lock = field(default_factory=threading.Lock, init=False, repr=False, compare=False)
    _total_wait: float        = field(default=0.0,                  init=False, repr=False, compare=False)
    _call_count: int          = field(default=0,                    init=False, repr=False, compare=False)

    def acquire(self) -> float:
        """Block until the minimum interval has elapsed since the last call.

        Returns:
            The number of seconds actually slept (≥ 0).
        """
        if self.min_interval <= 0:
            with self._lock:
                self._call_count += 1
            return 0.0

        with self._lock:
            now       = time.monotonic()
            elapsed   = now - self._last_call
            sleep_for = max(0.0, self.min_interval - elapsed)

            if sleep_for > 0:
                time.sleep(sleep_for)

            self._last_call   = time.monotonic()
            self._total_wait += sleep_for
            self._call_count += 1

        return sleep_for

    # ── Diagnostics ───────────────────────────────────────────────────────────

    @property
    def total_wait_seconds(self) -> float:
        """Cumulative seconds spent sleeping across all :meth:`acquire` calls."""
        with self._lock:
            return self._total_wait

    @property
    def call_count(self) -> int:
        """Number of times :meth:`acquire` has been called."""
        with self._lock:
            return self._call_count

    def reset_stats(self) -> None:
        """Reset diagnostic counters (does not affect rate-limiting state)."""
        with self._lock:
            self._total_wait = 0.0
            self._call_count = 0


def no_limit() -> RateLimiter:
    """Return a :class:`RateLimiter` that never sleeps."""
    return RateLimiter(min_interval=0.0)
