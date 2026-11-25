"""Behavior tests for the market-listing CLI.

All network interaction is mocked at the ``_fetch_order_books`` and
``_fetch_binary_options`` seams — these tests never open a socket or touch
key material beyond dummy values. The canned perp/spot payload mirrors the
real ``/api/v1/orderBooks`` response shape (verified against
https://api.testnet.lighter.xyz on 2026-11-24); the canned binary payload
mirrors the ``order_books`` entries inside the real ``/api/v1/events``
response (``market_type: "binary_options"``, verified on 2026-11-25).
"""

from __future__ import annotations

import json
import re
import urllib.error

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


CANNED_BINARY_BOOKS = [
    {
        "symbol": "VD-MLS-SJE-HOU-20261124-1",
        "market_id": 4681,
        "market_type": "binary_options",
        "status": "active",
        "default_price": 50,
    },
    {
        "symbol": "VD-MLS-SJE-HOU-20261124-2",
        "market_id": 4682,
        "market_type": "binary_options",
        "status": "waiting-for-settlement",
        "default_price": 50,
    },
    {
        "symbol": "VD-MLS-SJE-HOU-20261124-3",
        "market_id": 4683,
        "market_type": "binary_options",
        "status": "expired",
        "default_price": 50,
    },
    {
        "symbol": "VD-MLS-SJE-HOU-20261124-4",
        "market_id": 4684,
        "market_type": "binary_options",
        "status": "closed",
        "default_price": 50,
    },
]

# Only the active and waiting-for-settlement books above may reach stdout.
KEPT_BINARY_IDS = [4681, 4682]

# Used by the missing-status test: an entry with no status key at all.
NO_STATUS_BINARY_BOOK = {
    "symbol": "VD-MLS-SJE-HOU-20261124-9",
    "market_id": 4689,
    "market_type": "binary_options",
    "default_price": 50,
}

EXPECTED_PERP_SPOT = [
    {"market_id": 4095, "symbol": "ETH", "status": "active", "market_type": "perp"},
    {"market_id": 4096, "symbol": "BTC", "status": "active", "market_type": "perp"},
    {"market_id": 4098, "symbol": "ETH/USDC", "status": "active", "market_type": "spot"},
]


