"""Stage 10 encode: diff the 'implement' commits against 'address review' commits
— what reviewers made the agent change is the corrections corpus feed.
Usage: extract_corrections.py --pr 482   (writes /tmp/corrections-PR.md)"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh

pr = sys.argv[sys.argv.index("--pr")+1]
commits = gh("GET", f"/pulls/{pr}/commits?per_page=100")
impl = [c for c in commits if "address review" not in c["commit"]["message"]]
fix = [c for c in commits if "address review" in c["commit"]["message"]]
lines = [f"# Correction candidates from PR #{pr}", ""]
if not fix:
    lines.append("No review-induced changes — nothing to distill this time.")
else:
    lines.append(f"{len(fix)} fix commit(s) vs {len(impl)} implement commit(s). "
                 "Review each fix diff; distill recurring patterns below.")
    for c in fix:
        lines.append(f"\n## {c['sha'][:8]} — {c['commit']['message'].splitlines()[0]}")
open(f"/tmp/corrections-{pr}.md", "w").write("\n".join(lines))
print(f"candidates -> /tmp/corrections-{pr}.md")
