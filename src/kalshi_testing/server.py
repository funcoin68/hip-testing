"""Boilerplate Kalshi WebSocket client for the demo trade API.

Usage:
    python -m kalshi_testing.server

Configuration comes from the environment (a gitignored local ``.env`` at the
repo root is loaded — by this module or the package ``__init__``, whichever
runs first — before any configuration is read):
- ``KALSHI_API_KEY_ID`` — Kalshi API key ID. Required.
- ``KALSHI_PRIVATE_KEY`` — RSA private key (PEM contents) used to sign the
  WebSocket handshake. Required.
- ``KALSHI_WS_URL`` — WebSocket URL, defaulting to the Kalshi DEMO trade API
  (fake money only).
- ``KALSHI_REST_URL`` — REST base URL, defaulting to the Kalshi DEMO trade
  API (fake money only). Used to resolve the event's market list.

A missing or empty credential fails fast — before any connection is opened —
with an error naming the variable. On startup the server fetches the markets
of ``EVENT_TICKER`` via the REST API (failing fast, naming the event ticker,
if the event is unknown or has no markets), subscribes to the ``trade``
channel for each of those markets, and prints every raw trade payload to
stdout until Ctrl+C, which exits cleanly (0).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import load_dotenv

# Load .env from the repo root ONLY, resolved from this file's location:
# when this module runs as __main__ (python server.py, PyCharm run configs)
# the package __init__ never executes, so the load must live here too.
# Never load from cwd — a planted .env could redirect the signed handshake
# via KALSHI_WS_URL. override=False (default) keeps real env vars authoritative.
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=_REPO_ROOT / ".env")

DEFAULT_WS_URL = "wss://demo-api.kalshi.co/trade-api/ws/v2"
DEFAULT_REST_URL = "https://demo-api.kalshi.co/trade-api/v2"
# Kalshi signs "<timestamp>GET<path>" for the WebSocket handshake.
_WS_SIGN_PATH = "/trade-api/ws/v2"
# The single event whose markets this server subscribes to.
EVENT_TICKER = "KXDAVISCUPMATCH-26NOV25COBKWON"


class MissingKalshiCredentialError(RuntimeError):
    """Raised when a Kalshi credential env var is unset, empty, or whitespace-only."""


class EventMarketsError(RuntimeError):
    """Raised when an event's market list cannot be fetched or is empty."""


@dataclass(frozen=True)
class KalshiConfig:
    """Server configuration. ``private_key`` is repr-redacted, but note that
    ``dataclasses.asdict(config)`` and ``vars(config)`` still expose it in
    cleartext — never pass either to logs or exception messages."""

    api_key_id: str
    ws_url: str
    rest_url: str
    private_key: str = field(repr=False)


