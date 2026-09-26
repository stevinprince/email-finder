"""
Command-line interface for email-finder.

Usage examples
--------------
    email-finder --name "Jane Doe" --company "Acme Corp"
    email-finder --name "Jane Doe" --domain "acme.com"
    email-finder --name "Jane Doe" --company "Acme Corp" --domain "acme.com"
"""

from __future__ import annotations

import click

from email_finder import __version__
from email_finder.config import load_config
from email_finder.domain import resolve_domain
from email_finder.name_parser import parse_name
from email_finder.permutations import generate_permutations
from email_finder.smtp_verifier import SmtpStatus, verify_smtp


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, "-V", "--version")
@click.option(
    "--name",
    "-n",
    required=True,
    metavar="FULL_NAME",
    help="Full name of the person (e.g. 'Jane Doe').",
)
@click.option(
    "--company",
    "-c",
    default=None,
    metavar="COMPANY",
    help="Company name (used to resolve the domain when --domain is omitted).",
)
@click.option(
    "--domain",
    "-d",
    default=None,
    metavar="DOMAIN",
    help="Corporate domain, e.g. 'acme.com'. Skips domain resolution when provided.",
)
@click.option(
    "--smtp",
    "run_smtp",
    is_flag=True,
    default=False,
    help="Run SMTP verification on each candidate address (slow; may trigger rate limits).",
)
@click.option(
    "--smtp-timeout",
    default=10.0,
    show_default=True,
    metavar="SECONDS",
    help="Per-connection timeout for SMTP probes.",
)
@click.option(
    "--smtp-delay",
    default=1.0,
    show_default=True,
    metavar="SECONDS",
    help="Delay between consecutive SMTP probes (be polite to mail servers).",
)
def main(
    name: str,
    company: str | None,
    domain: str | None,
    run_smtp: bool,
    smtp_timeout: float,
    smtp_delay: float,
) -> None:
    """Find and verify professional email addresses for a person at a company."""

    # Load .env (no-op if the file does not exist yet)
    load_config()

    # ── Validation ────────────────────────────────────────────────────────────
    if not company and not domain:
        raise click.UsageError(
            "Provide at least one of --company or --domain."
        )

    # ── Domain resolution (Step 2) ────────────────────────────────────────────
    domain_result = resolve_domain(company=company, domain=domain)

    # ── Name parsing (Step 3) ─────────────────────────────────────────────────
    parsed = parse_name(name)

    # ── Permutation generation (Step 3) ───────────────────────────────────────
    candidates = generate_permutations(parsed, domain_result.domain)

    # ── Output ────────────────────────────────────────────────────────────────
    click.echo(f"email-finder v{__version__}")
    click.echo(f"  Name       : {name}")
    if parsed.prefix or parsed.suffix or parsed.middle:
        parts = []
        if parsed.prefix:
            parts.append(f"prefix={parsed.prefix!r}")
        if parsed.middle:
            parts.append(f"middle={parsed.middle!r}")
        if parsed.suffix:
            parts.append(f"suffix={parsed.suffix!r}")
        click.echo(f"  Parsed     : first={parsed.first!r}, last={parsed.last!r}  ({', '.join(parts)})")
    else:
        click.echo(f"  Parsed     : first={parsed.first!r}, last={parsed.last!r}")

    if company:
        click.echo(f"  Company    : {company}")
    click.echo(
        f"  Domain     : {domain_result.domain}"
        f"  [{domain_result.method}, confidence: {domain_result.confidence}]"
    )

    if domain_result.notes:
        for note in domain_result.notes:
            click.echo(f"  ⚠  {note}")

    # ── Permutation table ─────────────────────────────────────────────────────
    smtp_col = "  Status       Code  MX host" if run_smtp else ""
    click.echo()
    click.echo(f"  {'#':<4} {'Pattern':<20} {'Email address':<40}{smtp_col}")
    click.echo(f"  {'-'*4} {'-'*20} {'-'*40}" + ("  " + "-"*52 if run_smtp else ""))

    # ── Optional SMTP verification ────────────────────────────────────────────
    _STATUS_COLOUR = {
        SmtpStatus.VALID:     ("green",  "✓ valid    "),
        SmtpStatus.INVALID:   ("red",    "✗ invalid  "),
        SmtpStatus.CATCH_ALL: ("yellow", "~ catch-all"),
        SmtpStatus.UNKNOWN:   ("yellow", "? unknown  "),
        SmtpStatus.ERROR:     ("red",    "! error    "),
    }

    import time as _time

    for i, candidate in enumerate(candidates):
        smtp_suffix = ""
        if run_smtp:
            if i > 0:
                _time.sleep(smtp_delay)
            click.echo(f"  Checking {candidate.address} …", nl=False)
            result = verify_smtp(candidate.address, timeout=smtp_timeout)
            colour, label = _STATUS_COLOUR[result.status]
            code_str = str(result.smtp_code) if result.smtp_code else "—"
            mx_str   = result.mx_host or "—"
            smtp_suffix = f"  {click.style(label, fg=colour)}  {code_str:<5} {mx_str}"
            # overwrite the "Checking …" line
            click.echo(f"\r  {candidate.rank:<4} {candidate.pattern:<20} {candidate.address:<40}{smtp_suffix}")
        else:
            click.echo(f"  {candidate.rank:<4} {candidate.pattern:<20} {candidate.address}")

    if not run_smtp:
        click.echo()
        click.echo("  Tip: add --smtp to run SMTP verification on each address.")
    click.echo()
    click.echo("[Steps 5–8 not yet implemented]")
