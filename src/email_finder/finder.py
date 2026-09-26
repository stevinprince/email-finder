"""
Top-level orchestration: the main importable API for email-finder.

This module wires together all the building blocks — domain resolution,
name parsing, permutation generation, SMTP probing, and third-party API
verification — into a single, easy-to-use function.

Typical usage (Python API)
--------------------------
>>> from email_finder.finder import find_emails, FinderConfig
>>>
>>> # List permutations only (no verification)
>>> result = find_emails("Jane Doe", domain="acme.com")
>>> for c in result.candidates:
...     print(c.email)
...
>>> # With SMTP + Hunter.io verification
>>> config = FinderConfig(run_smtp=True, run_api=True, api_key="your-hunter-key")
>>> result = find_emails("Jane Doe", company="Acme Corp", config=config)
>>> best = result.candidates[0]
>>> print(best.email, best.confidence, best.verdict)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from email_finder.api_verifier import ApiResult, BaseApiVerifier, get_verifier
from email_finder.combiner import combine
from email_finder.domain import resolve_domain
from email_finder.name_parser import ParsedName, parse_name
from email_finder.permutations import generate_permutations
from email_finder.smtp_verifier import SmtpResult, verify_smtp


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class FinderConfig:
    """All tunable parameters for a :func:`find_emails` call.

    All fields have sensible defaults so you only need to set what you care
    about.
    """

    # ── SMTP verification ─────────────────────────────────────────────────────
    run_smtp: bool = False
    """Set ``True`` to run an SMTP-level probe on every candidate."""

    smtp_timeout: float = 10.0
    """Per-connection / per-command SMTP timeout in seconds."""

    smtp_delay: float = 1.0
    """Seconds to sleep between consecutive SMTP probes.
    Keep this at ≥ 1 s to avoid triggering rate-limits."""

    smtp_max_retries: int = 1
    """Number of retries on transient SMTP errors (timeout / disconnect)."""

    smtp_from_address: str = "verify@example.com"
    """Address used in the SMTP ``MAIL FROM`` command."""

    # ── API verification ──────────────────────────────────────────────────────
    run_api: bool = False
    """Set ``True`` to run a third-party API verification on every candidate."""

    api_provider: str = "hunter"
    """Provider name: ``'hunter'`` (Hunter.io) or ``'mock'`` (for testing)."""

    api_key: str = ""
    """API key for the chosen provider.  Omit to read from the environment
    (e.g. ``HUNTER_API_KEY`` for Hunter.io)."""

    api_timeout: float = 15.0
    """HTTP timeout for API calls in seconds."""


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class EmailResult:
    """Verification outcome for a single candidate email address."""

    email: str
    """Full email address (e.g. ``'jane.doe@acme.com'``)."""

    local_part: str
    """Local part before the ``@``."""

    pattern: str
    """Pattern name that produced this address (e.g. ``'first.last'``)."""

    pattern_rank: int
    """Static frequency rank of the pattern (1 = most common)."""

    confidence: int = 0
    """Combined confidence score 0–100.  ``0`` when no verification was run."""

    verdict: str = "unverified"
    """``'valid'``, ``'uncertain'``, ``'invalid'``, or ``'unverified'``."""

    smtp: SmtpResult | None = None
    """SMTP probe result, or ``None`` if SMTP verification was not run."""

    api: ApiResult | None = None
    """API verification result, or ``None`` if no API was used."""


@dataclass
class FinderResult:
    """Complete output of a :func:`find_emails` call."""

    name: str
    """The raw name string that was queried."""

    company: str | None
    """Company name supplied by the caller, if any."""

    parsed_name: ParsedName
    """Structured name components."""

    domain: str
    """The resolved corporate domain (e.g. ``'acme.com'``)."""

    domain_method: str
    """How the domain was obtained: ``'provided'`` or ``'heuristic'``."""

    domain_confidence: str
    """Domain confidence level: ``'high'`` or ``'low'``."""

    domain_notes: list[str]
    """Warnings / suggestions about domain resolution accuracy."""

    candidates: list[EmailResult]
    """Candidate email addresses.  Sorted by *confidence* descending when
    verification was run; by *pattern_rank* ascending otherwise."""

    verified_with: list[str] = field(default_factory=list)
    """Names of the verification methods that were used
    (e.g. ``['smtp', 'hunter.io']``)."""


# ---------------------------------------------------------------------------
# Main API
# ---------------------------------------------------------------------------

def find_emails(
    name: str,
    *,
    company: str | None = None,
    domain: str | None = None,
    config: FinderConfig | None = None,
) -> FinderResult:
    """Find and optionally verify professional email addresses.

    This is the primary importable entry point for the library.

    Args:
        name:    Full name of the person (e.g. ``"Dr. Jane Doe PhD"``).
        company: Company name, used for domain resolution when *domain* is
                 not supplied (e.g. ``"Acme Corp"``).
        domain:  Corporate domain.  When provided, domain resolution is
                 skipped entirely (e.g. ``"acme.com"``).
        config:  :class:`FinderConfig` controlling verification behaviour.
                 Pass ``None`` (default) to only generate permutations without
                 running any network calls.

    Returns:
        A :class:`FinderResult` containing the domain, parsed name, and a
        list of :class:`EmailResult` objects — one per candidate address —
        sorted by confidence descending (or by pattern rank when unverified).

    Raises:
        ValueError: When neither *company* nor *domain* is supplied.

    Example::

        result = find_emails("Jane Doe", domain="acme.com")
        for c in result.candidates:
            print(c.email)
    """
    if not company and not domain:
        raise ValueError("Provide at least one of 'company' or 'domain'.")

    if config is None:
        config = FinderConfig()

    # ── 1. Domain resolution ──────────────────────────────────────────────────
    domain_result = resolve_domain(company=company, domain=domain)

    # ── 2. Name parsing ───────────────────────────────────────────────────────
    parsed = parse_name(name)

    # ── 3. Permutation generation ─────────────────────────────────────────────
    permutations = generate_permutations(parsed, domain_result.domain)

    # ── 4. Verifier setup ─────────────────────────────────────────────────────
    api_verifier: BaseApiVerifier | None = None
    if config.run_api:
        api_verifier = get_verifier(config.api_provider, api_key=config.api_key)

    verified_with: list[str] = []
    if config.run_smtp:
        verified_with.append("smtp")
    if api_verifier:
        verified_with.append(api_verifier.provider_name)

    run_verification = config.run_smtp or bool(api_verifier)

    # ── 5. Per-candidate verification ─────────────────────────────────────────
    email_results: list[EmailResult] = []

    for idx, candidate in enumerate(permutations):
        if run_verification and idx > 0:
            time.sleep(config.smtp_delay)

        smtp_result: SmtpResult | None = None
        api_result:  ApiResult  | None = None

        if config.run_smtp:
            smtp_result = verify_smtp(
                candidate.address,
                from_address=config.smtp_from_address,
                timeout=config.smtp_timeout,
                max_retries=config.smtp_max_retries,
            )

        if api_verifier:
            api_result = api_verifier.verify(candidate.address)

        confidence = 0
        verdict    = "unverified"
        if smtp_result or api_result:
            combined   = combine(candidate.address, smtp=smtp_result, api=api_result)
            confidence = combined.confidence
            verdict    = combined.verdict

        email_results.append(
            EmailResult(
                email=candidate.address,
                local_part=candidate.local_part,
                pattern=candidate.pattern,
                pattern_rank=candidate.rank,
                confidence=confidence,
                verdict=verdict,
                smtp=smtp_result,
                api=api_result,
            )
        )

    # ── 6. Sort ───────────────────────────────────────────────────────────────
    if run_verification:
        # Highest confidence first; break ties by pattern frequency rank
        email_results.sort(key=lambda r: (-r.confidence, r.pattern_rank))

    return FinderResult(
        name=name,
        company=company,
        parsed_name=parsed,
        domain=domain_result.domain,
        domain_method=domain_result.method,
        domain_confidence=domain_result.confidence,
        domain_notes=domain_result.notes,
        candidates=email_results,
        verified_with=verified_with,
    )


# ---------------------------------------------------------------------------
# JSON serialisation
# ---------------------------------------------------------------------------

def result_to_dict(result: FinderResult) -> dict[str, Any]:
    """Convert a :class:`FinderResult` to a plain JSON-serialisable dict.

    Args:
        result: The result to serialise.

    Returns:
        A nested dict suitable for ``json.dumps()``.
    """
    pn = result.parsed_name

    def _smtp(s: SmtpResult | None) -> dict | None:
        if s is None:
            return None
        return {
            "status":    s.status.value,
            "smtp_code": s.smtp_code,
            "mx_host":   s.mx_host,
            "detail":    s.detail,
            "attempts":  s.attempts,
        }

    def _api(a: ApiResult | None) -> dict | None:
        if a is None:
            return None
        return {
            "status":     a.status.value,
            "provider":   a.provider,
            "score":      a.score,
            "raw_status": a.raw_status,
            "detail":     a.detail,
            "extra":      a.extra,
        }

    return {
        "query": {
            "name":    result.name,
            "company": result.company,
        },
        "parsed_name": {
            "first":    pn.first,
            "last":     pn.last,
            "middle":   pn.middle,
            "prefix":   pn.prefix,
            "suffix":   pn.suffix,
            "original": pn.original,
        },
        "domain": {
            "value":      result.domain,
            "method":     result.domain_method,
            "confidence": result.domain_confidence,
            "notes":      result.domain_notes,
        },
        "verified_with": result.verified_with,
        "candidates": [
            {
                "result_rank":   i + 1,
                "email":         c.email,
                "pattern":       c.pattern,
                "pattern_rank":  c.pattern_rank,
                "confidence":    c.confidence,
                "verdict":       c.verdict,
                "smtp":          _smtp(c.smtp),
                "api":           _api(c.api),
            }
            for i, c in enumerate(result.candidates)
        ],
    }


def result_to_json(result: FinderResult, *, indent: int = 2) -> str:
    """Serialise a :class:`FinderResult` to a JSON string.

    Args:
        result: The result to serialise.
        indent: JSON indentation level (default 2).

    Returns:
        A formatted JSON string.
    """
    return json.dumps(result_to_dict(result), indent=indent, ensure_ascii=False)
