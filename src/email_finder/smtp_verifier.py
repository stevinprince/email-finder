"""
SMTP-level email verification (verification method #1).

Performs an MX record lookup followed by a partial SMTP handshake
(EHLO → MAIL FROM → RCPT TO) to check deliverability without
sending an actual message.

┌─────────────────────────────────────────────────────────────────┐
│  ⚠  PLEASE READ BEFORE USING AT SCALE                          │
│                                                                 │
│  • Many ISPs and cloud providers (AWS, GCP, Azure, etc.)        │
│    block outbound port 25 to prevent spam.  On such hosts you   │
│    will receive ERROR / connection refused on every check.      │
│                                                                 │
│  • Mail servers log every RCPT TO probe.  Sending many probes   │
│    quickly will get your IP rate-limited or blacklisted.        │
│    Use the built-in delay parameters and keep volume low.       │
│                                                                 │
│  • A VALID result is a strong positive signal but NOT a         │
│    guarantee — some servers accept all RCPT TO and only         │
│    bounce later.  A CATCH_ALL result means we cannot tell.      │
│                                                                 │
│  • An UNKNOWN result (4xx, timeout, greylisting) must NOT be    │
│    treated as INVALID.                                          │
└─────────────────────────────────────────────────────────────────┘

Public API
----------
    lookup_mx(domain)          → list of MX hostnames (sorted by priority)
    verify_smtp(email, ...)    → SmtpResult
"""

from __future__ import annotations

import random
import smtplib
import socket
import string
import time
from dataclasses import dataclass, field
from enum import Enum

import dns.exception
import dns.resolver


# ---------------------------------------------------------------------------
# Status enum
# ---------------------------------------------------------------------------

class SmtpStatus(str, Enum):
    """Possible outcomes of an SMTP verification probe."""

    VALID     = "valid"      # Server accepted RCPT TO with 250
    INVALID   = "invalid"    # Server permanently rejected the address (5xx)
    CATCH_ALL = "catch-all"  # Domain accepts mail for any local-part
    UNKNOWN   = "unknown"    # Temporary failure, greylisting, or ambiguous reply
    ERROR     = "error"      # Technical failure (DNS, blocked port, bad format)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class SmtpResult:
    """Outcome of a single SMTP verification attempt."""

    email: str
    """The email address that was probed."""

    status: SmtpStatus
    """Verification outcome."""

    smtp_code: int | None = None
    """The final SMTP response code received from the server, if any."""

    mx_host: str | None = None
    """The MX host that was contacted, if a connection was attempted."""

    detail: str = ""
    """Human-readable explanation of the result."""

    attempts: int = 0
    """Number of connection attempts made (including retries)."""


# ---------------------------------------------------------------------------
# MX lookup
# ---------------------------------------------------------------------------

def lookup_mx(domain: str, *, timeout: float = 10.0) -> list[str]:
    """Return MX hostnames for *domain*, sorted by priority (lowest first).

    Args:
        domain:  The domain part of an email address (e.g. ``'acme.com'``).
        timeout: DNS query lifetime in seconds.

    Returns:
        A list of MX hostnames.  Empty list if the domain has no MX records.

    Raises:
        dns.exception.DNSException: On resolver errors other than NXDOMAIN /
            NoAnswer (e.g. network unreachable, SERVFAIL).
    """
    resolver = dns.resolver.Resolver()
    resolver.lifetime = timeout

    try:
        answers = resolver.resolve(domain, "MX")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    # Let other dns.exception.DNSException subclasses propagate to the caller.

    records = sorted(answers, key=lambda r: r.preference)
    return [str(r.exchange).rstrip(".") for r in records]


# ---------------------------------------------------------------------------
# SMTP probe internals
# ---------------------------------------------------------------------------

_SMTP_PORT = 25


