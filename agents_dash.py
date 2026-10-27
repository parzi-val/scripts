"""agents-dash: live local dashboard of running Claude Code sessions.

Usage: agents-dash [--port 8790] [--no-open]
Serves http://127.0.0.1:8790 (never the LAN). Read-only: it never sends messages to sessions,
kills processes, or reads the *.key files / peer tokens / socket paths in ~/.claude/sessions.

Data: ~/.claude/sessions/<pid>.json (live registry, checked against real processes),
transcript tails in ~/.claude/projects/<slug>/<sessionId>.jsonl, and workspace plans
whose `owner:` matches a session name. Formats are Claude Code internals, so every field
is optional and shows as "?" when missing.
"""
import argparse
import glob
import json
import os
import re
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    import psutil
except ImportError:  # still works, just can't verify liveness or show memory
    psutil = None

HOME = os.path.expanduser("~")
SESSIONS = os.path.join(HOME, ".claude", "sessions")
PROJECTS = os.path.join(HOME, ".claude", "projects")
PLANS = os.environ.get("AGENTS_DASH_PLANS", r"D:\seriesn\workspace\plans")
RECENT_DAYS = 7
FILETIME_EPOCH = 11644473600  # seconds between 1601-01-01 (Windows FILETIME) and 1970-01-01


# ---------- transcripts ----------

def tail_lines(path, max_bytes=32 << 20, chunk=1 << 20):
    """Yield the file's lines newest-first, reading at most max_bytes from the end."""
    with open(path, "rb") as f:
        f.seek(0, 2)
        end = pos = f.tell()
        buf = b""
        while pos > 0 and end - pos < max_bytes:
            n = min(chunk, pos)
            pos -= n
            f.seek(pos)
            buf = f.read(n) + buf
            lines = buf.split(b"\n")
            buf = lines[0] if pos > 0 else b""  # first line may be cut off; finish it next round
            for line in reversed(lines if pos == 0 else lines[1:]):
                yield line


REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
COMMAND = re.compile(r"<command-name>(.*?)</command-name>", re.S)
COMMAND_ARGS = re.compile(r"<command-args>(.*?)</command-args>", re.S)
TAG = re.compile(r"</?[a-z-]+(?: [^>]*)?>")
PIPE = re.compile(r"(?:uds:)?\\{1,2}[.?]\\pipe\\\S+", re.I)  # session messaging sockets, if quoted anywhere


def clean(text):
    text = PIPE.sub("[pipe]", REMINDER.sub("", text))
    cmd = COMMAND.search(text)
    if cmd:  # slash command: show "/name args" rather than the raw tags
        args = COMMAND_ARGS.search(text)
        text = f"{cmd.group(1)} {args.group(1) if args else ''}"
    return re.sub(r"\s+", " ", TAG.sub(" ", text)).strip()


def text_of(content):
    if isinstance(content, str):
        return clean(content)
    parts = []
    for c in content or []:
        if c.get("type") == "text":
            parts.append(clean(c.get("text", "")))
        elif c.get("type") == "image":
            parts.append("[image]")
    return " ".join(p for p in parts if p)


def request_of(d):
    """The user's own prompt or a peer message; None for tool results and system noise."""
    origin = d.get("origin") or {}
    if origin.get("kind") == "peer":
        return {"text": clean(origin.get("body", "")), "from": origin.get("name") or "?"}
    if origin.get("kind") == "human":
        text = text_of(d.get("message", {}).get("content"))
        if text:
            return {"text": text, "from": None}
    return None


_transcript_cache = {}


