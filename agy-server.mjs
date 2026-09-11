#!/usr/bin/env node
// agy-server.mjs — local OpenAI-compatible endpoint + tiny dashboard over agy-gemini.
// Zero deps, binds 127.0.0.1 only. Point any OpenAI client at http://127.0.0.1:8787/v1
// with one of the keys the dashboard shows (auto-generated on first run).
//
//   node agy-server.mjs                 # http://127.0.0.1:8787  (dashboard + /v1)
//   $env:AGY_PORT="1234"; node agy-server.mjs
//
// Security: /v1/* needs a local API key (Bearer). The dashboard + /api/* are open but
// localhost-bound and origin-guarded (cross-site browser requests are rejected), so a
// webpage can't read your keys or spend quota. Set AGY_CORS_ORIGIN=<origin> only if you
// deliberately want a browser app on another origin to call /v1.

import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import {
  loadRefreshToken,
  refreshAccessToken,
  bootstrapProject,
  listModels,
  streamGenerate,
  getQuota,
} from "./agy-gemini.mjs";

const PORT = Number(process.env.AGY_PORT || 8787);
const HOST = "127.0.0.1";
const KEYS_FILE = process.env.AGY_KEYS_FILE || path.join(os.homedir(), ".agy-gemini-keys.json");
const DEFAULT_MODEL = process.env.AGY_MODEL || "gemini-3.7-flash-tiered";
const ALLOW_ORIGIN = process.env.AGY_CORS_ORIGIN || "";

// ── local API keys (auth for /v1/*) ─────────────────────────────────────────
function loadKeys() {
  try {
    return JSON.parse(fs.readFileSync(KEYS_FILE, "utf8"));
  } catch {
    return [];
  }
}
function saveKeys(k) {
  fs.writeFileSync(KEYS_FILE, JSON.stringify(k, null, 2), { mode: 0o600 });
}
function newKey(label) {
  return {
    id: crypto.randomBytes(4).toString("hex"),
    key: "sk-agy-" + crypto.randomBytes(24).toString("hex"),
    label: label || "key",
    created: new Date().toISOString(),
  };
}
let keys = loadKeys();
if (keys.length === 0) {
  keys = [newKey("default")];
  saveKeys(keys);
  console.log("Generated API key:", keys[0].key);
}
function keyValid(req) {
  const tok = (req.headers.authorization || "").replace(/^Bearer\s+/i, "").trim();
  return !!tok && keys.some((k) => k.key === tok);
}

// ── access-token cache: refresh ~hourly, bootstrap the project once ─────────
let authCache = null;
async function getAuth() {
  if (authCache && Date.now() - authCache.at < 50 * 60 * 1000) return authCache;
  const rt = loadRefreshToken();
  if (!rt) throw new Error("no refresh token — run: node agy-gemini.mjs login");
  const accessToken = await refreshAccessToken(rt);
  const projectId = authCache?.projectId || (await bootstrapProject(accessToken));
  authCache = { accessToken, projectId, at: Date.now() };
  return authCache;
}

// ── security: reject cross-site browser requests (localhost-bound already) ──
function originOk(req) {
  const o = req.headers.origin;
  if (!o) return true; // non-browser client (curl, editor, CLI)
  if (ALLOW_ORIGIN && o === ALLOW_ORIGIN) return true;
  try {
    const h = new URL(o).host;
    return h === `${HOST}:${PORT}` || h === `localhost:${PORT}`;
  } catch {
    return false;
  }
}

