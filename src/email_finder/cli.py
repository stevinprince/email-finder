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
def main(name: str, company: str | None, domain: str | None) -> None:
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

    click.echo()
    click.echo(f"  {'#':<4} {'Pattern':<20} {'Email address'}")
    click.echo(f"  {'-'*4} {'-'*20} {'-'*40}")
    for candidate in candidates:
        click.echo(
            f"  {candidate.rank:<4} {candidate.pattern:<20} {candidate.address}"
        )

    click.echo()
    click.echo("[Steps 4–8 (verification) not yet implemented]")
