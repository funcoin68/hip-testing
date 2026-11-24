# SETUP — hip-testing (Hyperliquid HIP testnet, Python)

Ported 2026-11-24 from funcoin68/chenyujielong (the 12-stage agentic delivery
pipeline). This is the re-setup checklist to make every stage work in the new
repo. Do the steps in order; the pipeline fails loudly on purpose when a step
is missing.

## 0. Push this code

```bash
cd hip-testing
git add -A && git commit -m "port: agentic delivery pipeline from chenyujielong (Python/HIP-testnet adapted)"
git push origin main
```

## 1. GitHub — branch protection

Settings → Branches → add a ruleset (or classic rule) for `main`:
- Require a pull request before merging
- Require status checks: `ci-pr-gates / gates`
- Do NOT allow bypass for the ruleset; the pipeline's pushes go to `agent/*`
  branches via PAT, never to `main`
- Block force pushes

Labels the pipeline expects (create under Issues → Labels if missing):
`diff-cap-signed-off`, `qa-evidence-green`

## 2. GitHub — secrets (Settings → Secrets and variables → Actions)

**Required for the agent stages:**
- `KIMI_CODE_API_KEY` — Kimi Code API key (api.kimi.ai/coding)
- `AGENT_PUSH_TOKEN` — fine-grained PAT, contents:write, **restricted to
  `funcoin68/hip-testing`, `agent/*` branches only** (never `main`, no
  workflow scope — the missing workflow scope is deliberate containment)
- `DISPATCH_TOKEN` — PAT with `actions:write` (used by ci-pr-gates to
  dispatch the review swarm); can be the same fine-grained token if granted
  workflow dispatch on this repo
- `JIRA_TOKEN` — Jira API token (same Atlassian account as below)

**Optional:**
- `SLACK_WEBHOOK_URL` — pipeline notifications
- `METRICS_DB_DSN` — pipeline metrics sink (stage 10)

**Deploy stages (leave unset until you have the targets — stages 8b/9b stay
dormant/fail loudly without them):**
- `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` — only if you add a
  web bundle (repo has no `package.json` today)
- `STAGING_SUPABASE_URL`, `STAGING_SUPABASE_ANON_KEY`, `SUPABASE_ACCESS_TOKEN`,
  `SUPABASE_DB_PASSWORD` — only with Supabase edge functions

## 3. GitHub — repository variables (same page, Variables tab)

- `JIRA_BASE_URL` — e.g. `https://<your-site>.atlassian.net`
- `JIRA_USER_EMAIL` — your Atlassian login
- `JIRA_PROJECT` — new Jira project key you create below (e.g. `HIP`)
- `FRONTIER_MODEL`, `FIXER_MODEL`, `REVIEWER_MODEL` — e.g. `kimi-for-coding`
  (or your k3 endpoint model names)
- `CLAUDE_CODE_VERSION` — leave empty at first; pin to a validated version
  after the first green run (bug #33 lesson from the old repo)
- `STAGING_URL` — a URL the 8b smoke check can curl; only needed when you want
  the QA-evidence gate live (no `package.json` in this repo → Vercel build is
  skipped and this var is the staging target)
- `AUTO_MERGE_ENABLED` — leave unset (off) until metrics justify tier-3
  auto-merge
- `TEST_COMMAND` / `LINT_COMMAND` / `COVERAGE_COMMAND` — leave unset; repo
  defaults are `pytest`, `ruff check . && ruff format --check .`, and
  `pytest --cov=src --cov-report=json`

## 4. Jira (free tier is fine)

1. Create a new project for this repo (e.g. key `HIP`).
2. Recreate the automation rules you used in chenyujielong:
   - **Stage 3:** when a ticket transitions to "Ready for proposal" → send
     web request (POST) to GitHub `workflow_dispatch` for
     `stage3-proposal.yml` with `ticket_key`.
   - **Gate 1 → Stage 5:** when the plan is approved (your approval transition)
     → dispatch `stage5-implement.yml` with `ticket_key` +
     `proposal_comment_id` (the ID of the approved proposal comment).
   - **Gate 2:** the QA evidence label (`qa-evidence-green`) is what makes a PR
     merge-eligible per `check_merge_eligibility.py`; human merge stays the
     default.
3. Issue types/fields: keep the same custom fields you used for risk tier and
   acceptance criteria (stage 6 routing and 8b AC verification read them the
   same way).

## 5. Local development

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                        # tests
ruff check . && ruff format --check .
```

Everything targets the HIP testnet with fake money. Testnet wallet (MetaMask):
`0x2C9651cBaf1CD6bA028ADe56C1D4007B4E51dBDA`. Never commit a private key —
use a gitignored `.env` or CI secrets.

## 6. Pilot ticket (end-to-end smoke of the pipeline)

Same ritual as the chenyujielong pilot:
1. Create Jira ticket with acceptance criteria → automation fires stage 3 →
   proposal comment appears on the ticket.
2. Approve (gate 1) → stage 5 implements on `agent/<ticket>`, opens PR,
   transitions ticket to In review.
3. CI gates + 3-persona review swarm run on the PR; fix rounds capped at 3.
4. Merge → stage 10 post-merge tests run; nightly onboarding-pack refresh
   keeps agent context current.

Watch the first run's logs for the same failure modes the old repo hit
(permissions 403s, missing vars blanking `run:`, rate limits) — the workflow
header comments document each fix if you need to debug.

## 7. What was intentionally changed vs. chenyujielong

- `npm ci` install steps → Python installs (`pip install -e ".[dev]"`),
  node kept only for the claude-code CLI.
- Test/lint/coverage fallbacks → `pytest` / `ruff` / `pytest --cov=src --cov-report=json`.
- stage 8b: three runtime modes — `vercel` (package.json present), `url`
  (vars.STAGING_URL set), `local` (neither: deploys nothing; smoke = full
  local test suite + lint, AC checks via AC_CHECK_<n> repo vars with
  {ENV_URL}=local). Supabase edge-function deploy only in vercel mode.
- stage 9b: deploy jobs are dormant (`if: vars set`) instead of red on every
  push, since this repo has no Supabase/Vercel targets yet.
- `repo-registry.yaml`, `CLAUDE.md`, `docs/agent/pack-curated.md`: rewritten
  for HIP testnet / Python; CORRECTIONS.md reset (none yet).
- `CYJ-6-implementation-notes.md` dropped (ticket-specific history).
