"""
Third-party API verification layer (verification method #2).

Design
------
* :class:`BaseApiVerifier` is an abstract base class (ABC).  Any provider
  must implement ``verify(email) -> ApiResult`` and expose a ``provider_name``
  property.  This makes it trivial to swap or add providers later.

* :class:`HunterVerifier` implements Hunter.io's email-verifier endpoint.
  It reads the API key from the environment (``HUNTER_API_KEY``).

* :class:`MockVerifier` is a fully-configurable test double.  Tests should
  use it instead of hitting real APIs.  Enable it by passing
  ``provider="mock"`` to :func:`get_verifier`.

* :func:`get_verifier` is a factory that instantiates the right provider
  from a name string — makes CLI and future orchestration code easy.

Adding a new provider
---------------------
1. Subclass :class:`BaseApiVerifier`.
2. Implement ``provider_name`` and ``verify``.
3. Register the name in :func:`get_verifier`.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import requests

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

class ApiStatus(str, Enum):
    """Normalised status returned by any API verifier."""

    VALID   = "valid"    # Provider is confident the address is deliverable
    INVALID = "invalid"  # Provider is confident the address is not deliverable
    UNKNOWN = "unknown"  # Provider cannot determine deliverability (incl. catch-all)
    ERROR   = "error"    # Call failed (network, auth, rate-limit, …)


@dataclass
class ApiResult:
    """Outcome of a single API verification call."""

    email: str
    """The address that was verified."""

    status: ApiStatus
    """Normalised deliverability status."""

    provider: str
    """Provider name (e.g. ``'hunter.io'``)."""

    score: int | None = None
    """Provider-supplied confidence score (0–100), if available."""

    raw_status: str = ""
    """The provider's own status string, before normalisation."""

    detail: str = ""
    """Human-readable explanation."""

    extra: dict[str, Any] = field(default_factory=dict)
    """Any extra structured data from the provider (disposable, gibberish, …)."""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseApiVerifier(ABC):
    """Abstract interface for a third-party email verification provider."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Short identifier for this provider (e.g. ``'hunter.io'``)."""

    @abstractmethod
    def verify(self, email: str) -> ApiResult:
        """Verify *email* and return a normalised :class:`ApiResult`.

        Implementations must never raise — all errors should be captured
        in the returned result with ``status=ApiStatus.ERROR``.
        """


# ---------------------------------------------------------------------------
# Hunter.io implementation
# ---------------------------------------------------------------------------

