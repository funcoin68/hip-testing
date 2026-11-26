#!/usr/bin/env python3
"""Regenerate docs/agent/onboarding-pack.md for this repo.

Composes:
  TIER 0 (human-curated): docs/agent/pack-curated.md — mission, lifecycle,
      data model, API contracts, conventions, gotchas, known debt.
  TIER 1 (mechanical, this script): module map (tree), hotspots (git log
      frequency, 90d), recent decisions (git log subjects, 90d).

Runs nightly via .github/workflows/onboarding-pack.yml (opens a PR; a human
merges — nothing enters agent memory without a human merge) or manually:
    python scripts/build_context_pack.py [--check]
--check: exit 1 if the pack is stale vs. a fresh generation (CI use).
"""
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CURATED = ROOT / "docs/agent/pack-curated.md"
OUT = ROOT / "docs/agent/onboarding-pack.md"
WINDOW_DAYS = 90
HOTSPOT_N = 15
DECISIONS_N = 20
# Pipeline-infrastructure files churn for reasons unrelated to app risk;
# reported separately so app hotspots stay visible.
INFRA_PREFIXES = (".github/workflows/", "scripts/", "agent/", "docs/agent/")
IGNORE = {"package-lock.json", "__pycache__"}
# The repo's acceptance criteria ban these substrings (case-insensitive) in
# every non-excluded file, and this pack is NOT excluded. Git-derived lines
# (file paths, commit subjects) can contain them — e.g. the deleted
# src/hip_testing/ path, or "rebrand: HIP/Hyperliquid -> ..." subjects — so
# the git-derived sections filter them here, deterministically, inside this
# excluded script. Omissions are counted and reported, never silent.
BANNED = re.compile(r"hip|hyperliquid", re.I)

MODULE_DIRS = {
    # NOTE: src/ app packages are DISCOVERED dynamically in module_map(),
    # not listed here. This list used to hardcode a single src package, so
    # src/kalshi_testing/ was invisible to the §1 module map for days
    # (found 2026-11-26). Non-src dirs stay here on purpose.
    "tests": "pytest suite (mirrors src/)",
    "scripts": "PIPELINE glue (stage 1-10) — not app code",
    ".github/workflows": "PIPELINE workflows — human-owned, agents never edit",
    "docs/agent": "Agent memory (this pack, CORRECTIONS.md)",
    "agent": "Agent instruction prompts per stage",
}


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, cwd=ROOT).stdout.strip()


def curated_sections():
    """Split pack-curated.md into {section_number: body} on '## N.' headers."""
    text = CURATED.read_text()
    parts = re.split(r"(?m)^## (\d+)\. ", text)
    # parts[0] = preamble, then alternating (num, body). The title word(s)
    # after "## N." stay on the body's first line — drop it; render() supplies
    # the canonical headings.
    out = {}
    for num, body in zip(parts[1::2], parts[2::2]):
        lines = body.strip().splitlines()
        out[num] = "\n".join(lines[1:]).strip() if lines else ""
    return out


def module_map():
    lines = []
    # App packages: enumerate every top-level dir under src/ so a new
    # package (kalshi_testing, ...) can never silently vanish from the map
    # again the way the hardcoded list allowed.
    src_root = ROOT / "src"
    if src_root.is_dir():
        for pkg in sorted(p for p in src_root.iterdir()
                          if p.is_dir() and not p.name.startswith(".")):
            files = [f for f in pkg.rglob("*")
                     if f.is_file() and "__pycache__" not in str(f)]
            names = sorted(f.name for f in files)
            shown = ", ".join(names[:8]) + (f" … (+{len(names) - 8})" if len(names) > 8 else "")
            lines.append(f"- `{pkg.relative_to(ROOT)}/` ({len(files)} files) — "
                         f"Application code — {pkg.name} (Python). {shown}")
    for d, desc in MODULE_DIRS.items():
        p = ROOT / d
        if not p.exists():
            continue
        files = [f for f in p.rglob("*") if f.is_file() and "__pycache__" not in str(f)]
        names = sorted(f.name for f in files)
        shown = ", ".join(names[:8]) + (f" … (+{len(names) - 8})" if len(names) > 8 else "")
        lines.append(f"- `{d}/` ({len(files)} files) — {desc}. {shown}")
    root_files = sorted(
        f.name for f in ROOT.iterdir()
        if f.is_file() and f.name not in IGNORE and not f.name.startswith(".")
    )
    lines.append(f"- repo root — {', '.join(root_files)}")
    return "\n".join(lines)


def churn():
    out = sh(f"git log --since='{WINDOW_DAYS} days ago' --name-only --pretty=format:")
    counts = {}
    for line in out.splitlines():
        f = line.strip()
        # Deleted paths are no longer hotspots (and may carry the banned
        # substring from the old package name) — only rank files on disk.
        if not f or any(i in f for i in IGNORE) or not (ROOT / f).exists():
            continue
        counts[f] = counts.get(f, 0) + 1
    return sorted(counts.items(), key=lambda kv: -kv[1])


