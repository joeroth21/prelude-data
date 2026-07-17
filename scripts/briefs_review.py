"""The Brief — review console.

A local single-page app for the editorial gate: lists the pending cycle's
drafts, renders each piece the way the PRELUDE app will (serif reader,
masthead, drop cap, source chips), allows inline markdown editing with
live lint, per-piece approval, and one PUBLISH button that runs the full
existing gate (re-lint -> reviewed flags -> briefs_cli publish -> push ->
app snapshot refresh -> Pages verification) with a progress log.

Nothing publishes without explicit approval here (or via the CLI fallback,
which remains untouched). Serves on 127.0.0.1 only.

Run:  .venv/Scripts/python scripts/briefs_review.py [--no-browser]
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import requests  # noqa: E402

from prelude_data import briefs_lint  # noqa: E402
from prelude_data.briefs_draft import parse_draft_markdown  # noqa: E402
from prelude_data.briefs_gather import DRAFTS_ROOT  # noqa: E402
from prelude_data.briefs_publish import ReviewGateError, publish_cycle  # noqa: E402

PORT = 8377
APP_REPO = Path(r"C:\Dev\prelude")
PAGES_BRIEFS_URL = "https://joeroth21.github.io/prelude-data/feed/v1/briefs.json"

KIND_LABEL = {
    "status_change": "Status change",
    "valuation_change": "Valuation mark",
    "wrapper_move": "Wrapper watch",
    "wrapper_spotlight": "Wrapper watch",
    "pipeline_wave": "The pipeline",
}

# ---------------------------------------------------------------------------
# Draft plumbing (pure-ish helpers, reusing the tested pipeline machinery)
# ---------------------------------------------------------------------------

def latest_cycle_dir() -> Path | None:
    if not DRAFTS_ROOT.exists():
        return None
    dirs = sorted(p for p in DRAFTS_ROOT.iterdir() if p.is_dir())
    return dirs[-1] if dirs else None


def lint_markdown(markdown: str) -> tuple[dict | None, list[str]]:
    """Parse + lint a draft's markdown. Returns (parsed|None, errors)."""
    try:
        parsed = parse_draft_markdown(markdown)
    except ValueError as exc:
        return None, [f"unparseable draft: {exc}"]
    errors = briefs_lint.lint_draft(
        parsed["title"], parsed["body"], parsed["why_it_matters"], parsed["sources"], []
    )
    return parsed, errors


def load_drafts(cycle: Path) -> list[dict]:
    drafts = []
    for path in sorted(cycle.glob("*.md")):
        markdown = path.read_text(encoding="utf-8")
        parsed, errors = lint_markdown(markdown)
        entry = {
            "file": path.name,
            "markdown": markdown,
            "lint": errors,
            "parsed": parsed,
        }
        drafts.append(entry)
    return drafts


def set_reviewed(path: Path, approved: bool) -> None:
    text = path.read_text(encoding="utf-8")
    new = re.sub(
        r"^reviewed: (true|false)$",
        f"reviewed: {'true' if approved else 'false'}",
        text,
        count=1,
        flags=re.M,
    )
    path.write_text(new, encoding="utf-8", newline="\n")


# ---------------------------------------------------------------------------
# Publish worker — the full existing gate, streamed as a log
# ---------------------------------------------------------------------------

PUBLISH = {"state": "idle", "log": []}
PUBLISH_LOCK = threading.Lock()


def plog(message: str) -> None:
    with PUBLISH_LOCK:
        PUBLISH["log"].append(message)


