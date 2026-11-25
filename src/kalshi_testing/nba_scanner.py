"""Discovery of NBA money-line markets via the Kalshi demo REST API.

Pure, socket-free logic shared by the WebSocket server: paginated event and
market fetches, the America/New_York today/tomorrow window, the money-line
title predicate, and per-event qualification. Everything here is public
market data — no signing needed.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

# Kalshi series ticker for NBA game events.
SERIES_TICKER = "KXNBAGAME"
# Money-line market titles look like "Will the <team> win".
MONEYLINE_TITLE_RE = re.compile(r"^will the .+ win", re.IGNORECASE)
# Re-scan cadence: every 4 hours, measured from startup.
RESCAN_SECONDS = 4 * 3600

_EASTERN = ZoneInfo("America/New_York")


class EventMarketsError(RuntimeError):
    """Raised when event/market data cannot be fetched or is malformed."""


def et_window(now: datetime) -> tuple[datetime, datetime]:
    """Return the ET today/tomorrow window as UTC datetimes.

    ``now`` must be timezone-aware. The window is [today 00:00:00
    America/New_York, tomorrow 23:59:59.999999 America/New_York], converted
    to UTC — a game at 02:00 UTC belongs to the *previous* ET day during EST.
    """
    local = now.astimezone(_EASTERN)
    start_local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end_local = start_local + timedelta(days=2) - timedelta(microseconds=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def is_moneyline_market(title: str) -> bool:
    """True when a market title matches the NBA money-line pattern."""
    return bool(MONEYLINE_TITLE_RE.match(title))


def _get_json(url: str, context: str) -> dict:
    """GET one URL and parse the JSON body; errors name what was requested."""
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise EventMarketsError(
            f"Kalshi REST request for {context} failed: HTTP {exc.code}"
        ) from exc
    except urllib.error.URLError as exc:
        raise EventMarketsError(f"Kalshi REST request for {context} failed: {exc.reason}") from exc


def fetch_open_events(rest_url: str) -> list[dict]:
    """Return every open event in the NBA series, following the response
    cursor so events are never silently truncated."""
    events: list[dict] = []
    cursor = ""
    while True:
        query = {"series_ticker": SERIES_TICKER, "status": "open", "limit": "200"}
        if cursor:
            query["cursor"] = cursor
        url = f"{rest_url}/events?{urllib.parse.urlencode(query)}"
        payload = _get_json(url, f"series {SERIES_TICKER}")
        for event in payload.get("events", []):
            if not isinstance(event, dict) or not event.get("event_ticker"):
                raise EventMarketsError(
                    f"Kalshi series {SERIES_TICKER} returned a malformed event entry"
                )
            events.append(event)
        cursor = payload.get("cursor") or ""
        if not cursor:
            break
    return events


def fetch_event_markets(rest_url: str, event_ticker: str) -> list[dict]:
    """Return every market entry of ``event_ticker``, following the response
    cursor so markets are never silently truncated. An event with zero
    markets yields an empty list — qualification is decided by the caller.
    """
    markets: list[dict] = []
    cursor = ""
    while True:
        query = {"event_ticker": event_ticker, "limit": "1000"}
        if cursor:
            query["cursor"] = cursor
        url = f"{rest_url}/markets?{urllib.parse.urlencode(query)}"
        payload = _get_json(url, f"event {event_ticker}")
        markets.extend(payload.get("markets", []))
        cursor = payload.get("cursor") or ""
        if not cursor:
            break
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


def discover_moneyline_markets(rest_url: str, now: datetime) -> dict[str, list[str]]:
    """Map each qualifying NBA event ticker to its in-window money-line market tickers.

    An event qualifies when at least one of its markets closes within the ET
    today/tomorrow window (``et_window(now)`` — recomputed on every call). A
    qualifying event maps to the tickers of its in-window markets whose title
    matches the money-line pattern (possibly an empty list). Malformed market
    entries raise ``EventMarketsError``; the WebSocket caller logs it and the
    next scheduled scan retries. Returns ``{}`` when nothing qualifies.
    """
    window_start, window_end = et_window(now)
    discovered: dict[str, list[str]] = {}
    for event in fetch_open_events(rest_url):
        event_ticker = event["event_ticker"]
        moneyline: list[str] = []
        event_in_window = False
        for market in fetch_event_markets(rest_url, event_ticker):
            ticker, title, close_raw = _market_fields(market, event_ticker)
            close_time = _market_close_time(ticker, close_raw, event_ticker)
            if window_start <= close_time <= window_end:
                event_in_window = True
                if is_moneyline_market(title):
                    moneyline.append(ticker)
        if event_in_window:
            discovered[event_ticker] = moneyline
    return discovered
