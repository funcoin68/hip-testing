"""Clear reviewed-* / consensus labels before re-arming the swarm on a new push
(PDF stage-7 fix sequence step 7: the metadata job clears old labels so the
aggregator never fires on stale state). Usage: clear_review_labels.py --pr 482"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import pr_labels, remove_label

pr = sys.argv[sys.argv.index("--pr")+1]
for l in pr_labels(pr):
    if l.startswith("reviewed-") or l in ("consensus-passed", "qa-evidence-green"):
        remove_label(pr, l); print(f"cleared label: {l}")
