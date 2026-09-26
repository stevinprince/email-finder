"""
Command-line interface for email-finder.

Usage examples
--------------
    email-finder --name "Jane Doe" --domain "acme.com"
    email-finder --name "Jane Doe" --company "Acme Corp" --smtp
    email-finder --name "Jane Doe" --domain "acme.com" --smtp --api
    email-finder --name "Jane Doe" --domain "acme.com" --json
    email-finder --name "Jane Doe" --domain "acme.com" --output results.json
"""

from __future__ import annotations

from pathlib import Path

import click

from email_finder import __version__
from email_finder.api_verifier import ApiStatus
from email_finder.config import get as cfg_get
from email_finder.config import load_config
from email_finder.finder import FinderConfig, FinderResult, find_emails, result_to_json
from email_finder.smtp_verifier import SmtpStatus


# ---------------------------------------------------------------------------
# Colour / label helpers
# ---------------------------------------------------------------------------

_SMTP_LABEL: dict[str, tuple[str, str]] = {
    SmtpStatus.VALID.value:     ("green",  "✓ valid    "),
    SmtpStatus.INVALID.value:   ("red",    "✗ invalid  "),
    SmtpStatus.CATCH_ALL.value: ("yellow", "~ catch-all"),
    SmtpStatus.UNKNOWN.value:   ("yellow", "? unknown  "),
    SmtpStatus.ERROR.value:     ("red",    "! error    "),
}
_API_LABEL: dict[str, tuple[str, str]] = {
    ApiStatus.VALID.value:   ("green",  "✓ valid  "),
    ApiStatus.INVALID.value: ("red",    "✗ invalid"),
    ApiStatus.UNKNOWN.value: ("yellow", "? unknown"),
    ApiStatus.ERROR.value:   ("red",    "! error  "),
}
_VERDICT_COLOUR: dict[str, str] = {
    "valid":      "green",
    "uncertain":  "yellow",
    "invalid":    "red",
    "unverified": "white",
}


# ---------------------------------------------------------------------------
# Human-readable renderer
# ---------------------------------------------------------------------------

def _render(result: FinderResult, verify_any: bool) -> None:
    """Print a human-readable summary of *result* to stdout."""
    pn = result.parsed_name

    click.echo(f"email-finder v{__version__}")
    click.echo(f"  Name    : {result.name}")

    # Parsed name — only show extra components when they're present
    extras = []
    if pn.prefix:  extras.append(f"prefix={pn.prefix!r}")
    if pn.middle:  extras.append(f"middle={pn.middle!r}")
    if pn.suffix:  extras.append(f"suffix={pn.suffix!r}")
    parsed_line = f"first={pn.first!r}, last={pn.last!r}"
    if extras:
        parsed_line += f"  ({', '.join(extras)})"
    click.echo(f"  Parsed  : {parsed_line}")

    if result.company:
        click.echo(f"  Company : {result.company}")
    click.echo(
        f"  Domain  : {result.domain}"
        f"  [{result.domain_method}, confidence: {result.domain_confidence}]"
    )
    for note in result.domain_notes:
        click.echo(f"  ⚠  {note}")

    if result.verified_with:
        click.echo(f"  Methods : {', '.join(result.verified_with)}")

    # ── Candidate table ───────────────────────────────────────────────────────
    extra_hdr = "  Conf   Verdict      SMTP          API" if verify_any else ""
    click.echo()
    click.echo(f"  {'#':<4} {'Pattern':<20} {'Email address':<40}{extra_hdr}")
    click.echo(f"  {'-'*4} {'-'*20} {'-'*40}" + ("  " + "-"*40 if verify_any else ""))

    for i, c in enumerate(result.candidates):
        if verify_any:
            conf_str    = f"{c.confidence:>3}/100"
            v_col       = _VERDICT_COLOUR.get(c.verdict, "white")
            verdict_str = click.style(f"{c.verdict:<12}", fg=v_col)

            if c.smtp:
                sc, sl = _SMTP_LABEL.get(c.smtp.status.value, ("white", c.smtp.status.value))
                smtp_str = click.style(sl, fg=sc)
            else:
                smtp_str = "—           "

            if c.api:
                ac, al = _API_LABEL.get(c.api.status.value, ("white", c.api.status.value))
                api_str = click.style(al, fg=ac)
            else:
                api_str = "—"

            suffix = f"  {conf_str}  {verdict_str}  {smtp_str}  {api_str}"
            click.echo(f"  {i+1:<4} {c.pattern:<20} {c.email:<40}{suffix}")
        else:
            click.echo(f"  {c.pattern_rank:<4} {c.pattern:<20} {c.email}")

    if not verify_any:
        click.echo()
        click.echo("  Tip: add --smtp and/or --api to verify each address.")


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version")
# ── Identity ──────────────────────────────────────────────────────────────────
@click.option("--name",    "-n", required=True, metavar="FULL_NAME",
              help="Full name of the person (e.g. 'Jane Doe').")
