"""Behavior tests for NBA market discovery (nba_scanner).

All REST interaction is mocked at the ``urllib.request.urlopen`` seam via
monkeypatch — these tests never open a socket. Time is fixed: NOW is
2026-11-24 23:00 UTC = 18:00 America/New_York (EST, UTC-5), which fixes the
ESPN fetch dates at 2026-11-24 through 2026-11-29 ET.

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
        if isinstance(self._payload, bytes):
            return self._payload
        return json.dumps(self._payload).encode()


def _stub_urlopen(
    monkeypatch: pytest.MonkeyPatch, pages: list, espn_page=None, espn_requests=None
) -> list:
    """Serve Kalshi `pages` (dicts or exceptions) in order; record every Kalshi
    request. Requests whose URL contains "espn" are routed to `espn_page` (a
    dict, raw bytes, an exception, or a list of per-date pages served in
    request order; defaults to an empty scoreboard) so the ESPN enrichment
    fetch never consumes a Kalshi page and is never recorded here. When
    `espn_requests` (a list) is passed, every ESPN request is appended to it
    so the request shape is assertable.
    """
    requests: list = []
    espn_served = 0

    def fake_urlopen(request, timeout=None):
        nonlocal espn_served
        if "espn" in request.full_url:
            if espn_requests is not None:
                espn_requests.append(request)
            page = {"events": []} if espn_page is None else espn_page
            if isinstance(page, list):
                page = page[espn_served]
            espn_served += 1
        else:
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


def test_discovers_all_markets_regardless_of_close_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every well-formed market is ingested regardless of close time: a
    market closing 2-3 days out (and beyond) is collected alongside the
    near-close markets, and no event is excluded for closing far out."""
    requests = _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1", "KXNBAGAME-EV2"]),
            {
                "markets": [
                    _market("M-ML", "Will the Boston Celtics win", "2026-11-25T02:00:00Z"),
                    _market("M-TOTAL", "Celtics total points over 220.5", "2026-11-27T02:00:00Z"),
                    _market("M-FAR", "Will the Miami Heat win", "2026-12-01T02:00:00Z"),
                ],
                "cursor": "",
            },
            {  # EV2: single market closing 3 days out — still ingested
                "markets": [_market("M-OTHER", "Will the Knicks win", "2026-11-27T02:00:00Z")],
                "cursor": "",
            },
        ],
    )
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {
        "KXNBAGAME-EV1": ["M-ML", "M-TOTAL", "M-FAR"],
        "KXNBAGAME-EV2": ["M-OTHER"],
    }
    assert len(requests) == 3  # one events fetch, then one markets fetch per event