def read_transcript(path):
    """Latest title, request, reply and cwd from the tail. Cached per (mtime, size)."""
    try:
        st = os.stat(path)
    except OSError:
        return {}
    key = (path, st.st_mtime, st.st_size)
    if key in _transcript_cache:
        return _transcript_cache[key]
    info = {"mtime": st.st_mtime * 1000, "names": []}
    for line in tail_lines(path):
        if b'"api-request' in line[:60] or b'"type"' not in line:
            continue  # request blobs are huge and never useful here
        try:
            d = json.loads(line)
        except ValueError:
            continue
        t = d.get("type")
        if t in ("custom-title", "agent-name"):
            name = d.get("customTitle") or d.get("agentName")
            if name and name not in info["names"]:
                info["names"].append(name)  # newest first
        if d.get("isSidechain"):
            continue
        if "cwd" not in info and d.get("cwd"):
            info["cwd"] = d["cwd"]
        if t == "assistant" and "reply" not in info:
            text = text_of(d.get("message", {}).get("content"))
            if text:
                info["reply"] = {"text": text, "ts": d.get("timestamp")}
        if t == "user" and "request" not in info:
            req = request_of(d)
            if req:
                info["request"] = {**req, "ts": d.get("timestamp")}
        if info["names"] and "reply" in info and "request" in info and "cwd" in info:
            break
    _transcript_cache[key] = info
    return info


def slug(cwd):
    return re.sub(r"[:\\/]", "-", cwd)


def transcript_path(cwd, sid):
    p = os.path.join(PROJECTS, slug(cwd or ""), f"{sid}.jsonl")
    if os.path.exists(p):
        return p
    found = glob.glob(os.path.join(PROJECTS, "*", f"{sid}.jsonl"))  # slug rule changed? search
    return found[0] if found else None


# ---------- processes & plans ----------

_proc_cache = {}


def proc_info(pid, proc_start):
    """None if the PID is dead or was reused by another process; else memory/host/children.

    The alive check runs every poll; walking parents/children is slow on Windows (~200 ms
    per process), so those details are cached for 10 s.
    """
    if psutil is None:
        return {}
    try:
        p = psutil.Process(pid)
        if proc_start and abs(p.create_time() - (int(proc_start) / 1e7 - FILETIME_EPOCH)) > 2:
            return None  # same PID, different process: the registry file is stale
        hit = _proc_cache.get((pid, proc_start))
        if hit and time.time() - hit[0] < 10:
            return hit[1]
        info = _proc_details(p)
        _proc_cache[(pid, proc_start)] = (time.time(), info)
        return info
    except psutil.Error:
        return None


def _proc_details(p):
    try:
        kids = [c for c in p.children(recursive=True) if c.name().lower() != "conhost.exe"]
        kid_mem = 0
        for c in kids:
            try:
                kid_mem += c.memory_info().rss
            except psutil.Error:
                pass
        parents = [q.name().lower() for q in p.parents()]
        host = "Herdr" if "herdr.exe" in parents else "Desktop app" if "claude.exe" in parents else "terminal"
        names = {}
        for c in kids:
            try:
                names[c.name()] = names.get(c.name(), 0) + 1
            except psutil.Error:
                pass
        return {
            "memMB": p.memory_info().rss >> 20,
            "childMemMB": kid_mem >> 20,
            "children": [f"{n} ×{k}" if k > 1 else n for n, k in sorted(names.items())],
            "host": host,
        }
    except psutil.Error:  # a child exited mid-walk; the session itself is still alive
        return {}


def plans_by_owner():
    owners = {}
    for f in glob.glob(os.path.join(PLANS, "*.md")):
        try:
            head = open(f, encoding="utf-8").read(4000).split("\n---", 1)[0]
        except OSError:
            continue
        meta = dict(re.findall(r"^(\w+):[ \t]*(.*)$", head, re.M))
        if meta.get("owner"):
            owners.setdefault(meta["owner"].strip(), []).append(
                {"title": meta.get("title", os.path.basename(f)), "status": meta.get("status", "?"),
                 "file": os.path.basename(f)})
    return owners


def resume_cmd(cwd, sid):
    return f"cd '{cwd}'; claude --resume {sid}" if cwd else f"claude --resume {sid}"


# ---------- snapshot ----------