def _probe(
    email: str,
    domain: str,
    mx_host: str,
    from_address: str,
    timeout: float,
) -> SmtpResult:
    """Open a single SMTP connection and run the handshake.

    Raises:
        socket.timeout: Connection or command timed out.
        smtplib.SMTPServerDisconnected: Server closed the connection early.
        smtplib.SMTPConnectError: Could not connect (e.g. transient).
        ConnectionRefusedError: Port 25 is blocked / not listening.
        OSError: Other network-level errors.
    """
    with smtplib.SMTP(mx_host, _SMTP_PORT, timeout=timeout) as smtp:
        # ── EHLO (fall back to HELO for legacy servers) ───────────────────
        code, _ = smtp.ehlo()
        if code >= 400:
            code, _ = smtp.helo()
            if code >= 400:
                return SmtpResult(
                    email=email,
                    status=SmtpStatus.UNKNOWN,
                    smtp_code=code,
                    mx_host=mx_host,
                    detail=f"Server rejected EHLO/HELO with code {code}.",
                )

        # ── MAIL FROM ─────────────────────────────────────────────────────
        code, _ = smtp.mail(from_address)
        if code >= 400:
            return SmtpResult(
                email=email,
                status=SmtpStatus.UNKNOWN,
                smtp_code=code,
                mx_host=mx_host,
                detail=f"Server rejected MAIL FROM with code {code}.",
            )

        # ── Catch-all detection ───────────────────────────────────────────
        # Send RCPT TO with a random address that almost certainly does not
        # exist.  If the server accepts it (250), the domain is catch-all.
        random_local = "".join(
            random.choices(string.ascii_lowercase + string.digits, k=20)
        )
        probe_addr = f"{random_local}@{domain}"
        catch_code, _ = smtp.rcpt(probe_addr)
        smtp.rset()  # reset transaction before the real RCPT TO

        if catch_code == 250:
            return SmtpResult(
                email=email,
                status=SmtpStatus.CATCH_ALL,
                smtp_code=250,
                mx_host=mx_host,
                detail=(
                    f"'{domain}' is a catch-all domain — it accepts mail for "
                    "any local-part.  Cannot confirm whether this specific "
                    "address exists."
                ),
            )

        # ── Real RCPT TO ──────────────────────────────────────────────────
        smtp.mail(from_address)   # restart transaction after rset
        code, msg = smtp.rcpt(email)
        return _interpret_rcpt_code(email, mx_host, code, msg)


def _interpret_rcpt_code(
    email: str,
    mx_host: str,
    code: int,
    msg: bytes,
) -> SmtpResult:
    """Map an SMTP RCPT TO response to a :class:`SmtpResult`."""
    msg_str = msg.decode(errors="replace").strip()

    if code == 250:
        return SmtpResult(
            email=email,
            status=SmtpStatus.VALID,
            smtp_code=code,
            mx_host=mx_host,
            detail=f"Server accepted the address ({code} {msg_str}).",
        )

    if code == 252:
        # "Cannot VRFY user but will accept message and attempt delivery"
        return SmtpResult(
            email=email,
            status=SmtpStatus.UNKNOWN,
            smtp_code=code,
            mx_host=mx_host,
            detail=(
                f"Server returned {code}: cannot verify but may accept. "
                "Deliverability is uncertain."
            ),
        )

    if code in (550, 551, 552, 553, 554):
        return SmtpResult(
            email=email,
            status=SmtpStatus.INVALID,
            smtp_code=code,
            mx_host=mx_host,
            detail=f"Server permanently rejected the address ({code} {msg_str}).",
        )

    if code in (421, 450, 451, 452):
        return SmtpResult(
            email=email,
            status=SmtpStatus.UNKNOWN,
            smtp_code=code,
            mx_host=mx_host,
            detail=(
                f"Server returned a temporary error ({code} {msg_str}). "
                "This may indicate greylisting or rate limiting — "
                "retry later before treating as invalid."
            ),
        )

    return SmtpResult(
        email=email,
        status=SmtpStatus.UNKNOWN,
        smtp_code=code,
        mx_host=mx_host,
        detail=f"Unexpected SMTP response code {code}: {msg_str}.",
    )


# ---------------------------------------------------------------------------
# Public verify function
# ---------------------------------------------------------------------------

