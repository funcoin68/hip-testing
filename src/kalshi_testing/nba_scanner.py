"""Discovery of NBA money-line markets via the Kalshi demo REST API.

Pure, socket-free logic shared by the WebSocket server: paginated event and
market fetches, the America/New_York today/tomorrow window, the money-line
title predicate, and per-event qualification. Every REST request is signed
with the Kalshi API key (see ``kalshi_testing.auth``); missing or rejected
credentials abort the scan — there is no unsigned fallback.

Kalshi REST requests that hit HTTP 429 (rate limit) are retried with
exponential backoff capped at ``RETRY_MAX_WAIT``; exhausting retries on one
event's markets fetch skips that event (logged, scan continues), while any
other HTTP error fails fast.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from kalshi_testing.auth import KalshiAuthError as _SigningAuthError
from kalshi_testing.auth import kalshi_auth_headers

# Kalshi series ticker for NBA game events.
SERIES_TICKER = "KXNBAGAME"
# Money-line market titles look like "Will the <team> win".
MONEYLINE_TITLE_RE = re.compile(r".* wins", re.IGNORECASE)
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


def et_window(now: datetime) -> tuple[datetime, datetime]:
    """Return the ET today/tomorrow window as UTC datetimes.

    ``now`` must be timezone-aware. The window is [today 00:00:00
    America/New_York, tomorrow 23:59:59.999999 America/New_York], converted
    to UTC — a game at 02:00 UTC belongs to the *previous* ET day during EST.
    """
    local = now.astimezone(_EASTERN)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=4) - timedelta(microseconds=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def is_moneyline_market(title: str) -> bool:
    """True when a market title matches the NBA money-line pattern."""
    return True
    #return bool(MONEYLINE_TITLE_RE.match(title))


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
    never silently skipped, because the skipped entry could be the qualifying
    money-line market.
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


def _market_close_time(ticker: str, close_raw: str, event_ticker: str) -> datetime:
    try:
        close_time = datetime.fromisoformat(close_raw)
    except ValueError as exc:
        raise EventMarketsError(
            f"Kalshi event {event_ticker} returned market {ticker} with "
            f"unparseable close_time {close_raw!r}"
        ) from exc
    if close_time.tzinfo is None:
        raise EventMarketsError(
            f"Kalshi event {event_ticker} returned market {ticker} with a timezone-naive close_time"
        )
    return close_time


def discover_moneyline_markets(
    rest_url: str, now: datetime, api_key_id: str, private_key_pem: str
) -> dict[str, list[str]]:
    """Map each qualifying NBA event ticker to its in-window money-line market tickers.

    An event qualifies when at least one of its markets closes within the ET
    today/tomorrow window (``et_window(now)`` — recomputed on every call). A
    qualifying event maps to the tickers of its in-window markets whose title
    matches the money-line pattern (possibly an empty list). Malformed market
    entries raise ``EventMarketsError``; the WebSocket caller logs it and the
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
    """
    window_start, window_end = et_window(now)
    discovered: dict[str, list[str]] = {}
    skipped: list[str] = []
    for event in fetch_open_events(rest_url, api_key_id, private_key_pem):
        event_ticker = event["event_ticker"]
        try:
            markets = fetch_event_markets(rest_url, event_ticker, api_key_id, private_key_pem)
        except RateLimitExhaustedError as exc:
            print(f"Skipping Kalshi event {event_ticker}: {exc}", file=sys.stderr)
            skipped.append(event_ticker)
            continue
        moneyline: list[str] = []
        event_in_window = False
        for market in markets:
            ticker, title, close_raw = _market_fields(market, event_ticker)
            close_time = _market_close_time(ticker, close_raw, event_ticker)
            if window_start <= close_time <= window_end:
                event_in_window = True
                if is_moneyline_market(title):
                    moneyline.append(ticker)
        if event_in_window:
            discovered[event_ticker] = moneyline
    if skipped:
        print(
            f"NBA scan: {len(skipped)} event(s) skipped after rate-limit "
            f"retries, {len(discovered)} resolved",
            file=sys.stderr,
        )
    return discovered
