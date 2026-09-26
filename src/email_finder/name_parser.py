"""
Name parsing: convert a raw full-name string into structured components.

We delegate the heavy lifting to the ``nameparser`` library, which handles
prefixes (Dr., Mr.), suffixes (Jr., PhD, III), compound last names
(van der Berg), hyphenated first names (Mary-Jane), and more.

Typical usage
-------------
>>> from email_finder.name_parser import parse_name
>>> p = parse_name("Dr. John Paul Smith Jr.")
>>> p.first, p.middle, p.last, p.suffix
('John', 'Paul', 'Smith', 'Jr.')
"""

from __future__ import annotations

from dataclasses import dataclass

from nameparser import HumanName


@dataclass(frozen=True)
class ParsedName:
    """Structured representation of a human name.

    All fields are stripped strings; they may be empty but are never ``None``.
    """

    first: str
    """Primary given name (e.g. ``'Jane'``, ``'Mary-Jane'``)."""

    last: str
    """Family name, including particles (e.g. ``'Smith'``, ``'van der Berg'``)."""

    middle: str
    """Middle name or initial, if present (e.g. ``'Paul'``, ``'R.'``)."""

    suffix: str
    """Generational or credential suffix (e.g. ``'Jr.'``, ``'III'``, ``'PhD'``)."""

    prefix: str
    """Honorific prefix (e.g. ``'Dr.'``, ``'Prof.'``)."""

    original: str
    """The raw string that was parsed."""


def parse_name(full_name: str) -> ParsedName:
    """Parse a free-form full name into structured components.

    Args:
        full_name: A name string in any common format, e.g.
                   ``"Jane Doe"``, ``"Dr. John P. Smith Jr."``,
                   ``"Mary-Jane Watson"``, ``"Madonna"``.

    Returns:
        A :class:`ParsedName` with all available components populated.

    Notes:
        * A single-token name (e.g. ``"Madonna"``) is placed in ``first``.
        * Compound last names (``"van der Berg"``) are preserved as-is in
          ``last``; the slug helper in ``permutations`` collapses the spaces.
        * Accented characters (``"José"``, ``"García"``) are kept here;
          transliteration to ASCII happens in the permutation layer.
        * Suffixes like ``"III"`` or ``"Jr."`` are captured but not used in
          email patterns (they are rarely part of an address).
    """
    raw = (full_name or "").strip()
    hn = HumanName(raw)

    return ParsedName(
        first=hn.first.strip(),
        last=hn.last.strip(),
        middle=hn.middle.strip(),
        suffix=hn.suffix.strip(),
        prefix=hn.title.strip(),
        original=raw,
    )
