"""Fetch ONE pinned proposal comment from Jira by comment id.
Usage: fetch_proposal.py PROJ-123 100231 > proposal.md"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import jira

def collect_text(node):
    if isinstance(node, dict):
        if node.get("type") == "text": return node.get("text", "")
        return "\n".join(filter(None, (collect_text(c) for c in node.get("content", []))))
    return ""

key, cid = sys.argv[1], sys.argv[2]
c = jira("GET", f"/issue/{key}/comment/{cid}")
print(collect_text(c.get("body") or {}))
