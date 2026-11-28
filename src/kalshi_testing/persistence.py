"""Supabase persistence for Kalshi events (NBA and NFL), markets, and whale trades.

Configuration comes from ``SUPABASE_DB_URI`` (a libpq connection string
without an embedded password) and ``SUPABASE_DB_PW``, loaded from the
environment (the package ``__init__`` loads the repo-root ``.env`` first).
Both are required: a missing or blank variable raises
``MissingSupabaseCredentialError`` at startup rather than silently skipping
persistence.

Event/market ingestion is idempotent read-compare-write: each record is
compared field-by-field against the stored row (``plan_upsert``), inserted
when absent, updated in place when a field changed, and left completely
untouched — no UPDATE statement is issued, so the ``updated_at`` trigger
never fires — when nothing changed. ``Decimal`` arithmetic is used for all
money quantities; Kalshi sends them as strings and floats never touch them.

A trade is a whale when ``count_fp * price_dollars`` is strictly above
``WHALE_THRESHOLD_DOLLARS`` (exactly $2499 is not a whale) AND the price is
below ``WHALE_MAX_PRICE_DOLLARS`` ($0.90) — a trade priced at or above the
cutoff is never a whale, whatever its notional. The stored
``price_dollars`` is the taker-side price: the yes price for a "yes" taker,
the no price for a "no" taker.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kalshi_testing.nba_scanner import EventRecord, MarketRecord

# A trade whose notional is strictly above this many dollars is a whale.
WHALE_THRESHOLD_DOLLARS = Decimal("2499")

# Trades priced at or above this many dollars per contract are never whales.
WHALE_MAX_PRICE_DOLLARS = Decimal("0.90")

_EVENT_FIELDS = ("event_ticker", "title", "url", "espn_start_ts", "away_team", "home_team")
_MARKET_FIELDS = ("event_id", "market_ticker", "title", "close_time")


class MissingSupabaseCredentialError(RuntimeError):
    """Raised when SUPABASE_DB_URI or SUPABASE_DB_PW is unset, empty, or whitespace-only."""


@dataclass(frozen=True)
class DbConfig:
    """Supabase connection config. ``password`` is repr-redacted, but note
    that ``dataclasses.asdict``/``vars`` still expose it in cleartext —
    never pass either to logs or exception messages."""

    uri: str
    password: str = field(repr=False)


def load_db_config(env: Mapping[str, str] | None = None) -> DbConfig:
    """Build a DbConfig from the environment, failing visibly on missing input."""
    source = os.environ if env is None else env
    uri = source.get("SUPABASE_DB_URI", "")
    if not uri.strip():
        raise MissingSupabaseCredentialError(
            "SUPABASE_DB_URI is not set (or is empty). Provide the Supabase "
            "Postgres connection string (without an embedded password) via the "
            "environment; never hardcode one."
        )
    password = source.get("SUPABASE_DB_PW", "")
    if not password.strip():
        raise MissingSupabaseCredentialError(
            "SUPABASE_DB_PW is not set (or is empty). Provide the Supabase "
            "database password via the environment; never hardcode one."
        )
    return DbConfig(uri=uri, password=password)


def connect(config: DbConfig):
    """Open a psycopg connection — the module's only live-database seam.

    The driver import is lazy so unit tests (which use fake connections)
    never require psycopg to be installed. TLS is fail-closed: unless the
    URI sets its own ``sslmode`` (e.g. ``verify-full``), ``sslmode=require``
    is forced so a misconfigured URI cannot silently downgrade the
    SUPABASE_DB_PW to a plaintext connection (libpq's default ``prefer``
    would).
    """
    import psycopg

    kwargs: dict[str, Any] = {"password": config.password}
    if not _uri_sets_sslmode(config.uri):
        kwargs["sslmode"] = "require"
    return psycopg.connect(config.uri, **kwargs)


def _uri_sets_sslmode(uri: str) -> bool:
    """True when the connection string carries an ``sslmode`` setting.

    The URI query string is parsed with ``urllib.parse`` rather than
    substring-matched so an ``sslmode=`` appearing inside another
    parameter's value cannot suppress the forced ``sslmode=require`` (which
    would let libpq's ``prefer`` default downgrade the connection to
    plaintext). The keyword/value libpq format (``host=... sslmode=...``)
    is matched on a whitespace-delimited token for the same reason.
    """
    query = urllib.parse.urlsplit(uri).query
    if "sslmode" in urllib.parse.parse_qs(query):
        return True
    return re.search(r"(?:^|\s)sslmode\s*=", uri) is not None


def side_price_dollars(trade: Mapping[str, Any]) -> Decimal:
    """The taker-side dollar price of one trade: ``yes_price_dollars`` when
    ``taker_outcome_side`` is "yes", ``no_price_dollars`` when it is "no".

    An unknown side or a missing/malformed price raises ``ValueError`` —
    the side is never silently guessed.
    """
    side = trade.get("taker_outcome_side")
    if side == "yes":
        key = "yes_price_dollars"
    elif side == "no":
        key = "no_price_dollars"
    else:
        raise ValueError(f"unknown taker_outcome_side {side!r} — cannot pick a side-matched price")
    raw = trade.get(key)
    if raw is None:
        raise ValueError(f"trade is missing {key}")
    try:
        return Decimal(str(raw))
    except InvalidOperation as exc:
        raise ValueError(f"malformed {key} {raw!r}") from exc


def is_whale(count_fp: Any, price_dollars: Decimal) -> bool:
    """True when ``count_fp * price_dollars`` is strictly above $2499 AND the
    price is below ``WHALE_MAX_PRICE_DOLLARS``.

    Exactly $2499 is not a whale, and a price at or above the $0.90 cutoff
    disqualifies the trade regardless of notional. ``count_fp`` arrives as a
    string; a malformed value raises ``ValueError``.
    """
    try:
        count = Decimal(str(count_fp))
    except InvalidOperation as exc:
        raise ValueError(f"malformed count_fp {count_fp!r}") from exc
    if price_dollars >= WHALE_MAX_PRICE_DOLLARS:
        return False
    return count * price_dollars > WHALE_THRESHOLD_DOLLARS


def plan_upsert(existing: Mapping[str, Any] | None, new: Mapping[str, Any]) -> str:
    """Decide what to do with ``new`` given the stored row: "insert" when no
    row exists, "noop" when every managed field already matches (extra keys
    on ``existing`` such as ``id`` are ignored), "update" otherwise."""
    if existing is None:
        return "insert"
    if all(existing.get(key) == value for key, value in new.items()):
        return "noop"
    return "update"


def _iso_to_datetime(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp for a timestamptz column (None passes
    through). A naive input is treated as UTC so the parsed value always
    compares equal to the timezone-aware value the column returns —
    otherwise every re-ingest would compare unequal and UPDATE the row,
    defeating the untouched-when-unchanged guarantee."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def upsert_event(conn, record: EventRecord) -> int:
    """Insert or update one ``kalshi_events`` row and return its id.

    A record matching the stored row issues no write at all; a changed
    record is updated in place (same id), so re-ingesting an event never
    creates a second row.
    """
    new = {
        "event_ticker": record.event_ticker,
        "title": record.title,
        "url": record.url,
        "espn_start_ts": _iso_to_datetime(record.espn_start_ts),
        "away_team": record.away_team,
        "home_team": record.home_team,
    }
    row = conn.execute(
        "select id, event_ticker, title, url, espn_start_ts, away_team, home_team "
        "from kalshi_events where event_ticker = %s",
        (record.event_ticker,),
    ).fetchone()
    existing = None if row is None else dict(zip(_EVENT_FIELDS, row[1:], strict=True))
    action = plan_upsert(existing, new)
    if action == "insert":
        inserted = conn.execute(
            "insert into kalshi_events "
            "(event_ticker, title, url, espn_start_ts, away_team, home_team) "
            "values (%s, %s, %s, %s, %s, %s) returning id",
            tuple(new[name] for name in _EVENT_FIELDS),
        ).fetchone()
        return inserted[0]
    if action == "update":
        conn.execute(
            "update kalshi_events set title = %s, url = %s, espn_start_ts = %s, "
            "away_team = %s, home_team = %s where event_ticker = %s",
            (
                new["title"],
                new["url"],
                new["espn_start_ts"],
                new["away_team"],
                new["home_team"],
                record.event_ticker,
            ),
        )
    return row[0]


def upsert_market(conn, event_id: int, record: MarketRecord) -> None:
    """Insert or update one ``kalshi_markets`` row under ``event_id``.

    Same read-compare-write semantics as ``upsert_event``: re-ingesting an
    unchanged market issues no write, so its ``updated_at`` never bumps.
    """
    new = {
        "event_id": event_id,
        "market_ticker": record.ticker,
        "title": record.title,
        "close_time": _iso_to_datetime(record.close_time),
    }
    row = conn.execute(
        "select id, event_id, market_ticker, title, close_time "
        "from kalshi_markets where market_ticker = %s",
        (record.ticker,),
    ).fetchone()
    existing = None if row is None else dict(zip(_MARKET_FIELDS, row[1:], strict=True))
    action = plan_upsert(existing, new)
    if action == "insert":
        conn.execute(
            "insert into kalshi_markets (event_id, market_ticker, title, close_time) "
            "values (%s, %s, %s, %s)",
            tuple(new[name] for name in _MARKET_FIELDS),
        )
    elif action == "update":
        conn.execute(
            "update kalshi_markets set event_id = %s, title = %s, close_time = %s "
            "where market_ticker = %s",
            (new["event_id"], new["title"], new["close_time"], record.ticker),
        )


def insert_whale_trade(conn, trade: Mapping[str, Any], price_dollars: Decimal) -> None:
    """Append one whale trade row (immutable — there is no update path).

    ``price_dollars`` is the side-matched price from ``side_price_dollars``;
    a missing trade field raises ``KeyError`` rather than inserting a
    partial row.
    """
    conn.execute(
        "insert into whale_trades (market_ticker, taker_outcome_side, taker_book_side, "
        "count_fp, price_dollars, is_block_trade, ts_ms) values (%s, %s, %s, %s, %s, %s, %s)",
        (
            trade["market_ticker"],
            trade["taker_outcome_side"],
            trade["taker_book_side"],
            Decimal(str(trade["count_fp"])),
            price_dollars,
            bool(trade["is_block_trade"]),
            int(trade["ts_ms"]),
        ),
    )