def test_event_qualifies_on_any_market(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every well-formed market counts toward discovery: an event with any
    markets at all is included, however far out its markets close (HIP-36
    removed the HIP-34 money-line title predicate from qualification)."""
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
    assert discovered == {"KXNBAGAME-EV1": ["M-TOTAL", "M-ML"]}


def test_no_qualifying_events_returns_empty_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_urlopen(monkeypatch, [_events_page([])])
    assert nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM) == {}


def test_malformed_market_entry_raises_naming_event(monkeypatch: pytest.MonkeyPatch) -> None:
    """A market missing ticker/title/close_time must fail loudly, never be
    silently skipped (it could be the market that qualifies the event)."""
    _stub_urlopen(
        monkeypatch,
        [
            _events_page(["KXNBAGAME-EV1"]),
            {"markets": [{"ticker": "M-X", "close_time": "2026-11-25T02:00:00Z"}], "cursor": ""},
        ],
    )
    with pytest.raises(nba_scanner.EventMarketsError, match="KXNBAGAME-EV1"):
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
    assert sleeps[:3] == [1.0, 2.0, 4.0]  # the 429 backoff
    # Everything after the backoff is ESPN request pacing, never a retry.
    assert set(sleeps[3:]) == {nba_scanner.ESPN_REQUEST_DELAY}
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


def test_clean_scan_has_no_rate_limit_sleeps_and_logs_no_rate_limit_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """No 429 anywhere: no retry/backoff sleep (only ESPN request pacing) and
    no retry/skip stderr output. (The ESPN enrichment pass still logs its
    start-time lines — HIP-26.)"""
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
    assert [s for s in sleeps if s != nba_scanner.ESPN_REQUEST_DELAY] == []
    err = capsys.readouterr().err
    assert "429" not in err
    assert "skipped" not in err
    assert "retry" not in err


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


# --- ESPN start-time enrichment, log-only (HIP-26, per-date fetch + ticker matching in HIP-27) ---


def _event(ticker: str, title: str) -> dict:
    return {"event_ticker": ticker, "series_ticker": "KXNBAGAME", "title": title}


def _espn_page(games: list) -> dict:
    """One ESPN scoreboard entry per (away_abbr, home_abbr, iso_start) triple,
    in the real scoreboard shape (competitions[0].competitors[*].team)."""
    return {
        "events": [
            {
                "date": start,
                "competitions": [
                    {
                        "competitors": [
                            {"homeAway": "away", "team": {"abbreviation": away}},
                            {"homeAway": "home", "team": {"abbreviation": home}},
                        ]
                    }
                ],
            }
            for away, home, start in games
        ]
    }


def _kalshi_pages(tickers: list, market_tickers: list) -> list:
    """Events page + one single-market markets page per ticker."""
    return [
        {"events": [_event(t, "Boston Celtics at Miami Heat") for t in tickers], "cursor": ""},
        *[
            {"markets": [_market(m, "Will the X win", "2026-11-25T02:00:00Z")], "cursor": ""}
            for m in market_tickers
        ],
    ]


def test_espn_scoreboard_is_fetched_once_per_date_never_comma_joined(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC1/AC5: one scoreboard request per date, today through today+5
    ET (6 ET days), each ``dates=`` exactly one 8-digit date — never a
    comma-joined param, so no HTTP 400 for the normal span."""
    espn_requests: list = []
    _stub_urlopen(monkeypatch, [], espn_requests=espn_requests)
    assert nba_scanner.fetch_espn_tipoffs(NOW) == {}
    assert len(espn_requests) == 6
    dates = []
    for request in espn_requests:
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        (value,) = query["dates"]
        assert "," not in value
        assert len(value) == 8 and value.isdigit()
        dates.append(value)
    assert dates == ["20261124", "20261125", "20261126", "20261127", "20261128", "20261129"]


def test_one_failing_date_raises_espn_fetch_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: any single date's failure raises EspnFetchError immediately — no
    silent partial merge of the remaining dates."""
    pages = [
        {"events": []},
        urllib.error.HTTPError("https://x", 400, "Bad Request", None, None),
    ]
    espn_requests: list = []
    _stub_urlopen(monkeypatch, [], espn_page=pages, espn_requests=espn_requests)
    with pytest.raises(nba_scanner.EspnFetchError, match="HTTP 400"):
        nba_scanner.fetch_espn_tipoffs(NOW)
    assert len(espn_requests) == 2  # stopped at the failing date


def test_espn_entry_without_competitor_abbreviations_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The single-error-seam contract covers the new payload shape: a game
    without competitor team abbreviations is a malformed payload."""
    espn_page = {"events": [{"date": "2026-11-25T02:00:00Z", "competitions": []}]}
    _stub_urlopen(monkeypatch, [], espn_page=espn_page)
    with pytest.raises(nba_scanner.EspnFetchError, match="matchup"):
        nba_scanner.fetch_espn_tipoffs(NOW)


def test_espn_match_logs_scheduled_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC1/AC4/AC5: a Kalshi event whose ticker matchup matches an ESPN game
    logs the scheduled start next to the ticker; the discovered set is
    unchanged and the only sleeps are the per-request ESPN pacing delays."""
    requests = _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV24BOSMIA"], ["M-ML"]),
        espn_page=_espn_page([("BOS", "MIA", "2026-11-25T02:00:00Z")]),
    )
    sleeps = _capture_sleep(monkeypatch)
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-26NOV24BOSMIA": ["M-ML"]}
    err = capsys.readouterr().err
    assert "KXNBAGAME-26NOV24BOSMIA: ESPN scheduled start 2026-11-25T02:00:00" in err
    assert len(requests) == 2  # events fetch + markets fetch, unchanged
    assert sleeps == [nba_scanner.ESPN_REQUEST_DELAY] * 6  # pacing only, no retries


def test_espn_results_from_all_dates_are_merged_for_matching(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC2: games returned on different per-date responses are all merged —
    events matching games from different dates each still get their start
    time logged (none lost to the per-date split), including a game on the
    last window date (today+5)."""
    pages = [
        _espn_page([("BOS", "MIA", "2026-11-25T00:30:00Z")]),  # 19:30 ET Nov 24
        _espn_page([("NY", "CHI", "2026-11-26T00:30:00Z")]),  # 19:30 ET Nov 25
        {"events": []},
        {"events": []},
        {"events": []},
        _espn_page([("LAL", "PHX", "2026-11-30T00:30:00Z")]),  # 19:30 ET Nov 29
    ]
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(
            ["KXNBAGAME-26NOV24BOSMIA", "KXNBAGAME-26NOV25NYKCHI", "KXNBAGAME-26NOV29LALPHX"],
            ["M-1", "M-2", "M-3"],
        ),
        espn_page=pages,
    )
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {
        "KXNBAGAME-26NOV24BOSMIA": ["M-1"],
        "KXNBAGAME-26NOV25NYKCHI": ["M-2"],
        "KXNBAGAME-26NOV29LALPHX": ["M-3"],
    }
    err = capsys.readouterr().err
    assert "KXNBAGAME-26NOV24BOSMIA: ESPN scheduled start 2026-11-25T00:30:00" in err
    assert "KXNBAGAME-26NOV25NYKCHI: ESPN scheduled start 2026-11-26T00:30:00" in err
    assert "KXNBAGAME-26NOV29LALPHX: ESPN scheduled start 2026-11-30T00:30:00" in err


