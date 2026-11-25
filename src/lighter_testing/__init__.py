"""Lighter testnet experiments.

Everything here targets the Lighter testnet with fake money only. The testnet
wallet (MetaMask) is recorded in docs/agent/pack-curated.md; private keys
never live in this repo.
"""

from pathlib import Path

from dotenv import load_dotenv


def _load_dotenv(search_start: Path | None = None) -> None:
    """Load .env from the cwd, then fall back to the repo root.

    The cwd load comes first and is resolved explicitly against cwd (NOT
    load_dotenv()'s default find_dotenv, which walks up from this installed
    file and would miss the caller's cwd). The fallback then walks up from
    this file and loads the first .env found, so debuggers whose working
    directory is the package dir (e.g. PyCharm) still pick up the repo-root
    .env. The walk stops at the first directory containing pyproject.toml —
    that is the repo root — so an unrelated .env in $HOME or / can never
    leak in. Both loads use override=False (the default), keeping real
    environment variables authoritative: shell env > cwd .env > repo-root
    .env, so CI secrets are never shadowed by a stray .env. Any module that
    reads os.environ at import time must come after this call.
    """
    load_dotenv(dotenv_path=Path.cwd() / ".env")
    start = (search_start if search_start is not None else Path(__file__)).resolve()
    for directory in start.parents:
        env_path = directory / ".env"
        if env_path.exists():
            load_dotenv(dotenv_path=env_path)
            return
        if (directory / "pyproject.toml").exists():
            return


_load_dotenv()

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
