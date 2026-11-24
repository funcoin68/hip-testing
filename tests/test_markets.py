"""Behavior tests for the market-listing CLI.

All network interaction is mocked at the ``_fetch_order_books`` seam — these
tests never open a socket or touch key material beyond dummy values. The
canned payload mirrors the real ``/api/v1/orderBooks`` response shape
(verified against https://api.testnet.lighter.xyz on 2026-11-24).
"""

import json

import pytest

from lighter_testing import markets
from lighter_testing.markets import MarketListError, main

WALLET_KEY = "0xdummytestnetwalletkey"  # noqa: S105 — dummy, not a real key

CANNED_ORDER_BOOKS = [
    {
        "symbol": "ETH",
        "market_id": 4095,
        "market_type": "perp",
        "status": "active",
        "min_base_amount": "0.0050",
    },
    {
        "symbol": "BTC",
        "market_id": 4096,
        "market_type": "perp",
        "status": "active",
        "min_base_amount": "0.0005",
    },
    {
        "symbol": "ETH/USDC",
        "market_id": 4098,
        "market_type": "spot",
        "status": "active",
        "min_base_amount": "0.0100",
    },
]


@pytest.fixture
def seam(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace _fetch_order_books with a recorder returning canned data."""
    calls: list[str] = []

    def fake_fetch(base_url: str) -> list[dict]:
        calls.append(base_url)
        return CANNED_ORDER_BOOKS

    monkeypatch.setattr(markets, "_fetch_order_books", fake_fetch)
    return calls


def test_stdout_is_a_single_parseable_json_array(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 0
    markets_out = json.loads(captured.out)  # must parse without modification
    assert isinstance(markets_out, list)
    assert len(markets_out) == len(CANNED_ORDER_BOOKS)


def test_every_market_has_exactly_perp_or_spot_market_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    assert main([]) == 0
    markets_out = json.loads(capsys.readouterr().out)
    for market in markets_out:
        assert market["market_type"] in {"perp", "spot"}
    # Both kinds present when both exist on testnet.
    assert {m["market_type"] for m in markets_out} == {"perp", "spot"}


def test_market_identity_fields_are_preserved(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    assert main([]) == 0
    markets_out = json.loads(capsys.readouterr().out)
    eth_perp = next(m for m in markets_out if m["market_id"] == 4095)
    assert eth_perp["symbol"] == "ETH"
    assert eth_perp["status"] == "active"
    assert eth_perp["market_type"] == "perp"


def test_default_base_url_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    assert main([]) == 0
    capsys.readouterr()
    assert seam == ["https://api.testnet.lighter.xyz"]


def test_empty_base_url_env_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.setenv("LIGHTER_API_URL", "")
    assert main([]) == 0
    capsys.readouterr()
    assert seam == ["https://api.testnet.lighter.xyz"]


@pytest.mark.parametrize(
    "value",
    [
        "https://mainnet.zklighter.io",  # mainnet API host
        "https://testnet.evilzklighter.io",  # lookalike testnet host
    ],
)
def test_non_testnet_base_url_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str], value: str
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.setenv("LIGHTER_API_URL", value)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 2
    assert captured.out == ""
    assert seam == []  # nothing was sent to a non-testnet host


def test_missing_wallet_private_key_exits_nonzero_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert "WALLET_PRIVATE_KEY" in captured.err
    assert captured.out == ""
    assert seam == []  # no request attempted without a key


def test_wallet_key_never_appears_in_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    assert main([]) == 0
    captured = capsys.readouterr()
    assert WALLET_KEY not in captured.out
    assert WALLET_KEY not in captured.err


def test_fetch_failure_exits_one_with_stderr_and_empty_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def failing_fetch(base_url: str) -> list[dict]:
        raise MarketListError("GET /api/v1/orderBooks failed: HTTP 502")

    monkeypatch.setattr(markets, "_fetch_order_books", failing_fetch)
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""  # no partial JSON on stdout
    assert "orderBooks" in captured.err
    assert WALLET_KEY not in captured.err


def test_unmappable_market_type_fails_visibly_instead_of_dropping(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def weird_fetch(base_url: str) -> list[dict]:
        return [{"market_id": 1, "symbol": "X", "status": "active", "market_type": "future"}]

    monkeypatch.setattr(markets, "_fetch_order_books", weird_fetch)
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""  # the odd book is not silently dropped
    assert "future" in captured.err
