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
``RESCAN_SECONDS`` (4 hours, measured from the end of the previous scan) a
re-scan subscribes newly qualifying markets and unsubscribes
closed/out-of-window ones; both actions and every ``subscribed``
confirmation are logged. Scan failures are logged to stderr and never crash
the connection — the next scheduled scan still runs. Every raw trade payload
is printed to stdout until Ctrl+C, which exits cleanly (0).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import websockets
from dotenv import load_dotenv

from kalshi_testing.auth import kalshi_auth_headers
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
    """Signed headers for the WebSocket handshake; the shared signer lives in
    ``kalshi_testing.auth`` so REST and WS signing never drift apart.

    Kalshi signs ``timestamp + "GET" + path``. The private key is never
    interpolated into any log or error message.
    """
    return kalshi_auth_headers(api_key_id, private_key_pem, "GET", _WS_SIGN_PATH)


async def _sleep_until_next_scan() -> None:
    """Delay until the next scheduled re-scan (module-level seam so tests can
    drive the re-scan loop without waiting ``RESCAN_SECONDS``)."""
    await asyncio.sleep(RESCAN_SECONDS)


async def _run(config: KalshiConfig) -> None:
    """Discover NBA money-line markets, subscribe per market, print raw trades.

    The initial scan runs at startup inside a try/except: a failure is
    logged to stderr and the server continues with an empty desired set —
    the scheduled re-scans still run. Both scans run via ``asyncio.to_thread``
    so the blocking REST calls never stall the trade-message loop. Raises
    ConnectionError if the server closes the connection, so main() can map it
    to a non-zero exit. These are the module's only network seams besides
    ``websockets.connect`` — unit tests replace them wholesale.
    """
    headers = _auth_headers(config.api_key_id, config.private_key)
    async with websockets.connect(config.ws_url, additional_headers=headers) as ws:
        print(f"connected to {config.ws_url}", flush=True)
        # One subscribe command per market so an error frame's request id
        # attributes the failure to a single market ticker.
        subscription_markets: dict[int, str] = {}  # request id -> market ticker
        ticker_sids: dict[str, list[str]] = {}  # market ticker -> confirmed sids
        # Subscribe frame sent but `subscribed` confirmation not yet processed;
        # without this, a re-scan would double-subscribe (still wanted) or
        # silently lose track of a server-side subscription (no longer wanted).
        pending_subscriptions: set[str] = set()
        wanted_tickers: set[str] = set()  # tickers from the last successful scan
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
            pending_subscriptions.add(ticker)
            print(f"subscribing to trades for market {ticker}", flush=True)

        async def _unsubscribe_market(ticker: str) -> None:
            # Kalshi v2 unsubscribes by sid; without a confirmed sid there is
            # nothing to send — but that state must stay visible in the logs,
            # never silently dropped.
            sids = ticker_sids.pop(ticker, [])
            if not sids:
                print(
                    f"skipping unsubscribe for market {ticker}: no confirmed "
                    "sid (subscribe sent, confirmation never arrived); the "
                    "subscription is retried once the confirmation lands",
                    file=sys.stderr,
                    flush=True,
                )
                return
            await _send({"cmd": "unsubscribe", "params": {"sids": sids}})
            print(
                f"unsubscribing from market {ticker} (sid: {', '.join(sids)})",
                flush=True,
            )

        async def _apply_desired(desired: dict[str, list[str]]) -> None:
            wanted = sorted({t for tickers in desired.values() for t in tickers})
            wanted_tickers.clear()
            wanted_tickers.update(wanted)
            for ticker in wanted:
                # Skip tickers whose subscribe is still awaiting confirmation —
                # re-sending would create a duplicate server-side subscription.
                if ticker not in ticker_sids and ticker not in pending_subscriptions:
                    await _subscribe_market(ticker)
            for ticker in sorted(
                t for t in set(ticker_sids) | pending_subscriptions if t not in wanted_tickers
            ):
                await _unsubscribe_market(ticker)

        async def _rescan_loop() -> None:
            while True:
                try:
                    # Blocking urllib I/O runs in a thread so the trade loop
                    # is not starved for the duration of the scan.
                    desired = await asyncio.to_thread(
                        discover_moneyline_markets,
                        config.rest_url,
                        datetime.now(UTC),
                        config.api_key_id,
                        config.private_key,
                    )
                except Exception as exc:  # scan failure must not kill the WS
                    print(
                        f"NBA money-line re-scan failed: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    await _apply_desired(desired)
                    print(
                        f"re-scan complete: {sum(len(v) for v in desired.values())} "
                        "money-line market(s) in window",
                        flush=True,
                    )
                # Sleep after every iteration — success or failure — so the
                # re-scan cadence (measured from the end of the previous scan)
                # survives a failed scan instead of spinning.
                await _sleep_until_next_scan()

        try:
            initial = await asyncio.to_thread(
                discover_moneyline_markets,
                config.rest_url,
                datetime.now(UTC),
                config.api_key_id,
                config.private_key,
            )
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
                    was_pending = ticker in pending_subscriptions
                    pending_subscriptions.discard(ticker)
                    ticker_sids.setdefault(ticker, []).extend(sids)
                    print(
                        f"subscription confirmed for market {ticker} (sid: {', '.join(sids)})",
                        flush=True,
                    )
                    if was_pending and ticker not in wanted_tickers:
                        # The confirmation raced a re-scan that already dropped
                        # this market: unsubscribe it now that a sid exists.
                        await _unsubscribe_market(ticker)
                elif msg_type == "error":
                    ticker = subscription_markets.get(payload.get("id"), "<unknown>")
                    # A rejected subscribe is no longer pending, so the next
                    # re-scan may retry it.
                    pending_subscriptions.discard(ticker)
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
