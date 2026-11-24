"""Fetch a Jira ticket (+comments with --with-comments) as normalized JSON.
Usage: fetch_ticket.py PROJ-123 [--with-comments] > ticket.json"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import jira

def main():
    key = sys.argv[1]
    with_comments = "--with-comments" in sys.argv
    fields = "summary,description,status,labels,components,created,updated,attachment"
    issue = jira("GET", f"/issue/{key}?fields={fields}")
    f = issue["fields"]
    out = {
        "key": key,
        "summary": f.get("summary"),
        "status": (f.get("status") or {}).get("name"),
        "labels": f.get("labels", []),
        "components": [c["name"] for c in f.get("components", [])],
        # description is ADF; callers needing plain text should use the
        # acceptance-criteria section convention (## Acceptance criteria)
        "description_adf": f.get("description"),
        "acceptance_criteria": extract_ac(f.get("description")),
        # bug #34: attachment metadata so fetch_attachments.py can download the
        # files server-side — agents hold no Jira creds by design (bug #19).
        "attachments": [{"id": a.get("id"), "filename": a.get("filename"),
                         "mimeType": a.get("mimeType"), "size": a.get("size")}
                        for a in f.get("attachment", [])],
    }
    if with_comments:
        cs = jira("GET", f"/issue/{key}/comment?maxResults=100&orderBy=created")
        out["comments"] = [{"id": c["id"], "author": (c.get("author") or {}).get("displayName"),
                            "created": c.get("created"), "body_adf": c.get("body")}
                           for c in cs.get("comments", [])]
    import json; print(json.dumps(out, indent=2))

def extract_ac(adf):
    """Pull the '## Acceptance criteria' section out of the ADF description.
    AC are bullet items; verbatim text is what stage 6 puts in the packet."""
    if not adf: return []
    lines, in_ac = [], False
    def walk(node):
        nonlocal in_ac
        if not isinstance(node, dict): return
        if node.get("type") == "heading":
            text = "".join(c.get("text", "") for c in node.get("content", []))
            in_ac = "acceptance criteria" in text.lower()
            return
        if in_ac and node.get("type") in ("bulletList", "orderedList"):
            for item in node.get("content", []):
                txt = collect_text(item)
                if txt: lines.append(txt)
            return
        for c in node.get("content", []): walk(c)
    def collect_text(node):
        if isinstance(node, dict):
            if node.get("type") == "text": return node.get("text", "")
            return "".join(collect_text(c) for c in node.get("content", []))
        return ""
    walk(adf)
    return lines

if __name__ == "__main__":
    main()
