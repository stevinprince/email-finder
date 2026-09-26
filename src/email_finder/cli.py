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

    # ── Placeholder output (business logic added in later steps) ──────────────
    click.echo(f"email-finder v{__version__}")
    click.echo(f"  Name    : {name}")
    if company:
        click.echo(f"  Company : {company}")
    if domain:
        click.echo(f"  Domain  : {domain}")
    click.echo()
    click.echo("[Step 1 scaffold — business logic not yet implemented]")
