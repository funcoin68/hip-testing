# fix-ci process — repairing red verification so PRs don't get stuck

## Problem it solves

`stage5-implement` deliberately runs "Run repo verification" **after** the PR
is open — a red step is a fix-up signal, not discarded work (run 45707393058
taught us the reverse destroys finished implementations). But until now nothing
consumed that signal: run 46023627197 failed step 15, and the PR just sat
there red. `ci-pr-gates` red on tests/lint had the same dead end.

## The loop

1. **Trigger.** `fix-ci` fires on `workflow_run` completion of
   `stage5-implement` or `ci-pr-gates` with conclusion=failure, and manually
   via `workflow_dispatch` (input: `pr_number`; optional `allow_any_branch`
   for human-owned PRs).
2. **Triage gate (auto path only).** ALL of these must hold, or the run exits
   with a notice and no action:
   - head branch is `agent/*` (human PRs are never auto-fixed);
   - every failed step in the triggering run is one of `Tests`,
     `Lint + format`, `Coverage floor`, `Run repo verification …` — read from
     the run's jobs API, not assumed. **gitleaks, scope-guard, and diff-cap
     failures route to humans**; an agent must never "fix" a secret-scan or
     scope violation by editing around it;
   - an open PR exists for the branch.
3. **Round accounting.** Rounds are counted in `fix-ci-round-N` labels on the
   PR (N = 1..3), computed from labels in **both** trigger paths — the cap
   cannot be reset by re-dispatching. Round 4 can never start.
4. **Repair.** Checks out the PR branch, re-runs the checks (capturing
   output), and hands failures to the cheap tier (`kimi-for-coding`, same as
   stage 7) under `agent/fix-ci-instructions.md`: minimal diff, never weaken
   tests/lint to pass, never touch `.github/` (containment guard force-reverts
   it, bug #32 pattern), never commit secrets.
5. **Push gate.** The fix is committed and pushed **only if pytest and ruff
   pass locally** — a red branch is never pushed. The push triggers
   `ci-pr-gates` naturally.
6. **Outcomes.**
   - Push + gates green → PR proceeds to swarm review as usual.
   - Push + gates red → next failure triggers round N+1 (bounded at 3).
   - Fix can't verify green locally → no push; the remaining failure output is
     commented on the PR and the step fails loudly.
   - Everything green on a fresh runner (flaky/original failure not
     reproducible) → PR comment, nothing pushed, round label stays but no diff
     is burned.
   - 3 rounds exhausted → comment on the PR telling a human to take over, and
     how to re-arm (delete the `fix-ci-round-*` labels). Jira is untouched —
     the ticket is already in review, which is the correct state for a human
     handoff.

## What it deliberately does NOT do

- Fix human-owned branches automatically (manual dispatch + `allow_any_branch`
  exists as the escape hatch).
- Touch gitleaks / scope-guard / diff-cap failures — those are policy/safety
  signals.
- Bypass review: a pushed fix still goes through `ci-pr-gates` → swarm →
  merge eligibility like any other commit.

## Adoption checklist

1. Copy `fix-ci.yml` → `.github/workflows/fix-ci.yml`.
2. Copy `agent-fix-ci-instructions.md` → `agent/fix-ci-instructions.md`.
3. (Optional, comment-only) In `stage5-implement.yml`, update the header note
   "A follow-up fix-up job (out of scope for now)" to
   "fix-ci.yml consumes this signal (auto repair, bounded at 3 rounds)".
4. Commit to main — required for the `workflow_run` trigger to fire.
5. Verify: on the stuck PR (the one from run 46023627197), either wait for the
   next failing run or dispatch `fix-ci` manually with its `pr_number`.

## Tuning knobs (repo vars)

- `TEST_COMMAND` / `LINT_COMMAND` — same vars the other workflows already use.
- `FIXER_MODEL` / `CLAUDE_CODE_VERSION` — shared with stage 7.
- Round cap is hardcoded at 3 in the workflow (mirrors stage 7's bound); raise
  it there if the swarm review data ever justifies it.

## Known failure modes to watch

- **Flaky tests** will eat rounds. The agent instructions forbid papering
  over flakiness; if a PR exhausts rounds on an intermittent failure, the
  handoff comment is the cue to quarantine that test in its own PR.
- **Environment-drift failures** (passes locally in fix-ci, fails in gates, or
  vice versa — e.g. Python 3.12 vs 3.14) can ping-pong rounds. The local
  verify gate limits the blast radius to comments, not pushes.
- **Prompt injection via failing test output** is bounded by the same
  containment as stage 7: read-only default token, branch-scoped push token,
  `.github/` revert, cheap model, 20-minute timeout.
