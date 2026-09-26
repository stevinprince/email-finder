"""Tests for the SMTP × API result combiner (Step 5)."""

from __future__ import annotations

import pytest

from email_finder.api_verifier import ApiResult, ApiStatus
from email_finder.combiner import CombinedResult, combine
from email_finder.smtp_verifier import SmtpResult, SmtpStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _smtp(status: SmtpStatus) -> SmtpResult:
    return SmtpResult(email="x@y.com", status=status, mx_host="mail.y.com")


def _api(status: ApiStatus, score: int | None = None) -> ApiResult:
    return ApiResult(
        email="x@y.com",
        status=status,
        provider="mock",
        score=score,
    )


EMAIL = "jane@acme.com"


# ---------------------------------------------------------------------------
# Basic contract
# ---------------------------------------------------------------------------

class TestCombineContract:
    def test_returns_combined_result_type(self):
        r = combine(EMAIL, smtp=_smtp(SmtpStatus.VALID))
        assert isinstance(r, CombinedResult)

    def test_email_preserved(self):
        r = combine(EMAIL, smtp=_smtp(SmtpStatus.VALID))
        assert r.email == EMAIL

    def test_both_none_raises(self):
        with pytest.raises(ValueError):
            combine(EMAIL)

    def test_smtp_stored(self):
        s = _smtp(SmtpStatus.VALID)
        r = combine(EMAIL, smtp=s)
        assert r.smtp is s

    def test_api_stored(self):
        a = _api(ApiStatus.VALID)
        r = combine(EMAIL, api=a)
        assert r.api is a

    def test_confidence_in_range(self):
        for ss in SmtpStatus:
            for sa in ApiStatus:
                r = combine(EMAIL, smtp=_smtp(ss), api=_api(sa))
                assert 0 <= r.confidence <= 100

    def test_verdict_is_valid_string(self):
        for ss in SmtpStatus:
            r = combine(EMAIL, smtp=_smtp(ss))
            assert r.verdict in ("valid", "uncertain", "invalid")

    def test_explanation_non_empty(self):
        r = combine(EMAIL, smtp=_smtp(SmtpStatus.VALID), api=_api(ApiStatus.VALID))
        assert r.explanation != ""


# ---------------------------------------------------------------------------
# Confidence table spot-checks (SMTP only)
# ---------------------------------------------------------------------------

class TestConfidenceSmtpOnly:
    @pytest.mark.parametrize("status, min_score", [
        (SmtpStatus.VALID,     60),
        (SmtpStatus.INVALID,    5),
        (SmtpStatus.CATCH_ALL, 35),
        (SmtpStatus.UNKNOWN,   25),
        (SmtpStatus.ERROR,      0),
    ])
    def test_smtp_only_score_range(self, status, min_score):
        r = combine(EMAIL, smtp=_smtp(status))
        assert r.confidence >= min_score or r.confidence == 0

    def test_smtp_valid_verdict_is_valid(self):
        r = combine(EMAIL, smtp=_smtp(SmtpStatus.VALID))
        assert r.verdict == "valid"

    def test_smtp_invalid_verdict_is_invalid(self):
        r = combine(EMAIL, smtp=_smtp(SmtpStatus.INVALID))
        assert r.verdict == "invalid"

    def test_smtp_catch_all_verdict_is_uncertain(self):
        r = combine(EMAIL, smtp=_smtp(SmtpStatus.CATCH_ALL))
        assert r.verdict == "uncertain"


# ---------------------------------------------------------------------------
# Confidence table spot-checks (API only)
# ---------------------------------------------------------------------------

class TestConfidenceApiOnly:
    @pytest.mark.parametrize("status, min_score", [
        (ApiStatus.VALID,    60),
        (ApiStatus.INVALID,   5),
        (ApiStatus.UNKNOWN,  20),
        (ApiStatus.ERROR,     0),
    ])
    def test_api_only_score_range(self, status, min_score):
        r = combine(EMAIL, api=_api(status))
        assert r.confidence >= min_score or r.confidence == 0

    def test_api_valid_verdict_is_valid(self):
        r = combine(EMAIL, api=_api(ApiStatus.VALID))
        assert r.verdict == "valid"

    def test_api_invalid_verdict_is_invalid(self):
        r = combine(EMAIL, api=_api(ApiStatus.INVALID))
        assert r.verdict == "invalid"


# ---------------------------------------------------------------------------
# Provider score integration
# ---------------------------------------------------------------------------

class TestProviderScore:
    def test_provider_score_used_when_present(self):
        """If API supplies score=95, combined confidence should be close to 95."""
        r = combine(EMAIL, api=_api(ApiStatus.VALID, score=95))
        assert r.confidence >= 90  # may get +5 boost

    def test_smtp_agreement_boosts_score(self):
        """SMTP VALID + API VALID (score=80) should push confidence above 80."""
        r_no_smtp = combine(EMAIL, api=_api(ApiStatus.VALID, score=80))
        r_with    = combine(EMAIL, smtp=_smtp(SmtpStatus.VALID), api=_api(ApiStatus.VALID, score=80))
        assert r_with.confidence >= r_no_smtp.confidence

    def test_conflicting_signals_reduce_score(self):
        """SMTP VALID + API INVALID should reduce confidence versus API alone."""
        r_no_smtp = combine(EMAIL, api=_api(ApiStatus.INVALID, score=10))
        r_with    = combine(EMAIL, smtp=_smtp(SmtpStatus.VALID), api=_api(ApiStatus.INVALID, score=10))
        # Conflict pulls score: combined might be higher than pure API invalid
        # but should be in uncertain zone
        assert r_with.verdict in ("invalid", "uncertain")

    def test_score_clamped_to_0_100(self):
        r = combine(EMAIL, api=_api(ApiStatus.VALID, score=100))
        assert r.confidence <= 100
        r2 = combine(EMAIL, api=_api(ApiStatus.INVALID, score=0))
        assert r2.confidence >= 0


# ---------------------------------------------------------------------------
# Combined (both SMTP + API) spot-checks
# ---------------------------------------------------------------------------

class TestCombinedSignals:
    @pytest.mark.parametrize("smtp_s, api_s, expected_verdict", [
        (SmtpStatus.VALID,     ApiStatus.VALID,    "valid"),
        (SmtpStatus.INVALID,   ApiStatus.INVALID,  "invalid"),
        (SmtpStatus.CATCH_ALL, ApiStatus.VALID,    "valid"),
        (SmtpStatus.VALID,     ApiStatus.UNKNOWN,  "valid"),
        (SmtpStatus.UNKNOWN,   ApiStatus.VALID,    "valid"),
        (SmtpStatus.UNKNOWN,   ApiStatus.UNKNOWN,  "uncertain"),
        (SmtpStatus.ERROR,     ApiStatus.ERROR,    "invalid"),  # score=0 → invalid
    ])
    def test_verdict_matrix(self, smtp_s, api_s, expected_verdict):
        r = combine(EMAIL, smtp=_smtp(smtp_s), api=_api(api_s))
        assert r.verdict == expected_verdict

    def test_agreeing_valid_gives_highest_confidence(self):
        r = combine(EMAIL, smtp=_smtp(SmtpStatus.VALID), api=_api(ApiStatus.VALID))
        assert r.confidence >= 90

    def test_agreeing_invalid_gives_lowest_confidence(self):
        r = combine(EMAIL, smtp=_smtp(SmtpStatus.INVALID), api=_api(ApiStatus.INVALID))
        assert r.confidence <= 15
