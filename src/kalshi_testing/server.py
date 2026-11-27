"""Kalshi WebSocket server: auto-discovered NBA trade feeds.

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
  API (fake money only). Used to discover NBA events and their markets.
- ``SUPABASE_DB_URI`` — Supabase Postgres connection string (libpq format,
  without an embedded password). Required.
- ``SUPABASE_DB_PW`` — Supabase database password. Required.

A missing or empty credential fails fast — before any connection is opened —
with an error naming the variable. On startup the server discovers every NBA
event (Kalshi series ``KXNBAGAME``) with at least one market closing today or
tomorrow (America/New_York) and subscribes to the ``trade`` channel of every
market in those events. Every
``RESCAN_SECONDS`` (4 hours, measured from the end of the previous scan) a
re-scan subscribes newly qualifying markets and unsubscribes
closed/out-of-window ones; both actions and every ``subscribed``
confirmation are logged. Scan failures are logged to stderr and never crash
the connection — the next scheduled scan still runs. After every successful
scan the discovered events and markets are upserted into Supabase
(idempotent: unchanged rows are left untouched); events whose ticker does
not encode away/home teams are skipped for persistence with a warning.
Supabase failures are logged to stderr and never kill the WebSocket loop.
Every raw trade payload
is printed to stdout. A trade whose notional (``count_fp`` times the
taker-side price) is strictly above $1000 is a whale: it gets an additional
formatted stdout line and a row in ``whale_trades`` — exactly $1000 is not
a whale. If the connection drops for any reason (including a
``sent 1011 ... keepalive ping timeout``), the server reconnects
automatically — re-running the full startup sequence each time — with a
backoff that starts at 1s, doubles up to 30s, and resets to 1s after a
connection stays established for 60s. Every attempt, failure, and recovery
is logged to stdout with the attempt number. Ctrl+C exits cleanly (0).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import websockets
from dotenv import load_dotenv

from kalshi_testing import persistence
from kalshi_testing.auth import kalshi_auth_headers
from kalshi_testing.nba_scanner import RESCAN_SECONDS, EventRecord, discover_events
from kalshi_testing.persistence import (
    DbConfig,
    MissingSupabaseCredentialError,
    load_db_config,
)

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

# Reconnect backoff: starts at 1s, doubles up to 30s, and resets to 1s once a
# connection has stayed established for 60s. Retries are unbounded.
INITIAL_RECONNECT_DELAY_S = 1.0
MAX_RECONNECT_DELAY_S = 30.0
RECONNECT_DELAY_RESET_S = 60.0


def _next_delay(current: float) -> float:
    """Double the reconnect delay, capped at MAX_RECONNECT_DELAY_S."""
    return min(current * 2, MAX_RECONNECT_DELAY_S)


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


async def _sleep_for_reconnect(seconds: float) -> None:
    """Delay between reconnect attempts (module-level seam so tests can
    drive the backoff without real waiting)."""
    await asyncio.sleep(seconds)


def _persist_records(db_config: DbConfig, records: list[EventRecord]) -> None:
    """Blocking Supabase upsert of one scan's events then their markets
    (runs in a worker thread; the caller logs any failure)."""
    conn = persistence.connect(db_config)
    try:
        for record in records:
            event_id = persistence.upsert_event(conn, record)
            for market in record.markets:
                persistence.upsert_market(conn, event_id, market)
        conn.commit()
    finally:
        conn.close()


def _insert_whale_row(db_config: DbConfig, trade: dict, price_dollars: Decimal) -> None:
    """Blocking insert of one whale trade (runs in a worker thread)."""
    conn = persistence.connect(db_config)
    try:
        persistence.insert_whale_trade(conn, trade, price_dollars)
        conn.commit()
    finally:
        conn.close()


def _desired_markets(records: list[EventRecord]) -> dict[str, list[str]]:
    """The subscription shape derived from one scan's records."""
    return {record.event_ticker: [market.ticker for market in record.markets] for record in records}


