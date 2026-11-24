"""Test that importing lighter_testing loads .env from the current working directory."""

import importlib
import os
import sys

import pytest

import lighter_testing  # noqa: F401  (ensures the original module object is restorable)


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
