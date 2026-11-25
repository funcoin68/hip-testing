"""Behavior tests for NBA money-line market discovery (nba_scanner).

All REST interaction is mocked at the ``urllib.request.urlopen`` seam via
monkeypatch — these tests never open a socket. Time is fixed: NOW is
2026-11-24 23:00 UTC = 18:00 America/New_York (EST, UTC-5), so the ET
today/tomorrow window is [2026-11-24 05:00 UTC, 2026-11-26 04:59:59.999999 UTC].
"""

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime

import pytest

from kalshi_testing import nba_scanner

REST = "https://demo-api.kalshi.co/trade-api/v2"
# 18:00 ET on 2026-11-24 (November -> EST, UTC-5).
NOW = datetime(2026, 11, 24, 23, 0, tzinfo=UTC)


class _FakeResponse:
    """Minimal stand-in for an urllib response: context manager + read()."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def _stub_urlopen(monkeypatch: pytest.MonkeyPatch, pages: list) -> list:
    """Serve `pages` (dicts or exceptions) in order; record every request."""
    requests: list = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        page = pages[len(requests) - 1]
        if isinstance(page, Exception):
            raise page
        return _FakeResponse(page)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return requests


def _events_page(tickers: list, cursor: str = "") -> dict:
    return {
        "events": [{"event_ticker": t, "series_ticker": "KXNBAGAME"} for t in tickers],
        "cursor": cursor,
    }


def _market(ticker: str, title: str, close_time: str) -> dict:
    return {"ticker": ticker, "title": title, "close_time": close_time}


# --- constants (the AC's 4-hour re-scan cadence and NBA series) ---


def test_rescan_interval_is_four_hours() -> None:
    assert nba_scanner.RESCAN_SECONDS == 4 * 3600


def test_series_ticker_is_nba_games() -> None:
    assert nba_scanner.SERIES_TICKER == "KXNBAGAME"


# --- et_window: today/tomorrow in America/New_York, compared in UTC ---


def test_et_window_spans_today_and_tomorrow_in_eastern_time() -> None:
    start, end = nba_scanner.et_window(NOW)
    assert start == datetime(2026, 11, 24, 5, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 26, 4, 59, 59, 999999, tzinfo=UTC)


def test_late_utc_game_belongs_to_the_previous_eastern_day() -> None:
    """02:00 UTC on Nov 25 is 21:00 ET on Nov 24 — inside the window."""
    start, end = nba_scanner.et_window(NOW)
    game = datetime(2026, 11, 25, 2, 0, tzinfo=UTC)
    assert start <= game <= end


def test_et_window_excludes_the_day_after_tomorrow_in_eastern_time() -> None:
    """00:30 ET on Nov 26 is already outside the today/tomorrow window."""
    start, end = nba_scanner.et_window(NOW)
    too_late = datetime(2026, 11, 26, 5, 30, tzinfo=UTC)
    assert too_late > end


def test_et_window_excludes_yesterday_in_eastern_time() -> None:
    start, _ = nba_scanner.et_window(NOW)
    too_early = datetime(2026, 11, 24, 4, 0, tzinfo=UTC)  # 23:00 ET Nov 23
    assert too_early < start


# --- is_moneyline_market ---


@pytest.mark.parametrize(
    "title",
    ["Will the Boston Celtics win", "will the denver nuggets win", "Will the LA Lakers win"],
)
def test_moneyline_titles_match(title: str) -> None:
    assert nba_scanner.is_moneyline_market(title)


@pytest.mark.parametrize(
    "title",
    [
        "Celtics v Nuggets spread",
        "Celtics total points over 220.5",
        "How many points will be scored",
    ],
)
def test_non_moneyline_titles_do_not_match(title: str) -> None:
    assert not nba_scanner.is_moneyline_market(title)


# --- fetch_open_events ---


def test_fetch_open_events_queries_nba_series_with_open_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_urlopen(monkeypatch, [_events_page(["KXNBAGAME-EV1"])])
    events = nba_scanner.fetch_open_events(REST)
    assert [e["event_ticker"] for e in events] == ["KXNBAGAME-EV1"]
    url = str(requests[0])
    assert "series_ticker=KXNBAGAME" in url
    assert "status=open" in url


def test_fetch_open_events_follows_cursor_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_urlopen(
        monkeypatch,
        [_events_page(["KXNBAGAME-EV1"], cursor="page-2"), _events_page(["KXNBAGAME-EV2"])],
    )
    events = nba_scanner.fetch_open_events(REST)
    assert [e["event_ticker"] for e in events] == ["KXNBAGAME-EV1", "KXNBAGAME-EV2"]
    assert len(requests) == 2
    assert "cursor=page-2" in str(requests[1])


def test_fetch_open_events_http_error_names_series(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(
        monkeypatch,
        [urllib.error.HTTPError("https://x", 500, "Server Error", None, None)],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME"):
        nba_scanner.fetch_open_events(REST)


def test_fetch_open_events_malformed_entry_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(monkeypatch, [{"events": [{"no_ticker": True}], "cursor": ""}])
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME"):
        nba_scanner.fetch_open_events(REST)


# --- fetch_event_markets (moved from server.py; returns full market entries) ---


def test_fetch_event_markets_returns_market_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_urlopen(
        monkeypatch,
        [{"markets": [_market("M-1", "Will the X win", "2026-11-25T02:00:00Z")], "cursor": ""}],
    )
    markets = nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1")
    assert markets == [_market("M-1", "Will the X win", "2026-11-25T02:00:00Z")]
    assert "event_ticker=KXNBAGAME-EV1" in str(requests[0])


def test_fetch_event_markets_follows_cursor_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_urlopen(
        monkeypatch,
        [
            {"markets": [_market("M-1", "t", "c")], "cursor": "page-2"},
            {"markets": [_market("M-2", "t", "c")], "cursor": ""},
        ],
    )
    markets = nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1")
    assert [m["ticker"] for m in markets] == ["M-1", "M-2"]
    assert "cursor=page-2" in str(requests[1])


def test_fetch_event_markets_zero_markets_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No fail-fast: qualification is decided by discover_moneyline_markets."""
    _stub_urlopen(monkeypatch, [{"markets": [], "cursor": ""}])
    assert nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1") == []


