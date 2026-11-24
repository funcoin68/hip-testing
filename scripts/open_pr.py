"""Open the implementation PR (idempotent) or check for existence.
  open_pr.py --ticket KEY --check            exit 0 + 'exists' if an open PR exists (stage-5 idempotency)
  open_pr.py --ticket KEY --base main --head agent/KEY --body-file /tmp/pr_body.md

Bug #41: the PR body now comes from the agent-written --body-file. This script
used to open every PR with a placeholder body ('modules: []'), and ci-pr-gates
fires ~4s after the 'opened' event — so the stage-6 scope guard read the empty
placeholder and hard-failed (PR #6 / CYJ-8) before any human could paste the
real body. The stage-5 output contract therefore makes the AGENT write the
full body to /tmp/pr_body.md, and we validate a non-empty 'modules:' line
BEFORE opening: a missing declaration fails stage 5 loudly here, with the
implement log still fresh — not as a red CI run minutes later."""
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh

def find_open(head):
    repo = os.environ["GITHUB_REPOSITORY"]; owner = repo.split("/")[0]
    prs = gh("GET", f"/pulls?state=open&head={owner}:{head}")
    return prs[0] if prs else None

args = sys.argv[1:]
opt = {args[i].lstrip("-"): args[i+1] for i in range(len(args)-1) if args[i].startswith("--")}
# bug #17: the parser above drops a trailing valueless flag (e.g. --check)
for _a in args:
    if _a.startswith("--") and _a.lstrip("-") not in opt:
        opt[_a.lstrip("-")] = True
head = opt.get("head", f"agent/{opt['ticket']}")
existing = find_open(head)
if "check" in opt:
    exists = "true" if existing else "false"
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        open(out, "a").write(f"exists={exists}\n")
    print(f"pr-exists={exists}" + (f" (#{existing['number']})" if existing else ""))
    raise SystemExit(0)
if existing:
    print(f"PR already open: #{existing['number']}"); raise SystemExit(0)

body_file = opt.get("body-file")
if not body_file:
    raise SystemExit("FATAL: --body-file is required (bug #41). The implementing agent "
                     "writes the PR body (with a real 'modules:' list) to that file; "
                     "the old placeholder body ('modules: []') fails the stage-6 scope "
                     "guard by construction, so opening without it is never right.")
try:
    body = open(body_file, encoding="utf-8").read()
except OSError as e:
    raise SystemExit(f"FATAL: --body-file {body_file} unreadable: {e}. The implement "
                     "step's output contract requires the agent to write the PR body "
                     "there — check /tmp/implement.log for what the agent actually did.")

# Mirror check_scope.py's declaration check: never open a PR the scope guard
# is guaranteed to reject.
m = re.search(r"^modules:\s*(.+)$", body, re.M)
if not m or not m.group(1).strip() or m.group(1).strip() == "[]":
    raise SystemExit(f"FATAL: agent PR body in {body_file} has no usable 'modules:' "
                     "declaration (missing, empty, or '[]'). Fix the agent's output — "
                     "opening this PR would fail the stage-6 scope guard on arrival.")

pr = gh("POST", "/pulls", {"title": f"[{opt['ticket']}] agent implementation",
                           "head": head, "base": opt.get("base", "main"), "body": body})
print(f"opened PR #{pr['number']}: {pr['html_url']}")
