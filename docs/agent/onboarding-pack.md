# Agent onboarding pack — lighter-testing
<!-- GENERATED FILE — do not edit by hand. Narrative sections come from
     docs/agent/pack-curated.md; mechanical sections from git. Regenerate:
     python scripts/build_context_pack.py (nightly workflow opens the PR). -->
<!-- source commit: 7bb87d2 · generated: 2026-11-24 · pack owner: funcoin68 -->

## 0. Mission
Lighter testnet experiments in Python. The repo is the practice vehicle
for the agentic delivery pipeline: pipeline infrastructure (scripts/,
.github/workflows/) lives in the same repo as the app code — keep them
conceptually separate. Everything on-chain targets the Lighter testnet ONLY, with
testnet funds (fake money). There is no mainnet code path and never should be.

Testnet wallet (MetaMask, fake money only):
`0x2C9651cBaf1CD6bA028ADe56C1D4007B4E51dBDA`.

## 1. Module map
- `src/lighter_testing/` (2 files) — Application code — Lighter testnet interaction (Python). __init__.py, client.py
- `tests/` (2 files) — pytest suite (mirrors src/). test_client.py, test_smoke.py
- `scripts/` (33 files) — PIPELINE glue (stage 1-10) — not app code. _pipeline_lib.py, assemble_context.py, build_context_pack.py, build_outcome_record.py, build_review_packet.py, check_coverage_floor.py, check_diff_size.py, check_merge_eligibility.py … (+25)
- `.github/workflows/` (12 files) — PIPELINE workflows — human-owned, agents never edit. ci-pr-gates.yml, conflict-resolver.yml, onboarding-pack.yml, review-agent.yml, stage10-learn.yml, stage3-proposal.yml, stage3-respond.yml, stage5-implement.yml … (+4)
- `docs/agent/` (3 files) — Agent memory (this pack, CORRECTIONS.md). CORRECTIONS.md, onboarding-pack.md, pack-curated.md
- `agent/` (8 files) — Agent instruction prompts per stage. conflict-resolution-instructions.md, fix-instructions.md, implementation-instructions.md, proposal-instructions.md, proposal-template.md, review-functional.md, review-security.md, review-standards.md
- repo root — CLAUDE.md, SETUP.md, pyproject.toml, repo-registry.yaml

## 2. Request lifecycle
Fresh repo (ported 2026-11-24 from the chenyujielong pipeline). Python 3.12,
src layout (`src/lighter_testing/`), pytest + ruff, GitHub Actions on a public
repo. Agent stages (3 proposal → 5 implement → 6 swarm review → 7 fix → 8b QA
evidence → 9 merge) run against Jira tickets via the same workflows as the
idiom-game practice repo; stages 8b/9b deploy jobs are DORMANT until a staging
/prod target exists (set vars.STAGING_URL to wake 8b).

## 3. Data model
(nothing yet — first tickets will define it)

### Migrations on disk (mechanical — regenerated every run; trust this over any prose above)
- `supabase/migrations/` — 0 file(s): (none yet)

## 4. API / interface contracts
(nothing yet — Lighter testnet API client will land with the first ticket)

## 5. Conventions
- Python 3.12, src layout. Package importable as `lighter_testing` (`pip install -e ".[dev]"`).
- Tests in `tests/`, named `test_*.py`, run with `pytest` from repo root.
- Lint + format: `ruff check . && ruff format --check .` before every commit.
- Coverage: `pytest --cov=src --cov-report=json` produces `coverage.json`
  (input for scripts/check_coverage_floor.py, floor 70%).
- No private keys in the repo, ever — testnet keys come from secrets or a
  gitignored local .env.
- `scripts/` and `.github/workflows/` are pipeline infrastructure, human-owned;
  agents never edit them (stage 5/7 containment guards auto-revert).

## 6. Gotchas (highest-value section)
(none yet — port the chenyujielong CORRECTIONS.md ritual: one line per incident,
`YYYY-MM-DD — module — rule — source (PR#)`)

## 7. Hotspots & owners
App code (most-changed first, last 90d — high churn = review carefully, keep tests close):
- `.gitignore` — 1 commits

Pipeline infrastructure (churn here = pipeline debugging, not app risk):
- (none)

Owner: single service `lighter-testing` (repo-registry.yaml), owner funcoin68, risk tier 3. `.github/workflows/` is human-owned — workflow diffs from an agent are auto-reverted.

## 8. Recent decisions (rolling, last 90 days, from git log)
- 2026-11-24 — Initial commit

## 9. Open risks / known debt
- Deploy targets (stage 8b staging, stage 9b prod) not configured — smoke checks
  against a real URL will fail loudly until vars.STAGING_URL / Vercel+Supabase
  config is set.
- No application code yet — the first stage-5 run will set the module layout.

---
## Verification
Before claiming a task done, the agent must have run: `pytest` and
`ruff check .` on its diff. Coverage gate input: `pytest --cov=src --cov-report=json` (coverage.json).
Stale-pack warning: if this file is older than 14 days relative to repo HEAD,
distrust section 8 and re-derive from git log.
