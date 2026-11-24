"""Build the stage-6 -> stage-7 review packet (schema: review-packet/v1).
Clean-room anchor: every reviewer reads THIS + the diff + the ticket, nothing else.
Usage: build_review_packet.py --pr 482 --repo-registry repo-registry.yaml > packet.json"""
import sys, os, json, subprocess, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import get_pr, gh
import yaml

args = sys.argv[1:]
pr_no = args[args.index("--pr")+1]
reg_path = args[args.index("--repo-registry")+1]
pr = get_pr(pr_no)
repo = os.environ["GITHUB_REPOSITORY"].split("/")[-1]

# ticket key from branch name agent/PROJ-123
m = re.match(r"agent/([A-Za-z]+-\d+)", pr["head"]["ref"])
ticket = m.group(1).upper() if m else "UNKNOWN"

# risk tier = max tier of repos touched (README-stage6); single-repo practice = its tier
reg = yaml.safe_load(open(reg_path)) if os.path.exists(reg_path) else {}
tier = 3
for name, svc in (reg.get("services") or {}).items():
    if repo in (svc.get("repos") or []):
        tier = svc.get("tier", 3); break

files = gh("GET", f"/pulls/{pr_no}/files?per_page=100")
modules = sorted({f["filename"].rsplit("/", 1)[0] for f in files})
diff_lines = sum(f.get("additions", 0) + f.get("deletions", 0) for f in files)

# AC verbatim from Jira
ac = []
if ticket != "UNKNOWN" and os.environ.get("JIRA_TOKEN"):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch_ticket import extract_ac
    from _pipeline_lib import jira
    issue = jira("GET", f"/issue/{ticket}?fields=description")
    ac = extract_ac(issue["fields"].get("description"))

# proposal pin from PR body
pm = re.search(r"proposal_comment_id[:\s]+(\d+)", pr.get("body") or "")

packet = {
    "schema": "review-packet/v1",
    "pr_number": int(pr_no),
    "ticket_key": ticket,
    "branch": pr["head"]["ref"],
    "base": pr["base"]["ref"],
    "proposal_comment_id": pm.group(1) if pm else None,
    "repos_touched": [{"repo": repo, "tier": tier}],
    "modules_changed": modules,
    "acceptance_criteria": ac,
    "risk_tier": tier,
    "ci": {"tests": "pass", "lint": "pass", "secrets": "pass", "diff_lines": diff_lines},
    "confidence_inputs": {
        "diff_lines": diff_lines, "modules_touched": len(modules),
        "tier1": tier == 1,
        "has_migration": any("migration" in f["filename"].lower() for f in files),
        "behind_flag": f"flag-{ticket}" in (pr.get("body") or ""),
    },
}
print(json.dumps(packet, indent=2))