def snapshot():
    owners = plans_by_owner()
    running, live_ids = [], set()
    for f in glob.glob(os.path.join(SESSIONS, "*.json")):  # never *.key
        try:
            reg = json.load(open(f, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sid = reg.get("sessionId")
        proc = proc_info(reg.get("pid"), reg.get("procStart")) if reg.get("pid") else {}
        if proc is None or not sid:
            continue
        live_ids.add(sid)
        cwd = reg.get("cwd")
        path = transcript_path(cwd, sid)
        tr = read_transcript(path) if path else {}
        name = reg.get("name") or (tr.get("names") or ["?"])[0]
        running.append({
            # Explicit whitelist: messagingSocketPath, keys and tokens never leave the server.
            "name": name,
            "sessionId": sid,
            "cwd": cwd,
            "entrypoint": reg.get("entrypoint"),
            "status": reg.get("status") or "?",
            "statusSince": reg.get("statusUpdatedAt") or reg.get("updatedAt"),
            "startedAt": reg.get("startedAt"),
            "version": reg.get("version"),
            "otherNames": [n for n in tr.get("names", []) if n != name][:3],
            "plans": owners.get(name, []),
            "request": tr.get("request"),
            "reply": tr.get("reply"),
            "lastActivity": tr.get("mtime"),
            "resume": resume_cmd(cwd, sid),
            **proc,
        })
    rank = {"busy": 0, "shell": 1}
    running.sort(key=lambda s: (rank.get(s["status"], 2), s["name"].lower()))
    rec = recent(live_ids)
    return {"now": time.time() * 1000, "running": running, "recent": rec, "recentReady": _recent_cache["at"] > 0}


_recent_cache = {"at": 0, "data": [], "scanning": False}
_recent_lock = threading.Lock()


def recent(live_ids):
    """Named sessions with transcripts touched in the last week that aren't running now.

    The scan reads ~100 transcript tails (seconds when cold), so it runs in the background
    every 30 s and requests get the last result straight away; [] until the first scan ends.
    """
    with _recent_lock:
        if time.time() - _recent_cache["at"] >= 30 and not _recent_cache["scanning"]:
            _recent_cache["scanning"] = True
            threading.Thread(target=_scan_recent, daemon=True).start()
    return [r for r in _recent_cache["data"] if r["sessionId"] not in live_ids]


def _scan_recent():
    try:
        _recent_cache.update(at=time.time(), data=_find_recent())
    finally:
        _recent_cache["scanning"] = False


def _find_recent():
    cutoff = time.time() - RECENT_DAYS * 86400
    out = []
    for path in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl")):
        try:
            if os.path.getmtime(path) < cutoff:
                continue
        except OSError:
            continue
        sid = os.path.basename(path)[:-6]
        tr = read_transcript(path)
        if not tr.get("names"):
            continue  # unnamed one-offs would drown the list
        out.append({"name": tr["names"][0], "otherNames": tr["names"][1:3], "sessionId": sid,
                    "cwd": tr.get("cwd"), "lastActivity": tr.get("mtime"), "request": tr.get("request"),
                    "resume": resume_cmd(tr.get("cwd"), sid)})
    out.sort(key=lambda r: -(r["lastActivity"] or 0))
    return out


# ---------- server ----------

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # Reject other Host headers so a web page can't read this via DNS rebinding.
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return self.send_error(403)
        if self.path == "/api/sessions":
            body, ctype = json.dumps(snapshot()).encode(), "application/json"
        elif self.path in ("/", "/index.html"):
            body, ctype = PAGE.encode(), "text/html; charset=utf-8"
        else:
            return self.send_error(404)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # polling every 2s would flood the terminal


class Server(ThreadingHTTPServer):
    # Python sets SO_REUSEADDR, which on Windows lets a second process bind the same port.
    # Bind exclusively so a second start is detected (and nothing else can share the port).
    allow_reuse_address = sys.platform != "win32"

    def server_bind(self):
        if sys.platform == "win32":
            import socket
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Claude sessions</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Google+Sans+Flex:wght@400..700&family=IBM+Plex+Mono:wght@400;500&display=swap">
<style>
  :root {
    --paper: #F7F6F2; --card: #FFFFFF; --ink: #1F2328; --muted: #686C73; --faint: #8F939A;
    --line: #E8E5DE; --inset: #F5F3EE;
    --busy: #9A5C00; --busy-bg: #FFF0D2; --busy-dot: #E39A12; --busy-tint: #FFFCF6; --busy-line: #F1D9A8;
    --live: #2F8F5B;
    --sans: "Google Sans Flex", system-ui, -apple-system, "Segoe UI", sans-serif;
    --mono: "IBM Plex Mono", ui-monospace, "Cascadia Mono", Consolas, monospace;
    --ease: cubic-bezier(.2, .75, .25, 1);
    --shadow: 0 1px 2px rgba(31, 35, 40, .04), 0 6px 20px rgba(31, 35, 40, .045);
    --shadow-busy: 0 1px 2px rgba(120, 75, 0, .06), 0 10px 30px rgba(160, 100, 0, .10);
  }
  * { box-sizing: border-box }
  html { background: var(--paper) }
  body { margin: 0; color: var(--ink); font: 15px/1.5 var(--sans); -webkit-font-smoothing: antialiased }
  .mono { font-family: var(--mono); font-size: 12.5px; letter-spacing: -.01em }
  .wrap { max-width: 1760px; margin: 0 auto; padding: 40px 40px 64px }

  /* header */
  .head { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px 32px; flex-wrap: wrap;
          margin-bottom: 28px }
  h1 { font-size: 26px; font-weight: 650; letter-spacing: -.02em; margin: 0 0 4px }
  .counts { color: var(--muted); font-size: 15px }
  .counts b { font-weight: 600; color: var(--ink) }
  .counts .n-busy { color: var(--busy) }
  .live { display: flex; align-items: center; gap: 8px; color: var(--muted); font-size: 13px }
  .live .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--live); animation: breathe 2.4s ease-in-out infinite }
  .live.off .dot { background: var(--faint); animation: none }
  .banner { display: none; margin: -12px 0 24px; padding: 12px 16px; border-radius: 12px; background: #FDECEC;
            color: #8A1F1F; font-size: 14px }
  .banner.show { display: block }

  /* cards */
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 400px), 1fr)); gap: 18px;
          align-items: start }
  .card { background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 20px 22px 18px;
          box-shadow: var(--shadow);
          transition: background-color .5s var(--ease), border-color .5s var(--ease), box-shadow .5s var(--ease) }
  .card.busy { background: var(--busy-tint); border-color: var(--busy-line); box-shadow: var(--shadow-busy) }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px }
  .name { font-size: 18px; font-weight: 620; letter-spacing: -.01em; margin: 0; overflow-wrap: anywhere }
  .pill { flex: none; display: inline-flex; align-items: center; gap: 7px; padding: 4px 11px 4px 9px;
          border-radius: 999px; background: #F0EFEB; color: var(--muted); font-size: 13px; font-weight: 550;
          transition: background-color .5s var(--ease), color .5s var(--ease) }
  .pill .dot { position: relative; width: 7px; height: 7px; border-radius: 50%; background: #A3A7AD;
               transition: background-color .5s var(--ease) }
  .pill .time { font-family: var(--mono); font-size: 12px; font-weight: 400; font-variant-numeric: tabular-nums }
  .pill.busy { background: var(--busy-bg); color: var(--busy) }
  .pill.busy .dot { background: var(--busy-dot) }
  .pill.busy .dot::after { content: ""; position: absolute; inset: -4px; border-radius: 50%;
                           border: 2px solid var(--busy-dot); opacity: 0; animation: ping 1.8s var(--ease) infinite }

  .facts { display: grid; grid-template-columns: 92px 1fr; gap: 5px 14px; margin: 0 0 16px; font-size: 13px }
  .facts dt { color: var(--muted) }
  .facts dd { margin: 0; min-width: 0; overflow-wrap: anywhere; font-family: var(--mono); font-size: 12.5px;
              padding-top: 1px }
  .facts .sans { font-family: var(--sans); font-size: 13px; padding-top: 0 }
  .facts .soft { color: var(--muted) }

  .brief { display: grid; gap: 8px }
  .plan { font-size: 14px; line-height: 1.4 }
  .plan .lead { color: var(--muted) }
  .plan .state { color: var(--muted); font-size: 13px; margin-left: 6px; white-space: nowrap }
  .snip { display: block; width: 100%; text-align: left; border: 0; border-radius: 12px; background: var(--inset);
          padding: 10px 12px; color: var(--ink); cursor: pointer; font: inherit;
          transition: background-color .2s var(--ease) }
  .snip:hover { background: #EFEDE6 }
  .snip .who { display: flex; justify-content: space-between; gap: 10px; color: var(--muted); font-size: 12.5px;
               margin-bottom: 3px }
  .snip .text { display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
                font-family: var(--mono); font-size: 12.5px; line-height: 1.55; overflow-wrap: anywhere;
                white-space: pre-wrap }
  .snip.open .text { display: block }
  .empty-brief { color: var(--faint); font-size: 13px }

  .actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 16px }
  button.copy { border: 1px solid var(--line); background: var(--card); color: var(--ink); border-radius: 9px;
                padding: 6px 12px; font: 500 13px var(--sans); cursor: pointer;
                transition: background-color .2s var(--ease), border-color .2s var(--ease), color .2s var(--ease) }
  button.copy:hover { border-color: #CFCBC2 }
  button.copy.done { background: var(--ink); border-color: var(--ink); color: #fff }
  button.copy.fail { border-color: #D9A0A0; color: #8A1F1F }
  .note { color: var(--faint); font-size: 12.5px }
  :focus-visible { outline: 2px solid var(--ink); outline-offset: 2px }

  .empty { grid-column: 1 / -1; padding: 48px 0; color: var(--muted); text-align: center }

  /* recent */
  .recent { margin-top: 40px; border-top: 1px solid var(--line); padding-top: 8px }
  .recent > summary { list-style: none; cursor: pointer; display: flex; align-items: baseline; gap: 10px;
                      padding: 12px 0; font-size: 17px; font-weight: 600 }
  .recent > summary::-webkit-details-marker { display: none }
  .recent > summary .count { color: var(--muted); font-weight: 400; font-size: 14px }
  .recent > summary .chev { display: inline-block; color: var(--muted); transition: transform .3s var(--ease) }
  .recent[open] > summary .chev { transform: rotate(90deg) }
  .rows { background: var(--card); border: 1px solid var(--line); border-radius: 16px; box-shadow: var(--shadow);
          overflow: hidden }
  .row { display: grid; grid-template-columns: minmax(180px, 240px) 1fr auto; gap: 6px 20px; align-items: center;
         padding: 14px 20px; border-top: 1px solid var(--line) }
  .row:first-child { border-top: 0 }
  .row .rname { font-weight: 600; overflow-wrap: anywhere }
  .row .rmeta { color: var(--muted); font-size: 12px; overflow-wrap: anywhere }
  .row .rreq { color: var(--muted); overflow: hidden; white-space: nowrap; text-overflow: ellipsis; min-width: 0 }
  .row .actions { margin-top: 0; flex-wrap: nowrap }

  @keyframes breathe { 50% { opacity: .35 } }
  @keyframes ping { 0% { transform: scale(.6); opacity: .55 } 80%, 100% { transform: scale(1.6); opacity: 0 } }

  @media (max-width: 720px) {
    .wrap { padding: 24px 16px 48px }
    h1 { font-size: 22px }
    .card { padding: 18px 16px 16px; border-radius: 16px }
    .facts { grid-template-columns: 84px 1fr }
    .row { grid-template-columns: 1fr; padding: 14px 16px }
    .row .rreq { white-space: normal; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical }
    .row .actions { flex-wrap: wrap }
  }
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important }
  }
</style></head><body>
<div class="wrap">
  <header class="head">
    <div>
      <h1>Claude sessions</h1>
      <div class="counts" id="counts">Looking for running sessions…</div>
    </div>
    <div class="live" id="live"><span class="dot"></span><span id="liveText">Connecting</span></div>
  </header>
  <div class="banner" id="banner" role="alert"></div>
  <main class="grid" id="grid" aria-live="polite"></main>
  <details class="recent" id="recentBox">
    <summary><span class="chev" aria-hidden="true">›</span><span>Recent sessions, not running</span>
      <span class="count" id="recentCount"></span></summary>
    <div class="rows" id="recent"></div>
  </details>
</div>
<script>
const $ = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls;
  if (text != null) e.textContent = text; return e; };