@click.option("--company", "-c", default=None,  metavar="COMPANY",
              help="Company name (resolves domain when --domain is omitted).")
@click.option("--domain",  "-d", default=None,  metavar="DOMAIN",
              help="Corporate domain, e.g. 'acme.com'. Skips resolution when provided.")
# ── SMTP ──────────────────────────────────────────────────────────────────────
@click.option("--smtp", "run_smtp", is_flag=True, default=False,
              help="SMTP-probe each candidate (slow; may trigger rate limits).")
@click.option("--smtp-timeout", default=10.0, show_default=True, metavar="SECS",
              help="Per-connection SMTP timeout in seconds.")
@click.option("--smtp-delay",   default=1.0,  show_default=True, metavar="SECS",
              help="Delay between SMTP probes (be polite to mail servers).")
@click.option("--smtp-retries", default=1,    show_default=True, metavar="N",
              help="Retries on transient SMTP errors.")
# ── API ───────────────────────────────────────────────────────────────────────
@click.option("--api", "run_api", is_flag=True, default=False,
              help="Verify via third-party API (requires API key in .env).")
@click.option("--api-provider", default="hunter", show_default=True, metavar="PROVIDER",
              help="API provider: 'hunter' (Hunter.io) or 'mock' (testing).")
# ── Output ────────────────────────────────────────────────────────────────────
@click.option("--json", "as_json", is_flag=True, default=False,
              help="Print results as JSON instead of the human-readable table.")
@click.option("--output", "-o", default=None, metavar="FILE",
              help="Write JSON results to FILE (implies --json format).")
def main(
    name:         str,
    company:      str | None,
    domain:       str | None,
    run_smtp:     bool,
    smtp_timeout: float,
    smtp_delay:   float,
    smtp_retries: int,
    run_api:      bool,
    api_provider: str,
    as_json:      bool,
    output:       str | None,
) -> None:
    """Find and verify professional email addresses for a person at a company."""

    load_config()

    if not company and not domain:
        raise click.UsageError("Provide at least one of --company or --domain.")

    # API key — read from environment for real providers
    api_key = ""
    if run_api and api_provider == "hunter":
        api_key = cfg_get("HUNTER_API_KEY", "") or ""

    config = FinderConfig(
        run_smtp=run_smtp,
        smtp_timeout=smtp_timeout,
        smtp_delay=smtp_delay,
        smtp_max_retries=smtp_retries,
        run_api=run_api,
        api_provider=api_provider,
        api_key=api_key,
        api_timeout=15.0,
    )

    try:
        result = find_emails(name, company=company, domain=domain, config=config)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    verify_any = run_smtp or run_api

    # ── JSON output ───────────────────────────────────────────────────────────
    if output:
        json_str = result_to_json(result)
        Path(output).write_text(json_str, encoding="utf-8")
        click.echo(f"Results written to {output}")
        if not as_json:
            _render(result, verify_any)
        return

    if as_json:
        click.echo(result_to_json(result))
        return

    # ── Human-readable output (default) ───────────────────────────────────────
    _render(result, verify_any)
