"""Tests for hip_testing.keys — local testnet private-key generator."""

import re
from pathlib import Path

from hip_testing.keys import generate_private_key, main

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_key_format() -> None:
    key = generate_private_key()
    assert key.startswith("0x")
    assert len(key) == 66  # "0x" + 64 hex chars
    assert len(bytes.fromhex(key[2:])) == 32  # 32-byte secp256k1 key


def test_keys_are_unique() -> None:
    """Two successive calls differ — the CSPRNG is actually invoked."""
    assert generate_private_key() != generate_private_key()


def test_default_prints_key_and_warning(capsys) -> None:
    assert main([]) == 0
    out = capsys.readouterr().out
    assert "TESTNET ONLY" in out
    assert re.search(r"^0x[0-9a-f]{64}$", out, re.MULTILINE)


def test_env_file_write(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    assert main(["--env-file", str(env_file)]) == 0
    content = env_file.read_text()
    assert re.search(r"^HIP_PRIVATE_KEY=0x[0-9a-f]{64}$", content, re.MULTILINE)


def test_env_file_refuses_overwrite(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    assert main(["--env-file", str(env_file)]) == 0
    before = env_file.read_bytes()
    assert main(["--env-file", str(env_file)]) != 0
    assert env_file.read_bytes() == before  # byte-identical, no silent overwrite


def test_default_env_path_is_gitignored() -> None:
    """The default --env-file target (.env) must never be committable."""
    gitignore_lines = (REPO_ROOT / ".gitignore").read_text().splitlines()
    assert ".env" in gitignore_lines
