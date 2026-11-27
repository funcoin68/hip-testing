"""Discovery of Kalshi game markets (NBA and NFL) via the Kalshi demo REST API.

Pure, socket-free logic shared by the WebSocket server: paginated event and
market fetches and per-event qualification, run once per league (see
``LeagueConfig``). Every REST request is signed
with the Kalshi API key (see ``kalshi_testing.auth``); missing or rejected
credentials abort the scan — there is no unsigned fallback.

Kalshi REST requests that hit HTTP 429 (rate limit) are retried with
exponential backoff capped at ``RETRY_MAX_WAIT``; exhausting retries on one
event's markets fetch skips that event (logged, scan continues), while any
other HTTP error fails fast.

After discovery, a log-only enrichment pass fetches the league's public ESPN
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
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from kalshi_testing.auth import KalshiAuthError as _SigningAuthError
from kalshi_testing.auth import kalshi_auth_headers

# Kalshi series ticker for NBA game events.
SERIES_TICKER = "KXNBAGAME"
# Kalshi series ticker for NFL game events.
NFL_SERIES_TICKER = "KXNFLGAME"
# ESPN's public, unsigned NBA scoreboard endpoint (log-only start-time
# enrichment; never used to decide which markets are ingested).
ESPN_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
# ESPN's public, unsigned NFL scoreboard endpoint (same log-only role).
ESPN_NFL_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
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
# Politeness delay between consecutive ESPN scoreboard requests (one per ET
# date); it is pacing, not a retry backoff.
ESPN_REQUEST_DELAY = 0.4
# One ET scoreboard request per day, today through today+5 inclusive.
ESPN_LOOKAHEAD_DAYS = 6

_EASTERN = ZoneInfo("America/New_York")

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
# NFL override map: Kalshi -> ESPN code drift verified 2026-11-27 against the
# 26 open KXNFLGAME event tickers on the public Kalshi API — JAC (Jaguars)
# and WAS (Commanders) are the only drifts; every other code matches ESPN.
_KALSHI_TO_ESPN_NFL = {
    "JAC": "JAX",
    "WAS": "WSH",
}


@dataclass(frozen=True)
class LeagueConfig:
    """Everything a scan needs to run one league end to end: the Kalshi
    series ticker, the ESPN scoreboard endpoint, and the Kalshi -> ESPN
    team-code override map (identity for codes not listed)."""

    name: str  # short label for log lines ("NBA" / "NFL")
    series_ticker: str
    espn_scoreboard_url: str
    kalshi_to_espn: dict[str, str]


NBA = LeagueConfig(
    name="NBA",
    series_ticker=SERIES_TICKER,
    espn_scoreboard_url=ESPN_SCOREBOARD_URL,
    kalshi_to_espn=_KALSHI_TO_ESPN,
)
NFL = LeagueConfig(
    name="NFL",
    series_ticker=NFL_SERIES_TICKER,
    espn_scoreboard_url=ESPN_NFL_SCOREBOARD_URL,
    kalshi_to_espn=_KALSHI_TO_ESPN_NFL,
)

# Kalshi money-line game markets come in two observed title shapes:
# "Will the <team> win ..." and "<team> wins" — the latter verified
# 2026-11-27 against the live demo API (e.g. "Denver wins" in
# KXNFLGAME-26NOV27DENPIT, "Miami wins" in KXNBAGAME-26NOV27MIANYK; the
# events endpoint itself carries no markets whose titles match "Will the").
# Spread/total titles ("Bills -3.5", "Celtics total points over 220.5")
# match neither shape.
_MONEYLINE_TITLE = re.compile(r"^(?:will the .+ win\b|.+ wins$)", re.IGNORECASE)


def _is_moneyline(title: str) -> bool:
    """True when a market title is a money-line game-market title ("Will the
    <team> win ..." or "<team> wins"). The one predicate shared by every
    league scan: an event with no money-line market is dropped before
    persistence."""
    return _MONEYLINE_TITLE.match(title) is not None


@dataclass(frozen=True)
class MarketRecord:
    """One discovered Kalshi market, as persisted to ``kalshi_markets``."""

    ticker: str
    title: str
    close_time: str | None


@dataclass(frozen=True)
class EventRecord:
    """One discovered Kalshi event with its markets, as persisted to
    ``kalshi_events``/``kalshi_markets``.

    ``url`` is the event's web URL — the API-provided one when the Kalshi
    response carries it, else a constructed kalshi.com URL (see
    ``_event_url``) — so every persisted event has a non-null URL.

    ``away_team``/``home_team`` are the ordered ESPN abbreviations parsed
    from the ticker suffix; both are ``None`` when the ticker does not
    follow the ``<YYMMM><DD><AWAY><HOME>`` convention — such an event is
    still discovered (and subscribed) but skipped for persistence, which
    requires NOT NULL teams.
    """

    event_ticker: str
    title: str
    url: str
    espn_start_ts: str | None
    away_team: str | None
    home_team: str | None
    markets: tuple[MarketRecord, ...]


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


def fetch_open_events(
    rest_url: str, api_key_id: str, private_key_pem: str, league: LeagueConfig = NBA
) -> list[dict]:
    """Return every open event in the league's series, following the response
    cursor so events are never silently truncated."""
    events: list[dict] = []
    cursor = ""
    for _ in range(MAX_PAGES):
        query = {"series_ticker": league.series_ticker, "status": "open", "limit": "200"}
        if cursor:
            query["cursor"] = cursor
        url = f"{rest_url}/events?{urllib.parse.urlencode(query)}"
        payload = _get_json(url, f"series {league.series_ticker}", api_key_id, private_key_pem)
        for event in payload.get("events", []):
            if not isinstance(event, dict) or not event.get("event_ticker"):
                raise EventMarketsError(
                    f"Kalshi series {league.series_ticker} returned a malformed event entry"
                )
            events.append(event)
        cursor = payload.get("cursor") or ""
        if not cursor:
            break
    else:
        raise EventMarketsError(
            f"Kalshi series {league.series_ticker} pagination exceeded {MAX_PAGES} pages"
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


def _parse_ticker_suffix(event_ticker) -> tuple[date, str, str] | None:
    """Parse a Kalshi NBA event ticker (``KXNBAGAME-<YYMMM><DD><AWAY><HOME>``,
    e.g. ``KXNBAGAME-26NOV24BOSMIA``) into the ET game date and the ORDERED
    raw Kalshi away/home team codes. Returns ``None`` when the ticker does
    not follow the convention.
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
    return game_date, away, home


def _kalshi_matchup(event_ticker, league: LeagueConfig = NBA) -> tuple[date, frozenset[str]] | None:
    """The ET game date and order-insensitive pair of ESPN team
    abbreviations used for matching, or ``None`` when the ticker does not
    follow the convention — the caller degrades to the "no matching ESPN
    game" warning, never a crash and never a silently dropped event.
    """
    parsed = _parse_ticker_suffix(event_ticker)
    if parsed is None:
        return None
    game_date, away, home = parsed
    pair = frozenset((league.kalshi_to_espn.get(away, away), league.kalshi_to_espn.get(home, home)))
    if len(pair) != 2:
        return None
    return game_date, pair


def _fetch_espn_scoreboard(date_str: str, scoreboard_url: str) -> dict:
    """GET the league's ESPN scoreboard for one date (``YYYYMMDD`` — a single
    date, never comma-joined) and return the parsed JSON body. Every failure
    — HTTP error, timeout, connection error, malformed JSON — raises
    ``EspnFetchError``.
    """
    request = urllib.request.Request(f"{scoreboard_url}?dates={date_str}")
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


def fetch_espn_tipoffs(
    now: datetime, league: LeagueConfig = NBA
) -> dict[tuple[date, frozenset[str]], str]:
    """Map each of the league's ESPN games' (ET date, team pair) to its
    scheduled start.

    One unsigned GET per ET day from today through today+5 inclusive
    (6 requests — a single ``dates=YYYYMMDD`` per request, never
    comma-joined), 10s timeout, no retries, with ``ESPN_REQUEST_DELAY``
    seconds of pacing between requests; the per-date results are merged.
    Keys are ``(et_date, frozenset({away_abbr, home_abbr}))`` —
    order-insensitive and date-constrained; values are ISO 8601 start times.
    Any one date failing — HTTP error, timeout, connection error, malformed
    JSON, or a missing/wrong-typed field — raises ``EspnFetchError``.
    """
    start_local = now.astimezone(_EASTERN).replace(hour=0, minute=0, second=0, microsecond=0)
    tipoffs: dict[tuple[date, frozenset[str]], str] = {}
    for i in range(ESPN_LOOKAHEAD_DAYS):
        date_str = (start_local + timedelta(days=i)).strftime("%Y%m%d")
        payload = _fetch_espn_scoreboard(date_str, league.espn_scoreboard_url)
        entries = payload.get("events") if isinstance(payload, dict) else None
        if not isinstance(entries, list):
            raise EspnFetchError("ESPN scoreboard returned a malformed payload")
        for entry in entries:
            et_date, pair, start = _espn_game(entry)
            tipoffs[(et_date, pair)] = start
        time.sleep(ESPN_REQUEST_DELAY)
    return tipoffs


def _attach_espn_start_times(
    records: list[EventRecord], now: datetime, league: LeagueConfig = NBA
) -> list[EventRecord]:
    """Log-only ESPN enrichment: for each qualifying Kalshi event (scan
    order), log the matched ESPN scheduled start next to its ticker, or a
    warning when no ESPN game matches. An ESPN fetch failure logs one warning
    naming every qualifying event and never raises. Returns the records with
    ``espn_start_ts`` set where a game matched; no outcome of this pass
    changes which events or markets were discovered.
    """
    if not records:
        return records
    try:
        tipoffs = fetch_espn_tipoffs(now, league)
    except EspnFetchError as exc:
        tickers = ", ".join(record.event_ticker for record in records)
        print(
            f"warning: ESPN start-time fetch failed for Kalshi events {tickers}: {exc}",
            file=sys.stderr,
        )
        return records
    enriched: list[EventRecord] = []
    for record in records:
        matchup = _kalshi_matchup(record.event_ticker, league)
        if matchup is None:
            print(
                f"warning: Kalshi event {record.event_ticker} has an unparseable ticker date; "
                "skipping ESPN matching",
                file=sys.stderr,
            )
            enriched.append(record)
            continue
        start = tipoffs.get(matchup)
        if start is None:
            print(
                f"warning: no matching ESPN game for Kalshi event {record.event_ticker}",
                file=sys.stderr,
            )
            enriched.append(record)
        else:
            print(f"{record.event_ticker}: ESPN scheduled start {start}", file=sys.stderr)
            enriched.append(replace(record, espn_start_ts=start))
    return enriched


def _event_url(event: dict, league: LeagueConfig) -> str:
    """The event's web URL: the API-provided one when the Kalshi response
    carries a non-empty ``https://`` URL on a kalshi.com host, else the
    constructed kalshi.com market URL (series and event ticker lowercased) —
    so every persisted event has a non-null URL by construction. Anything
    else — non-https schemes (``http:``, ``javascript:``, ...) or https URLs
    on a foreign host — is rejected at this single writer so a future
    consumer rendering ``kalshi_events.url`` never sees an open-redirect or
    phishing value planted by a malformed upstream response."""
    url = event.get("url")
    if isinstance(url, str):
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        if parts.scheme == "https" and (host == "kalshi.com" or host.endswith(".kalshi.com")):
            return url
    return (
        f"https://kalshi.com/markets/{league.series_ticker.lower()}/{event['event_ticker'].lower()}"
    )


def discover_events(
    rest_url: str, now: datetime, api_key_id: str, private_key_pem: str, league: LeagueConfig = NBA
) -> list[EventRecord]:
    """Return one ``EventRecord`` per qualifying event of the league, markets
    attached.

    An open event qualifies when at least one of its markets has a money-line
    title (the shared ``_is_moneyline`` predicate); an event with no
    money-line market is dropped, logged, and never persisted. All markets of
    a qualifying event are ingested regardless of close time — no event is
    excluded based on its close timestamp. Malformed market entries raise
    ``EventMarketsError``; the WebSocket caller logs it and the
    next scheduled scan retries. Returns ``[]`` when nothing qualifies.

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
    ESPN scheduled start time (see ``_attach_espn_start_times``); it never
    changes which events were discovered.
    """
    records: list[EventRecord] = []
    skipped: list[str] = []
    events = fetch_open_events(rest_url, api_key_id, private_key_pem, league)
    for event in events:
        event_ticker = event["event_ticker"]
        try:
            markets = fetch_event_markets(rest_url, event_ticker, api_key_id, private_key_pem)
        except RateLimitExhaustedError as exc:
            print(f"Skipping Kalshi event {event_ticker}: {exc}", file=sys.stderr)
            skipped.append(event_ticker)
            continue
        market_records: list[MarketRecord] = []
        for market in markets:
            ticker, title, close_raw = _market_fields(market, event_ticker)
            market_records.append(MarketRecord(ticker=ticker, title=title, close_time=close_raw))
        if not market_records:
            continue
        if not any(_is_moneyline(record.title) for record in market_records):
            print(
                f"Skipping Kalshi event {event_ticker}: no money-line market "
                f"in series {league.series_ticker}",
                file=sys.stderr,
            )
            continue
        away_team = home_team = None
        parsed = _parse_ticker_suffix(event_ticker)
        if parsed is not None:
            _game_date, away_raw, home_raw = parsed
            away_team = league.kalshi_to_espn.get(away_raw, away_raw)
            home_team = league.kalshi_to_espn.get(home_raw, home_raw)
        title = event.get("title")
        records.append(
            EventRecord(
                event_ticker=event_ticker,
                title=title if isinstance(title, str) and title else event_ticker,
                url=_event_url(event, league),
                espn_start_ts=None,
                away_team=away_team,
                home_team=home_team,
                markets=tuple(market_records),
            )
        )
    if skipped:
        print(
            f"{league.name} scan: {len(skipped)} event(s) skipped after rate-limit "
            f"retries, {len(records)} resolved",
            file=sys.stderr,
        )
    return _attach_espn_start_times(records, now, league)


def markets_by_event(records: list[EventRecord]) -> dict[str, list[str]]:
    """Map each event ticker to its market tickers — the one place the
    subscription shape is derived, shared by ``discover_markets`` and the
    WebSocket server."""
    return {record.event_ticker: [market.ticker for market in record.markets] for record in records}


def discover_markets(
    rest_url: str, now: datetime, api_key_id: str, private_key_pem: str, league: LeagueConfig = NBA
) -> dict[str, list[str]]:
    """Map each qualifying event ticker of the league to its market tickers.

    Thin wrapper over ``discover_events`` (whose docstring carries the full
    scan contract) deriving the ``dict[str, list[str]]`` shape the
    subscription logic consumes.
    """
    records = discover_events(rest_url, now, api_key_id, private_key_pem, league)
    return markets_by_event(records)