const setText = (el, v) => { v = v == null ? "" : String(v); if (el.textContent !== v) el.textContent = v; };
const reduce = matchMedia("(prefers-reduced-motion: reduce)");
const grid = document.getElementById("grid");
const pad = n => String(n).padStart(2, "0");
const ts = v => typeof v === "string" ? Date.parse(v) : v;

// "4 min", "2.5 h": for things that change slowly. clock(): busy timer, ticks every second.
function span(ms) {
  if (!ms && ms !== 0) return "?";
  const s = Math.max(0, ms / 1000);
  if (s < 60) return Math.floor(s) + " s";
  if (s < 3600) return Math.floor(s / 60) + " min";
  if (s < 86400) { const h = s / 3600; return (h < 10 ? h.toFixed(1).replace(".0", "") : Math.round(h)) + " h"; }
  return Math.floor(s / 86400) + " d";
}
function clock(ms) {
  const s = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60);
  return h ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}
const since = t => t ? span(Date.now() - t) : "?";
const statusName = s => ({ busy: "Busy", idle: "Idle", shell: "Shell" })[s] || (s ? s[0].toUpperCase() + s.slice(1) : "?");

// Clipboard API first; if it's missing, fails or stalls (e.g. behind a pending permission prompt),
// fall back to the selection-based copy so the button always ends in "Copied" or "Copy failed".
async function writeClipboard(text) {
  try {
    await Promise.race([navigator.clipboard.writeText(text),
                        new Promise((_, reject) => setTimeout(() => reject(new Error("timeout")), 800))]);
  } catch {
    const ta = $("textarea"); ta.value = text; ta.readOnly = true;
    ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
    document.body.append(ta); ta.select();
    const ok = document.execCommand("copy"); ta.remove();
    if (!ok) throw new Error("copy failed");
  }
}

