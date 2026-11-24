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
from dataclasses import dataclass

DEFAULT_BASE_URL = "https://testnet.app.lighter.xyz"


class MissingPrivateKeyError(RuntimeError):
    """Raised when LIGHTER_PRIVATE_KEY is unset, empty, or whitespace-only."""


@dataclass(frozen=True)
class LighterConfig:
    base_url: str
    private_key: str


def load_config(env: Mapping[str, str] | None = None) -> LighterConfig:
    """Build a LighterConfig from the environment, failing visibly on a missing key."""
    source = os.environ if env is None else env
    base_url = source.get("LIGHTER_API_BASE_URL") or DEFAULT_BASE_URL
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
