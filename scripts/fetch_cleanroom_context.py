"""Stage-7 clean-room context: ticket spec + packet + diff ONLY.
Excluded BY CONSTRUCTION: proposal comment, agent reasoning, other reviewers.
Usage: fetch_cleanroom_context.py --pr 482 --ticket PROJ-123 > context.md"""
import sys, os, json, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh, jira
from fetch_ticket import extract_ac

args = sys.argv[1:]
pr_no = args[args.index("--pr")+1]; ticket = args[args.index("--ticket")+1]

comments = gh("GET", f"/issues/{pr_no}/comments?per_page=100")
packet = None
for c in comments:
    if "<!-- review-packet -->" in (c.get("body") or ""):
        m = re.search(r"```json\n(.*?)```", c["body"], re.S)
        packet = json.loads(m.group(1)); break
if not packet: raise SystemExit("FATAL: no review packet comment — stage 6 must run first")

issue = jira("GET", f"/issue/{ticket}?fields=summary,description")
ac = extract_ac(issue["fields"].get("description"))

# diff via API (not git — reviewers don't need the repo checked out)
diff = gh("GET", f"/pulls/{pr_no}", None)  # placeholder to satisfy linter
import urllib.request
req = urllib.request.Request(
    f"https://api.github.com/repos/{os.environ['GITHUB_REPOSITORY']}/pulls/{pr_no}",
    headers={"Authorization": f"Bearer {os.environ['GH_TOKEN']}",
             "Accept": "application/vnd.github.v3.diff"})
diff_text = urllib.request.urlopen(req, timeout=30).read().decode()

print(f"# Ticket {ticket}: {issue['fields'].get('summary')}")
print("\n## Acceptance criteria")
for a in ac: print(f"- {a}")
print("\n## Review packet (from stage 6)\n```json")
print(json.dumps(packet, indent=2)); print("```")
print("\n## Diff under review\n```diff")
print(diff_text[:60000]); print("```")
print("\n## Standards rubric (repo-owned rules — NOT author reasoning)")
print("Apply these when judging the diff. If the diff violates a CORRECTIONS.md")
print("entry, that is a finding even when no checklist item catches it.")
for _p, _label in [("docs/agent/CORRECTIONS.md", "CORRECTIONS.md — prior-engineer memory"),
                   ("CLAUDE.md", "CLAUDE.md — repo rules + project specifics")]:
    if os.path.exists(_p):
        print(f"\n### {_label}\n")
        print(open(_p).read())

print("\nREMINDER: the rubric above is the ONLY repo context you get. You receive "
      "NO implementation plan and NO author reasoning. Judge the code as it is. "
      "End with your verdict block and comments JSON.")
