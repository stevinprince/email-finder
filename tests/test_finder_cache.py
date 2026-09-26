"""Integration tests for cache + rate-limiter inside find_emails() (Step 7)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from email_finder.api_verifier import ApiResult, ApiStatus, MockVerifier
from email_finder.cache import InMemoryCache
from email_finder.finder import FinderConfig, find_emails
from email_finder.smtp_verifier import SmtpResult, SmtpStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _smtp(email: str, status: SmtpStatus = SmtpStatus.VALID, **_kw) -> SmtpResult:
    return SmtpResult(email=email, status=status, smtp_code=250,
                      mx_host="mail.acme.com", attempts=1)


def _api(email: str, status: ApiStatus = ApiStatus.VALID) -> ApiResult:
    return ApiResult(email=email, status=status, provider="mock", score=85)


# ---------------------------------------------------------------------------
# SMTP caching
# ---------------------------------------------------------------------------

class TestSmtpCache:
    def test_second_run_makes_no_smtp_calls(self):
        """After the first run, a second run with the same cache should not
        call verify_smtp at all."""
        cache = InMemoryCache(default_ttl=3600)
        calls = []

        def fake_smtp(email, **kw):
            calls.append(email)
            return _smtp(email)

        config = FinderConfig(run_smtp=True, smtp_delay=0.0, cache=cache)

        with patch("email_finder.finder.verify_smtp", side_effect=fake_smtp), \
             patch("email_finder.rate_limiter.time.sleep"):
            find_emails("Jane Doe", domain="acme.com", config=config)
            first = len(calls)

            calls.clear()
            find_emails("Jane Doe", domain="acme.com", config=config)
            second = len(calls)

        assert first > 0,  "First run must make real SMTP calls"
        assert second == 0, "Second run must serve entirely from cache"

    def test_cached_smtp_results_same_as_original(self):
        """Results returned from cache must equal the original results."""
        cache = InMemoryCache(default_ttl=3600)
        config = FinderConfig(run_smtp=True, smtp_delay=0.0, cache=cache)

        with patch("email_finder.finder.verify_smtp", side_effect=_smtp), \
             patch("email_finder.rate_limiter.time.sleep"):
            r1 = find_emails("Jane Doe", domain="acme.com", config=config)
            r2 = find_emails("Jane Doe", domain="acme.com", config=config)

        # Same candidates in same order, same verdicts
        assert [c.email for c in r1.candidates] == [c.email for c in r2.candidates]
        assert [c.verdict for c in r1.candidates] == [c.verdict for c in r2.candidates]
        assert [c.confidence for c in r1.candidates] == [c.confidence for c in r2.candidates]

    def test_cache_is_populated_after_first_run(self):
        cache = InMemoryCache(default_ttl=3600)
        config = FinderConfig(run_smtp=True, smtp_delay=0.0, cache=cache)

        with patch("email_finder.finder.verify_smtp", side_effect=_smtp), \
             patch("email_finder.rate_limiter.time.sleep"):
            r = find_emails("Jane Doe", domain="acme.com", config=config)

        assert len(cache) == len(r.candidates)

    def test_rate_limiter_skipped_on_cache_hit(self):
        """Rate limiter must NOT be invoked for cached candidates."""
        cache = InMemoryCache(default_ttl=3600)
        config = FinderConfig(run_smtp=True, smtp_delay=1.0, cache=cache)
        acquire_calls = []

        with patch("email_finder.finder.verify_smtp", side_effect=_smtp), \
             patch("email_finder.rate_limiter.time.sleep"):
            find_emails("Jane Doe", domain="acme.com", config=config)  # fill cache

        with patch("email_finder.finder.verify_smtp", side_effect=_smtp), \
             patch("email_finder.rate_limiter.time.sleep") as mock_sleep:
            find_emails("Jane Doe", domain="acme.com", config=config)  # all from cache

        mock_sleep.assert_not_called()


# ---------------------------------------------------------------------------
# API caching
# ---------------------------------------------------------------------------

class TestApiCache:
    def test_second_run_makes_no_api_calls(self):
        cache = InMemoryCache(default_ttl=3600)
        calls = []
        mock_v = MockVerifier()

        original_verify = mock_v.verify
        def counting_verify(email):
            calls.append(email)
            return original_verify(email)
        mock_v.verify = counting_verify

        config = FinderConfig(run_api=True, api_provider="mock",
                              smtp_delay=0.0, cache=cache)

        with patch("email_finder.finder.get_verifier", return_value=mock_v), \
             patch("email_finder.rate_limiter.time.sleep"):
            find_emails("Jane Doe", domain="acme.com", config=config)
            first = len(calls)

            calls.clear()
            find_emails("Jane Doe", domain="acme.com", config=config)
            second = len(calls)

        assert first > 0
        assert second == 0

    def test_api_and_smtp_cache_keys_are_different(self):
        """SMTP and API results for the same email must be stored under separate keys."""
        cache = InMemoryCache(default_ttl=3600)
        config = FinderConfig(run_smtp=True, run_api=True, api_provider="mock",
                              smtp_delay=0.0, cache=cache)
        mock_v = MockVerifier()

        with patch("email_finder.finder.verify_smtp", side_effect=_smtp), \
             patch("email_finder.finder.get_verifier", return_value=mock_v), \
             patch("email_finder.rate_limiter.time.sleep"):
            r = find_emails("Jane Doe", domain="acme.com", config=config)

        # Cache should have 2 × n_candidates entries (one smtp:, one api:)
        assert len(cache) == 2 * len(r.candidates)


# ---------------------------------------------------------------------------
# Cache TTL
# ---------------------------------------------------------------------------

class TestCacheTtl:
    def test_expired_cache_entries_trigger_new_smtp_calls(self):
        """After TTL expiry, find_emails must re-verify rather than serve stale data.

        We mock ``time.monotonic`` so the 'second run' appears to happen one
        hour after the first, which is far past any reasonable TTL.
        """
        import time as real_time
        cache = InMemoryCache(default_ttl=3600)
        config = FinderConfig(run_smtp=True, smtp_delay=0.0,
                              cache=cache, cache_ttl=60.0)
        calls = []

        # Monotonic ticks for the 'future' scenario
        base_now = real_time.monotonic()
        future_offset = 7200.0  # 2 hours in the future

        with patch("email_finder.finder.verify_smtp", side_effect=_smtp), \
             patch("email_finder.rate_limiter.time.sleep"):
            find_emails("Jane Doe", domain="acme.com", config=config)
            first_cached = len(cache)

        # Advance the clock inside the cache module so TTL appears expired
        with patch("email_finder.cache.time.monotonic",
                   return_value=base_now + future_offset), \
             patch("email_finder.finder.verify_smtp",
                   side_effect=lambda e, **kw: (calls.append(e), _smtp(e))[1]), \
             patch("email_finder.rate_limiter.time.sleep"):
            find_emails("Jane Doe", domain="acme.com", config=config)

        assert first_cached > 0
        assert len(calls) > 0, "After TTL expiry, fresh SMTP calls should be made"

    def test_config_ttl_is_used_not_default_ttl(self):
        """cache_ttl from FinderConfig overrides the cache's own default TTL."""
        import time as real_time
        cache = InMemoryCache(default_ttl=3600)
        config = FinderConfig(run_smtp=True, smtp_delay=0.0,
                              cache=cache, cache_ttl=120.0)

        with patch("email_finder.finder.verify_smtp", side_effect=_smtp), \
             patch("email_finder.rate_limiter.time.sleep"):
            find_emails("Jane Doe", domain="acme.com", config=config)

        # Each entry should have been stored with ttl=120, not 3600
        for entry in cache._store.values():  # type: ignore[attr-defined]
            assert entry.ttl == 120.0


# ---------------------------------------------------------------------------
# No cache (baseline)
# ---------------------------------------------------------------------------

def test_no_cache_does_not_crash():
    """Running without a cache should work normally."""
    calls = []
    config = FinderConfig(run_smtp=True, smtp_delay=0.0, cache=None)

    def fake_smtp(email, **kw):
        calls.append(email)
        return _smtp(email)

    with patch("email_finder.finder.verify_smtp", side_effect=fake_smtp), \
         patch("email_finder.rate_limiter.time.sleep"):
        r = find_emails("Jane Doe", domain="acme.com", config=config)

    assert len(calls) == len(r.candidates)
    assert all(c.verdict == "valid" for c in r.candidates)
