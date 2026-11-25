#!/usr/bin/env bash
# fix-stage3-marker-drift-and-push.sh — stage 3 "Post proposal" failed in <1s
# (run 45617817182) because the agent emitted em-dash markers (——PROPOSAL-START——)
# and parse_proposal only matched ASCII '---'. This patches the parser to accept
# en/em-dash/minus/fullwidth-dash runs of 2+ and appends the exact output
# template to agent/proposal-instructions.md (it was missing).
# Run INSIDE your hip-testing clone:  bash fix-stage3-marker-drift-and-push.sh
set -euo pipefail

if [ ! -d .git ] || [ ! -d .github/workflows ]; then
  echo "ERROR: run this inside your hip-testing git clone." >&2
  exit 1
fi

PP=scripts/post_proposal.py
INSTR=agent/proposal-instructions.md
if [ -n "$(git status --porcelain -- "$PP")" ]; then
  echo "Local edits detected in $PP — commit or stash first:"
  git status --porcelain -- "$PP"
  exit 1
fi

echo '>> writing scripts/post_proposal.py'
cat > "$PP" <<'PY_EOF'
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

# hip-testing v2 (2026-11-25): markers also match en-dash / em-dash / minus /
# fullwidth-dash runs (2+) — kimi-for-coding sometimes renders '---' as the
# Chinese em-dash pair '——', which used to make the whole proposal unparseable
# (run 45617817182 failed in <1s with "no PROPOSAL markers" while the near-miss
# detector could see '——PROPOSAL-START——' plain as day).
# NOTE {2,} not {3,}: the Chinese em-dash pair "——" is TWO characters; {3,}
# would keep rejecting exactly the shape this is meant to rescue.
_MARKER_DASH = "[-\\u2013\\u2014\\u2212\\uff0d]{2,}"

def parse_proposal(text):
    m = re.search(_MARKER_DASH + r"PROPOSAL-START" + _MARKER_DASH +
                  r"(.*?)" + _MARKER_DASH + r"PROPOSAL-END" + _MARKER_DASH,
                  text, re.S)
    if not m:
        # bug #48: name the drift instead of making a human diff the dump.
        near = sorted(set(re.findall(r"\S*PROPOSAL\S*", text)))
        raise SystemExit("FATAL: no PROPOSAL markers in agent output"
                         + (f" — near-miss tokens found: {[t for t in map(repr, near)]}"
                            " (agent used a drifted marker shape — e.g. em-dashes"
                            " ——PROPOSAL-START——? extend _MARKER_DASH or fix the"
                            " instructions)" if near else ""))
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
    # hip-testing v2 (2026-11-24): was silently returning False on a name
    # mismatch while the caller printed "-> Proposal ready" regardless —
    # status writes must fail LOUDLY so a workflow-name drift is diagnosable.
    ts = jira("GET", f"/issue/{issue_key}/transitions")["transitions"]
    for t in ts:
        if t["name"].lower() == name.lower():
            jira("POST", f"/issue/{issue_key}/transitions", {"transition": {"id": t["id"]}})
            return True
    raise SystemExit(f"FATAL: workflow has no transition named '{name}' from the "
                     f"ticket's current status (offers: {[t['name'] for t in ts]}). "
                     "Fix the name in post_proposal.py to one of the listed transitions.")

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
PY_EOF

if ! grep -q "Output template (copy this shape EXACTLY" "$INSTR" 2>/dev/null; then
  echo '>> appending output template to agent/proposal-instructions.md'
  cat >> "$INSTR" <<'MD_EOF'

## Output template (copy this shape EXACTLY — ASCII hyphens, not em/en dashes)

---PROPOSAL-START---
```yaml
status: ready            # ready | BLOCKED
risk_tier: low           # low | medium | high
confidence: high         # low | medium | high
open_questions: []       # non-empty list => status BLOCKED
```
## Plan
<what you will build, file by file, path:line grounded>

## Verification
<how the human will see it works>
---PROPOSAL-END---

Markers must be ASCII `---` (three hyphens). The parser also accepts
en/em-dash variants, but do not rely on that — copy the ASCII shape.
Everything between the markers and outside the ```yaml fence is the JIRA
comment body; keep it decision-ready.
MD_EOF
else
  echo '>> template already present in agent/proposal-instructions.md — skipping append'
fi

if git diff --quiet -- "$PP" && git diff --quiet -- "$INSTR" 2>/dev/null; then
  echo "Already up to date — nothing to commit."
  exit 0
fi

git config user.email >/dev/null 2>&1 || git config user.email "pipeline-fix@users.noreply.github.com"
git config user.name  >/dev/null 2>&1 || git config user.name  "pipeline-fix"

git add "$PP" "$INSTR"
git commit -m "fix: stage 3 marker drift — accept em/en-dash markers, ship exact output template

- run 45617817182: agent emitted ——PROPOSAL-START—— (Chinese em-dash pair);
  the ASCII-only marker regex made post_proposal die in <1s with
  'no PROPOSAL markers' even though the near-miss detector could see the tokens
- _MARKER_DASH now accepts runs of 2+ of hyphen/en-dash/em-dash/minus/
  fullwidth-dash ({2,} because —— is TWO characters)
- near-miss FATAL message now prints repr() of the found tokens
- agent/proposal-instructions.md: append the exact copy-pasteable template —
  it described the contract but never showed the shape, inviting drift"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo ">> pushing $BRANCH"
git push origin "$BRANCH"
echo "Done. Re-run stage 3 for the ticket (or let Jira automation re-dispatch it)."
