"""Parse a reviewer run's stdout: verdict block + comments JSON fence; post as a
GitHub PR review with inline comments; add the reviewed-<persona> label.
Usage: post_review.py --pr 482 --persona functional --output-file /tmp/review_out.txt"""
import sys, os, re, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh, add_label, get_pr

def canon_verdict(raw):
    """Collapse free-text verdicts to the 3-state contract (bug #37). Cheap
    models paraphrase: "approve with comments", "LGTM", "looks good" — the
    aggregator's decision matrix only has room for block|approve|comment."""
    r = (raw or "").strip().lower()
    if "block" in r or "reject" in r or "request_change" in r or "request change" in r:
        return "block"
    if "approv" in r or "lgtm" in r or r in ("pass", "ok", "yes"):
        return "approve"
    return "comment"

def parse_crit(raw):
    """'0', 'none', '0 (see comments)' -> 0; '2' -> 2. Never raises (bug #37)."""
    m = re.search(r"\d+", str(raw or ""))
    return int(m.group(0)) if m else 0

def parse_review_output(text):
    """Extract verdict block and comments JSON from agent stdout."""
    vm = re.search(r"---REVIEW-START---(.*?)---REVIEW-END---", text, re.S)
    if not vm: raise SystemExit("FATAL: reviewer produced no verdict block")
    verdict = {}
    for line in vm.group(1).strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1); verdict[k.strip()] = v.strip()
    cm = re.search(r"```json\s*(\[.*?\])\s*```", text, re.S)
    comments = json.loads(cm.group(1)) if cm else []
    return verdict, comments

def main():
    args = sys.argv[1:]
    pr_no = args[args.index("--pr")+1]; persona = args[args.index("--persona")+1]
    text = open(args[args.index("--output-file")+1]).read()
    verdict, comments = parse_review_output(text)

    raw_v = (verdict.get("verdict") or "").strip()
    v = canon_verdict(raw_v)            # bug #37: never pass free text downstream
    crit = parse_crit(verdict.get("criticals"))
    # Never APPROVE as github-actions[bot] (bug #25): (a) repos that disallow
    # Actions PR approvals 422 with "GitHub Actions is not permitted to approve
    # pull requests"; (b) gate 2 is the HUMAN PR approval — a bot APPROVE would
    # satisfy a required-approvals rule and let the pipeline approve its own
    # code. The machine verdict travels in the review body + reviewed-* label;
    # maybe_aggregate parses verdicts from bodies, never from review state.
    event = "REQUEST_CHANGES" if v == "block" else "COMMENT"

    # inline comments: criticals become review comments; needs-human-eye flagged
    gh_comments = []
    for c in comments[:50]:   # safety bound
        body = c["body"]
        if c.get("severity") == "critical" and c.get("confidence") != "high":
            body = "**[needs-human-eye]** " + body
        gh_comments.append({"path": c["file"], "line": int(c.get("line", 1)),
                            "side": "RIGHT", "body": f"[{c.get('severity','comment')}] {body}"})
    head_sha = get_pr(pr_no)["head"]["sha"]
    body = (f"### Review — {persona} (clean-room, kimi-for-coding)\n"
            f"```\n---REVIEW-START---\npersona: {persona}\nverdict: {v}\n"
            f"criticals: {crit}\ncomments: {len(comments)}\n")
    if raw_v and raw_v.lower() != v:
        body += f"verdict_raw: {raw_v}\n"   # audit trail when drift was normalized (bug #37)
    body += "---REVIEW-END---\n```"
    payload = {"commit_id": head_sha, "event": event, "body": body}
    if gh_comments: payload["comments"] = gh_comments
    try:
        gh("POST", f"/pulls/{pr_no}/reviews", payload)
    except SystemExit:   # inline anchors can fail on outdated diffs; fall back to summary review
        payload.pop("comments", None)
        body += "\n\n" + "\n".join(f"- `{c['file']}:{c.get('line','?')}` "
                f"[{c.get('severity')}] {c['body']}" for c in comments)
        payload["body"] = body
        gh("POST", f"/pulls/{pr_no}/reviews", payload)
    add_label(pr_no, f"reviewed-{persona}")
    print(f"review posted: {persona} verdict={v} criticals={crit}; label reviewed-{persona}")

if __name__ == "__main__":
    main()
