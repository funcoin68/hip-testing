"""Post-merge smoke/e2e on the touched modules (stage 10 validate).
Usage: postmerge_smoke.py --pr 482 --env production"""
import sys, os, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

args = sys.argv[1:]
env = args[args.index("--env")+1]
cmd = os.environ.get("SMOKE_COMMAND") or os.environ.get("POSTMERGE_SMOKE_COMMAND")
if not cmd:
    print("WARNING: no SMOKE_COMMAND configured — post-merge smoke skipped (configure it!)")
    raise SystemExit(0)
r = subprocess.run(cmd, shell=True)
raise SystemExit(r.returncode)
