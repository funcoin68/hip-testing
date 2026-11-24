"""Merge ticket + onboarding pack + CORRECTIONS.md + repo registry into one
context doc for the agent. Enforces the 40k-char pack budget (PDF: pack <40k
chars, staleness 14 days warning).
Usage: assemble_context.py ticket.json [--with-qa] > context.md"""
import sys, os, json, re, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BUDGET = 40000

def flatten_adf(node):
    """bug #43: Jira comment bodies are ADF, not plain text. Walk the tree and
    return plain text, one newline per block node (paragraph, list item...)."""
    BLOCK = ("paragraph", "heading", "listItem", "blockquote", "codeBlock")
    if isinstance(node, list):
        return "".join(flatten_adf(c) for c in node)
    if not isinstance(node, dict):
        return ""
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "hardBreak":
        return "\n"
    inner = "".join(flatten_adf(c) for c in node.get("content", []))
    return inner + "\n" if node.get("type") in BLOCK else inner

def read(path, label):
    if not os.path.exists(path):
        return f"## {label}\n(not present in repo yet — first-run repo)\n"
    return f"## {label}\n" + open(path).read()

def main():
    ticket = json.load(open(sys.argv[1]))
    with_qa = "--with-qa" in sys.argv
    parts = [f"# Ticket {ticket['key']}: {ticket.get('summary')}",
             f"Status: {ticket.get('status')}  Labels: {', '.join(ticket.get('labels', []))}"]
    ac = ticket.get("acceptance_criteria") or []
    parts.append("## Acceptance criteria (verbatim)")
    parts += [f"- {a}" for a in ac] if ac else ["(NONE — missing AC is a stage-1 rejection; escalate, do not guess)"]
    # staleness warning on the pack (PDF: 14 days)
    pack = "docs/agent/onboarding-pack.md"
    if os.path.exists(pack):
        pack_text = open(pack).read()
        if "{repo_name}" in pack_text or re.search(r"^\{[a-z_]+\}$", pack_text, re.M):
            parts.append("> WARNING: onboarding pack is an UNFILLED TEMPLATE — "
                         "it contains placeholders, not knowledge. Run "
                         "scripts/build_context_pack.py (nightly workflow keeps it fresh). "
                         "Do NOT trust its section content.")
        m = re.search(r"generated: ([0-9-]+)", pack_text)
        if m:
            age = (datetime.date.today() - datetime.date.fromisoformat(m.group(1))).days
            if age > 14:
                parts.append(f"> WARNING: onboarding pack is {age} days old (>14). "
                             "Distrust 'recent decisions'; re-derive from git log.")
    # bug #34: design docs etc. attached to the ticket — fetched server-side
    # by fetch_attachments.py (agents hold no Jira creds). Placed BEFORE the
    # pack so the 40k budget truncates the pack tail, not the ticket's docs.
    att_index = os.path.join(os.environ.get("ATTACHMENTS_DIR", "/tmp/attachments"), "index.md")
    if os.path.exists(att_index):
        parts.append("## Ticket attachments (fetched at pipeline runtime)\n"
                     + open(att_index).read())
    parts.append(read(pack, "Onboarding pack"))
    parts.append(read("docs/agent/CORRECTIONS.md", "CORRECTIONS.md (prior-engineer memory)"))
    if os.path.exists("repo-registry.yaml"):
        parts.append(read("repo-registry.yaml", "Repo registry (scope + risk tiers)"))
    if with_qa and ticket.get("comments"):
        parts.append("## Q&A transcript since proposal")
        for c in ticket["comments"]:
            # bug #43: fetch_ticket.py stores comment text under body_adf (ADF
            # dict); the old line emitted only author/date/id, so the agent
            # never saw the human's actual question.
            body = flatten_adf(c.get("body_adf")).strip()
            if not body:
                continue  # empty / image-only comment: nothing to answer
            parts.append(f"- **{c['author']}** ({c['created']}): {body}")
    out = "\n\n".join(parts)
    if len(out) > BUDGET:
        out = out[:BUDGET] + "\n\n[TRUNCATED at 40k-char pack budget — narrow the ticket scope]"
    print(out)

if __name__ == "__main__":
    main()
