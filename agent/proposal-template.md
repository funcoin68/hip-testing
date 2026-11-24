---
ticket: {TICKET_KEY}
repos: [{repo1}, {repo2}]
modules_touched: ["path/to/module"]
risk_tier: {1|2|3}
confidence: {high|medium|low}
est_review_minutes: ~10
open_questions: []        # non-empty => BLOCKED until answered
flags:
  touches_tier1: false
  touches_migrations: false
  behind_feature_flag: true
---
## Proposal for {TICKET_KEY}

### 1. Restatement
One paragraph: what we're building and for whom, in your own words.

### 2. Approach
The chosen approach + 1-2 alternatives considered and why rejected.

### 3. Changes by repo
- **{repo1}**:
  - `path/file.ts` — what changes and why (cite what you read)
  - New: `path/new-file.ts` — purpose
- **{repo2}**: ...

### 4. Test plan
- New tests that encode the AC (name them); existing tests affected.
- How you'll verify: `{test_command}` gates done.

### 5. Risks & mitigations
Including any pack/CORRECTIONS gotchas this plan must respect.

### 6. Rollout
Feature flag name, rollout order, rollback path. (Tier-1 services: this
section is mandatory, never "n/a".)

### 7. Out of scope (flagged, not absorbed)
