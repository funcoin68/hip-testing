"""Behavior tests for Supabase persistence (kalshi_testing.persistence).

No live database: connections are an in-memory ``FakeConn`` implementing
exactly the SQL statements the module issues, so re-ingest semantics
(insert / update / untouched-noop) are asserted on real row state and on
which statements were executed. The live psycopg seam (``connect``) is
covered by the server tests via monkeypatch.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from kalshi_testing import persistence
from kalshi_testing.nba_scanner import EventRecord, MarketRecord
from kalshi_testing.persistence import (
    MissingSupabaseCredentialError,
    is_whale,
    load_db_config,
    plan_upsert,
    side_price_dollars,
)

DB_ENV = {"SUPABASE_DB_URI": "postgresql://db.example.test:5432/postgres", "SUPABASE_DB_PW": "pw"}


class FakeConn:
    """In-memory stand-in for the psycopg connection, covering exactly the
    SQL persistence.py issues. Records every statement so tests can assert
    that an unchanged re-ingest executes no UPDATE at all."""

    def __init__(self) -> None:
        self.events: dict[str, dict] = {}  # event_ticker -> row
        self.markets: dict[str, dict] = {}  # market_ticker -> row
        self.whale_trades: list[dict] = []
        self.statements: list[str] = []
        self.commits = 0
        self._next_id = 1
        self._result: tuple | None = None

    def execute(self, sql: str, params: tuple = ()) -> "FakeConn":
        self.statements.append(sql)
        self._result = None
        if sql.startswith("select id, event_ticker"):
            row = self.events.get(params[0])
            if row is not None:
                self._result = (
                    row["id"],
                    row["event_ticker"],
                    row["title"],
                    row["espn_start_ts"],
                    row["away_team"],
                    row["home_team"],
                )
        elif sql.startswith("insert into kalshi_events"):
            ticker, title, espn_start, away, home = params
            self.events[ticker] = {
                "id": self._next_id,
                "event_ticker": ticker,
                "title": title,
                "espn_start_ts": espn_start,
                "away_team": away,
                "home_team": home,
            }
            self._next_id += 1
            self._result = (self.events[ticker]["id"],)
        elif sql.startswith("update kalshi_events"):
            title, espn_start, away, home, ticker = params
            self.events[ticker].update(
                title=title, espn_start_ts=espn_start, away_team=away, home_team=home
            )
        elif sql.startswith("select id, event_id, market_ticker"):
            row = self.markets.get(params[0])
            if row is not None:
                self._result = (
                    row["id"],
                    row["event_id"],
                    row["market_ticker"],
                    row["title"],
                    row["close_time"],
                )
        elif sql.startswith("insert into kalshi_markets"):
            event_id, ticker, title, close_time = params
            self.markets[ticker] = {
                "id": self._next_id,
                "event_id": event_id,
                "market_ticker": ticker,
                "title": title,
                "close_time": close_time,
            }
            self._next_id += 1
        elif sql.startswith("update kalshi_markets"):
            event_id, title, close_time, ticker = params
            self.markets[ticker].update(event_id=event_id, title=title, close_time=close_time)
        elif sql.startswith("insert into whale_trades"):
            keys = (
                "market_ticker",
                "taker_outcome_side",
                "taker_book_side",
                "count_fp",
                "price_dollars",
                "is_block_trade",
                "ts_ms",
            )
            self.whale_trades.append(dict(zip(keys, params, strict=True)))
        else:  # an unexpected statement is a test bug, never silently ignored
            raise AssertionError(f"FakeConn received unexpected SQL: {sql}")
        return self

    def fetchone(self) -> tuple | None:
        return self._result

    def commit(self) -> None:
        self.commits += 1

    def close(self) -> None:
        pass

    def writes(self, table: str) -> list[str]:
        """INSERT/UPDATE statements issued against ``table``."""
        return [s for s in self.statements if s.startswith(("insert", "update")) and table in s]


def _event_record(**overrides) -> EventRecord:
    fields = {
        "event_ticker": "KXNBAGAME-26NOV24BOSMIA",
        "title": "Boston Celtics at Miami Heat",
        "espn_start_ts": "2026-11-25T02:00:00+00:00",
        "away_team": "BOS",
        "home_team": "MIA",
        "markets": (MarketRecord("M-ML", "Will the Celtics win", "2026-11-25T02:00:00Z"),),
    }
    fields.update(overrides)
    return EventRecord(**fields)


# --- load_db_config: missing credentials fail visibly at startup ---


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_db_uri_raises_naming_variable(value: str | None) -> None:
    env = dict(DB_ENV)
    if value is None:
        env.pop("SUPABASE_DB_URI")
    else:
        env["SUPABASE_DB_URI"] = value
    with pytest.raises(MissingSupabaseCredentialError, match="SUPABASE_DB_URI"):
        load_db_config(env)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_db_password_raises_naming_variable(value: str | None) -> None:
    env = dict(DB_ENV)
    if value is None:
        env.pop("SUPABASE_DB_PW")
    else:
        env["SUPABASE_DB_PW"] = value
    with pytest.raises(MissingSupabaseCredentialError, match="SUPABASE_DB_PW"):
        load_db_config(env)


def test_load_db_config_returns_the_configured_values() -> None:
    config = load_db_config(DB_ENV)
    assert config.uri == DB_ENV["SUPABASE_DB_URI"]
    assert config.password == DB_ENV["SUPABASE_DB_PW"]


def test_db_config_repr_never_leaks_the_password() -> None:
    config = load_db_config(DB_ENV)
    assert DB_ENV["SUPABASE_DB_PW"] not in repr(config)


# --- _uri_sets_sslmode: only a real sslmode parameter suppresses the forced require ---


@pytest.mark.parametrize(
    "uri",
    [
        "postgresql://db.example.test:5432/postgres?sslmode=verify-full",
        "postgresql://db.example.test/postgres?application_name=x&sslmode=disable",
        "host=db.example.test dbname=postgres sslmode=require",
    ],
)
def test_uri_with_explicit_sslmode_is_detected(uri: str) -> None:
    assert persistence._uri_sets_sslmode(uri) is True


@pytest.mark.parametrize(
    "uri",
    [
        "postgresql://db.example.test:5432/postgres",
        # 'sslmode=' inside an unrelated query VALUE must not count.
        "postgresql://db.example.test/postgres?application_name=sslmode%3Ddisable",
        "postgresql://db.example.test/postgres?options=-c%20sslmode%3Ddisable",
        # A keyword that merely contains the text must not count either.
        "host=db.example.test xsslmode=disable",
    ],
)
def test_uri_without_sslmode_still_forces_require(uri: str) -> None:
    assert persistence._uri_sets_sslmode(uri) is False


# --- side_price_dollars: the stored price is the taker-side price ---


def test_yes_taker_price_is_the_yes_price() -> None:
    trade = {
        "taker_outcome_side": "yes",
        "yes_price_dollars": "0.62",
        "no_price_dollars": "0.38",
    }
    assert side_price_dollars(trade) == Decimal("0.62")


def test_no_taker_price_is_the_no_price() -> None:
    trade = {
        "taker_outcome_side": "no",
        "yes_price_dollars": "0.62",
        "no_price_dollars": "0.38",
    }
    assert side_price_dollars(trade) == Decimal("0.38")


@pytest.mark.parametrize("side", [None, "", "maybe", "YES"])
def test_unknown_taker_side_raises_instead_of_guessing(side: str | None) -> None:
    trade = {"yes_price_dollars": "0.62", "no_price_dollars": "0.38"}
    if side is not None:
        trade["taker_outcome_side"] = side
    with pytest.raises(ValueError, match="taker_outcome_side"):
        side_price_dollars(trade)


def test_missing_side_price_raises() -> None:
    with pytest.raises(ValueError, match="yes_price_dollars"):
        side_price_dollars({"taker_outcome_side": "yes"})


def test_malformed_side_price_raises() -> None:
    trade = {"taker_outcome_side": "no", "no_price_dollars": "not-a-price"}
    with pytest.raises(ValueError, match="no_price_dollars"):
        side_price_dollars(trade)


# --- is_whale: strictly above $1000 ---


@pytest.mark.parametrize(
    ("count_fp", "price", "expected"),
    [
        ("500", Decimal("2.00"), False),  # exactly $1000 is NOT a whale
        ("500", Decimal("2.01"), True),  # $1005
        ("500", Decimal("1.99"), False),  # $995
        ("1000.01", Decimal("1.00"), True),  # $1000.01, one cent over
        ("1", Decimal("1000.00"), False),  # exactly $1000 via price
        ("0", Decimal("99999"), False),
    ],
)
def test_whale_boundary_is_strictly_above_1000(
    count_fp: str, price: Decimal, expected: bool
) -> None:
    assert is_whale(count_fp, price) is expected


def test_malformed_count_raises() -> None:
    with pytest.raises(ValueError, match="count_fp"):
        is_whale("lots", Decimal("2.00"))


# --- plan_upsert ---


def test_plan_upsert_inserts_when_no_row_exists() -> None:
    assert plan_upsert(None, {"title": "x"}) == "insert"


def test_plan_upsert_noops_when_all_managed_fields_match() -> None:
    existing = {"id": 7, "title": "x", "away_team": "BOS", "created_at": "ignored"}
    assert plan_upsert(existing, {"title": "x", "away_team": "BOS"}) == "noop"


def test_plan_upsert_updates_when_any_managed_field_differs() -> None:
    existing = {"id": 7, "title": "old", "away_team": "BOS"}
    assert plan_upsert(existing, {"title": "new", "away_team": "BOS"}) == "update"


# --- upsert_event / upsert_market: idempotent re-ingest ---


def test_first_ingest_inserts_event_and_markets() -> None:
    conn = FakeConn()
    event_id = persistence.upsert_event(conn, _event_record())
    persistence.upsert_market(conn, event_id, _event_record().markets[0])
    assert len(conn.events) == 1
    assert len(conn.markets) == 1
    row = conn.events["KXNBAGAME-26NOV24BOSMIA"]
    assert row["away_team"] == "BOS"
    assert row["home_team"] == "MIA"
    assert row["espn_start_ts"] == datetime(2026, 11, 25, 2, 0, tzinfo=UTC)
    market = conn.markets["M-ML"]
    assert market["event_id"] == event_id
    assert market["close_time"] == datetime(2026, 11, 25, 2, 0, tzinfo=UTC)


def test_reingesting_identical_event_and_market_leaves_rows_untouched() -> None:
    """AC: re-ingest with no changes -> no new row, no UPDATE issued (so the
    updated_at trigger never fires and the row is genuinely untouched)."""
    conn = FakeConn()
    event_id = persistence.upsert_event(conn, _event_record())
    persistence.upsert_market(conn, event_id, _event_record().markets[0])
    conn.statements.clear()
    again_id = persistence.upsert_event(conn, _event_record())
    persistence.upsert_market(conn, again_id, _event_record().markets[0])
    assert again_id == event_id  # same row, stable id
    assert len(conn.events) == 1
    assert len(conn.markets) == 1
    assert conn.writes("kalshi_events") == []
    assert conn.writes("kalshi_markets") == []


def test_naive_timestamp_is_treated_as_utc_so_reingest_is_a_noop() -> None:
    """A timestamp without an offset parses to the same aware value a
    timestamptz column returns — the re-ingest comparison must not see a
    naive-vs-aware mismatch and UPDATE the row on every scan."""
    conn = FakeConn()
    naive = _event_record(espn_start_ts="2026-11-25T02:00:00")
    persistence.upsert_event(conn, naive)
    assert conn.events["KXNBAGAME-26NOV24BOSMIA"]["espn_start_ts"] == datetime(
        2026, 11, 25, 2, 0, tzinfo=UTC
    )
    conn.statements.clear()
    persistence.upsert_event(conn, naive)
    assert conn.writes("kalshi_events") == []  # noop, not a spurious update


def test_changed_event_field_updates_the_existing_row_in_place() -> None:
    conn = FakeConn()
    event_id = persistence.upsert_event(conn, _event_record())
    changed = _event_record(espn_start_ts="2026-11-25T03:30:00+00:00")
    again_id = persistence.upsert_event(conn, changed)
    assert again_id == event_id  # updated in place, never a second row
    assert len(conn.events) == 1
    assert conn.events["KXNBAGAME-26NOV24BOSMIA"]["espn_start_ts"] == datetime(
        2026, 11, 25, 3, 30, tzinfo=UTC
    )
    assert len(conn.writes("kalshi_events")) == 2  # the insert + this update


def test_changed_market_field_updates_the_existing_row_in_place() -> None:
    conn = FakeConn()
    event_id = persistence.upsert_event(conn, _event_record())
    persistence.upsert_market(conn, event_id, MarketRecord("M-ML", "Old title", None))
    persistence.upsert_market(conn, event_id, MarketRecord("M-ML", "New title", None))
    assert len(conn.markets) == 1
    assert conn.markets["M-ML"]["title"] == "New title"


def test_nullable_timestamps_round_trip_as_none() -> None:
    conn = FakeConn()
    record = _event_record(espn_start_ts=None)
    event_id = persistence.upsert_event(conn, record)
    persistence.upsert_market(conn, event_id, MarketRecord("M-ML", "t", None))
    assert conn.events[record.event_ticker]["espn_start_ts"] is None
    assert conn.markets["M-ML"]["close_time"] is None


# --- insert_whale_trade ---


def test_insert_whale_trade_stores_all_columns_with_side_matched_price() -> None:
    conn = FakeConn()
    trade = {
        "market_ticker": "MKT-AAA",
        "taker_outcome_side": "no",
        "taker_book_side": "yes",
        "count_fp": "500",
        "no_price_dollars": "2.50",
        "is_block_trade": True,
        "ts_ms": 1732492800000,
    }
    persistence.insert_whale_trade(conn, trade, side_price_dollars(trade))
    assert conn.whale_trades == [
        {
            "market_ticker": "MKT-AAA",
            "taker_outcome_side": "no",
            "taker_book_side": "yes",
            "count_fp": Decimal("500"),
            "price_dollars": Decimal("2.50"),  # the NO price, not the yes price
            "is_block_trade": True,
            "ts_ms": 1732492800000,
        }
    ]


def test_insert_whale_trade_missing_field_raises_instead_of_partial_insert() -> None:
    conn = FakeConn()
    trade = {"market_ticker": "MKT-AAA", "taker_outcome_side": "yes"}
    with pytest.raises(KeyError):
        persistence.insert_whale_trade(conn, trade, Decimal("2.50"))
    assert conn.whale_trades == []
