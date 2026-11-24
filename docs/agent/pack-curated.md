# Curated sections for the onboarding pack (TIER 0 — human-edited)

This file is the human-owned source for the narrative sections of
docs/agent/onboarding-pack.md. Edit THIS file; the pack itself is regenerated
by scripts/build_context_pack.py (nightly workflow or manual run), which
splices these sections together with the mechanical ones (module map,
hotspots, recent decisions).

## 0. Mission
Lighter testnet experiments in Python. The repo is the practice vehicle
for the agentic delivery pipeline: pipeline infrastructure (scripts/,
.github/workflows/) lives in the same repo as the app code — keep them
conceptually separate. Everything on-chain targets the Lighter testnet ONLY, with
testnet funds (fake money). There is no mainnet code path and never should be.

Testnet wallet (MetaMask, fake money only):
`0x2C9651cBaf1CD6bA028ADe56C1D4007B4E51dBDA`.

## 2. Request lifecycle
Fresh repo (ported 2026-11-24 from the chenyujielong pipeline). Python 3.12,
src layout (`src/lighter_testing/`), pytest + ruff, GitHub Actions on a public
repo. Agent stages (3 proposal → 5 implement → 6 swarm review → 7 fix → 8b QA
evidence → 9 merge) run against Jira tickets via the same workflows as the
idiom-game practice repo; stages 8b/9b deploy jobs are DORMANT until a staging
/prod target exists (set vars.STAGING_URL to wake 8b).

## 3. Data model
(nothing yet — first tickets will define it)

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

## 6. Gotchas (highest-value section — fill from real incidents/reviews)
(none yet — port the chenyujielong CORRECTIONS.md ritual: one line per incident,
`YYYY-MM-DD — module — rule — source (PR#)`)

## 9. Open risks / known debt
- Deploy targets (stage 8b staging, stage 9b prod) not configured — smoke checks
  against a real URL will fail loudly until vars.STAGING_URL / Vercel+Supabase
  config is set.
- No application code yet — the first stage-5 run will set the module layout.
