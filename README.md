# email-finder

> Find and verify professional email addresses for a person at a company.

Given a person's full name and a company name (or domain), **email-finder**
generates the most likely email address permutations and checks which of those
addresses are valid and deliverable.

---

## ⚠️ Responsible-Use Notice

This tool is intended for **legitimate professional use only** — for example,
reaching out to a contact whose email you cannot locate through normal channels.

- **Do not** use this tool for spam, harassment, or unsolicited bulk outreach.
- **Respect** target mail servers' rate limits and terms of service.
- **Comply** with applicable data-protection laws (GDPR, CAN-SPAM, etc.)
  regarding collection and use of discovered email addresses.
- **Do not** store or process discovered addresses beyond what is necessary for
  your stated purpose.

---

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (package manager)

## Setup

```bash
# 1. Clone the repo
git clone <repo-url>
cd email-finder

# 2. Install dependencies (creates .venv automatically)
uv sync --group dev

# 3. Copy the example env file and fill in any API keys you need
cp .env.example .env
```

## Usage

```bash
# Resolve via company name
uv run email-finder --name "Jane Doe" --company "Acme Corp"

# Skip domain resolution — supply the domain directly
uv run email-finder --name "Jane Doe" --domain "acme.com"

# Both flags accepted (domain takes priority for resolution)
uv run email-finder --name "Jane Doe" --company "Acme Corp" --domain "acme.com"
```

## Running tests

```bash
uv run pytest -v
```

## Project structure

```
email-finder/
├── src/
│   └── email_finder/
│       ├── __init__.py   # package version
│       ├── cli.py        # Click-based CLI entrypoint
│       └── config.py     # .env loading & config helpers
├── tests/
│   ├── test_cli.py
│   └── test_config.py
├── .env.example          # template — copy to .env
├── pyproject.toml
└── README.md
```

---

*More steps (domain resolution, permutation generation, SMTP verification, and
third-party API integration) will be added in subsequent development steps.*
