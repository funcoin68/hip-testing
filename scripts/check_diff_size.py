"""Diff-size cap (default 600 lines, PDF stage 6). Excludes lockfiles, generated
code, snapshots, and test fixtures — the cap is on hand-written surface.
Usage: check_diff_size.py [--max-lines 600] [--base origin/main]"""
import subprocess, sys, re

mx = 600; base = "origin/main"
if "--max-lines" in sys.argv: mx = int(sys.argv[sys.argv.index("--max-lines")+1])
if "--base" in sys.argv: base = sys.argv[sys.argv.index("--base")+1]
EXCLUDE = re.compile(r"(package-lock\.json|pnpm-lock\.yaml|yarn\.lock|\.snap$|"
                     r"/generated/|\.min\.(js|css)$|__fixtures__/)")
out = subprocess.run(["git", "diff", "--numstat", f"{base}...HEAD"],
                     capture_output=True, text=True, check=True).stdout
total = 0; counted = []
for line in out.strip().splitlines():
    a, d, path = line.split("\t", 2)
    if a == "-" or EXCLUDE.search(path): continue
    total += int(a) + int(d); counted.append(path)
print(f"diff size: {total} changed lines across {len(counted)} files (cap {mx})")
if total > mx:
    print("FAIL: diff explosion — split the ticket or get human sign-off to raise the cap")
    raise SystemExit(1)
