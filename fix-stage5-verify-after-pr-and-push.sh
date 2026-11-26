#!/usr/bin/env bash
# fix-stage5-verify-after-pr-and-push.sh — move "Run repo verification" to AFTER
# "Push branch + open PR" in stage5-implement.yml. Run 45707393058: verification
# failed pre-PR, so containment/push/Jira-transition all skipped and a finished
# implementation was thrown away. New order: Implement -> Containment -> Push +
# open PR -> Jira "In review" -> verification (red here is now only a fix-up
# signal — the PR stays open).
# Run INSIDE your hip-testing clone:  bash fix-stage5-verify-after-pr-and-push.sh
set -euo pipefail

if [ ! -d .git ] || [ ! -d .github/workflows ]; then
  echo "ERROR: run this inside your hip-testing git clone." >&2
  exit 1
fi

WF=.github/workflows/stage5-implement.yml
if [ -n "$(git status --porcelain -- "$WF")" ]; then
  echo "Local edits detected in $WF — commit or stash first:"
  git status --porcelain -- "$WF"
  exit 1
fi

echo '>> writing .github/workflows/stage5-implement.yml'
cat > "$WF" <<'WORKFLOW_EOF'
env:
  CLAUDE_CODE_EFFORT_LEVEL: high

# .github/workflows/stage5-implement.yml — stage 5: implementation on agent/* branch
# hip-testing v2 (2026-11-25): "Run repo verification" moved AFTER Push branch +
# open PR. Run 45707393058: verification failed pre-PR, the finished implementation
# was never pushed and the Jira transition never fired — pure waste. Order is now
# Implement -> Containment -> Push + open PR -> Jira "In review" -> verification.
# A red verification step no longer discards anything: the PR is open, the ticket
# is in review, and ci-pr-gates runs the same checks on the PR. A follow-up
# fix-up job (out of scope for now) can repair failing tests on the open branch.
# v4.9 bug #34: fetch Jira attachments into context (see scripts/fetch_attachments.py).
# Dispatched by Jira Automation when gate 1 ("Approve plan") fires.
# v4.1 fixes: git identity on the ephemeral runner (commit used to fail silently),
# read-only default token (write comes only from the branch-scoped AGENT_PUSH_TOKEN),
# idempotency pre-check, Kimi Code endpoint + model pinned.
# v4.6 bug #21: resume-or-create the agent branch (plain push from a fresh
# branch off main is a non-fast-forward on re-run) + --force-with-lease push +
# loud failure when the agent produced no changes (a no-op commit had pushed
# only a __pycache__ .pyc). TEST/LINT vars get npm fallbacks.
# v4.4 bug #19: --permission-mode bypassPermissions on the claude step - a headless
# runner cannot answer interactive write approvals, so every Write/Edit was denied.
# v4.9 bug #32: containment guard reverts any .github/ edits before commit - the
# CYJ-3 agent rewrote all 8 workflows (node 20 -> 26) instead of implementing, and
# only the PAT's missing workflow scope stopped it. Defense in depth: instructions
# say no, this guard enforces it, the token makes it physically impossible.
# v4.10 bug #40: empty-diff guard no longer hard-fails. A re-dispatched ticket
# whose work is already merged (CYJ-7/PR #5) legitimately produces no changes -
# the branch resumes == main and the agent rightly writes nothing. The old guard
# burned a red run and destroyed the evidence; the step now judges branch-vs-main,
# dumps the agent output + git state into the log, and exits green when empty.
# Blast radius stays small: this step env holds only KIMI_CODE_API_KEY; pushes use the
# branch-scoped AGENT_PUSH_TOKEN and the ruleset still blocks main.
# v4.11 bug #41: the PR body now comes from the agent-written /tmp/pr_body.md
# (--body-file). open_pr.py used to open every PR with placeholder 'modules: []',
# and ci-pr-gates fires ~4s after 'opened' - the scope guard read the placeholder
# and hard-failed (PR #6) before a human could paste the real body. open_pr.py now
# validates a non-empty modules: line BEFORE opening: a missing declaration fails
# stage 5 loudly here, not as a red CI run minutes later.
name: stage5-implement
on:
  workflow_dispatch:
    inputs:
      ticket_key:          { required: true, type: string }
      proposal_comment_id: { required: true, type: string }

permissions: { contents: read }   # GITHUB_TOKEN stays read-only; push uses AGENT_PUSH_TOKEN (agent/* only)

