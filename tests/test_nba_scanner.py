"""Behavior tests for NBA market discovery (nba_scanner).

All REST interaction is mocked at the ``urllib.request.urlopen`` seam via
monkeypatch — these tests never open a socket. Time is fixed: NOW is
2026-11-24 23:00 UTC = 18:00 America/New_York (EST, UTC-5), so the ET
today/tomorrow window is [2026-11-24 05:00 UTC, 2026-11-26 04:59:59.999999 UTC].

Every request is signed with a real Ed25519 test key generated at import —
signatures are verified cryptographically, and no key material lives here.
"""

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from kalshi_testing import nba_scanner

REST = "https://demo-api.kalshi.co/trade-api/v2"
# 18:00 ET on 2026-11-24 (November -> EST, UTC-5).
NOW = datetime(2026, 11, 24, 23, 0, tzinfo=UTC)

# Generated at import: real signatures, zero key material in the repo.
_TEST_KEY = Ed25519PrivateKey.generate()
API_KEY_ID = "kalshi-test-key-id"
PRIVATE_KEY_PEM = _TEST_KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()


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
    assert end == datetime(2026, 11, 28, 4, 59, 59, 999999, tzinfo=UTC)


def test_late_utc_game_belongs_to_the_previous_eastern_day() -> None:
    """02:00 UTC on Nov 25 is 21:00 ET on Nov 24 — inside the window."""
    start, end = nba_scanner.et_window(NOW)
    game = datetime(2026, 11, 25, 2, 0, tzinfo=UTC)
    assert start <= game <= end


def test_et_window_excludes_the_day_after_tomorrow_in_eastern_time() -> None:
    """00:30 ET on Nov 26 is already outside the today/tomorrow window."""
    start, end = nba_scanner.et_window(NOW)
    too_late = datetime(2026, 11, 28, 5, 30, tzinfo=UTC)
    assert too_late > end


def test_et_window_excludes_yesterday_in_eastern_time() -> None:
    start, _ = nba_scanner.et_window(NOW)
    too_early = datetime(2026, 11, 24, 4, 0, tzinfo=UTC)  # 23:00 ET Nov 23
    assert too_early < start


# --- fetch_open_events ---


def test_fetch_open_events_queries_nba_series_with_open_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_urlopen(monkeypatch, [_events_page(["KXNBAGAME-EV1"])])
    events = nba_scanner.fetch_open_events(REST, API_KEY_ID, PRIVATE_KEY_PEM)
    assert [e["event_ticker"] for e in events] == ["KXNBAGAME-EV1"]
    url = requests[0].full_url
    assert "series_ticker=KXNBAGAME" in url
    assert "status=open" in url


