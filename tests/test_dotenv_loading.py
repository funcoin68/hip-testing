"""Test that importing lighter_testing loads .env from the current working directory."""

import importlib
import os
import subprocess
import sys

import pytest

import lighter_testing  # noqa: F401  (ensures the original module object is restorable)


def _clean_env(**overrides: str) -> dict[str, str]:
    """os.environ without the variables under test, plus explicit overrides."""
    env = dict(os.environ)
    env.pop("WALLET_PRIVATE_KEY", None)
    env.pop("LIGHTER_API_BASE_URL", None)
    env.update(overrides)
    return env


def test_import_loads_dotenv_from_cwd(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel = "LIGHTER_DOTENV_TEST_SENTINEL"
    (tmp_path / ".env").write_text(f"{sentinel}=loaded\n")
    monkeypatch.delenv(sentinel, raising=False)
    monkeypatch.chdir(tmp_path)
    original = sys.modules.pop("lighter_testing")
    try:
        reloaded = importlib.import_module("lighter_testing")
        assert os.environ[sentinel] == "loaded"
        assert hasattr(reloaded, "LighterClient")
    finally:
        sys.modules["lighter_testing"] = original


def test_cli_missing_key_exits_one_and_names_variable(tmp_path) -> None:
    """AC2 through the real __main__ path: no key anywhere -> exit 1, stderr names it."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "lighter_testing.api_keys",
            "--account-index",
            "1",
            "--api-key-index",
            "1",
        ],
        cwd=tmp_path,  # no .env here
        env=_clean_env(),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 1
    assert "WALLET_PRIVATE_KEY" in result.stderr
    assert result.stdout == ""


def test_shell_env_takes_precedence_over_dotenv(tmp_path) -> None:
    """AC3: python-dotenv default (override=False) keeps the shell variable authoritative."""
    (tmp_path / ".env").write_text("WALLET_PRIVATE_KEY=from_dotenv\n")
    code = "import lighter_testing, os; print(os.environ['WALLET_PRIVATE_KEY'])"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=_clean_env(WALLET_PRIVATE_KEY="from_shell"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "from_shell"
