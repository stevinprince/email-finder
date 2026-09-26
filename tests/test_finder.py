"""Tests for the orchestration layer (Step 6)."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from unittest.mock import patch

import pytest

from email_finder.api_verifier import ApiResult, ApiStatus, MockVerifier
from email_finder.finder import (
    EmailResult,
    FinderConfig,
    FinderResult,
    find_emails,
    result_to_dict,
    result_to_json,
)
from email_finder.smtp_verifier import SmtpResult, SmtpStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _smtp_valid(email: str) -> SmtpResult:
    return SmtpResult(email=email, status=SmtpStatus.VALID,
                      smtp_code=250, mx_host="mail.acme.com", attempts=1)

def _smtp_invalid(email: str) -> SmtpResult:
    return SmtpResult(email=email, status=SmtpStatus.INVALID,
                      smtp_code=550, mx_host="mail.acme.com", attempts=1)

def _smtp_unknown(email: str) -> SmtpResult:
    return SmtpResult(email=email, status=SmtpStatus.UNKNOWN,
                      mx_host="mail.acme.com", attempts=1)


# ---------------------------------------------------------------------------
# find_emails — basic contract (no verification)
# ---------------------------------------------------------------------------

class TestFindEmailsBasic:
    def test_returns_finder_result(self):
        r = find_emails("Jane Doe", domain="acme.com")
        assert isinstance(r, FinderResult)

    def test_name_preserved(self):
        r = find_emails("Jane Doe", domain="acme.com")
        assert r.name == "Jane Doe"

    def test_company_preserved(self):
        r = find_emails("Jane Doe", company="Acme Corp", domain="acme.com")
        assert r.company == "Acme Corp"

    def test_domain_resolved(self):
        r = find_emails("Jane Doe", domain="acme.com")
        assert r.domain == "acme.com"
        assert r.domain_method == "provided"

    def test_domain_from_company(self):
        r = find_emails("Jane Doe", company="Acme Corp")
        assert r.domain == "acme.com"
        assert r.domain_method == "heuristic"

    def test_candidates_non_empty(self):
        r = find_emails("Jane Doe", domain="acme.com")
        assert len(r.candidates) > 0

    def test_each_candidate_is_email_result(self):
        r = find_emails("Jane Doe", domain="acme.com")
        assert all(isinstance(c, EmailResult) for c in r.candidates)

    def test_no_verification_verdict_is_unverified(self):
        r = find_emails("Jane Doe", domain="acme.com")
        assert all(c.verdict == "unverified" for c in r.candidates)

    def test_no_verification_smtp_is_none(self):
        r = find_emails("Jane Doe", domain="acme.com")
        assert all(c.smtp is None for c in r.candidates)

    def test_no_verification_api_is_none(self):
        r = find_emails("Jane Doe", domain="acme.com")
        assert all(c.api is None for c in r.candidates)

    def test_no_verification_sorted_by_pattern_rank(self):
        r = find_emails("Jane Doe", domain="acme.com")
        ranks = [c.pattern_rank for c in r.candidates]
        assert ranks == sorted(ranks)

    def test_missing_company_and_domain_raises(self):
        with pytest.raises(ValueError, match="company.*domain|domain.*company"):
            find_emails("Jane Doe")

    def test_verified_with_empty_when_no_verification(self):
        r = find_emails("Jane Doe", domain="acme.com")
        assert r.verified_with == []

    def test_default_config_used_when_none(self):
        r = find_emails("Jane Doe", domain="acme.com", config=None)
        assert r.verified_with == []


# ---------------------------------------------------------------------------
# find_emails — with SMTP verification (mocked)
# ---------------------------------------------------------------------------

class TestFindEmailsSmtp:
    DOMAIN = "acme.com"

    def _run_smtp(self, smtp_status: SmtpStatus) -> FinderResult:
        """Run find_emails with SMTP mocked to always return *smtp_status*."""
        mock_result = SmtpResult(
            email="x@acme.com",
            status=smtp_status,
            smtp_code=250 if smtp_status == SmtpStatus.VALID else 550,
            mx_host="mail.acme.com",
            attempts=1,
        )
        def fake_verify(email, **kwargs):
            return SmtpResult(email=email, status=smtp_status,
                              smtp_code=mock_result.smtp_code,
                              mx_host="mail.acme.com", attempts=1)

        config = FinderConfig(run_smtp=True, smtp_delay=0.0, smtp_max_retries=0)
        with patch("email_finder.finder.verify_smtp", side_effect=fake_verify):
            with patch("email_finder.finder.time.sleep"):
                return find_emails("Jane Doe", domain=self.DOMAIN, config=config)

    def test_smtp_result_present_on_candidates(self):
        r = self._run_smtp(SmtpStatus.VALID)
        assert all(c.smtp is not None for c in r.candidates)

    def test_smtp_valid_gives_nonzero_confidence(self):
        r = self._run_smtp(SmtpStatus.VALID)
        assert all(c.confidence > 0 for c in r.candidates)

    def test_smtp_valid_verdict_is_valid(self):
        r = self._run_smtp(SmtpStatus.VALID)
        assert all(c.verdict == "valid" for c in r.candidates)

    def test_smtp_invalid_verdict_is_invalid(self):
        r = self._run_smtp(SmtpStatus.INVALID)
        assert all(c.verdict == "invalid" for c in r.candidates)

    def test_verified_with_contains_smtp(self):
        r = self._run_smtp(SmtpStatus.VALID)
        assert "smtp" in r.verified_with

    def test_sorted_by_confidence_descending(self):
        r = self._run_smtp(SmtpStatus.VALID)
        confs = [c.confidence for c in r.candidates]
        assert confs == sorted(confs, reverse=True)


# ---------------------------------------------------------------------------
# find_emails — with API verification (MockVerifier)
# ---------------------------------------------------------------------------

class TestFindEmailsApi:
    DOMAIN = "acme.com"

    def _run_api(self, api_status: ApiStatus, score: int = 85) -> FinderResult:
        config = FinderConfig(
            run_api=True,
            api_provider="mock",
            smtp_delay=0.0,
        )
        mock_v = MockVerifier(default_status=api_status, default_score=score)
        with patch("email_finder.finder.get_verifier", return_value=mock_v):
            with patch("email_finder.finder.time.sleep"):
                return find_emails("Jane Doe", domain=self.DOMAIN, config=config)

    def test_api_result_present_on_candidates(self):
        r = self._run_api(ApiStatus.VALID)
        assert all(c.api is not None for c in r.candidates)

    def test_api_valid_gives_nonzero_confidence(self):
        r = self._run_api(ApiStatus.VALID)
        assert all(c.confidence > 0 for c in r.candidates)

    def test_api_invalid_gives_low_confidence(self):
        # score=8 matches a realistic invalid response; combiner uses provider score
        r = self._run_api(ApiStatus.INVALID, score=8)
        assert all(c.confidence <= 25 for c in r.candidates)

    def test_verified_with_contains_provider_name(self):
        r = self._run_api(ApiStatus.VALID)
        assert "mock" in r.verified_with

    def test_sorted_by_confidence_descending(self):
        r = self._run_api(ApiStatus.VALID)
        confs = [c.confidence for c in r.candidates]
        assert confs == sorted(confs, reverse=True)


# ---------------------------------------------------------------------------
# find_emails — mixed confidence sorting
# ---------------------------------------------------------------------------

class TestFindEmailsSorting:
    def test_high_confidence_candidate_ranked_first(self):
        """When different candidates get different SMTP results, the
        high-confidence one should appear first in the output."""
        call_count = 0

        def fake_verify(email, **kwargs):
            nonlocal call_count
            call_count += 1
            # Make only the very first candidate VALID, rest UNKNOWN
            status = SmtpStatus.VALID if call_count == 1 else SmtpStatus.UNKNOWN
            return SmtpResult(email=email, status=status, mx_host="mx.acme.com",
                              smtp_code=250 if status == SmtpStatus.VALID else None,
                              attempts=1)

        config = FinderConfig(run_smtp=True, smtp_delay=0.0)
        with patch("email_finder.finder.verify_smtp", side_effect=fake_verify):
            with patch("email_finder.finder.time.sleep"):
                r = find_emails("Jane Doe", domain="acme.com", config=config)

        # First candidate should have the highest confidence (the VALID one)
        assert r.candidates[0].confidence >= r.candidates[1].confidence

    def test_no_verification_preserves_pattern_rank_order(self):
        r = find_emails("Jane Doe", domain="acme.com")
        ranks = [c.pattern_rank for c in r.candidates]
        assert ranks == sorted(ranks)


# ---------------------------------------------------------------------------
# result_to_dict / result_to_json
# ---------------------------------------------------------------------------

class TestSerialization:
    def _result(self) -> FinderResult:
        return find_emails("Jane Doe", domain="acme.com")

    def test_result_to_dict_returns_dict(self):
        assert isinstance(result_to_dict(self._result()), dict)

    def test_dict_has_required_keys(self):
        d = result_to_dict(self._result())
        assert "query" in d
        assert "parsed_name" in d
        assert "domain" in d
        assert "candidates" in d
        assert "verified_with" in d

    def test_query_block(self):
        d = result_to_dict(find_emails("Jane Doe", company="Acme Corp",
                                       domain="acme.com"))
        assert d["query"]["name"] == "Jane Doe"
        assert d["query"]["company"] == "Acme Corp"

    def test_parsed_name_block(self):
        d = result_to_dict(self._result())
        pn = d["parsed_name"]
        assert pn["first"] == "Jane"
        assert pn["last"] == "Doe"

    def test_domain_block(self):
        d = result_to_dict(self._result())
        assert d["domain"]["value"] == "acme.com"
        assert d["domain"]["method"] == "provided"

    def test_candidates_are_list(self):
        d = result_to_dict(self._result())
        assert isinstance(d["candidates"], list)
        assert len(d["candidates"]) > 0

    def test_candidate_has_required_fields(self):
        d = result_to_dict(self._result())
        c = d["candidates"][0]
        for key in ("result_rank", "email", "pattern", "pattern_rank",
                    "confidence", "verdict", "smtp", "api"):
            assert key in c, f"Missing key: {key}"

    def test_smtp_null_when_not_run(self):
        d = result_to_dict(self._result())
        assert all(c["smtp"] is None for c in d["candidates"])

    def test_api_null_when_not_run(self):
        d = result_to_dict(self._result())
        assert all(c["api"] is None for c in d["candidates"])

    def test_result_to_json_is_valid_json(self):
        json_str = result_to_json(self._result())
        parsed = json.loads(json_str)
        assert "candidates" in parsed

    def test_json_contains_email_addresses(self):
        json_str = result_to_json(self._result())
        assert "jane.doe@acme.com" in json_str

    def test_smtp_block_when_verified(self):
        def fake_verify(email, **kwargs):
            return SmtpResult(email=email, status=SmtpStatus.VALID,
                              smtp_code=250, mx_host="mail.acme.com", attempts=1)
        config = FinderConfig(run_smtp=True, smtp_delay=0.0)
        with patch("email_finder.finder.verify_smtp", side_effect=fake_verify):
            with patch("email_finder.finder.time.sleep"):
                r = find_emails("Jane Doe", domain="acme.com", config=config)
        d = result_to_dict(r)
        smtp_block = d["candidates"][0]["smtp"]
        assert smtp_block is not None
        assert smtp_block["status"] == "valid"
        assert smtp_block["smtp_code"] == 250

    def test_json_is_ascii_safe(self):
        """ensure_ascii=False — unicode names should still parse."""
        r = find_emails("José García", domain="example.com")
        json_str = result_to_json(r)
        parsed = json.loads(json_str)
        assert parsed["query"]["name"] == "José García"

    def test_output_to_file(self, tmp_path: Path):
        r = self._result()
        out = tmp_path / "result.json"
        out.write_text(result_to_json(r), encoding="utf-8")
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["domain"]["value"] == "acme.com"


# ---------------------------------------------------------------------------
# FinderConfig defaults
# ---------------------------------------------------------------------------

class TestFinderConfig:
    def test_defaults_are_safe(self):
        c = FinderConfig()
        assert c.run_smtp is False
        assert c.run_api is False
        assert c.smtp_delay >= 1.0  # polite default
        assert c.smtp_timeout > 0
        assert c.api_timeout > 0

    def test_custom_values_set(self):
        c = FinderConfig(run_smtp=True, smtp_delay=2.5, api_provider="mock")
        assert c.run_smtp is True
        assert c.smtp_delay == 2.5
        assert c.api_provider == "mock"
