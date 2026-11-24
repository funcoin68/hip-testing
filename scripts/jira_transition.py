"""Transition a Jira issue by name; optional comment. --pr N resolves the ticket
key from the PR packet comment (used by stage 9 auto path).
Usage: jira_transition.py PROJ-123 "In review" [--comment text] [--if-available]
       jira_transition.py --pr 482 --to "Done" [--comment text]

Matching: exact (case-insensitive) on the transition name; if no exact match,
a SINGLE fuzzy candidate containing a significant word of the target is used
with a warning (Jira workflows name the same move "In Review", "Start review",
"Ready for review"...). Ambiguity or no match is FATAL and lists what the
workflow actually offers — override with vars.JIRA_REVIEW_TRANSITION /
JIRA_DONE_TRANSITION. --if-available downgrades the no-candidate case to a
notice+exit 0 (re-dispatches where the ticket was already moved).

Idempotency: if the issue's current status already equals the target, the
script prints a notice and exits 0 without double-commenting.
"""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import jira, jira_adf, gh

args = [a for a in sys.argv[1:] if a != "--if-available"]
if_available = len(args) != len(sys.argv[1:])
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

# Idempotency: already at the target status -> nothing to do, no duplicate comment.
current = (jira("GET", f"/issue/{key}?fields=status")["fields"].get("status") or {}).get("name", "")
if current.strip().lower() == name.strip().lower():
    print(f"{key}: already in '{current}' — nothing to do")
    raise SystemExit(0)

ts = jira("GET", f"/issue/{key}/transitions")["transitions"]
match = next((t for t in ts if t["name"].lower() == name.lower()), None)
if match is None:
    words = [w for w in re.split(r"\s+", name.lower()) if len(w) > 2]
    cands = [t for t in ts if any(w in t["name"].lower() for w in words)]
    if len(cands) == 1:
        match = cands[0]
        print(f"::warning::no transition named '{name}' — using closest available "
              f"'{match['name']}'. Set the JIRA_REVIEW_TRANSITION / JIRA_DONE_TRANSITION "
              "repo var to silence this.")
    elif not cands and if_available:
        print(f"::notice::no transition toward '{name}' from status '{current}' "
              f"(have: {[t['name'] for t in ts]}) — likely already handled; continuing")
        raise SystemExit(0)
    else:
        raise SystemExit(f"FATAL: no transition '{name}' available from status '{current}' "
                         f"(workflow offers: {[t['name'] for t in ts]}). Set the "
                         "JIRA_REVIEW_TRANSITION / JIRA_DONE_TRANSITION repo var to one "
                         "of the listed names.")

jira("POST", f"/issue/{key}/transitions", {"transition": {"id": match["id"]}})
print(f"{key} -> {match['name']}")
if comment:
    jira("POST", f"/issue/{key}/comment", {"body": jira_adf(comment)})
