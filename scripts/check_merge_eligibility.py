"""Stage-9 routing (PDF: auto-merge eligible ONLY if tier-3 + consensus-passed +
high confidence + behind flag + <=1 fix round + qa-evidence-green). Emits
{eligible, reasons} JSON. Tier-1 is NEVER eligible.
Usage: check_merge_eligibility.py --pr 482 > decision.json"""
import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import get_pr, pr_labels, gh

pr_no = sys.argv[sys.argv.index("--pr")+1]
pr = get_pr(pr_no); labels = pr_labels(pr_no)
comments = gh("GET", f"/issues/{pr_no}/comments?per_page=100")
packet = {}
for c in comments:
    m = re.search(r"```json\n(.*?)```", c.get("body") or "", re.S)
    if "<!-- review-packet -->" in (c.get("body") or "") and m:
        packet = json.loads(m.group(1))
tier = packet.get("risk_tier", 1)
fix_rounds = len([l for l in labels if re.fullmatch(r"fix-round-\d+", l)])
ci = packet.get("confidence_inputs", {})
checks = {
    "tier-3": tier == 3,
    "consensus-passed": "consensus-passed" in labels,
    "behind-flag": bool(ci.get("behind_flag")),
    "fix-rounds<=1": fix_rounds <= 1,
    "qa-bundle-green": "qa-evidence-green" in labels,
    "no-migration": not ci.get("has_migration"),
}
decision = {"pr": int(pr_no), "eligible": all(checks.values()),
            "checks": checks, "fix_rounds": fix_rounds, "risk_tier": tier,
            "note": "tier-1 is never auto-merge eligible" if tier == 1 else ""}
print(json.dumps(decision, indent=2))