def verify_smtp(
    email: str,
    *,
    from_address: str = "verify@example.com",
    timeout: float = 10.0,
    max_retries: int = 1,
    retry_delay: float = 2.0,
    dns_timeout: float = 10.0,
) -> SmtpResult:
    """Verify *email* via MX lookup + partial SMTP handshake.

    Args:
        email:        The address to probe (e.g. ``'jane.doe@acme.com'``).
        from_address: The address used in ``MAIL FROM``.  Should look
                      realistic; defaults to ``verify@example.com``.
        timeout:      Per-connection / per-command timeout in seconds.
        max_retries:  How many times to retry on transient errors (timeout,
                      server disconnect) before giving up.  Each retry doubles
                      the wait (exponential back-off starting at *retry_delay*).
        retry_delay:  Base delay in seconds between retries.
        dns_timeout:  DNS query lifetime in seconds.

    Returns:
        A :class:`SmtpResult` describing the outcome.

    Notes:
        * This function never raises — all errors are captured in the result.
        * ``UNKNOWN`` is not the same as ``INVALID``.  Treat it as
          "we could not determine deliverability" rather than "bad address".
        * Port 25 is frequently blocked by cloud providers.  If you get
          repeated ``ERROR`` results, that is the most likely cause.
    """
    # ── 1. Validate format ────────────────────────────────────────────────────
    if "@" not in email:
        return SmtpResult(
            email=email,
            status=SmtpStatus.ERROR,
            detail="Invalid email address: no '@' character found.",
        )
    _, domain = email.rsplit("@", 1)
    domain = domain.strip().lower()

    # ── 2. MX lookup ──────────────────────────────────────────────────────────
    try:
        mx_hosts = lookup_mx(domain, timeout=dns_timeout)
    except dns.exception.DNSException as exc:
        return SmtpResult(
            email=email,
            status=SmtpStatus.ERROR,
            detail=f"DNS resolution failed: {exc}",
        )

    if not mx_hosts:
        return SmtpResult(
            email=email,
            status=SmtpStatus.ERROR,
            detail=(
                f"No MX records found for '{domain}'. "
                "The domain may not accept email."
            ),
        )

    # ── 3. Try each MX host (highest-priority first) ──────────────────────────
    last_result: SmtpResult | None = None

    for mx_host in mx_hosts:
        for attempt in range(max_retries + 1):
            if attempt > 0:
                time.sleep(retry_delay * (2 ** (attempt - 1)))

            try:
                result = _probe(email, domain, mx_host, from_address, timeout)
                result.attempts = attempt + 1
                return result

            except socket.timeout:
                last_result = SmtpResult(
                    email=email,
                    status=SmtpStatus.UNKNOWN,
                    mx_host=mx_host,
                    attempts=attempt + 1,
                    detail=(
                        f"Connection to {mx_host}:{_SMTP_PORT} timed out "
                        f"(attempt {attempt + 1}/{max_retries + 1}). "
                        "This may indicate greylisting, server overload, "
                        "or an outbound port-25 block on your network."
                    ),
                )
                # Transient — retry

            except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError) as exc:
                last_result = SmtpResult(
                    email=email,
                    status=SmtpStatus.UNKNOWN,
                    mx_host=mx_host,
                    attempts=attempt + 1,
                    detail=(
                        f"SMTP connection problem on attempt "
                        f"{attempt + 1}/{max_retries + 1}: {exc}"
                    ),
                )
                # Transient — retry

            except ConnectionRefusedError:
                # Port 25 blocked or not listening — no point retrying this host
                last_result = SmtpResult(
                    email=email,
                    status=SmtpStatus.ERROR,
                    mx_host=mx_host,
                    attempts=attempt + 1,
                    detail=(
                        f"Connection to {mx_host}:{_SMTP_PORT} was refused. "
                        "Port 25 may be blocked by your ISP or cloud provider."
                    ),
                )
                break  # skip retries; try next MX host

            except OSError as exc:
                last_result = SmtpResult(
                    email=email,
                    status=SmtpStatus.ERROR,
                    mx_host=mx_host,
                    attempts=attempt + 1,
                    detail=f"Network error reaching {mx_host}: {exc}",
                )
                break  # skip retries; try next MX host

        # If we got a non-error result stop trying other MX hosts
        if last_result and last_result.status != SmtpStatus.ERROR:
            break

    return last_result or SmtpResult(
        email=email,
        status=SmtpStatus.ERROR,
        detail="All MX hosts failed without returning a usable result.",
    )