jobs:
  implement:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: main
          token: "${{ secrets.AGENT_PUSH_TOKEN }}"
      - uses: actions/setup-node@v4
        with: { node-version: "26.13.0" }
      - run: npm i -g "@anthropic-ai/claude-code@${{ vars.CLAUDE_CODE_VERSION || 'latest' }}"   # bug #33: set repo var CLAUDE_CODE_VERSION to freeze after validating a version
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Install dependencies
        # Fail loudly here, not as a confusing pytest collection error later
        # (2026-11-24 incident): a silent fallback to bare pytest leaves the
        # package uninstalled. Middle tier covers a branch dropping [dev].
        run: pip install -e ".[dev]" || pip install -e . || { echo "::error::editable install failed — package cannot be imported"; exit 1; }

      - name: Git identity (ephemeral runner has none)
        run: |
          git config user.name "pipeline-agent"
          git config user.email "pipeline-agent@users.noreply.github.com"

      - id: dup
        name: Idempotency — detect existing PR for this ticket
        env:
          GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}"
        run: python scripts/open_pr.py --ticket "${{ inputs.ticket_key }}" --check

      - name: Create or resume work branch
        if: steps.dup.outputs.exists != 'true'
        run: |
          # Idempotent re-run (bug #21): if an earlier attempt already pushed
          # agent/<ticket>, resume from it instead of branching fresh off main -
          # a plain push from a fresh branch is a non-fast-forward and dies with
          # "fetch first". agent/* branches are agent-owned, so rebasing onto
          # origin/main is safe and keeps history linear.
          git fetch origin "agent/${{ inputs.ticket_key }}" || true
          if git rev-parse --verify "origin/agent/${{ inputs.ticket_key }}" >/dev/null 2>&1; then
            git checkout -B "agent/${{ inputs.ticket_key }}" "origin/agent/${{ inputs.ticket_key }}"
            git rebase origin/main || git rebase --abort
          else
            git checkout -b "agent/${{ inputs.ticket_key }}"
          fi

      - name: Fetch ticket + PINNED proposal + context
        if: steps.dup.outputs.exists != 'true'
        env:
          JIRA_BASE_URL: ${{ vars.JIRA_BASE_URL }}
          JIRA_USER_EMAIL: ${{ vars.JIRA_USER_EMAIL }}
          JIRA_TOKEN: ${{ secrets.JIRA_TOKEN }}
        run: |
          python scripts/fetch_ticket.py "${{ inputs.ticket_key }}" > /tmp/ticket.json
          pip install --quiet pypdf || true   # PDF design docs; script degrades gracefully without it
          python scripts/fetch_attachments.py /tmp/ticket.json --out /tmp/attachments
          python scripts/fetch_proposal.py "${{ inputs.ticket_key }}"               "${{ inputs.proposal_comment_id }}" > /tmp/proposal.md
          # hard gate: fetched proposal hash must still match the approved one
          python scripts/verify_pinned.py "${{ inputs.ticket_key }}" /tmp/proposal.md
          python scripts/assemble_context.py /tmp/ticket.json > /tmp/context.md

      - name: Implement (kimi-for-coding, normal mode, hard timeout)
        if: steps.dup.outputs.exists != 'true'
        env:
          ANTHROPIC_BASE_URL: https://api.kimi.ai/coding
          ANTHROPIC_API_KEY: ${{ secrets.KIMI_CODE_API_KEY }}
          CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1"
        timeout-minutes: 45
        run: |
          claude -p --permission-mode bypassPermissions --model "${{ vars.FRONTIER_MODEL || 'kimi-for-coding' }}"             --append-system-prompt "$(cat agent/implementation-instructions.md)"             "$(cat /tmp/context.md)
          APPROVED PROPOSAL (your spec):
          $(cat /tmp/proposal.md)" 2>&1 | tee /tmp/implement.log   # bug #40: tee so the empty-diff path can dump what the agent actually did

      - name: "Containment: revert any .github/ edits (bug #32)"
        if: steps.dup.outputs.exists != 'true'
        run: |
          # Agents never touch pipeline infrastructure. The AGENT_PUSH_TOKEN lacks
          # the workflow scope, so a push carrying .github/ changes is rejected
          # wholesale — stripping them here saves the rest of the agent's work.
          if git status --porcelain -- .github/ | grep .; then
            echo "::warning::Agent modified .github/ — reverting. Workflows/actions are human-owned infrastructure."
            git status --porcelain -- .github/
            git restore --source=HEAD --staged --worktree -- .github/
          fi

      - name: Push branch + open PR
        if: steps.dup.outputs.exists != 'true'
        env:
          GH_TOKEN: ${{ secrets.AGENT_PUSH_TOKEN }}
          JIRA_BASE_URL: ${{ vars.JIRA_BASE_URL }}
          JIRA_USER_EMAIL: ${{ vars.JIRA_USER_EMAIL }}
          JIRA_TOKEN: ${{ secrets.JIRA_TOKEN }}
        run: |
          git add -A
          # bug #40: commit only if this run produced changes, then judge the
          # BRANCH against main - not the staged tree. On a resumed branch whose
          # work already merged, the staged diff is empty AND branch == main:
          # that is a no-op, not an agent failure. Dump evidence, exit green.
          if ! git diff --cached --quiet; then
            git commit -m "agent: ${{ inputs.ticket_key }} implementation"
          fi
          git fetch origin main
          if git diff --quiet origin/main...HEAD; then
            echo "::notice::Nothing to ship: branch matches main (ticket already merged, or agent made no changes). Not opening an empty PR."
            echo "=== git status ==="
            git status --short --branch
            echo "=== branch commits not on main ==="
            git log --oneline origin/main..HEAD || true
            echo "=== tail of agent output (implement step) ==="
            tail -n 120 /tmp/implement.log 2>/dev/null || echo "(no implement log captured)"
            exit 0
          fi
          # --force-with-lease: agent-owned branch; overwrites only if the remote
          # is exactly what we fetched (rebase path), never clobbers human work.
          git push --force-with-lease -u origin "agent/${{ inputs.ticket_key }}"
          python scripts/open_pr.py --ticket "${{ inputs.ticket_key }}"             --base main --head "agent/${{ inputs.ticket_key }}" --body-file /tmp/pr_body.md   # bug #41: agent-written body; placeholder modules:[] fails the scope guard on arrival

      # 2026-11-24: this lived INSIDE the push step, so any re-dispatch that hit
      # the idempotency skip (PR already exists) never reached the Jira transition.
      # It is its own step now: runs on every implement completion, tolerant when
      # the ticket is already in review (--if-available + already-in-status check),
      # and the transition name comes from a repo var because the HIP project's
      # workflow names it differently than chenyujielong's did (set
      # JIRA_REVIEW_TRANSITION to the exact transition name your workflow offers).
      - name: "Jira: move ticket to review + comment"
        env:
          JIRA_BASE_URL: ${{ vars.JIRA_BASE_URL }}
          JIRA_USER_EMAIL: ${{ vars.JIRA_USER_EMAIL }}
          JIRA_TOKEN: ${{ secrets.JIRA_TOKEN }}
        run: |
          python scripts/jira_transition.py "${{ inputs.ticket_key }}" \
            "${{ vars.JIRA_REVIEW_TRANSITION || 'In review' }}" \
            --comment "Implementation PR opened. Status: In review." \
            --if-available
      - name: "Run repo verification (post-PR — failure never discards implementation)"
        if: steps.dup.outputs.exists != 'true'
        # hip-testing v2 (2026-11-25): moved AFTER Push branch + open PR (run
        # 45707393058: verification failed pre-PR and the finished implementation
        # was thrown away). The PR is already open and the ticket already in
        # review when this runs; a red step here is a fix-up signal, not lost
        # work — ci-pr-gates re-runs the same checks on the PR anyway.
        run: |
          echo "PR is open and ticket is in review; verification failure here is a fix-up signal, not discarded work."
          ${{ vars.TEST_COMMAND || 'pytest' }}
          ${{ vars.LINT_COMMAND || 'ruff check .' }}
WORKFLOW_EOF

if git diff --quiet -- "$WF"; then
  echo "Already up to date — nothing to commit."
  exit 0
fi

git config user.email >/dev/null 2>&1 || git config user.email "pipeline-fix@users.noreply.github.com"
git config user.name  >/dev/null 2>&1 || git config user.name  "pipeline-fix"

git add "$WF"
git commit -m "fix: run repo verification AFTER opening the PR (never discard implementation)

run 45707393058: verification failed before the push step, so containment,
push+PR and the Jira transition all skipped and the finished implementation
was wasted. Verification now runs last; a red step no longer throws work away
— the PR is open, the ticket is in review, and ci-pr-gates re-runs the same
checks on the PR. Follow-up fix-up job intentionally out of scope."

BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo ">> pushing $BRANCH"
git push origin "$BRANCH"
echo "Done."
