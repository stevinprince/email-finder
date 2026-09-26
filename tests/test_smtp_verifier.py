"""Tests for SMTP-level verification (Step 4).

All network I/O (DNS + SMTP) is mocked — no real connections are made.
"""

from __future__ import annotations

import smtplib
import socket
from unittest.mock import MagicMock, call, patch

import dns.exception
import dns.resolver
import pytest

from email_finder.smtp_verifier import (
    SmtpResult,
    SmtpStatus,
    lookup_mx,
    verify_smtp,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mx_answer(hostnames: list[str]) -> list[MagicMock]:
    """Build fake dns.resolver answer records."""
    records = []
    for i, host in enumerate(hostnames):
        rec = MagicMock()
        rec.preference = (i + 1) * 10
        rec.exchange = MagicMock()
        rec.exchange.__str__ = lambda self, h=host: h + "."
        records.append(rec)
    return records


def _make_smtp_mock(
    ehlo_code: int = 250,
    mail_code: int = 250,
    rcpt_codes: list[int] | None = None,
    rset_code: int = 250,
) -> MagicMock:
    """Return a MagicMock that behaves like smtplib.SMTP context manager.

    *rcpt_codes* is consumed in order: first call uses rcpt_codes[0], second
    uses rcpt_codes[1], etc.  Defaults to [250, 250].
    """
    if rcpt_codes is None:
        rcpt_codes = [250, 250]

    smtp_instance = MagicMock()
    smtp_instance.ehlo.return_value = (ehlo_code, b"OK")
    smtp_instance.helo.return_value = (250, b"OK")
    smtp_instance.mail.return_value = (mail_code, b"OK")
    smtp_instance.rset.return_value = (rset_code, b"OK")

    # rcpt() is called multiple times; use side_effect to cycle through codes
    smtp_instance.rcpt.side_effect = [
        (code, f"Response {code}".encode()) for code in rcpt_codes
    ]

    # Make it work as a context manager
    smtp_instance.__enter__ = lambda s: smtp_instance
    smtp_instance.__exit__ = MagicMock(return_value=False)

    return smtp_instance


def _patch_dns(hosts: list[str]):
    """Patch dns.resolver.Resolver to return fake MX records."""
    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = _mx_answer(hosts)
    return patch("email_finder.smtp_verifier.dns.resolver.Resolver",
                 return_value=mock_resolver)


def _patch_smtp(smtp_mock: MagicMock):
    """Patch smtplib.SMTP to return *smtp_mock* as the context manager."""
    smtp_class = MagicMock(return_value=smtp_mock)
    return patch("email_finder.smtp_verifier.smtplib.SMTP", smtp_class)


# ---------------------------------------------------------------------------
# lookup_mx tests
# ---------------------------------------------------------------------------

class TestLookupMx:
    def test_returns_sorted_hostnames(self):
        mock_resolver = MagicMock()
        # Simulate two records returned in wrong priority order
        high = MagicMock(preference=20)
        high.exchange.__str__ = lambda s: "mail2.example.com."
        low = MagicMock(preference=10)
        low.exchange.__str__ = lambda s: "mail1.example.com."
        mock_resolver.resolve.return_value = [high, low]

        with patch("email_finder.smtp_verifier.dns.resolver.Resolver",
                   return_value=mock_resolver):
            hosts = lookup_mx("example.com")

        assert hosts == ["mail1.example.com", "mail2.example.com"]

    def test_nxdomain_returns_empty_list(self):
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = dns.resolver.NXDOMAIN()
        with patch("email_finder.smtp_verifier.dns.resolver.Resolver",
                   return_value=mock_resolver):
            assert lookup_mx("nonexistent.invalid") == []

    def test_no_answer_returns_empty_list(self):
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = dns.resolver.NoAnswer()
        with patch("email_finder.smtp_verifier.dns.resolver.Resolver",
                   return_value=mock_resolver):
            assert lookup_mx("nomx.example.com") == []

    def test_dns_exception_propagates(self):
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = dns.exception.DNSException("SERVFAIL")
        with patch("email_finder.smtp_verifier.dns.resolver.Resolver",
                   return_value=mock_resolver):
            with pytest.raises(dns.exception.DNSException):
                lookup_mx("broken.example.com")

    def test_trailing_dot_stripped(self):
        mock_resolver = MagicMock()
        rec = MagicMock(preference=10)
        rec.exchange.__str__ = lambda s: "mail.example.com."
        mock_resolver.resolve.return_value = [rec]
        with patch("email_finder.smtp_verifier.dns.resolver.Resolver",
                   return_value=mock_resolver):
            hosts = lookup_mx("example.com")
        assert hosts == ["mail.example.com"]


# ---------------------------------------------------------------------------
# verify_smtp — format / DNS errors
# ---------------------------------------------------------------------------

class TestVerifySmtpEarlyErrors:
    def test_email_without_at_returns_error(self):
        result = verify_smtp("notanemail")
        assert result.status == SmtpStatus.ERROR
        assert result.smtp_code is None

    def test_dns_failure_returns_error(self):
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = dns.exception.DNSException("SERVFAIL")
        with patch("email_finder.smtp_verifier.dns.resolver.Resolver",
                   return_value=mock_resolver):
            result = verify_smtp("jane@broken.example.com")
        assert result.status == SmtpStatus.ERROR
        assert "DNS" in result.detail

    def test_no_mx_records_returns_error(self):
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = dns.resolver.NoAnswer()
        with patch("email_finder.smtp_verifier.dns.resolver.Resolver",
                   return_value=mock_resolver):
            result = verify_smtp("jane@nomx.example.com")
        assert result.status == SmtpStatus.ERROR
        assert "MX" in result.detail


# ---------------------------------------------------------------------------
# verify_smtp — SMTP handshake outcomes
# ---------------------------------------------------------------------------

class TestVerifySmtpHandshake:
    EMAIL = "jane.doe@acme.com"
    MX    = ["mail.acme.com"]

    def _run(self, rcpt_codes: list[int], **smtp_kwargs) -> SmtpResult:
        smtp_mock = _make_smtp_mock(rcpt_codes=rcpt_codes, **smtp_kwargs)
        with _patch_dns(self.MX), _patch_smtp(smtp_mock):
            return verify_smtp(self.EMAIL, max_retries=0)

    # ── Successful probes ────────────────────────────────────────────────────

    def test_250_is_valid(self):
        # rcpt_codes[0]=550 (catch-all probe fails=good), rcpt_codes[1]=250 (real=valid)
        result = self._run(rcpt_codes=[550, 250])
        assert result.status == SmtpStatus.VALID
        assert result.smtp_code == 250
        assert result.mx_host == "mail.acme.com"

    def test_catch_all_detected(self):
        # Both random probe and real address return 250 → catch-all
        result = self._run(rcpt_codes=[250, 250])
        assert result.status == SmtpStatus.CATCH_ALL
        assert result.smtp_code == 250
        assert "catch-all" in result.detail.lower()

    # ── Permanent rejections ─────────────────────────────────────────────────

    @pytest.mark.parametrize("code", [550, 551, 552, 553, 554])
    def test_5xx_is_invalid(self, code: int):
        result = self._run(rcpt_codes=[550, code])  # catch-all probe=550, real=code
        assert result.status == SmtpStatus.INVALID
        assert result.smtp_code == code

    # ── Temporary / ambiguous responses ──────────────────────────────────────

    @pytest.mark.parametrize("code", [421, 450, 451, 452])
    def test_4xx_rcpt_is_unknown(self, code: int):
        result = self._run(rcpt_codes=[550, code])
        assert result.status == SmtpStatus.UNKNOWN
        assert result.smtp_code == code
        assert "greylisting" in result.detail.lower() or "temporary" in result.detail.lower()

    def test_252_is_unknown(self):
        result = self._run(rcpt_codes=[550, 252])
        assert result.status == SmtpStatus.UNKNOWN
        assert result.smtp_code == 252

    def test_mail_from_rejected_is_unknown(self):
        smtp_mock = _make_smtp_mock(mail_code=550, rcpt_codes=[550, 250])
        with _patch_dns(self.MX), _patch_smtp(smtp_mock):
            result = verify_smtp(self.EMAIL, max_retries=0)
        assert result.status == SmtpStatus.UNKNOWN
        assert "MAIL FROM" in result.detail

    def test_ehlo_failure_falls_back_to_helo(self):
        smtp_mock = _make_smtp_mock(ehlo_code=500, rcpt_codes=[550, 250])
        with _patch_dns(self.MX), _patch_smtp(smtp_mock):
            result = verify_smtp(self.EMAIL, max_retries=0)
        # helo fallback returns 250, so should reach RCPT TO and be valid
        assert result.status == SmtpStatus.VALID
        assert smtp_mock.helo.called

    # ── Network errors ───────────────────────────────────────────────────────

    def test_connection_refused_returns_error(self):
        smtp_class = MagicMock(side_effect=ConnectionRefusedError("port blocked"))
        with _patch_dns(self.MX):
            with patch("email_finder.smtp_verifier.smtplib.SMTP", smtp_class):
                result = verify_smtp(self.EMAIL, max_retries=0)
        assert result.status == SmtpStatus.ERROR
        assert "refused" in result.detail.lower() or "blocked" in result.detail.lower()

    def test_timeout_returns_unknown(self):
        smtp_class = MagicMock(side_effect=socket.timeout("timed out"))
        with _patch_dns(self.MX):
            with patch("email_finder.smtp_verifier.smtplib.SMTP", smtp_class):
                result = verify_smtp(self.EMAIL, max_retries=0)
        assert result.status == SmtpStatus.UNKNOWN
        assert "timed out" in result.detail.lower() or "timeout" in result.detail.lower()

    def test_os_error_returns_error(self):
        smtp_class = MagicMock(side_effect=OSError("network unreachable"))
        with _patch_dns(self.MX):
            with patch("email_finder.smtp_verifier.smtplib.SMTP", smtp_class):
                result = verify_smtp(self.EMAIL, max_retries=0)
        assert result.status == SmtpStatus.ERROR


# ---------------------------------------------------------------------------
# verify_smtp — retry and fallback behaviour
# ---------------------------------------------------------------------------

class TestVerifySmtpRetries:
    EMAIL = "jane.doe@acme.com"

    def test_retries_on_timeout(self):
        """First attempt times out; second succeeds — result should be VALID."""
        smtp_mock = _make_smtp_mock(rcpt_codes=[550, 250])

        smtp_class = MagicMock(
            side_effect=[socket.timeout("first attempt"), smtp_mock]
        )
        with _patch_dns(["mail.acme.com"]):
            with patch("email_finder.smtp_verifier.smtplib.SMTP", smtp_class):
                with patch("email_finder.smtp_verifier.time.sleep"):  # no real sleep
                    result = verify_smtp(self.EMAIL, max_retries=1, retry_delay=0)

        assert result.status == SmtpStatus.VALID
        assert result.attempts == 2

    def test_falls_back_to_second_mx_on_refusal(self):
        """First MX refuses; second MX succeeds."""
        smtp_mock = _make_smtp_mock(rcpt_codes=[550, 250])

        call_count = 0
        def smtp_side_effect(host, port, timeout):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionRefusedError("port blocked")
            return smtp_mock

        with _patch_dns(["primary.acme.com", "backup.acme.com"]):
            with patch("email_finder.smtp_verifier.smtplib.SMTP",
                       side_effect=smtp_side_effect):
                result = verify_smtp(self.EMAIL, max_retries=0)

        assert result.status == SmtpStatus.VALID
        assert result.mx_host == "backup.acme.com"

    def test_attempts_counted(self):
        smtp_mock = _make_smtp_mock(rcpt_codes=[550, 250])
        with _patch_dns(["mail.acme.com"]), _patch_smtp(smtp_mock):
            result = verify_smtp(self.EMAIL, max_retries=0)
        assert result.attempts == 1


# ---------------------------------------------------------------------------
# SmtpResult — basic dataclass checks
# ---------------------------------------------------------------------------

class TestSmtpResult:
    def test_default_fields(self):
        r = SmtpResult(email="x@y.com", status=SmtpStatus.UNKNOWN)
        assert r.smtp_code is None
        assert r.mx_host is None
        assert r.detail == ""
        assert r.attempts == 0

    def test_status_is_string_comparable(self):
        assert SmtpStatus.VALID == "valid"
        assert SmtpStatus.INVALID == "invalid"
