"""Behavior tests for the Kalshi WebSocket server entry point.

All network interaction is mocked at the ``_run`` seam via monkeypatch —
these tests never open a socket or touch real key material.
"""

import contextlib
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

import kalshi_testing  # noqa: F401  (ensures the original module object is restorable)
from kalshi_testing import server
from kalshi_testing.server import DEFAULT_WS_URL, main

API_KEY_ID = "kalshi-test-key-id"
# Dummy PEM string, not a real key.
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----\ndummy-not-a-real-key\n-----END PRIVATE KEY-----"


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


def test_plaintext_ws_url_rejected_before_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, run_seam: list
) -> None:
    _set_credentials(monkeypatch)
    monkeypatch.setenv("KALSHI_WS_URL", "ws://localhost:9999/ws")
    rc = main()
    captured = capsys.readouterr()
    assert rc == 1
    assert "wss://" in captured.err
    assert run_seam == []  # no connection attempted with a plaintext URL


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


def test_server_close_logs_connected_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    """Exercise the real _run: 'connected' is logged, and a server-side close
    exits non-zero so it cannot be mistaken for a clean Ctrl+C shutdown."""

    class _FakeWs:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration  # server closes immediately

    class _FakeConnect:
        def __init__(self, url: str, additional_headers: dict) -> None:
            self.url = url
            self.additional_headers = additional_headers

        async def __aenter__(self) -> _FakeWs:
            return _FakeWs()

        async def __aexit__(self, *exc) -> bool:
            return False

    monkeypatch.setattr(server.websockets, "connect", _FakeConnect)
    monkeypatch.setattr(server, "_auth_headers", lambda *args: {})
    _set_credentials(monkeypatch)
    rc = main()
    captured = capsys.readouterr()
    assert f"connected to {DEFAULT_WS_URL}" in captured.out
    assert rc == 2  # dropped connection != clean Ctrl+C exit 0
    assert "closed" in captured.err
    assert PRIVATE_KEY not in captured.err
    assert PRIVATE_KEY not in captured.out


def test_import_ignores_dotenv_planted_in_cwd(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A .env planted in an untrusted cwd must never be loaded — only the
    repo-root .env is a trusted credential source."""
    sentinel = "KALSHI_DOTENV_TEST_SENTINEL"
    (tmp_path / ".env").write_text(f"{sentinel}=planted\n")
    monkeypatch.delenv(sentinel, raising=False)
    monkeypatch.chdir(tmp_path)
    original = sys.modules.pop("kalshi_testing")
    try:
        importlib.import_module("kalshi_testing")
        assert sentinel not in os.environ
    finally:
        sys.modules["kalshi_testing"] = original


# --- Direct-script .env loading (HIP-17): running server.py as a script must
# load the repo-root .env itself, because the package __init__ never runs. ---

_DOTENV_CREDENTIALS = (
    "KALSHI_API_KEY_ID=dotenv-test-key-id\n"
    "KALSHI_PRIVATE_KEY=dotenv-dummy-key\n"
    # Nothing listens on port 9 on localhost, so a real connection attempt
    # refuses instantly instead of hanging.
    "KALSHI_WS_URL=wss://127.0.0.1:9/ws\n"
)


def _clean_env() -> dict[str, str]:
    """os.environ without the Kalshi variables under test."""
    env = dict(os.environ)
    for var in ("KALSHI_API_KEY_ID", "KALSHI_PRIVATE_KEY", "KALSHI_WS_URL"):
        env.pop(var, None)
    return env


def _repo_root() -> Path:
    for directory in Path(__file__).resolve().parents:
        if (directory / "pyproject.toml").exists():
            return directory
    pytest.skip("repo root (pyproject.toml) not found above tests/")


@contextlib.contextmanager
def _repo_root_dotenv(contents: str):
    """Temporarily write a repo-root .env; never touches a real one."""
    env_path = _repo_root() / ".env"
    if env_path.exists():
        pytest.skip("real repo-root .env present — refusing to touch it")
    env_path.write_text(contents)
    try:
        yield
    finally:
        env_path.unlink()


def _server_script() -> Path:
    return _repo_root() / "src" / "kalshi_testing" / "server.py"


def test_direct_script_from_package_dir_loads_repo_root_dotenv() -> None:
    """AC1: `cd src/kalshi_testing && python server.py` with a repo-root .env
    gets past the credential guardrail (exit 2 = failed past config, not the
    missing-credential exit 1)."""
    with _repo_root_dotenv(_DOTENV_CREDENTIALS):
        result = subprocess.run(
            [sys.executable, "server.py"],
            cwd=_server_script().parent,
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 2
    assert "KALSHI_API_KEY_ID" not in result.stderr


def test_direct_script_from_other_cwd_loads_repo_root_dotenv(tmp_path) -> None:
    """AC2 (PyCharm simulation): running server.py by absolute path with a
    non-repo-root working directory still loads the repo-root .env."""
    with _repo_root_dotenv(_DOTENV_CREDENTIALS):
        result = subprocess.run(
            [sys.executable, str(_server_script())],
            cwd=tmp_path,  # PyCharm debug working directory: not the repo root
            env=_clean_env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 2
    assert "KALSHI_API_KEY_ID" not in result.stderr


def test_direct_script_without_repo_root_dotenv_still_fails_guardrail() -> None:
    """AC4: with no repo-root .env, direct-script invocation still exits 1
    naming KALSHI_API_KEY_ID — same behavior as the -m path."""
    if (_repo_root() / ".env").exists():
        pytest.skip("real repo-root .env present — would be loaded")
    result = subprocess.run(
        [sys.executable, "server.py"],
        cwd=_server_script().parent,
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "KALSHI_API_KEY_ID" in result.stderr
    assert result.stdout == ""