function copyButton(label) {
  const b = $("button", "copy", label); b.type = "button";
  b.onclick = async () => {
    clearTimeout(b._t);
    try { await writeClipboard(b.dataset.value); b.classList.add("done"); b.textContent = "Copied ✓"; }
    catch { b.classList.add("fail"); b.textContent = "Copy failed"; }
    b._t = setTimeout(() => { b.classList.remove("done", "fail"); b.textContent = label; }, 1400);
  };
  return b;
}

function snippet() {
  const b = $("button", "snip"); b.type = "button"; b.setAttribute("aria-expanded", "false");
  const who = $("span", "who"); const label = $("span"); const when = $("span", "mono");
  who.append(label, when); const text = $("span", "text"); b.append(who, text);
  b.onclick = () => flip(() => { const open = b.classList.toggle("open"); b.setAttribute("aria-expanded", open); });
  return { el: b, label, when, text };
}

// One card per session, built once and then updated in place so nothing flickers.
function makeCard() {
  const el = $("article", "card");
  const top = $("div", "top"), name = $("h2", "name"), pill = $("span", "pill");
  const dot = $("span", "dot"), pillText = $("span"), pillTime = $("span", "time");
  pill.append(dot, pillText, pillTime); top.append(name, pill);
  const facts = $("dl", "facts"); const f = {};
  for (const [key, label, sans] of [["folder", "Folder"], ["id", "Session"], ["via", "Runs in", 1],
       ["up", "Running for", 1], ["mem", "Memory", 1], ["aka", "Also named", 1]]) {
    const dt = $("dt", null, label), dd = $("dd", sans ? "sans" : null);
    facts.append(dt, dd); f[key] = { dt, dd };
  }
  const brief = $("div", "brief");
  const plans = $("div"); const req = snippet(), reply = snippet(); const none = $("div", "empty-brief", "No messages yet");
  brief.append(plans, req.el, reply.el, none);
  const actions = $("div", "actions");
  const copyId = copyButton("Copy ID"), copyResume = copyButton("Copy resume command");
  const note = $("span", "note");
  actions.append(copyId, copyResume, note);
  el.append(top, facts, brief, actions);
  return { el, name, pill, pillText, pillTime, f, plans, req, reply, none, copyId, copyResume, note, data: null };
}

