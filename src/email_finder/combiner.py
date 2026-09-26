"""
Result combiner: merge SMTP and API verification signals into a single
:class:`CombinedResult` with a 0–100 confidence score and a plain-English
verdict.

Confidence table
----------------
The table below encodes domain knowledge about how much to trust each
combination of signals.  Rows are the SMTP status; columns are the API
status.  When the API provider supplies its own score (0–100) that score
is used as the *base*, adjusted ±5 for SMTP agreement/conflict.

+-----------+--------+---------+---------+-------+--------+
| SMTP \\ API| valid  | invalid | unknown | error | (none) |
+-----------+--------+---------+---------+-------+--------+
| valid     |   92   |    45   |    70   |   65  |   65   |
| invalid   |   45   |     8   |    15   |   12  |   12   |
| catch-all |   80   |    20   |    40   |   40  |   40   |
| unknown   |   72   |    18   |    30   |   30  |   30   |
| error     |   70   |    15   |    20   |    0  |    0   |
| (none)    |   70   |    15   |    25   |    0  |    0   |
+-----------+--------+---------+---------+-------+--------+

Verdict thresholds
------------------
* score ≥ 70  →  ``"valid"``
* score ≥ 25  →  ``"uncertain"``
* score < 25  →  ``"invalid"``
"""

from __future__ import annotations

from dataclasses import dataclass, field

from email_finder.api_verifier import ApiResult, ApiStatus
from email_finder.smtp_verifier import SmtpResult, SmtpStatus


# ---------------------------------------------------------------------------
# Confidence table  (smtp_status_str, api_status_str) → base_score
# ---------------------------------------------------------------------------

_CONFIDENCE: dict[tuple[str | None, str | None], int] = {
    # (smtp,          api)         score
    ("valid",     "valid"):         92,
    ("valid",     "invalid"):       45,
    ("valid",     "unknown"):       70,
    ("valid",     "error"):         65,
    ("valid",     None):            70,
    ("invalid",   "valid"):         45,
    ("invalid",   "invalid"):        8,
    ("invalid",   "unknown"):       15,
    ("invalid",   "error"):         12,
    ("invalid",   None):            12,
    ("catch-all", "valid"):         80,
    ("catch-all", "invalid"):       20,
    ("catch-all", "unknown"):       40,
    ("catch-all", "error"):         40,
    ("catch-all", None):            40,
    ("unknown",   "valid"):         72,
    ("unknown",   "invalid"):       18,
    ("unknown",   "unknown"):       30,
    ("unknown",   "error"):         30,
    ("unknown",   None):            30,
    ("error",     "valid"):         70,
    ("error",     "invalid"):       15,
    ("error",     "unknown"):       20,
    ("error",     "error"):          0,
    ("error",     None):             0,
    (None,        "valid"):         70,
    (None,        "invalid"):       15,
    (None,        "unknown"):       25,
    (None,        "error"):          0,
    (None,        None):             0,
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class CombinedResult:
    """Merged outcome from SMTP and/or API verification.

    At least one of *smtp* or *api* will be non-None.
    """

    email: str
    """The address that was checked."""

    confidence: int
    """0–100 confidence score.  Higher = more likely deliverable."""

    verdict: str
    """Human-readable conclusion: ``'valid'``, ``'uncertain'``, or ``'invalid'``."""

    smtp: SmtpResult | None = None
    """SMTP probe result, or ``None`` if SMTP was not run."""

    api: ApiResult | None = None
    """API verification result, or ``None`` if no API was used."""

    explanation: str = ""
    """Plain-English explanation of how the confidence was calculated."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def combine(
    email: str,
    smtp: SmtpResult | None = None,
    api: ApiResult | None = None,
) -> CombinedResult:
    """Merge SMTP and API results into a single :class:`CombinedResult`.

    Args:
        email: The email address that was checked.
        smtp:  SMTP probe result (``None`` if not run).
        api:   API verification result (``None`` if not used).

    Returns:
        A :class:`CombinedResult` with a confidence score and verdict.

    Raises:
        ValueError: If both *smtp* and *api* are ``None``.
    """
    if smtp is None and api is None:
        raise ValueError("At least one of 'smtp' or 'api' must be provided.")

    smtp_key = smtp.status.value if smtp else None
    api_key  = api.status.value  if api  else None

    # ── Confidence score ──────────────────────────────────────────────────────
    base_score = _CONFIDENCE.get((smtp_key, api_key), 0)

    # If the API provided its own numeric score, use it as the base and
    # adjust ±5 for SMTP agreement/conflict.
    if api and api.score is not None:
        provider_score = max(0, min(100, api.score))
        if smtp:
            if (smtp.status == SmtpStatus.VALID   and api.status == ApiStatus.VALID) or \
               (smtp.status == SmtpStatus.INVALID and api.status == ApiStatus.INVALID):
                provider_score = min(100, provider_score + 5)   # signals agree → small boost
            elif smtp.status == SmtpStatus.VALID and api.status == ApiStatus.INVALID or \
                 smtp.status == SmtpStatus.INVALID and api.status == ApiStatus.VALID:
                provider_score = max(0, provider_score - 5)      # conflict → small penalty
        base_score = provider_score

    confidence = max(0, min(100, base_score))

    # ── Verdict ───────────────────────────────────────────────────────────────
    if confidence >= 70:
        verdict = "valid"
    elif confidence >= 25:
        verdict = "uncertain"
    else:
        verdict = "invalid"

    # ── Explanation ───────────────────────────────────────────────────────────
    parts: list[str] = []
    if smtp:
        parts.append(f"SMTP={smtp.status.value}")
    if api:
        score_note = f", provider_score={api.score}" if api.score is not None else ""
        parts.append(f"API={api.status.value}{score_note} via {api.provider}")
    explanation = (
        f"confidence={confidence}/100 ({verdict})  "
        + "  |  ".join(parts)
    )

    return CombinedResult(
        email=email,
        confidence=confidence,
        verdict=verdict,
        smtp=smtp,
        api=api,
        explanation=explanation,
    )
