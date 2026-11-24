"""Swarm aggregator. Runs at the end of EVERY reviewer run; exits unless all
three reviewed-* labels are present (fires exactly once, on the last reviewer).
Decision matrix (PDF stage 7/8):
  any persona missing            -> exit quietly (not my job yet)
  block (= changes requested)    -> dispatch stage7-fix round N+1 (bug #46;
                                    was: straight to human, fix loop never ran)
  any criticals                  -> dispatch stage7-fix round N+1 (N<3), else human
  all approve/comment, 0 crit    -> consensus-passed label + dispatch stage 8b
Usage: maybe_aggregate.py --pr 482 --ticket PROJ-123

v4.9 bug #37: cheap models paraphrase the verdict contract ("approve with
comments", "LGTM", "criticals: none"); the old strict parser routed ANY
non-exact verdict string to the "conflicting verdicts -> human" dead end
(labels cleared, nothing dispatched) and int() crashed on non-numeric
criticals — three clean reviews could stall the pipeline with no consensus.
Verdicts are now normalized to the 3-state contract on BOTH sides (post_review
writes canonical; this script re-normalizes on read, defense in depth), and
criticals are digit-extracted. ALSO: self-heals a missing reviewed-* label
when that persona's review IS posted (bug-#25 class: review posted, run died
before add_label), with a 120s age guard so a concurrent reviewer mid-post
is never double-aggregated.
"""
import sys, os, re, datetime
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh, pr_labels, add_label, pr_comment, dispatch

PERSONAS = ["functional", "security", "standards"]
MAX_FIX_ROUNDS = 3
HEAL_MIN_AGE_S = 120

def canon_verdict(raw):
    """Collapse free-text verdicts to the 3-state contract: block|approve|comment."""
    r = (raw or "").strip().lower()
    if "block" in r or "reject" in r or "request_change" in r or "request change" in r:
        return "block"
    if "approv" in r or "lgtm" in r or r in ("pass", "ok", "yes"):
        return "approve"
    return "comment"

def parse_crit(raw):
    """'0', 'none', '0 (see comments)', 'two' -> 0; '2' -> 2. Never raises."""
    m = re.search(r"\d+", str(raw or ""))
    return int(m.group(0)) if m else 0

def latest_verdicts(pr):
    """Newest verdict block per persona, parsed from PR review bodies."""
    verdicts = {}
    reviews = gh("GET", f"/pulls/{pr}/reviews?per_page=100")
    for r in reviews:
        m = re.search(r"---REVIEW-START---(.*?)---REVIEW-END---", r.get("body") or "", re.S)
        if not m: continue
        d = dict(line.split(":", 1) for line in
                 (l.strip() for l in m.group(1).strip().splitlines() if ":" in l))
        p = d.get("persona", "").strip()
        if p in PERSONAS:
            d["_submitted_at"] = r.get("submitted_at") or ""
            verdicts[p] = d   # later reviews overwrite = newest wins
    return verdicts

def decide(verdicts, fix_round):
    """Pure decision function — unit-tested. Returns (action, detail).
    bug #46: a 'block' verdict IS a changes-requested (post_review maps
    request_changes -> block), so it enters the same bounded fix loop as
    criticals — a change request was previously a dead end straight to
    human. A request that survives MAX_FIX_ROUNDS rounds still lands on a
    human; consensus genuinely means zero criticals AND zero change requests."""
    vs = {p: canon_verdict(v.get("verdict")) for p, v in verdicts.items()}
    requested = sorted(p for p, x in vs.items() if x == "block")
    total_crit = sum(parse_crit(v.get("criticals")) for v in verdicts.values())
    if requested or total_crit > 0:
        if fix_round >= MAX_FIX_ROUNDS:
            return "human", (f"changes still requested ({', '.join(requested)}) / "
                             f"{total_crit} criticals open after {fix_round} fix rounds")
        why = []
        if requested:
            why.append(f"changes requested by {', '.join(requested)}")
        if total_crit:
            why.append(f"{total_crit} criticals")
        return "fix", "; ".join(why) + f" -> fix round {fix_round + 1}"
    return "consensus", "CI green + zero criticals + security pass"

def healable(labels, verdicts, now=None):
    """Personas whose review IS posted but whose reviewed-* label is missing
    (run died between posting and add_label — bug-#25 class). The 120s age
    guard ensures we never heal a reviewer whose post/label steps are merely
    in flight right now (would double-fire aggregation with that run)."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    out = []
    for p in PERSONAS:
        if f"reviewed-{p}" in labels or p not in verdicts:
            continue
        ts = verdicts[p].get("_submitted_at", "")
        try:
            age = (now - datetime.datetime.fromisoformat(
                ts.replace("Z", "+00:00"))).total_seconds()
        except ValueError:
            continue   # no trustworthy timestamp -> don't heal
        if age >= HEAL_MIN_AGE_S:
            out.append(p)
    return out

def remove_label_safe(pr, label):
    from _pipeline_lib import remove_label
    remove_label(pr, label)

def main():
    args = sys.argv[1:]
    pr_no, ticket = args[args.index("--pr")+1], args[args.index("--ticket")+1]
    labels = pr_labels(pr_no)
    verdicts = latest_verdicts(pr_no)
    for p in healable(labels, verdicts):
        add_label(pr_no, f"reviewed-{p}")
        labels.add(f"reviewed-{p}")
        print(f"self-heal: re-added reviewed-{p} (review was posted, label missing)")
    if not all(f"reviewed-{p}" in labels for p in PERSONAS):
        print("not all reviewers in yet — exiting (aggregation fires on the last one)")
        return
    fix_round = len([l for l in labels if re.fullmatch(r"fix-round-\d+", l)])
    action, detail = decide(verdicts, fix_round)
    print(f"aggregation: {action} — {detail}")

    if action == "fix":
        add_label(pr_no, f"fix-round-{fix_round + 1}")
        dispatch("stage7-fix.yml", {"pr_number": pr_no, "ticket_key": ticket,
                                    "round": fix_round + 1})
    elif action == "consensus":
        add_label(pr_no, "consensus-passed")
        pr_comment(pr_no, f"**Consensus passed** (stage 8): {detail}. QA evidence bundle next.")
        dispatch("stage8b-qa-evidence.yml", {"pr_number": pr_no, "ticket_key": ticket})
    else:
        for p in PERSONAS: remove_label_safe(pr_no, f"reviewed-{p}")
        pr_comment(pr_no, f"**Swarm stopped — needs human**: {detail}. "
                          "Labels cleared; a human decides next step.")

if __name__ == "__main__":
    main()
