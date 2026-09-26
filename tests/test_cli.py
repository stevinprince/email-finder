"""Smoke tests for the CLI scaffold (Step 1)."""

from click.testing import CliRunner

from email_finder.cli import main


def test_help_exits_cleanly():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "FULL_NAME" in result.output


def test_version_flag():
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_name_and_company_printed():
    runner = CliRunner()
    result = runner.invoke(main, ["--name", "Jane Doe", "--company", "Acme Corp"])
    assert result.exit_code == 0
    assert "Jane Doe" in result.output
    assert "Acme Corp" in result.output


def test_name_and_domain_printed():
    runner = CliRunner()
    result = runner.invoke(main, ["--name", "Jane Doe", "--domain", "acme.com"])
    assert result.exit_code == 0
    assert "Jane Doe" in result.output
    assert "acme.com" in result.output


def test_missing_company_and_domain_raises():
    runner = CliRunner()
    result = runner.invoke(main, ["--name", "Jane Doe"])
    assert result.exit_code != 0
    assert "company" in result.output.lower() or "domain" in result.output.lower()
