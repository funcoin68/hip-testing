# CLAUDE.md — 12 Rules

Drop this file in your project root. Claude Code / Codex / Cursor / Hermes all read it. Keep it short — past ~200 lines compliance drops sharply.

## Rules

1. **Think before coding.** State assumptions out loud. Surface tradeoffs. Push back when a simpler approach exists. No silent guesses.
2. **Simplicity first.** Minimum code that solves the stated problem. No speculative features. No abstractions for single-use code.
3. **Surgical changes.** Touch only what the task requires. Don't "improve" adjacent code, comments, or formatting. Match existing style.
4. **Goal-driven execution.** Define success criteria up front, then loop until verified. Prefer stating the goal over dictating steps.
5. **Don't make the model do non-language work.** Retries, routing, rate-limiting, arithmetic, time — write deterministic code, not prompts.
6. **Hard token budget.** Every loop gets a ceiling. If the same 8KB input has been re-chewed for 90 minutes, stop and step back.
7. **Surface conflicts, don't average them.** When two parts of the codebase disagree (two error patterns, two state stores), pick one visibly and explain why. Doing both doubles the bug surface.
8. **Read before you write.** Before adding code, read the nearby code. New functions that duplicate existing ones break silently through import order.
9. **Tests are gated by correctness, not "pass."** A passing test on a function returning a constant is not a passing test. Tie assertions to behavior, not shape.
10. **Long-running operations need checkpoints.** Multi-step refactors and migrations commit between steps so one bad turn doesn't require rewinding six.
11. **Convention beats novelty.** In a codebase with an established pattern, use that pattern even when yours is "better." Two patterns are always worse than either alone.
12. **Fail visibly, not silently.** A migration that "completed successfully" while skipping 14% of records on constraint violations is a bug, not a success. Surface partial failure, skipped rows, truncated output, retry exhaustion.

## Project specifics

- Agent context pack: `docs/agent/onboarding-pack.md` (generated — do not edit; edit `docs/agent/pack-curated.md` and let `scripts/build_context_pack.py` / the nightly workflow regenerate)

- Stack: Python 3.12 (package `lighter-testing`, src layout), pytest, ruff (lint + format), GitHub Actions
- Test runner: `pytest`; lint before every commit: `ruff check . && ruff format --check .`
- Coverage gate input: `pytest --cov=src --cov-report=json` (coverage.json)
- Everything runs against the Lighter testnet. Testnet wallet (MetaMask, FAKE money only):
  `0x2C9651cBaf1CD6bA028ADe56C1D4007B4E51dBDA` — never a mainnet address, never real funds
- Never hardcode a private key. Testnet keys come from repo/CI secrets or a gitignored local .env
- Secrets live in GitHub Actions secrets, never in the repo

## Verification checklist

Before returning a task as done:

- [ ] Did I state my assumptions explicitly?
- [ ] Did any change touch code outside the stated scope? If yes, revert or justify.
- [ ] Did any test pass without actually verifying behavior? Re-check assertions.
- [ ] Any partial failure, skipped record, truncated output? Surface it in the summary.
- [ ] If the change touches on-chain interaction: does it target Lighter testnet endpoints only?
