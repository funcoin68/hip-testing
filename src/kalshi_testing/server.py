"""Boilerplate Kalshi WebSocket client for the demo trade API.

Usage:
    python -m kalshi_testing.server

Configuration comes from the environment (a gitignored local ``.env`` is
loaded by the package ``__init__`` before this module reads anything):
- ``KALSHI_API_KEY_ID`` — Kalshi API key ID. Required.
- ``KALSHI_PRIVATE_KEY`` — RSA private key (PEM contents) used to sign the
  WebSocket handshake. Required.
- ``KALSHI_WS_URL`` — WebSocket URL, defaulting to the Kalshi DEMO trade API
  (fake money only).

A missing or empty credential fails fast — before any connection is opened —
with an error naming the variable. With credentials present, the server
connects, logs "connected", and idles until Ctrl+C, which exits cleanly (0).
"""

from __future__ import annotations

import asyncio
import base64
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

DEFAULT_WS_URL = "wss://demo-api.kalshi.co/trade-api/ws/v2"
# Kalshi signs "<timestamp>GET<path>" for the WebSocket handshake.
_WS_SIGN_PATH = "/trade-api/ws/v2"


class MissingKalshiCredentialError(RuntimeError):
    """Raised when a Kalshi credential env var is unset, empty, or whitespace-only."""


@dataclass(frozen=True)
class KalshiConfig:
    """Server configuration. ``private_key`` is repr-redacted, but note that
    ``dataclasses.asdict(config)`` and ``vars(config)`` still expose it in
    cleartext — never pass either to logs or exception messages."""

    api_key_id: str
    ws_url: str
    private_key: str = field(repr=False)


def load_config(env: Mapping[str, str] | None = None) -> KalshiConfig:
    """Build a KalshiConfig from the environment, failing visibly on missing input."""
    source = os.environ if env is None else env
    api_key_id = source.get("KALSHI_API_KEY_ID", "")
    if not api_key_id.strip():
        raise MissingKalshiCredentialError(
            "KALSHI_API_KEY_ID is not set (or is empty). "
            "Provide a Kalshi demo API key ID via the environment; never hardcode one."
        )
    private_key = source.get("KALSHI_PRIVATE_KEY", "")
    if not private_key.strip():
        raise MissingKalshiCredentialError(
            "KALSHI_PRIVATE_KEY is not set (or is empty). "
            "Provide the Kalshi demo RSA private key (PEM contents) via the "
            "environment; never hardcode one."
        )
    ws_url = source.get("KALSHI_WS_URL") or DEFAULT_WS_URL
    if not ws_url.startswith("wss://"):
        raise ValueError(
            f"KALSHI_WS_URL must use the wss:// scheme (got {ws_url!r}); "
            "signed credentials must never travel over plaintext ws://."
        )
    return KalshiConfig(api_key_id=api_key_id, ws_url=ws_url, private_key=private_key)


def _auth_headers(api_key_id: str, private_key_pem: str) -> dict[str, str]:
    """RSA-PSS/SHA-256 signed headers for the WebSocket handshake.

    Kalshi signs ``timestamp + "GET" + path``. The private key is never
    interpolated into any log or error message.
    """
    # A PEM stored in a single env var carries literal "\n" escapes; restore them.
    pem = private_key_pem.replace("\\n", "\n")
    private_key = serialization.load_pem_private_key(pem.encode(), password=None)
    timestamp = str(int(time.time() * 1000))
    message = f"{timestamp}GET{_WS_SIGN_PATH}".encode()
    signature = private_key.sign(
        message,
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
        hashes.SHA256(),
    )
    return {
        "KALSHI-ACCESS-KEY": api_key_id,
        "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode(),
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
    }


async def _run(config: KalshiConfig) -> None:
    """Connect, log the handshake, then idle until cancelled.

    This is the module's only network seam — unit tests replace it wholesale.
    """
    headers = _auth_headers(config.api_key_id, config.private_key)
    async with websockets.connect(config.ws_url, additional_headers=headers) as ws:
        print(f"connected to {config.ws_url}", flush=True)
        async for _message in ws:
            pass  # boilerplate: log-and-discard, no message handling yet
        # Server closed the connection: surface it so a silent stop is never
        # mistaken for a clean Ctrl+C shutdown.
        print("connection closed by server", file=sys.stderr)


def main() -> int:
    try:
        config = load_config()
    except (MissingKalshiCredentialError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        asyncio.run(_run(config))
    except KeyboardInterrupt:
        return 0  # Ctrl+C: clean shutdown
    except Exception as exc:
        print(f"Kalshi WebSocket connection failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