function showFact(row, value) {
  const has = value != null && value !== "";
  row.dt.hidden = row.dd.hidden = !has;
  if (has) setText(row.dd, value);
}

function updateCard(c, s) {
  c.data = s;
  const st = ["busy", "idle", "shell"].includes(s.status) ? s.status : "other";
  c.el.classList.toggle("busy", st === "busy");
  c.pill.className = "pill " + st;
  setText(c.name, s.name);
  setText(c.pillText, statusName(s.status));
  showFact(c.f.folder, s.cwd || "?");
  showFact(c.f.id, s.sessionId.slice(0, 8) + "…");
  c.f.id.dd.title = s.sessionId;
  showFact(c.f.via, s.host || (s.entrypoint === "claude-desktop" ? "Desktop app" : "Terminal"));
  let mem = s.memMB != null ? s.memMB + " MB" : "";
  if (s.childMemMB) mem += ` plus ${s.childMemMB} MB in ${(s.children || []).length || "its"} child process${(s.children || []).length === 1 ? "" : "es"}`;
  showFact(c.f.mem, mem);
  c.f.mem.dd.title = (s.children || []).join(", ");
  showFact(c.f.aka, (s.otherNames || []).join(", "));

  const planKey = JSON.stringify(s.plans || []);
  if (c.plans.dataset.key !== planKey) {
    c.plans.dataset.key = planKey;
    c.plans.replaceChildren(...(s.plans || []).map(p => {
      const d = $("div", "plan"); d.append($("span", "lead", "Working on "), $("span", null, p.title), $("span", "state", (p.status || "").replace("-", " ")));
      return d;
    }));
  }
  fillSnip(c.req, s.request && (s.request.from ? "Message from " + s.request.from : "Asked"),
           s.request && s.request.text, s.request && ts(s.request.ts));
  fillSnip(c.reply, s.reply && "Replied", s.reply && s.reply.text, s.reply && ts(s.reply.ts));
  c.none.hidden = !!(s.request || s.reply);

  c.copyId.dataset.value = s.sessionId;
  c.copyResume.dataset.value = s.resume;
  setText(c.note, s.entrypoint === "claude-desktop" ? "Lives in the Desktop app" : "");
  tickCard(c);
}