class HunterVerifier(BaseApiVerifier):
    """Email verifier backed by the Hunter.io API v2.

    Docs: https://hunter.io/api-documentation/v2#email-verifier

    Args:
        api_key: Hunter.io API key.  Obtain from https://hunter.io/api_keys.
                 Never hardcode — read from ``HUNTER_API_KEY`` in your ``.env``.
        timeout: HTTP request timeout in seconds.
        session: Optional :class:`requests.Session` to reuse (useful for
                 testing / connection pooling).
    """

    _BASE_URL = "https://api.hunter.io/v2/email-verifier"

    # Hunter's status strings → our normalised ApiStatus
    _STATUS_MAP: dict[str, ApiStatus] = {
        "valid":      ApiStatus.VALID,
        "invalid":    ApiStatus.INVALID,
        "accept_all": ApiStatus.UNKNOWN,   # catch-all domain
        "unknown":    ApiStatus.UNKNOWN,
    }

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 15.0,
        session: requests.Session | None = None,
    ) -> None:
        if not api_key:
            raise ValueError(
                "Hunter.io API key is empty.  Set HUNTER_API_KEY in your .env file."
            )
        self._api_key = api_key
        self._timeout = timeout
        self._session = session or requests.Session()

    @property
    def provider_name(self) -> str:
        return "hunter.io"

    def verify(self, email: str) -> ApiResult:
        """Call Hunter.io's email-verifier endpoint and return a normalised result."""
        _log.debug("GET %s?email=%s", self._BASE_URL, email)
        try:
            resp = self._session.get(
                self._BASE_URL,
                params={"email": email, "api_key": self._api_key},
                timeout=self._timeout,
            )
        except requests.Timeout:
            return ApiResult(
                email=email,
                status=ApiStatus.ERROR,
                provider=self.provider_name,
                detail="Request to Hunter.io timed out.",
            )
        except requests.RequestException as exc:
            return ApiResult(
                email=email,
                status=ApiStatus.ERROR,
                provider=self.provider_name,
                detail=f"Network error calling Hunter.io: {exc}",
            )
        except Exception as exc:  # noqa: BLE001  — verify() must never raise
            return ApiResult(
                email=email,
                status=ApiStatus.ERROR,
                provider=self.provider_name,
                detail=f"Unexpected error calling Hunter.io: {exc}",
            )

        # ── HTTP-level errors ─────────────────────────────────────────────
        if resp.status_code == 401:
            return ApiResult(
                email=email,
                status=ApiStatus.ERROR,
                provider=self.provider_name,
                detail="Hunter.io authentication failed — check your HUNTER_API_KEY.",
            )
        if resp.status_code == 429:
            return ApiResult(
                email=email,
                status=ApiStatus.ERROR,
                provider=self.provider_name,
                detail="Hunter.io rate limit exceeded.  Wait before retrying.",
            )
        if resp.status_code == 422:
            return ApiResult(
                email=email,
                status=ApiStatus.INVALID,
                provider=self.provider_name,
                raw_status="invalid_format",
                detail="Hunter.io rejected the address as malformed.",
            )
        if not resp.ok:
            return ApiResult(
                email=email,
                status=ApiStatus.ERROR,
                provider=self.provider_name,
                detail=f"Hunter.io returned HTTP {resp.status_code}.",
            )

        # ── Parse JSON ────────────────────────────────────────────────────
        try:
            body = resp.json()
        except ValueError:
            return ApiResult(
                email=email,
                status=ApiStatus.ERROR,
                provider=self.provider_name,
                detail="Hunter.io returned non-JSON response.",
            )

        data       = body.get("data", {})
        raw_status = data.get("status", "unknown")
        score      = data.get("score")          # int 0–100 or None
        result_str = data.get("result", "")     # "deliverable" / "undeliverable" / …

        status = self._STATUS_MAP.get(raw_status, ApiStatus.UNKNOWN)
        _log.debug(
            "Hunter.io response for %s: raw_status=%r score=%s result=%r → %s",
            email, raw_status, score, result_str, status.value,
        )

        detail_parts = [f"status={raw_status!r}"]
        if result_str:
            detail_parts.append(f"result={result_str!r}")
        if score is not None:
            detail_parts.append(f"score={score}")

        extra = {k: data[k] for k in
                 ("disposable", "gibberish", "webmail", "accept_all", "block",
                  "mx_records", "smtp_server", "smtp_check")
                 if k in data}

        return ApiResult(
            email=email,
            status=status,
            provider=self.provider_name,
            score=score,
            raw_status=raw_status,
            detail="Hunter.io: " + ", ".join(detail_parts),
            extra=extra,
        )


# ---------------------------------------------------------------------------
# Mock verifier (test double)
# ---------------------------------------------------------------------------

class MockVerifier(BaseApiVerifier):
    """Configurable test double — never makes real HTTP calls.

    Args:
        responses:      Map of email → :class:`ApiResult` to return for
                        specific addresses.
        default_status: Status returned for any email not in *responses*.
        default_score:  Score returned for addresses using the default.
    """

    def __init__(
        self,
        responses: dict[str, ApiResult] | None = None,
        *,
        default_status: ApiStatus = ApiStatus.VALID,
        default_score: int = 85,
    ) -> None:
        self._responses = responses or {}
        self._default_status = default_status
        self._default_score = default_score

    @property
    def provider_name(self) -> str:
        return "mock"

    def verify(self, email: str) -> ApiResult:
        if email in self._responses:
            return self._responses[email]
        return ApiResult(
            email=email,
            status=self._default_status,
            provider=self.provider_name,
            score=self._default_score,
            raw_status=self._default_status.value,
            detail=f"Mock response: {self._default_status.value} (score={self._default_score})",
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_SUPPORTED_PROVIDERS = ("hunter", "mock")


def get_verifier(
    provider: str,
    api_key: str = "",
    **kwargs: Any,
) -> BaseApiVerifier:
    """Instantiate a :class:`BaseApiVerifier` by provider name.

    Args:
        provider: One of ``'hunter'`` or ``'mock'``.
        api_key:  API key for the chosen provider.  Pass an empty string for
                  ``'mock'``.  For ``'hunter'``, leave blank to read
                  ``HUNTER_API_KEY`` from the environment automatically.
        **kwargs: Extra keyword arguments forwarded to the provider's
                  ``__init__``.

    Returns:
        A configured :class:`BaseApiVerifier` instance.

    Raises:
        ValueError: For unsupported provider names or a missing API key.
    """
    p = provider.lower().strip()

    if p == "hunter":
        key = api_key or os.environ.get("HUNTER_API_KEY", "")
        return HunterVerifier(api_key=key, **kwargs)

    if p == "mock":
        return MockVerifier(**kwargs)

    raise ValueError(
        f"Unknown provider {provider!r}.  "
        f"Supported providers: {', '.join(_SUPPORTED_PROVIDERS)}."
    )
