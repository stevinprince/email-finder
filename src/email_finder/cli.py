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
from email_finder.api_verifier import ApiStatus, get_verifier
from email_finder.combiner import combine
from email_finder.config import get as cfg_get
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
@click.option(
    "--api",
    "run_api",
    is_flag=True,
    default=False,
    help="Run third-party API verification on each candidate (requires API key in .env).",
)
@click.option(
    "--api-provider",
    default="hunter",
    show_default=True,
    metavar="PROVIDER",
    help="API provider to use: 'hunter' (Hunter.io) or 'mock' (testing).",
)
def main(
    name: str,
    company: str | None,
    domain: str | None,
    run_smtp: bool,
    smtp_timeout: float,
    smtp_delay: float,
    run_api: bool,
    api_provider: str,
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

    # ── Optional API verifier setup ───────────────────────────────────────────
    api_verifier = None
    if run_api:
        api_key = cfg_get("HUNTER_API_KEY", "") if api_provider == "hunter" else ""
        try:
            api_verifier = get_verifier(api_provider, api_key=api_key or "")
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc

    # ── Permutation table ─────────────────────────────────────────────────────
    verify_any = run_smtp or run_api
    extra_col  = "  Confidence  Verdict     SMTP         API" if verify_any else ""
    click.echo()
    click.echo(f"  {'#':<4} {'Pattern':<20} {'Email address':<40}{extra_col}")
    click.echo(f"  {'-'*4} {'-'*20} {'-'*40}" + ("  " + "-"*50 if verify_any else ""))

    _SMTP_LABEL: dict[SmtpStatus, tuple[str, str]] = {
        SmtpStatus.VALID:     ("green",  "✓ valid    "),
        SmtpStatus.INVALID:   ("red",    "✗ invalid  "),
        SmtpStatus.CATCH_ALL: ("yellow", "~ catch-all"),
        SmtpStatus.UNKNOWN:   ("yellow", "? unknown  "),
        SmtpStatus.ERROR:     ("red",    "! error    "),
    }
    _API_LABEL: dict[ApiStatus, tuple[str, str]] = {
        ApiStatus.VALID:   ("green",  "✓ valid  "),
        ApiStatus.INVALID: ("red",    "✗ invalid"),
        ApiStatus.UNKNOWN: ("yellow", "? unknown"),
        ApiStatus.ERROR:   ("red",    "! error  "),
    }
    _VERDICT_COLOUR = {"valid": "green", "uncertain": "yellow", "invalid": "red"}

    import time as _time

    for i, candidate in enumerate(candidates):
        if verify_any and i > 0:
            _time.sleep(smtp_delay)

        smtp_result = None
        api_result  = None

        if run_smtp:
            smtp_result = verify_smtp(candidate.address, timeout=smtp_timeout)
        if run_api and api_verifier:
            api_result = api_verifier.verify(candidate.address)

        if verify_any:
            combined = combine(candidate.address, smtp=smtp_result, api=api_result)

            conf_str    = f"{combined.confidence:>3}/100"
            v_colour    = _VERDICT_COLOUR.get(combined.verdict, "white")
            verdict_str = click.style(f"{combined.verdict:<11}", fg=v_colour)

            if smtp_result:
                sc, sl = _SMTP_LABEL[smtp_result.status]
                smtp_str = click.style(sl, fg=sc)
            else:
                smtp_str = "—           "

            if api_result:
                ac, al = _API_LABEL[api_result.status]
                api_str = click.style(al, fg=ac)
            else:
                api_str = "—"

            suffix = f"  {conf_str}  {verdict_str}  {smtp_str}  {api_str}"
            click.echo(f"  {candidate.rank:<4} {candidate.pattern:<20} {candidate.address:<40}{suffix}")
        else:
            click.echo(f"  {candidate.rank:<4} {candidate.pattern:<20} {candidate.address}")

    if not verify_any:
        click.echo()
        click.echo("  Tip: add --smtp and/or --api to verify each address.")
    click.echo()
    click.echo("[Steps 6–8 not yet implemented]")