def test_fetch_open_events_follows_cursor_pagination(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = _stub_urlopen(
        monkeypatch,
        [_events_page(["KXNBAGAME-EV1"], cursor="page-2"), _events_page(["KXNBAGAME-EV2"])],
    )
    events = nba_scanner.fetch_open_events(REST, API_KEY_ID, PRIVATE_KEY_PEM)
    assert [e["event_ticker"] for e in events] == ["KXNBAGAME-EV1", "KXNBAGAME-EV2"]
    assert len(requests) == 2
    assert "cursor=page-2" in requests[1].full_url


def test_fetch_open_events_http_error_names_series(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(
        monkeypatch,
        [urllib.error.HTTPError("https://x", 500, "Server Error", None, None)],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME"):
        nba_scanner.fetch_open_events(REST, API_KEY_ID, PRIVATE_KEY_PEM)


def test_fetch_open_events_malformed_entry_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(monkeypatch, [{"events": [{"no_ticker": True}], "cursor": ""}])
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME"):
        nba_scanner.fetch_open_events(REST, API_KEY_ID, PRIVATE_KEY_PEM)


# --- fetch_event_markets (moved from server.py; returns full market entries) ---


def test_fetch_event_markets_returns_market_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_urlopen(
        monkeypatch,
        [{"markets": [_market("M-1", "Will the X win", "2026-11-25T02:00:00Z")], "cursor": ""}],
    )
    markets = nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1", API_KEY_ID, PRIVATE_KEY_PEM)
    assert markets == [_market("M-1", "Will the X win", "2026-11-25T02:00:00Z")]
    assert "event_ticker=KXNBAGAME-EV1" in requests[0].full_url


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
    markets = nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1", API_KEY_ID, PRIVATE_KEY_PEM)
    assert [m["ticker"] for m in markets] == ["M-1", "M-2"]
    assert "cursor=page-2" in requests[1].full_url


def test_fetch_event_markets_zero_markets_returns_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No fail-fast: qualification is decided by discover_markets."""
    _stub_urlopen(monkeypatch, [{"markets": [], "cursor": ""}])
    assert nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1", API_KEY_ID, PRIVATE_KEY_PEM) == []


def test_fetch_open_events_fails_visibly_on_perpetual_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-empty cursor that never ends must not loop until process kill."""
    page = _events_page(["KXNBAGAME-EV1"], cursor="never-ends")
    requests = _stub_urlopen(monkeypatch, [page] * nba_scanner.MAX_PAGES)
    with pytest.raises(nba_scanner.EventMarketsError, match="pagination exceeded"):
        nba_scanner.fetch_open_events(REST, API_KEY_ID, PRIVATE_KEY_PEM)
    assert len(requests) == nba_scanner.MAX_PAGES


def test_fetch_event_markets_fails_visibly_on_perpetual_cursor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = {"markets": [_market("M-1", "t", "c")], "cursor": "never-ends"}
    requests = _stub_urlopen(monkeypatch, [page] * nba_scanner.MAX_PAGES)
    with pytest.raises(nba_scanner.EventMarketsError, match="pagination exceeded"):
        nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1", API_KEY_ID, PRIVATE_KEY_PEM)
    assert len(requests) == nba_scanner.MAX_PAGES


def test_fetch_event_markets_http_error_names_event_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(
        monkeypatch,
        [urllib.error.HTTPError("https://x", 404, "Not Found", None, None)],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME-EV1"):
        nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1", API_KEY_ID, PRIVATE_KEY_PEM)


# --- discover_markets ---


def test_discovers_all_in_window_markets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every in-window market is ingested regardless of type: the totals
    market is collected alongside the game market, and the out-of-window
    market is still excluded."""
    requests = _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1", "KXNBAGAME-EV2"]),
            {
                "markets": [
                    _market("M-ML", "Will the Boston Celtics win", "2026-11-25T02:00:00Z"),
                    _market("M-TOTAL", "Celtics total points over 220.5", "2026-11-25T02:00:00Z"),
                    _market("M-STALE", "Will the Miami Heat win", "2026-11-29T02:00:00Z"),
                ],
                "cursor": "",
            },
            {  # EV2: nothing closes inside the window -> dropped entirely
                "markets": [_market("M-OTHER", "Will the Knicks win", "2026-11-29T02:00:00Z")],
                "cursor": "",
            },
        ],
    )
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-EV1": ["M-ML", "M-TOTAL"]}
    assert len(requests) == 3  # one events fetch, then one markets fetch per event


def test_event_qualifies_on_any_in_window_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """A totals market closing in-window is collected (not merely a
    qualifier); the game market closing outside the window is excluded."""
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            {
                "markets": [
                    _market("M-TOTAL", "Celtics total points over 220.5", "2026-11-25T02:00:00Z"),
                    _market("M-ML", "Will the Boston Celtics win", "2026-11-29T02:00:00Z"),
                ],
                "cursor": "",
            },
        ],
    )
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-EV1": ["M-TOTAL"]}


def test_no_qualifying_events_returns_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(monkeypatch, [_events_page([])])
    assert nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM) == {}


def test_malformed_market_entry_raises_naming_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A market missing ticker/title/close_time must fail loudly, never be
    silently skipped (it could be an in-window market)."""
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            {"markets": [{"ticker": "M-X", "close_time": "2026-11-25T02:00:00Z"}], "cursor": ""},
        ],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME-EV1"):
        nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)


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
        nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)


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
        nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)


def test_discovery_failure_on_events_fetch_names_series(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(
        monkeypatch,
        [urllib.error.HTTPError("https://x", 503, "Unavailable", None, None)],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME"):
        nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)


def test_discovery_failure_on_markets_fetch_names_event(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            urllib.error.HTTPError("https://x", 500, "Server Error", None, None),
        ],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME-EV1"):
        nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)


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
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-EV1": ["M-ML"]}
    later = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)  # 07:00 ET Nov 26
    assert nba_scanner.discover_markets(REST, later, API_KEY_ID, PRIVATE_KEY_PEM) == {}
    assert len(requests) == 4


# --- HTTP 429 rate-limit retry (HIP-22) ---


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://x", code, "error", None, None)


def _capture_sleep(monkeypatch: pytest.MonkeyPatch) -> list:
    """Record every time.sleep duration the scanner asks for (never really sleep)."""
    sleeps: list = []
    monkeypatch.setattr(nba_scanner.time, "sleep", sleeps.append)
    return sleeps


