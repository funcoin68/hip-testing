"""Behavior tests for the limit-order client.

The lighter-sdk layer is mocked wholesale — ``lighter.SignerClient`` (order
signing/submission and its attached read API) is replaced with a fake, so
these tests never open a socket or touch key material beyond dummy values.
Assertions target observable behavior: returned order IDs, values passed
through to the SDK, and raised error conditions.
"""

from __future__ import annotations

import pytest

from lighter_testing import orders
from lighter_testing.client import DEFAULT_BASE_URL, InvalidBaseUrlError, MissingPrivateKeyError
from lighter_testing.orders import LighterOrderClient, OrderError

API_KEY = "0xdummytestnetapikey"  # noqa: S105 — dummy, not a real key
ACCOUNT_INDEX = 2081
API_KEY_INDEX = 6


class FakeResponse:
    """Stands in for the SDK's RespSendTx."""

    def __init__(self, code: int = 200, message: str = "") -> None:
        self.code = code
        self.message = message


class FakeOrder:
    """Stands in for the SDK's Order model (attribute access + to_dict)."""

    def __init__(self, **fields) -> None:
        self._fields = fields
        for name, value in fields.items():
            setattr(self, name, value)

    def to_dict(self) -> dict:
        return dict(self._fields)


class FakeOrders:
    """Stands in for the SDK's Orders response (account_active_orders)."""

    def __init__(self, order_list: list, code: int = 200, message: str = "") -> None:
        self.code = code
        self.message = message
        self.orders = order_list


class FakeApiClient:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeOrderApi:
    """Stands in for SignerClient.order_api (the SDK read seam)."""

    def __init__(self, signer: FakeSignerClient) -> None:
        self._signer = signer
        self.calls: list[dict] = []

    async def account_active_orders(self, authorization: str, account_index: int) -> FakeOrders:
        self.calls.append({"authorization": authorization, "account_index": account_index})
        result = type(self._signer).active_orders_result
        if isinstance(result, Exception):
            raise result
        return result


