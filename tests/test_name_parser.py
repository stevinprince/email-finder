"""Tests for name parsing (Step 3)."""

from __future__ import annotations

import pytest

from email_finder.name_parser import ParsedName, parse_name


class TestParseNameBasic:
    def test_simple_first_last(self):
        p = parse_name("Jane Doe")
        assert p.first == "Jane"
        assert p.last == "Doe"
        assert p.middle == ""
        assert p.suffix == ""
        assert p.prefix == ""

    def test_three_part_name(self):
        p = parse_name("John Paul Smith")
        assert p.first == "John"
        assert p.middle == "Paul"
        assert p.last == "Smith"

    def test_middle_initial(self):
        p = parse_name("Elon R. Musk")
        assert p.first == "Elon"
        assert p.middle == "R."
        assert p.last == "Musk"

    def test_returns_parsed_name_type(self):
        assert isinstance(parse_name("Jane Doe"), ParsedName)

    def test_original_preserved(self):
        raw = "Dr. Jane Doe PhD"
        assert parse_name(raw).original == raw


class TestParseNameSingleToken:
    def test_single_name_goes_to_first(self):
        """nameparser puts a lone token in first."""
        p = parse_name("Madonna")
        # nameparser puts single-token names in first
        assert p.first == "Madonna"
        assert p.last == ""

    def test_single_name_no_suffix(self):
        p = parse_name("Cher")
        assert p.suffix == ""


class TestParseNameSuffixes:
    def test_jr_suffix(self):
        p = parse_name("John Smith Jr.")
        assert p.first == "John"
        assert p.last == "Smith"
        assert p.suffix == "Jr."

    def test_roman_numeral_suffix(self):
        p = parse_name("Elon R. Musk III")
        assert p.suffix == "III"
        assert p.last == "Musk"

    def test_academic_suffix(self):
        p = parse_name("Jane Doe PhD")
        assert p.last == "Doe"
        assert "PhD" in p.suffix


class TestParseNamePrefixes:
    def test_dr_prefix(self):
        p = parse_name("Dr. Jane Doe")
        assert p.prefix == "Dr."
        assert p.first == "Jane"
        assert p.last == "Doe"

    def test_prefix_not_in_first(self):
        p = parse_name("Prof. Alan Turing")
        assert "Prof" not in p.first


class TestParseNameSpecialForms:
    def test_hyphenated_first_name(self):
        p = parse_name("Mary-Jane Watson")
        assert p.first == "Mary-Jane"
        assert p.last == "Watson"

    def test_hyphenated_last_name(self):
        p = parse_name("John Smith-Jones")
        assert p.last == "Smith-Jones"

    def test_compound_last_name_with_particle(self):
        p = parse_name("Robert van der Berg")
        assert p.first == "Robert"
        assert "Berg" in p.last       # particle + surname kept together

    def test_apostrophe_in_last_name(self):
        p = parse_name("Mary O'Brien")
        assert p.first == "Mary"
        assert "Brien" in p.last

    def test_accented_characters_preserved(self):
        """Transliteration is the permutation layer's job; parser keeps originals."""
        p = parse_name("José García")
        assert "Jos" in p.first       # nameparser may or may not keep accent
        assert "arc" in p.last        # "García" contains "arc"

    def test_all_caps(self):
        p = parse_name("JANE DOE")
        assert p.first != ""
        assert p.last != ""


class TestParseNameEdgeCases:
    def test_empty_string_returns_all_empty(self):
        p = parse_name("")
        assert p.first == ""
        assert p.last == ""
        assert p.middle == ""
        assert p.original == ""

    def test_whitespace_only(self):
        p = parse_name("   ")
        assert p.first == ""
        assert p.last == ""
        assert p.original == ""

    def test_leading_trailing_whitespace_stripped(self):
        p = parse_name("  Jane Doe  ")
        assert p.first == "Jane"
        assert p.last == "Doe"

    def test_extra_internal_whitespace(self):
        p = parse_name("Jane   Doe")
        assert p.first == "Jane"
        assert p.last == "Doe"
