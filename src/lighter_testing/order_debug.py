"""Submit → status → cancel a Lighter TESTNET limit order, step by step.

Debuggable lifecycle script for the HIP-13 order client: it constructs
:class:`lighter_testing.orders.LighterOrderClient`, prints the configured
order details, asks for interactive confirmation, then submits the limit
order, prints the returned order ID, fetches and prints the order's status,
and cancels it — each step visible in the console or a debugger.

Usage (testnet only, fake money):
    python -m lighter_testing.order_debug
or point PyCharm's debugger at this file and edit the constants below.

Requires ``LIGHTER_PRIVATE_KEY`` (the testnet API-key private key printed by
``python -m lighter_testing.api_keys``, fake money only) from the environment
or a gitignored local ``.env`` — never hardcoded. ``LIGHTER_API_URL``
overrides the API host and is validated to be a testnet URL, and the key must
be present, both checked by the client constructor before any network call;
mainnet hosts and missing keys are refused with a clear error and a non-zero
exit code.
"""

from __future__ import annotations

import sys

from lighter_testing.client import InvalidBaseUrlError, MissingPrivateKeyError
from lighter_testing.orders import LighterOrderClient, OrderError

# --- Editable configuration (tweak these, then run or debug the file) --------
# ACCOUNT_INDEX / API_KEY_INDEX identify the account and API key slot for the
# signing client; MARKET_INDEX is a numeric testnet market id (list them with
# `python -m lighter_testing.markets`). PRICE / SIZE are scaled integers per
# the market's decimals. ORDER_ID_OVERRIDE skips the submit step and instead
# status/cancels an existing order — handy when re-running a debugger session
# against an order that is already on the book. None = the normal submit flow.
ACCOUNT_INDEX = 2081
API_KEY_INDEX = 6
MARKET_INDEX = 4095
SIDE = "buy"
PRICE = 100000  #ETH:$1000
SIZE = 1000    # amount: 0.1
ORDER_ID_OVERRIDE: int | None = None


def main() -> int:
    try:
        client = LighterOrderClient(ACCOUNT_INDEX, API_KEY_INDEX)
    except MissingPrivateKeyError as exc:
        print(exc, file=sys.stderr)
        return 1
    except InvalidBaseUrlError as exc:
        print(exc, file=sys.stderr)
        return 2

    print(f"Order details: market={MARKET_INDEX} side={SIDE!r} price={PRICE} size={SIZE}")
    if input("Submit this order? [y/N] ").strip().lower() not in {"y", "yes"}:
        print("Aborted — no order submitted.")
        return 0

    try:
        if ORDER_ID_OVERRIDE is not None:
            order_id = ORDER_ID_OVERRIDE
            print(f"Skipping submit; using ORDER_ID_OVERRIDE={order_id}")
        else:
            order_id = client.submit_limit_order(MARKET_INDEX, SIDE, PRICE, SIZE)
            print(f"Submitted — order ID: {order_id}")
        print(f"Order status: {client.get_order_status(order_id)}")
        client.cancel_order(order_id)
        print(f"Canceled order {order_id}")
    except OrderError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
