"""Push the outcome record to your metrics sink. Default: append to
outcomes/metrics.jsonl (works with zero infra); METRICS_DB_DSN set -> POST there.
Usage: push_metrics.py --pr 482"""
import sys, os, glob, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import _req

pr = sys.argv[sys.argv.index("--pr")+1]
files = sorted(glob.glob(f"outcomes/*-pr{pr}.json"))
if not files: raise SystemExit("no outcome record found — capture job must run first")
rec = json.load(open(files[-1]))
dsn = os.environ.get("METRICS_DB_DSN", "")
if dsn:
    _req("POST", dsn, body=rec); print("metrics pushed to DSN")
else:
    os.makedirs("outcomes", exist_ok=True)
    with open("outcomes/metrics.jsonl", "a") as f: f.write(json.dumps(rec) + "\n")
    print("metrics appended to outcomes/metrics.jsonl (local sink)")
