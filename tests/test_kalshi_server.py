"""Behavior tests for the Kalshi WebSocket server entry point.

All network interaction is mocked at the ``_run`` seam via monkeypatch —
these tests never open a socket or touch real key material.
"""

import importlib
import os
import subprocess
import sys

import pytest

import kalshi_testing  # noqa: F401  (ensures the original module object is restorable)
from kalshi_testing import server
from kalshi_testing.server import DEFAULT_WS_URL, main

API_KEY_ID = "kalshi-test-key-id"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\ndummy-not-a-real-key\n-----END PRIVATE KEY-----"  # noqa: S105 — dummy, not a real key


@pytest.fixture
def run_seam(monkeypatch: pytest.MonkeyPatch) -> list:
    """Replace the async _run seam with a recorder."""

    calls: list = []

    async def fake_run(config) -> None:
        calls.append(config)

    monkeypatch.setattr(server, "_run", fake_run)
    return calls


def _set_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KALSHI_API_KEY_ID", API_KEY_ID)
    monkeypatch.setenv("KALSHI_PRIVATE_KEY", PRIVATE_KEY)


def test_missing_api_key_id_exits_one_naming_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, run_seam: list
) -> None:
    monkeypatch.delenv("KALSHI_API_KEY_ID", raising=False)
    monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "KALSHI_API_KEY_ID" in captured.err
    assert captured.out == ""
    assert run_seam == []  # no connection attempted without credentials


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_private_key_takes_the_same_error_path(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture,
    run_seam: list,
    value: str | None,
) -> None:
    monkeypatch.setenv("KALSHI_API_KEY_ID", API_KEY_ID)
    if value is None:
        monkeypatch.delenv("KALSHI_PRIVATE_KEY", raising=False)
    else:
        monkeypatch.setenv("KALSHI_PRIVATE_KEY", value)
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "KALSHI_PRIVATE_KEY" in captured.err
    assert captured.out == ""
    assert run_seam == []


def test_credentials_present_runs_server_with_those_exact_values(
    monkeypatch: pytest.MonkeyPatch, run_seam: list
) -> None:
    _set_credentials(monkeypatch)
    monkeypatch.delenv("KALSHI_WS_URL", raising=False)
    rc = main()
    assert rc == 0
    assert len(run_seam) == 1
    config = run_seam[0]
    assert config.api_key_id == API_KEY_ID
    assert config.private_key == PRIVATE_KEY
    assert config.ws_url == DEFAULT_WS_URL


def test_keyboard_interrupt_shuts_down_cleanly(
    monkeypatch: pytest.MonkeyPatch, run_seam: list
) -> None:
    async def interrupted(config) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(server, "_run", interrupted)
    _set_credentials(monkeypatch)
    assert main() == 0  # Ctrl+C -> clean exit 0


def test_connection_failure_exits_nonzero_without_leaking_key(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    async def failing(config) -> None:
        raise ConnectionError("handshake refused")

    monkeypatch.setattr(server, "_run", failing)
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert rc != 0
    assert captured.err != ""
    # The private key may never leak into any output stream.
    assert PRIVATE_KEY not in captured.err
    assert PRIVATE_KEY not in captured.out


def test_cli_missing_credentials_exits_one_and_names_variable(tmp_path) -> None:
    """AC2 through the real __main__ path: no credentials -> exit 1, stderr names it."""
    env = dict(os.environ)
    env.pop("KALSHI_API_KEY_ID", None)
    env.pop("KALSHI_PRIVATE_KEY", None)
    result = subprocess.run(
        [sys.executable, "-m", "kalshi_testing.server"],
        cwd=tmp_path,  # no .env here
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "KALSHI_API_KEY_ID" in result.stderr
    assert result.stdout == ""


def test_import_loads_dotenv_from_cwd(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = "KALSHI_DOTENV_TEST_SENTINEL"
    (tmp_path / ".env").write_text(f"{sentinel}=loaded\n")
    monkeypatch.delenv(sentinel, raising=False)
    monkeypatch.chdir(tmp_path)
    original = sys.modules.pop("kalshi_testing")
    try:
        importlib.import_module("kalshi_testing")
        assert os.environ[sentinel] == "loaded"
    finally:
        sys.modules["kalshi_testing"] = original
