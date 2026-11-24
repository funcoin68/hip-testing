"""Squash-merge a PR (stage 9 auto path — only reachable when AUTO_MERGE_ENABLED
is true, which ships unset). Usage: squash_merge.py --pr 482 --reason '...'"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh

args = sys.argv[1:]
pr = args[args.index("--pr")+1]
reason = args[args.index("--reason")+1] if "--reason" in args else ""
r = gh("PUT", f"/pulls/{pr}/merge", {"merge_method": "squash", "commit_title": reason or None})
print(f"merged: {r.get('sha')}")
