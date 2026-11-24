"""Stage 8b: AC-by-AC verification against the staging URL. For each AC, an
optional deterministic check command (env AC_CHECK_<n>) runs; otherwise the AC
is marked for the kimi-for-coding exploratory pass (workflow runs claude before this).
Output: one evidence markdown per AC in --out dir + an index.
Usage: verify_acceptance_criteria.py --ticket KEY --env-url URL --out /tmp/evidence/"""
import sys, os, json, subprocess, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import jira
from fetch_ticket import extract_ac

args = sys.argv[1:]
opt = {args[i].lstrip("-"): args[i+1] for i in range(len(args)-1) if args[i].startswith("--")}
# bug #17: the parser above drops a trailing valueless flag (e.g. --check)
for _a in args:
    if _a.startswith("--") and _a.lstrip("-") not in opt:
        opt[_a.lstrip("-")] = True
issue = jira("GET", f"/issue/{opt['ticket']}?fields=summary,description")
ac = extract_ac(issue["fields"].get("description"))
os.makedirs(opt["out"], exist_ok=True)
results = []
for i, a in enumerate(ac, 1):
    cmd = os.environ.get(f"AC_CHECK_{i}")
    status, detail = "agent-verified", "exploratory pass (see QA agent transcript)"
    if cmd:
        r = subprocess.run(cmd.replace("{ENV_URL}", opt["env-url"]),
                           shell=True, capture_output=True, text=True, timeout=120)
        status = "pass" if r.returncode == 0 else "FAIL"
        detail = f"```\n$ {cmd}\nexit {r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-500:]}\n```"
    fn = os.path.join(opt["out"], f"ac-{i}.md")
    open(fn, "w").write(f"# AC {i}: {a}\n\nStatus: **{status}**\nEnv: {opt['env-url']}\n\n{detail}\n")
    results.append((i, a, status))
idx = ["# AC verification index", f"Ticket: {opt['ticket']}", f"Env: {opt['env-url']}",
       f"Date: {datetime.date.today()}", ""]
idx += [f"- AC{i}: **{s}** — {a}" for i, a, s in results]
open(os.path.join(opt["out"], "index.md"), "w").write("\n".join(idx))
fails = [r for r in results if r[2] == "FAIL"]
print(f"{len(results)-len(fails)}/{len(results)} AC passed")
raise SystemExit(1 if fails else 0)