def run_publish(cycle: Path) -> None:
    with PUBLISH_LOCK:
        PUBLISH["state"] = "running"
        PUBLISH["log"] = []
    try:
        drafts = load_drafts(cycle)
        ids = [d["parsed"]["id"] for d in drafts if d["parsed"]]
        plog(f"cycle {cycle.name}: {len(drafts)} draft(s)")

        unapproved = [d["file"] for d in drafts if not (d["parsed"] and d["parsed"]["reviewed"])]
        if unapproved:
            raise ReviewGateError(f"not approved: {', '.join(unapproved)}")
        plog("review gate: all pieces approved ✓")

        dirty = [d["file"] for d in drafts if d["lint"]]
        if dirty:
            raise ValueError(f"lint failures in: {', '.join(dirty)}")
        plog("lint: clean ✓")

        plog("publishing (briefs_cli gate + assemble + git push) ...")
        publish_cycle(cycle, push=True)
        plog("feed pushed ✓ (baseline advanced)")

        plog("refreshing app snapshot (C:\\Dev\\prelude) ...")
        subprocess.run(
            ["node", "scripts/copy-snapshot.js"], cwd=APP_REPO, check=True, capture_output=True
        )
        commit = subprocess.run(
            ["git", "add", "src/feed/snapshot"], cwd=APP_REPO, capture_output=True
        )
        if commit.returncode == 0:
            result = subprocess.run(
                [
                    "git", "-c", "user.name=Joe Rotherham",
                    "-c", "user.email=joe.rotherham45@gmail.com",
                    "commit", "-m",
                    f"snapshot: Brief cycle {cycle.name}\n\nCo-Authored-By: Claude Fable 5 <noreply@anthropic.com>",
                ],
                cwd=APP_REPO,
                capture_output=True,
                text=True,
            )
            plog("app snapshot committed ✓" if result.returncode == 0 else "app snapshot unchanged")

        plog("verifying Pages ...")
        deadline = time.time() + 300
        while time.time() < deadline:
            try:
                live = requests.get(PAGES_BRIEFS_URL, timeout=15).json()
                live_ids = {b["id"] for b in live.get("briefs", [])}
                if all(i in live_ids for i in ids):
                    plog("live on Pages ✓")
                    with PUBLISH_LOCK:
                        PUBLISH["state"] = "done"
                    return
            except Exception:  # noqa: BLE001 — polling; report only on timeout
                pass
            time.sleep(10)
        plog("WARNING: published and pushed, but Pages did not confirm within 5 minutes — check manually")
        with PUBLISH_LOCK:
            PUBLISH["state"] = "done"
    except (ReviewGateError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        plog(f"REFUSED / FAILED: {exc}")
        with PUBLISH_LOCK:
            PUBLISH["state"] = "failed"


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # quiet
        pass

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            body = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/api/cycle":
            cycle = latest_cycle_dir()
            if cycle is None:
                self._json({"cycle": None, "drafts": []})
                return
            self._json({"cycle": cycle.name, "drafts": load_drafts(cycle)})
        elif self.path == "/api/publish/status":
            with PUBLISH_LOCK:
                self._json(dict(PUBLISH))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        cycle = latest_cycle_dir()
        if cycle is None:
            self._json({"error": "no cycle"}, 400)
            return
        if self.path == "/api/lint":
            data = self._read_body()
            parsed, errors = lint_markdown(data.get("markdown", ""))
            self._json({"lint": errors, "parsed": parsed})
        elif self.path == "/api/save":
            data = self._read_body()
            name = Path(data["file"]).name  # no traversal
            target = cycle / name
            if not target.exists():
                self._json({"error": "unknown draft"}, 404)
                return
            target.write_text(data["markdown"], encoding="utf-8", newline="\n")
            parsed, errors = lint_markdown(data["markdown"])
            self._json({"saved": True, "lint": errors, "parsed": parsed})
        elif self.path == "/api/approve":
            data = self._read_body()
            name = Path(data["file"]).name
            target = cycle / name
            if not target.exists():
                self._json({"error": "unknown draft"}, 404)
                return
            set_reviewed(target, bool(data.get("approved")))
            parsed, errors = lint_markdown(target.read_text(encoding="utf-8"))
            self._json({"approved": bool(data.get("approved")), "lint": errors, "parsed": parsed})
        elif self.path == "/api/publish":
            with PUBLISH_LOCK:
                if PUBLISH["state"] == "running":
                    self._json({"started": False, "reason": "already running"})
                    return
            threading.Thread(target=run_publish, args=(cycle,), daemon=True).start()
            self._json({"started": True})
        else:
            self._json({"error": "not found"}, 404)


# ---------------------------------------------------------------------------
# The page — dark ink, gold, serif reader. Mirrors the app's Brief reader.
# ---------------------------------------------------------------------------

PAGE = r"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>The Brief — Review Console</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link href="https://fonts.googleapis.com/css2?family=Archivo:wght@400;500;600;700&family=Source+Serif+4:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
<style>
:root{--ink:#08090B;--panel:#0E1014;--surface:#12151A;--high:#191D24;--hair:rgba(232,230,225,.07);
--hairs:rgba(201,169,97,.22);--gold:#C9A961;--goldf:rgba(201,169,97,.12);--ongold:#14100A;
--text:#E8E6E1;--muted:rgba(232,230,225,.6);--faint:rgba(232,230,225,.34);--down:#C96161;--up:#5F9E83}
*{box-sizing:border-box;margin:0}
body{background:var(--ink);color:var(--text);font-family:Archivo,sans-serif;font-size:14px}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 80px}
.mast{text-align:center;border-top:1px solid var(--hairs);border-bottom:1px solid var(--hairs);padding:22px 0;margin-bottom:26px}
.mast h1{font-weight:700;font-size:26px;letter-spacing:10px}
.mast .sub{font-family:'Source Serif 4',serif;font-style:italic;color:var(--gold);font-size:14px;margin-top:6px}
.cols{display:grid;grid-template-columns:330px 1fr;gap:26px}
.list .draft{background:var(--surface);border:1px solid var(--hair);border-radius:12px;padding:14px 16px;margin-bottom:10px;cursor:pointer}
.list .draft.active{border-color:var(--gold)}
.list .kick{font-size:9.5px;letter-spacing:2px;color:var(--gold);text-transform:uppercase;font-weight:600}
.list .t{font-family:'Source Serif 4',serif;font-weight:600;font-size:15.5px;line-height:1.3;margin:4px 0 8px}
.chips{display:flex;gap:6px;flex-wrap:wrap}
.chip{font-size:9.5px;letter-spacing:1px;font-weight:700;text-transform:uppercase;border-radius:99px;padding:3px 9px;border:1px solid var(--hair);color:var(--muted)}
.chip.ok{border-color:var(--up);color:var(--up)}
.chip.bad{border-color:var(--down);color:var(--down)}
.chip.approved{background:var(--gold);border-color:var(--gold);color:var(--ongold)}
.reader{background:var(--panel);border:1px solid var(--hairs);border-radius:16px;padding:34px 40px}
.reader .kick{font-size:11px;letter-spacing:2px;color:var(--gold);text-transform:uppercase;font-weight:600}
.reader h2{font-family:'Source Serif 4',serif;font-weight:600;font-size:27px;line-height:1.25;margin-top:8px}
.rule{width:44px;height:2px;background:var(--gold);margin:22px 0}
.reader p{font-family:'Source Serif 4',serif;font-size:16.5px;line-height:1.65;margin-bottom:16px}
.reader p:first-of-type::first-letter{font-weight:600;font-size:26px;color:var(--gold)}
.why{border-left:2px solid var(--gold);padding:6px 0 6px 16px;margin:8px 0 24px}
.why p{font-style:italic;font-size:15.5px;color:var(--muted);margin:0}
.srchead{font-size:10.5px;letter-spacing:1.8px;color:var(--faint);text-transform:uppercase;font-weight:600;
display:flex;align-items:center;gap:12px;margin:22px 0 10px}
.srchead:after{content:'';flex:1;height:1px;background:var(--hair)}
.srcchips{display:flex;gap:8px;flex-wrap:wrap}
.srcchip{display:inline-flex;gap:6px;align-items:center;border:1px solid var(--hairs);background:var(--goldf);
border-radius:6px;padding:3px 8px;text-decoration:none}
.srcchip .l{font-size:8.5px;letter-spacing:1.2px;text-transform:uppercase;color:var(--gold);font-weight:600}
.srcchip .u{font-family:'IBM Plex Mono',monospace;font-size:10px;color:var(--muted)}
.panel{background:var(--surface);border:1px solid var(--hair);border-radius:12px;padding:16px 18px;margin-top:18px}
.panel h3{font-size:10.5px;letter-spacing:1.8px;text-transform:uppercase;color:var(--faint);margin-bottom:10px}
.lint-ok{color:var(--up);font-family:'IBM Plex Mono',monospace;font-size:12px}
.lint-bad{color:var(--down);font-family:'IBM Plex Mono',monospace;font-size:12px;line-height:1.6}
textarea{width:100%;min-height:340px;background:var(--ink);color:var(--text);border:1px solid var(--hair);
border-radius:8px;padding:12px;font-family:'IBM Plex Mono',monospace;font-size:12px;line-height:1.55}
.btn{border:none;border-radius:9px;padding:10px 18px;font-family:Archivo;font-weight:600;font-size:13px;
letter-spacing:.4px;cursor:pointer;background:var(--surface);color:var(--text);border:1px solid var(--hairs)}
.btn.primary{background:var(--gold);color:var(--ongold);border-color:var(--gold)}
.btn:disabled{opacity:.35;cursor:default}
.row{display:flex;gap:10px;align-items:center;margin-top:12px;flex-wrap:wrap}
.approve{display:flex;align-items:center;gap:8px;margin-left:auto;font-weight:600;font-size:13px;cursor:pointer}
.approve input{width:18px;height:18px;accent-color:var(--gold)}
.publishbar{position:sticky;bottom:0;background:var(--panel);border-top:1px solid var(--hairs);
margin:26px -20px -80px;padding:16px 20px;display:flex;gap:16px;align-items:center}
.publishbar .status{font-family:'IBM Plex Mono',monospace;font-size:11.5px;color:var(--muted)}
.log{font-family:'IBM Plex Mono',monospace;font-size:11.5px;line-height:1.7;color:var(--muted);
background:var(--ink);border:1px solid var(--hair);border-radius:8px;padding:12px;margin-top:12px;
max-height:220px;overflow:auto;white-space:pre-wrap}
.live{color:var(--up);font-weight:700}
.dis{font-size:10px;color:var(--faint);text-align:center;margin-top:30px;letter-spacing:.4px}
</style></head><body><div class="wrap">
<div class="mast"><h1>THE BRIEF</h1><div class="sub">Review console — nothing publishes without your approval</div></div>
<div class="cols">
  <div class="list" id="list"></div>
  <div>
    <div class="reader" id="reader"><p style="font-family:Archivo;color:var(--faint)">Loading cycle…</p></div>
    <div class="panel"><h3>Verification — sources & lint</h3><div id="verify"></div></div>
    <div class="panel"><h3>Edit markdown</h3>
      <textarea id="editor" spellcheck="true"></textarea>
      <div class="row">
        <button class="btn" id="save">Save draft</button>
        <span id="editlint" class="lint-ok"></span>
      </div>
    </div>
  </div>
</div>
<div class="publishbar">
  <button class="btn primary" id="publish" disabled>PUBLISH CYCLE</button>
  <span class="status" id="pubstatus">approve every piece to enable</span>
</div>
<div class="log" id="publog" style="display:none"></div>
<div class="dis">Educational information, not investment advice. PRELUDE recommends nothing.</div>
</div>
<script>
let CYCLE=null,DRAFTS=[],SEL=0,POLL=null;
const $=id=>document.getElementById(id);
const KINDS={status_change:'Status change',valuation_change:'Valuation mark',wrapper_move:'Wrapper watch',wrapper_spotlight:'Wrapper watch',pipeline_wave:'The pipeline'};
const host=u=>{try{const h=new URL(u).hostname.replace(/^www\./,'');return h==='sec.gov'?'EDGAR':h.split('.')[0]}catch{return 'src'}};
async function api(p,body){const r=await fetch(p,body?{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{});return r.json()}
async function refresh(keepSel){const d=await api('/api/cycle');CYCLE=d.cycle;DRAFTS=d.drafts;if(!keepSel)SEL=0;renderList();renderReader();renderPublishBar()}
function renderList(){
  $('list').innerHTML = DRAFTS.length? DRAFTS.map((d,i)=>{
    const p=d.parsed||{},ok=d.lint.length===0;
    return `<div class="draft ${i===SEL?'active':''}" onclick="select(${i})">
      <div class="kick">${KINDS[p.kind]||p.kind||'draft'}</div>
      <div class="t">${p.title||d.file}</div>
      <div class="chips">
        <span class="chip ${ok?'ok':'bad'}">${ok?'lint clean':d.lint.length+' lint'}</span>
        <span class="chip ${p.reviewed?'approved':''}">${p.reviewed?'approved':'pending'}</span>
      </div></div>`}).join('') : '<div class="draft">No pending cycle.</div>';
}
function renderReader(){
  const d=DRAFTS[SEL]; if(!d){$('reader').innerHTML='<p style="font-family:Archivo;color:var(--faint)">No drafts.</p>';return}
  const p=d.parsed;
  if(!p){$('reader').innerHTML='<p class="lint-bad">'+d.lint.join('<br>')+'</p>';$('editor').value=d.markdown;return}
  const paras=p.body.split('\n\n').map(x=>`<p>${x}</p>`).join('');
  $('reader').innerHTML=`<div class="kick">${KINDS[p.kind]||p.kind} · ${p.date}</div>
    <h2>${p.title}</h2><div class="rule"></div>${paras}
    <div class="why"><p>${p.why_it_matters}</p></div>
    <div class="srchead">Sources</div>
    <div class="srcchips">${p.sources.map(s=>`<a class="srcchip" href="${s}" target="_blank"><span class="l">${host(s)}</span><span class="u">${p.date}</span></a>`).join('')}</div>`;
  $('verify').innerHTML=(d.lint.length===0?'<div class="lint-ok">lint clean ✓</div>':'<div class="lint-bad">'+d.lint.map(e=>'✕ '+e).join('<br>')+'</div>')+
    '<div class="srchead" style="margin-top:14px">Verify against</div>'+
    '<div class="srcchips">'+p.sources.map(s=>`<a class="srcchip" href="${s}" target="_blank"><span class="l">${host(s)}</span><span class="u">${s.length>52?s.slice(0,52)+'…':s}</span></a>`).join('')+'</div>'+
    `<div class="row"><label class="approve"><input type="checkbox" ${p.reviewed?'checked':''} onchange="approve(this.checked)"> Approve this piece</label></div>`;
  $('editor').value=d.markdown; $('editlint').textContent='';
}
function renderPublishBar(){
  const all=DRAFTS.length>0&&DRAFTS.every(d=>d.parsed&&d.parsed.reviewed&&d.lint.length===0);
  $('publish').disabled=!all;
  $('pubstatus').textContent=all?`${DRAFTS.length} pieces approved — ready`:
    `${DRAFTS.filter(d=>d.parsed&&d.parsed.reviewed).length}/${DRAFTS.length} approved — approve every lint-clean piece to enable`;
}
function select(i){SEL=i;renderList();renderReader()}
async function approve(v){const d=DRAFTS[SEL];const r=await api('/api/approve',{file:d.file,approved:v});
  d.parsed=r.parsed;d.lint=r.lint;renderList();renderPublishBar()}
$('save').onclick=async()=>{const d=DRAFTS[SEL];const r=await api('/api/save',{file:d.file,markdown:$('editor').value});
  d.markdown=$('editor').value;d.parsed=r.parsed;d.lint=r.lint;renderList();renderReader();renderPublishBar()};
let lintTimer=null;
$('editor').addEventListener('input',()=>{clearTimeout(lintTimer);lintTimer=setTimeout(async()=>{
  const r=await api('/api/lint',{markdown:$('editor').value});
  $('editlint').className=r.lint.length?'lint-bad':'lint-ok';
  $('editlint').textContent=r.lint.length?r.lint.join(' · '):'lint clean ✓ (unsaved)';},600)});
$('publish').onclick=async()=>{
  $('publish').disabled=true;$('publog').style.display='block';
  await api('/api/publish',{});
  POLL=setInterval(async()=>{const s=await api('/api/publish/status');
    $('publog').innerHTML=s.log.map(l=>l.includes('live on Pages')?`<span class="live">${l}</span>`:l).join('\n');
    $('pubstatus').textContent=s.state==='running'?'publishing…':s.state;
    if(s.state!=='running'){clearInterval(POLL);if(s.state==='done')$('pubstatus').textContent='live on Pages ✓';refresh(true)}},1200)};
refresh();
</script></body></html>
"""


def main() -> int:
    no_browser = "--no-browser" in sys.argv
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError:
        # already running — just open the console
        if not no_browser:
            webbrowser.open(f"http://localhost:{PORT}/")
        return 0
    if not no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://localhost:{PORT}/")).start()
    print(f"The Brief review console: http://localhost:{PORT}/")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