def test_event_markets_request_retries_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One 429 on an event's markets fetch is retried after the initial wait."""
    requests = _stub_urlopen(
        monkeypatch,
        [
            urllib.error.HTTPError("https://x", 429, "Too Many Requests", None, None),
            {"markets": [_market("M-ML", "Will the X win", "2026-11-25T02:00:00Z")], "cursor": ""},
        ],
    )
    sleeps = _capture_sleep(monkeypatch)
    markets = nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1", API_KEY_ID, PRIVATE_KEY_PEM)
    assert [m["ticker"] for m in markets] == ["M-ML"]
    assert len(requests) == 2
    assert sleeps == [nba_scanner.RETRY_INITIAL_WAIT]


def test_fetch_open_events_retries_429_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The backoff also applies at series level (fetch_open_events)."""
    requests = _stub_urlopen(
        monkeypatch,
        [
            urllib.error.HTTPError("https://x", 429, "Too Many Requests", None, None),
            _events_page(["KXNBAGAME-EV1"]),
        ],
    )
    sleeps = _capture_sleep(monkeypatch)
    events = nba_scanner.fetch_open_events(REST, API_KEY_ID, PRIVATE_KEY_PEM)
    assert [e["event_ticker"] for e in events] == ["KXNBAGAME-EV1"]
    assert len(requests) == 2
    assert sleeps == [nba_scanner.RETRY_INITIAL_WAIT]


def test_scan_skips_event_after_rate_limit_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """429 on every request for EV1's markets: backoff 1s/2s/4s, then the event
    is skipped loudly, the scan continues with EV2, and a skip/resolved summary
    is printed at the end."""
    requests = _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1", "KXNBAGAME-EV2"]),
            *[_http_error(429)] * 4,  # EV1 markets: initial + 3 retries all 429
            {
                "markets": [_market("M-ML2", "Will the Knicks win", "2026-11-25T02:00:00Z")],
                "cursor": "",
            },
        ],
    )
    sleeps = _capture_sleep(monkeypatch)
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-EV2": ["M-ML2"]}
    assert len(requests) == 6
    assert sleeps == [1.0, 2.0, 4.0]
    err = capsys.readouterr().err
    assert "KXNBAGAME-EV1" in err
    assert "429" in err
    assert "1 event(s) skipped" in err
    assert "1 resolved" in err


def test_backoff_wait_is_capped_at_retry_max_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An initial wait above the cap is clamped to RETRY_MAX_WAIT, not slept."""
    monkeypatch.setattr(nba_scanner, "RETRY_INITIAL_WAIT", 100.0)
    _stub_urlopen(
        monkeypatch,
        [
            _http_error(429),
            {"markets": [], "cursor": ""},
        ],
    )
    sleeps = _capture_sleep(monkeypatch)
    nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1", API_KEY_ID, PRIVATE_KEY_PEM)
    assert sleeps == [nba_scanner.RETRY_MAX_WAIT]


@pytest.mark.parametrize("code", [401, 403, 404, 500])
def test_non_429_http_errors_fail_fast_without_sleep(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """401/403/404/5xx are never retried: one request, no sleep, EventMarketsError."""
    requests = _stub_urlopen(monkeypatch, [_http_error(code)])
    sleeps = _capture_sleep(monkeypatch)
    with pytest.raises(nba_scanner.EventMarketsError, match=f"HTTP {code}"):
        nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1", API_KEY_ID, PRIVATE_KEY_PEM)
    assert len(requests) == 1
    assert sleeps == []


def test_clean_scan_never_sleeps_and_logs_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """No 429 anywhere: zero sleep calls, no retry/skip stderr output."""
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            {
                "markets": [_market("M-ML", "Will the X win", "2026-11-25T02:00:00Z")],
                "cursor": "",
            },
        ],
    )
    sleeps = _capture_sleep(monkeypatch)
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-EV1": ["M-ML"]}
    assert sleeps == []
    assert capsys.readouterr().err == ""