@pytest.fixture
def binary_seam(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace _fetch_binary_options with a recorder returning canned data."""
    calls: list[str] = []

    def fake_fetch(base_url: str) -> list[dict]:
        calls.append(base_url)
        return CANNED_BINARY_BOOKS

    monkeypatch.setattr(markets, "_fetch_binary_options", fake_fetch)
    return calls


@pytest.fixture
def seam(monkeypatch: pytest.MonkeyPatch, binary_seam: list[str]) -> list[str]:
    """Replace _fetch_order_books with a recorder returning canned data.

    Also stubs the binary-options seam (via ``binary_seam``) so no test that
    uses this fixture can touch the network.
    """
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
    assert len(markets_out) == len(EXPECTED_PERP_SPOT) + len(KEPT_BINARY_IDS)


def test_every_market_has_a_known_market_type(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    assert main([]) == 0
    markets_out = json.loads(capsys.readouterr().out)
    for market in markets_out:
        assert market["market_type"] in {"perp", "spot", "binary_options"}
    # All three kinds present when all exist on testnet.
    assert {m["market_type"] for m in markets_out} == {"perp", "spot", "binary_options"}


def test_stdout_merges_perp_spot_and_binary_in_one_array(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 0
    markets_out = json.loads(captured.out)  # one valid JSON document
    assert isinstance(markets_out, list)
    # Perp/spot entries come first and are byte-identical to HIP-10 output.
    assert markets_out[: len(EXPECTED_PERP_SPOT)] == EXPECTED_PERP_SPOT
    binary_out = markets_out[len(EXPECTED_PERP_SPOT) :]
    assert [m["market_id"] for m in binary_out] == KEPT_BINARY_IDS
    for market in binary_out:
        assert market["market_type"] == "binary_options"
        assert market["status"] in {"active", "waiting-for-settlement"}
        assert set(market) == {"market_id", "symbol", "status", "market_type"}


def test_only_active_and_waiting_for_settlement_binary_options_appear(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 0
    markets_out = json.loads(captured.out)
    binary_out = markets_out[len(EXPECTED_PERP_SPOT) :]
    assert [m["market_id"] for m in binary_out] == KEPT_BINARY_IDS
    assert {m["status"] for m in binary_out} == {"active", "waiting-for-settlement"}
    for market in binary_out:
        assert market["market_type"] == "binary_options"
        assert "status" in market
    # Filtered statuses never reach stdout.
    assert "expired" not in captured.out
    assert "closed" not in captured.out


def test_binary_entry_without_status_is_skipped_with_warning(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setattr(
        markets, "_fetch_binary_options", lambda base_url: [NO_STATUS_BINARY_BOOK]
    )
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert json.loads(captured.out) == EXPECTED_PERP_SPOT  # entry excluded from stdout
    assert re.search(
        r"warning:.*skipping.*VD-MLS-SJE-HOU-20261124-9.*no status", captured.err
    )
    assert WALLET_KEY not in captured.err


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


def test_binary_seam_receives_the_same_resolved_base_url(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    seam: list[str],
    binary_seam: list[str],
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    assert main([]) == 0
    capsys.readouterr()
    assert seam == ["https://api.testnet.lighter.xyz"]
    assert binary_seam == ["https://api.testnet.lighter.xyz"]


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


def test_successful_run_has_empty_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    assert main([]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    json.loads(captured.out)  # stdout still parses


def test_binary_fetch_failure_is_fatal(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    def failing_binary_fetch(base_url: str) -> list[dict]:
        raise MarketListError("GET /api/v1/events failed: HTTP 502")

    monkeypatch.setattr(markets, "_fetch_binary_options", failing_binary_fetch)
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""  # no perp/spot-only partial output
    assert "events" in captured.err  # the error identifies the failure
    assert WALLET_KEY not in captured.out
    assert WALLET_KEY not in captured.err


def test_empty_binary_list_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    monkeypatch.setattr(markets, "_fetch_binary_options", lambda base_url: [])
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.err == ""  # empty is not a failure — no warning
    assert json.loads(captured.out) == EXPECTED_PERP_SPOT


def test_malformed_binary_entry_is_fatal_not_dropped(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[str]
) -> None:
    def weird_binary_fetch(base_url: str) -> list[dict]:
        return [{"market_id": 1, "symbol": "X", "status": "active", "market_type": "future"}]

    monkeypatch.setattr(markets, "_fetch_binary_options", weird_binary_fetch)
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.out == ""  # the odd book is not silently dropped
    assert "future" in captured.err
    assert WALLET_KEY not in captured.err


class _FakeResponse:
    """Minimal stand-in for the urllib response context manager."""

    def __init__(self, payload: object) -> None:
        self._body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._body


def _stub_urlopen(monkeypatch: pytest.MonkeyPatch, pages: list[object]) -> list[str]:
    """Serve canned payloads from urllib (no socket), recording requested URLs."""
    urls: list[str] = []

    def fake_urlopen(url: str, timeout: int) -> _FakeResponse:
        urls.append(url)
        return _FakeResponse(pages[len(urls) - 1])

    monkeypatch.setattr(markets.urllib.request, "urlopen", fake_urlopen)
    return urls


def test_fetch_binary_options_flattens_event_order_books(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "code": 200,
            "next_cursor": None,
            "events": [
                {"event_id": 211, "order_books": [CANNED_BINARY_BOOKS[0]]},
                {"event_id": 212, "order_books": [CANNED_BINARY_BOOKS[1]]},
                {"event_id": 213, "order_books": [CANNED_BINARY_BOOKS[2]]},
                {"event_id": 214, "order_books": [CANNED_BINARY_BOOKS[3]]},
            ],
        }
    ]
    urls = _stub_urlopen(monkeypatch, pages)
    books = markets._fetch_binary_options("https://api.testnet.lighter.xyz")
    assert books == CANNED_BINARY_BOOKS
    assert urls == ["https://api.testnet.lighter.xyz/api/v1/events"]


def test_fetch_binary_options_follows_next_cursor_until_empty_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "code": 200,
            "next_cursor": 112,
            "events": [{"event_id": 211, "order_books": [CANNED_BINARY_BOOKS[0]]}],
        },
        {"code": 200, "next_cursor": 0, "events": []},
    ]
    urls = _stub_urlopen(monkeypatch, pages)
    books = markets._fetch_binary_options("https://api.testnet.lighter.xyz")
    assert books == [CANNED_BINARY_BOOKS[0]]
    assert urls == [
        "https://api.testnet.lighter.xyz/api/v1/events",
        "https://api.testnet.lighter.xyz/api/v1/events?cursor=112",
    ]


def test_fetch_binary_options_stops_when_cursor_is_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "code": 200,
            "next_cursor": 0,
            "events": [{"event_id": 211, "order_books": [CANNED_BINARY_BOOKS[0]]}],
        }
    ]
    _stub_urlopen(monkeypatch, pages)
    books = markets._fetch_binary_options("https://api.testnet.lighter.xyz")
    assert books == [CANNED_BINARY_BOOKS[0]]


def test_fetch_binary_options_http_error_raises_market_list_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(url: str, timeout: int) -> _FakeResponse:
        raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)

    monkeypatch.setattr(markets.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(MarketListError, match="HTTP 502"):
        markets._fetch_binary_options("https://api.testnet.lighter.xyz")


def test_fetch_binary_options_connection_error_raises_market_list_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_urlopen(url: str, timeout: int) -> _FakeResponse:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(markets.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(MarketListError, match="connection refused"):
        markets._fetch_binary_options("https://api.testnet.lighter.xyz")


@pytest.mark.parametrize(
    ("page", "match"),
    [
        (b"not json", "unparseable JSON"),
        ({"code": 200}, "no events list"),
        (
            {"code": 200, "next_cursor": None, "events": [{"event_id": 211}]},
            "no order_books list",
        ),
    ],
)
def test_fetch_binary_options_malformed_payload_raises(
    monkeypatch: pytest.MonkeyPatch, page: object, match: str
) -> None:
    _stub_urlopen(monkeypatch, [page])
    with pytest.raises(MarketListError, match=match):
        markets._fetch_binary_options("https://api.testnet.lighter.xyz")


def test_fetch_binary_options_non_advancing_cursor_raises_instead_of_looping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "code": 200,
            "next_cursor": 7,
            "events": [{"event_id": 211, "order_books": [CANNED_BINARY_BOOKS[0]]}],
        },
        {
            "code": 200,
            "next_cursor": 7,
            "events": [{"event_id": 212, "order_books": [CANNED_BINARY_BOOKS[1]]}],
        },
    ]
    _stub_urlopen(monkeypatch, pages)
    with pytest.raises(MarketListError, match="next_cursor"):
        markets._fetch_binary_options("https://api.testnet.lighter.xyz")
