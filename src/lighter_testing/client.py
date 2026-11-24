"""Minimal Lighter testnet client boilerplate.

Configuration comes from the environment:
- ``LIGHTER_API_URL`` — API base URL (defaults to the Lighter testnet).
- ``WALLET_PRIVATE_KEY`` — testnet private key (fake money only). Required;
  a missing or empty key raises :class:`MissingPrivateKeyError` rather than
  falling back to any silent default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse

DEFAULT_BASE_URL = "https://api.testnet.lighter.xyz"
# Controlled domains for testnet hosts. A host is accepted only when the label
# immediately left of an allowed suffix is exactly `testnet` — an exact-host
# allowlist would forbid legitimate testnet subdomains (e.g. the docs default
# `api.testnet.lighter.xyz`), while a bare "testnet" substring or a testnet
# label in any position would admit `testnet.attacker.example`,
# `testnet.evil.lighter.xyz`, or `nottestnet.com`. `zklighter.io` is the
# Lighter API domain (https://testnet.zklighter.io); `lighter.xyz` is the
# web app domain.
_TESTNET_DOMAIN_SUFFIXES = (("lighter", "xyz"), ("zklighter", "io"))


class MissingPrivateKeyError(RuntimeError):
    """Raised when WALLET_PRIVATE_KEY is unset, empty, or whitespace-only."""


class InvalidBaseUrlError(RuntimeError):
    """Raised when LIGHTER_API_URL is not an https testnet URL."""


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
            f"LIGHTER_API_URL must use https:// (got {base_url!r}); "
            "a plaintext endpoint could expose the testnet private key."
        )
    labels = (parsed.hostname or "").lower().split(".")
    # Controlled-domain suffix match, not a bare substring: only hosts whose
    # label immediately left of an allowed suffix is exactly `testnet` pass
    # (api.testnet.lighter.xyz, testnet.zklighter.io), so mainnet, unknown,
    # or lookalike hosts (nottestnet.com, testnet.attacker.example,
    # testnet.evil.lighter.xyz, testnet.evilzklighter.io) are refused.
    if not (
        len(labels) >= 3
        and labels[-3] == "testnet"
        and any(tuple(labels[-len(suffix) :]) == suffix for suffix in _TESTNET_DOMAIN_SUFFIXES)
    ):
        raise InvalidBaseUrlError(
            f"LIGHTER_API_URL must be a testnet host under the Lighter "
            f"domain (got {base_url!r}); mainnet or unknown hosts are refused."
        )


def load_config(env: Mapping[str, str] | None = None) -> LighterConfig:
    """Build a LighterConfig from the environment, failing visibly on bad input."""
    source = os.environ if env is None else env
    base_url = source.get("LIGHTER_API_URL") or DEFAULT_BASE_URL
    _validate_base_url(base_url)
    private_key = source.get("WALLET_PRIVATE_KEY", "")
    if not private_key.strip():
        raise MissingPrivateKeyError(
            "WALLET_PRIVATE_KEY is not set (or is empty). "
            "Provide a Lighter testnet key via the environment; never hardcode one."
        )
    return LighterConfig(base_url=base_url, private_key=private_key)


class LighterClient:
    """Lighter testnet client (boilerplate — no network calls yet)."""

    def __init__(self, config: LighterConfig | None = None) -> None:
        self.config = config if config is not None else load_config()