// ── helpers ──────────────────────────────────────────────────────────────
function send(res, code, obj) {
  if (ALLOW_ORIGIN) res.setHeader("Access-Control-Allow-Origin", ALLOW_ORIGIN);
  res.writeHead(code, { "Content-Type": "application/json" });
  res.end(JSON.stringify(obj));
}
function readBody(req) {
  return new Promise((resolve) => {
    let b = "";
    req.on("data", (c) => (b += c));
    req.on("end", () => resolve(b));
  });
}
function pickModel(m) {
  // Generic OpenAI clients default to gpt-4/gpt-3.5 — remap those to a real id.
  if (!m || /^(gpt-3\.5|gpt-4|gpt-4o|chatgpt|o1|o3|text-|default|auto)$/i.test(m)) return DEFAULT_MODEL;
  return m;
}
// OpenAI messages → Gemini request { contents, systemInstruction? }
function messagesToRequest(messages) {
  const contents = [];
  let sysText = "";
  for (const m of messages || []) {
    const text =
      typeof m.content === "string"
        ? m.content
        : Array.isArray(m.content)
          ? m.content.map((p) => (typeof p === "string" ? p : p?.text || "")).join("")
          : "";
    if (m.role === "system") sysText += (sysText ? "\n" : "") + text;
    else contents.push({ role: m.role === "assistant" ? "model" : "user", parts: [{ text }] });
  }
  const request = { contents };
  if (sysText) request.systemInstruction = { parts: [{ text: sysText }] };
  return request;
}
function oaChunk(id, model, delta, finish) {
  return `data: ${JSON.stringify({
    id,
    object: "chat.completion.chunk",
    created: Math.floor(Date.now() / 1000),
    model,
    choices: [{ index: 0, delta, finish_reason: finish || null }],
  })}\n\n`;
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${HOST}:${PORT}`);
  const p = url.pathname;
  try {
    if (!originOk(req)) return send(res, 403, { error: { message: "cross-origin blocked" } });
    if (req.method === "OPTIONS") {
      if (ALLOW_ORIGIN) {
        res.setHeader("Access-Control-Allow-Origin", ALLOW_ORIGIN);
        res.setHeader("Access-Control-Allow-Headers", "authorization,content-type");
        res.setHeader("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS");
      }
      res.writeHead(204);
      return res.end();
    }

    // ── dashboard + its API (localhost-open, origin-guarded) ──
    if (p === "/" && req.method === "GET") {
      res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      return res.end(DASHBOARD_HTML);
    }
    if (p === "/api/quota" && req.method === "GET") {
      const { accessToken, projectId } = await getAuth();
      return send(res, 200, { projectId, quota: await getQuota(accessToken, projectId) });
    }
    if (p === "/api/keys" && req.method === "GET") return send(res, 200, keys);
    if (p === "/api/keys" && req.method === "POST") {
      const body = JSON.parse((await readBody(req)) || "{}");
      const k = newKey(body.label);
      keys.push(k);
      saveKeys(keys);
      return send(res, 200, k);
    }
    if (p === "/api/keys" && req.method === "DELETE") {
      keys = keys.filter((k) => k.id !== url.searchParams.get("id"));
      saveKeys(keys);
      return send(res, 200, { ok: true });
    }

    // ── OpenAI-compatible (Bearer key required) ──
    if (p.startsWith("/v1/") && !keyValid(req)) {
      return send(res, 401, { error: { message: "missing or invalid API key" } });
    }
    if (p === "/v1/models" && req.method === "GET") {
      const { accessToken, projectId } = await getAuth();
      const { models } = await listModels(accessToken, projectId);
      return send(res, 200, {
        object: "list",
        data: models.map((m) => ({ id: m, object: "model", owned_by: "antigravity" })),
      });
    }
    if (p === "/v1/chat/completions" && req.method === "POST") {
      const body = JSON.parse((await readBody(req)) || "{}");
      const model = pickModel(body.model);
      const request = messagesToRequest(body.messages);
      const { accessToken, projectId } = await getAuth();
      const id = "chatcmpl-" + crypto.randomBytes(8).toString("hex");

      if (body.stream) {
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
          ...(ALLOW_ORIGIN ? { "Access-Control-Allow-Origin": ALLOW_ORIGIN } : {}),
        });
        res.write(oaChunk(id, model, { role: "assistant" }));
        await streamGenerate(accessToken, projectId, model, request, (t) =>
          res.write(oaChunk(id, model, { content: t }))
        );
        res.write(oaChunk(id, model, {}, "stop"));
        res.write("data: [DONE]\n\n");
        return res.end();
      }
      let text = "";
      await streamGenerate(accessToken, projectId, model, request, (t) => (text += t));
      return send(res, 200, {
        id,
        object: "chat.completion",
        created: Math.floor(Date.now() / 1000),
        model,
        choices: [{ index: 0, message: { role: "assistant", content: text }, finish_reason: "stop" }],
        usage: {},
      });
    }

    send(res, 404, { error: { message: "not found" } });
  } catch (e) {
    // best-effort — headers may already be sent on a mid-stream failure
    try {
      send(res, 500, { error: { message: String(e.message || e) } });
    } catch {
      res.end();
    }
  }
});

server.listen(PORT, HOST, () => {
  console.log(`agy-server → http://${HOST}:${PORT}  (dashboard)`);
  console.log(`OpenAI API  → http://${HOST}:${PORT}/v1   keys: ${KEYS_FILE}`);
  getAuth().catch((e) => console.log("[warn] auth not ready:", e.message)); // warm + surface token errors early
});