def load_config(env: Mapping[str, str] | None = None) -> KalshiConfig:
    """Build a KalshiConfig from the environment, failing visibly on missing input."""
    source = os.environ if env is None else env
    api_key_id = source.get("KALSHI_API_KEY_ID", "")
    if not api_key_id.strip():
        raise MissingKalshiCredentialError(
            "KALSHI_API_KEY_ID is not set (or is empty). "
            "Provide a Kalshi API key ID via the environment; never hardcode one."
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
    rest_url = source.get("KALSHI_REST_URL") or DEFAULT_REST_URL
    if not rest_url.startswith("https://"):
        raise ValueError(
            f"KALSHI_REST_URL must use the https:// scheme (got {rest_url!r}); "
            "API traffic must never travel over plaintext http://."
        )
    return KalshiConfig(
        api_key_id=api_key_id, ws_url=ws_url, rest_url=rest_url, private_key=private_key
    )


def _auth_headers(api_key_id: str, private_key_pem: str) -> dict[str, str]:
    """Signed headers for the WebSocket handshake; the scheme follows the
    loaded key type (Ed25519 or RSA-PSS/SHA-256).

    Kalshi signs ``timestamp + "GET" + path``. The private key is never
    interpolated into any log or error message.
    """
    # A PEM stored in a single env var carries literal "\n" escapes; restore them.
    pem = private_key_pem.replace("\\n", "\n")
    private_key = serialization.load_pem_private_key(pem.encode(), password=None)
    timestamp = str(int(time.time() * 1000))
    message = f"{timestamp}GET{_WS_SIGN_PATH}".encode()
    if isinstance(private_key, Ed25519PrivateKey):
        signature = private_key.sign(message)  # Ed25519: no padding/digest params
    else:
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


def fetch_event_markets(rest_url: str, event_ticker: str) -> list[str]:
    """Return every market ticker belonging to ``event_ticker``.

    Public market-data GET — no signing needed. Follows the response cursor
    until empty so markets are never silently truncated. Raises
    ``EventMarketsError`` (naming the event ticker) on HTTP/transport failure
    or when the event yields zero markets — startup must never no-op silently.
    """
    tickers: list[str] = []
    cursor = ""
    while True:
        query = {"event_ticker": event_ticker, "limit": "1000"}
        if cursor:
            query["cursor"] = cursor
        url = f"{rest_url}/markets?{urllib.parse.urlencode(query)}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as exc:
            raise EventMarketsError(
                f"Kalshi REST request for event {event_ticker} failed: HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise EventMarketsError(
                f"Kalshi REST request for event {event_ticker} failed: {exc.reason}"
            ) from exc
        tickers.extend(market["ticker"] for market in payload.get("markets", []))
        cursor = payload.get("cursor") or ""
        if not cursor:
            break
    if not tickers:
        raise EventMarketsError(
            f"Kalshi event {event_ticker} returned zero markets; nothing to subscribe."
        )
    return tickers


async def _run(config: KalshiConfig) -> None:
    """Fetch the event's markets, subscribe per market, print raw trades.

    Raises EventMarketsError before any socket opens if the event cannot be
    resolved, and ConnectionError if the server closes the connection, so
    main() can map both to distinct non-zero exits. These are the module's
    only network seams besides ``websockets.connect`` — unit tests replace
    them wholesale.
    """
    market_tickers = fetch_event_markets(config.rest_url, EVENT_TICKER)
    headers = _auth_headers(config.api_key_id, config.private_key)
    async with websockets.connect(config.ws_url, additional_headers=headers) as ws:
        print(f"connected to {config.ws_url}", flush=True)
        # One subscribe command per market so an error frame's request id
        # attributes the failure to a single market ticker.
        subscription_markets: dict[int, str] = {}
        for sub_id, ticker in enumerate(market_tickers, start=1):
            subscription_markets[sub_id] = ticker
            await ws.send(
                json.dumps(
                    {
                        "id": sub_id,
                        "cmd": "subscribe",
                        "params": {"channels": ["trade"], "market_tickers": [ticker]},
                    }
                )
            )
        async for message in ws:
            try:
                payload = json.loads(message)
            except (TypeError, json.JSONDecodeError):
                continue  # not a JSON frame; nothing to act on
            msg_type = payload.get("type")
            if msg_type == "trade":
                print(message, flush=True)  # full raw trade payload
            elif msg_type == "error":
                ticker = subscription_markets.get(payload.get("id"), "<unknown>")
                print(
                    f"subscription error for market {ticker}: {message}",
                    file=sys.stderr,
                    flush=True,
                )
            # other message types (subscribed confirmations, etc.) are ignored
        # Server closed the connection: raise so main() exits non-zero — a
        # dropped connection must never look like a clean Ctrl+C shutdown
        # to a supervisor checking only the exit code.
        raise ConnectionError("server closed the connection")


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
    except EventMarketsError as exc:
        print(exc, file=sys.stderr)
        return 1  # startup failure: event unknown or has no markets
    except Exception as exc:
        print(f"Kalshi WebSocket connection failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
