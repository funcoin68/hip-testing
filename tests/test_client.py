"""Behavior tests for the Lighter testnet client configuration."""

import pytest

from lighter_testing import (
    DEFAULT_BASE_URL,
    LighterClient,
    MissingPrivateKeyError,
    load_config,
)


def test_default_config_exposes_lighter_testnet_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LIGHTER_API_BASE_URL", raising=False)
    monkeypatch.setenv("LIGHTER_PRIVATE_KEY", "test-key")
    config = load_config()
    assert config.base_url == "https://testnet.app.lighter.xyz"
    assert config.base_url == DEFAULT_BASE_URL


def test_base_url_env_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTER_API_BASE_URL", "https://example.test")
    monkeypatch.setenv("LIGHTER_PRIVATE_KEY", "test-key")
    assert load_config().base_url == "https://example.test"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_empty_private_key_fails_visibly(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("LIGHTER_PRIVATE_KEY", raising=False)
    else:
        monkeypatch.setenv("LIGHTER_PRIVATE_KEY", value)
    with pytest.raises(MissingPrivateKeyError):
        load_config()


def test_valid_key_is_carried_into_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTER_PRIVATE_KEY", "0xtestkey")
    assert load_config().private_key == "0xtestkey"


def test_client_defaults_to_env_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LIGHTER_PRIVATE_KEY", "0xtestkey")
    assert LighterClient().config.private_key == "0xtestkey"
