"""Tests for email permutation generation (Step 3)."""

from __future__ import annotations

import pytest

from email_finder.name_parser import ParsedName, parse_name
from email_finder.permutations import EmailCandidate, _slug, generate_permutations


# ---------------------------------------------------------------------------
# _slug helper
# ---------------------------------------------------------------------------

class TestSlug:
    @pytest.mark.parametrize("raw, expected", [
        ("Jane",            "jane"),
        ("Doe",             "doe"),
        ("Mary-Jane",       "mary-jane"),
        ("O'Brien",         "obrien"),
        ("van der Berg",    "vanderberg"),
        ("José",            "jose"),
        ("García",          "garcia"),
        ("3M",              "3m"),
        ("",                ""),
        ("  Jane  ",        "jane"),
        ("R.",              "r"),
    ])
    def test_slug_cases(self, raw, expected):
        assert _slug(raw) == expected


# ---------------------------------------------------------------------------
# generate_permutations — standard two-part name
# ---------------------------------------------------------------------------

class TestGeneratePermutations:
    DOMAIN = "acme.com"

    def _run(self, full_name: str) -> list[EmailCandidate]:
        return generate_permutations(parse_name(full_name), self.DOMAIN)

    def test_returns_list_of_email_candidates(self):
        results = self._run("Jane Doe")
        assert all(isinstance(c, EmailCandidate) for c in results)

    def test_addresses_contain_domain(self):
        for c in self._run("Jane Doe"):
            assert c.address.endswith(f"@{self.DOMAIN}")

    def test_no_duplicate_addresses(self):
        results = self._run("Jane Doe")
        addresses = [c.address for c in results]
        assert len(addresses) == len(set(addresses))

    def test_sorted_by_rank(self):
        results = self._run("Jane Doe")
        ranks = [c.rank for c in results]
        assert ranks == sorted(ranks)

    def test_first_last_pattern_present_and_top_ranked(self):
        results = self._run("Jane Doe")
        first_last = next((c for c in results if c.pattern == "first.last"), None)
        assert first_last is not None
        assert first_last.address == "jane.doe@acme.com"
        assert first_last.rank == results[0].rank  # rank 1

    def test_expected_patterns_for_jane_doe(self):
        """Spot-check a handful of expected addresses."""
        results = self._run("Jane Doe")
        addresses = {c.address for c in results}
        assert "jane.doe@acme.com"  in addresses   # first.last
        assert "jdoe@acme.com"      in addresses   # flast
        assert "janedoe@acme.com"   in addresses   # firstlast
        assert "j.doe@acme.com"     in addresses   # f.last
        assert "jane_doe@acme.com"  in addresses   # first_last
        assert "doe.jane@acme.com"  in addresses   # last.first
        assert "jane@acme.com"      in addresses   # first
        assert "doe@acme.com"       in addresses   # last

    # ── Middle name ───────────────────────────────────────────────────────────

    def test_middle_name_patterns_included(self):
        results = self._run("John Paul Smith")
        addresses = {c.address for c in results}
        assert "john.paul.smith@acme.com" in addresses   # first.middle.last
        assert "jps@acme.com"            not in addresses  # initials-only not in spec
        assert "j.p.smith@acme.com"      in addresses     # f.m.last
        assert "john.p.smith@acme.com"   in addresses     # first.m.last

    def test_middle_name_flast_variant(self):
        results = self._run("John Paul Smith")
        addresses = {c.address for c in results}
        # fmlast = first-initial + middle-initial + last
        assert "jpsm ith@acme.com" not in addresses  # sanity: no spaces
        patterns = {c.pattern: c.address for c in results}
        assert "fmlast" in patterns
        assert patterns["fmlast"] == "jpsm ith@acme.com".replace(" ", "")  # "jpsmith"
        # correct: fi=j, mi=p, l=smith → "jpsmith"
        assert patterns["fmlast"] == "jpsmith@acme.com"

    def test_no_middle_name_patterns_absent(self):
        """Middle-name patterns must not appear when there is no middle name."""
        results = self._run("Jane Doe")
        patterns = {c.pattern for c in results}
        assert "first.middle.last" not in patterns
        assert "fmlast"            not in patterns
        assert "f.m.last"          not in patterns
        assert "first.m.last"      not in patterns

    # ── Single-token names ────────────────────────────────────────────────────

    def test_single_name_only_first_patterns(self):
        """'Madonna' → first='Madonna', last='' → only 'first' pattern."""
        results = self._run("Madonna")
        assert len(results) == 1
        assert results[0].address == "madonna@acme.com"
        assert results[0].pattern == "first"

    # ── Hyphenated names ──────────────────────────────────────────────────────

    def test_hyphenated_first_name_preserved_in_slug(self):
        results = self._run("Mary-Jane Watson")
        addresses = {c.address for c in results}
        assert "mary-jane.watson@acme.com" in addresses

    # ── Accented / unicode names ──────────────────────────────────────────────

    def test_accented_name_transliterated(self):
        results = self._run("José García")
        addresses = {c.address for c in results}
        # All addresses should be pure ASCII
        for addr in addresses:
            assert addr.isascii(), f"Non-ASCII address generated: {addr}"
        assert "jose.garcia@acme.com" in addresses

    # ── Compound last names ───────────────────────────────────────────────────

    def test_compound_last_name_spaces_removed(self):
        results = self._run("Robert van der Berg")
        addresses = {c.address for c in results}
        assert "robert.vanderberg@acme.com" in addresses

    # ── Domain edge cases ─────────────────────────────────────────────────────

    def test_custom_domain_used(self):
        results = generate_permutations(parse_name("Jane Doe"), "stripe.com")
        assert all("stripe.com" in c.address for c in results)

    # ── Metadata ──────────────────────────────────────────────────────────────

    def test_each_candidate_has_pattern_name(self):
        for c in self._run("Jane Doe"):
            assert c.pattern != ""

    def test_local_part_matches_address(self):
        for c in self._run("Jane Doe"):
            assert c.address == f"{c.local_part}@{self.DOMAIN}"
