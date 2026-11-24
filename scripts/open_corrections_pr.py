"""Open a DRAFT PR appending correction candidates to docs/agent/CORRECTIONS.md.
Human merges — nothing enters agent memory without a human merge (PDF principle).
Usage: open_corrections_pr.py --from-pr 482"""
import sys, os, base64
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh

pr = sys.argv[sys.argv.index("--from-pr")+1]
cand_file = f"/tmp/corrections-{pr}.md"
if not os.path.exists(cand_file):
    print("no candidates file — run extract_corrections.py first"); raise SystemExit(0)
cand = open(cand_file).read()
# bug #42 loop guard: merging a corrections PR re-triggers stage 10 on THAT PR.
# Its commits never contain "address review", so extract_corrections.py writes
# "No review-induced changes" — without this guard we'd open yet another draft
# PR, whose merge re-triggers stage 10 again: an infinite draft-PR loop.
if "No review-induced changes" in cand:
    print("no review-induced changes — nothing to admit; not opening an empty corrections PR")
    raise SystemExit(0)
path = "docs/agent/CORRECTIONS.md"
try:
    cur = gh("GET", f"/contents/{path}")
    content = base64.b64decode(cur["content"]).decode(); sha = cur["sha"]
except SystemExit:
    content = "# CORRECTIONS.md — prior-engineer memory (human-curated)\n"; sha = None
branch = f"corrections/pr-{pr}"
main_sha = gh("GET", "/git/ref/heads/main")["object"]["sha"]
try: gh("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": main_sha})
except SystemExit: pass
body = {"message": f"chore: correction candidates from PR #{pr}",
        "content": base64.b64encode((content + "\n\n" + cand).encode()).decode(),
        "branch": branch}
if sha: body["sha"] = sha
gh("PUT", f"/contents/{path}", body)
r = gh("POST", "/pulls", {"title": f"Correction candidates from PR #{pr} (DRAFT — human curates)",
                          "head": branch, "base": "main", "draft": True,
                          "body": "Distilled review corrections. Merge to admit into agent memory; "
                                  "edit/prune first. Nothing enters memory without this human merge."})
print(f"draft corrections PR: #{r['number']}")
