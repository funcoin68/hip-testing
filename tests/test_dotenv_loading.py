"""Tests for lighter_testing's .env loading (cwd first, repo-root fallback)."""

import contextlib
import importlib
import os
import subprocess
import sys
from pathlib import Path

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


def _repo_root() -> Path:
    for directory in Path(lighter_testing.__file__).resolve().parents:
        if (directory / "pyproject.toml").exists():
            return directory
    pytest.skip("repo root (pyproject.toml) not found above lighter_testing")


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


def test_fallback_loads_repo_root_dotenv(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit: the fallback walk climbs from the package dir to the pyproject.toml root."""
    sentinel = "LIGHTER_FALLBACK_SENTINEL"
    repo = tmp_path / "repo"
    package_dir = repo / "src" / "lighter_testing"
    package_dir.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (repo / ".env").write_text(f"{sentinel}=from_repo_root\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # cwd has no .env
    monkeypatch.delenv(sentinel, raising=False)
    try:
        lighter_testing._load_dotenv(search_start=package_dir)
        assert os.environ[sentinel] == "from_repo_root"
    finally:
        os.environ.pop(sentinel, None)


def test_fallback_stops_at_pyproject_boundary(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit: a .env ABOVE the pyproject.toml marker is never loaded."""
    sentinel = "LIGHTER_BOUNDARY_SENTINEL"
    repo = tmp_path / "repo"
    package_dir = repo / "src" / "lighter_testing"
    package_dir.mkdir(parents=True)
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / ".env").write_text(f"{sentinel}=must_not_load\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # cwd has no .env
    monkeypatch.delenv(sentinel, raising=False)
    try:
        lighter_testing._load_dotenv(search_start=package_dir)
        assert sentinel not in os.environ
    finally:
        os.environ.pop(sentinel, None)


def test_import_from_other_cwd_loads_repo_root_dotenv(tmp_path) -> None:
    """AC1, PyCharm simulation: cwd != repo root still finds the repo-root .env."""
    sentinel = "LIGHTER_PYCHARM_SIM_SENTINEL"
    env = _clean_env()
    env.pop(sentinel, None)
    code = f"import lighter_testing, os; print(os.environ.get('{sentinel}', ''))"
    with _repo_root_dotenv(f"{sentinel}=from_repo_root\n"):
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=tmp_path,  # PyCharm debug working directory: not the repo root
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 0
    assert result.stdout.strip() == "from_repo_root"


def test_cwd_dotenv_beats_repo_root_dotenv(tmp_path) -> None:
    """Precedence regression: with both files present, the cwd .env wins."""
    sentinel = "LIGHTER_PRECEDENCE_SENTINEL"
    (tmp_path / ".env").write_text(f"{sentinel}=from_cwd\n")
    env = _clean_env()
    env.pop(sentinel, None)
    with _repo_root_dotenv(f"{sentinel}=from_repo_root\n"):
        result = subprocess.run(
            [sys.executable, "-c", f"import lighter_testing, os; print(os.environ['{sentinel}'])"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert result.returncode == 0
    assert result.stdout.strip() == "from_cwd"
