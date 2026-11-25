"""Behavior tests for the order_debug lifecycle script.

``LighterOrderClient`` is mocked wholesale (the fake records calls and never
opens a socket) for the happy-path / abort / override tests. The guard tests
(non-testnet URL, missing key) run the real client constructor, which performs
its validation before any SDK or network object exists — so the guards are
verified for real, not mocked away.
"""

from __future__ import annotations

import pytest

from lighter_testing import order_debug

API_KEY = "0xdummytestnetapikey"  # noqa: S105 — dummy, not a real key


class FakeOrderClient:
    """Stands in for LighterOrderClient: records calls, no network."""

    instances: list[FakeOrderClient] = []
    submit_result: int = 4242
    status_result: dict = {"status": "open"}

    def __init__(self, account_index: int, api_key_index: int) -> None:
        self.account_index = account_index
        self.api_key_index = api_key_index
        self.calls: list[tuple] = []
        type(self).instances.append(self)

    def submit_limit_order(self, market_index: int, side: str, price: int, size: int) -> int:
        self.calls.append(("submit", market_index, side, price, size))
        return type(self).submit_result

    def get_order_status(self, order_id: int) -> dict:
        self.calls.append(("status", order_id))
        return dict(type(self).status_result)

    def cancel_order(self, order_id: int) -> None:
        self.calls.append(("cancel", order_id))


@pytest.fixture
def fake_client(monkeypatch: pytest.MonkeyPatch) -> type[FakeOrderClient]:
    """Install the fake client and a valid dummy environment."""
    FakeOrderClient.instances = []
    FakeOrderClient.submit_result = 4242
    FakeOrderClient.status_result = {"status": "open"}
    monkeypatch.setattr(order_debug, "LighterOrderClient", FakeOrderClient)
    monkeypatch.setenv("LIGHTER_PRIVATE_KEY", API_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    monkeypatch.setattr(order_debug, "ORDER_ID_OVERRIDE", None)
    return FakeOrderClient


def confirm(monkeypatch: pytest.MonkeyPatch, answer: str) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: answer)


# --- happy path: submit -> status -> cancel, in that order -------------------


def test_confirmed_run_submits_then_statuses_then_cancels(
    fake_client, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    confirm(monkeypatch, "y")
    assert order_debug.main() == 0
    client = fake_client.instances[0]
    order_id = fake_client.submit_result
    # The exact configured order details reach submit; the returned ID drives
    # status and cancel; and the three calls happen in sequence.
    assert client.calls == [
        ("submit", order_debug.MARKET_INDEX, order_debug.SIDE, order_debug.PRICE, order_debug.SIZE),
        ("status", order_id),
        ("cancel", order_id),
    ]
    out = capsys.readouterr().out
    assert str(order_debug.MARKET_INDEX) in out
    assert order_debug.SIDE in out
    assert str(order_id) in out


# --- declined confirmation aborts before any submission ----------------------


@pytest.mark.parametrize("answer", ["n", "", "no", "NO"])
def test_declined_confirmation_aborts_with_no_order_calls(
    fake_client, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    confirm(monkeypatch, answer)
    assert order_debug.main() == 0
    assert fake_client.instances[0].calls == []  # submit/status/cancel never called


# --- guards run the real constructor (no network exists at that point) -------


def test_non_testnet_base_url_refused_before_any_network(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setenv("LIGHTER_PRIVATE_KEY", API_KEY)
    monkeypatch.setenv("LIGHTER_API_URL", "https://app.lighter.xyz")
    assert order_debug.main() == 2
    assert "testnet" in capsys.readouterr().err


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_private_key_refused_before_any_network(
    monkeypatch: pytest.MonkeyPatch, value: str | None, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    if value is None:
        monkeypatch.delenv("LIGHTER_PRIVATE_KEY", raising=False)
    else:
        monkeypatch.setenv("LIGHTER_PRIVATE_KEY", value)
    assert order_debug.main() == 1
    assert "LIGHTER_PRIVATE_KEY" in capsys.readouterr().err


# --- order-ID override: skip submit, status/cancel the pre-existing order ----


def test_order_id_override_skips_submit(fake_client, monkeypatch: pytest.MonkeyPatch) -> None:
    confirm(monkeypatch, "y")
    monkeypatch.setattr(order_debug, "ORDER_ID_OVERRIDE", 777)
    assert order_debug.main() == 0
    assert fake_client.instances[0].calls == [("status", 777), ("cancel", 777)]
