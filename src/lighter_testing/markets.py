"""List all Lighter TESTNET markets (perp + spot) as JSON on stdout.

Usage:
    python -m lighter_testing.markets

Fetches every market from the public ``GET /api/v1/orderBooks`` endpoint on
the testnet API and prints a single JSON array; each market object carries
``market_id``, ``symbol``, ``status``, and a normalized ``market_type`` whose
value is exactly ``"perp"`` or ``"spot"``. The endpoint is unauthenticated —
the testnet wallet key is loaded only to satisfy the shared config contract
(``load_config``) and is never sent anywhere.

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
    print(json.dumps(markets, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
