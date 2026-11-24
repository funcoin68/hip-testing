"""Parse the agent's proposal output, post it to Jira, pin the comment id,
transition status, manage the agent-awaiting-human label, ping Slack.
Contract: proposal lives between ---PROPOSAL-START--- / ---PROPOSAL-END---
with YAML front matter. No prose outside markers (proposal-instructions.md).
Usage: post_proposal.py out.txt --ticket KEY --round N [--notify-slack CH]"""
import sys, os, re, json, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import jira, jira_adf, sha256_text, canon_text, _req

def _load_fm(raw):
    """Parse front matter from whatever shape the agent emitted. bug #16: agents
    wrap fm in a ```yaml fence as often as bare ---. bug #39: agents also emit
    the --- delimiters INSIDE the fence (and occasionally stray extra docs) —
    yaml.safe_load demands exactly one document and ComposerErrors. Strip edge
    delimiters, then merge any remaining mapping docs."""
    import yaml
    lines = raw.strip().splitlines()
    while lines and lines[0].strip() == "---": lines.pop(0)
    while lines and lines[-1].strip() == "---": lines.pop()
    try:
        docs = [d for d in yaml.safe_load_all("\n".join(lines)) if isinstance(d, dict)]
    except yaml.YAMLError:
        # fix-45: a non-YAML line inside the fm (prose mistaken for fm, drifted
        # shape) used to crash with an uncaught ScannerError/ComposerError.
        docs = []  # empty fm; the guard below reports WITH A FULL DUMP.
    fm = {}
    for d in docs:
        fm.update(d)
    return fm

def parse_proposal(text):
    m = re.search(r"---PROPOSAL-START---(.*?)---PROPOSAL-END---", text, re.S)
    if not m:
        # bug #48: name the drift instead of making a human diff the dump.
        near = sorted(set(re.findall(r"\S*PROPOSAL\S*", text)))
        raise SystemExit("FATAL: no PROPOSAL markers in agent output"
                         + (f" — near-miss tokens found: {near}"
                            " (agent used a drifted marker shape? fix the instructions"
                            " or extend parse_proposal)" if near else ""))
    body = m.group(1).strip()
    fm = {}
    fence = re.match(r"```ya?ml\s*\n(.*?)\n```", body, re.S)
    bare = body.startswith("---")
    if fence or bare:
        if fence:
            fm = _load_fm(fence.group(1))
            body = body[fence.end():].strip()
        else:
            end = body.find("\n---", 3)   # fix-45: find, never raise
            if end == -1:
                # No closing delimiter: parse best-effort, leave body empty so
                # the guard below fires WITH A FULL DUMP (was: ValueError).
                fm = _load_fm(body[3:])
                body = ""
            else:
                fm = _load_fm(body[3:end])
                body = body[end+4:].strip()
    return fm, body

def is_blocked(fm):
    """Template says open_questions:[]; agents also emit status: BLOCKED. Honor both."""
    return bool(fm.get("open_questions")) or str(fm.get("status", "")).upper() == "BLOCKED"

def transition(issue_key, name):
    ts = jira("GET", f"/issue/{issue_key}/transitions")["transitions"]
    for t in ts:
        if t["name"].lower() == name.lower():
            jira("POST", f"/issue/{issue_key}/transitions", {"transition": {"id": t["id"]}})
            return True
    return False

def set_label(issue_key, label, on):
    op = "add" if on else "remove"
    jira("PUT", f"/issue/{issue_key}", {"update": {"labels": [{op: label}]}})

def _dump_out(raw, out_file):
    """bug #45: on parse failure, show what the agent ACTUALLY wrote — marker
    drift is invisible from a bare exception. Size + first 800 chars."""
    print(f"--- agent output dump ({out_file}: {len(raw)} bytes, first 800) ---")
    print(raw[:800])
    print("--- end dump ---")

def main():
    out_file, args = sys.argv[1], sys.argv[2:]
    opt = {args[i]: args[i+1] for i in range(len(args)-1) if args[i].startswith("--")}
    key, rnd = opt["--ticket"], opt.get("--round", "0")
    raw = open(out_file).read()
    try:
        fm, body = parse_proposal(raw)
    except SystemExit:
        _dump_out(raw, out_file); raise
    except Exception as e:
        _dump_out(raw, out_file)
        raise SystemExit(f"FATAL: failed to parse agent output ({type(e).__name__}: {e})")
    if not body.strip():
        # bug #43: jira_adf("") builds an ADF doc with content [], which Jira
        # rejects as a cryptic 400 ("Comment body can not be empty!"). Die HERE
        # with evidence instead of at the API boundary.
        _dump_out(raw, out_file)
        raise SystemExit(f"FATAL: proposal body empty after parsing {out_file} "
                         f"(round {rnd}, {len(raw)} bytes) - markers "
                         "found but no body text (front-matter-only output?). "
                         "Check the agent step: was the human comment in context?")

    posted = jira("POST", f"/issue/{key}/comment", {"body": jira_adf(body)})
    # THE PINNING CONTRACT (PDF stage 4): gate 1 carries proposal_comment_id
    jira("PUT", f"/issue/{key}/properties/last-proposal",
         {"comment_id": posted["id"], "hash": sha256_text(canon_text(body)), "round": rnd})

    blocked = is_blocked(fm)
    if blocked and int(rnd) >= 3:
        transition(key, "Needs human refinement"); set_label(key, "agent-awaiting-human", False)
        print(f"round {rnd} still ambiguous -> Needs human refinement")
    elif blocked:
        transition(key, "Waiting on human input"); set_label(key, "agent-awaiting-human", True)
        print("open questions -> Waiting on human input (+agent-awaiting-human)")
    else:
        transition(key, "Proposal ready"); set_label(key, "agent-awaiting-human", True)
        print(f"proposal posted (comment {posted['id']}, tier {fm.get('risk_tier')}, "
              f"confidence {fm.get('confidence')}) -> Proposal ready")
    hook = os.environ.get("SLACK_WEBHOOK_URL")
    if hook and opt.get("--notify-slack"):
        _req("POST", hook, expect_json=False, body={"text": f"Proposal v{int(rnd)+1} for {key} "
            f"(tier {fm.get('risk_tier','?')}, confidence {fm.get('confidence','?')}): "
            f"{os.environ.get('JIRA_BASE_URL','')}/browse/{key}"})

if __name__ == "__main__":
    main()
