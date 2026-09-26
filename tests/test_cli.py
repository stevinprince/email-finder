"""CLI tests — smoke tests (Step 1) + feature tests for Steps 2–7."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from email_finder.api_verifier import ApiResult, ApiStatus, MockVerifier
from email_finder.cli import main
from email_finder.smtp_verifier import SmtpResult, SmtpStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _smtp(email: str, status: SmtpStatus = SmtpStatus.VALID) -> SmtpResult:
    return SmtpResult(
        email=email, status=status,
        smtp_code=250 if status == SmtpStatus.VALID else 550,
        mx_host="mail.acme.com", attempts=1,
    )

def _api(email: str, status: ApiStatus = ApiStatus.VALID) -> ApiResult:
    return ApiResult(email=email, status=status, provider="mock", score=85)


# ---------------------------------------------------------------------------
# Step 1 — basic scaffold tests
# ---------------------------------------------------------------------------

def test_help_exits_cleanly():
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "FULL_NAME" in result.output


def test_version_flag():
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_name_and_company_printed():
    result = CliRunner().invoke(main, ["--name", "Jane Doe", "--company", "Acme Corp"])
    assert result.exit_code == 0
    assert "Jane Doe" in result.output
    assert "Acme Corp" in result.output


def test_name_and_domain_printed():
    result = CliRunner().invoke(main, ["--name", "Jane Doe", "--domain", "acme.com"])
    assert result.exit_code == 0
    assert "Jane Doe" in result.output
    assert "acme.com" in result.output


def test_missing_company_and_domain_raises():
    result = CliRunner().invoke(main, ["--name", "Jane Doe"])
    assert result.exit_code != 0
    assert "company" in result.output.lower() or "domain" in result.output.lower()


# ---------------------------------------------------------------------------
# Step 3 — parsed name extras shown in output
# ---------------------------------------------------------------------------

def test_prefix_middle_suffix_shown():
    result = CliRunner().invoke(
        main, ["--name", "Dr. John Paul Smith Jr.", "--domain", "acme.com"]
    )
    assert result.exit_code == 0
    # The 'Parsed' line should show the extras
    assert "prefix=" in result.output or "middle=" in result.output or "suffix=" in result.output


def test_verified_with_shown_when_methods_used():
    with patch("email_finder.finder.verify_smtp",
               side_effect=lambda e, **kw: _smtp(e)), \
         patch("email_finder.rate_limiter.time.sleep"):
        result = CliRunner().invoke(
            main, ["--name", "Jane Doe", "--domain", "acme.com", "--smtp"]
        )
    assert result.exit_code == 0
    assert "smtp" in result.output.lower()


# ---------------------------------------------------------------------------
# Step 4 — SMTP verification output
# ---------------------------------------------------------------------------

def test_smtp_flag_shows_confidence_column():
    with patch("email_finder.finder.verify_smtp",
               side_effect=lambda e, **kw: _smtp(e)), \
         patch("email_finder.rate_limiter.time.sleep"):
        result = CliRunner().invoke(
            main, ["--name", "Jane Doe", "--domain", "acme.com", "--smtp"]
        )
    assert result.exit_code == 0
    assert "Conf" in result.output
    assert "/100" in result.output


def test_smtp_valid_shows_valid_verdict():
    with patch("email_finder.finder.verify_smtp",
               side_effect=lambda e, **kw: _smtp(e, SmtpStatus.VALID)), \
         patch("email_finder.rate_limiter.time.sleep"):
        result = CliRunner().invoke(
            main, ["--name", "Jane Doe", "--domain", "acme.com", "--smtp"]
        )
    assert "valid" in result.output.lower()


def test_smtp_invalid_shows_invalid_verdict():
    with patch("email_finder.finder.verify_smtp",
               side_effect=lambda e, **kw: _smtp(e, SmtpStatus.INVALID)), \
         patch("email_finder.rate_limiter.time.sleep"):
        result = CliRunner().invoke(
            main, ["--name", "Jane Doe", "--domain", "acme.com", "--smtp"]
        )
    assert "invalid" in result.output.lower()


# ---------------------------------------------------------------------------
# Step 5 — API verification output
# ---------------------------------------------------------------------------

def test_api_flag_mock_provider_shows_verdict():
    mock_v = MockVerifier(default_status=ApiStatus.VALID, default_score=90)
    with patch("email_finder.finder.get_verifier", return_value=mock_v), \
         patch("email_finder.rate_limiter.time.sleep"):
        result = CliRunner().invoke(
            main, ["--name", "Jane Doe", "--domain", "acme.com",
                   "--api", "--api-provider", "mock"]
        )
    assert result.exit_code == 0
    assert "Conf" in result.output


def test_smtp_and_api_both_shown():
    mock_v = MockVerifier(default_status=ApiStatus.VALID, default_score=90)
    with patch("email_finder.finder.verify_smtp",
               side_effect=lambda e, **kw: _smtp(e)), \
         patch("email_finder.finder.get_verifier", return_value=mock_v), \
         patch("email_finder.rate_limiter.time.sleep"):
        result = CliRunner().invoke(
            main, ["--name", "Jane Doe", "--domain", "acme.com",
                   "--smtp", "--api", "--api-provider", "mock"]
        )
    assert result.exit_code == 0
    # Both SMTP and API columns present
    assert "Conf" in result.output


# ---------------------------------------------------------------------------
# Step 6 — JSON and file output
# ---------------------------------------------------------------------------

def test_json_flag_outputs_valid_json():
    result = CliRunner().invoke(
        main, ["--name", "Jane Doe", "--domain", "acme.com", "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "candidates" in data
    assert data["domain"]["value"] == "acme.com"


def test_json_contains_name():
    result = CliRunner().invoke(
        main, ["--name", "Jane Doe", "--domain", "acme.com", "--json"]
    )
    data = json.loads(result.output)
    assert data["query"]["name"] == "Jane Doe"


def test_output_flag_writes_file(tmp_path: Path):
    out = str(tmp_path / "result.json")
    result = CliRunner().invoke(
        main, ["--name", "Jane Doe", "--domain", "acme.com", "--output", out]
    )
    assert result.exit_code == 0
    assert os.path.exists(out)
    data = json.loads(Path(out).read_text())
    assert "candidates" in data


def test_output_flag_also_prints_human_table(tmp_path: Path):
    out = str(tmp_path / "result.json")
    result = CliRunner().invoke(
        main, ["--name", "Jane Doe", "--domain", "acme.com", "--output", out]
    )
    assert "Parsed" in result.output   # human table rendered alongside file write


def test_output_flag_prints_confirmation(tmp_path: Path):
    out = str(tmp_path / "out.json")
    result = CliRunner().invoke(
        main, ["--name", "Jane Doe", "--domain", "acme.com", "--output", out]
    )
    assert "written" in result.output.lower()


# ---------------------------------------------------------------------------
# Step 7 — cache flags
# ---------------------------------------------------------------------------

def test_cache_flag_enables_memory_cache():
    """--cache should not crash and produces same human output."""
    result = CliRunner().invoke(
        main, ["--name", "Jane Doe", "--domain", "acme.com", "--cache"]
    )
    assert result.exit_code == 0
    assert "jane.doe@acme.com" in result.output


def test_cache_db_flag_creates_file(tmp_path: Path):
    db = str(tmp_path / "cache.db")
    result = CliRunner().invoke(
        main, ["--name", "Jane Doe", "--domain", "acme.com", "--cache-db", db]
    )
    assert result.exit_code == 0
    assert os.path.exists(db)


def test_cache_db_persists_between_runs(tmp_path: Path):
    """Two CLI runs sharing the same SQLite file: second sees cached results."""
    db = str(tmp_path / "shared.db")
    smtp_calls: list[str] = []

    def counting_verify(email, **kw):
        smtp_calls.append(email)
        return _smtp(email, SmtpStatus.VALID)

    flags = ["--name", "Jane Doe", "--domain", "acme.com",
             "--smtp", "--cache-db", db]

    with patch("email_finder.finder.verify_smtp", side_effect=counting_verify), \
         patch("email_finder.rate_limiter.time.sleep"):
        CliRunner().invoke(main, flags)
        first_run_calls = len(smtp_calls)

        smtp_calls.clear()
        CliRunner().invoke(main, flags)
        second_run_calls = len(smtp_calls)

    assert first_run_calls > 0,   "First run should make SMTP calls"
    assert second_run_calls == 0, "Second run should serve everything from cache"
