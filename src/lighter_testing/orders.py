"""Submit, inspect, and cancel limit orders on the Lighter TESTNET.

Wraps the ``lighter-sdk`` signing client (``lighter.SignerClient``) and its
read API behind three sync methods — :meth:`LighterOrderClient.submit_limit_order`,
:meth:`LighterOrderClient.get_order_status`, and
:meth:`LighterOrderClient.cancel_order` — bridging to the SDK's async API with
``asyncio.run`` per call (same pattern as ``lighter_testing.api_keys``).

Requires ``LIGHTER_PRIVATE_KEY`` (the testnet API-key private key printed by
``python -m lighter_testing.api_keys``, fake money only) from the environment
or a gitignored local ``.env`` — never hardcoded, never included in error
messages. ``LIGHTER_API_URL`` overrides the API host and is validated to be a
testnet URL before any request is made; mainnet hosts are refused.
"""

from __future__ import annotations

import asyncio
import os
import random

import lighter

from lighter_testing.client import (
    DEFAULT_BASE_URL,
    LighterConfig,
    MissingPrivateKeyError,
    _validate_base_url,
)

_API_KEY_ENV_VAR = "LIGHTER_PRIVATE_KEY"
# Client order indexes are 48-bit on Lighter; index 0 is reserved.
_CLIENT_ORDER_INDEX_MAX = 2**48


class OrderError(RuntimeError):
    """Raised when an order submit/status/cancel operation fails.

    Carries the operation name and the SDK/API message — failures are never
    swallowed or reported as success. Never contains private key material.
    """


def _load_api_private_key() -> str:
    """Read LIGHTER_PRIVATE_KEY from the environment, failing visibly if absent."""
    key = os.environ.get(_API_KEY_ENV_VAR, "")
    if not key.strip():
        raise MissingPrivateKeyError(
            "LIGHTER_PRIVATE_KEY is not set (or is empty). "
            "Provide the Lighter testnet API key (printed by "
            "`python -m lighter_testing.api_keys`) via the environment; "
            "never hardcode one."
        )
    return key


def _parse_side(side: str) -> bool:
    """Map a side string to the SDK's is_ask flag, rejecting anything else."""
    normalized = side.strip().lower()
    if normalized == "buy":
        return False
    if normalized == "sell":
        return True
    raise ValueError(f"side must be 'buy' or 'sell' (got {side!r})")


def _match_order(order_list: list, order_id: int):
    """Find an order by client order index or exchange order index."""
    for order in order_list:
        if order.client_order_index == order_id or order.order_index == order_id:
            return order
    return None


