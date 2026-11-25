"""Lighter testnet experiments.

Everything here targets the Lighter testnet with fake money only. The testnet
wallet (MetaMask) is recorded in docs/agent/pack-curated.md; private keys
never live in this repo.
"""

from pathlib import Path

from dotenv import load_dotenv

# Load .env from the current working directory at import time. The path is
# resolved explicitly against cwd (NOT load_dotenv()'s default find_dotenv,
# which walks up from this installed file and would miss the caller's cwd).
# override=False (the default) keeps real environment variables authoritative,
# so CI secrets are never shadowed by a stray .env. Any module that reads
# os.environ at import time must come after this call.
load_dotenv(dotenv_path=Path.cwd() / ".env")

from lighter_testing.api_keys import (  # noqa: E402
    DEFAULT_API_BASE_URL,
    ApiKeyCreationError,
    register_api_key,
)
from lighter_testing.client import (  # noqa: E402
    DEFAULT_BASE_URL,
    InvalidBaseUrlError,
    LighterClient,
    LighterConfig,
    MissingPrivateKeyError,
    load_config,
)
from lighter_testing.orders import LighterOrderClient, OrderError  # noqa: E402

LIGHTER_TESTNET = True  # hard guard: every on-chain path must be testnet-only

__all__ = [
    "DEFAULT_API_BASE_URL",
    "DEFAULT_BASE_URL",
    "LIGHTER_TESTNET",
    "ApiKeyCreationError",
    "InvalidBaseUrlError",
    "LighterClient",
    "LighterConfig",
    "LighterOrderClient",
    "MissingPrivateKeyError",
    "OrderError",
    "load_config",
    "register_api_key",
]
