#!/usr/bin/env python3
"""Local PR review UI. GitHub-style split diff, line comments, submit-to-file.

Usage:
  python3 server.py <pr-number> [--repo OWNER/REPO] [--port 8765] [--out PATH]
  python3 server.py --diff-file some.patch [--port 8765] [--out PATH]

On Submit the page POSTs all comments to /submit; the server writes them to
--out (JSON) and prints them to stdout. Watch that file to collect the review.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GENERATED = re.compile(r"\.(g|gr|gql|freezed|config|mocks|fakes|req|data|var|schema)\.dart$|\.lock$|\.g\.dart$|\.pb\.dart$")


def sh(args):
    out = subprocess.run(args, capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout


def get_diff(pr, repo, diff_file):
    if diff_file:
        with open(diff_file) as f:
            return f.read()
    args = ["gh", "pr", "diff", str(pr)]
    if repo:
        args += ["--repo", repo]
    return sh(args)


def source_sig(diff_text):
    return hashlib.sha1(diff_text.encode("utf-8")).hexdigest()


def post_pr_comment(g, c):
    """Post one inline review comment on the PR. Returns (html_url, None) or (None, error)."""
    side = "LEFT" if c.get("side") == "L" else "RIGHT"
    args = ["gh", "api", "--method", "POST",
            f"/repos/{g['owner']}/{g['repo']}/pulls/{g['pr']}/comments",
            "-f", f"body={c.get('text', '')}",
            "-f", f"commit_id={g['sha']}",
            "-f", f"path={c.get('file', '')}",
            "-F", f"line={int(c.get('line'))}",
            "-f", f"side={side}"]
    try:
        out = subprocess.run(args, capture_output=True, text=True)
        if out.returncode != 0:
            return None, (out.stderr.strip() or "gh api failed")
        return json.loads(out.stdout).get("html_url", ""), None
    except Exception as e:
        return None, str(e)


def resolve_gh(pr, repo, diff_file):
    """Owner/repo/pr/head-sha for posting inline comments, or None when there is no PR."""
    if diff_file or not pr:
        return None
    args = ["gh", "pr", "view", str(pr), "--json", "url,number,headRefOid"]
    if repo:
        args += ["--repo", repo]
    out = sh(args)  # RuntimeError (gh failed) propagates to main()'s handler
    try:
        j = json.loads(out)
        m = re.match(r"https?://github\.com/([^/]+)/([^/]+)/pull/", j.get("url", ""))
        if not m:
            return None
        return {"owner": m.group(1), "repo": m.group(2), "pr": j.get("number", pr), "sha": j["headRefOid"]}
    except Exception:
        return None


def get_meta(pr, repo, diff_file):
    if diff_file or not pr:
        return {"title": diff_file or "local diff", "url": "", "number": pr or ""}
    args = ["gh", "pr", "view", str(pr), "--json", "title,url,number"]
    if repo:
        args += ["--repo", repo]
    out = sh(args)  # RuntimeError (gh failed) propagates to main()'s handler
    try:
        return json.loads(out)
    except Exception:
        return {"title": f"PR #{pr}", "url": "", "number": pr}


HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


def parse_diff(text):
    files = []
    cur = None
    hunk = None
    old_ln = new_ln = 0
    pend_del = []
    pend_add = []

    def flush_pairs():
        nonlocal pend_del, pend_add
        n = max(len(pend_del), len(pend_add))
        for k in range(n):
            left = pend_del[k] if k < len(pend_del) else {"t": "empty"}
            right = pend_add[k] if k < len(pend_add) else {"t": "empty"}
            hunk["rows"].append({"l": left, "r": right})
        pend_del, pend_add = [], []

    for raw in text.split("\n"):
        if raw.startswith("diff --git"):
            flush_pairs() if hunk else None
            m = re.match(r"diff --git a/(.*) b/(.*)$", raw)
            cur = {
                "old": m.group(1) if m else "",
                "new": m.group(2) if m else "",
                "display": (m.group(2) if m else ""),
                "status": "modified",
                "generated": bool(GENERATED.search(m.group(2))) if m else False,
                "binary": False,
                "hunks": [],
                "adds": 0,
                "dels": 0,
            }
            files.append(cur)
            hunk = None
            continue
        if cur is None:
            continue
        if raw.startswith("new file mode"):
            cur["status"] = "added"
            continue
        if raw.startswith("deleted file mode"):
            cur["status"] = "deleted"
            continue
        if raw.startswith("rename from") or raw.startswith("rename to") or raw.startswith("copy "):
            cur["status"] = "renamed"
            continue
        if raw.startswith("Binary files"):
            cur["binary"] = True
            continue
        if raw.startswith("--- "):
            continue
        if raw.startswith("+++ "):
            continue
        hm = HUNK.match(raw)
        if hm:
            if hunk:
                flush_pairs()
            old_ln = int(hm.group(1))
            new_ln = int(hm.group(3))
            hunk = {"header": raw, "section": hm.group(5).strip(), "rows": []}
            cur["hunks"].append(hunk)
            continue
        if hunk is None:
            continue
        if raw.startswith("\\"):  # \ No newline at end of file
            continue
        tag = raw[:1]
        body = raw[1:]
        if tag == " ":
            flush_pairs()
            hunk["rows"].append({
                "l": {"t": "ctx", "n": old_ln, "s": body},
                "r": {"t": "ctx", "n": new_ln, "s": body},
            })
            old_ln += 1
            new_ln += 1
        elif tag == "-":
            pend_del.append({"t": "del", "n": old_ln, "s": body})
            old_ln += 1
            cur["dels"] += 1
        elif tag == "+":
            pend_add.append({"t": "add", "n": new_ln, "s": body})
            new_ln += 1
            cur["adds"] += 1
    if hunk:
        flush_pairs()
    return files


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{
  --bg:#f4f6f8; --surface:#ffffff; --surface2:#eef1f4; --border:#d7dde3;
  --text:#1b222c; --dim:#63707e; --accent:#b26a00; --accent-bg:rgba(232,163,23,.13);
  --add-bg:rgba(46,160,67,.12); --add-gut:rgba(46,160,67,.22); --add-num:#1a7f37;
  --del-bg:rgba(207,34,46,.10); --del-gut:rgba(207,34,46,.18); --del-num:#b3202b;
  --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  --ui:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
}
@media (prefers-color-scheme:dark){:root{
  --bg:#0c1320; --surface:#0f1826; --surface2:#0b111b; --border:#1e2a3a;
  --text:#d6deea; --dim:#8493a5; --accent:#e8a317; --accent-bg:rgba(232,163,23,.14);
  --add-bg:rgba(46,160,67,.16); --add-gut:rgba(46,160,67,.28); --add-num:#3fb950;
  --del-bg:rgba(248,81,73,.15); --del-gut:rgba(248,81,73,.26); --del-num:#f85149;
}}
:root[data-theme=dark]{
  --bg:#0c1320; --surface:#0f1826; --surface2:#0b111b; --border:#1e2a3a;
  --text:#d6deea; --dim:#8493a5; --accent:#e8a317; --accent-bg:rgba(232,163,23,.14);
  --add-bg:rgba(46,160,67,.16); --add-gut:rgba(46,160,67,.28); --add-num:#3fb950;
  --del-bg:rgba(248,81,73,.15); --del-gut:rgba(248,81,73,.26); --del-num:#f85149;}
:root[data-theme=light]{
  --bg:#f4f6f8; --surface:#ffffff; --surface2:#eef1f4; --border:#d7dde3;
  --text:#1b222c; --dim:#63707e; --accent:#b26a00; --accent-bg:rgba(232,163,23,.13);
  --add-bg:rgba(46,160,67,.12); --add-gut:rgba(46,160,67,.22); --add-num:#1a7f37;
  --del-bg:rgba(207,34,46,.10); --del-gut:rgba(207,34,46,.18); --del-num:#b3202b;}
/* dart token colors */
:root{--tk-k:#7c3aed;--tk-s:#0a7d3f;--tk-c:#6a737d;--tk-n:#b3560f;--tk-t:#1668b8;--tk-an:#b26a00}
@media (prefers-color-scheme:dark){:root{--tk-k:#c792ea;--tk-s:#7ee787;--tk-c:#8b98a5;--tk-n:#f0a35e;--tk-t:#6cb6ff;--tk-an:#e8a317}}
:root[data-theme=dark]{--tk-k:#c792ea;--tk-s:#7ee787;--tk-c:#8b98a5;--tk-n:#f0a35e;--tk-t:#6cb6ff;--tk-an:#e8a317}
:root[data-theme=light]{--tk-k:#7c3aed;--tk-s:#0a7d3f;--tk-c:#6a737d;--tk-n:#b3560f;--tk-t:#1668b8;--tk-an:#b26a00}
*{box-sizing:border-box}
html,body{margin:0}
body{background:var(--bg);color:var(--text);font-family:var(--ui);font-size:14px;line-height:1.5}
header{position:sticky;top:0;z-index:20;background:var(--surface);border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:14px;padding:10px 18px;flex-wrap:nowrap}
header .title{font-weight:650;font-size:15px;line-height:1.35;min-width:0;flex:1 1 0;
  display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2;overflow:hidden;
  word-break:break-word}
header .title.clamped{-webkit-mask-image:linear-gradient(to bottom,#000 48%,transparent 96%);
  mask-image:linear-gradient(to bottom,#000 48%,transparent 96%)}
header .title a{color:var(--accent);text-decoration:none}
header .controls{flex:0 1 auto;min-width:0;display:flex;align-items:center;gap:12px;
  flex-wrap:wrap;justify-content:flex-end}
header .brand{flex:none;display:flex;align-items:center;white-space:nowrap;
  font-weight:800;font-size:26px;line-height:1;letter-spacing:.2px;color:var(--accent);
  user-select:none;padding-left:2px}
header .brand .tm{font-size:.42em;font-weight:700;align-self:flex-start;margin-top:.15em;margin-left:1px}
.btn{font:inherit;color:var(--text);background:var(--surface2);border:1px solid var(--border);
  border-radius:7px;padding:6px 12px;cursor:pointer}
.btn:hover{border-color:var(--accent)}
.btn.primary{background:var(--accent);color:#fff;border-color:transparent;font-weight:600}
.btn.primary:disabled{opacity:.5;cursor:not-allowed}
.btn.approve{background:var(--add-num);color:#fff;border-color:transparent;font-weight:600}
.btn.approve:hover{filter:brightness(1.05)}
.btn.refresh{background:var(--accent);color:#fff;border-color:transparent;font-weight:600;
  animation:pulse 2s ease-in-out infinite}
.btn.refresh:hover{filter:brightness(1.05)}
.btn.refresh:disabled{opacity:.6;cursor:progress;animation:none}
@keyframes pulse{0%,100%{box-shadow:0 0 0 0 var(--accent-bg)}50%{box-shadow:0 0 0 5px var(--accent-bg)}}
.count{color:var(--dim);font-variant-numeric:tabular-nums}
main{max-width:1180px;margin:0 auto;padding:18px}
.file{background:var(--surface);border:1px solid var(--border);border-radius:10px;margin-bottom:16px}
.file-head{display:flex;align-items:center;gap:10px;padding:9px 12px;cursor:pointer;
  position:sticky;top:52px;background:var(--surface);border-bottom:1px solid var(--border);
  border-radius:10px 10px 0 0;z-index:10}
.file-head .chev{color:var(--dim);transition:transform .15s;flex:none}
.file.collapsed .chev{transform:rotate(-90deg)}
.file.collapsed .file-body{display:none}
.file-head .path{font-family:var(--mono);font-size:12.5px;word-break:break-all}
.file-head .badge{font-size:11px;color:var(--dim);border:1px solid var(--border);border-radius:20px;padding:1px 8px}
.file-head .stat{font-family:var(--mono);font-size:12px}
.file-head .stat .a{color:var(--add-num)} .file-head .stat .d{color:var(--del-num)}
.file-head .grow{flex:1}
.file-head label{display:flex;gap:5px;align-items:center;color:var(--dim);font-size:12px;cursor:pointer}
.file-body{overflow-x:auto}
.hunk-head{font-family:var(--mono);font-size:12px;color:var(--dim);background:var(--surface2);
  padding:3px 12px;border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.grid{display:grid;grid-template-columns:46px minmax(0,1fr) 46px minmax(0,1fr);min-width:760px}
.num{font-family:var(--mono);font-size:12px;color:var(--dim);text-align:right;padding:1px 8px;
  user-select:none;border-right:1px solid var(--border);white-space:nowrap}
.code{font-family:var(--mono);font-size:12.5px;padding:1px 10px;white-space:pre-wrap;word-break:break-word;
  cursor:pointer;position:relative}
.code:hover{background:var(--accent-bg)}
.code.empty,.num.empty{background:var(--surface2);cursor:default}
.code.empty:hover{background:var(--surface2)}
.code.r-add{background:var(--add-bg)} .num.r-add{background:var(--add-gut);color:var(--add-num)}
.code.r-del{background:var(--del-bg)} .num.r-del{background:var(--del-gut);color:var(--del-num)}
.code .plus{display:none;position:absolute;left:2px;top:50%;transform:translateY(-50%);
  background:var(--accent);color:#fff;border-radius:4px;width:16px;height:16px;line-height:16px;
  text-align:center;font-size:12px}
.code:hover .plus{display:block}
.code .cmt-kill{display:none;position:absolute;right:6px;top:50%;transform:translateY(-50%);
  background:var(--del-num);color:#fff;border-radius:4px;min-width:16px;height:16px;line-height:16px;
  text-align:center;font-size:12px;padding:0 3px;cursor:pointer;z-index:2}
.code:hover .cmt-kill{display:block}
.code .cmt-kill:hover{filter:brightness(1.08)}
.code.mark-remove{text-decoration:line-through;text-decoration-color:var(--del-num);opacity:.55}
.cmt-saved.dismiss{border-left:3px solid var(--del-num)}
.cmt-saved.dismiss .del{color:var(--del-num);font-weight:700;font-size:18px}
.cmt-row{grid-column:1/5;background:var(--surface2);border-top:1px solid var(--border);
  border-bottom:1px solid var(--border);padding:10px 12px}
.cmt-anchor{font-family:var(--mono);font-size:11px;color:var(--accent);margin-bottom:6px}
.cmt textarea{width:100%;font:inherit;font-family:var(--ui);background:var(--surface);color:var(--text);
  border:1px solid var(--border);border-radius:7px;padding:8px;resize:vertical;min-height:52px}
.cmt-actions{display:flex;gap:8px;margin-top:7px}
.saved{display:flex;gap:8px;align-items:flex-start;background:var(--surface);border:1px solid var(--border);
  border-left:3px solid var(--accent);border-radius:7px;padding:8px 10px;margin-bottom:6px}
.saved .txt{flex:1;white-space:pre-wrap}
.saved .del,.saved .edit{color:var(--dim);cursor:pointer;border:none;background:none;font-size:16px;line-height:1}
.saved .edit{font-size:13px}
.saved .del:hover,.saved .edit:hover{color:var(--text)}
.cmt-anchor .ghdest{display:inline-flex;align-items:center;gap:4px;color:#8b949e;border:1px solid var(--border);
  border-radius:20px;padding:0 7px;margin-left:6px;font-size:10.5px;vertical-align:1px}
.cmt-saved.gh .saved{border-left-color:#6e7681}
.btn.gh{background:#24292f;color:#fff;border-color:#24292f;font-weight:600;display:inline-flex;align-items:center;gap:6px}
.btn.gh:hover{filter:brightness(1.2);border-color:#24292f}
.btn.gh:disabled{opacity:.6;cursor:progress}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--accent);color:#fff;
  padding:11px 18px;border-radius:9px;box-shadow:0 6px 24px rgba(0,0,0,.25);opacity:0;transition:opacity .25s;z-index:50}
.toast.show{opacity:1}
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;align-items:center;justify-content:center;z-index:60;padding:20px}
.modal-bg.show{display:flex}
.modal{background:var(--surface);border:1px solid var(--border);border-radius:12px;max-width:560px;width:100%;padding:18px;box-shadow:0 14px 44px rgba(0,0,0,.32)}
.modal h2{margin:0 0 4px;font-size:16px}
.modal .sub{color:var(--dim);font-size:13px;margin-bottom:12px}
.modal textarea{width:100%;min-height:120px;font:inherit;font-family:var(--ui);background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:8px;padding:10px;resize:vertical}
.modal .actions{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
.empty-note{color:var(--dim);padding:14px}
/* highlight.js token classes mapped onto the same palette (light + dark) */
.hljs-keyword,.hljs-literal,.hljs-selector-tag,.hljs-doctag{color:var(--tk-k)}
.hljs-string,.hljs-regexp,.hljs-meta .hljs-string,.hljs-addition{color:var(--tk-s)}
.hljs-comment{color:var(--tk-c);font-style:italic}
.hljs-number,.hljs-symbol,.hljs-bullet{color:var(--tk-n)}
.hljs-title,.hljs-title.class_,.hljs-title.function_,.hljs-type,.hljs-built_in,.hljs-selector-class{color:var(--tk-t)}
.hljs-meta,.hljs-attr,.hljs-attribute,.hljs-name,.hljs-selector-attr,.hljs-selector-id{color:var(--tk-an)}
.hljs-emphasis{font-style:italic}.hljs-strong{font-weight:600}
.hunk-head{cursor:pointer;user-select:none;display:flex;gap:8px;align-items:center}
.hunk-head .hchev{color:var(--dim);transition:transform .15s;flex:none}
.hunk.collapsed .hchev{transform:rotate(-90deg)}
.hunk.collapsed .grid{display:none}
:focus-visible{outline:2px solid var(--accent);outline-offset:1px}
</style></head><body>
<header>
  <span class="title" id="title">__TITLE_HTML__</span>
  <div class="controls">
    <button class="btn refresh" id="refresh" hidden>↻ Refresh</button>
    <span class="count" id="fcount"></span>
    <button class="btn" id="expandAll">Expand all</button>
    <button class="btn" id="collapseAll">Collapse all</button>
    <button class="btn" id="theme">◐</button>
    <button class="btn primary" id="submit" disabled>Submit review (0)</button>
    <span class="brand">CaseyDiff<span class="tm">™</span></span>
  </div>
</header>
<main id="root"></main>
<div class="toast" id="toast"></div>
<div class="modal-bg" id="finishBg">
  <div class="modal" role="dialog" aria-modal="true" aria-label="Finish your review">
    <h2>Finish your review</h2>
    <div class="sub" id="finishSub"></div>
    <textarea id="finishSummary" placeholder="Overall review comment (optional)"></textarea>
    <div class="actions">
      <button class="btn" id="finishCancel">Cancel</button>
      <button class="btn primary" id="finishSubmit">Submit review</button>
      <button class="btn approve" id="finishApprove">Approve</button>
    </div>
  </div>
</div>
<script src="/vendor/highlight.min.js"></script>
<script src="/vendor/dart.min.js"></script>
<script>
const DIFF = /*__DIFF_JSON__*/[];
const META = /*__META_JSON__*/{};
const comments = {}; // anchor -> {file,line,side,code,text}
const HAS_PR = !!(META && META.github);   // only offer GitHub posting when the diff is a PR
const GH_ICON = '<svg viewBox="0 0 16 16" width="13" height="13" fill="currentColor" aria-hidden="true" style="vertical-align:-2px"><path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0016 8c0-4.42-3.58-8-8-8z"></path></svg>';
const esc = s => s.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const root = document.getElementById('root');

// Syntax highlighting via highlight.js (vendored). Pick a language per file from
// its extension; fall back to auto-detection when the extension is unknown.
const LANG_MAP = {
  dart:'dart', swift:'swift', kt:'kotlin', kts:'kotlin', java:'java', groovy:'groovy',
  js:'javascript', jsx:'javascript', mjs:'javascript', cjs:'javascript',
  ts:'typescript', tsx:'typescript', py:'python', rb:'ruby', go:'go', rs:'rust',
  c:'c', h:'c', cc:'cpp', cpp:'cpp', cxx:'cpp', hpp:'cpp', hh:'cpp', cs:'csharp',
  m:'objectivec', mm:'objectivec', php:'php', scala:'scala',
  sh:'bash', bash:'bash', zsh:'bash', yml:'yaml', yaml:'yaml', json:'json',
  xml:'xml', html:'xml', htm:'xml', vue:'xml', svg:'xml', plist:'xml',
  css:'css', scss:'scss', less:'less', sql:'sql', md:'markdown', markdown:'markdown',
  toml:'ini', ini:'ini', lua:'lua', r:'r', pl:'perl', pm:'perl',
  make:'makefile', mk:'makefile', gradle:'groovy', dockerfile:'dockerfile',
};
function langForFile(file){
  if(typeof hljs==='undefined') return null;
  const base = (file.new || file.display || '').toLowerCase().split('/').pop();
  if(base==='makefile' || base==='dockerfile') return base;
  const ext = base.includes('.') ? base.split('.').pop() : '';
  const l = LANG_MAP[ext];
  return (l && hljs.getLanguage(l)) ? l : null;
}
// Highlight one source line and report whether the whole line is a comment.
// Comment detection reuses the highlighter's own tokens, so it works for any
// language hljs knows (// , # , -- , /* */ , <!-- -->, …) with no per-language rules.
function hlLine(src, lang){
  if(src==null || src==='') return {html:' ', isComment:false};
  if(typeof hljs==='undefined') return {html:esc(src), isComment:false};
  let res;
  try{
    res = lang ? hljs.highlight(src, {language:lang, ignoreIllegals:true})
               : hljs.highlightAuto(src);
  }catch(e){ return {html:esc(src), isComment:false}; }
  const html = res.value;
  if(html.indexOf('hljs-comment')===-1) return {html, isComment:false};
  const tmp = document.createElement('div'); tmp.innerHTML = html;
  const allNS = (tmp.textContent||'').replace(/\s+/g,'');
  let cmt=''; tmp.querySelectorAll('.hljs-comment').forEach(s=>{cmt+=s.textContent;});
  const isComment = allNS.length>0 && allNS===cmt.replace(/\s+/g,'');
  return {html, isComment};
}

function anchorOf(file, cell){
  const path = cell.t==='del' ? file.old : file.new;
  const side = cell.t==='del' ? 'L' : 'R';
  return {key:`${path}|${side}${cell.n}`, path, side, line:cell.n, code:cell.s};
}

function render(){
  root.innerHTML='';
  if(!DIFF.length){root.innerHTML='<div class="empty-note">No changes in this diff.</div>';return;}
  DIFF.forEach((file, fi) => {
    const el = document.createElement('section');
    el.className = 'file' + (file.generated ? ' collapsed' : '');
    el.dataset.fi = fi;
    const stat = `<span class="stat"><span class="a">+${file.adds}</span> <span class="d">-${file.dels}</span></span>`;
    const gen = file.generated ? '<span class="badge">generated</span>' : '';
    const status = file.status!=='modified' ? `<span class="badge">${file.status}</span>` : '';
    el.innerHTML = `<div class="file-head">
        <span class="chev">▾</span>
        <span class="path">${esc(file.display)}</span>
        ${status}${gen}
        <span class="grow"></span>
        ${stat}
        <label><input type="checkbox" class="viewed"> Viewed</label>
      </div><div class="file-body"></div>`;
    const body = el.querySelector('.file-body');
    if(file.binary){ body.innerHTML='<div class="empty-note">Binary file not shown.</div>'; }
    else{
      const lang = langForFile(file);
      file.hunks.forEach(h => {
        const hunkEl = document.createElement('div'); hunkEl.className='hunk';
        const hh = document.createElement('div'); hh.className='hunk-head';
        hh.innerHTML = `<span class="hchev">▾</span><span>${esc(h.header)}${h.section? '  '+esc(h.section):''}</span>`;
        hh.addEventListener('click', () => hunkEl.classList.toggle('collapsed'));
        hunkEl.appendChild(hh);
        const g = document.createElement('div'); g.className='grid';
        const rlines = [];
        h.rows.forEach(row => { const made = addRow(g, file, row, lang); if(made.r) rlines.push(made.r); });
        assignCommentBlocks(rlines, file, g);
        hunkEl.appendChild(g);
        body.appendChild(hunkEl);
      });
    }
    // head interactions
    el.querySelector('.file-head').addEventListener('click', e => {
      if(e.target.closest('label')) return;
      el.classList.toggle('collapsed');
    });
    el.querySelector('.viewed').addEventListener('change', e => {
      el.classList.toggle('collapsed', e.target.checked);
    });
    root.appendChild(el);
  });
  refreshCounts();
}

function addRow(g, file, row, lang){
  const made = {l:null, r:null};
  ['l','r'].forEach(sideKey => {
    const cell = row[sideKey];
    const num = document.createElement('div');
    const code = document.createElement('div');
    const cls = cell.t==='add'?'r-add':cell.t==='del'?'r-del':'';
    if(cell.t==='empty'){
      num.className='num empty'; code.className='code empty';
    }else{
      num.className='num'+(cls?' '+cls:''); num.textContent=cell.n!=null?cell.n:'';
      code.className='code'+(cls?' '+cls:'');
      const {html, isComment} = hlLine(cell.s, lang);
      const kill = (sideKey==='r' && isComment)
        ? '<span class="cmt-kill" title="Remove this comment block">⊘</span>' : '';
      code.innerHTML = `<span class="plus">+</span>${kill}${html||' '}`;
      code.addEventListener('click', e => {
        if(e.target.closest('.cmt-kill')) return;
        const sel = window.getSelection();          // don't hijack a drag-to-select for copy/paste
        if(sel && !sel.isCollapsed && sel.toString().length) return;
        openComposer(file, cell, g, code);
      });
      made[sideKey] = {cell, codeEl:code, isComment, kill: kill ? code.querySelector('.cmt-kill') : null};
    }
    g.appendChild(num); g.appendChild(code);
  });
  return made;
}

// A "dismiss comment block" comment covers the maximal run of contiguous comment
// lines on the new side. One tap on any line of the run tells Claude to delete it.
function assignCommentBlocks(rlines, file, g){
  for(let i=0;i<rlines.length;i++){
    const rc = rlines[i];
    if(!(rc.isComment && rc.kill)) continue;
    let a=i; while(a>0 && rlines[a-1].isComment && rlines[a-1].cell.n===rlines[a].cell.n-1) a--;
    let b=i; while(b<rlines.length-1 && rlines[b+1].isComment && rlines[b+1].cell.n===rlines[b].cell.n+1) b++;
    const run = rlines.slice(a, b+1);
    rc.kill.onclick = ev => { ev.stopPropagation(); dismissBlock(file, run, g); };
  }
}

function dismissBlock(file, run, g){
  const first = run[0].cell, last = run[run.length-1].cell;
  const path = file.new;
  const key = `${path}|R${first.n}`;
  const text = run.length>1 ? `Remove these ${run.length} comment lines.` : 'Remove this comment.';
  comments[key] = {file:path, line:first.n, side:'R', code:first.s, endLine:last.n, text, kind:'dismiss-comments'};
  run.forEach(rc => rc.codeEl.classList.add('mark-remove'));
  refreshCounts();
  showDismissChip(g, run, key);
}

function showDismissChip(g, run, key){
  const last = run[run.length-1];
  const ex = g.querySelector(`.cmt-saved[data-k="${CSS.escape(key)}"]`); if(ex) ex.remove();
  const c = comments[key];
  const range = c.endLine>c.line ? `R${c.line}–R${c.endLine}` : `R${c.line}`;
  const chip = document.createElement('div');
  chip.className='cmt-row cmt-saved dismiss'; chip.dataset.k=key;
  chip.innerHTML = `<div class="cmt-anchor">${esc(c.file)} : ${range} · marked for removal</div>
    <div class="saved"><span class="txt">${esc(c.text)}</span>
      <button class="del" title="Undo removal">×</button></div>`;
  chip.querySelector('.del').onclick = () => {
    delete comments[key];
    run.forEach(rc => rc.codeEl.classList.remove('mark-remove'));
    chip.remove(); refreshCounts();
  };
  insertAfterRow(g, last.codeEl, chip);
}

function openComposer(file, cell, grid, codeEl){
  const a = anchorOf(file, cell);
  // find the DOM row index to insert after: insert a full-width comment row right after this code cell's row
  const existing = comments[a.key];
  const cmt = document.createElement('div');
  cmt.className='cmt-row';
  cmt.innerHTML = `<div class="cmt-anchor">${esc(a.path)} : ${a.side}${a.line}</div>
    <div class="cmt">
      <textarea placeholder="Leave a comment on this line…"></textarea>
      <div class="cmt-actions">
        <button class="btn primary save">${existing ? 'Update comment' : 'Add comment'}</button>
        ${HAS_PR ? `<button class="btn gh save-gh" title="Save for the agent and post on the PR">${GH_ICON} Comment on GitHub</button>` : ''}
        <button class="btn cancel">Cancel</button>
      </div></div>`;
  // insert after the code cell's grid cell (append at end of grid keeps it after; better: place right after row)
  insertAfterRow(grid, codeEl, cmt);
  const ta = cmt.querySelector('textarea');
  if(existing) ta.value = existing.text;
  ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length);
  cmt.querySelector('.save').onclick = () => {
    const v = ta.value.trim(); if(!v) return;
    comments[a.key] = {file:a.path, line:a.line, side:a.side, code:a.code, text:v, github: !!(existing && existing.github)};
    cmt.remove(); refreshCounts(); rerenderSaved(grid, file, cell, codeEl);
  };
  cmt.querySelector('.cancel').onclick = () => cmt.remove();
  const ghBtn = cmt.querySelector('.save-gh');
  if(ghBtn) ghBtn.onclick = () => {   // same as a normal comment, just flagged to also post on the PR at submit
    const v = ta.value.trim(); if(!v) return;
    comments[a.key] = {file:a.path, line:a.line, side:a.side, code:a.code, text:v, github:true};
    cmt.remove(); refreshCounts(); rerenderSaved(grid, file, cell, codeEl);
  };
  cmt.querySelectorAll('.saved .del').forEach(b => b.onclick = () => {
    delete comments[b.dataset.k]; cmt.remove(); refreshCounts(); rerenderSaved(grid, file, cell, codeEl);
  });
}
function rerenderSaved(grid, file, cell, codeEl){
  // show a persistent saved chip under the line if a comment exists
  const a = anchorOf(file, cell);
  // remove any existing chip row we placed for this anchor
  const existing = grid.querySelector(`.cmt-saved[data-k="${CSS.escape(a.key)}"]`);
  if(existing) existing.remove();
  const c = comments[a.key]; if(!c) return;
  const chip = document.createElement('div');
  chip.className='cmt-row cmt-saved' + (c.github ? ' gh' : ''); chip.dataset.k=a.key;
  chip.innerHTML = `<div class="cmt-anchor">${esc(a.path)} : ${a.side}${a.line}${c.github?` <span class="ghdest" title="Will be posted on the PR when you submit">${GH_ICON} GitHub</span>`:''}</div>
    <div class="saved"><span class="txt">${esc(c.text)}</span>
      <button class="edit" title="Edit">✎</button>
      <button class="del" title="Delete">×</button></div>`;
  chip.querySelector('.del').onclick = () => { delete comments[a.key]; chip.remove(); refreshCounts(); };
  const editSaved = () => { chip.remove(); openComposer(file, cell, grid, codeEl); };
  chip.querySelector('.edit').onclick = editSaved;
  chip.querySelector('.saved .txt').onclick = editSaved;
  insertAfterRow(grid, codeEl, chip);
}
function insertAfterRow(grid, codeEl, node){
  // codeEl is a grid cell; find the last cell of its visual row (the r-code, index%4==3) then insert node after
  const cells = Array.from(grid.children).filter(c=>!c.classList.contains('cmt-row'));
  const idx = Array.from(grid.children).indexOf(codeEl);
  // insert after the current grid row's 4th cell
  let insertRef = grid.children[idx];
  // advance to end of this 4-col row
  let col = 0, p = idx;
  // find start of row
  // simpler: insert immediately after codeEl
  if(codeEl.nextSibling) grid.insertBefore(node, codeEl.nextSibling); else grid.appendChild(node);
}

function refreshCounts(){
  const n = Object.keys(comments).length;
  const btn = document.getElementById('submit');
  btn.textContent = `Submit review (${n})`; btn.disabled = false;
  document.getElementById('fcount').textContent =
    `${DIFF.length} file${DIFF.length!==1?'s':''} · ${n} comment${n!==1?'s':''}`;
}

document.getElementById('expandAll').onclick = () =>
  document.querySelectorAll('.file').forEach(f=>f.classList.remove('collapsed'));
document.getElementById('collapseAll').onclick = () =>
  document.querySelectorAll('.file').forEach(f=>f.classList.add('collapsed'));
document.getElementById('theme').onclick = () => {
  const r=document.documentElement;
  const cur=r.getAttribute('data-theme')|| (matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
  r.setAttribute('data-theme', cur==='dark'?'light':'dark');
};

// Out-of-sync detection: the server recomputes the diff from the same source it
// was launched with and reports whether it differs from what this page is showing.
// When it does, reveal the Refresh button so the user can rebuild on demand.
const refreshBtn = document.getElementById('refresh');
const REFRESH_LABEL = '↻ Refresh';
let refreshArmed = false, refreshArmTimer = null;
function disarmRefresh(){ refreshArmed = false; clearTimeout(refreshArmTimer); refreshBtn.textContent = REFRESH_LABEL; }
async function checkSync(){
  try{
    const r = await fetch('/state', {cache:'no-store'});
    if(!r.ok) return;
    const s = await r.json();
    refreshBtn.hidden = !s.stale;
    if(refreshBtn.hidden) disarmRefresh();
  }catch(e){ /* transient; try again next tick */ }
}
async function doRefresh(){
  refreshBtn.disabled = true; refreshBtn.textContent = 'Refreshing…';
  try{
    const r = await fetch('/refresh', {method:'POST'});
    if(!r.ok) throw new Error(await r.text());
    location.reload();
  }catch(e){
    toast('Refresh failed: '+e.message);
    refreshBtn.disabled = false; refreshBtn.textContent = REFRESH_LABEL;
  }
}
// One click when there is nothing to lose. When comments are pending, the first
// click arms an inline confirm on the button itself (no native dialog, which some
// browsers block); a second click within 4s reloads and discards them.
refreshBtn.onclick = () => {
  const n = Object.keys(comments).length;
  if(n && !refreshArmed){
    refreshArmed = true;
    refreshBtn.textContent = `Discard ${n} comment${n!==1?'s':''} & refresh?`;
    refreshArmTimer = setTimeout(disarmRefresh, 4000);
    return;
  }
  clearTimeout(refreshArmTimer); refreshArmed = false;
  doRefresh();
};
setInterval(checkSync, 6000);
document.addEventListener('visibilitychange', () => { if(!document.hidden) checkSync(); });
window.addEventListener('focus', checkSync);
checkSync();
const submitBtn = document.getElementById('submit');
const finishBg = document.getElementById('finishBg');
const finishSummary = document.getElementById('finishSummary');
const finishSubmit = document.getElementById('finishSubmit');
const finishApprove = document.getElementById('finishApprove');
function updateFinishBtn(){                 // Submit needs feedback; Approve is always allowed
  const n = Object.keys(comments).length;
  finishSubmit.disabled = (n===0 && !finishSummary.value.trim());
}
submitBtn.onclick = () => {                 // step 1: open the finish-review window
  const n = Object.keys(comments).length;
  const g = Object.values(comments).filter(c=>c.github).length;
  document.getElementById('finishSub').textContent =
    `${n} line comment${n!==1?'s':''} on this review${g?`, ${g} will be posted to GitHub`:''}. Add an optional overall comment, then submit or approve.`;
  finishBg.classList.add('show'); finishSummary.focus(); updateFinishBtn();
};
finishSummary.addEventListener('input', updateFinishBtn);
document.getElementById('finishCancel').onclick = () => finishBg.classList.remove('show');
finishBg.addEventListener('click', e => { if(e.target===finishBg) finishBg.classList.remove('show'); });
async function doSubmit(approved){          // step 2: submit the round, optionally approving
  const payload = {meta:META, summary:finishSummary.value.trim(), approved, comments:Object.values(comments)};
  finishSubmit.disabled = true; finishApprove.disabled = true;
  try{
    const res = await fetch('/submit',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(!res.ok) throw new Error(await res.text());
    const info = await res.json().catch(()=>({}));
    finishBg.classList.remove('show');
    let msg = approved
      ? 'Approved — Claude will make sure the PR is up and marked ready.'
      : `Submitted ${payload.comments.length} comment(s) — switch back to Claude.`;
    if(info.github_posted) msg += ` · ${info.github_posted} posted to GitHub`;
    if(info.github_failed) msg += ` · ${info.github_failed} GitHub post(s) failed`;
    toast(msg);
    submitBtn.textContent = approved ? 'Approved ✓' : 'Submitted ✓'; submitBtn.disabled = true;
  }catch(e){
    toast('Submit failed: '+e.message);
    finishSubmit.disabled = false; finishApprove.disabled = false;
  }
}
finishSubmit.onclick = () => doSubmit(false);
finishApprove.onclick = () => doSubmit(true);
function toast(msg){
  const t=document.getElementById('toast'); t.textContent=msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 4000);
}
// Fade the title only when it truly overflows two lines (so short titles keep crisp edges).
function fitTitle(){
  const t = document.getElementById('title'); if(!t) return;
  t.classList.remove('clamped');
  if(t.scrollHeight - t.clientHeight > 1) t.classList.add('clamped');
}
window.addEventListener('resize', fitTitle);
render();
fitTitle();
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    page = b""
    out_path = "pr_comments.json"
    vendor_dir = ""
    # source the diff was generated from, so the server can recompute it on demand
    pr = None
    repo = None
    diff_file = None
    diff_sig = ""
    _last_sig = None
    _last_check = 0.0
    gh = None  # {owner, repo, pr, sha} when the diff is an associated PR, else None
    srv = None  # the running server, set by main() so a handler can shut it down
    once = False  # shut the server down after a successful /submit

    def log_message(self, *a):
        pass

    def current_sig(self):
        # Recompute the signature of the live source, throttled so PR mode does not
        # shell out to `gh` on every poll. Returns the served signature on failure
        # (a source we cannot read is not a reason to nag about being out of sync).
        now = time.monotonic()
        if Handler._last_sig is not None and (now - Handler._last_check) < 4.0:
            return Handler._last_sig
        try:
            sig = source_sig(get_diff(Handler.pr, Handler.repo, Handler.diff_file))
        except Exception:
            return Handler.diff_sig
        Handler._last_sig = sig
        Handler._last_check = now
        return sig

    def _send_json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.page)
            return
        if self.path == "/state":
            sig = self.current_sig()
            self._send_json(200, {"stale": sig != Handler.diff_sig, "sig": sig})
            return
        if self.path.startswith("/vendor/"):
            name = self.path[len("/vendor/"):].split("?", 1)[0]
            fp = os.path.join(self.vendor_dir, name)
            if re.fullmatch(r"[\w.\-]+\.js", name) and os.path.isfile(fp):
                with open(fp, "rb") as f:
                    data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("Cache-Control", "max-age=3600")
                self.end_headers()
                self.wfile.write(data)
                return
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path == "/refresh":
            try:
                diff = get_diff(Handler.pr, Handler.repo, Handler.diff_file)
                meta = get_meta(Handler.pr, Handler.repo, Handler.diff_file)
                Handler.gh = resolve_gh(Handler.pr, Handler.repo, Handler.diff_file)
                meta["github"] = bool(Handler.gh)
                files = parse_diff(diff)
                Handler.page = build_page(files, meta)
                Handler.diff_sig = source_sig(diff)
                Handler._last_sig = Handler.diff_sig
                Handler._last_check = time.monotonic()
            except Exception as e:
                self._send_json(500, {"ok": False, "error": str(e)})
                return
            self._send_json(200, {"ok": True, "files": len(files)})
            return
        if self.path != "/submit":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        data = self.rfile.read(length)
        try:
            payload = json.loads(data)
        except Exception as e:
            self.send_response(400)
            self.end_headers()
            self.wfile.write(str(e).encode())
            return
        # The temp file is created before posting, not just before the write: an
        # unwritable/missing --out directory is then caught up front, instead of
        # after comments were already posted (a 500 with nothing posted is safe
        # to retry; a 500 after posting is not).
        out_dir = os.path.dirname(os.path.abspath(self.out_path)) or "."
        try:
            fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=".out-", suffix=".tmp")
        except OSError as e:
            # Fail before any comment posts: a retry after a partial post would
            # duplicate the posted comments.
            self._send_json(500, {"ok": False, "error": f"cannot write --out: {e}"})
            return
        # human-readable to stdout
        header = "REVIEW APPROVED" if payload.get("approved") else "REVIEW SUBMITTED"
        print(f"\n===== {header} =====", flush=True)
        if payload.get("approved"):
            if Handler.gh:
                print("APPROVED: ensure the PR is pushed to GitHub and marked ready for review.", flush=True)
            else:
                print("APPROVED: local review — no PR to update.", flush=True)
        if payload.get("summary"):
            print(f"SUMMARY: {payload['summary']}", flush=True)
        for c in payload.get("comments", []):
            end = c.get("endLine")
            rng = f"-{c['side']}{end}" if end and end != c["line"] else ""
            tag = " [dismiss-comments]" if c.get("kind") == "dismiss-comments" else ""
            tag += " [→github]" if c.get("github") else ""
            print(f"{c['file']}:{c['side']}{c['line']}{rng}{tag}  {c['text']}", flush=True)

        try:
            # Post the GitHub-flagged comments to the PR before writing out_path:
            # the skill kills this process the moment out_path appears, so
            # posting first keeps a slow multi-comment loop from being cut off
            # mid-post.
            gh_posted, gh_failed = [], []
            if Handler.gh:
                for c in payload.get("comments", []):
                    if not c.get("github"):
                        continue
                    url, err = post_pr_comment(Handler.gh, c)
                    if err:
                        gh_failed.append({"file": c.get("file"), "line": c.get("line"), "error": err})
                    else:
                        gh_posted.append(url)
                if gh_posted or gh_failed:
                    print(f"GITHUB: posted {len(gh_posted)}, failed {len(gh_failed)}", flush=True)
                    for u in gh_posted:
                        print(f"  posted: {u}", flush=True)
                    for f in gh_failed:
                        print(f"  FAILED {f['file']}:{f['line']}: {f['error']}", flush=True)
            print("============================\n", flush=True)

            if Handler.gh:
                payload["github_posted"] = gh_posted
                payload["github_failed"] = gh_failed
            with os.fdopen(fd, "w") as f:
                json.dump(payload, f, indent=2)
            os.replace(tmp_path, self.out_path)
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            os.unlink(tmp_path)
            raise

        # Schedule the --once shutdown before touching the response: the review
        # is durably saved at this point, and a client disconnect mid-response
        # (BrokenPipeError) must not leave the server running.
        if Handler.once and Handler.srv:
            threading.Thread(target=Handler.srv.shutdown, daemon=True).start()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"ok": True, "count": len(payload.get("comments", [])),
                                     "github_posted": len(gh_posted), "github_failed": len(gh_failed)}).encode())


def build_page(files, meta):
    title = f"Review · {meta.get('title','diff')}"
    if meta.get("number"):
        title = f"#{meta['number']} · {meta.get('title','')}"
    if meta.get("url"):
        title_html = f'<a href="{meta["url"]}" target="_blank">#{meta.get("number","")}</a> {esc_py(meta.get("title",""))}'
    else:
        title_html = esc_py(meta.get("title", "local diff"))
    html = (PAGE
            .replace("__TITLE__", esc_py(title))
            .replace("__TITLE_HTML__", title_html)
            .replace("/*__DIFF_JSON__*/[]", json.dumps(files))
            .replace("/*__META_JSON__*/{}", json.dumps(meta)))
    return html.encode("utf-8")


def esc_py(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pr", nargs="?", help="PR number")
    ap.add_argument("--repo")
    ap.add_argument("--diff-file")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--out", default="pr_comments.json")
    ap.add_argument("--once", action="store_true", help="shut down after a successful /submit")
    args = ap.parse_args()

    if not args.pr and not args.diff_file:
        print("error: provide a PR number or --diff-file", file=sys.stderr)
        sys.exit(2)

    try:
        diff = get_diff(args.pr, args.repo, args.diff_file)
        meta = get_meta(args.pr, args.repo, args.diff_file)
        gh = resolve_gh(args.pr, args.repo, args.diff_file)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
    meta["github"] = bool(gh)
    files = parse_diff(diff)
    Handler.page = build_page(files, meta)
    Handler.out_path = args.out
    Handler.vendor_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor")
    Handler.pr = args.pr
    Handler.repo = args.repo
    Handler.diff_file = args.diff_file
    Handler.diff_sig = source_sig(diff)
    Handler.gh = gh
    Handler.once = args.once

    srv = ThreadingHTTPServer(("127.0.0.1", args.port or 0), Handler)
    Handler.srv = srv
    port = srv.server_address[1]
    print(f"PR review UI: http://127.0.0.1:{port}   ({len(files)} files)  out={args.out}", flush=True)
    print(f"LOCAL_REVIEW_URL=http://127.0.0.1:{port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
