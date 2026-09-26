"""Tests for domain resolution (Step 2)."""

from __future__ import annotations

import pytest

from email_finder.domain import DomainResult, _heuristic_resolve, resolve_domain


# ---------------------------------------------------------------------------
# resolve_domain — routing logic
# ---------------------------------------------------------------------------

class TestResolveDomain:
    def test_domain_provided_returns_it_unchanged(self):
        result = resolve_domain(domain="acme.com")
        assert result.domain == "acme.com"
        assert result.method == "provided"
        assert result.confidence == "high"
        assert result.notes == []

    def test_domain_strips_leading_at_sign(self):
        result = resolve_domain(domain="@acme.com")
        assert result.domain == "acme.com"

    def test_domain_lowercased(self):
        result = resolve_domain(domain="Acme.COM")
        assert result.domain == "acme.com"

    def test_domain_strips_whitespace(self):
        result = resolve_domain(domain="  stripe.com  ")
        assert result.domain == "stripe.com"

    def test_domain_takes_precedence_over_company(self):
        """When both are given, the explicit domain wins."""
        result = resolve_domain(company="Some Corp", domain="explicit.io")
        assert result.domain == "explicit.io"
        assert result.method == "provided"

    def test_neither_raises_value_error(self):
        with pytest.raises(ValueError, match="At least one"):
            resolve_domain()

    def test_company_only_falls_back_to_heuristic(self):
        result = resolve_domain(company="Acme Corp")
        assert result.method == "heuristic"
        assert result.domain.endswith(".com")


# ---------------------------------------------------------------------------
# _heuristic_resolve — normalisation cases
# ---------------------------------------------------------------------------

class TestHeuristicResolve:
    # ── Legal suffix stripping ────────────────────────────────────────────────

    @pytest.mark.parametrize("company, expected_domain", [
        # Single-word suffixes
        ("Acme Corp",           "acme.com"),
        ("Acme Corporation",    "acme.com"),
        ("Widgets Inc",         "widgets.com"),
        ("Widgets Inc.",        "widgets.com"),
        ("Widgets Incorporated","widgets.com"),
        ("Gadgets LLC",         "gadgets.com"),
        ("Gadgets Ltd",         "gadgets.com"),
        ("Gadgets Limited",     "gadgets.com"),
        ("Gadgets PLC",         "gadgets.com"),
        ("Gadgets GmbH",        "gadgets.com"),
        ("Gadgets Co",          "gadgets.com"),
        # Multi-word names
        ("General Electric",    "generalelectric.com"),
        ("Home Depot",          "homedepot.com"),
    ])
    def test_suffix_stripped(self, company, expected_domain):
        assert _heuristic_resolve(company).domain == expected_domain

    # ── Conjunctions ──────────────────────────────────────────────────────────

    @pytest.mark.parametrize("company, expected_domain", [
        ("McKinsey & Company",      "mckinsey.com"),
        ("Johnson and Johnson",     "johnsonjohnson.com"),
        ("Procter & Gamble",        "proctergamble.com"),
    ])
    def test_conjunction_stripped(self, company, expected_domain):
        assert _heuristic_resolve(company).domain == expected_domain

    # ── Hyphenated names ─────────────────────────────────────────────────────

    def test_hyphen_preserved(self):
        assert _heuristic_resolve("Coca-Cola").domain == "coca-cola.com"

    def test_hyphenated_with_suffix(self):
        assert _heuristic_resolve("Coca-Cola Company").domain == "coca-cola.com"

    # ── Numbers / alphanumeric ────────────────────────────────────────────────

    def test_numeric_prefix(self):
        assert _heuristic_resolve("3M").domain == "3m.com"

    def test_all_numeric_name(self):
        result = _heuristic_resolve("123")
        assert result.domain == "123.com"

    # ── Case handling ─────────────────────────────────────────────────────────

    def test_mixed_case_lowercased(self):
        assert _heuristic_resolve("ACME CORP").domain == "acme.com"

    def test_camel_case_name(self):
        assert _heuristic_resolve("YouTube LLC").domain == "youtube.com"

    # ── Whitespace edge cases ─────────────────────────────────────────────────

    def test_extra_internal_whitespace(self):
        assert _heuristic_resolve("Acme   Corp").domain == "acme.com"

    def test_leading_trailing_whitespace(self):
        assert _heuristic_resolve("  Acme Corp  ").domain == "acme.com"

    # ── Punctuation ───────────────────────────────────────────────────────────

    def test_period_in_name_stripped(self):
        # e.g. "U.S. Steel" → "ussteel.com"
        assert _heuristic_resolve("U.S. Steel").domain == "ussteel.com"

    def test_comma_stripped(self):
        assert _heuristic_resolve("Acme, Inc.").domain == "acme.com"

    # ── Result metadata ───────────────────────────────────────────────────────

    def test_method_is_heuristic(self):
        result = _heuristic_resolve("Acme Corp")
        assert result.method == "heuristic"

    def test_confidence_is_low(self):
        result = _heuristic_resolve("Acme Corp")
        assert result.confidence == "low"

    def test_notes_contain_warning(self):
        result = _heuristic_resolve("Acme Corp")
        combined = " ".join(result.notes).lower()
        assert "heuristic" in combined or "guess" in combined or "wrong" in combined

    def test_returns_domain_result_type(self):
        assert isinstance(_heuristic_resolve("Acme"), DomainResult)
