"""
Domain resolution: map a company name to its most likely corporate domain.

Two resolution paths are supported:

1. **Provided** — the caller already has a domain; nothing to do.
2. **Heuristic** — strip common legal suffixes and punctuation from the
   company name, then guess ``<slug>.com``.  Confidence is *low*; the user
   should verify or supply ``--domain`` directly.
3. **API** (stub) — a future integration point for services like Clearbit
   Autocomplete that can look up the real domain.  Not yet wired in; set
   ``CLEARBIT_API_KEY`` in ``.env`` when that step is implemented.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class DomainResult:
    """Outcome of a domain-resolution attempt."""

    domain: str
    """The resolved domain (e.g. ``acme.com``)."""

    method: str
    """How the domain was obtained: ``'provided'``, ``'heuristic'``, or ``'api'``."""

    confidence: str
    """Rough confidence level: ``'high'``, ``'medium'``, or ``'low'``."""

    notes: list[str] = field(default_factory=list)
    """Human-readable caveats or suggestions about this result."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_domain(
    company: str | None = None,
    domain: str | None = None,
) -> DomainResult:
    """Resolve the corporate domain for a company.

    Args:
        company: Company name supplied by the user (e.g. ``"Acme Corp"``).
        domain:  Domain explicitly supplied by the user (e.g. ``"acme.com"``).
                 When provided, resolution is skipped entirely.

    Returns:
        A :class:`DomainResult` describing the resolved domain and how
        confident we are in it.

    Raises:
        ValueError: When neither *company* nor *domain* is provided.
    """
    if domain:
        # Normalise: strip leading/trailing whitespace and force lowercase.
        cleaned = domain.strip().lower().lstrip("@")
        return DomainResult(
            domain=cleaned,
            method="provided",
            confidence="high",
        )

    if not company:
        raise ValueError("At least one of 'company' or 'domain' must be supplied.")

    # Try the heuristic first.  In a future step we can fall back to an API
    # when the heuristic result looks dubious (e.g. slug is very long).
    return _heuristic_resolve(company)


# ---------------------------------------------------------------------------
# Resolution strategies
# ---------------------------------------------------------------------------

# Legal / generic suffixes that are almost never part of a domain name.
# Order matters: longer / more specific patterns first.
_SUFFIX_PATTERNS: list[str] = [
    r"incorporated",
    r"corporation",
    r"international",
    r"solutions",
    r"services",
    r"holdings",
    r"company",
    r"limited",
    r"group",
    r"corp",
    r"inc",
    r"llc",
    r"ltd",
    r"plc",
    r"gmbh",
    r"s\.a",
    r"co",
    r"the",
]

# Compiled once — each pattern is word-boundary-anchored and case-insensitive.
_SUFFIX_RE = re.compile(
    r"\b(" + "|".join(_SUFFIX_PATTERNS) + r")\b\.?",
    re.IGNORECASE,
)

# Standalone conjunctions / symbols to drop.
_CONJUNCTION_RE = re.compile(r"\b(and|&)\b", re.IGNORECASE)


def _heuristic_resolve(company: str) -> DomainResult:
    """Guess a domain from a company name using simple text normalisation.

    Strategy
    --------
    1. Strip leading/trailing whitespace.
    2. Remove standalone conjunctions (``and``, ``&``).
    3. Remove common legal suffixes (``Inc``, ``LLC``, ``Corp``, …).
    4. Remove any remaining punctuation except hyphens.
    5. Collapse whitespace; concatenate tokens into a single slug.
    6. Strip leading/trailing hyphens from the slug.
    7. Append ``.com``.

    Examples
    --------
    >>> _heuristic_resolve("Acme Corp").domain
    'acme.com'
    >>> _heuristic_resolve("McKinsey & Company").domain
    'mckinsey.com'
    >>> _heuristic_resolve("Coca-Cola").domain
    'coca-cola.com'
    """
    name = company.strip()

    # 1. Drop conjunctions.
    name = _CONJUNCTION_RE.sub(" ", name)

    # 2. Drop legal suffixes.
    name = _SUFFIX_RE.sub(" ", name)

    # 3. Remove punctuation except hyphens (preserve "Coca-Cola" → "coca-cola").
    name = re.sub(r"[^\w\s-]", "", name)

    # 4. Lowercase.
    name = name.lower()

    # 5. Collapse whitespace; join tokens (no separator — "General Electric" → "generalelectric").
    tokens = name.split()
    slug = "".join(tokens)

    # 6. Strip leading/trailing hyphens that might remain after suffix removal.
    slug = slug.strip("-")

    # 7. Replace any internal runs of hyphens with a single hyphen.
    slug = re.sub(r"-{2,}", "-", slug)

    if not slug:
        # Fallback: use the raw company name lowercased with spaces removed.
        slug = re.sub(r"\s+", "", company.lower())

    domain = f"{slug}.com"

    return DomainResult(
        domain=domain,
        method="heuristic",
        confidence="low",
        notes=[
            f"Domain '{domain}' was guessed from the company name using a simple "
            "heuristic (strip suffixes → slugify → .com). It may be wrong.",
            "For higher accuracy either pass --domain directly, or configure a "
            "lookup API (e.g. Clearbit Autocomplete) via CLEARBIT_API_KEY in .env.",
        ],
    )


def _api_resolve(company: str) -> DomainResult:  # noqa: ARG001
    """Stub: API-based domain resolution (not yet implemented).

    When implemented this will call a service such as Clearbit Autocomplete
    (``https://autocomplete.clearbit.com/v1/companies/suggest?query=<name>``)
    to return the canonical domain for a company.

    Args:
        company: Company name to look up.

    Raises:
        NotImplementedError: Always — this is a stub for a future step.
    """
    raise NotImplementedError(
        "API-based domain resolution is not yet implemented. "
        "It will be added in a future step; set CLEARBIT_API_KEY in .env to enable it."
    )
