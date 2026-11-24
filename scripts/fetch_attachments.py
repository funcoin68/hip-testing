"""Fetch Jira ticket attachments and render them agent-readable (bug #34).

Stage-3/5 agents run with NO Jira credentials by design (bug #19 containment),
so an attachment link in the ticket is a dead end for them — "JIRA attachments
are not reachable from my environment." This script runs in the workflow step
that DOES hold JIRA_* creds, downloads each attachment via the Jira content
API, and converts it to something the agent can consume:

  text-like (.md/.txt/.csv/.json/.yaml/.log, mime text/*) -> inline markdown
  .pdf   -> text via pypdf (workflow pip-installs it; degrades to a note)
  .docx  -> text via stdlib zipfile + XML strip (no dependency)
  images (.png/.jpg/.jpeg/.gif/.webp) -> saved to disk, path listed so a
            multimodal agent can Read the file
  others -> listed as skipped

Output: <out>/index.md digest (inlined into context.md by assemble_context.py)
plus one extracted .md per attachment (agent can Read the full file if the
inline cap truncated it). Never fails the build: per-file errors are notes,
exit code stays 0.

Usage: fetch_attachments.py ticket.json [--out /tmp/attachments]"""
import sys, os, json, base64, zipfile, re, urllib.request, urllib.error

MAX_BYTES = 10 * 1024 * 1024      # skip downloads above 10 MB
INLINE_CAP = 12000                # chars inlined per file in index.md
TEXT_EXT = {".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".log", ".xml", ".ts", ".js"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

def download(att_id):
    base = os.environ["JIRA_BASE_URL"].rstrip("/")
    basic = f"{os.environ['JIRA_USER_EMAIL']}:{os.environ['JIRA_TOKEN']}"
    req = urllib.request.Request(f"{base}/rest/api/3/attachment/content/{att_id}")
    req.add_header("Authorization", "Basic " + base64.b64encode(basic.encode()).decode())
    req.add_header("X-Atlassian-Token", "no-check")   # Jira's own docs recommend this for file GETs
    with urllib.request.urlopen(req, timeout=60) as r:   # follows the media redirect
        return r.read()

def docx_text(raw):
    with zipfile.ZipFile(__import__("io").BytesIO(raw)) as z:
        xml = z.read("word/document.xml").decode("utf-8", "replace")
    xml = re.sub(r"</w:p>", "\n", xml)                 # paragraph breaks
    return re.sub(r"<[^>]+>", "", xml).strip()

def pdf_text(raw):
    try:
        import io, pypdf
        return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(io.BytesIO(raw)).pages).strip()
    except ImportError:
        return "(PDF text extraction unavailable — pypdf not installed in the fetch step)"

def main():
    ticket = json.load(open(sys.argv[1]))
    out_dir = "/tmp/attachments"
    if "--out" in sys.argv:
        out_dir = sys.argv[sys.argv.index("--out") + 1]
    atts = ticket.get("attachments") or []
    if not atts:
        print("no attachments on ticket")
        return
    os.makedirs(out_dir, exist_ok=True)
    idx = []
    for i, a in enumerate(atts, 1):
        name = a.get("filename") or f"attachment-{a.get('id', i)}"
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        ext = os.path.splitext(name)[1].lower()
        size = a.get("size") or 0
        header = f"### {i}. {name} ({a.get('mimeType', '?')}, {size} bytes)"
        if size > MAX_BYTES:
            idx.append(f"{header}\n\nSKIPPED — larger than {MAX_BYTES // 1048576} MB cap.")
            continue
        try:
            raw = download(a["id"])
        except Exception as e:
            idx.append(f"{header}\n\nDOWNLOAD FAILED: {e}")
            continue
        if ext in IMG_EXT or (a.get("mimeType") or "").startswith("image/"):
            path = os.path.join(out_dir, f"{i:02d}-{safe}")
            open(path, "wb").write(raw)
            idx.append(f"{header}\n\nImage saved at `{path}` — Read this file directly if you support vision.")
            continue
        if ext == ".docx":
            text = docx_text(raw)
        elif ext == ".pdf":
            text = pdf_text(raw)
        elif ext in TEXT_EXT or (a.get("mimeType") or "").startswith("text/"):
            text = raw.decode("utf-8", "replace")
        else:
            idx.append(f"{header}\n\nSKIPPED — unsupported type (no text extractor).")
            continue
        text_path = os.path.join(out_dir, f"{i:02d}-{safe}" + ("" if safe.lower().endswith(".md") else ".md"))
        open(text_path, "w").write(text)
        body = text[:INLINE_CAP]
        note = (f"\n\n[truncated at {INLINE_CAP} chars — full text at `{text_path}`]"
                if len(text) > INLINE_CAP else "")
        idx.append(f"{header}\n\nFull text also at `{text_path}`.\n\n{body}{note}")
    open(os.path.join(out_dir, "index.md"), "w").write("\n\n".join(idx))
    print(f"{len(atts)} attachment(s) processed -> {out_dir}/index.md")

if __name__ == "__main__":
    main()
