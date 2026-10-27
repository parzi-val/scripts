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
from datetime import datetime
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
        host = "Herdr" if "herdr.exe" in parents else "Desktop app" if "claude.exe" in parents else "Terminal"
        return {
            "memMB": p.memory_info().rss >> 20,
            "childMemMB": kid_mem >> 20,
            "host": host,
            "childCount": len(kids),
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

def _msg(m, outgoing=False):
    """Transcript request/reply -> the page's Message ({from, at, text}); None if missing."""
    if not m:
        return None
    try:
        at = datetime.fromisoformat(m["ts"].replace("Z", "+00:00")).timestamp() * 1000
    except (KeyError, AttributeError, ValueError):
        at = None
    out = {"at": at, "text": m["text"]}
    if not outgoing:
        out["from"] = m.get("from") or "you"
    return out


def _session(name, sid, cwd, state, since, owners, tr, proc=None):
    group, _, short = name.partition("-")
    plans = owners.get(name, [])
    return {
        # Explicit whitelist: messagingSocketPath, keys and tokens never leave the server.
        "name": name, "id": short or name, "group": group,
        "state": state, "stateSince": since,
        "folder": cwd, "sessionId": sid,
        "runsIn": proc.get("host") if proc is not None else None,
        "memoryMB": proc.get("memMB") if proc is not None else None,
        "children": {"count": proc["childCount"], "memoryMB": proc["childMemMB"]}
        if proc and proc.get("childCount") else None,
        "workingOn": plans[0]["title"] if plans else None,
        "lastIn": _msg(tr.get("request")), "lastOut": _msg(tr.get("reply"), True),
        "resume": resume_cmd(cwd, sid),
    }


def snapshot():
    """Session[] in the shape the page's loadSessions() documents."""
    owners = plans_by_owner()
    out, live_ids = [], set()
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
        state = "busy" if reg.get("status") == "busy" else "idle"
        s = _session(name, sid, cwd, state, reg.get("statusUpdatedAt") or reg.get("updatedAt"), owners, tr, proc)
        s["startedAt"] = reg.get("startedAt")
        out.append(s)
    # A manager that isn't running still gets a row, so it can be resumed from here.
    if not any(s["group"] == "manager" for s in out):
        r = next((r for r in recent(live_ids) if r["name"].partition("-")[0] == "manager"), None)
        if r:
            s = _session(r["name"], r["sessionId"], r["cwd"], "stopped", r["lastActivity"], owners, r["tr"])
            s["startedAt"] = None
            out.append(s)
    # Two groups can share a short name (dev-lead, research-lead); fall back to the full name.
    for s in out:
        if sum(o["id"] == s["id"] for o in out) > 1:
            s["id"] = s["name"]
    return out


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
        out.append({"name": tr["names"][0], "sessionId": sid, "cwd": tr.get("cwd"),
                    "lastActivity": tr.get("mtime"), "tr": tr})
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
<html lang="en" data-theme="light">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Claude sessions</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:opsz,wght@14..32,400..700&family=JetBrains+Mono:wght@400;500&display=swap">
<script>
  // Set the theme before first paint: saved choice, else the OS preference.
  (function () {
    var t = null;
    try { t = localStorage.getItem('sessions-theme'); } catch (e) {}
    if (t !== 'light' && t !== 'dark') {
      t = window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }
    document.documentElement.setAttribute('data-theme', t);
  })();
</script>
<style>
/* ------------------------------------------------------------------
   Design tokens. Two neutral greys per theme + one accent:
   light = orange, dark = sky blue. The accent only marks "busy"
   and the live dot; everything else stays monochrome.
   ------------------------------------------------------------------ */
:root,
:root[data-theme="light"] {
  color-scheme: light;
  --bg: #fcfcfc;
  --sidebar: #f5f5f6;
  --surface: #ffffff;
  --raised: #f7f7f8;
  --hover: #efeff1;
  --sel: #e9e9ec;
  --line: #e9e9eb;
  --line-strong: #dcdce0;
  --ink: #1c1c1f;
  --muted: #5e5e66;
  --label: #6b6b74;
  --idle: #8b8b93;
  --chip-bg: #f0f0f2;
  --chip-ink: #3b3b41;
  --accent: #e2730f;
  --accent-ink: #a9500b;
  --accent-bg: #fdf1e4;
  --avatar: #ececef;
  --in-bg: #ffffff;
  --out-bg: #f3f3f5;
  --out-line: #f3f3f5;
  --pri-bg: #1c1c1f;
  --pri-ink: #ffffff;
  --sec-bg: #ffffff;
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --bg: #0f0f10;
  --sidebar: #0a0a0b;
  --surface: #151516;
  --raised: #19191b;
  --hover: #1a1a1c;
  --sel: #1f1f22;
  --line: #232326;
  --line-strong: #2f2f33;
  --ink: #ededef;
  --muted: #a1a1a8;
  --label: #8b8b93;
  --idle: #8b8b93;
  --chip-bg: #1f1f22;
  --chip-ink: #d4d4d8;
  --accent: #6ec3f2;
  --accent-ink: #96d4f7;
  --accent-bg: #13212a;
  --avatar: #26262a;
  --in-bg: #151516;
  --out-bg: #1e1e21;
  --out-line: #2a2a2e;
  --pri-bg: #ededef;
  --pri-ink: #0f0f10;
  --sec-bg: #151516;
}

*, *::before, *::after { box-sizing: border-box; }
html, body { margin: 0; }
body {
  background: var(--bg);
  color: var(--ink);
  font-family: 'Inter', ui-sans-serif, system-ui, -apple-system, 'Segoe UI', sans-serif;
  font-size: 13px;
  line-height: 1.45;
  -webkit-font-smoothing: antialiased;
}
.mono { font-family: 'JetBrains Mono', ui-monospace, Menlo, Consolas, monospace; }
a { color: inherit; text-decoration: none; }
button { font: inherit; color: inherit; }
:focus-visible { outline: 2px solid var(--ink); outline-offset: 2px; border-radius: 6px; }

/* ---------- Shell ---------- */
.app { min-height: 100vh; display: flex; flex-wrap: wrap; align-items: stretch; }
.sidebar {
  flex: 1 1 264px; min-width: 0;
  background: var(--sidebar); border-right: 1px solid var(--line);
  padding: 8px 8px 24px; display: flex; flex-direction: column; gap: 14px;
}
.main { flex: 999 1 560px; min-width: 0; display: flex; flex-direction: column; }
@media (min-width: 900px) {
  .app { flex-wrap: nowrap; }
  .sidebar { flex: 0 0 264px; position: sticky; top: 0; height: 100vh; overflow-y: auto; }
}

/* ---------- Sidebar ---------- */
.brand { min-height: 44px; display: flex; align-items: center; justify-content: space-between; gap: 8px; padding: 0 10px; }
.brand-name { font-size: 14px; font-weight: 600; letter-spacing: -0.01em; }
.live { display: inline-flex; align-items: center; gap: 6px; font-size: 11px; color: var(--label); }
.live-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); }