// ── dashboard (single inline page; client JS uses string concat, no template literals) ──
const DASHBOARD_HTML = `<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>agy-gemini</title><style>
:root{--bg:#0d0f12;--card:#151a21;--fg:#e6e9ef;--mut:#8a93a2;--line:#232a34;--accent:#f59e0b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:24px}h1{font-size:18px;margin:0 0 2px}
.sub{color:var(--mut);font-size:12px;margin-bottom:20px}.sub b{color:var(--fg);font-family:ui-monospace,monospace}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
.card h2{font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--mut);margin:0 0 12px}
.row{display:flex;align-items:center;gap:10px;margin:7px 0}
.name{flex:0 0 190px;font-family:ui-monospace,monospace;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.bar{flex:1;height:8px;background:#0b0e12;border-radius:5px;overflow:hidden}.bar>i{display:block;height:100%}
.pct{flex:0 0 40px;text-align:right;font-variant-numeric:tabular-nums;color:var(--mut)}
.reset{flex:0 0 auto;color:var(--mut);font-size:11px}
.key{display:flex;align-items:center;gap:8px;margin:6px 0;font-family:ui-monospace,monospace;font-size:12px}
.key .k{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--mut)}
button{background:#222a34;color:var(--fg);border:1px solid var(--line);border-radius:6px;padding:5px 10px;font-size:12px;cursor:pointer}
button:hover{border-color:var(--accent)}input{background:#0b0e12;border:1px solid var(--line);color:var(--fg);border-radius:6px;padding:5px 8px;font-size:12px;flex:1}
.mut{color:var(--mut)}.err{color:#f87171}</style></head><body><div class=wrap>
<h1>agy-gemini</h1><div class=sub id=meta>loading…</div>
<div class=card><h2>Quota</h2><div id=quota class=mut>loading…</div></div>
<div class=card><h2>API keys</h2><div id=keys class=mut>loading…</div>
<div class=row style="margin-top:12px"><input id=label placeholder="label (optional)"><button onclick=addKey()>+ new key</button></div></div>
<div class=sub>Point OpenAI clients at <b id=base></b> using a key above.</div>
<script>
var esc=function(s){return String(s).replace(/[&<>"]/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]})};
var col=function(p){return p>50?'#34d399':p>=20?'#f59e0b':'#f87171'};
var reset=function(r){if(!r)return '';var d=new Date(r);return isNaN(d.getTime())?'':'resets '+d.toLocaleString()};
document.getElementById('base').textContent=location.origin+'/v1';
function q(){fetch('/api/quota').then(function(r){return r.json()}).then(function(d){
 document.getElementById('meta').textContent='local OpenAI endpoint · project '+((d&&d.projectId)||'?');
 var a=(d&&d.quota)||[];document.getElementById('quota').innerHTML=a.length?a.map(function(m){
  return '<div class=row><span class=name title="'+esc(m.model)+'">'+esc(m.model)+'</span>'+
   '<span class=bar><i style="width:'+m.remainingPct+'%;background:'+col(m.remainingPct)+'"></i></span>'+
   '<span class=pct>'+m.remainingPct+'%</span><span class=reset>'+esc(reset(m.resetAt))+'</span></div>'}).join(''):'no quota data';
}).catch(function(e){document.getElementById('quota').innerHTML='<span class=err>'+esc(e.message||e)+'</span>';
 document.getElementById('meta').textContent='error'})}
function k(){fetch('/api/keys').then(function(r){return r.json()}).then(function(ks){
 document.getElementById('keys').innerHTML=(ks&&ks.length)?ks.map(function(x){
  return '<div class=key><b>'+esc(x.label)+'</b><span class=k>'+esc(x.key)+'</span>'+
   '<button onclick="cp(\\''+esc(x.key)+'\\',this)">copy</button>'+
   '<button onclick="rm(\\''+esc(x.id)+'\\')">revoke</button></div>'}).join(''):'<span class=mut>none — add one below</span>';
}).catch(function(e){document.getElementById('keys').innerHTML='<span class=err>'+esc(e.message||e)+'</span>'})}
function addKey(){var l=document.getElementById('label').value;fetch('/api/keys',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({label:l})}).then(function(){document.getElementById('label').value='';k()})}
function rm(id){fetch('/api/keys?id='+encodeURIComponent(id),{method:'DELETE'}).then(k)}
function cp(v,b){navigator.clipboard.writeText(v);var t=b.textContent;b.textContent='copied';setTimeout(function(){b.textContent=t},900)}
q();k();setInterval(q,60000);
</script></div></body></html>`;
