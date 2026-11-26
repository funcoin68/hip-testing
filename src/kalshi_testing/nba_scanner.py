"""Discovery of NBA markets via the Kalshi demo REST API.

Pure, socket-free logic shared by the WebSocket server: paginated event and
market fetches and per-event qualification. Every REST request is signed
with the Kalshi API key (see ``kalshi_testing.auth``); missing or rejected
credentials abort the scan — there is no unsigned fallback.

Kalshi REST requests that hit HTTP 429 (rate limit) are retried with
exponential backoff capped at ``RETRY_MAX_WAIT``; exhausting retries on one
event's markets fetch skips that event (logged, scan continues), while any
other HTTP error fails fast.

After discovery, a log-only enrichment pass fetches ESPN's public NBA
scoreboard and logs each qualifying Kalshi event's scheduled start time next
to its ticker (or a warning when no ESPN game matches, or when the ESPN fetch
fails). The enrichment never changes which markets are ingested.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from kalshi_testing.auth import KalshiAuthError as _SigningAuthError
from kalshi_testing.auth import kalshi_auth_headers

# Kalshi series ticker for NBA game events.
SERIES_TICKER = "KXNBAGAME"
# ESPN's public, unsigned NBA scoreboard endpoint (log-only start-time
# enrichment; never used to decide which markets are ingested).
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
# Re-scan cadence: every 4 hours, measured from the end of the previous scan.
RESCAN_SECONDS = 4 * 3600
# Defensive ceiling on paginated fetches: a perpetual non-empty cursor would
# otherwise loop until process kill and stall the 4-hour re-scan schedule.
MAX_PAGES = 100
# HTTP 429 (rate limit) retry policy: exponential backoff starting at the
# initial wait, doubling per attempt, capped at the max wait.
RETRY_INITIAL_WAIT = 1.0
RETRY_BACKOFF_FACTOR = 2.0
RETRY_MAX_WAIT = 30.0
MAX_RETRIES = 3

_EASTERN = ZoneInfo("America/New_York")


class EventMarketsError(RuntimeError):
    """Raised when event/market data cannot be fetched or is malformed."""


class RateLimitExhaustedError(EventMarketsError):
    """Raised when HTTP 429 retries are exhausted for one Kalshi REST request.

    A subclass of ``EventMarketsError`` so existing handlers that catch the
    parent still see it; discovery catches it specifically to skip one event
    instead of failing the whole scan.
    """


class KalshiAuthError(EventMarketsError):
    """Raised when a Kalshi REST request cannot be authenticated.

    Covers blank credentials (raised before any request leaves the process)
    and Kalshi rejecting the key (HTTP 401/403). Never retried and never
    converted into a per-event skip: the whole scan aborts rather than
    falling back to unsigned requests.
    """


class EspnFetchError(RuntimeError):
    """Raised when the ESPN scoreboard cannot be fetched or is malformed.

    Single seam for every ESPN failure — HTTP error, timeout, connection
    error, malformed JSON, or a malformed payload. Never raised past
    ``discover_markets``: the enrichment pass catches it and logs a warning.
    """


def _get_json(url: str, context: str, api_key_id: str, private_key_pem: str) -> dict:
    """GET one signed URL and parse the JSON body; every failure — auth
    failure, HTTP error, connection error, timeout, or malformed body —
    raises an EventMarketsError naming what was requested.

    Every request carries Kalshi API-key auth headers signing
    ``<timestamp>GET<path>`` (query string excluded). Blank credentials raise
    ``KalshiAuthError`` before any request is made; HTTP 401/403 raises
    ``KalshiAuthError`` immediately — never retried, never an unsigned retry.
    HTTP 429 (rate limit) is retried with exponential backoff (initial wait
    doubling per attempt, capped at ``RETRY_MAX_WAIT``) up to ``MAX_RETRIES``
    times; exhausting retries raises ``RateLimitExhaustedError``. Any other
    HTTP status raises ``EventMarketsError`` immediately, without a retry.
    """
    try:
        headers = kalshi_auth_headers(
            api_key_id, private_key_pem, "GET", urllib.parse.urlsplit(url).path
        )
    except _SigningAuthError as exc:
        raise KalshiAuthError(str(exc)) from exc
    request = urllib.request.Request(url, headers=headers)
    body = b""
    for attempt in range(MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise KalshiAuthError(
                    f"Kalshi rejected API credentials (HTTP {exc.code}) for {context} — "
                    "check KALSHI_API_KEY_ID/KALSHI_PRIVATE_KEY"
                ) from exc
            if exc.code != 429:
                raise EventMarketsError(
                    f"Kalshi REST request for {context} failed: HTTP {exc.code}"
                ) from exc
            if attempt == MAX_RETRIES:
                raise RateLimitExhaustedError(
                    f"Kalshi REST request for {context} still rate-limited "
                    f"after {MAX_RETRIES} retries: HTTP {exc.code}"
                ) from exc
            wait = min(RETRY_INITIAL_WAIT * RETRY_BACKOFF_FACTOR**attempt, RETRY_MAX_WAIT)
            print(
                f"Kalshi REST request for {context} rate-limited (HTTP 429); "
                f"retry {attempt + 1}/{MAX_RETRIES} in {wait:.1f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
            continue
        except urllib.error.URLError as exc:
            raise EventMarketsError(
                f"Kalshi REST request for {context} failed: {exc.reason}"
            ) from exc
        except TimeoutError as exc:  # includes socket.timeout (same class since 3.10)
            raise EventMarketsError(f"Kalshi REST request for {context} timed out") from exc
        break
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise EventMarketsError(
            f"Kalshi REST request for {context} returned malformed JSON"
        ) from exc


def fetch_open_events(rest_url: str, api_key_id: str, private_key_pem: str) -> list[dict]:
    """Return every open event in the NBA series, following the response
    cursor so events are never silently truncated."""
    events: list[dict] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        query = {"series_ticker": SERIES_TICKER, "status": "open", "limit": "200"}
        if cursor:
            query["cursor"] = cursor
        url = f"{rest_url}/events?{urllib.parse.urlencode(query)}"
        payload = _get_json(url, f"series {SERIES_TICKER}", api_key_id, private_key_pem)
        for event in payload.get("events", []):
            if not isinstance(event, dict) or not event.get("event_ticker"):
                raise EventMarketsError(
                    f"Kalshi series {SERIES_TICKER} returned a malformed event entry"
                )
            events.append(event)
        cursor = payload.get("cursor") or ""
        if not cursor:
            break
    else:
        raise EventMarketsError(
            f"Kalshi series {SERIES_TICKER} pagination exceeded {MAX_PAGES} pages"
        )
    return events


def fetch_event_markets(
    rest_url: str, event_ticker: str, api_key_id: str, private_key_pem: str
) -> list[dict]:
    """Return every market entry of ``event_ticker``, following the response
    cursor so markets are never silently truncated. An event with zero
    markets yields an empty list — qualification is decided by the caller.
    """
    markets: list[dict] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        query = {"event_ticker": event_ticker, "limit": "1000"}
        if cursor:
            query["cursor"] = cursor
        url = f"{rest_url}/markets?{urllib.parse.urlencode(query)}"
        payload = _get_json(url, f"event {event_ticker}", api_key_id, private_key_pem)
        markets.extend(payload.get("markets", []))
        cursor = payload.get("cursor") or ""
        if not cursor:
            break
    else:
        raise EventMarketsError(
            f"Kalshi event {event_ticker} pagination exceeded {MAX_PAGES} pages"
        )
    return markets


def _market_fields(market: dict, event_ticker: str) -> tuple[str, str, str]:
    """Validate one market entry, returning (ticker, title, close_time).

    Malformed entries raise ``EventMarketsError`` naming the event — they are
    never silently skipped, because the skipped entry could be the market
    that qualifies the event.
    """
    if not isinstance(market, dict):
        raise EventMarketsError(f"Kalshi event {event_ticker} returned a malformed market entry")
    try:
        ticker = market["ticker"]
        title = market["title"]
        close_raw = market["close_time"]
    except KeyError as exc:
        raise EventMarketsError(
            f"Kalshi event {event_ticker} returned a malformed market entry "
            f"(missing field {exc.args[0]!r})"
        ) from exc
    if not all(isinstance(value, str) for value in (ticker, title, close_raw)):
        raise EventMarketsError(
            f"Kalshi event {event_ticker} returned a malformed market entry "
            "(ticker, title, and close_time must all be strings)"
        )
    return ticker, title, close_raw


# Kalshi ticker suffix: <YYMMM><DD><AWAY><HOME>, e.g. "26NOV24BOSMIA".
_TICKER_SUFFIX = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})([A-Z]{3})([A-Z]{3})$")
_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
# Kalshi team code -> ESPN abbreviation, for the handful of teams whose
# public codes differ (identity for every other team).
_KALSHI_TO_ESPN = {
    "GSW": "GS",
    "NOP": "NO",
    "NYK": "NY",
    "SAS": "SA",
    "UTA": "UTAH",
    "WAS": "WSH",
}


def _kalshi_matchup(event_ticker) -> tuple[date, frozenset[str]] | None:
    """Parse a Kalshi NBA event ticker (``KXNBAGAME-<YYMMM><DD><AWAY><HOME>``,
    e.g. ``KXNBAGAME-26NOV24BOSMIA``) into the ET game date and the
    order-insensitive pair of ESPN team abbreviations used for matching.

    Returns ``None`` when the ticker does not follow the convention — the
    caller degrades to the "no matching ESPN game" warning, never a crash
    and never a silently dropped event.
    """
    if not isinstance(event_ticker, str):
        return None
    match = _TICKER_SUFFIX.match(event_ticker.rsplit("-", 1)[-1])
    if match is None:
        return None
    yy, month_raw, dd, away, home = match.groups()
    month = _MONTHS.get(month_raw)
    if month is None:
        return None
    try:
        game_date = date(2000 + int(yy), month, int(dd))
    except ValueError:
        return None
    pair = frozenset((_KALSHI_TO_ESPN.get(away, away), _KALSHI_TO_ESPN.get(home, home)))
    if len(pair) != 2:
        return None
    return game_date, pair


def _fetch_espn_scoreboard(date_str: str) -> dict:
    """GET the ESPN NBA scoreboard for one date (``YYYYMMDD`` — a single
    date, never comma-joined) and return the parsed JSON body. Every failure
    — HTTP error, timeout, connection error, malformed JSON — raises
    ``EspnFetchError``.
    """
    request = urllib.request.Request(f"{ESPN_SCOREBOARD_URL}?dates={date_str}")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise EspnFetchError(f"ESPN scoreboard request failed: HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise EspnFetchError(f"ESPN scoreboard request failed: {exc.reason}") from exc
    except TimeoutError as exc:  # includes socket.timeout (same class since 3.10)
        raise EspnFetchError("ESPN scoreboard request timed out") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise EspnFetchError("ESPN scoreboard returned malformed JSON") from exc


def _espn_game(entry) -> tuple[date, frozenset[str], str]:
    """Validate one ESPN scoreboard entry, returning its (ET date,
    team-abbreviation pair, ISO 8601 start time). Missing or wrong-typed
    fields raise ``EspnFetchError`` — the single error seam."""
    if not isinstance(entry, dict):
        raise EspnFetchError("ESPN scoreboard returned a malformed event entry")
    raw_start = entry.get("date")
    if not isinstance(raw_start, str):
        raise EspnFetchError("ESPN scoreboard returned a game without a start time")
    try:
        start = datetime.fromisoformat(raw_start)
    except ValueError as exc:
        raise EspnFetchError(
            f"ESPN scoreboard returned an unparseable start time {raw_start!r}"
        ) from exc
    if start.tzinfo is None:
        raise EspnFetchError(f"ESPN scoreboard returned a timezone-naive start time {raw_start!r}")
    try:
        competitors = entry["competitions"][0]["competitors"]
        pair = frozenset(competitor["team"]["abbreviation"] for competitor in competitors)
    except (KeyError, IndexError, TypeError) as exc:
        raise EspnFetchError("ESPN scoreboard returned a game with an unparseable matchup") from exc
    if len(pair) != 2 or not all(isinstance(abbr, str) and abbr for abbr in pair):
        raise EspnFetchError("ESPN scoreboard returned a game with an unparseable matchup")
    return start.astimezone(_EASTERN).date(), pair, start.isoformat()


def fetch_espn_tipoffs(now: datetime) -> dict[tuple[date, frozenset[str]], str]:
    """Map each ESPN NBA game's (ET date, team pair) to its scheduled start.

    One unsigned GET per ET day from today through today+2 inclusive
    (3 requests — a single ``dates=YYYYMMDD`` per request, never
    comma-joined), 10s timeout, no retries; the per-date results are merged.
    Keys are ``(et_date, frozenset({away_abbr, home_abbr}))`` —
    order-insensitive and date-constrained; values are ISO 8601 start times.
    Any one date failing — HTTP error, timeout, connection error, malformed
    JSON, or a missing/wrong-typed field — raises ``EspnFetchError``.
    """
    start_local = now.astimezone(_EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    tipoffs: dict[tuple[date, frozenset[str]], str] = {}
    for i in range(3):
        date_str = (start_local + timedelta(days=i)).strftime("%Y%m%d")
        payload = _fetch_espn_scoreboard(date_str)
        entries = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise EspnFetchError("ESPN scoreboard returned a malformed payload")
        for entry in entries:
            et_date, pair, start = _espn_game(entry)
            tipoffs[(et_date, pair)] = start
    return tipoffs


def _log_espn_start_times(
    events: list[dict], discovered: dict[str, list[str]], now: datetime
) -> None:
    """Log-only ESPN enrichment: for each qualifying Kalshi event (scan
    order), log the matched ESPN scheduled start next to its ticker, or a
    warning when no ESPN game matches. An ESPN fetch failure logs one warning
    naming every qualifying event and never raises; no outcome of this pass
    changes which markets were discovered.
    """
    qualifying = [event for event in events if event["event_ticker"] in discovered]
    if not qualifying:
        return
    try:
        tipoffs = fetch_espn_tipoffs(now)
    except EspnFetchError as exc:
        tickers = ", ".join(event["event_ticker"] for event in qualifying)
        print(
            f"warning: ESPN start-time fetch failed for Kalshi events {tickers}: {exc}",
            file=sys.stderr,
        )
        return
    for event in qualifying:
        ticker = event["event_ticker"]
        matchup = _kalshi_matchup(ticker)
        if matchup is None:
            print(
                f"warning: Kalshi event {ticker} has an unparseable ticker date; "
                "skipping ESPN matching",
                file=sys.stderr,
            )
            continue
        start = tipoffs.get(matchup)
        if start is None:
            print(f"warning: no matching ESPN game for Kalshi event {ticker}", file=sys.stderr)
        else:
            print(f"{ticker}: ESPN scheduled start {start}", file=sys.stderr)


def discover_markets(
    rest_url: str, now: datetime, api_key_id: str, private_key_pem: str
) -> dict[str, list[str]]:
    """Map each qualifying NBA event ticker to its market tickers.

    Every open event with at least one well-formed market qualifies, and all
    of its markets are ingested regardless of close time — no event is
    excluded based on its close timestamp. Malformed market entries raise
    ``EventMarketsError``; the WebSocket caller logs it and the
    next scheduled scan retries. Returns ``{}`` when nothing qualifies.

    Every request is authenticated with ``api_key_id``/``private_key_pem``.
    HTTP 401/403 (rejected credentials) aborts the whole scan via
    ``KalshiAuthError`` — it is never converted into a per-event skip.

    HTTP 429 responses on a single event's markets fetch are retried with
    exponential backoff by ``_get_json``; if the retries are exhausted, that
    event is skipped (its ticker and the final error are logged to stderr) and
    the scan continues with the remaining events, printing a skip/resolved
    summary at the end. A 429 exhausting retries on the series-level events
    fetch still fails the whole scan via ``EventMarketsError``.

    After the scan, a log-only pass enriches each qualifying event with its
    ESPN scheduled start time (see ``_log_espn_start_times``); it never
    changes the returned mapping.
    """
    discovered: dict[str, list[str]] = {}
    skipped: list[str] = []
    events = fetch_open_events(rest_url, api_key_id, private_key_pem)
    for event in events:
        event_ticker = event["event_ticker"]
        try:
            markets = fetch_event_markets(rest_url, event_ticker, api_key_id, private_key_pem)
        except RateLimitExhaustedError as exc:
            print(f"Skipping Kalshi event {event_ticker}: {exc}", file=sys.stderr)
            skipped.append(event_ticker)
            continue
        tickers: list[str] = []
        for market in markets:
            ticker, _title, _close_raw = _market_fields(market, event_ticker)
            tickers.append(ticker)
        if tickers:
            discovered[event_ticker] = tickers
    if skipped:
        print(
            f"NBA scan: {len(skipped)} event(s) skipped after rate-limit "
            f"retries, {len(discovered)} resolved",
            file=sys.stderr,
        )
    _log_espn_start_times(events, discovered, now)
    return discovered