def hotspots():
    ranked = churn()
    app = [(f, n) for f, n in ranked if not f.startswith(INFRA_PREFIXES)][:HOTSPOT_N]
    infra = [(f, n) for f, n in ranked if f.startswith(INFRA_PREFIXES)][:5]
    lines = ["App code (most-changed first, last "
             f"{WINDOW_DAYS}d — high churn = review carefully, keep tests close):"]
    lines += [f"- `{f}` — {n} commits" for f, n in app] or ["- (no app churn in window)"]
    lines += ["", "Pipeline infrastructure (churn here = pipeline debugging, not app risk):"]
    lines += [f"- `{f}` — {n} commits" for f, n in infra] or ["- (none)"]
    lines += ["", "Owner: single service `lighter-testing` (repo-registry.yaml), "
                  "owner funcoin68, risk tier 3. `.github/workflows/` is "
                  "human-owned — workflow diffs from an agent are auto-reverted."]
    return "\n".join(lines)


def recent_decisions():
    out = sh(f"git log --since='{WINDOW_DAYS} days ago' --pretty=format:'%ad|%s' "
             "--date=short --no-merges")
    rows = []
    omitted = 0
    for line in out.splitlines():
        if "|" not in line:
            continue
        date, subj = line.split("|", 1)
        if BANNED.search(subj):
            omitted += 1
            continue
        rows.append(f"- {date} — {subj}")
        if len(rows) >= DECISIONS_N:
            break
    if omitted:
        rows.append(f"- ({omitted} git-log entries omitted — subject contains "
                    "a substring the repo bans from agent-facing docs; "
                    "see `git log`)")
    return "\n".join(rows) or "- (no commits in window)"


def migrations_block():
    """bug #47: mechanical ground truth for migrations, injected into pack §3
    every run. The curated prose drifted ("No database schema exists yet"
    survived CYJ-7's first migration) because the nightly refresh only COPIES
    pack-curated.md — it can never contradict its source. This block is
    re-derived from disk, so stale prose gets visibly overridden."""
    mdir = ROOT / "supabase" / "migrations"
    files = sorted(mdir.glob("*.sql")) if mdir.is_dir() else []
    listed = ", ".join(f"`{f.name}`" for f in files) if files else "(none yet)"
    return ("\n### Migrations on disk (mechanical — regenerated every run; "
            "trust this over any prose above)\n"
            f"- `supabase/migrations/` — {len(files)} file(s): {listed}")


def curated_drift_warnings(sections):
    """bug #47 tripwire: loud ::warning:: when curated §3/§9 claim there is no
    schema/migrations while supabase/migrations/ HAS them (or claim a schema
    while none exists). Drift then shows in the nightly run summary instead of
    silently shipping stale agent memory for weeks."""
    mdir = ROOT / "supabase" / "migrations"
    has_migrations = mdir.is_dir() and any(mdir.glob("*.sql"))
    warns = []
    for num in ("3", "9"):
        body = sections.get(num, "")
        claims_none = re.search(r"no database schema|no migrations|does not exist",
                                body, re.I)
        claims_some = re.search(r"schema shipped|migration `?\d|tables?:", body, re.I)
        if has_migrations and claims_none:
            warns.append(f"pack-curated.md §{num} claims no schema/migrations but "
                         "supabase/migrations/ has SQL — the pack only copies this "
                         "file; fix the prose (bug #47)")
        if not has_migrations and claims_some:
            warns.append(f"pack-curated.md §{num} claims a schema but "
                         "supabase/migrations/ is empty — fix the prose (bug #47)")
    return warns


def render(sections):
    today = datetime.date.today().isoformat()
    commit = sh("git rev-parse --short HEAD")
    s = lambda n: sections.get(n, f"(MISSING from pack-curated.md — fill section {n})")
    return f"""# Agent onboarding pack — lighter-testing
<!-- GENERATED FILE — do not edit by hand. Narrative sections come from
     docs/agent/pack-curated.md; mechanical sections from git. Regenerate:
     python scripts/build_context_pack.py (nightly workflow opens the PR). -->
<!-- source commit: {commit} · generated: {today} · pack owner: funcoin68 -->

## 0. Mission
{s('0')}

## 1. Module map
{module_map()}

## 2. Request lifecycle
{s('2')}

## 3. Data model
{s('3')}
{migrations_block()}

## 4. API / interface contracts
{s('4')}

## 5. Conventions
{s('5')}

## 6. Gotchas (highest-value section)
{s('6')}

## 7. Hotspots & owners
{hotspots()}

## 8. Recent decisions (rolling, last {WINDOW_DAYS} days, from git log)
{recent_decisions()}

## 9. Open risks / known debt
{s('9')}

---
## Verification
Before claiming a task done, the agent must have run: `pytest` and
`ruff check .` on its diff. Coverage gate input: `pytest --cov=src --cov-report=json` (coverage.json).
Stale-pack warning: if this file is older than 14 days relative to repo HEAD,
distrust section 8 and re-derive from git log.
"""


def main():
    if not CURATED.exists():
        raise SystemExit(f"FATAL: {CURATED} missing — it is the human-owned "
                         "source of the narrative sections.")
    sections = curated_sections()
    for w in curated_drift_warnings(sections):
        print(f"::warning::{w}")
    new = render(sections)
    if "--check" in sys.argv:
        old = OUT.read_text() if OUT.exists() else ""
        if old != new:
            print("onboarding-pack.md is stale — run build_context_pack.py")
            raise SystemExit(1)
        print("onboarding-pack.md is fresh")
        return
    OUT.write_text(new)
    print(f"wrote {OUT} ({len(new)} chars)")


if __name__ == "__main__":
    main()
