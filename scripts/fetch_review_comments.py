"""Fix-loop context: the swarm's unresolved critical/comment findings ONLY —
still clean-room (no implementation reasoning is re-injected, PDF stage 7).
Usage: fetch_review_comments.py --pr 482 > context.md"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh

pr = sys.argv[sys.argv.index("--pr")+1]
reviews = gh("GET", f"/pulls/{pr}/reviews?per_page=100")
comments = gh("GET", f"/pulls/{pr}/comments?per_page=100")
print("# Review findings to address (fix loop, comments-only context)")
print("\n## Verdict blocks")
for r in reviews:
    if "---REVIEW-START---" in (r.get("body") or ""):
        print("```"); print(r["body"][:800]); print("```")
print("\n## Inline findings")
for c in comments:
    print(f"- `{c['path']}:{c.get('line','?')}` {c['body']}")
print("\nAddress EACH finding. Tiny budget. Scope is only the flagged items. "
      "Run tests + lint before pushing.")