def test_series_level_rate_limit_exhaustion_fails_scan_visibly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429 exhausting retries on fetch_open_events has no event to skip: the
    whole scan fails (subclass keeps it catchable as EventMarketsError)."""
    _stub_urlopen(monkeypatch, [_http_error(429)] * 4)
    sleeps = _capture_sleep(monkeypatch)
    with pytest.raises(nba_scanner.RateLimitExhaustedError, match="429"):
        nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert sleeps == [1.0, 2.0, 4.0]


# --- API-key authentication on every REST request (HIP-23) ---


def _request_headers(request: urllib.request.Request) -> dict:
    """Request headers keyed lowercase (urllib re-cases header names)."""
    return {key.lower(): value for key, value in request.headers.items()}


def _verify_request_signature(request: urllib.request.Request) -> None:
    """The request's KALSHI-ACCESS-SIGNATURE must verify against the test
    public key over '<timestamp>GET<path-without-query>' — Kalshi's scheme."""
    headers = _request_headers(request)
    timestamp = headers["kalshi-access-timestamp"]
    path = urllib.parse.urlsplit(request.full_url).path
    message = f"{timestamp}GET{path}".encode()
    signature = base64.b64decode(headers["kalshi-access-signature"])
    _TEST_KEY.public_key().verify(signature, message)  # raises InvalidSignature on mismatch


def test_every_scan_request_carries_verifiable_kalshi_auth_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1: every outgoing REST request — events fetch and markets fetch —
    carries all three KALSHI-ACCESS-* headers with a valid signature."""
    requests = _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            {
                "markets": [_market("M-ML", "Will the X win", "2026-11-25T02:00:00Z")],
                "cursor": "",
            },
        ],
    )
    nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert len(requests) == 2
    for request in requests:
        headers = _request_headers(request)
        assert headers["kalshi-access-key"] == API_KEY_ID
        assert headers["kalshi-access-timestamp"].isdigit()
        _verify_request_signature(request)


def test_query_string_is_excluded_from_the_signed_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The events URL carries a query string; the signature verifies over the
    bare path and NOT over path+query (Kalshi rejects the latter)."""
    requests = _stub_urlopen(monkeypatch, [_events_page([])])
    nba_scanner.fetch_open_events(REST, API_KEY_ID, PRIVATE_KEY_PEM)
    (request,) = requests
    assert "?" in request.full_url
    headers = _request_headers(request)
    timestamp = headers["kalshi-access-timestamp"]
    signature = base64.b64decode(headers["kalshi-access-signature"])
    path = urllib.parse.urlsplit(request.full_url).path
    _TEST_KEY.public_key().verify(signature, f"{timestamp}GET{path}".encode())
    with pytest.raises(InvalidSignature):
        _TEST_KEY.public_key().verify(
            signature, f"{timestamp}GET{request.full_url[len(REST) :]}".encode()
        )


@pytest.mark.parametrize("code", [401, 403])
def test_rejected_credentials_raise_auth_error_without_retry(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """AC3: 401/403 name the auth failure and are never retried or slept on."""
    requests = _stub_urlopen(monkeypatch, [_http_error(code)])
    sleeps = _capture_sleep(monkeypatch)
    with pytest.raises(nba_scanner.KalshiAuthError, match=f"HTTP {code}") as excinfo:
        nba_scanner.fetch_event_markets(REST, "KXNBAGAME-EV1", API_KEY_ID, PRIVATE_KEY_PEM)
    assert "credentials" in str(excinfo.value)
    assert len(requests) == 1
    assert sleeps == []


def test_auth_failure_on_one_event_aborts_the_whole_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC3: a 401 on one event's markets fetch must NOT become a per-event
    skip — the scan aborts before any further event is fetched, and never
    falls back to unsigned requests."""
    requests = _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1", "KXNBAGAME-EV2"]),
            _http_error(401),
            _events_page(["KXNBAGAME-EV2"]),  # served only if the scan continued
        ],
    )
    sleeps = _capture_sleep(monkeypatch)
    with pytest.raises(nba_scanner.KalshiAuthError, match="KXNBAGAME-EV1"):
        nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert len(requests) == 2  # EV2's markets fetch never happened
    assert sleeps == []


@pytest.mark.parametrize(
    ("api_key_id", "private_key_pem"),
    [("", PRIVATE_KEY_PEM), ("   ", PRIVATE_KEY_PEM), (API_KEY_ID, ""), (API_KEY_ID, "  ")],
)
def test_blank_credentials_raise_before_any_request(
    monkeypatch: pytest.MonkeyPatch, api_key_id: str, private_key_pem: str
) -> None:
    """AC3: missing credentials fail before any request leaves the process —
    there is no unsigned fallback."""
    requests = _stub_urlopen(monkeypatch, [_events_page([])])
    with pytest.raises(nba_scanner.KalshiAuthError):
        nba_scanner.fetch_open_events(REST, api_key_id, private_key_pem)
    assert requests == []
