# Email Finder & Verifier

A command-line tool and importable Python library that generates likely corporate
email addresses from a person's name and company, then optionally verifies them
via SMTP handshake and/or a third-party API (Hunter.io).

---

## ⚠️ Ethical Use Notice

This tool is designed to support **legitimate professional outreach** — contacting
colleagues, reconnecting with past collaborators, or verifying your own address on
a server you control.

**Do NOT use this tool to:**

- Build unsolicited marketing or spam lists
- Harvest emails at scale for commercial purposes without recipients' consent
- Target individuals who have not expressed interest in being contacted

Many jurisdictions (GDPR, CAN-SPAM, CASL, CCPA, and others) impose legal
obligations on unsolicited commercial email. Always obtain consent, honor
opt-out requests, and comply with applicable law. Misuse may result in civil
or criminal liability.

---

## Features

| Feature | Details |
|---|---|
| **Name parsing** | Handles prefixes, suffixes, middle names, hyphens, and accented characters |
| **Pattern generation** | 16 email patterns (first.last, flast, firstl, …) ranked by industry prevalence |
| **SMTP verification** | MX lookup → EHLO/HELO → catch-all probe → RCPT TO |
| **API verification** | Hunter.io v2; pluggable abstract base for other providers |
| **Confidence scoring** | Combined SMTP × API score (0-100) with `valid` / `uncertain` / `invalid` verdicts |
| **Caching** | In-memory (TTL) or SQLite; shared across invocations via CLI `--cache-db` |
| **Rate limiting** | Configurable per-candidate SMTP delay to avoid server bans |
| **JSON output** | Full structured results to stdout or file |
| **Python API** | `find_emails()` is importable and fully typed |

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) **or** pip

---

## Installation

### 1. Clone and enter the repo

```bash
git clone <repo-url>
cd email-finder
```

### 2. Create the virtual environment and install

```bash
uv sync
```

Or with pip:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### 3. Configure secrets (optional)

```bash
cp .env.example .env
# Edit .env and add your Hunter.io key if you plan to use API verification
```

`.env` is already in `.gitignore` and is never committed.

---

## Quick Start

```bash
# Generate candidates for Jane Doe at Acme Corp (no verification)
uv run email-finder --name "Jane Doe" --company "Acme Corp"

# Provide the domain directly (skips heuristic lookup)
uv run email-finder --name "Jane Doe" --domain "acme.com"

# Add SMTP verification
uv run email-finder --name "Jane Doe" --domain "acme.com" --smtp

# Add Hunter.io API verification (requires HUNTER_API_KEY in .env)
uv run email-finder --name "Jane Doe" --domain "acme.com" --api

# Both SMTP and API
uv run email-finder --name "Jane Doe" --domain "acme.com" --smtp --api

# Output as JSON
uv run email-finder --name "Jane Doe" --domain "acme.com" --json

# Save JSON to a file (human table still printed to stdout)
uv run email-finder --name "Jane Doe" --domain "acme.com" --output result.json
```

---

## CLI Reference

```
Usage: email-finder [OPTIONS] 

Options:
  --name TEXT           Full name of the person (required)
  --company TEXT        Company name (used for domain heuristic if --domain not given)
  --domain TEXT         Corporate email domain, e.g. acme.com

  SMTP verification:
  --smtp                Enable SMTP-level verification
  --smtp-timeout FLOAT  Socket timeout per SMTP attempt (default: 10 s)
  --smtp-delay FLOAT    Seconds to wait between candidates (default: 1.0)
  --smtp-retries INT    Max retry attempts per candidate (default: 2)

  API verification:
  --api                 Enable third-party API verification
  --api-provider TEXT   Provider name: hunter | mock (default: hunter)

  Caching:
  --cache               Enable in-memory cache for this run
  --cache-db PATH       Path to a SQLite file for persistent cross-run caching
  --cache-ttl FLOAT     Cache entry lifetime in seconds (default: 3600)

  Output:
  --json                Print results as JSON instead of human-readable table
  --output PATH         Write JSON results to this file (human table still printed)

  Other:
  --version             Show version and exit
  --help                Show this message and exit
```

### Example output

```
Name    : Jane Doe  (first=Jane  last=Doe)
Company : Acme Corp
Domain  : acme.com  (heuristic, confidence=80)
Methods : smtp, hunter

 #   Pattern              Email                                    Conf  Verdict       SMTP          API
 1   first.last           jane.doe@acme.com                         95/100  valid         ✓ valid       ✓ valid
 2   flast                jdoe@acme.com                             85/100  valid         ✓ valid       ✓ valid
 3   firstlast            janedoe@acme.com                          25/100  uncertain     ✗ invalid     ? unknown
...
```

---

## Python API

```python
from email_finder.finder import find_emails, FinderConfig, result_to_dict, result_to_json
from email_finder.smtp_verifier import SmtpStatus
from email_finder.api_verifier import ApiStatus

config = FinderConfig(
    run_smtp=True,
    run_api=False,
    smtp_delay=1.0,        # seconds between SMTP probes
    smtp_timeout=10.0,
    smtp_retries=2,
)

result = find_emails("Jane Doe", domain="acme.com", config=config)

for candidate in result.candidates:
    print(candidate.email, candidate.verdict, candidate.confidence)

# Full structured dict / JSON
data = result_to_dict(result)
json_str = result_to_json(result, indent=2)
```

### `FinderConfig` options

