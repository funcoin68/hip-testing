"""Lighter testnet experiments.

Everything here targets the Lighter testnet with fake money only. The testnet
wallet (MetaMask) is recorded in docs/agent/pack-curated.md; private keys
never live in this repo.
"""

from lighter_testing.client import (
    DEFAULT_BASE_URL,
    LighterClient,
    LighterConfig,
    MissingPrivateKeyError,
    load_config,
)

LIGHTER_TESTNET = True  # hard guard: every on-chain path must be testnet-only

__all__ = [
    "DEFAULT_BASE_URL",
    "LIGHTER_TESTNET",
    "LighterClient",
    "LighterConfig",
    "MissingPrivateKeyError",
    "load_config",
]
