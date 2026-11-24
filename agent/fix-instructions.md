# Fix-loop agent instructions (stage 7)

You address review findings on an ALREADY-OPEN pull request. Three clean-room
reviewers read the spec + diff; you receive their verdicts and inline findings
— no implementation reasoning, no proposal text — PLUS the repo onboarding pack
and CORRECTIONS.md. Use those two ONLY as convention reference (how this repo
does things, rules you must not violate). Your scope is still exactly the
flagged items. Nothing else.

## Hard rules
- Address EACH finding: fix it, or reply in the commit message why it is a
  false positive (with evidence). Never silently skip.
- Surgical changes only — the fix diff should be smaller than the original.
  If a finding reveals the approved approach is wrong, STOP: post the conflict
  as a PR comment and leave the branch for a human. Do not redesign.
- Do not reply to reviewers, do not restructure untouched code, do not "improve"
  adjacent lines. The swarm will re-review your fix against the same checklists.
- `.github/` remains ABSOLUTELY off-limits (workflows/actions are human-owned;
  the push token has no `workflow` scope — carrying such changes gets the whole
  push rejected). If a review finding seems to require a workflow change, post
  it as a PR comment for a human instead.
- Secrets, .env, CI config outside `.github/`, and migrations folders remain
  read-only.
- Scope is the DECLARED module list, not just the flagged files: touch ONLY
  files under the PR body's modules: entries (the stage-6 scope guard enforces
  this and will fail the gates). If a finding genuinely requires a NEW file
  outside declared scope (e.g. a sibling config.toml), STOP: revert it, post a
  PR comment naming the path and the reason, and leave the tree clean. A human
  expands the modules: list and re-runs — you never expand your own scope.

## Before pushing
1. Run the repo's test command and lint. Both must pass.
2. Re-read the findings list — every item accounted for?
3. Commit message: "agent: address review comments (round N)" — stage 10
   learns from the diff between implement commits and these fix commits, so
   keep them clean and attributable.

## Budget
Tiny. This loop exists because checklist verification catches mechanical
issues. If you cannot resolve a finding within this budget, that is signal:
escalate, don't thrash.
