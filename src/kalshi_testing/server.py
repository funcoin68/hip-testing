"""Kalshi WebSocket server: auto-discovered NBA money-line trade feeds.

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
  API (fake money only). Used to discover NBA events and money-line markets.

A missing or empty credential fails fast — before any connection is opened —
with an error naming the variable. On startup the server discovers every NBA
event (Kalshi series ``KXNBAGAME``) with at least one market closing today or
tomorrow (America/New_York) and subscribes to the ``trade`` channel of each
money-line market ("Will the <team> win") in those events. Every
``RESCAN_SECONDS`` (4 hours, measured from startup) a re-scan subscribes
newly qualifying markets and unsubscribes closed/out-of-window ones; both
actions and every ``subscribed`` confirmation are logged. Scan failures are
logged to stderr and never crash the connection — the next scheduled scan
still runs. Every raw trade payload is printed to stdout until Ctrl+C, which
exits cleanly (0).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import websockets
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from dotenv import load_dotenv

from kalshi_testing.nba_scanner import RESCAN_SECONDS, discover_moneyline_markets

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


class MissingKalshiCredentialError(RuntimeError):
    """Raised when a Kalshi credential env var is unset, empty, or whitespace-only."""


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


async def _sleep_until_next_scan() -> None:
    """Delay until the next scheduled re-scan (module-level seam so tests can
    drive the re-scan loop without waiting ``RESCAN_SECONDS``)."""
    await asyncio.sleep(RESCAN_SECONDS)


async def _run(config: KalshiConfig) -> None:
    """Discover NBA money-line markets, subscribe per market, print raw trades.

    The initial scan runs at startup inside a try/except: a failure is
    logged to stderr and the server continues with an empty desired set —
    the scheduled re-scans still run. Raises ConnectionError if the server
    closes the connection, so main() can map it to a non-zero exit. These
    are the module's only network seams besides ``websockets.connect`` —
    unit tests replace them wholesale.
    """
    headers = _auth_headers(config.api_key_id, config.private_key)
    async with websockets.connect(config.ws_url, additional_headers=headers) as ws:
        print(f"connected to {config.ws_url}", flush=True)
        # One subscribe command per market so an error frame's request id
        # attributes the failure to a single market ticker.
        subscription_markets: dict[int, str] = {}  # request id -> market ticker
        ticker_sids: dict[str, list[str]] = {}  # market ticker -> confirmed sids
        next_request_id = 0

        async def _send(frame: dict) -> int:
            nonlocal next_request_id
            next_request_id += 1
            frame["id"] = next_request_id
            await ws.send(json.dumps(frame))
            return next_request_id

        async def _subscribe_market(ticker: str) -> None:
            request_id = await _send(
                {
                    "cmd": "subscribe",
                    "params": {"channels": ["trade"], "market_tickers": [ticker]},
                }
            )
            subscription_markets[request_id] = ticker
            print(f"subscribing to trades for market {ticker}", flush=True)

        async def _unsubscribe_market(ticker: str) -> None:
            # Kalshi v2 unsubscribes by sid; a market without a confirmed sid
            # has no server-side subscription to remove.
            sids = ticker_sids.pop(ticker, [])
            if not sids:
                return
            await _send({"cmd": "unsubscribe", "params": {"sids": sids}})
            print(
                f"unsubscribing from market {ticker} (sid: {', '.join(sids)})",
                flush=True,
            )

        async def _apply_desired(desired: dict[str, list[str]]) -> None:
            wanted = sorted({t for tickers in desired.values() for t in tickers})
            for ticker in wanted:
                if ticker not in ticker_sids:
                    await _subscribe_market(ticker)
            for ticker in sorted(t for t in ticker_sids if t not in set(wanted)):
                await _unsubscribe_market(ticker)

        async def _rescan_loop() -> None:
            while True:
                await _sleep_until_next_scan()
                try:
                    desired = discover_moneyline_markets(config.rest_url, datetime.now(UTC))
                except Exception as exc:  # scan failure must not kill the WS
                    print(
                        f"NBA money-line re-scan failed: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                await _apply_desired(desired)
                print(
                    f"re-scan complete: {sum(len(v) for v in desired.values())} "
                    "money-line market(s) in window",
                    flush=True,
                )

        try:
            initial = discover_moneyline_markets(config.rest_url, datetime.now(UTC))
        except Exception as exc:  # startup scan failure must not crash the server
            print(f"NBA money-line scan failed: {exc}", file=sys.stderr, flush=True)
            initial = {}
        await _apply_desired(initial)
        rescan_task = asyncio.create_task(_rescan_loop())
        try:
            async for message in ws:
                try:
                    payload = json.loads(message)
                except (TypeError, json.JSONDecodeError):
                    continue  # not a JSON frame; nothing to act on
                msg_type = payload.get("type")
                if msg_type == "trade":
                    print(message, flush=True)  # full raw trade payload
                elif msg_type == "subscribed":
                    ticker = subscription_markets.get(payload.get("id"), "<unknown>")
                    sids = (payload.get("msg") or {}).get("sids") or []
                    ticker_sids.setdefault(ticker, []).extend(sids)
                    print(
                        f"subscription confirmed for market {ticker} (sid: {', '.join(sids)})",
                        flush=True,
                    )
                elif msg_type == "error":
                    ticker = subscription_markets.get(payload.get("id"), "<unknown>")
                    print(
                        f"subscription error for market {ticker}: {message}",
                        file=sys.stderr,
                        flush=True,
                    )
            # Server closed the connection: raise so main() exits non-zero — a
            # dropped connection must never look like a clean Ctrl+C shutdown
            # to a supervisor checking only the exit code.
            raise ConnectionError("server closed the connection")
        finally:
            rescan_task.cancel()


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
