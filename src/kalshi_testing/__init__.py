"""Kalshi demo-environment experiments.

Everything here targets the Kalshi demo trade API with fake money only. API
credentials come from the environment or a gitignored local ``.env``; private
keys never live in this repo.
"""

from pathlib import Path

from dotenv import load_dotenv

# Load .env from the current working directory at import time. The path is
# resolved explicitly against cwd (NOT load_dotenv()'s default find_dotenv,
# which walks up from this installed file and would miss the caller's cwd).
# override=False (the default) keeps real environment variables authoritative,
# so CI secrets are never shadowed by a stray .env. Any module that reads
# os.environ at import time must come after this call.
load_dotenv(dotenv_path=Path.cwd() / ".env")