def test_espn_match_is_order_insensitive(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC3: the matchup key is a frozenset of the two abbreviations — ESPN
    listing the teams in the opposite order still matches the ticker."""
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV24BOSMIA"], ["M-ML"]),
        espn_page=_espn_page([("MIA", "BOS", "2026-11-25T02:00:00Z")]),
    )
    nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    err = capsys.readouterr().err
    assert "KXNBAGAME-26NOV24BOSMIA: ESPN scheduled start 2026-11-25T02:00:00" in err


def test_same_matchup_on_the_wrong_date_does_not_match(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC3: matching is date-constrained — a BOS/MIA game on Nov 25 ET does
    not match the Kalshi event for the Nov 24 BOS/MIA game."""
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV24BOSMIA"], ["M-ML"]),
        espn_page=_espn_page([("BOS", "MIA", "2026-11-26T00:30:00Z")]),  # Nov 25 ET
    )
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-26NOV24BOSMIA": ["M-ML"]}
    err = capsys.readouterr().err
    assert "no matching ESPN game" in err
    assert "ESPN scheduled start" not in err


def test_kalshi_gsw_matches_espn_gs(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC3: Kalshi/ESPN abbreviation drift is mapped — a ticker with GSW
    matches an ESPN game whose team abbreviation is GS."""
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV24GSWMIA"], ["M-ML"]),
        espn_page=_espn_page([("GS", "MIA", "2026-11-25T02:00:00Z")]),
    )
    nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    err = capsys.readouterr().err
    assert "KXNBAGAME-26NOV24GSWMIA: ESPN scheduled start 2026-11-25T02:00:00" in err


def test_back_to_back_same_matchup_matches_each_date_unambiguously(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC2 (HIP-29): the same two teams playing on consecutive days yield two
    Kalshi events (26NOV25 / 26NOV26) and two ESPN games; the
    (team-pair, ticker date) key attaches each event its own start time —
    the match is unambiguous."""
    pages = [
        {"events": []},  # Nov 24 ET: no games
        _espn_page([("BOS", "MIA", "2026-11-26T00:30:00Z")]),  # 19:30 ET Nov 25
        _espn_page([("BOS", "MIA", "2026-11-27T00:30:00Z")]),  # 19:30 ET Nov 26
        {"events": []},
        {"events": []},
        {"events": []},
    ]
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV25BOSMIA", "KXNBAGAME-26NOV26BOSMIA"], ["M-1", "M-2"]),
        espn_page=pages,
    )
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {
        "KXNBAGAME-26NOV25BOSMIA": ["M-1"],
        "KXNBAGAME-26NOV26BOSMIA": ["M-2"],
    }
    err = capsys.readouterr().err
    assert "KXNBAGAME-26NOV25BOSMIA: ESPN scheduled start 2026-11-26T00:30:00" in err
    assert "KXNBAGAME-26NOV26BOSMIA: ESPN scheduled start 2026-11-27T00:30:00" in err