| Field | Type | Default | Description |
|---|---|---|---|
| `run_smtp` | `bool` | `False` | Enable SMTP verification |
| `run_api` | `bool` | `False` | Enable API verification |
| `smtp_from_address` | `str` | `"probe@example.com"` | MAIL FROM address used during SMTP probes |
| `smtp_timeout` | `float` | `10.0` | Per-attempt socket timeout (seconds) |
| `smtp_delay` | `float` | `1.0` | Minimum seconds between SMTP probes (rate limit) |
| `smtp_retries` | `int` | `2` | Max retry attempts per candidate |
| `smtp_dns_timeout` | `float` | `5.0` | DNS resolution timeout (seconds) |
| `api_provider` | `str` | `"hunter"` | API provider name |
| `api_key` | `str` | `""` | API key (falls back to env var / `.env`) |
| `cache` | `BaseCache \| None` | `None` | Cache instance (see caching section) |
| `cache_ttl` | `float` | `3600.0` | Seconds before cache entries expire |

### Caching

```python
from email_finder.cache import InMemoryCache, SqliteCache, make_cache

# In-memory (single process, cleared on exit)
cache = make_cache("memory", default_ttl=3600)

# SQLite (persistent across runs)
cache = make_cache("sqlite", db_path="/tmp/email_cache.db", default_ttl=3600)

config = FinderConfig(run_smtp=True, cache=cache)
```

### Adding a custom API verifier

Subclass `BaseApiVerifier` to plug in any provider:

```python
from email_finder.api_verifier import BaseApiVerifier, ApiResult, ApiStatus

class MyVerifier(BaseApiVerifier):
    @property
    def provider_name(self) -> str:
        return "my_provider"

    def verify(self, email: str) -> ApiResult:
        # Call your API here; never raise — return ApiStatus.ERROR on failure
        ...
```

Then pass it directly to `FinderConfig` (not yet wired through the CLI, but usable via the Python API).

---

## Architecture

```
email-finder/
├── src/email_finder/
│   ├── cli.py            # Click CLI entry point
│   ├── config.py         # .env loading (python-dotenv)
│   ├── domain.py         # Company → domain heuristic (+ stub API path)
│   ├── name_parser.py    # nameparser wrapper → ParsedName
│   ├── permutations.py   # 16-pattern email candidate generator
│   ├── smtp_verifier.py  # MX lookup + SMTP handshake verifier
│   ├── api_verifier.py   # BaseApiVerifier ABC + HunterVerifier
│   ├── combiner.py       # SMTP × API → confidence score + verdict
│   ├── finder.py         # Orchestration: find_emails(), result_to_dict()
│   ├── cache.py          # InMemoryCache + SqliteCache
│   └── rate_limiter.py   # Token-bucket-style per-candidate delay
└── tests/
    ├── test_cli.py
    ├── test_domain.py
    ├── test_name_parser.py
    ├── test_permutations.py
    ├── test_smtp_verifier.py
    ├── test_api_verifier.py
    ├── test_combiner.py
    ├── test_finder.py
    ├── test_cache.py
    ├── test_rate_limiter.py
    ├── test_finder_cache.py
    └── test_edge_cases.py
```

### Confidence scoring

Results without verification are sorted by **pattern rank** (most common pattern
first). When SMTP and/or API verification is run, each candidate receives a
**confidence score (0-100)**:

| SMTP status | API status | Base confidence |
|---|---|---|
| valid | valid | 95 |
| valid | invalid | 40 |
| valid | — | 70 |
| invalid | — | 5 |
| catch_all | valid | 75 |
| catch_all | — | 35 |
| unknown / error | valid | 65 |
| — | valid | 65 |
| — | invalid | 10 |

When the API provider returns a numeric score, it is used as the base confidence
(±5 for SMTP agreement/conflict). Verdicts: **valid** ≥ 70, **uncertain** ≥ 25,
**invalid** < 25.

### SMTP verification flow

```
DNS MX lookup
    ↓
Connect to MX host
    ↓
EHLO (→ HELO fallback if 400+)
    ↓
MAIL FROM: probe@example.com
    ↓
RCPT TO: <random 20-char address>  ← catch-all probe
    ↓ 250 → catch_all
    ↓ 550 → continue
RCPT TO: <target address>
    ↓ 250 → valid
    ↓ 5xx → invalid
    ↓ 4xx → unknown (greylisting?)
```

---

## Development

```bash
# Run tests
uv run pytest

# Run with coverage
uv run pytest --cov=email_finder --cov-report=term-missing

# Run a single test module
uv run pytest tests/test_smtp_verifier.py -v
```

All 318 tests run without network access (DNS, SMTP, and HTTP are fully mocked).

---

## Known Limitations

1. **Domain heuristic is approximate.** The heuristic strips common legal suffixes
   and slugifies the company name. It works well for `"Acme Corp"` → `acme.com`
   but fails for companies with unusual branding (e.g. `"23andMe"`, `"C3.ai"`).
   Always prefer `--domain` when you know it.

2. **Catch-all servers hide real validity.** If a server accepts mail to any
   address (`RCPT TO: *`), SMTP verification reports `catch_all` and cannot
   confirm individual addresses. A third-party API verifier often handles this
   better.

3. **SMTP greylisting / rate limiting.** Some servers temporarily reject
   connections (4xx codes). The tool retries but may still return `unknown`.
   Re-running after a short wait usually resolves this.

4. **No MX discovery for free-form domains.** If the company domain has no MX
   record, SMTP verification reports `unknown` with detail.

5. **API domain resolution is a stub.** The `_api_resolve()` path in `domain.py`
   raises `NotImplementedError` — a placeholder for a future service that maps
   company names to domains programmatically.

6. **Hunter.io free tier is limited.** The free plan provides 25 verifications/month.
   Use `--cache-db` to avoid re-verifying the same addresses.

7. **Not a spam tool.** This tool is intentionally designed for single-person
   lookups, not bulk scraping. The built-in rate limiter enforces a minimum delay
   between SMTP probes. Bypassing the rate limiter to scrape at scale would likely
   violate your mail server's terms of service and applicable law.
