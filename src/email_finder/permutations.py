"""
Email permutation generator.

Given a parsed name and a corporate domain, produces every standard
professional email pattern, deduplicates them, and returns the list
ranked from most-common to least-common (static heuristic ranking).

Typical usage
-------------
>>> from email_finder.name_parser import parse_name
>>> from email_finder.permutations import generate_permutations
>>> candidates = generate_permutations(parse_name("Jane Doe"), "acme.com")
>>> candidates[0].address
'jane.doe@acme.com'
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from email_finder.name_parser import ParsedName


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EmailCandidate:
    """A single candidate email address with metadata."""

    address: str
    """Full email address (e.g. ``'jane.doe@acme.com'``)."""

    local_part: str
    """The part before the ``@`` (e.g. ``'jane.doe'``)."""

    pattern: str
    """Human-readable pattern name (e.g. ``'first.last'``)."""

    rank: int
    """1-based rank — lower means more commonly seen in the wild."""


# ---------------------------------------------------------------------------
# Slug helper
# ---------------------------------------------------------------------------

def _slug(name_part: str) -> str:
    """Convert a raw name component to an ASCII email-safe token.

    Steps
    -----
    1. NFKD-decompose unicode so accented chars split into base + diacritic.
    2. Encode to ASCII and drop non-ASCII bytes (strips diacritics).
    3. Lowercase.
    4. Remove internal whitespace (collapses "van der Berg" → "vanderberg").
    5. Strip everything except ``[a-z0-9-]``.
    6. Strip any leading/trailing hyphens.

    Examples
    --------
    >>> _slug("Mary-Jane")
    'mary-jane'
    >>> _slug("O'Brien")
    'obrien'
    >>> _slug("van der Berg")
    'vanderberg'
    >>> _slug("José")
    'jose'
    """
    s = unicodedata.normalize("NFKD", name_part)
    s = s.encode("ascii", "ignore").decode("ascii")
    s = s.lower()
    s = re.sub(r"\s+", "", s)          # collapse spaces
    s = re.sub(r"[^a-z0-9-]", "", s)  # keep only safe chars
    s = s.strip("-")
    return s


# ---------------------------------------------------------------------------
# Pattern definitions
# ---------------------------------------------------------------------------
#
# Each entry: (rank, pattern_label, requires_first, requires_last, requires_middle)
# The actual local-part is assembled in generate_permutations() below.
#
# Ranking source: aggregated data from Hunter.io / various email-pattern studies.
# Patterns that need a component absent from the parsed name are silently skipped.

_PATTERNS_FL = [
    # (rank, label,         local_part_template)
    # fmt: off
    ( 1,  "first.last",    "{f}.{l}"),
    ( 2,  "flast",         "{fi}{l}"),
    ( 3,  "firstlast",     "{f}{l}"),
    ( 4,  "f.last",        "{fi}.{l}"),
    ( 5,  "first_last",    "{f}_{l}"),
    ( 6,  "last.first",    "{l}.{f}"),
    ( 7,  "last_first",    "{l}_{f}"),
    ( 8,  "lastfirst",     "{l}{f}"),
    ( 9,  "lastf",         "{l}{fi}"),
    (10,  "last.f",        "{l}.{fi}"),
    # fmt: on
]

_PATTERNS_FIRST_ONLY = [
    (11, "first", "{f}"),
]

_PATTERNS_LAST_ONLY = [
    (12, "last", "{l}"),
]

_PATTERNS_MIDDLE = [
    # Only emitted when middle is non-empty
    (13, "first.middle.last", "{f}.{m}.{l}"),
    (14, "fmlast",            "{fi}{mi}{l}"),
    (15, "f.m.last",          "{fi}.{mi}.{l}"),
    (16, "first.m.last",      "{f}.{mi}.{l}"),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_permutations(name: ParsedName, domain: str) -> list[EmailCandidate]:
    """Generate a deduplicated, ranked list of candidate email addresses.

    Args:
        name:   A :class:`~email_finder.name_parser.ParsedName` instance.
        domain: The corporate domain, e.g. ``'acme.com'``.

    Returns:
        A list of :class:`EmailCandidate` objects sorted by *rank* (ascending).
        Duplicate local-parts are deduplicated — the variant with the lower
        rank (more common pattern) is kept.

    Notes:
        * Patterns that require a name component that is absent (e.g. a middle
          name pattern when no middle name was supplied) are silently skipped.
        * A single-component name (e.g. ``"Madonna"`` → ``first="Madonna"``,
          ``last=""``) yields only the patterns that are satisfiable.
    """
    f  = _slug(name.first)
    l  = _slug(name.last)
    m  = _slug(name.middle)
    fi = f[:1]   # first initial
    li = l[:1]   # last initial  (unused in current patterns but kept for clarity)
    mi = m[:1]   # middle initial

    raw_candidates: list[tuple[int, str, str]] = []  # (rank, pattern, local_part)

    ctx = dict(f=f, l=l, m=m, fi=fi, li=li, mi=mi)

    # Patterns that need both first AND last
    if f and l:
        for rank, label, tmpl in _PATTERNS_FL:
            raw_candidates.append((rank, label, tmpl.format(**ctx)))

    # Patterns with middle name (need all three)
    if f and m and l:
        for rank, label, tmpl in _PATTERNS_MIDDLE:
            raw_candidates.append((rank, label, tmpl.format(**ctx)))

    # Single-component fallbacks
    if f:
        for rank, label, tmpl in _PATTERNS_FIRST_ONLY:
            raw_candidates.append((rank, label, tmpl.format(**ctx)))
    if l:
        for rank, label, tmpl in _PATTERNS_LAST_ONLY:
            raw_candidates.append((rank, label, tmpl.format(**ctx)))

    # Deduplicate by local_part: keep lowest rank if duplicates exist
    seen: dict[str, tuple[int, str]] = {}
    for rank, pattern, local in raw_candidates:
        if not local:
            continue
        if local not in seen or rank < seen[local][0]:
            seen[local] = (rank, pattern)

    # Sort by rank, then alphabetically for stable ordering of ties
    ordered = sorted(seen.items(), key=lambda kv: (kv[1][0], kv[0]))

    return [
        EmailCandidate(
            address=f"{local}@{domain}",
            local_part=local,
            pattern=pattern,
            rank=rank,
        )
        for local, (rank, pattern) in ordered
    ]