def test_unparseable_event_ticker_is_logged_and_skipped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC3 (HIP-29): a ticker with a missing/unparseable date is logged and
    skipped from ESPN matching — a distinct warning from the no-match case,
    no exception, and the event's markets are still discovered."""
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-EV1"], ["M-ML"]),
        espn_page=_espn_page([("BOS", "MIA", "2026-11-25T02:00:00Z")]),
    )
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-EV1": ["M-ML"]}
    err = capsys.readouterr().err
    assert "KXNBAGAME-EV1" in err
    assert "skipping ESPN matching" in err
    assert "no matching ESPN game" not in err


def test_espn_no_match_logs_warning_and_ingest_still_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC2: no matching ESPN game -> a warning naming the Kalshi event, no
    exception, and the event's markets are still discovered."""
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV24BOSMIA"], ["M-ML"]),
        espn_page=_espn_page([("NY", "CHI", "2026-11-25T02:00:00Z")]),
    )
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-26NOV24BOSMIA": ["M-ML"]}
    err = capsys.readouterr().err
    assert "no matching ESPN game" in err
    assert "KXNBAGAME-26NOV24BOSMIA" in err


def test_event_five_days_out_without_espn_match_still_ingests(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC4 at the window edge: a Kalshi event 5 days out with no matching
    ESPN game in the 6-day window logs the no-match warning and still
    ingests normally — no crash, no dropped event."""
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV29BOSMIA"], ["M-ML"]),
        espn_page=_espn_page([("BOS", "MIA", "2026-11-25T02:00:00Z")]),  # Nov 24 ET only
    )
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-26NOV29BOSMIA": ["M-ML"]}
    err = capsys.readouterr().err
    assert "no matching ESPN game" in err
    assert "KXNBAGAME-26NOV29BOSMIA" in err


@pytest.mark.parametrize(
    "espn_failure",
    [
        urllib.error.HTTPError("https://x", 503, "Unavailable", None, None),
        urllib.error.URLError("connection refused"),
        TimeoutError("timed out"),
        b"<html>not json</html>",
    ],
    ids=["http-error", "url-error", "timeout", "malformed-json"],
)
def test_espn_fetch_failure_logs_warning_without_raising(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, espn_failure
) -> None:
    """AC3/AC4: an ESPN failure (HTTP error, connection error, timeout, or
    malformed body) logs a warning naming the affected Kalshi events, does not
    raise, and leaves the Kalshi request sequence byte-for-byte unchanged."""
    requests = _stub_urlopen(
        monkeypatch,
        [
            {
                "events": [_event("KXNBAGAME-EV1", "Boston Celtics at Miami Heat")],
                "cursor": "",
            },
            {
                "markets": [_market("M-ML", "Will the Boston Celtics win", "2026-11-25T02:00:00Z")],
                "cursor": "",
            },
        ],
        espn_page=espn_failure,
    )
    sleeps = _capture_sleep(monkeypatch)
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {"KXNBAGAME-EV1": ["M-ML"]}
    err = capsys.readouterr().err
    assert "warning" in err
    assert "KXNBAGAME-EV1" in err
    assert [r.full_url for r in requests] == [
        f"{REST}/events?series_ticker=KXNBAGAME&status=open&limit=200",
        f"{REST}/markets?event_ticker=KXNBAGAME-EV1&limit=1000",
    ]
    assert sleeps == []


# --- discover_events: EventRecord discovery for Supabase persistence (HIP-30) ---


def test_discover_events_returns_ordered_teams_and_market_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: records carry the ordered away/home ESPN abbreviations parsed from
    the ticker suffix, the event title, and each market's title/close_time."""
    _stub_urlopen(
        monkeypatch,
        [
            {
                "events": [_event("KXNBAGAME-26NOV24BOSMIA", "Boston Celtics at Miami Heat")],
                "cursor": "",
            },
            {
                "markets": [
                    _market("M-ML", "Will the Celtics win", "2026-11-25T02:00:00Z"),
                    _market("M-SPREAD", "Celtics -5.5", "2026-11-25T02:30:00Z"),
                ],
                "cursor": "",
            },
        ],
        espn_page=_espn_page([("BOS", "MIA", "2026-11-25T02:00:00Z")]),
    )
    records = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert len(records) == 1
    record = records[0]
    assert record.event_ticker == "KXNBAGAME-26NOV24BOSMIA"
    assert record.title == "Boston Celtics at Miami Heat"
    assert record.away_team == "BOS"  # ordered: away first, home second
    assert record.home_team == "MIA"
    assert record.espn_start_ts == "2026-11-25T02:00:00+00:00"
    assert [(m.ticker, m.title, m.close_time) for m in record.markets] == [
        ("M-ML", "Will the Celtics win", "2026-11-25T02:00:00Z"),
        ("M-SPREAD", "Celtics -5.5", "2026-11-25T02:30:00Z"),
    ]


def test_discover_events_maps_kalshi_team_codes_to_espn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored team identifiers are ESPN abbreviations (GSW -> GS)."""
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV24GSWMIA"], ["M-ML"]),
        espn_page=_espn_page([("GS", "MIA", "2026-11-25T02:00:00Z")]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert record.away_team == "GS"
    assert record.home_team == "MIA"


def test_discover_events_without_espn_match_leaves_start_ts_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV24BOSMIA"], ["M-ML"]),
        espn_page=_espn_page([("NY", "CHI", "2026-11-25T02:00:00Z")]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert record.espn_start_ts is None
    assert (record.away_team, record.home_team) == ("BOS", "MIA")


def test_discover_events_with_unparseable_ticker_has_no_teams_but_keeps_markets(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """A ticker that does not encode teams yields None teams (persistence is
    skipped downstream) but the event is still discovered and subscribed."""
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-EV1"], ["M-ML"]),
        espn_page=_espn_page([]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert record.away_team is None
    assert record.home_team is None
    assert [m.ticker for m in record.markets] == ["M-ML"]
    assert "skipping ESPN matching" in capsys.readouterr().err


def test_discover_events_espn_failure_leaves_start_ts_none_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stub_urlopen(
        monkeypatch,
        _kalshi_pages(["KXNBAGAME-26NOV24BOSMIA"], ["M-ML"]),
        espn_page=urllib.error.URLError("connection refused"),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert record.espn_start_ts is None


def test_discover_markets_matches_discover_events_exactly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dict wrapper derives from the same scan: same events, same markets."""
    pages = _kalshi_pages(["KXNBAGAME-26NOV24BOSMIA", "KXNBAGAME-26NOV25NYKCHI"], ["M-1", "M-2"])
    _stub_urlopen(monkeypatch, pages, espn_page=_espn_page([]))
    records = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    _stub_urlopen(monkeypatch, pages, espn_page=_espn_page([]))
    discovered = nba_scanner.discover_markets(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert discovered == {
        record.event_ticker: [market.ticker for market in record.markets] for record in records
    }


# --- HIP-34: KXNFLGAME parity, shared money-line filter, event web URLs ---


def _nfl_event(ticker: str, title: str, url: str | None = None) -> dict:
    event = {"event_ticker": ticker, "series_ticker": "KXNFLGAME", "title": title}
    if url is not None:
        event["url"] = url
    return event


def _nfl_pages(events: list, market_tickers: list) -> list:
    """Events page + one single-market money-line markets page per ticker."""
    return [
        {"events": events, "cursor": ""},
        *[
            {"markets": [_market(m, "Will the X win", "2026-11-25T02:00:00Z")], "cursor": ""}
            for m in market_tickers
        ],
    ]


def test_nfl_league_config_points_at_the_nfl_series_and_scoreboard() -> None:
    assert nba_scanner.NFL_SERIES_TICKER == "KXNFLGAME"
    assert nba_scanner.NFL.series_ticker == "KXNFLGAME"
    assert nba_scanner.NFL.espn_scoreboard_url == (
        "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
    )
    assert nba_scanner.NBA.series_ticker == nba_scanner.SERIES_TICKER
    assert nba_scanner.NBA.espn_scoreboard_url == nba_scanner.ESPN_SCOREBOARD_URL


def test_fetch_open_events_queries_the_nfl_series(monkeypatch: pytest.MonkeyPatch) -> None:
    requests = _stub_urlopen(
        monkeypatch,
        [
            {
                "events": [{"event_ticker": "KXNFLGAME-EV1", "series_ticker": "KXNFLGAME"}],
                "cursor": "",
            }
        ],
    )
    events = nba_scanner.fetch_open_events(REST, API_KEY_ID, PRIVATE_KEY_PEM, nba_scanner.NFL)
    assert [e["event_ticker"] for e in events] == ["KXNFLGAME-EV1"]
    assert "series_ticker=KXNFLGAME" in requests[0].full_url


def test_nfl_espn_scoreboard_is_fetched_once_per_date_from_the_football_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: each scan issues 6 ESPN NFL scoreboard requests — today ET through
    today+5 ET, one date per request — against the football/nfl endpoint."""
    espn_requests: list = []
    _stub_urlopen(monkeypatch, [], espn_requests=espn_requests)
    assert nba_scanner.fetch_espn_tipoffs(NOW, nba_scanner.NFL) == {}
    assert len(espn_requests) == 6
    dates = []
    for request in espn_requests:
        assert request.full_url.startswith(f"{nba_scanner.ESPN_NFL_SCOREBOARD_URL}?dates=")
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(request.full_url).query)
        (value,) = query["dates"]
        dates.append(value)
    assert dates == ["20261124", "20261125", "20261126", "20261127", "20261128", "20261129"]


def test_nfl_event_matches_espn_game_on_team_pair_and_ticker_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: an ESPN start time attaches to an NFL event when both the
    team-abbreviation pair and the ticker date in ET match the ESPN game."""
    _stub_urlopen(
        monkeypatch,
        _nfl_pages(
            [_nfl_event("KXNFLGAME-26NOV24BUFMIA", "Buffalo Bills at Miami Dolphins")],
            ["M-ML"],
        ),
        espn_page=_espn_page([("BUF", "MIA", "2026-11-25T02:00:00Z")]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM, nba_scanner.NFL)
    assert record.espn_start_ts == "2026-11-25T02:00:00+00:00"
    assert (record.away_team, record.home_team) == ("BUF", "MIA")


def test_nfl_kalshi_code_drift_maps_to_espn_abbreviations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verified Kalshi->ESPN NFL drifts (JAC->JAX, WAS->WSH — observed on
    the 26 open KXNFLGAME tickers, 2026-11-27) are mapped before matching, so
    a JAC/WAS ticker still attaches its ESPN start time."""
    _stub_urlopen(
        monkeypatch,
        _nfl_pages(
            [_nfl_event("KXNFLGAME-26NOV24JACWAS", "Jacksonville Jaguars at Washington")],
            ["M-ML"],
        ),
        espn_page=_espn_page([("JAX", "WSH", "2026-11-25T02:00:00Z")]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM, nba_scanner.NFL)
    assert record.espn_start_ts == "2026-11-25T02:00:00+00:00"
    assert (record.away_team, record.home_team) == ("JAX", "WSH")


def test_nfl_same_matchup_on_the_wrong_date_does_not_match(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """AC: NFL matching is date-constrained exactly like NBA — a BUF/MIA game
    on Nov 25 ET does not match the Nov 24 ticker."""
    _stub_urlopen(
        monkeypatch,
        _nfl_pages(
            [_nfl_event("KXNFLGAME-26NOV24BUFMIA", "Buffalo Bills at Miami Dolphins")],
            ["M-ML"],
        ),
        espn_page=_espn_page([("BUF", "MIA", "2026-11-26T00:30:00Z")]),  # Nov 25 ET
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM, nba_scanner.NFL)
    assert record.espn_start_ts is None
    err = capsys.readouterr().err
    assert "no matching ESPN game" in err
    assert "KXNFLGAME-26NOV24BUFMIA" in err


# --- HIP-34: event web URL (API-provided vs constructed) ---


def test_event_url_is_the_api_provided_url_when_the_response_has_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: the API-provided URL wins whenever the Kalshi response carries a
    non-empty URL string."""
    _stub_urlopen(
        monkeypatch,
        [
            {
                "events": [
                    _nfl_event(
                        "KXNFLGAME-26NOV24BUFMIA",
                        "Buffalo Bills at Miami Dolphins",
                        url="https://kalshi.com/markets/kxnflgame/api-provided",
                    )
                ],
                "cursor": "",
            },
            {
                "markets": [_market("M-ML", "Will the Buffalo Bills win", "2026-11-25T02:00:00Z")],
                "cursor": "",
            },
        ],
        espn_page=_espn_page([]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM, nba_scanner.NFL)
    assert record.url == "https://kalshi.com/markets/kxnflgame/api-provided"


@pytest.mark.parametrize(
    ("league", "event_ticker", "expected_url"),
    [
        (
            nba_scanner.NBA,
            "KXNBAGAME-26NOV24BOSMIA",
            "https://kalshi.com/markets/kxnbagame/kxnbagame-26nov24bosmia",
        ),
        (
            nba_scanner.NFL,
            "KXNFLGAME-26NOV24BUFMIA",
            "https://kalshi.com/markets/kxnflgame/kxnflgame-26nov24bufmia",
        ),
    ],
    ids=["nba", "nfl"],
)
def test_event_url_falls_back_to_the_constructed_kalshi_url(
    monkeypatch: pytest.MonkeyPatch, league, event_ticker: str, expected_url: str
) -> None:
    """AC: with no API-provided URL, every event still gets a non-null URL
    under https://kalshi.com/markets/<series lowercased>/<event ticker
    lowercased> — for both leagues."""
    _stub_urlopen(
        monkeypatch,
        [
            {
                "events": [{"event_ticker": event_ticker, "series_ticker": league.series_ticker}],
                "cursor": "",
            },
            {"markets": [_market("M-ML", "Will the X win", "2026-11-25T02:00:00Z")], "cursor": ""},
        ],
        espn_page=_espn_page([]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM, league)
    assert record.url == expected_url


def test_event_url_falls_back_when_the_api_url_is_an_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty-string URL is not "API-provided": the constructed URL is used
    so the persisted column is never an empty string."""
    _stub_urlopen(
        monkeypatch,
        [
            {
                "events": [_nfl_event("KXNFLGAME-26NOV24BUFMIA", "Bills at Dolphins", url="")],
                "cursor": "",
            },
            {
                "markets": [_market("M-ML", "Will the Buffalo Bills win", "2026-11-25T02:00:00Z")],
                "cursor": "",
            },
        ],
        espn_page=_espn_page([]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM, nba_scanner.NFL)
    assert record.url == "https://kalshi.com/markets/kxnflgame/kxnflgame-26nov24bufmia"


@pytest.mark.parametrize(
    "bad_url",
    [
        "http://kalshi.com/x",
        "javascript:alert(1)",
        "ftp://x",
        "https://evil.example.com/markets/kxnflgame",
        "https://kalshi.com.evil.example.com/x",
    ],
    ids=["http", "javascript", "ftp", "foreign-host", "lookalike-host"],
)
def test_event_url_rejects_non_https_or_foreign_host_api_urls(
    monkeypatch: pytest.MonkeyPatch, bad_url: str
) -> None:
    """An API-provided URL that is not https on a kalshi.com host is rejected
    at the writer: the constructed https kalshi.com URL is persisted instead,
    so no future consumer of ``kalshi_events.url`` can render a javascript:/
    http: value or an arbitrary phishing URL planted by a malformed upstream
    response."""
    _stub_urlopen(
        monkeypatch,
        [
            {
                "events": [_nfl_event("KXNFLGAME-26NOV24BUFMIA", "Bills at Dolphins", url=bad_url)],
                "cursor": "",
            },
            {
                "markets": [_market("M-ML", "Will the Buffalo Bills win", "2026-11-25T02:00:00Z")],
                "cursor": "",
            },
        ],
        espn_page=_espn_page([]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM, nba_scanner.NFL)
    assert record.url == "https://kalshi.com/markets/kxnflgame/kxnflgame-26nov24bufmia"


# --- HIP-36: market-type agnostic discovery ---


def test_event_with_only_spread_and_total_markets_is_discovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: an NBA event whose markets are all non-money-line (spread/total
    only) passes discovery — no market-type filter remains, so the event is
    returned with both markets attached."""
    _stub_urlopen(
        monkeypatch,
        [
            {
                "events": [_event("KXNBAGAME-26NOV24BOSMIA", "Boston Celtics at Miami Heat")],
                "cursor": "",
            },
            {
                "markets": [
                    _market("M-TOTAL", "Celtics total points over 220.5", "2026-11-25T02:00:00Z"),
                    _market("M-SPREAD", "Celtics -5.5", "2026-11-25T02:00:00Z"),
                ],
                "cursor": "",
            },
        ],
        espn_page=_espn_page([]),
    )
    records = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert len(records) == 1
    (record,) = records
    assert record.event_ticker == "KXNBAGAME-26NOV24BOSMIA"
    assert [market.ticker for market in record.markets] == ["M-TOTAL", "M-SPREAD"]


def test_nfl_event_with_only_a_spread_market_is_discovered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AC: KXNFLGAME events are discovered regardless of market type — an NFL
    event with a spread-only markets page yields an EventRecord."""
    _stub_urlopen(
        monkeypatch,
        [
            {"events": [_nfl_event("KXNFLGAME-26NOV24BUFMIA", "Bills at Dolphins")], "cursor": ""},
            {"markets": [_market("M-SPREAD", "Bills -3.5", "2026-11-25T02:00:00Z")], "cursor": ""},
        ],
        espn_page=_espn_page([]),
    )
    records = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM, nba_scanner.NFL)
    assert len(records) == 1
    (record,) = records
    assert record.event_ticker == "KXNFLGAME-26NOV24BUFMIA"
    assert [market.ticker for market in record.markets] == ["M-SPREAD"]


def test_mixed_market_event_is_discovered_with_all_markets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression: an event mixing a money-line market with spread/total
    markets is still ingested with every one of its markets attached."""
    _stub_urlopen(
        monkeypatch,
        [
            {"events": [_event("KXNBAGAME-26NOV24BOSMIA", "Celtics at Heat")], "cursor": ""},
            {
                "markets": [
                    _market("M-ML", "Will the Celtics win", "2026-11-25T02:00:00Z"),
                    _market("M-TOTAL", "Celtics total points over 220.5", "2026-11-25T02:00:00Z"),
                ],
                "cursor": "",
            },
        ],
        espn_page=_espn_page([]),
    )
    (record,) = nba_scanner.discover_events(REST, NOW, API_KEY_ID, PRIVATE_KEY_PEM)
    assert [m.ticker for m in record.markets] == ["M-ML", "M-TOTAL"]