function fillSnip(sn, label, text, t) {
  sn.el.hidden = !text;
  if (!text) return;
  setText(sn.label, label); setText(sn.text, text);
  sn.t = t; setText(sn.when, t ? since(t) + " ago" : "");
}

function tickCard(c) {
  const s = c.data; if (!s) return;
  const t = ts(s.statusSince);
  setText(c.pillTime, !t ? "" : s.status === "busy" ? clock(Date.now() - t) : "for " + span(Date.now() - t));
  setText(c.f.up.dd, since(s.startedAt));
  for (const sn of [c.req, c.reply]) if (sn.t) setText(sn.when, since(sn.t) + " ago");
}

// FLIP: measure, change the DOM, then glide every card from its old spot to its new one.
function flip(mutate) {
  const before = new Map([...grid.children].map(e => [e, e.getBoundingClientRect()]));
  mutate();
  if (reduce.matches) return;
  for (const e of grid.children) {
    const b = before.get(e); if (!b) continue;
    const a = e.getBoundingClientRect(), dx = b.left - a.left, dy = b.top - a.top;
    if (Math.abs(dx) > .5 || Math.abs(dy) > .5)
      e.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
                { duration: 450, easing: "cubic-bezier(.2, .75, .25, 1)" });
  }
}

const cards = new Map();
let firstRender = true;

function render(list) {
  const entering = [];
  flip(() => {
    const seen = new Set();
    for (const s of list) {
      let c = cards.get(s.sessionId);
      if (!c) { c = makeCard(); cards.set(s.sessionId, c); entering.push(c.el); }
      updateCard(c, s); seen.add(s.sessionId);
    }
    for (const [id, c] of cards) if (!seen.has(id)) { cards.delete(id); leave(c.el); }
    const want = list.map(s => cards.get(s.sessionId).el);
    const have = [...grid.children].filter(e => !e.dataset.leaving);
    if (want.length !== have.length || want.some((e, i) => have[i] !== e)) want.forEach(e => grid.append(e));
    grid.querySelector(".empty")?.remove();
    if (!list.length) grid.append($("div", "empty", "No Claude sessions are running right now."));
  });
  if (!reduce.matches) entering.forEach((e, i) => e.animate(
    [{ opacity: 0, transform: "translateY(10px)" }, { opacity: 1, transform: "none" }],
    { duration: 420, delay: firstRender ? i * 45 : 0, easing: "cubic-bezier(.2, .75, .25, 1)", fill: "backwards" }));
  firstRender = false;
}

