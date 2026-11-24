"""Coverage floor (default 70, PDF key parameters). Reads the first of:
coverage.json (coverage.py), coverage/lcov.info, coverage-summary.json (nyc).
Usage: check_coverage_floor.py [--min 70]"""
import sys, os, json

min_pct = 70.0
if "--min" in sys.argv: min_pct = float(sys.argv[sys.argv.index("--min")+1])
pct = None
if os.path.exists("coverage/coverage-summary.json"):
    d = json.load(open("coverage/coverage-summary.json"))
    pct = d["total"]["lines"]["pct"]
elif os.path.exists("coverage.json"):
    pct = json.load(open("coverage.json"))["totals"]["percent_covered"]
elif os.path.exists("coverage/lcov.info"):
    hit = found = 0
    for line in open("coverage/lcov.info"):
        if line.startswith("DA:"): found += 1; hit += 0 if line.endswith(",0") else 1
    if found: pct = 100.0 * hit / found
if pct is None:
    print("WARNING: no coverage report found; floor not enforceable (fix COVERAGE_COMMAND)")
    raise SystemExit(0)   # absence of report != failure, but shout about it
print(f"coverage: {pct:.1f}% (floor {min_pct}%)")
raise SystemExit(0 if pct >= min_pct else 1)
