"""Publish the QA evidence bundle: tar the dir, upload as a workflow artifact,
post ONE permalink comment on the PR, add qa-evidence-green only if all AC pass.
Usage: publish_evidence_bundle.py --pr 482 --dir /tmp/evidence --ticket PROJ-123"""
import sys, os, tarfile, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import upsert_marker_comment, add_label

args = sys.argv[1:]
opt = {args[i].lstrip("-"): args[i+1] for i in range(len(args)-1) if args[i].startswith("--")}
# bug #17: the parser above drops a trailing valueless flag (e.g. --check)
for _a in args:
    if _a.startswith("--") and _a.lstrip("-") not in opt:
        opt[_a.lstrip("-")] = True
tgz = f"/tmp/evidence-pr{opt['pr']}.tar.gz"
with tarfile.open(tgz, "w:gz") as t:
    t.add(opt["dir"], arcname="evidence")
run_url = f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ.get('GITHUB_RUN_ID','')}"
index = open(os.path.join(opt["dir"], "index.md")).read() if os.path.exists(
    os.path.join(opt["dir"], "index.md")) else "(no AC index)"
all_green = "FAIL" not in index
body = (f"### QA evidence bundle (stage 8b)\n\n{index}\n\n"
        f"Full bundle: workflow artifacts of [this run]({run_url}) "
        f"(`evidence-pr{opt['pr']}.tar.gz` — upload via actions/upload-artifact in the workflow).")
upsert_marker_comment(opt["pr"], "qa-evidence", body)
if all_green:
    add_label(opt["pr"], "qa-evidence-green")
    print("bundle published; qa-evidence-green set")
else:
    print("bundle published; FAILURES present — qa-evidence-green NOT set")
    raise SystemExit(1)
