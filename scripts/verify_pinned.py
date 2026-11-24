"""Stage-5 hard gate: the fetched proposal must still hash to the value pinned
at approval time. Mismatch (edited after approval) -> halt, re-route to gate 1.
Usage: verify_pinned.py PROJ-123 proposal.md   (exit 1 on mismatch/missing)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import jira, sha256_text, canon_text

key, path = sys.argv[1], sys.argv[2]
try:
    prop = jira("GET", f"/issue/{key}/properties/last-proposal")
except SystemExit:
    raise SystemExit("FATAL: no pinned proposal (issue property last-proposal missing). "
                     "Ticket must go through gate 1 first.")
actual = sha256_text(canon_text(open(path).read()))
if actual != prop["value"]["hash"]:
    raise SystemExit("FATAL: proposal hash mismatch — the approved proposal was edited. "
                     "Halting; route back to gate 1 for re-approval.")
print(f"pinned proposal verified ({actual[:23]}...)")
