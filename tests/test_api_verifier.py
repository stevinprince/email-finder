"""Tests for the third-party API verifier abstraction (Step 5).

All HTTP calls are mocked — no real network requests are made.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests

from email_finder.api_verifier import (
    ApiResult,
    ApiStatus,
    BaseApiVerifier,
    HunterVerifier,
    MockVerifier,
    get_verifier,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_hunter_response(
    status: str = "valid",
    score: int = 90,
    result: str = "deliverable",
    http_status: int = 200,
    extra: dict | None = None,
) -> MagicMock:
    """Return a mock requests.Response for Hunter.io."""
    body = {
        "data": {
            "status": status,
            "result": result,
            "score": score,
            "email": "test@example.com",
            "disposable": False,
            "gibberish": False,
            "webmail": False,
            "accept_all": status == "accept_all",
            "block": False,
            "mx_records": True,
            "smtp_server": True,
            "smtp_check": True,
            **(extra or {}),
        },
        "meta": {"params": {"email": "test@example.com"}},
    }
    mock_resp = MagicMock()
    mock_resp.status_code = http_status
    mock_resp.ok = (200 <= http_status < 300)
    mock_resp.json.return_value = body
    return mock_resp


def _patch_hunter_get(mock_response: MagicMock):
    """Patch the requests.Session.get used inside HunterVerifier."""
    session_mock = MagicMock()
    session_mock.get.return_value = mock_response
    return session_mock


# ---------------------------------------------------------------------------
# BaseApiVerifier — ABC contract
# ---------------------------------------------------------------------------

class TestBaseApiVerifier:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            BaseApiVerifier()  # type: ignore[abstract]

    def test_concrete_subclass_must_implement_verify(self):
        class Broken(BaseApiVerifier):
            @property
            def provider_name(self):
                return "broken"
            # missing verify()

        with pytest.raises(TypeError):
            Broken()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# HunterVerifier — happy path
# ---------------------------------------------------------------------------

class TestHunterVerifierHappyPath:
    EMAIL = "jane.doe@acme.com"

    def _run(self, mock_resp: MagicMock) -> ApiResult:
        session = _patch_hunter_get(mock_resp)
        v = HunterVerifier(api_key="test-key", session=session)
        return v.verify(self.EMAIL)

    def test_valid_status_mapped_correctly(self):
        r = self._run(_make_hunter_response("valid", score=88))
        assert r.status == ApiStatus.VALID
        assert r.score == 88
        assert r.provider == "hunter.io"

    def test_invalid_status_mapped_correctly(self):
        r = self._run(_make_hunter_response("invalid", score=5))
        assert r.status == ApiStatus.INVALID

    def test_accept_all_maps_to_unknown(self):
        r = self._run(_make_hunter_response("accept_all", score=50))
        assert r.status == ApiStatus.UNKNOWN
        assert r.raw_status == "accept_all"

    def test_unknown_status_maps_to_unknown(self):
        r = self._run(_make_hunter_response("unknown", score=30))
        assert r.status == ApiStatus.UNKNOWN

    def test_score_preserved(self):
        r = self._run(_make_hunter_response("valid", score=77))
        assert r.score == 77

    def test_extra_fields_in_result(self):
        r = self._run(_make_hunter_response("valid", score=90))
        assert "mx_records" in r.extra or "disposable" in r.extra

    def test_detail_contains_provider_info(self):
        r = self._run(_make_hunter_response("valid", score=90))
        assert "hunter.io" in r.detail.lower() or "valid" in r.detail.lower()

    def test_email_preserved_in_result(self):
        r = self._run(_make_hunter_response("valid"))
        assert r.email == self.EMAIL


# ---------------------------------------------------------------------------
# HunterVerifier — HTTP error cases
# ---------------------------------------------------------------------------

class TestHunterVerifierErrors:
    EMAIL = "jane.doe@acme.com"

    def _run_with_http_status(self, http_status: int) -> ApiResult:
        mock_resp = _make_hunter_response(http_status=http_status)
        session = _patch_hunter_get(mock_resp)
        v = HunterVerifier(api_key="test-key", session=session)
        return v.verify(self.EMAIL)

    def test_401_returns_error_with_auth_message(self):
        r = self._run_with_http_status(401)
        assert r.status == ApiStatus.ERROR
        assert "auth" in r.detail.lower() or "key" in r.detail.lower()

    def test_429_returns_error_with_rate_limit_message(self):
        r = self._run_with_http_status(429)
        assert r.status == ApiStatus.ERROR
        assert "rate" in r.detail.lower()

    def test_422_returns_invalid(self):
        r = self._run_with_http_status(422)
        assert r.status == ApiStatus.INVALID

    def test_500_returns_error(self):
        r = self._run_with_http_status(500)
        assert r.status == ApiStatus.ERROR

    def test_network_timeout_returns_error(self):
        session = MagicMock()
        session.get.side_effect = requests.Timeout()
        v = HunterVerifier(api_key="test-key", session=session)
        r = v.verify(self.EMAIL)
        assert r.status == ApiStatus.ERROR
        assert "timeout" in r.detail.lower() or "timed out" in r.detail.lower()

    def test_network_error_returns_error(self):
        session = MagicMock()
        session.get.side_effect = requests.ConnectionError("unreachable")
        v = HunterVerifier(api_key="test-key", session=session)
        r = v.verify(self.EMAIL)
        assert r.status == ApiStatus.ERROR

    def test_bad_json_returns_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.ok = True
        mock_resp.json.side_effect = ValueError("not json")
        session = _patch_hunter_get(mock_resp)
        v = HunterVerifier(api_key="test-key", session=session)
        r = v.verify(self.EMAIL)
        assert r.status == ApiStatus.ERROR


# ---------------------------------------------------------------------------
# HunterVerifier — construction
# ---------------------------------------------------------------------------

class TestHunterVerifierConstruction:
    def test_empty_api_key_raises(self):
        with pytest.raises(ValueError, match="API key"):
            HunterVerifier(api_key="")

    def test_provider_name(self):
        v = HunterVerifier(api_key="x")
        assert v.provider_name == "hunter.io"

    def test_result_never_raises(self):
        """verify() must return ApiResult, never raise."""
        session = MagicMock()
        session.get.side_effect = Exception("unexpected")
        v = HunterVerifier(api_key="x", session=session)
        r = v.verify("x@y.com")
        assert isinstance(r, ApiResult)
        assert r.status == ApiStatus.ERROR


# ---------------------------------------------------------------------------
# MockVerifier
# ---------------------------------------------------------------------------

class TestMockVerifier:
    def test_default_is_valid(self):
        v = MockVerifier()
        r = v.verify("any@email.com")
        assert r.status == ApiStatus.VALID

    def test_custom_default_status(self):
        v = MockVerifier(default_status=ApiStatus.INVALID)
        assert v.verify("x@y.com").status == ApiStatus.INVALID

    def test_per_email_response(self):
        specific = ApiResult(
            email="special@co.com",
            status=ApiStatus.INVALID,
            provider="mock",
            score=5,
        )
        v = MockVerifier(responses={"special@co.com": specific})
        assert v.verify("special@co.com").status == ApiStatus.INVALID
        assert v.verify("other@co.com").status == ApiStatus.VALID  # default

    def test_provider_name(self):
        assert MockVerifier().provider_name == "mock"

    def test_score_in_result(self):
        v = MockVerifier(default_score=42)
        assert v.verify("x@y.com").score == 42


# ---------------------------------------------------------------------------
# get_verifier factory
# ---------------------------------------------------------------------------

class TestGetVerifier:
    def test_mock_provider(self):
        v = get_verifier("mock")
        assert isinstance(v, MockVerifier)

    def test_hunter_provider_with_key(self):
        v = get_verifier("hunter", api_key="my-key")
        assert isinstance(v, HunterVerifier)

    def test_hunter_reads_env_key(self, monkeypatch):
        monkeypatch.setenv("HUNTER_API_KEY", "env-key")
        v = get_verifier("hunter")
        assert isinstance(v, HunterVerifier)

    def test_hunter_missing_key_raises(self, monkeypatch):
        monkeypatch.delenv("HUNTER_API_KEY", raising=False)
        with pytest.raises(ValueError, match="API key"):
            get_verifier("hunter", api_key="")

    def test_unknown_provider_raises(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            get_verifier("zerobounce")

    def test_case_insensitive_provider_name(self):
        v = get_verifier("MOCK")
        assert isinstance(v, MockVerifier)
