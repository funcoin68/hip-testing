"""List all Lighter TESTNET markets (perp + spot + binary options) as JSON.

Usage:
    python -m lighter_testing.markets

Fetches perp/spot markets from the public ``GET /api/v1/orderBooks`` endpoint
and binary options markets from the public ``GET /api/v1/events`` endpoint on
the testnet API, then prints a single JSON array merging both; each market
object carries ``market_id``, ``symbol``, ``status``, and a normalized
``market_type`` whose value is exactly ``"perp"``, ``"spot"``, or
``"binary_options"``. Both endpoints are unauthenticated — the testnet wallet
key is loaded only to satisfy the shared config contract (``load_config``)
and is never sent anywhere.

The binary options listing is filtered to markets whose status is
``active`` or ``waiting-for-settlement``; entries with any other status are
omitted, and entries with no status field are skipped with a warning on
stderr. A binary options fetch failure is fatal, like a perp/spot failure:
the script exits non-zero, prints the error on stderr, and emits no
partial perp/spot-only output.

Requires ``WALLET_PRIVATE_KEY`` (testnet wallet key, fake money only) from the
environment or a gitignored local ``.env`` — never hardcoded.
``LIGHTER_API_URL`` overrides the API host and is validated to be a
testnet URL before any request is made; mainnet hosts are refused.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from lighter_testing.client import (
    InvalidBaseUrlError,
    MissingPrivateKeyError,
    load_config,
)

_TIMEOUT_SECONDS = 30
_VALID_MARKET_TYPES = frozenset({"perp", "spot"})
_KEPT_BINARY_STATUSES = frozenset({"active", "waiting-for-settlement"})


class MarketListError(RuntimeError):
    """Raised when the market list cannot be fetched or mapped completely."""


def _fetch_order_books(base_url: str) -> list[dict]:
    """GET {base_url}/api/v1/orderBooks and return the order_books list.

    This is the module's only network seam — unit tests replace it wholesale.
    """
    url = f"{base_url}/api/v1/orderBooks"
    try:
        with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        raise MarketListError(f"GET {url} failed: HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise MarketListError(f"GET {url} failed: {exc}") from exc
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise MarketListError(f"GET {url} returned unparseable JSON: {exc}") from exc
    order_books = payload.get("order_books")
    if not isinstance(order_books, list):
        raise MarketListError(f"GET {url} response has no order_books list")
    return order_books


def _fetch_binary_options(base_url: str) -> list[dict]:
    """GET {base_url}/api/v1/events and return the flattened event order books.

    Binary options markets are the order books registered under events; the
    endpoint paginates with ``next_cursor`` (verified against
    https://api.testnet.lighter.xyz on 2026-11-25: pages of up to 100 events,
    an empty ``events`` page terminates). Every page is followed so the
    listing is complete rather than silently truncated. This is the module's
    second network seam alongside :func:`_fetch_order_books` — unit tests
    replace it wholesale.
    """
    books: list[dict] = []
    cursor: int | None = None
    while True:
        url = f"{base_url}/api/v1/events"
        if cursor is not None:
            url = f"{url}?cursor={cursor}"
        try:
            with urllib.request.urlopen(url, timeout=_TIMEOUT_SECONDS) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise MarketListError(f"GET {url} failed: HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise MarketListError(f"GET {url} failed: {exc}") from exc
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise MarketListError(f"GET {url} returned unparseable JSON: {exc}") from exc
        events = payload.get("events")
        if not isinstance(events, list):
            raise MarketListError(f"GET {url} response has no events list")
        if not events:
            return books
        for event in events:
            event_books = event.get("order_books")
            if not isinstance(event_books, list):
                raise MarketListError(f"GET {url} response has an event with no order_books list")
            books.extend(event_books)
        next_cursor = payload.get("next_cursor")
        if next_cursor is None or next_cursor == 0:
            return books
        if not isinstance(next_cursor, int) or next_cursor == cursor:
            raise MarketListError(f"GET {url} returned unusable next_cursor {next_cursor!r}")
        cursor = next_cursor


def list_markets(order_books: list[dict]) -> list[dict]:
    """Map raw order books to market objects, normalizing market_type.

    Every order book must map to a market — an entry whose type is not
    exactly ``perp`` or ``spot`` (after normalization) raises
    :class:`MarketListError` rather than being silently dropped.
    """
    markets = []
    for book in order_books:
        raw_type = str(book.get("market_type", "")).strip().lower()
        if raw_type not in _VALID_MARKET_TYPES:
            raise MarketListError(
                f"order book {book.get('symbol', book.get('market_id', '?'))!r} has "
                f"unmappable market_type {book.get('market_type')!r} "
                f"(expected one of {sorted(_VALID_MARKET_TYPES)})"
            )
        markets.append(
            {
                "market_id": book.get("market_id"),
                "symbol": book.get("symbol"),
                "status": book.get("status"),
                "market_type": raw_type,
            }
        )
    return markets


def _map_binary_options(order_books: list[dict]) -> list[dict]:
    """Map raw event order books to market objects tagged ``binary_options``.

    Only books whose status is ``active`` or ``waiting-for-settlement`` are
    kept; any other status is filtered out silently (expected output, not an
    anomaly). A book with no usable status field is skipped with a warning on
    stderr naming the market. Strict like :func:`list_markets`: an entry
    whose type is not exactly ``binary_options`` (after normalization)
    raises :class:`MarketListError` rather than being silently reclassified
    or dropped.
    """
    markets = []
    for book in order_books:
        raw_type = str(book.get("market_type", "")).strip().lower()
        if raw_type != "binary_options":
            raise MarketListError(
                f"event order book {book.get('symbol', book.get('market_id', '?'))!r} has "
                f"unexpected market_type {book.get('market_type')!r} "
                f"(expected 'binary_options')"
            )
        status = str(book.get("status") or "").strip().lower()
        if not status:
            print(
                f"warning: skipping binary options market "
                f"{book.get('symbol', book.get('market_id', '?'))!r}: no status field",
                file=sys.stderr,
            )
            continue
        if status not in _KEPT_BINARY_STATUSES:
            continue
        markets.append(
            {
                "market_id": book.get("market_id"),
                "symbol": book.get("symbol"),
                "status": book.get("status"),
                "market_type": "binary_options",
            }
        )
    return markets


def main(argv: list[str] | None = None) -> int:
    try:
        config = load_config()
    except MissingPrivateKeyError as exc:
        print(exc, file=sys.stderr)
        return 1
    except InvalidBaseUrlError as exc:
        print(exc, file=sys.stderr)
        return 2
    try:
        markets = list_markets(_fetch_order_books(config.base_url))
    except MarketListError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        binary_markets = _map_binary_options(_fetch_binary_options(config.base_url))
    except MarketListError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(json.dumps(markets + binary_markets, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
