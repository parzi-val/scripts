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
    return {"now": time.time() * 1000, "running": running, "recent": recent(live_ids)}


_recent_cache = {"at": 0, "data": []}


def recent(live_ids):
    """Named sessions with transcripts touched in the last week that aren't running now."""
    if time.time() - _recent_cache["at"] < 30:  # scanning ~100 files; 30s is fresh enough
        return [r for r in _recent_cache["data"] if r["sessionId"] not in live_ids]
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
    _recent_cache.update(at=time.time(), data=out)
    return [r for r in out if r["sessionId"] not in live_ids]


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
<style>
  :root { color-scheme: dark; --bg:#14161a; --card:#1d2026; --line:#2c3038; --dim:#8a919c; --text:#e6e8eb;
          --busy:#f5a524; --idle:#3fb950; --shell:#58a6ff; }
  * { box-sizing: border-box }
  body { margin:0; padding:24px; background:var(--bg); color:var(--text); font:14px/1.45 system-ui, sans-serif }
  h1 { font-size:18px; margin:0 0 4px } .sub { color:var(--dim); margin-bottom:20px }
  .grid { display:grid; grid-template-columns:repeat(auto-fill, minmax(420px, 1fr)); gap:14px }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px }
  .card.busy { border-color:var(--busy); box-shadow:0 0 0 1px var(--busy) inset }
  .top { display:flex; align-items:center; gap:10px; flex-wrap:wrap }
  .name { font-weight:600; font-size:16px }
  .badge { border-radius:999px; padding:2px 10px; font-size:12px; font-weight:600; color:#111 }
  .badge.busy { background:var(--busy) } .badge.idle { background:var(--idle) }
  .badge.shell { background:var(--shell) } .badge.other { background:#888 }
  .since { color:var(--dim); font-size:12px }
  .meta { color:var(--dim); font-size:12px; margin:8px 0; display:flex; flex-wrap:wrap; gap:4px 14px }
  .meta code { color:var(--text) }
  .brief div { margin:4px 0; overflow-wrap:anywhere } .lbl { color:var(--dim); font-size:12px; margin-right:6px }
  .plan { color:#d2a8ff }
  details summary { cursor:pointer; list-style:none } details summary::-webkit-details-marker { display:none }
  .clip { display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden }
  details[open] .clip { display:block }
  button { background:#2b3038; color:var(--text); border:1px solid var(--line); border-radius:6px;
           padding:3px 9px; font-size:12px; cursor:pointer } button:hover { border-color:var(--dim) }
  .actions { display:flex; gap:6px; margin-top:10px; flex-wrap:wrap }
  .section { margin-top:28px } .section > summary { font-size:15px; font-weight:600; cursor:pointer; margin-bottom:12px }
  .row { display:grid; grid-template-columns: 220px 1fr auto; gap:12px; align-items:center;
         padding:8px 0; border-bottom:1px solid var(--line) }
  .row .req { color:var(--dim); overflow:hidden; white-space:nowrap; text-overflow:ellipsis }
  .err { color:#f85149 }
</style></head><body>
<h1>Claude sessions</h1>
<div class="sub" id="sub">loading…</div>
<div class="grid" id="running"></div>
<details class="section" id="recentBox"><summary id="recentTitle">Recent, not running</summary>
  <div id="recent"></div></details>
<script>
const $ = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls;
  if (text != null) e.textContent = text; return e; };
const ago = ms => { if (!ms) return "?"; const s = Math.max(0, (Date.now() - ms) / 1000);
  if (s < 60) return Math.floor(s) + " s"; if (s < 3600) return Math.floor(s / 60) + " min";
  if (s < 86400) return (s / 3600).toFixed(1).replace(".0", "") + " h"; return Math.floor(s / 86400) + " d"; };
const ts = v => typeof v === "string" ? Date.parse(v) : v;
function copyBtn(label, value) {
  const b = $("button", null, label);
  b.onclick = async () => { await navigator.clipboard.writeText(value); b.textContent = "copied";
    setTimeout(() => b.textContent = label, 1200); };
  return b;
}
const openKeys = new Set();  // expanded briefs stay expanded across refreshes
function briefLine(key, label, text, cls) {
  const d = $("details"); const s = $("summary");
  d.open = openKeys.has(key);
  d.ontoggle = () => d.open ? openKeys.add(key) : openKeys.delete(key); const line = $("div", "clip " + (cls || ""));
  line.append($("span", "lbl", label), document.createTextNode(text)); s.append(line); d.append(s); return d;
}
function card(s) {
  const st = ["busy", "idle", "shell"].includes(s.status) ? s.status : "other";
  const c = $("div", "card" + (st === "busy" ? " busy" : ""));
  const top = $("div", "top");
  top.append($("span", "name", s.name), $("span", "badge " + st, s.status),
             $("span", "since", s.status + " for " + ago(s.statusSince)));
  c.append(top);
  const meta = $("div", "meta");
  const add = (k, v) => { const sp = $("span"); sp.append(k + " "); sp.append($("code", null, v)); meta.append(sp); };
  add("id", s.sessionId.slice(0, 8)); add("in", s.cwd || "?");
  add("via", (s.host || (s.entrypoint === "claude-desktop" ? "Desktop app" : "CLI")));
  add("up", ago(s.startedAt));
  if (s.memMB != null) add("mem", s.memMB + " MB" + (s.childMemMB ? " + " + s.childMemMB + " MB children" : ""));
  if (s.children && s.children.length) add("runs", s.children.join(", "));
  if (s.otherNames && s.otherNames.length) add("was", s.otherNames.join(", "));
  c.append(meta);
  const brief = $("div", "brief");
  const k = s.sessionId;
  for (const p of s.plans || []) brief.append(briefLine(k + p.file, "plan", p.title + " (" + p.status + ")", "plan"));
  if (s.request) brief.append(briefLine(k + "req", s.request.from ? "message from " + s.request.from : "asked",
                                         s.request.text + "  · " + ago(ts(s.request.ts)) + " ago"));
  if (s.reply) brief.append(briefLine(k + "reply", "replied", s.reply.text));
  if (!s.request && !s.reply) brief.append($("div", "since", "no transcript yet"));
  c.append(brief);
  const act = $("div", "actions");
  act.append(copyBtn("copy ID", s.sessionId), copyBtn("copy resume command", s.resume));
  if (s.entrypoint === "claude-desktop") act.append($("span", "since", "Desktop session: lives in the app"));
  c.append(act);
  return c;
}
function recentRow(r) {
  const row = $("div", "row");
  const left = $("div"); left.append($("div", "name", r.name),
    $("div", "since", ago(r.lastActivity) + " ago · " + (r.cwd || "?")));
  row.append(left, $("div", "req", r.request ? (r.request.from ? "message from " + r.request.from + ": " : "")
    + r.request.text : ""));
  const a = $("div", "actions"); a.style.marginTop = 0;
  a.append(copyBtn("copy ID", r.sessionId), copyBtn("copy resume command", r.resume)); row.append(a);
  return row;
}
async function refresh() {
  try {
    const d = await (await fetch("/api/sessions", { cache: "no-store" })).json();
    const busy = d.running.filter(s => s.status === "busy").length;
    document.getElementById("sub").textContent =
      d.running.length + " running · " + busy + " busy · updated " + new Date().toLocaleTimeString();
    document.getElementById("running").replaceChildren(...d.running.map(card));
    document.getElementById("recentTitle").textContent = "Recent, not running (" + d.recent.length + ", last 7 days)";
    document.getElementById("recent").replaceChildren(...d.recent.map(recentRow));
  } catch (e) {
    document.getElementById("sub").replaceChildren($("span", "err", "dashboard server not reachable: " + e));
  }
}
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
    # The first scan reads ~100 transcript tails (a few seconds); do it before the page asks.
    threading.Thread(target=snapshot, daemon=True).start()
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
