"""
Configuration and environment variable loading.

All secrets (API keys, etc.) must be set in a .env file or in the real
environment — never hardcoded.  Call `load_config()` once at startup.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def load_config(env_file: Path | str | None = None) -> None:
    """Load environment variables from a .env file.

    Args:
        env_file: Path to the .env file.  Defaults to ``<project-root>/.env``.
                  If the file does not exist, environment variables already
                  present in the shell are still used — no error is raised.
    """
    if env_file is None:
        # Walk up from this file to find the project root (.env sits there).
        env_file = Path(__file__).parent.parent.parent / ".env"

    load_dotenv(dotenv_path=env_file, override=False)


def get(key: str, default: str | None = None) -> str | None:
    """Retrieve a configuration value by name.

    Args:
        key:     Environment variable name.
        default: Value to return when the key is absent.

    Returns:
        The value as a string, or *default* if not set.
    """
    return os.environ.get(key, default)


def require(key: str) -> str:
    """Retrieve a required configuration value; raise if missing.

    Args:
        key: Environment variable name.

    Returns:
        The value as a string.

    Raises:
        KeyError: When the key is not set in the environment.
    """
    value = os.environ.get(key)
    if value is None:
        raise KeyError(
            f"Required configuration key '{key}' is not set. "
            f"Add it to your .env file or export it in your shell."
        )
    return value