def test_fetch_open_events_fails_visibly_on_perpetual_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty cursor that never ends must not loop until process kill."""
    page = _events_page(["KXNBAGAME-EV1"], cursor="never-ends")
    requests = _stub_urlopen(monkeypatch, [page] * nba_scanner.MAX_PAGES)
    with pytest.raises(nba_scanner.EventMarketsError, match="pagination exceeded"):
        nba_scanner.fetch_open_events(REST)
    assert len(requests) == nba_scanner.MAX_PAGES


def test_fetch_event_markets_fails_visibly_on_perpetual_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = {"markets": [_market("M-1", "t", "c")], "cursor": "never-ends"}
    requests = _stub_urlopen(monkeypatch, [page] * nba_scanner.MAX_PAGES)
    with pytest.raises(nba_scanner.EventMarketsError, match="pagination exceeded"):
        nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1")
    assert len(requests) == nba_scanner.MAX_PAGES


def test_fetch_event_markets_http_error_names_event_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(
        monkeypatch,
        [urllib.error.HTTPError("https://x", 404, "Not Found", None, None)],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME-EV1"):
        nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1")


# --- discover_moneyline_markets ---


def test_discovers_in_window_moneyline_markets(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1", "KXNBAGAME-EV2"]),
            {
                "markets": [
                    _market("M-ML", "Will the Boston Celtics win", "2026-11-25T02:00:00Z"),
                    _market("M-TOTAL", "Celtics total points over 220.5", "2026-11-25T02:00:00Z"),
                    _market("M-STALE", "Will the Miami Heat win", "2026-11-27T02:00:00Z"),
                ],
                "cursor": "",
            },
            {  # EV2: nothing closes inside the window -> dropped entirely
                "markets": [_market("M-OTHER", "Will the Knicks win", "2026-11-27T02:00:00Z")],
                "cursor": "",
            },
        ],
    )
    discovered = nba_scanner.discover_moneyline_markets(REST, NOW)
    assert discovered == {"KXNBAGAME-EV1": ["M-ML"]}
    assert len(requests) == 3  # one events fetch, then one markets fetch per event


def test_event_qualifies_on_any_in_window_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """A totals market closing in-window qualifies the event even when its
    money-line market closes outside the window (mapped list may be empty)."""
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            {
                "markets": [
                    _market("M-TOTAL", "Celtics total points over 220.5", "2026-11-25T02:00:00Z"),
                    _market("M-ML", "Will the Boston Celtics win", "2026-11-27T02:00:00Z"),
                ],
                "cursor": "",
            },
        ],
    )
    discovered = nba_scanner.discover_moneyline_markets(REST, NOW)
    assert discovered == {"KXNBAGAME-EV1": []}


def test_no_qualifying_events_returns_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(monkeypatch, [_events_page([])])
    assert nba_scanner.discover_moneyline_markets(REST, NOW) == {}


def test_malformed_market_entry_raises_naming_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A market missing ticker/title/close_time must fail loudly, never be
    silently skipped (it could be the qualifying money line market)."""
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            {"markets": [{"ticker": "M-X", "close_time": "2026-11-25T02:00:00Z"}], "cursor": ""},
        ],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME-EV1"):
        nba_scanner.discover_moneyline_markets(REST, NOW)


def test_unparseable_close_time_raises_naming_event_and_market(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            {
                "markets": [_market("M-BAD", "Will the X win", "not-a-timestamp")],
                "cursor": "",
            },
        ],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="M-BAD"):
        nba_scanner.discover_moneyline_markets(REST, NOW)


def test_timezone_naive_close_time_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            {
                "markets": [_market("M-NAIVE", "Will the X win", "2026-11-25T02:00:00")],
                "cursor": "",
            },
        ],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="M-NAIVE"):
        nba_scanner.discover_moneyline_markets(REST, NOW)


def test_discovery_failure_on_events_fetch_names_series(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(
        monkeypatch,
        [urllib.error.HTTPError("https://x", 503, "Unavailable", None, None)],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME"):
        nba_scanner.discover_moneyline_markets(REST, NOW)


def test_discovery_failure_on_markets_fetch_names_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            urllib.error.HTTPError("https://x", 500, "Server Error", None, None),
        ],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME-EV1"):
        nba_scanner.discover_moneyline_markets(REST, NOW)


def test_window_is_recomputed_per_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same fixed response qualifies on Nov 24 but not two days later."""
    pages = [
        _events_page(["KXNBAGAME-EV1"]),
        {
            "markets": [_market("M-ML", "Will the X win", "2026-11-25T02:00:00Z")],
            "cursor": "",
        },
    ]
    requests = _stub_urlopen(monkeypatch, pages * 2)
    assert nba_scanner.discover_moneyline_markets(REST, NOW) == {"KXNBAGAME-EV1": ["M-ML"]}
    later = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)  # 07:00 ET Nov 26
    assert nba_scanner.discover_moneyline_markets(REST, later) == {}
    assert len(requests) == 4