class FakeSignerClient:
    """Stands in for lighter.SignerClient.

    Per-test behavior is driven by class attributes the ``fake_sdk`` fixture
    resets to success defaults; tests override them to inject failures.
    """

    ORDER_TYPE_LIMIT = 0
    ORDER_TIME_IN_FORCE_GOOD_TILL_TIME = 1

    instances: list[FakeSignerClient] = []
    create_order_result: tuple = (object(), FakeResponse(), None)
    cancel_order_result: tuple = (object(), FakeResponse(), None)
    active_orders_result: FakeOrders = FakeOrders([])
    auth_token: tuple = ("fake-auth-token", None)

    def __init__(self, url: str, account_index: int, api_private_keys: dict) -> None:
        self.url = url
        self.account_index = account_index
        self.api_private_keys = api_private_keys
        self.api_client = FakeApiClient()
        self.order_api = FakeOrderApi(self)
        self.create_order_calls: list[dict] = []
        self.cancel_order_calls: list[dict] = []
        type(self).instances.append(self)

    def create_auth_token_with_expiry(self) -> tuple:
        return type(self).auth_token

    async def create_order(self, **kwargs) -> tuple:
        self.create_order_calls.append(kwargs)
        result = type(self).create_order_result
        if isinstance(result, Exception):
            raise result
        return result

    async def cancel_order(self, **kwargs) -> tuple:
        self.cancel_order_calls.append(kwargs)
        result = type(self).cancel_order_result
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def fake_sdk(monkeypatch: pytest.MonkeyPatch) -> type[FakeSignerClient]:
    """Install the fake SDK and a valid dummy environment."""
    FakeSignerClient.instances = []
    FakeSignerClient.create_order_result = (object(), FakeResponse(), None)
    FakeSignerClient.cancel_order_result = (object(), FakeResponse(), None)
    FakeSignerClient.active_orders_result = FakeOrders([])
    FakeSignerClient.auth_token = ("fake-auth-token", None)
    monkeypatch.setattr(orders.lighter, "SignerClient", FakeSignerClient)
    monkeypatch.setenv("LIGHTER_PRIVATE_KEY", API_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    return FakeSignerClient


def make_client() -> LighterOrderClient:
    return LighterOrderClient(account_index=ACCOUNT_INDEX, api_key_index=API_KEY_INDEX)


# --- submit -----------------------------------------------------------------


def test_submit_returns_order_id_and_passes_params_to_sdk(fake_sdk) -> None:
    client = make_client()
    order_id = client.submit_limit_order(market_index=3, side="buy", price=2500, size=7)
    signer = fake_sdk.instances[0]
    assert signer.create_order_calls == [
        {
            "market_index": 3,
            "client_order_index": order_id,
            "base_amount": 7,
            "price": 2500,
            "is_ask": False,
            "order_type": fake_sdk.ORDER_TYPE_LIMIT,
            "time_in_force": fake_sdk.ORDER_TIME_IN_FORCE_GOOD_TILL_TIME,
        }
    ]
    assert isinstance(order_id, int)
    # The signer was built for the testnet default host with our credentials.
    assert signer.url == DEFAULT_BASE_URL
    assert signer.account_index == ACCOUNT_INDEX
    assert signer.api_private_keys == {API_KEY_INDEX: API_KEY}
    # The signer's HTTP session is closed after the call.
    assert signer.api_client.closed


def test_submit_sell_side_maps_to_is_ask(fake_sdk) -> None:
    client = make_client()
    client.submit_limit_order(market_index=3, side="sell", price=2500, size=7)
    assert fake_sdk.instances[0].create_order_calls[0]["is_ask"] is True


def test_submit_invalid_side_raises_before_any_sdk_call(fake_sdk) -> None:
    client = make_client()
    with pytest.raises(ValueError, match="side"):
        client.submit_limit_order(market_index=3, side="hold", price=2500, size=7)
    assert fake_sdk.instances == []


def test_submit_sdk_error_tuple_raises_order_error(fake_sdk) -> None:
    fake_sdk.create_order_result = (None, None, "rejected: insufficient margin")
    client = make_client()
    with pytest.raises(OrderError, match="insufficient margin"):
        client.submit_limit_order(market_index=3, side="buy", price=2500, size=7)


def test_submit_non_200_response_raises_with_api_message(fake_sdk) -> None:
    fake_sdk.create_order_result = (object(), FakeResponse(code=400, message="bad price"), None)
    client = make_client()
    with pytest.raises(OrderError, match="bad price"):
        client.submit_limit_order(market_index=3, side="buy", price=2500, size=7)


def test_submit_sdk_exception_is_wrapped_not_swallowed(fake_sdk) -> None:
    fake_sdk.create_order_result = ConnectionError("connection reset")
    client = make_client()
    with pytest.raises(OrderError, match="connection reset"):
        client.submit_limit_order(market_index=3, side="buy", price=2500, size=7)


# --- status -----------------------------------------------------------------


def test_status_returns_sdk_payload_for_known_id(fake_sdk) -> None:
    fake_sdk.active_orders_result = FakeOrders(
        [FakeOrder(order_index=111, client_order_index=222, market_index=3, status="open")]
    )
    client = make_client()
    status = client.get_order_status(222)
    assert status["status"] == "open"
    assert status["order_index"] == 111
    # The read API was queried for our account with an auth token.
    signer = fake_sdk.instances[0]
    assert signer.order_api.calls == [
        {"authorization": "fake-auth-token", "account_index": ACCOUNT_INDEX}
    ]


def test_status_matches_exchange_order_index_too(fake_sdk) -> None:
    fake_sdk.active_orders_result = FakeOrders(
        [FakeOrder(order_index=111, client_order_index=222, market_index=3, status="open")]
    )
    client = make_client()
    assert client.get_order_status(111)["client_order_index"] == 222


def test_status_unknown_order_id_raises(fake_sdk) -> None:
    client = make_client()
    with pytest.raises(OrderError, match="424242"):
        client.get_order_status(424242)


def test_status_non_200_response_raises_with_api_message(fake_sdk) -> None:
    fake_sdk.active_orders_result = FakeOrders([], code=500, message="internal error")
    client = make_client()
    with pytest.raises(OrderError, match="internal error"):
        client.get_order_status(222)


def test_status_sdk_exception_is_wrapped_not_swallowed(fake_sdk) -> None:
    fake_sdk.active_orders_result = ConnectionError("connection reset")
    client = make_client()
    with pytest.raises(OrderError, match="connection reset"):
        client.get_order_status(222)


# --- cancel -----------------------------------------------------------------


def test_cancel_open_order_submits_signed_cancel(fake_sdk) -> None:
    fake_sdk.active_orders_result = FakeOrders(
        [FakeOrder(order_index=111, client_order_index=222, market_index=3, status="open")]
    )
    client = make_client()
    assert client.cancel_order(222) is None
    signer = fake_sdk.instances[0]
    assert signer.cancel_order_calls == [{"market_index": 3, "order_index": 111}]
    assert signer.api_client.closed


def test_cancel_unknown_filled_or_canceled_order_raises(fake_sdk) -> None:
    # Filled/canceled orders are no longer active, so the lookup finds nothing.
    client = make_client()
    with pytest.raises(OrderError, match="424242"):
        client.cancel_order(424242)
    assert fake_sdk.instances[0].cancel_order_calls == []


def test_cancel_sdk_error_tuple_raises_order_error(fake_sdk) -> None:
    fake_sdk.active_orders_result = FakeOrders(
        [FakeOrder(order_index=111, client_order_index=222, market_index=3, status="open")]
    )
    fake_sdk.cancel_order_result = (None, None, "order already canceled")
    client = make_client()
    with pytest.raises(OrderError, match="already canceled"):
        client.cancel_order(222)


def test_cancel_non_200_response_raises_with_api_message(fake_sdk) -> None:
    fake_sdk.active_orders_result = FakeOrders(
        [FakeOrder(order_index=111, client_order_index=222, market_index=3, status="open")]
    )
    fake_sdk.cancel_order_result = (
        object(),
        FakeResponse(code=400, message="already filled"),
        None,
    )
    client = make_client()
    with pytest.raises(OrderError, match="already filled"):
        client.cancel_order(222)


# --- the returned ID ties all three methods together -------------------------


def test_submitted_order_id_drives_status_and_cancel(fake_sdk) -> None:
    client = make_client()
    order_id = client.submit_limit_order(market_index=5, side="buy", price=100, size=1)
    # The exchange now reports that order as active (same client_order_index).
    fake_sdk.active_orders_result = FakeOrders(
        [FakeOrder(order_index=999, client_order_index=order_id, market_index=5, status="open")]
    )
    assert client.get_order_status(order_id)["status"] == "open"
    client.cancel_order(order_id)
    # Each method builds its own signer, so the cancel landed on the last one.
    assert fake_sdk.instances[-1].cancel_order_calls == [{"market_index": 5, "order_index": 999}]


# --- missing / malformed credentials and configuration ----------------------


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_api_private_key_raises_naming_variable_before_any_sdk_call(
    fake_sdk, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("LIGHTER_PRIVATE_KEY", raising=False)
    else:
        monkeypatch.setenv("LIGHTER_PRIVATE_KEY", value)
    with pytest.raises(MissingPrivateKeyError, match="LIGHTER_PRIVATE_KEY"):
        make_client()
    assert fake_sdk.instances == []  # no SDK object was ever constructed


def test_private_key_never_appears_in_errors_or_repr(fake_sdk) -> None:
    fake_sdk.create_order_result = (None, None, "rejected")
    client = make_client()
    with pytest.raises(OrderError) as exc_info:
        client.submit_limit_order(market_index=3, side="buy", price=2500, size=7)
    assert API_KEY not in str(exc_info.value)
    assert API_KEY not in repr(client)


def test_base_url_override_comes_from_env(fake_sdk, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTER_API_URL", "https://testnet.zklighter.io")
    client = make_client()
    client.submit_limit_order(market_index=3, side="buy", price=2500, size=7)
    assert fake_sdk.instances[0].url == "https://testnet.zklighter.io"


@pytest.mark.parametrize(
    "value",
    [
        "https://mainnet.zklighter.io",  # mainnet API host
        "https://mainnet.app.lighter.xyz",  # mainnet web host
        "https://testnet.evilzklighter.io",  # lookalike testnet host
    ],
)
def test_non_testnet_base_url_is_refused_before_any_sdk_call(
    fake_sdk, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("LIGHTER_API_URL", value)
    with pytest.raises(InvalidBaseUrlError):
        make_client()
    assert fake_sdk.instances == []
