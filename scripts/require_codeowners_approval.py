"""Human path (stage 9): require an approving review from a CODEOWNER.
No-op if present; otherwise post a reminder and fail the check.
Usage: require_codeowners_approval.py --pr 482"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh, pr_comment

pr = sys.argv[sys.argv.index("--pr")+1]
approvers = {r["user"]["login"] for r in gh("GET", f"/pulls/{pr}/reviews?per_page=100")
             if r.get("state") == "APPROVED"}
owners = set()
if os.path.exists(".github/CODEOWNERS") or os.path.exists("CODEOWNERS"):
    path = ".github/CODEOWNERS" if os.path.exists(".github/CODEOWNERS") else "CODEOWNERS"
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#"):
            owners |= {t.lstrip("@") for t in line.split()[1:] if t.startswith("@") and "/" not in t}
if owners & approvers:
    print(f"CODEOWNER approval present: {owners & approvers}")
elif not owners:
    print("WARNING: no CODEOWNERS file — ownership has no teeth (PDF constraint #2). "
          "Accepting any approval for now:" + str(approvers))
    if not approvers: raise SystemExit(1)
else:
    pr_comment(pr, f"Gate 2 requires approval from a code owner ({', '.join(sorted(owners))}). "
                   f"Current approvers: {', '.join(sorted(approvers)) or 'none'}.")
    raise SystemExit("FAIL: no CODEOWNER approval")
