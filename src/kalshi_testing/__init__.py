"""Kalshi demo-environment experiments.

Everything here targets the Kalshi demo trade API with fake money only. API
credentials come from the environment or a gitignored local ``.env``; private
keys never live in this repo.
"""

from pathlib import Path

from dotenv import load_dotenv

# Load .env from the repo root ONLY. A .env planted in an untrusted cwd is
# never loaded: KALSHI_WS_URL from such a file could redirect the signed
# handshake headers to an attacker host. override=False (the default) keeps
# real environment variables authoritative, so CI secrets are never shadowed
# by a stray .env. Any module that reads os.environ at import time must come
# after this call.
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(dotenv_path=_REPO_ROOT / ".env")
