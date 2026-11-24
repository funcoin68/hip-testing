"""Create a Lighter TESTNET API key for one account and print it to stdout.

Usage:
    python -m lighter_testing.api_keys --account-index 2081 --api-key-index 6

Flow (per https://docs.lighter.xyz — the API has no JSON "create apikey"
endpoint): a fresh stark keypair is generated locally, a ChangePubKey
transaction registering the public key is signed by the native Lighter signer
(bundled in the ``lighter-sdk`` package), the wallet authorizes it with an
EIP-191 personal_sign signature, and the transaction is submitted to
``POST /api/v1/sendTx`` on the testnet API. The new API private key exists
only in memory and is printed exactly once, on stdout.

Requires ``WALLET_PRIVATE_KEY`` (testnet wallet key, fake money only) from the
environment or a gitignored local ``.env`` — never hardcoded.
``LIGHTER_API_URL`` overrides the API host and is validated to be a
testnet URL before anything is sent; mainnet hosts are refused.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import lighter

from lighter_testing.client import (
    InvalidBaseUrlError,
    MissingPrivateKeyError,
    _validate_base_url,
    load_config,
)

DEFAULT_API_BASE_URL = "https://testnet.zklighter.io"


class ApiKeyCreationError(RuntimeError):
    """Raised when local key generation or on-chain registration fails."""


def register_api_key(
    *,
    api_base_url: str,
    wallet_private_key: str,
    account_index: int,
    api_key_index: int,
) -> str:
    """Generate a stark keypair and register its public key on the testnet.

    Returns the new API private key. This is the module's only network/signing
    seam — unit tests replace it wholesale.
    """
    api_private_key, api_public_key, err = lighter.create_api_key()
    if err is not None:
        raise ApiKeyCreationError(f"local API key generation failed: {err}")

    async def _submit() -> None:
        # SignerClient needs a running loop (aiohttp) and an API key to build
        # its native signer client; the not-yet-registered key is correct here
        # because the wallet's L1Sig — not a prior API key — authorizes the
        # ChangePubKey transaction.
        signer = lighter.SignerClient(
            url=api_base_url,
            account_index=account_index,
            api_private_keys={api_key_index: api_private_key},
        )
        try:
            response, send_err = await signer.change_api_key(
                eth_private_key=wallet_private_key,
                new_pubkey=api_public_key,
                api_key_index=api_key_index,
            )
        except ApiKeyCreationError:
            raise
        except Exception as exc:  # the SDK raises its own request exceptions
            raise ApiKeyCreationError(f"API key registration request failed: {exc}") from exc
        finally:
            await signer.api_client.close()
        if send_err is not None:
            raise ApiKeyCreationError(f"API key registration failed: {send_err}")
        if response is None or response.code != 200:
            message = getattr(response, "message", "no response")
            raise ApiKeyCreationError(f"API key registration refused: {message}")

    asyncio.run(_submit())
    return api_private_key


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m lighter_testing.api_keys",
        description="Create a Lighter TESTNET API key and print it to stdout.",
    )
    parser.add_argument("--account-index", type=int, required=True, help="Lighter account index")
    parser.add_argument(
        "--api-key-index", type=int, required=True, help="API key slot to register (0-254)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        config = load_config()
    except (MissingPrivateKeyError, InvalidBaseUrlError) as exc:
        print(exc, file=sys.stderr)
        return 2
    api_base_url = os.environ.get("LIGHTER_API_URL") or DEFAULT_API_BASE_URL
    try:
        _validate_base_url(api_base_url)
    except InvalidBaseUrlError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        api_key = register_api_key(
            api_base_url=api_base_url,
            wallet_private_key=config.private_key,
            account_index=args.account_index,
            api_key_index=args.api_key_index,
        )
    except ApiKeyCreationError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(api_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
