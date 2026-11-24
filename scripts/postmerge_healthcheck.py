"""Post-merge health check (stage 10 validate): probe HEALTHCHECK_URLS
(space-separated) N times; sustained failure -> open a Jira incident (which
lands in #inc-* and feeds the corpus). Usage: postmerge_healthcheck.py --pr 482"""
import sys, os, time, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _pipeline_lib import jira, jira_adf

pr = sys.argv[sys.argv.index("--pr")+1]
urls = os.environ.get("HEALTHCHECK_URLS", "").split()
if not urls:
    print("WARNING: HEALTHCHECK_URLS unset — skipping (wire it to your real endpoints)")
    raise SystemExit(0)
bad = []
for round_ in range(3):
    for u in urls:
        try:
            code = urllib.request.urlopen(u, timeout=10).status
            if code >= 500: bad.append((u, code))
        except Exception as e:
            bad.append((u, str(e)))
    if not bad: break
    time.sleep(30)
if bad and round_ == 2:
    jira("POST", "/issue", {"fields": {
        "project": {"key": os.environ.get("JIRA_PROJECT", "PROJ")},
        "summary": f"Post-merge health regression after PR #{pr}",
        "description": jira_adf("Failures: " + "; ".join(f"{u} -> {c}" for u, c in bad)),
        "issuetype": {"name": "Bug"}, "labels": ["incident", "post-merge-qa"]}})
    raise SystemExit("FAIL: post-merge regression — incident opened")
print("health OK" if not bad else f"transient failures, recovered: {bad}")
