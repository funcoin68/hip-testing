"""Minimal Lighter testnet client boilerplate.

Configuration comes from the environment:
- ``LIGHTER_API_BASE_URL`` — API base URL (defaults to the Lighter testnet).
- ``LIGHTER_PRIVATE_KEY`` — testnet private key (fake money only). Required;
  a missing or empty key raises :class:`MissingPrivateKeyError` rather than
  falling back to any silent default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

DEFAULT_BASE_URL = "https://testnet.app.lighter.xyz"
# Controlled domain for testnet API hosts. A host is accepted only when it is
# `testnet.<anything>.lighter.xyz` — an exact-host allowlist would forbid
# legitimate testnet subdomains, while a bare "testnet" substring would admit
# `testnet.attacker.example` or `nottestnet.com`.
_TESTNET_DOMAIN_LABELS = ("lighter", "xyz")


class MissingPrivateKeyError(RuntimeError):
    """Raised when LIGHTER_PRIVATE_KEY is unset, empty, or whitespace-only."""


class InvalidBaseUrlError(RuntimeError):
    """Raised when LIGHTER_API_BASE_URL is not an https testnet URL."""


@dataclass(frozen=True)
class LighterConfig:
    """Client configuration. ``private_key`` is repr-redacted, but note that
    ``dataclasses.asdict(config)`` and ``vars(config)`` still expose it in
    cleartext — never pass either to logs or exception messages."""

    base_url: str
    private_key: str = field(repr=False)


def _validate_base_url(base_url: str) -> None:
    """Enforce the testnet-only invariant before any key could be sent anywhere."""
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise InvalidBaseUrlError(
            f"LIGHTER_API_BASE_URL must use https:// (got {base_url!r}); "
            "a plaintext endpoint could expose the testnet private key."
        )
    labels = (parsed.hostname or "").lower().split(".")
    # Controlled-domain suffix match, not a bare substring: only hosts of the
    # form testnet.<sub>.lighter.xyz pass, so mainnet, unknown, or lookalike
    # hosts (nottestnet.com, testnet.attacker.example) are refused.
    if not (
        len(labels) >= len(_TESTNET_DOMAIN_LABELS) + 2
        and labels[0] == "testnet"
        and tuple(labels[-len(_TESTNET_DOMAIN_LABELS) :]) == _TESTNET_DOMAIN_LABELS
    ):
        raise InvalidBaseUrlError(
            f"LIGHTER_API_BASE_URL must be a testnet host under the Lighter "
            f"domain (got {base_url!r}); mainnet or unknown hosts are refused."
        )


def load_config(env: Mapping[str, str] | None = None) -> LighterConfig:
    """Build a LighterConfig from the environment, failing visibly on bad input."""
    source = os.environ if env is None else env
    base_url = source.get("LIGHTER_API_BASE_URL") or DEFAULT_BASE_URL
    _validate_base_url(base_url)
    private_key = source.get("LIGHTER_PRIVATE_KEY", "")
    if not private_key.strip():
        raise MissingPrivateKeyError(
            "LIGHTER_PRIVATE_KEY is not set (or is empty). "
            "Provide a Lighter testnet key via the environment; never hardcode one."
        )
    return LighterConfig(base_url=base_url, private_key=private_key)


class LighterClient:
    """Lighter testnet client (boilerplate — no network calls yet)."""

    def __init__(self, config: LighterConfig | None = None) -> None:
        self.config = config if config is not None else load_config()
