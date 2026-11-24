"""Scope guard: agent may only touch modules its PR body declared (stage 5
output contract: 'modules:' list). Stray paths fail the gate.
Usage: check_scope.py --pr 482 [--base origin/main]"""
import sys, os, re, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import get_pr

args = sys.argv[1:]
pr_no = args[args.index("--pr")+1]
base = args[args.index("--base")+1] if "--base" in args else "origin/main"
body = get_pr(pr_no).get("body") or ""
m = re.search(r"^modules:\s*(.+)$", body, re.M)
if not m or not m.group(1).strip() or m.group(1).strip() == "[]":
    raise SystemExit("FAIL: PR body has no 'modules:' declaration — the scope guard "
                     "cannot verify scope. Stage-5 contract requires it.")
# Bug #27: strip whitespace (incl. \r from CRLF edits) BEFORE stripping brackets —
# '...tsconfig.json]\r' otherwise keeps the trailing ']' and every real module
# with a trailing bracket gets flagged as scope creep. Validate entries too:
# a path char mismatch means the line is mangled, not that the diff is.
raw = m.group(1).strip().strip("[]")
declared = [x.strip().strip('"\'') for x in raw.split(",") if x.strip()]
bad = [d for d in declared if not re.fullmatch(r"[A-Za-z0-9._/\-]+", d)]
if bad:
    raise SystemExit(f"FAIL: malformed entries in the PR-body modules: line: {bad} — "
                     "fix the line (valid chars: letters, digits, . _ / -) and re-run. "
                     "Not scope creep: the declaration itself is unreadable.")
out = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD"],
                     capture_output=True, text=True, check=True).stdout
changed = [l for l in out.strip().splitlines() if l]
ALWAYS_OK = re.compile(r"^(\.github/workflows/|docs/agent/|CLAUDE\.md$|repo-registry\.yaml$)")
strays = [p for p in changed
          if not any(d and p.startswith(d) for d in declared) and not ALWAYS_OK.match(p)]
print(f"declared modules: {declared}\nchanged files: {len(changed)}")
if strays:
    print("FAIL: scope creep — files outside declared modules:\n  " + "\n  ".join(strays))
    print("remediation (human gate): review the stray diff, then either")
    print("  (a) edit the PR body and add the path to 'modules:' — the guard")
    print("      prefix-matches, so a trailing-slash directory covers its files —")
    print("      then re-run this job (the body is read live, no push needed); or")
    print("  (b) revert the file on the branch if the change is not legitimate.")
    raise SystemExit(1)
print("scope OK")