class LighterOrderClient:
    """Limit-order client for the Lighter testnet (fake money only).

    ``account_index`` and ``api_key_index`` identify the account and the API
    key slot (SignerClient requires both). The signing key itself comes from
    ``LIGHTER_PRIVATE_KEY`` in the environment — checked at construction,
    before any network call. ``config`` optionally supplies the base URL;
    otherwise ``LIGHTER_API_URL`` or the testnet default is used, and either
    way the host is validated to be a testnet URL.
    """

    def __init__(
        self,
        account_index: int,
        api_key_index: int,
        config: LighterConfig | None = None,
    ) -> None:
        # Fail visibly on a missing key before any network/SDK interaction.
        self._private_key = _load_api_private_key()
        base_url = (
            config.base_url
            if config is not None
            else os.environ.get("LIGHTER_API_URL") or DEFAULT_BASE_URL
        )
        _validate_base_url(base_url)
        self._base_url = base_url
        self._account_index = account_index
        self._api_key_index = api_key_index
        self._rng = random.SystemRandom()

    def submit_limit_order(self, market_index: int, side: str, price: int, size: int) -> int:
        """Sign and submit a limit order; return its client order index.

        ``price``/``size`` are passed to the SDK unchanged (scaled integers,
        per the exchange's market decimals). The returned ID is accepted by
        :meth:`get_order_status` and :meth:`cancel_order`.
        """
        is_ask = _parse_side(side)
        client_order_index = self._rng.randrange(1, _CLIENT_ORDER_INDEX_MAX)

        async def _submit() -> None:
            signer = self._new_signer()
            try:
                _tx, response, err = await signer.create_order(
                    market_index=market_index,
                    client_order_index=client_order_index,
                    base_amount=size,
                    price=price,
                    is_ask=is_ask,
                    order_type=lighter.SignerClient.ORDER_TYPE_LIMIT,
                    time_in_force=lighter.SignerClient.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
                )
            except Exception as exc:  # the SDK raises its own request exceptions
                raise OrderError(f"submit limit order request failed: {exc}") from exc
            finally:
                await signer.api_client.close()
            if err is not None:
                raise OrderError(f"submit limit order failed: {err}")
            if response is None or response.code != 200:
                message = getattr(response, "message", "no response")
                raise OrderError(f"submit limit order refused: {message}")

        asyncio.run(_submit())
        return client_order_index

    def get_order_status(self, order_id: int) -> dict:
        """Return the exchange's record for an order as a dict.

        Looks the ID up (as client order index or exchange order index) among
        the account's active orders. An unknown ID — including an order that
        is no longer active — raises :class:`OrderError`; a status is never
        fabricated.
        """

        async def _fetch() -> dict:
            signer = self._new_signer()
            try:
                order = _match_order(await self._fetch_active_orders(signer), order_id)
                if order is None:
                    raise OrderError(f"get order status failed: no order with id {order_id}")
                return order.to_dict()
            except OrderError:
                raise
            except Exception as exc:  # the SDK raises its own request exceptions
                raise OrderError(f"get order status request failed: {exc}") from exc
            finally:
                await signer.api_client.close()

        return asyncio.run(_fetch())

    def cancel_order(self, order_id: int) -> None:
        """Sign and submit a cancel for an open limit order.

        The ID (client order index or exchange order index) is resolved to the
        order's market and exchange index among the account's active orders;
        already-filled, already-canceled, or unknown IDs raise
        :class:`OrderError` rather than reporting success.
        """

        async def _cancel() -> None:
            signer = self._new_signer()
            try:
                order = _match_order(await self._fetch_active_orders(signer), order_id)
                if order is None:
                    raise OrderError(
                        f"cancel order failed: no open order with id {order_id} "
                        "(unknown, already filled, or already canceled)"
                    )
                _tx, response, err = await signer.cancel_order(
                    market_index=order.market_index, order_index=order.order_index
                )
            except OrderError:
                raise
            except Exception as exc:  # the SDK raises its own request exceptions
                raise OrderError(f"cancel order request failed: {exc}") from exc
            finally:
                await signer.api_client.close()
            if err is not None:
                raise OrderError(f"cancel order failed: {err}")
            if response is None or response.code != 200:
                message = getattr(response, "message", "no response")
                raise OrderError(f"cancel order refused: {message}")

        asyncio.run(_cancel())

    def _new_signer(self) -> lighter.SignerClient:
        # SignerClient needs a running loop (aiohttp), so it is constructed
        # inside each coroutine — same pattern as api_keys.register_api_key.
        return lighter.SignerClient(
            url=self._base_url,
            account_index=self._account_index,
            api_private_keys={self._api_key_index: self._private_key},
        )

    async def _fetch_active_orders(self, signer: lighter.SignerClient) -> list:
        """Read the account's active orders via the signer's read API."""
        auth, auth_err = signer.create_auth_token_with_expiry()
        if auth_err is not None:
            raise OrderError(f"could not create auth token: {auth_err}")
        result = await signer.order_api.account_active_orders(
            authorization=auth, account_index=self._account_index
        )
        if result.code != 200:
            raise OrderError(f"account active orders refused: {result.message}")
        return result.orders

    def __repr__(self) -> str:
        # The private key is deliberately omitted — never leak key material.
        return (
            f"LighterOrderClient(account_index={self._account_index}, "
            f"api_key_index={self._api_key_index}, base_url={self._base_url!r})"
        )
