"""Risk-specific checks (stage 8b): migration dry-run note for data changes,
authz matrix reminder for permission changes. Appends evidence files.
Usage: risk_checks.py --pr 482 --out /tmp/evidence/"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh

args = sys.argv[1:]
pr = args[args.index("--pr")+1]; out = args[args.index("--out")+1]
os.makedirs(out, exist_ok=True)
files = [f["filename"] for f in gh("GET", f"/pulls/{pr}/files?per_page=100")]
notes = []
if any("migration" in f.lower() or f.endswith(".sql") for f in files):
    notes.append("# Migration dry-run\nData change detected. Attach dry-run output here; "
                 "rollback path must exist before gate 2.")
if any(k in f.lower() for f in files for k in ("auth", "permission", "policy", "acl")):
    notes.append("# Authz matrix\nPermission-relevant change. Verify each new/changed "
                 "endpoint row: role x action = allow/deny, tested both ways.")
if not notes:
    notes.append("# Risk checks\nNo migration/auth surface detected in diff — nothing extra required.")
for i, n in enumerate(notes):
    open(os.path.join(out, f"risk-{i}.md"), "w").write(n)
print(f"{len(notes)} risk note(s) written")
