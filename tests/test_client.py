"""Behavior tests for the Lighter testnet client configuration."""

import pytest

from lighter_testing import (
    DEFAULT_BASE_URL,
    InvalidBaseUrlError,
    LighterClient,
    MissingPrivateKeyError,
    load_config,
)


def test_default_config_exposes_lighter_testnet_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LIGHTER_API_URL", raising=False)
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "test-key")
    config = load_config()
    assert config.base_url == "https://api.testnet.lighter.xyz"
    assert config.base_url == DEFAULT_BASE_URL


def test_base_url_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTER_API_URL", "https://testnet.zklighter.io")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "test-key")
    assert load_config().base_url == "https://testnet.zklighter.io"


def test_empty_base_url_env_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTER_API_URL", "")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "test-key")
    assert load_config().base_url == DEFAULT_BASE_URL


def test_docs_api_testnet_host_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTER_API_URL", "https://api.testnet.lighter.xyz")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "test-key")
    assert load_config().base_url == "https://api.testnet.lighter.xyz"


@pytest.mark.parametrize(
    "value",
    [
        "http://testnet.app.lighter.xyz",  # plaintext scheme
        "https://app.lighter.xyz",  # mainnet host, no "testnet" label
        "https://example.test",  # unknown host
        "https://testnet.attacker.example",  # "testnet" label, wrong domain
        "https://testnet.evil.lighter.xyz",  # "testnet" label not next to the suffix
        "https://nottestnet.com",  # substring only, wrong domain
        "https://eviltestnet.app.lighter.xyz",  # lookalike label on real domain
        "https://mainnet.zklighter.io",  # mainnet API host
        "https://zklighter.io",  # bare API domain, no "testnet" label
        "https://testnet.evilzklighter.io",  # lookalike suffix on API domain
    ],
)
def test_non_testnet_base_url_fails_visibly(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("LIGHTER_API_URL", value)
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "test-key")
    with pytest.raises(InvalidBaseUrlError):
        load_config()


def test_zklighter_testnet_api_host_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTER_API_URL", "https://testnet.zklighter.io")
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "test-key")
    assert load_config().base_url == "https://testnet.zklighter.io"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_empty_private_key_fails_visibly(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    else:
        monkeypatch.setenv("WALLET_PRIVATE_KEY", value)
    with pytest.raises(MissingPrivateKeyError):
        load_config()


def test_valid_key_is_carried_into_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "0xtestkey")
    assert load_config().private_key == "0xtestkey"


def test_client_defaults_to_env_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "0xtestkey")
    assert LighterClient().config.private_key == "0xtestkey"


def test_client_uses_explicit_config_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WALLET_PRIVATE_KEY", raising=False)
    config = load_config({"WALLET_PRIVATE_KEY": "0xexplicit-key"})
    client = LighterClient(config=config)
    assert client.config is config
    assert client.config.private_key == "0xexplicit-key"


def test_config_repr_does_not_leak_private_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WALLET_PRIVATE_KEY", "0xsecret-testnet-key")
    config = load_config()
    assert "0xsecret-testnet-key" not in repr(config)
    assert "0xsecret-testnet-key" not in str(config)
