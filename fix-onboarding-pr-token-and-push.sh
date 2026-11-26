#!/usr/bin/env bash
# fix-onboarding-pr-token-and-push.sh — onboarding-pack nightly run failed:
# branch pushed fine but "gh pr create" with GITHUB_TOKEN died, because new
# personal repos default "Allow GitHub Actions to create and approve pull
# requests" to OFF. Fix: create the refresh PR with AGENT_PUSH_TOKEN (bot PAT)
# exactly like stage5-implement does. GITHUB_TOKEN goes back to read-only.
# Run INSIDE your hip-testing clone:  bash fix-onboarding-pr-token-and-push.sh
set -euo pipefail

if [ ! -d .git ] || [ ! -d .github/workflows ]; then
  echo "ERROR: run this inside your hip-testing git clone." >&2
  exit 1
fi

WF=.github/workflows/onboarding-pack.yml
if [ -n "$(git status --porcelain -- "$WF")" ]; then
  echo "Local edits detected in $WF — commit or stash first:"
  git status --porcelain -- "$WF"
  exit 1
fi

echo '>> writing .github/workflows/onboarding-pack.yml'
cat > "$WF" <<'WORKFLOW_EOF'
# .github/workflows/onboarding-pack.yml — keep agent context fresh.
# Nightly regeneration of docs/agent/onboarding-pack.md (module map, hotspots,
# recent decisions from git; narrative from docs/agent/pack-curated.md).
# Opens a PR when stale — a HUMAN merges. Nothing enters agent memory without
# a human merge (same invariant as CORRECTIONS.md). To change what agents
# believe about the repo, edit docs/agent/pack-curated.md, not the pack.
# hip-testing v2 (2026-11-25): PR creation rides AGENT_PUSH_TOKEN, not
# GITHUB_TOKEN. New personal repos default "Allow GitHub Actions to create and
# approve pull requests" OFF, so gh pr create with GITHUB_TOKEN dies with
# "GitHub Actions is not permitted to create or approve pull requests"
# (first nightly run 45559978170: branch pushed, PR never opened). Stage 5's
# open_pr.py already uses AGENT_PUSH_TOKEN for the same reason.
name: onboarding-pack-refresh
on:
  schedule:
    - cron: "23 5 * * *"        # nightly, off the hour (memory-agent uses :17)
  workflow_dispatch:            # manual refresh after big merges

permissions:
  contents: read                # GITHUB_TOKEN stays read-only; branch push AND PR
                                # creation both ride AGENT_PUSH_TOKEN (bot PAT, same
                                # pattern as stage5-implement) because this repo does
                                # not allow GITHUB_TOKEN to create PRs.

jobs:
  regenerate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0        # hotspots + recent decisions read git log
          token: "${{ secrets.AGENT_PUSH_TOKEN }}"
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }

      - name: Regenerate pack
        run: python scripts/build_context_pack.py

      - name: Push refresh branch + PR if stale
        env:
          GH_TOKEN: "${{ secrets.AGENT_PUSH_TOKEN }}"   # PR creation with GITHUB_TOKEN is
                                                         # blocked repo-wide (new-repo default);
                                                         # the bot PAT bypasses it like stage 5.
        run: |
          set -e
          gh --version   # diagnostics: if PR creation fails, auth status shows WHICH identity gh uses
          gh auth status || true
          if git diff --quiet docs/agent/onboarding-pack.md; then
            echo "pack already fresh — nothing to do"; exit 0
          fi
          git config user.name "pipeline-agent"
          git config user.email "pipeline-agent@users.noreply.github.com"
          BRANCH="agent/onboarding-pack-refresh"
          git checkout -B "$BRANCH"
          git add docs/agent/onboarding-pack.md
          git commit -m "chore: regenerate onboarding pack ($(date +%F))"
          git push --force-with-lease origin "$BRANCH"
          EXISTING=$(gh pr list --head "$BRANCH" --state open --json number -q '.[0].number' || true)
          if [ -z "$EXISTING" ]; then
            BODY="Nightly mechanical regeneration of docs/agent/onboarding-pack.md."
            BODY="$BODY"$'\n\n'"- Module map / hotspots / recent decisions re-derived from git (last 90d)."
            BODY="$BODY"$'\n'"- Narrative sections only change when docs/agent/pack-curated.md is edited — review such diffs with care."
            BODY="$BODY"$'\n'"- Merge to keep stage 3/5 agent context current; the 14-day staleness warning in assemble_context.py is the tripwire."
            gh pr create --title "chore: onboarding pack refresh ($(date +%F))" \
              --body "$BODY" --base main --head "$BRANCH"
          else
            echo "refresh PR #$EXISTING updated"
          fi
WORKFLOW_EOF

if git diff --quiet -- "$WF"; then
  echo "Already up to date — nothing to commit."
  exit 0
fi

git config user.email >/dev/null 2>&1 || git config user.email "pipeline-fix@users.noreply.github.com"
git config user.name  >/dev/null 2>&1 || git config user.name  "pipeline-fix"

git add "$WF"
git commit -m "fix: onboarding-pack PR creation uses AGENT_PUSH_TOKEN, not GITHUB_TOKEN

first nightly run pushed the refresh branch but gh pr create failed: new
personal repos default 'Allow GitHub Actions to create and approve pull
requests' to OFF. Stage 5 already creates PRs with AGENT_PUSH_TOKEN for the
same reason. GITHUB_TOKEN goes back to least-privilege read."

BRANCH=$(git rev-parse --abbrev-ref HEAD)
echo ">> pushing $BRANCH"
git push origin "$BRANCH"
cat <<'NOTE'

Done. Optionally delete the leftover refresh branch once tonight's run
recreates it cleanly:  git push origin --delete agent/onboarding-pack-refresh
(Otherwise the next run force-pushes over it — harmless either way.)
NOTE
