"""Behavior tests for the API-key creation CLI.

All network and native-signer interaction is mocked at the
``register_api_key`` seam — these tests never open a socket or touch key
material beyond dummy values.
"""

import pytest

from lighter_testing import api_keys
from lighter_testing.api_keys import DEFAULT_API_BASE_URL, ApiKeyCreationError, main

GENERATED_KEY = "0xNEWLYGENERATEDAPIKEY"
WALLET_KEY = "0xdummytestnetwalletkey"  # noqa: S105 — dummy, not a real key


@pytest.fixture
def seam(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Replace register_api_key with a recorder returning a canned key."""
    calls: list[dict] = []

    def fake_register(**kwargs) -> str:
        calls.append(kwargs)
        return GENERATED_KEY

    monkeypatch.setattr(api_keys, "register_api_key", fake_register)
    return calls


def test_success_prints_key_to_stdout_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[dict]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_BASE_URL", raising=False)
    rc = main(["--account-index", "2081", "--api-key-index", "6"])
    captured = capsys.readouterr()
    assert rc == 0
    assert captured.out == GENERATED_KEY + "\n"  # exactly one stdout line
    assert captured.err == ""
    # The signing flow received the CLI values and the wallet key (not constants).
    assert seam == [
        {
            "api_base_url": DEFAULT_API_BASE_URL,
            "wallet_private_key": WALLET_KEY,
            "account_index": 2081,
            "api_key_index": 6,
        }
    ]


def test_account_and_key_index_are_cli_arguments(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[dict]
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_BASE_URL", raising=False)
    assert main(["--account-index", "42", "--api-key-index", "7"]) == 0
    capsys.readouterr()
    assert seam[0]["account_index"] == 42
    assert seam[0]["api_key_index"] == 7


def test_missing_wallet_private_key_exits_nonzero_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[dict]
) -> None:
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("LIGHTER_API_BASE_URL", raising=False)
    rc = main(["--account-index", "2081", "--api-key-index", "6"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "WALLET_PRIVATE_KEY" in captured.err
    assert captured.out == ""
    assert seam == []  # no request attempted without a key


@pytest.mark.parametrize("value", ["", "   "])
def test_empty_or_whitespace_key_takes_the_same_error_path(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[dict], value: str
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", value)
    monkeypatch.delenv("LIGHTER_API_BASE_URL", raising=False)
    rc = main(["--account-index", "2081", "--api-key-index", "6"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "WALLET_PRIVATE_KEY" in captured.err
    assert captured.out == ""
    assert seam == []


@pytest.mark.parametrize(
    "value",
    [
        "https://mainnet.zklighter.io",  # mainnet API host
        "https://mainnet.app.lighter.xyz",  # mainnet web host
        "https://testnet.evilzklighter.io",  # lookalike testnet host
    ],
)
def test_non_testnet_base_url_is_refused_before_any_request(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, seam: list[dict], value: str
) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.setenv("LIGHTER_API_BASE_URL", value)
    rc = main(["--account-index", "2081", "--api-key-index", "6"])
    captured = capsys.readouterr()
    assert rc != 0
    assert captured.out == ""
    assert seam == []  # nothing was sent to a non-testnet host


def test_registration_error_exits_nonzero_without_printing_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    def failing_register(**kwargs) -> str:
        raise ApiKeyCreationError("API key registration refused: invalid nonce")

    monkeypatch.setattr(api_keys, "register_api_key", failing_register)
    monkeypatch.setenv("WALLET_PRIVATE_KEY", WALLET_KEY)
    monkeypatch.delenv("LIGHTER_API_BASE_URL", raising=False)
    rc = main(["--account-index", "2081", "--api-key-index", "6"])
    captured = capsys.readouterr()
    assert rc != 0
    assert captured.out == ""  # no key (and no partial output) on stdout
    assert "registration refused" in captured.err
    # Neither the wallet key nor the generated key may leak into any output.
    assert WALLET_KEY not in captured.err
    assert GENERATED_KEY not in captured.err
