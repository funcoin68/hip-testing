"""Transition a Jira issue by name; optional comment. --pr N resolves the ticket
key from the PR packet comment (used by stage 9 auto path).
Usage: jira_transition.py PROJ-123 "In review" [--comment text]
       jira_transition.py --pr 482 --to "Done" [--comment text]"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import jira, jira_adf, gh

args = sys.argv[1:]
if args[0] == "--pr":
    pr = args[1]
    comments = gh("GET", f"/issues/{pr}/comments?per_page=100")
    key = None
    for c in comments:
        m = re.search(r'"ticket_key":\s*"([A-Z]+-\d+)"', c.get("body") or "")
        if m: key = m.group(1); break
    if not key: raise SystemExit("FATAL: no packet comment with ticket_key on PR")
    to_i = args.index("--to"); name = args[to_i+1]
else:
    key, name = args[0], args[1]
comment = None
if "--comment" in args:
    comment = args[args.index("--comment")+1]
ts = jira("GET", f"/issue/{key}/transitions")["transitions"]
for t in ts:
    if t["name"].lower() == name.lower():
        jira("POST", f"/issue/{key}/transitions", {"transition": {"id": t["id"]}})
        print(f"{key} -> {name}"); break
else:
    raise SystemExit(f"FATAL: no transition '{name}' available from current status "
                     f"(have: {[t['name'] for t in ts]})")
if comment:
    jira("POST", f"/issue/{key}/comment", {"body": jira_adf(comment)})
