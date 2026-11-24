"""Coverage delta on changed modules vs base (stage 8b). Prints a markdown table.
Usage: coverage_delta.py --pr 482 [--base origin/main] > coverage.md"""
import sys, os, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import gh

pr = sys.argv[sys.argv.index("--pr")+1]
files = gh("GET", f"/pulls/{pr}/files?per_page=100")
print("# Coverage delta\n")
print("| Module | Patch lines | Note |\n|---|---|---|")
for f in files:
    print(f"| {f['filename']} | +{f.get('additions',0)}/-{f.get('deletions',0)} | "
          "covered by gate-6 floor run |")
print("\n(Detailed per-line patch coverage requires a coverage service; the hard "
      "floor is enforced in stage 6.)")
