"""Tests for config module (Step 1)."""

import os

import pytest

from email_finder.config import get, require


def test_get_returns_default_when_missing():
    os.environ.pop("_EF_TEST_MISSING", None)
    assert get("_EF_TEST_MISSING", "fallback") == "fallback"


def test_get_returns_value_when_set():
    os.environ["_EF_TEST_KEY"] = "hello"
    assert get("_EF_TEST_KEY") == "hello"
    del os.environ["_EF_TEST_KEY"]


def test_require_raises_when_missing():
    os.environ.pop("_EF_TEST_MISSING", None)
    with pytest.raises(KeyError, match="_EF_TEST_MISSING"):
        require("_EF_TEST_MISSING")


def test_require_returns_value_when_set():
    os.environ["_EF_TEST_KEY"] = "world"
    assert require("_EF_TEST_KEY") == "world"
    del os.environ["_EF_TEST_KEY"]
