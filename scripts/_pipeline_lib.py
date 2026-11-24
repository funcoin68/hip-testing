"""Shared helpers for pipeline glue scripts. Stdlib only (urllib) — runners
need no pip install. Auth comes from env:
  GH_TOKEN                        GitHub token (GITHUB_TOKEN / AGENT_PUSH_TOKEN / DISPATCH_TOKEN)
  JIRA_BASE_URL, JIRA_USER_EMAIL, JIRA_TOKEN   Jira Cloud basic auth
  GITHUB_REPOSITORY               owner/repo (set automatically on Actions)
"""
import json, os, sys, urllib.request, urllib.error, base64

def _req(method, url, token=None, body=None, basic=None, extra_headers=None, expect_json=True):
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(basic.encode()).decode()
    if extra_headers:
        headers.update(extra_headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return json.loads(raw) if expect_json and raw.strip() else ({} if expect_json else raw)
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:500]
        print(f"HTTP {e.code} {method} {url}\n{detail}", file=sys.stderr)
        raise SystemExit(f"FATAL: {method} {url} -> {e.code}")

def gh(method, path, body=None):
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise SystemExit("FATAL: GITHUB_REPOSITORY not set")
    return _req(method, f"https://api.github.com/repos/{repo}{path}",
                token=os.environ["GH_TOKEN"], body=body,
                extra_headers={"Accept": "application/vnd.github+json"})

def jira(method, path, body=None):
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    basic = f"{os.environ['JIRA_USER_EMAIL']}:{os.environ['JIRA_TOKEN']}"
    return _req(method, f"{base}/rest/api/3{path}", basic=basic, body=body)

def jira_adf(text):
    """Wrap plain text in Atlassian Document Format (one paragraph per line)."""
    return {"version": 1, "type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": line}]}
        for line in text.split("\n") if line.strip()]}

def get_pr(pr):
    return gh("GET", f"/pulls/{pr}")

def add_label(pr, label):
    gh("POST", f"/issues/{pr}/labels", {"labels": [label]})

def remove_label(pr, label):
    try:
        gh("DELETE", f"/issues/{pr}/labels/{label}")
    except SystemExit:
        pass  # label absent is fine

def pr_labels(pr):
    return {l["name"] for l in gh("GET", f"/issues/{pr}/labels")}

def pr_comment(pr, body):
    gh("POST", f"/issues/{pr}/comments", {"body": body})

def upsert_marker_comment(pr, marker, body):
    """Keep ONE comment with `marker` updated in place (packet, digest, etc.)."""
    comments = gh("GET", f"/issues/{pr}/comments?per_page=100")
    full = f"<!-- {marker} -->\n{body}"
    for c in comments:
        if f"<!-- {marker} -->" in (c.get("body") or ""):
            gh("PATCH", f"/issues/comments/{c['id']}", {"body": full})
            return c["id"]
    pr_comment(pr, full)
    return None

def dispatch(workflow, inputs):
    token = os.environ.get("DISPATCH_TOKEN") or os.environ.get("GH_TOKEN")
    repo = os.environ["GITHUB_REPOSITORY"]
    return _req("POST",
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
        token=token, body={"ref": "main", "inputs": {k: str(v) for k, v in inputs.items()}},
        extra_headers={"Accept": "application/vnd.github+json"})

def sha256_text(t):
    import hashlib
    return "sha256:" + hashlib.sha256(t.encode()).hexdigest()

def canon_text(t):
    """Canonical form for the pin hash. jira_adf() drops blank lines when
    building ADF, so both sides must hash with blank lines removed — else the
    pin can never verify (bug #18). Idempotent."""
    return "\n".join(l for l in t.split("\n") if l.strip())