.nav-link { display: flex; align-items: center; gap: 10px; min-height: 36px; padding: 0 10px; border-radius: 8px; font-weight: 500; }
.nav-link:hover { background: var(--hover); }
.nav-link[aria-current="page"] { background: var(--sel); }

.nav-group { display: flex; flex-direction: column; gap: 1px; }
.nav-group-head { display: flex; justify-content: space-between; align-items: baseline; padding: 4px 10px; font-size: 12px; font-weight: 500; color: var(--label); }
.nav-group-head .mono { font-size: 11px; }
.nav-item {
  display: grid; grid-template-columns: 14px minmax(0, 1fr) auto;
  column-gap: 10px; row-gap: 2px; align-items: center;
  min-height: 48px; padding: 7px 10px; border-radius: 8px;
}
.nav-item:hover { background: var(--hover); }
.nav-item[aria-current="page"] { background: var(--sel); }
.nav-item .name { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.nav-item .time { font-size: 11px; color: var(--label); }
.nav-item .preview { grid-column: 2 / 4; font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ---------- Status icons ---------- */
.si { display: block; flex: 0 0 auto; }
.si-busy { color: var(--accent); }
.si-idle, .si-stopped { color: var(--idle); }
.si-busy .core { animation: breathe 2s ease-in-out infinite; }
@keyframes breathe { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
@media (prefers-reduced-motion: reduce) { .si-busy .core { animation: none; } }
.is-busy { color: var(--accent-ink) !important; }

/* ---------- Top bar ---------- */
.topbar {
  min-height: 52px; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between;
  gap: 4px 16px; padding: 4px clamp(16px, 2.5vw, 28px); border-bottom: 1px solid var(--line);
}
.crumbs { display: flex; align-items: center; gap: 2px; min-width: 0; font-size: 13px; }
.crumbs a { color: var(--muted); padding: 4px 6px; border-radius: 6px; }
.crumbs a:hover { color: var(--ink); background: var(--hover); }
.crumbs .sep { color: var(--label); display: flex; }
.crumbs .dim { color: var(--muted); padding: 4px 6px; }
.crumbs .here { font-weight: 500; padding: 4px 6px; }
.topbar-right { display: flex; align-items: center; gap: 14px; }
.counts { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12px; color: var(--muted); }
.counts > span { display: inline-flex; align-items: center; gap: 6px; }
.counts b { color: var(--ink); font-weight: 500; }
.icon-btn {
  width: 30px; height: 30px; display: grid; place-items: center; padding: 0; cursor: pointer;
  border: 1px solid var(--line-strong); border-radius: 7px; background: var(--sec-bg); color: var(--muted);
}
.icon-btn:hover { color: var(--ink); background: var(--hover); }

/* ---------- Page ---------- */
.content { padding: clamp(20px, 3vw, 36px) clamp(16px, 2.5vw, 28px) 48px; }
.page { max-width: 1120px; display: flex; flex-direction: column; gap: 24px; }
.h1 { margin: 0; font-size: 22px; line-height: 1.2; font-weight: 600; letter-spacing: -0.015em; }
.sub { margin: 4px 0 0; color: var(--muted); }
.label { margin: 0; display: flex; align-items: baseline; gap: 8px; font-size: 12px; font-weight: 500; color: var(--label); }
.label .mono { font-size: 11px; }
.panel { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; }
.cols { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
.col-main { flex: 3 1 460px; min-width: 0; display: flex; flex-direction: column; gap: 24px; }
.col-side { flex: 2 1 300px; min-width: 0; display: flex; flex-direction: column; gap: 10px; }
.section { display: flex; flex-direction: column; gap: 10px; }
.empty { padding: 16px; color: var(--muted); }

/* ---------- Buttons ---------- */
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 6px;
  height: 30px; padding: 0 11px; border-radius: 7px; cursor: pointer; white-space: nowrap;
  border: 1px solid var(--line-strong); background: var(--sec-bg); color: var(--ink);
  font-size: 12.5px; font-weight: 500;
}
.btn:hover { background: var(--hover); }
.btn-primary { background: var(--pri-bg); border-color: var(--pri-bg); color: var(--pri-ink); }
.btn-primary:hover { background: var(--pri-bg); opacity: 0.88; }
.btn-block { width: 100%; height: 32px; }
@media (pointer: coarse) {
  .btn, .btn-block { height: 44px; }
  .nav-link { min-height: 44px; }
  .icon-btn { width: 44px; height: 44px; }
}

/* ---------- Home: not-running banner ---------- */
.banner { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 16px; padding: 8px 8px 8px 14px; }
.banner-text { display: flex; align-items: center; gap: 10px; min-width: 0; }
.banner-text .dim { color: var(--muted); }
.actions { display: flex; flex-wrap: wrap; gap: 6px; }

/* ---------- Home: working now ---------- */
.work-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(260px, 100%), 1fr)); gap: 10px; }
.work-card { display: flex; flex-direction: column; gap: 10px; padding: 14px 16px; transition: border-color 0.12s; }
.work-card:hover { border-color: var(--line-strong); }
.work-top { display: flex; align-items: center; gap: 8px; }
.work-top .name { flex: 1 1 auto; min-width: 0; font-size: 14px; font-weight: 600; letter-spacing: -0.01em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.work-top .timer { font-size: 12px; }
.meta { display: flex; flex-wrap: wrap; gap: 2px 12px; font-size: 11.5px; color: var(--muted); }
.last { display: flex; flex-direction: column; gap: 3px; padding-top: 10px; border-top: 1px solid var(--line); }
.last-head { font-size: 12px; color: var(--muted); }
.last-head b { color: var(--ink); font-weight: 500; }
.clamp2 { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }

/* ---------- Home: groups as rows ---------- */
.groups { overflow: hidden; }
.group + .group { border-top: 1px solid var(--line); }
.group-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 8px 14px; background: var(--raised); }
.group-head .name { font-weight: 600; }
.group-head .count { font-size: 11px; color: var(--label); margin-left: 8px; }
.group-head .summary { font-size: 12px; color: var(--muted); }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 2px 12px; min-height: 40px; padding: 6px 14px; border-top: 1px solid var(--line); }
.row:hover { background: var(--hover); }
.row-name { flex: 1 1 160px; min-width: 0; display: flex; align-items: center; gap: 10px; }
.row-name span { font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row-meta { flex: 0 0 auto; display: flex; align-items: center; gap: 12px; font-size: 11.5px; color: var(--muted); }
.row-meta > span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.w-runs { width: 84px; } .w-up { width: 60px; } .w-mem { width: 58px; } .w-state { width: 72px; text-align: right; }

/* ---------- Home: recent activity ---------- */
.feed { list-style: none; margin: 0; padding: 0; overflow: hidden; }
.feed li + li { border-top: 1px solid var(--line); }
.feed a { display: grid; grid-template-columns: 36px minmax(0, 1fr); gap: 10px; align-items: start; padding: 10px 14px; }
.feed a:hover { background: var(--hover); }
.feed .ago { padding-top: 1px; font-size: 11.5px; color: var(--label); }
.feed .body { min-width: 0; display: flex; flex-direction: column; gap: 2px; }
.feed .who { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.feed .who b { font-weight: 500; }
.feed .who i { font-style: normal; color: var(--muted); }
.feed .text { font-size: 12.5px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* ---------- Session ---------- */
.title-row { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 12px; }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 2px 8px; border-radius: 6px; font-size: 12px; font-weight: 500; background: var(--chip-bg); color: var(--chip-ink); }
.chip .mono { font-size: 11.5px; }
.chip-busy { background: var(--accent-bg); color: var(--accent-ink); }
.chip-stopped { background: transparent; border: 1px solid var(--line-strong); padding: 1px 7px; }
.thread { display: flex; flex-direction: column; gap: 18px; }
.msg { max-width: 640px; display: flex; flex-direction: column; gap: 6px; }
.msg-in { align-self: flex-start; align-items: flex-start; }
.msg-out { align-self: flex-end; align-items: flex-end; }
.msg-head { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); }
.msg-head b { color: var(--ink); font-weight: 500; }
.msg-head .mono { font-size: 11.5px; color: var(--label); }
.avatar { width: 20px; height: 20px; border-radius: 50%; display: grid; place-items: center; background: var(--avatar); color: var(--ink); font-size: 10.5px; font-weight: 600; }
.bubble { margin: 0; padding: 12px 14px; border-radius: 10px; font-size: 13.5px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; text-wrap: pretty; }
.msg-in .bubble { background: var(--in-bg); border: 1px solid var(--line); }
.msg-out .bubble { background: var(--out-bg); border: 1px solid var(--out-line); }
.props { display: flex; flex-direction: column; gap: 6px; padding: 6px 14px 14px; }
.props dl { margin: 0; display: grid; grid-template-columns: 92px minmax(0, 1fr); column-gap: 12px; align-items: baseline; }
.props dt { padding: 8px 0; font-size: 12.5px; color: var(--label); }
.props dd { margin: 0; padding: 8px 0; overflow-wrap: anywhere; }
.props dd.mono { font-size: 12px; }
.props .extra { display: block; font-size: 12px; color: var(--muted); }
.props-actions { display: flex; flex-direction: column; gap: 8px; padding-top: 12px; border-top: 1px solid var(--line); }
</style>
</head>
<body>
<div class="app">
  <nav class="sidebar" id="sidebar" aria-label="Sessions"></nav>
  <main class="main">
    <div class="topbar" id="topbar"></div>
    <div class="content" id="content"></div>
  </main>
</div>

<script>
/* ==================================================================
   Claude sessions dashboard. Data comes from /api/sessions (snapshot() in agents_dash.py).

   Routes: #/ (overview)  ·  #/s/<session name> (session page)
   ================================================================== */

/**
 * @typedef {Object} Message
 * @property {string} [from]  sender name; 'you' when the user sent it (lastIn only)
 * @property {number} at      epoch ms
 * @property {string} text
 *
 * @typedef {Object} Session
 * @property {string} name               full session name ('research-max-agent')
 * @property {string} id                 name without the group prefix, unique ('max-agent')
 * @property {string} group              'manager' | 'dev' | 'research' | …
 * @property {'busy'|'idle'|'stopped'} state
 * @property {number} stateSince         epoch ms the current state began
 * @property {string} folder
 * @property {string} sessionId          Claude session id (copied by "Copy ID")
 * @property {string|null} runsIn        'Herdr', 'Desktop app', … (null when stopped)
 * @property {number|null} startedAt     epoch ms the process started (null when stopped)
 * @property {number|null} memoryMB
 * @property {{count:number, memoryMB:number}|null} children   child processes
 * @property {string|null} workingOn
 * @property {Message|null} lastIn       latest message the session received
 * @property {Message|null} lastOut      latest reply the session sent
 * @property {string} resume             "Copy resume command" text
 */

const CONFIG = {
  pollMs: 5000,
  feedSize: 10,
  groupOrder: ['manager', 'dev', 'research'],   // unknown groups go last, A–Z
  resumeCommand: (s) => s.resume,   // built by the server: cd into the folder, then claude --resume
};

async function loadSessions() {
  const res = await fetch('/api/sessions', { cache: 'no-store' });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

/* ---------- State ---------- */
let sessions = [];
let lastSignature = '';
let lastUpdated = null;

const $sidebar = document.getElementById('sidebar');
const $topbar = document.getElementById('topbar');
const $content = document.getElementById('content');

/* ---------- Icons ---------- */
const ICONS = {
  grid: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>',
  chevron: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>',
  moon: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/></svg>',
  sun: '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/></svg>',
  terminal: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3"/><path d="M12.5 15H17"/></svg>',
  copy: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9"/></svg>',
  check: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
};

function statusIcon(state, size = 14) {
  const dash = state === 'stopped' ? ' stroke-dasharray="2.4 2.1"' : '';
  const core = state === 'busy' ? '<circle class="core" cx="7" cy="7" r="2.6" fill="currentColor"/>' : '';
  return `<svg class="si si-${state}" width="${size}" height="${size}" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="5.75" fill="none" stroke="currentColor" stroke-width="1.5"${dash}/>${core}</svg>`;
}

/* ---------- Formatting ---------- */
const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const secSince = (t) => Math.max(0, Math.floor((Date.now() - t) / 1000));
const oneDecimal = (n) => String(Math.round(n * 10) / 10);
const pad2 = (n) => String(n).padStart(2, '0');

const FMT = {
  // 1:39 · 1:02:07
  clock: (s) => { const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), r = s % 60; return h ? `${h}:${pad2(m)}:${pad2(r)}` : `${m}:${pad2(r)}`; },
  // 12s · 16m · 3.4h · 2d
  short: (s) => s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m` : s < 86400 ? `${oneDecimal(s / 3600)}h` : `${Math.floor(s / 86400)}d`,
  // 12 s · 16 min · 3.4 h · 2 d
  long: (s) => s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min` : s < 86400 ? `${oneDecimal(s / 3600)} h` : `${oneDecimal(s / 86400)} d`,
  ago: (s) => `${FMT.long(s)} ago`,
};

// A time that keeps itself current: tick() below rewrites every [data-tick] once a second.
const live = (kind, at) => `<span data-tick="${kind}" data-at="${at}">${FMT[kind](secSince(at))}</span>`;

function tickAll() {
  document.querySelectorAll('[data-tick]').forEach((el) => {
    const next = FMT[el.dataset.tick](secSince(Number(el.dataset.at)));
    if (el.textContent !== next) el.textContent = next;
  });
}

const senderLabel = (from) => (from === 'you' ? 'You' : from);
const shortId = (id) => (id && id.length > 8 ? `${id.slice(0, 8)}…` : id || '—');
const memText = (s) => (s.memoryMB == null ? '—' : `${s.memoryMB} MB`);
const childText = (s) => (s.children ? `+${s.children.memoryMB} MB in ${s.children.count} child process${s.children.count === 1 ? '' : 'es'}` : '');
const upText = (s) => (s.startedAt ? `up ${live('long', s.startedAt)}` : 'up —');

// Status time with an optional word: "busy 1:39", "idle 16m", "off 42m"
function stateTime(s, withWord) {
  if (s.state === 'busy') return `<span class="is-busy">${withWord ? 'busy ' : ''}${live('clock', s.stateSince)}</span>`;
  if (s.state === 'idle') return `<span>${withWord ? 'idle ' : ''}${live('short', s.stateSince)}</span>`;
  return `<span>off ${live('short', s.stateSince)}</span>`;
}

function latest(s) {
  const a = s.lastIn, b = s.lastOut;
  if (!a && !b) return null;
  if (b && (!a || b.at >= a.at)) return { dir: 'out', who: s.id, verb: 'replied', at: b.at, text: b.text };
  return { dir: 'in', who: senderLabel(a.from), verb: a.from === 'you' ? 'asked' : 'sent a message', at: a.at, text: a.text };
}

/* ---------- Derived data ---------- */
const STATE_RANK = { busy: 0, idle: 1, stopped: 2 };
const bySessionOrder = (a, b) => STATE_RANK[a.state] - STATE_RANK[b.state] || b.stateSince - a.stateSince;
const groupRank = (g) => { const i = CONFIG.groupOrder.indexOf(g); return i < 0 ? 999 : i; };

function grouped() {
  const names = [...new Set(sessions.map((s) => s.group))].sort((a, b) => groupRank(a) - groupRank(b) || a.localeCompare(b));
  return names.map((name) => {
    const items = sessions.filter((s) => s.group === name).sort(bySessionOrder);
    const busy = items.filter((s) => s.state === 'busy').length;
    const allStopped = items.every((s) => s.state === 'stopped');
    return { name, items, summary: allStopped ? 'not running' : busy ? `${busy} busy` : 'all idle' };
  });
}

function counts() {
  const c = { busy: 0, idle: 0, stopped: 0 };
  sessions.forEach((s) => { c[s.state] += 1; });
  return c;
}

function feedEvents() {
  const ev = [];
  sessions.forEach((s) => {
    if (s.lastIn) ev.push({ s, dir: 'in', at: s.lastIn.at, from: senderLabel(s.lastIn.from), text: s.lastIn.text });
    if (s.lastOut) ev.push({ s, dir: 'out', at: s.lastOut.at, text: s.lastOut.text });
  });
  return ev.sort((a, b) => b.at - a.at).slice(0, CONFIG.feedSize);
}

/* ---------- Routing ---------- */
function route() {
  const m = location.hash.match(/^#\/s\/(.+)$/);
  if (m) {
    const id = decodeURIComponent(m[1]);
    if (sessions.some((s) => s.id === id)) return { view: 'session', id };
  }
  return { view: 'home', id: null };
}
const sessionHref = (s) => `#/s/${encodeURIComponent(s.id)}`;

/* ---------- Render: sidebar ---------- */
function renderSidebar(r) {
  let h = `
    <div class="brand">
      <span class="brand-name">Claude sessions</span>
      <span class="live mono" title="Last refreshed"><span class="live-dot" aria-hidden="true"></span><span id="updated">${esc(lastUpdated ? lastUpdated.toLocaleTimeString() : '')}</span></span>
    </div>
    <a class="nav-link" href="#/"${r.view === 'home' ? ' aria-current="page"' : ''}>${ICONS.grid}<span>Overview</span></a>`;

  grouped().forEach((g) => {
    h += `<div class="nav-group"><div class="nav-group-head"><span>${esc(g.name)}</span><span class="mono">${g.items.length}</span></div>`;
    g.items.forEach((s) => {
      const l = latest(s);
      const preview = l ? (l.dir === 'out' ? l.text : `${l.who}: ${l.text}`) : '';
      h += `
        <a class="nav-item" href="${sessionHref(s)}"${r.id === s.id ? ' aria-current="page"' : ''}>
          ${statusIcon(s.state)}
          <span class="name">${esc(s.id)}</span>
          <span class="time mono">${stateTime(s, false)}</span>
          <span class="preview">${esc(preview)}</span>
        </a>`;
    });
    h += `</div>`;
  });
  $sidebar.innerHTML = h;
}

/* ---------- Render: top bar ---------- */
function renderTopbar(r) {
  const c = counts();
  const dark = document.documentElement.getAttribute('data-theme') === 'dark';
  let crumbs;
  if (r.view === 'session') {
    const s = sessions.find((x) => x.id === r.id);
    crumbs = `<a href="#/">Overview</a><span class="sep">${ICONS.chevron}</span><span class="dim">${esc(s.group)}</span><span class="sep">${ICONS.chevron}</span><span class="here">${esc(s.id)}</span>`;
  } else {
    crumbs = `<span class="here">Overview</span>`;
  }
  $topbar.innerHTML = `
    <nav class="crumbs" aria-label="Breadcrumb">${crumbs}</nav>
    <div class="topbar-right">
      <div class="counts">
        <span>${statusIcon('busy', 12)}<span><b>${c.busy}</b> busy</span></span>
        <span>${statusIcon('idle', 12)}<span><b>${c.idle}</b> idle</span></span>
        <span>${statusIcon('stopped', 12)}<span><b>${c.stopped}</b> not running</span></span>
      </div>
      <button type="button" class="icon-btn" data-action="theme" aria-label="${dark ? 'Switch to light mode' : 'Switch to dark mode'}" title="${dark ? 'Light mode' : 'Dark mode'}">${dark ? ICONS.sun : ICONS.moon}</button>
    </div>`;
}

/* ---------- Render: overview ---------- */
function copyButtons(s, block) {
  const cls = block ? ' btn-block' : '';
  return {
    cmd: `<button type="button" class="btn btn-primary${cls}" data-copy="cmd" data-id="${esc(s.id)}">${ICONS.terminal}Copy resume command</button>`,
    id: `<button type="button" class="btn${cls}" data-copy="id" data-id="${esc(s.id)}">${ICONS.copy}Copy ID</button>`,
  };
}

function renderHome() {
  const busy = sessions.filter((s) => s.state === 'busy').sort(bySessionOrder);
  const stopped = sessions.filter((s) => s.state === 'stopped');
  const groups = grouped();

  const banners = stopped.map((s) => `
    <div class="panel banner">
      <div class="banner-text">${statusIcon('stopped')}<span><b style="font-weight:500">${esc(s.id)}</b><span class="dim"> is not running · off for ${live('long', s.stateSince)}</span></span></div>
      <div class="actions"><a class="btn" href="${sessionHref(s)}">View</a>${copyButtons(s).cmd}</div>
    </div>`).join('');

  const workCards = busy.length ? `<div class="work-grid">${busy.map((s) => {
    const l = latest(s);
    return `
      <a class="panel work-card" href="${sessionHref(s)}">
        <span class="work-top">${statusIcon('busy')}<span class="name">${esc(s.id)}</span><span class="timer mono">${stateTime(s, true)}</span></span>
        <span class="meta mono"><span>${esc(s.group)}</span><span>${esc(s.runsIn || '—')}</span><span>${upText(s)}</span><span>${esc(memText(s))}</span></span>
        ${l ? `<span class="last"><span class="last-head"><b>${esc(l.who)}</b> ${l.verb} · ${live('ago', l.at)}</span><span class="clamp2">${esc(l.text)}</span></span>` : ''}
      </a>`;
  }).join('')}</div>` : `<div class="panel empty">Nothing is running right now.</div>`;

  const groupRows = groups.map((g) => `
    <div class="group">
      <div class="group-head"><span><span class="name">${esc(g.name)}</span><span class="count mono">${g.items.length}</span></span><span class="summary">${g.summary}</span></div>
      ${g.items.map((s) => `
        <a class="row" href="${sessionHref(s)}">
          <span class="row-name">${statusIcon(s.state)}<span>${esc(s.id)}</span></span>
          <span class="row-meta mono">
            <span class="w-runs">${esc(s.runsIn || '—')}</span>
            <span class="w-up">${upText(s)}</span>
            <span class="w-mem">${esc(memText(s))}</span>
            <span class="w-state">${stateTime(s, true)}</span>
          </span>
        </a>`).join('')}
    </div>`).join('');

  const feed = feedEvents().map((e) => `
    <li><a href="${sessionHref(e.s)}">
      <span class="ago mono">${live('short', e.at)}</span>
      <span class="body">
        <span class="who">${e.dir === 'out' ? `<b>${esc(e.s.id)}</b><i> replied</i>` : `<b>${esc(e.from)}</b><i> to </i><b>${esc(e.s.id)}</b>`}</span>
        <span class="text">${esc(e.text)}</span>
      </span>
    </a></li>`).join('');

  $content.innerHTML = `
    <div class="page">
      <div>
        <h1 class="h1">Overview</h1>
        <p class="sub">${sessions.length} sessions in ${groups.length} groups · ${busy.length} working right now</p>
      </div>
      ${banners}
      <div class="cols">
        <div class="col-main">
          <section class="section" aria-labelledby="h-working">
            <h2 class="label" id="h-working"><span>Working now</span><span class="mono">${busy.length}</span></h2>
            ${workCards}
          </section>
          <section class="section" aria-labelledby="h-groups">
            <h2 class="label" id="h-groups"><span>Groups</span><span class="mono">${groups.length}</span></h2>
            <div class="panel groups">${groupRows}</div>
          </section>
        </div>
        <section class="col-side" aria-labelledby="h-activity">
          <h2 class="label" id="h-activity">Recent activity</h2>
          <ol class="panel feed">${feed}</ol>
        </section>
      </div>
    </div>`;
  document.title = 'Claude sessions';
}

/* ---------- Render: session ---------- */
function renderSession(id) {
  const s = sessions.find((x) => x.id === id);
  const chip = s.state === 'busy'
    ? `<span class="chip chip-busy"><span>Busy</span><span class="mono">${live('clock', s.stateSince)}</span></span>`
    : s.state === 'idle'
      ? `<span class="chip"><span>Idle for</span><span class="mono">${live('long', s.stateSince)}</span></span>`
      : `<span class="chip chip-stopped"><span>Not running for</span><span class="mono">${live('long', s.stateSince)}</span></span>`;

  const msgs = [];
  if (s.lastIn) msgs.push({ dir: 'in', at: s.lastIn.at, who: senderLabel(s.lastIn.from), verb: s.lastIn.from === 'you' ? 'asked' : 'sent a message', text: s.lastIn.text });
  if (s.lastOut) msgs.push({ dir: 'out', at: s.lastOut.at, who: s.id, verb: 'replied', text: s.lastOut.text });
  msgs.sort((a, b) => a.at - b.at);

  const thread = msgs.length ? msgs.map((m) => m.dir === 'in' ? `
      <article class="msg msg-in">
        <div class="msg-head"><span class="avatar" aria-hidden="true">${esc(m.who.charAt(0).toUpperCase())}</span><span><b>${esc(m.who)}</b> ${m.verb}</span><span class="mono">${live('ago', m.at)}</span></div>
        <p class="bubble">${esc(m.text)}</p>
      </article>` : `
      <article class="msg msg-out">
        <div class="msg-head"><span><b>${esc(m.who)}</b> ${m.verb}</span><span class="mono">${live('ago', m.at)}</span></div>
        <p class="bubble">${esc(m.text)}</p>
      </article>`).join('') : `<div class="panel empty">No messages yet.</div>`;

  const btns = copyButtons(s, true);
  $content.innerHTML = `
    <div class="page">
      <div class="title-row">${statusIcon(s.state, 18)}<h1 class="h1" style="font-size:24px;letter-spacing:-0.02em">${esc(s.id)}</h1>${chip}</div>
      <div class="cols">
        <section class="section" style="flex:999 1 420px;min-width:0" aria-labelledby="h-exchange">
          <h2 class="label" id="h-exchange">Latest exchange</h2>
          <div class="thread">${thread}</div>
        </section>
        <aside class="section" style="flex:1 1 280px;min-width:0" aria-labelledby="h-props">
          <h2 class="label" id="h-props">Properties</h2>
          <div class="panel props">
            <dl>
              <dt>Folder</dt><dd class="mono">${esc(s.folder)}</dd>
              <dt>Session</dt><dd class="mono" title="${esc(s.sessionId)}">${esc(shortId(s.sessionId))}</dd>
              <dt>Runs in</dt><dd>${esc(s.runsIn || '—')}</dd>
              <dt>Running for</dt><dd>${s.startedAt ? live('long', s.startedAt) : '—'}</dd>
              <dt>Memory</dt><dd>${esc(memText(s))}${s.children ? `<span class="extra">${esc(childText(s))}</span>` : ''}</dd>
              <dt>Working on</dt><dd>${esc(s.workingOn || '—')}</dd>
            </dl>
            <div class="props-actions">${btns.cmd}${btns.id}</div>
          </div>
        </aside>
      </div>
    </div>`;
  document.title = `${s.id} · Claude sessions`;
}

/* ---------- Render: all ---------- */
function render() {
  const r = route();
  renderSidebar(r);
  renderTopbar(r);
  if (r.view === 'session') renderSession(r.id); else renderHome();
}

/* ---------- Data refresh ---------- */
async function refresh() {
  try {
    const data = await loadSessions();
    lastUpdated = new Date();
    const sig = JSON.stringify(data);
    if (sig !== lastSignature) {   // only re-render when something changed
      lastSignature = sig;
      sessions = data;
      render();
    } else {
      const el = document.getElementById('updated');
      if (el) el.textContent = lastUpdated.toLocaleTimeString();
    }
  } catch (err) {
    console.error('Failed to load sessions', err);
  }
}

/* ---------- Events ---------- */
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e2) {}
    ta.remove();
  }
}

document.addEventListener('click', async (e) => {
  const copyBtn = e.target.closest('[data-copy]');
  if (copyBtn) {
    const s = sessions.find((x) => x.id === copyBtn.dataset.id);
    if (!s) return;
    await copyText(copyBtn.dataset.copy === 'cmd' ? CONFIG.resumeCommand(s) : s.sessionId);
    const original = copyBtn.innerHTML;
    copyBtn.innerHTML = `${ICONS.check}Copied`;
    setTimeout(() => { if (copyBtn.isConnected) copyBtn.innerHTML = original; }, 1400);
    return;
  }
  if (e.target.closest('[data-action="theme"]')) {
    const next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('sessions-theme', next); } catch (err) {}
    renderTopbar(route());
  }
});

window.addEventListener('hashchange', () => { render(); window.scrollTo(0, 0); });

refresh();
setInterval(refresh, CONFIG.pollMs);
setInterval(tickAll, 1000);
</script>
</body>
</html>"""


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