async def _run(
    config: KalshiConfig, db_config: DbConfig, on_connected: Callable[[], None] | None = None
) -> None:
    """Discover NBA markets, subscribe per market, print raw trades.

    The initial scan runs at startup inside a try/except: a failure is
    logged to stderr and the server continues with an empty desired set —
    the scheduled re-scans still run. Both scans run via ``asyncio.to_thread``
    so the blocking REST calls never stall the trade-message loop. Raises
    ConnectionError if the server closes the connection, so the supervising
    loop treats it as a drop and reconnects. ``on_connected`` (if given) is
    invoked once immediately after the handshake succeeds. These are the
    module's only network seams besides ``websockets.connect`` — unit tests
    replace them wholesale.
    """
    headers = _auth_headers(config.api_key_id, config.private_key)
    async with websockets.connect(config.ws_url, additional_headers=headers) as ws:
        print(f"connected to {config.ws_url}", flush=True)
        if on_connected is not None:
            on_connected()
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

        async def _persist_events(records: list[EventRecord]) -> None:
            """Upsert one scan's events/markets into Supabase. Failures are
            logged to stderr and never kill the WebSocket loop; events whose
            ticker does not encode away/home teams are skipped with a
            warning (the events/markets tables require NOT NULL teams)."""
            persistable: list[EventRecord] = []
            for record in records:
                if record.away_team and record.home_team:
                    persistable.append(record)
                else:
                    print(
                        f"skipping persistence for event {record.event_ticker}: "
                        "ticker does not encode away/home teams",
                        file=sys.stderr,
                        flush=True,
                    )
            if not persistable:
                return
            try:
                await asyncio.to_thread(_persist_records, db_config, persistable)
            except Exception as exc:  # persistence failure must not kill the WS
                print(f"Supabase persistence failed: {exc}", file=sys.stderr, flush=True)

        async def _handle_trade(trade: dict) -> None:
            """Whale check + persistence for one trade. Never raises: a
            trade that cannot be evaluated is logged, and a persistence
            failure never kills the WebSocket loop."""
            try:
                price = persistence.side_price_dollars(trade)
                count_fp = trade.get("count_fp")
                if count_fp is None or not persistence.is_whale(count_fp, price):
                    return
            except ValueError as exc:
                print(f"trade skipped for whale detection: {exc}", file=sys.stderr, flush=True)
                return
            total = Decimal(str(count_fp)) * price
            print(
                f"whale trade: market {trade.get('market_ticker')} — {count_fp} contracts "
                f"@ ${price} ({trade.get('taker_outcome_side')} side) = ${total}",
                flush=True,
            )
            try:
                await asyncio.to_thread(_insert_whale_row, db_config, trade, price)
            except Exception as exc:  # persistence failure must not kill the WS
                print(f"whale trade persistence failed: {exc}", file=sys.stderr, flush=True)

        async def _rescan_loop() -> None:
            while True:
                await _sleep_until_next_scan()
                try:
                    # Blocking urllib I/O runs in a thread so the trade loop
                    # is not starved for the duration of the scan.
                    records = await asyncio.to_thread(
                        discover_events,
                        config.rest_url,
                        datetime.now(UTC),
                        config.api_key_id,
                        config.private_key,
                    )
                except Exception as exc:  # scan failure must not kill the WS
                    print(
                        f"NBA market re-scan failed: {exc}",
                        file=sys.stderr,
                        flush=True,
                    )
                else:
                    await _persist_events(records)
                    desired = _desired_markets(records)
                    await _apply_desired(desired)
                    print(
                        f"re-scan complete: {sum(len(v) for v in desired.values())} "
                        "market(s) in window",
                        flush=True,
                    )

        try:
            records = await asyncio.to_thread(
                discover_events,
                config.rest_url,
                datetime.now(UTC),
                config.api_key_id,
                config.private_key,
            )
        except Exception as exc:  # startup scan failure must not crash the server
            print(f"NBA market scan failed: {exc}", file=sys.stderr, flush=True)
            records = []
        await _persist_events(records)
        await _apply_desired(_desired_markets(records))
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
                    await _handle_trade(payload.get("msg") or {})
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
            # Server closed the connection: raise so the supervising loop
            # logs the drop and reconnects — a dropped connection must never
            # look like a clean shutdown.
            raise ConnectionError("server closed the connection")
        finally:
            rescan_task.cancel()


async def _supervise(
    config: KalshiConfig, db_config: DbConfig, *, clock: Callable[[], float] = time.monotonic
) -> int:
    """Run ``_run``, auto-reconnecting after any connection failure.

    Every attempt re-runs the full startup sequence inside ``_run`` (auth,
    connect, subscribe, trade loop). The backoff starts at
    INITIAL_RECONNECT_DELAY_S, doubles up to MAX_RECONNECT_DELAY_S, and
    resets to INITIAL_RECONNECT_DELAY_S once a connection stayed established
    for RECONNECT_DELAY_RESET_S; retries are unbounded. Returns 0 on Ctrl+C.
    """
    attempt = 0
    delay = INITIAL_RECONNECT_DELAY_S
    connected_at: float | None = None
    while True:
        attempt += 1
        print(f"connection attempt {attempt}", flush=True)
        connected_at = None

        def _mark_connected(attempt: int = attempt) -> None:
            nonlocal connected_at
            connected_at = clock()
            if attempt > 1:
                print(f"recovered on attempt {attempt}", flush=True)

        try:
            await _run(config, db_config, on_connected=_mark_connected)
            failure = "connection ended without error"
        except KeyboardInterrupt:
            return 0  # Ctrl+C: clean shutdown
        except Exception as exc:
            failure = str(exc)
        if connected_at is not None and clock() - connected_at >= RECONNECT_DELAY_RESET_S:
            delay = INITIAL_RECONNECT_DELAY_S  # long-lived connection: reset backoff
        print(
            f"connection attempt {attempt} failed: {failure}; retrying in {delay}s",
            flush=True,
        )
        await _sleep_for_reconnect(delay)
        delay = _next_delay(delay)


def main() -> int:
    try:
        config = load_config()
        db_config = load_db_config()
    except (MissingKalshiCredentialError, MissingSupabaseCredentialError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        return asyncio.run(_supervise(config, db_config))
    except KeyboardInterrupt:
        return 0  # Ctrl+C: clean shutdown


if __name__ == "__main__":
    raise SystemExit(main())
