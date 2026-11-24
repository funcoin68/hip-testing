"""Local testnet private-key generator — HIP testnet, fake money only.

Run as ``python -m hip_testing.keys``. Prints a fresh key with a testnet
warning; with ``--env-file [PATH]`` also appends it as ``HIP_PRIVATE_KEY=``
(refusing to overwrite an existing key). Never use with real funds.
"""

import argparse
import secrets
import sys
from pathlib import Path

ENV_VAR = "HIP_PRIVATE_KEY"
WARNING = "TESTNET ONLY — fake money. Never commit this key, never use with real funds."


def generate_private_key() -> str:
    """Return a fresh 32-byte secp256k1 private key as 0x-prefixed hex."""
    return "0x" + secrets.token_bytes(32).hex()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        nargs="?",
        const=Path(".env"),
        default=None,
        type=Path,
        metavar="PATH",
        help="also append the key to PATH as HIP_PRIVATE_KEY=<key> (default PATH: .env)",
    )
    args = parser.parse_args(argv)

    env_path: Path | None = args.env_file
    existing = ""
    if env_path is not None and env_path.exists():
        try:
            existing = env_path.read_text()
        except OSError as exc:
            print(f"error: cannot read {env_path}: {exc}", file=sys.stderr)
            return 1
        if f"{ENV_VAR}=" in existing:
            print(
                f"error: {env_path} already contains {ENV_VAR}= — refusing to overwrite",
                file=sys.stderr,
            )
            return 1

    key = generate_private_key()
    print(WARNING)
    print(key)

    if env_path is None:
        return 0
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    try:
        with env_path.open("a") as f:
            f.write(f"{prefix}{ENV_VAR}={key}\n")
    except OSError as exc:
        print(f"error: cannot write {env_path}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
