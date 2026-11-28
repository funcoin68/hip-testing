# Agent onboarding pack — lighter-testing
<!-- GENERATED FILE — do not edit by hand. Narrative sections come from
     docs/agent/pack-curated.md; mechanical sections from git. Regenerate:
     python scripts/build_context_pack.py (nightly workflow opens the PR). -->
<!-- source commit: 238b9ce · generated: 2026-11-28 · pack owner: funcoin68 -->

## 0. Mission
Lighter testnet experiments in Python. The repo is the practice vehicle
for the agentic delivery pipeline: pipeline infrastructure (scripts/,
.github/workflows/) lives in the same repo as the app code — keep them
conceptually separate. Everything on-chain targets the Lighter testnet ONLY, with
testnet funds (fake money). There is no mainnet code path and never should be.

Testnet wallet (MetaMask, fake money only):
`0x2C9651cBaf1CD6bA028ADe56C1D4007B4E51dBDA`.

## 1. Module map
- `src/kalshi_testing/` (5 files) — Application code — kalshi_testing (Python). __init__.py, auth.py, nba_scanner.py, persistence.py, server.py
- `src/lighter_testing/` (6 files) — Application code — lighter_testing (Python). __init__.py, api_keys.py, client.py, markets.py, order_debug.py, orders.py
- `tests/` (11 files) — pytest suite (mirrors src/). test_api_keys.py, test_client.py, test_dotenv_loading.py, test_kalshi_auth.py, test_kalshi_server.py, test_markets.py, test_nba_scanner.py, test_order_debug.py … (+3)
- `scripts/` (33 files) — PIPELINE glue (stage 1-10) — not app code. _pipeline_lib.py, assemble_context.py, build_context_pack.py, build_outcome_record.py, build_review_packet.py, check_coverage_floor.py, check_diff_size.py, check_merge_eligibility.py … (+25)
- `.github/workflows/` (16 files) — PIPELINE workflows — human-owned, agents never edit. ci-pr-gates.yml, conflict-resolver.yml, fix-ci.yml, onboarding-pack.yml, review-agent.yml, stage10-learn.yml, stage3-proposal.yml, stage3-respond.yml … (+8)
- `docs/agent/` (3 files) — Agent memory (this pack, CORRECTIONS.md). CORRECTIONS.md, onboarding-pack.md, pack-curated.md
- `agent/` (9 files) — Agent instruction prompts per stage. conflict-resolution-instructions.md, fix-ci-instructions.md, fix-instructions.md, implementation-instructions.md, proposal-instructions.md, proposal-template.md, review-functional.md, review-security.md … (+1)
- repo root — CLAUDE.md, SETUP.md, pyproject.toml, repo-registry.yaml, requirements.txt

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
- `supabase/migrations/` — 5 file(s): `20261126000000_kalshi_events.sql`, `20261126000100_kalshi_markets.sql`, `20261127000000_whale_trades.sql`, `20261127000001_kalshi_events_url.sql`, `20261128000000_kalshi_markets_winning_side.sql`

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
- `src/kalshi_testing/server.py` — 28 commits
- `src/kalshi_testing/nba_scanner.py` — 24 commits
- `tests/test_nba_scanner.py` — 21 commits
- `tests/test_kalshi_server.py` — 19 commits
- `.env.example` — 9 commits
- `src/kalshi_testing/persistence.py` — 8 commits
- `tests/test_persistence.py` — 7 commits
- `src/lighter_testing/client.py` — 7 commits
- `tests/test_client.py` — 7 commits
- `pyproject.toml` — 6 commits
- `src/lighter_testing/__init__.py` — 6 commits
- `src/lighter_testing/api_keys.py` — 6 commits
- `requirements.txt` — 5 commits
- `SETUP.md` — 4 commits
- `src/lighter_testing/order_debug.py` — 4 commits

Pipeline infrastructure (churn here = pipeline debugging, not app risk):
- `docs/agent/onboarding-pack.md` — 9 commits
- `.github/workflows/stage5-implement.yml` — 5 commits
- `agent/implementation-instructions.md` — 3 commits
- `scripts/build_context_pack.py` — 3 commits
- `scripts/post_proposal.py` — 3 commits

Owner: single service `lighter-testing` (repo-registry.yaml), owner funcoin68, risk tier 3. `.github/workflows/` is human-owned — workflow diffs from an agent are auto-reverted.

## 8. Recent decisions (rolling, last 90 days, from git log)
- 2026-11-28 — feat(persistence): exclude trades priced >= $0.90 from whale alert and insert
- 2026-11-28 — feat(kalshi): raise whale-trade threshold from $1000 to $2499
- 2026-11-27 — feat(nba_scanner): remove money-line title filter from NBA/NFL scan path
- 2026-11-27 — feat(server): sample raw trade payload prints at 5%
- 2026-11-27 — agent: address review comments (round 3)
- 2026-11-27 — agent: address review comments (round 2)
- 2026-11-27 — fix-ci v1.1: create round labels on demand before adding
- 2026-11-28 — Add fix-ci: bounded auto-repair loop for red verification/CI on agent PRs
- 2026-11-27 — Update kalshi-scanner.service
- 2026-11-27 — fix
- 2026-11-27 — fix
- 2026-11-27 — fix
- 2026-11-27 — agent: never revert out-of-scope code — update comments, not code
- 2026-11-27 — agent: address review comments (round 2)
- 2026-11-27 — agent: address review comments (round 2)
- 2026-11-27 — agent: address review comments (round 1)
- 2026-11-27 — chore: regenerate onboarding pack (2026-11-27)
- 2026-11-27 — chore: regenerate onboarding pack (2026-11-27)
- 2026-11-26 — fix
- 2026-11-26 — fix
- (10 git-log entries omitted — subject contains a substring the repo bans from agent-facing docs; see `git log`)

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