function leave(el) {
  el.dataset.leaving = "1";
  const done = () => flip(() => el.remove());
  if (reduce.matches) return done();
  el.animate([{ opacity: 1 }, { opacity: 0, transform: "scale(.98)" }], { duration: 220, fill: "forwards" }).onfinish = done;
}

let recentKey = "";
function renderRecent(list, ready) {
  if (!ready) return setText(document.getElementById("recentCount"), "Loading");
  setText(document.getElementById("recentCount"), list.length ? `${list.length} in the last 7 days` : "none in the last 7 days");
  const key = JSON.stringify(list.map(r => [r.sessionId, r.name, r.lastActivity, r.request && r.request.text]));
  if (key === recentKey) return;
  recentKey = key;
  document.getElementById("recent").replaceChildren(...list.map(r => {
    const row = $("div", "row");
    const left = $("div"); left.append($("div", "rname", r.name),
      $("div", "rmeta mono", `${since(r.lastActivity)} ago in ${r.cwd || "?"}`));
    const req = $("div", "rreq mono", r.request ? (r.request.from ? `Message from ${r.request.from}: ` : "") + r.request.text : "");
    const a = $("div", "actions");
    const id = copyButton("Copy ID"); id.dataset.value = r.sessionId;
    const res = copyButton("Copy resume command"); res.dataset.value = r.resume;
    a.append(id, res); row.append(left, req, a); return row;
  }));
}

async function refresh() {
  const live = document.getElementById("live"), banner = document.getElementById("banner");
  try {
    const r = await fetch("/api/sessions", { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    const d = await r.json();
    render(d.running);
    renderRecent(d.recent, d.recentReady);
    const n = st => d.running.filter(s => s.status === st).length;
    const busy = n("busy"), idle = n("idle"), shell = n("shell"), other = d.running.length - busy - idle - shell;
    const counts = document.getElementById("counts");
    if (!d.running.length) setText(counts, "Nothing running on this machine");
    else {
      const part = (cls, text) => { const b = $("b", cls, text); return b; };
      const parts = [part("n-busy", busy + " busy"), part(null, idle + " idle")];
      if (shell) parts.push(part(null, shell + " in a shell"));
      if (other) parts.push(part(null, other + " other"));
      counts.replaceChildren(...parts.flatMap((p, i) => i ? [document.createTextNode(", "), p] : [p]));
    }
    live.classList.remove("off");
    setText(document.getElementById("liveText"), "Live, updated " + new Date().toLocaleTimeString());
    banner.classList.remove("show");
  } catch (e) {
    live.classList.add("off");
    setText(document.getElementById("liveText"), "Offline");
    banner.textContent = "Can't reach the dashboard server. Start it again with agents-dash, then this page reconnects on its own.";
    banner.classList.add("show");
  }
}

setInterval(() => cards.forEach(tickCard), 1000);
setInterval(refresh, 2000);
refresh();
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Live dashboard of running Claude sessions (127.0.0.1 only).")
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    args = ap.parse_args()
    url = f"http://127.0.0.1:{args.port}/"
    try:
        server = Server(("127.0.0.1", args.port), Handler)
    except OSError:
        print(f"port {args.port} is busy; assuming the dashboard is already running at {url}")
        if not args.no_open:
            webbrowser.open(url)
        sys.exit(0)
    print(f"agents-dash: {url}  (Ctrl+C to stop)")
    snapshot()  # starts the background scan for the "recent" list before the page asks
    if psutil is None:
        print("note: psutil not installed; can't verify sessions are alive or show memory")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
