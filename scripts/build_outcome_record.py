"""Stage 10 capture: one outcome record per merged PR (raw material for metrics
and the corrections feed). Usage: build_outcome_record.py --pr 482 --out file.json"""
import sys, os, json, re, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import get_pr, pr_labels, gh

args = sys.argv[1:]
pr_no = args[args.index("--pr")+1]; out = args[args.index("--out")+1]
pr = get_pr(pr_no); labels = pr_labels(pr_no)
reviews = gh("GET", f"/pulls/{pr_no}/reviews?per_page=100")
created = datetime.datetime.fromisoformat(pr["created_at"].replace("Z", "+00:00"))
merged = datetime.datetime.fromisoformat(pr["merged_at"].replace("Z", "+00:00"))
rec = {
    "pr": int(pr_no), "ticket": (re.match(r"agent/([A-Za-z]+-\d+)", pr["head"]["ref"]) or [None, None])[1],
    "merged_at": pr["merged_at"],
    "cycle_time_minutes": round((merged - created).total_seconds() / 60, 1),
    "fix_rounds": len([l for l in labels if re.fullmatch(r"fix-round-\d+", l)]),
    "verdicts": [b for r in reviews for b in
                 re.findall(r"---REVIEW-START---.*?---REVIEW-END---", r.get("body") or "", re.S)],
    "diff_lines": pr.get("additions", 0) + pr.get("deletions", 0),
    "approvers": [r["user"]["login"] for r in reviews if r.get("state") == "APPROVED"],
    "labels": sorted(labels),
}
os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
json.dump(rec, open(out, "w"), indent=2)
print(f"outcome record -> {out}")
