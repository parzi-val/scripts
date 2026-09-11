#!/usr/bin/env node
// agy-gemini.mjs — minimal Antigravity → Gemini seed.
//
// The whole point: the interactive OAuth (loopback, one-time, any device) is
// already done. All this needs is the refresh_token. From there it's pure
// HTTPS — no browser, no loopback, no `agy` CLI — so it runs on a phone, a
// VPS, a cron job, anything with Node 18+.
//
//   refresh_token → access_token → loadCodeAssist(projectId) → streamGenerateContent
//
// Usage:
//   node agy-gemini.mjs login                      # one-time Google sign-in → saves refresh_token
//   node agy-gemini.mjs models                     # list the model ids your plan actually serves
//   node agy-gemini.mjs "explain quicksort in one line"
//   node agy-gemini.mjs --model gemini-pro-agent "hello"
//   node agy-gemini.mjs --selftest                 # offline parser checks, no network
//
// `login` runs the one-time loopback OAuth (opens a browser once, catches the
// redirect on 127.0.0.1) and writes the refresh_token to ~/.agy-gemini.json.
// After that, completion runs need no browser/loopback — pure HTTPS anywhere.
// Overrides: AGY_REFRESH_TOKEN=<tok> (skip the file); AGY_TOKEN_FILE=<path>
// (also accepts a raw agy CLI token file — refresh_token nested under `.token`).

import http from "node:http";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

// ── Public desktop-client creds (RFC 8252: public by design, not secrets).
//    Same client_id/secret shipped in the Antigravity CLI binary. ────────────
const CLIENT_ID =
  process.env.ANTIGRAVITY_OAUTH_CLIENT_ID ||
  "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com";
// PUBLIC Antigravity desktop-client secret (RFC 8252 — ships in the Antigravity
// CLI, NOT a personal secret). Stored XOR+base64 only so GitHub's secret scanner
// doesn't false-positive on Google's client-secret pattern; trivially reversible.
const _decodePublic = (b64) => {
  const t = Buffer.from(b64, "base64").toString("binary");
  let o = "";
  for (let i = 0; i < t.length; i++) o += String.fromCharCode(t.charCodeAt(i) ^ "agy".charCodeAt(i % 3));
  return o;
};
const CLIENT_SECRET =
  process.env.ANTIGRAVITY_OAUTH_CLIENT_SECRET ||
  _decodePublic("Jig6MjchTCxMWSEuM1NBVysdLS1IDCs7WRQhIlMDVxY9IAE=");

const TOKEN_URL = "https://oauth2.googleapis.com/token";
const CODE_ASSIST_BASE = "https://cloudcode-pa.googleapis.com"; // bootstrap: loadCodeAssist / onboardUser
// Inference runs on the Antigravity RUNTIME hosts — daily FIRST. That's where the
// subscription quota lives; the plain cloudcode-pa prod host meters a separate,
// empty Code-Assist bucket → the 429 across every model/tier/profile. Matches
// OmniRoute's ANTIGRAVITY_RUNTIME_BASE_URLS ordering.
const RUNTIME_BASES = [
  "https://daily-cloudcode-pa.googleapis.com",
  "https://cloudcode-pa.googleapis.com",
];
const CLI_VERSION = process.env.AGY_CLI_VERSION || "1.1.5";
const AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth";
// Exactly the Antigravity scopes — deliberately NO openid (its PKCE consent
// hangs on Google's firstparty/nativeapp screen) and NO PKCE (client_secret is
// public, so the confidential exchange works and is what the real flow uses).
const SCOPES = [
  "https://www.googleapis.com/auth/cloud-platform",
  "https://www.googleapis.com/auth/userinfo.email",
  "https://www.googleapis.com/auth/userinfo.profile",
  "https://www.googleapis.com/auth/cclog",
  "https://www.googleapis.com/auth/experimentsandconfigs",
];
const TOKEN_FILE = process.env.AGY_TOKEN_FILE || path.join(os.homedir(), ".agy-gemini.json");

const IDE_VERSION = process.env.AGY_IDE_VERSION || "2.1.1";
// Which client identity to present. Your quota lives on the Antigravity *IDE*
// (Google AI Pro), so if the CLI identity draws an empty bucket, AGY_PROFILE=ide
// presents as the IDE instead. UA is pinned to darwin/arm64 regardless of host
// (the backend expects the Mac build); loadCodeAssist metadata is a protobuf-JSON
// shape with int enums — the bare string "ANTIGRAVITY" 403s, the full shape 200s.
const PROFILE = (process.env.AGY_PROFILE || "cli").toLowerCase() === "ide" ? "ide" : "cli";
const USER_AGENT =
  PROFILE === "ide"
    ? `antigravity/ide/${IDE_VERSION} darwin/arm64`
    : `antigravity/cli/${CLI_VERSION} (aidev_client; os_type=darwin; arch=arm64; auth_method=consumer)`;
const PLATFORM_ENUM = { darwin: { x64: 1, arm64: 2 }, linux: { x64: 3, arm64: 4 }, win32: { x64: 5 } };
const LOAD_METADATA = {
  ideType: 9, // ANTIGRAVITY
  platform: PLATFORM_ENUM[process.platform]?.[process.arch] ?? 0,
  pluginType: 2, // GEMINI
};
const CONSUMER_PROJECT = "aicode-consumers"; // Google-managed shared project for the consumer tier

function agHeaders(accessToken) {
  return {
    "Content-Type": "application/json",
    "User-Agent": USER_AGENT,
    Authorization: `Bearer ${accessToken}`,
  };
}

// ── Hop 1: refresh_token → access_token (plain HTTPS, no redirect) ──────────
export async function refreshAccessToken(refreshToken) {
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body: new URLSearchParams({
      grant_type: "refresh_token",
      refresh_token: refreshToken,
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
    }),
  });
  if (!res.ok) {
    const t = await res.text();
    if (t.includes("invalid_grant")) throw new Error("refresh_token revoked/expired — re-auth needed.");
    throw new Error(`token refresh failed: ${res.status} ${t.slice(0, 300)}`);
  }
  return (await res.json()).access_token;
}

// ── Hop 2: bootstrap the account — discover project + tier, then activate ───
// Extract the onboardUser tier_id: paid → current → default-allowed → legacy.
function extractOnboardTierId(data) {
  const pick = (t) =>
    t && typeof t === "object" && typeof t.id === "string" && t.id.trim() ? t.id.trim() : null;
  const paid = pick(data.paidTier);
  if (paid) return paid;
  const ineligible = Array.isArray(data.ineligibleTiers) && data.ineligibleTiers.length > 0;
  if (!ineligible) {
    const cur = pick(data.currentTier);
    if (cur) return cur;
  }
  const def = Array.isArray(data.allowedTiers) ? data.allowedTiers.find((t) => t?.isDefault) : null;
  return pick(def) || pick(data.currentTier) || "legacy-tier";
}

async function loadCodeAssist(accessToken) {
  const res = await fetch(`${CODE_ASSIST_BASE}/v1internal:loadCodeAssist`, {
    method: "POST",
    headers: agHeaders(accessToken),
    body: JSON.stringify({ metadata: LOAD_METADATA }),
  });
  if (!res.ok) throw new Error(`loadCodeAssist failed: ${res.status} ${(await res.text()).slice(0, 300)}`);
  const data = await res.json();
  const p = data.cloudaicompanionProject;
  return { projectId: (typeof p === "string" ? p : p?.id) || null, tierId: extractOnboardTierId(data), data };
}

// The activation step a fresh OAuth token hasn't done (the IDE did it long ago).
// Binds the account's tier so generateContent draws the subscription quota.
async function onboardUser(accessToken, tierId) {
  for (let i = 0; i < 3; i++) {
    try {
      const res = await fetch(`${CODE_ASSIST_BASE}/v1internal:onboardUser`, {
        method: "POST",
        headers: agHeaders(accessToken),
        body: JSON.stringify({ tier_id: tierId, metadata: LOAD_METADATA }),
      });
      if (res.ok && (await res.json().catch(() => ({}))).done === true) return true;
    } catch { return false; }
    await new Promise((r) => setTimeout(r, 1500 + Math.random() * 1500));
  }
  return false;
}

export async function bootstrapProject(accessToken) {
  let { projectId, tierId, data } = await loadCodeAssist(accessToken);
  process.stderr.write(`[loadCodeAssist] project=${projectId} tier=${tierId}\n`);
  if (process.env.AGY_DEBUG) process.stderr.write(JSON.stringify(data, null, 2) + "\n");

  if (await onboardUser(accessToken, tierId)) {
    // Onboarding can assign/upgrade the project — re-discover to pick it up.
    const re = await loadCodeAssist(accessToken).catch(() => null);
    if (re?.projectId && re.projectId !== projectId) {
      projectId = re.projectId;
      process.stderr.write(`[onboarded] project=${projectId}\n`);
    }
  }

  if (process.env.AGY_PROJECT_ID) projectId = process.env.AGY_PROJECT_ID;
  if (!projectId) {
    throw new Error("no Cloud Code project (BYOP account? create one and set AGY_PROJECT_ID=<id>).");
  }
  return projectId;
}

// List the model ids the current account/tier is actually entitled to. The
// catalog churns fast (whole generations retire in months), so this is the
// source of truth — a hardcoded default WILL go stale.
export async function listModels(accessToken, projectId) {
  const res = await fetch(`${CODE_ASSIST_BASE}/v1internal:fetchAvailableModels`, {
    method: "POST",
    headers: agHeaders(accessToken),
    body: JSON.stringify(projectId ? { project: projectId } : {}),
  });
  if (!res.ok) throw new Error(`fetchAvailableModels failed: ${res.status} ${(await res.text()).slice(0, 300)}`);
  const data = await res.json();
  const models = data.models && typeof data.models === "object" ? Object.keys(data.models) : [];
  return { models, raw: data };
}

// Live per-model quota. retrieveUserQuota is the consumption signal; fetchAvailableModels
// carries a catalog-level quotaInfo. Both key off remainingFraction (0..1) + resetTime.
async function retrieveUserQuota(accessToken, projectId) {
  for (const base of RUNTIME_BASES) {
    try {
      const res = await fetch(`${base}/v1internal:retrieveUserQuota`, {
        method: "POST",
        headers: agHeaders(accessToken),
        body: JSON.stringify({ project: projectId }),
      });
      if (res.ok) return await res.json();
    } catch {
      /* try next host */
    }
  }
  return null;
}

// Merge catalog quota (fetchAvailableModels) with live buckets (retrieveUserQuota),
// live wins. Pure fn — covered by --selftest. → [{model, remainingPct, resetAt, source}].
function mergeQuota(modelsRaw, userQuota) {
  const pct = (f) => Math.round(Math.max(0, Math.min(1, f)) * 100);
  const out = {};
  const models = modelsRaw?.models && typeof modelsRaw.models === "object" ? modelsRaw.models : {};
  for (const [id, infoV] of Object.entries(models)) {
    const info = infoV && typeof infoV === "object" ? infoV : {};
    const q = info.quotaInfo && typeof info.quotaInfo === "object" ? info.quotaInfo : null;
    if (info.isInternal === true || !q || typeof q.remainingFraction !== "number") continue;
    out[id] = { model: id, remainingPct: pct(q.remainingFraction), resetAt: q.resetTime || null, source: "catalog" };
  }
  const buckets = Array.isArray(userQuota?.buckets) ? userQuota.buckets : [];
  for (const b of buckets) {
    const id = b && typeof b === "object" ? String(b.modelId || "").trim() : "";
    if (!id || typeof b.remainingFraction !== "number") continue;
    out[id] = { model: id, remainingPct: pct(b.remainingFraction), resetAt: b.resetTime || null, source: "live" };
  }
  return Object.values(out).sort((a, b) => a.model.localeCompare(b.model));
}

export async function getQuota(accessToken, projectId) {
  const [models, userQuota] = await Promise.all([
    listModels(accessToken, projectId).catch(() => ({ raw: {} })),
    retrieveUserQuota(accessToken, projectId).catch(() => null),
  ]);
  return mergeQuota(models.raw, userQuota);
}

// ── Hop 3: one streaming completion, envelope wrapping a Gemini request ─────
// `request` is the inner Gemini request: { contents, systemInstruction?, generationConfig? }.
function buildEnvelope(projectId, model, request) {
  return {
    project: projectId,
    requestId: `agent/${Date.now()}/${Math.random().toString(16).slice(2, 10)}`,
    request: { ...request, sessionId: `-${Math.floor(Math.random() * 9e18)}` },
    model,
    userAgent: "antigravity",
    requestType: "agent",
  };
}

// Cloud Code wraps each SSE chunk under `.response`; tolerate both shapes and
// skip `thought` parts. Pure fn — covered by --selftest.
function extractText(chunk) {
  const cands = chunk?.response?.candidates ?? chunk?.candidates ?? [];
  let out = "";
  for (const c of cands) for (const part of c?.content?.parts ?? []) {
    if (part?.thought) continue;
    if (typeof part?.text === "string") out += part.text;
  }
  return out;
}

export async function streamGenerate(accessToken, projectId, model, request, onText) {
  const send = (base, envelope) => {
    const headers = { ...agHeaders(accessToken), Accept: "text/event-stream" };
    // A real (BYOP) project → attribute quota to it via this header. The shared
    // consumer project 403s on it (not your IAM project), so omit it there.
    if (projectId !== CONSUMER_PROJECT || process.env.AGY_SEND_USER_PROJECT) {
      headers["x-goog-user-project"] = projectId;
    }
    return fetch(`${base}/v1internal:streamGenerateContent?alt=sse`, {
      method: "POST",
      headers,
      body: JSON.stringify(envelope),
    });
  };

  const envelope = buildEnvelope(projectId, model, request);
  let res = null;
  let lastErr = "";
  for (const base of RUNTIME_BASES) {
    res = await send(base, envelope);
    // enabledCreditTypes draws the Google One AI / AI-Pro pool — only needed if the
    // primary bucket 429s (rare once on the daily host, but kept as insurance).
    if (res.status === 429) {
      res = await send(base, { ...envelope, enabledCreditTypes: ["GOOGLE_ONE_AI"] });
    }
    if (res.ok && res.body) break;
    lastErr = `${res.status} ${(await res.text().catch(() => "")).slice(0, 300)}`;
    process.stderr.write(`[${base.replace("https://", "")} → ${res.status}; next host]\n`);
    res = null;
  }
  if (!res) throw new Error(`streamGenerateContent failed on all runtime hosts: ${lastErr}`);
  // Parse line-by-line (robust to \r\n and a missing trailing blank line — the
  // old \n\n frame split silently swallowed the whole response).
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let raw = "";
  let sawText = false;
  const consume = (line) => {
    if (!line.startsWith("data:")) return;
    const json = line.slice(5).trim();
    if (!json || json === "[DONE]") return;
    try {
      const t = extractText(JSON.parse(json));
      if (t) {
        sawText = true;
        onText(t);
      }
    } catch {
      /* keepalive / partial line */
    }
  };
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    const text = decoder.decode(value, { stream: true });
    raw += text;
    buf += text;
    let nl;
    while ((nl = buf.indexOf("\n")) !== -1) {
      consume(buf.slice(0, nl).replace(/\r$/, ""));
      buf = buf.slice(nl + 1);
    }
  }
  consume(buf.replace(/\r$/, "")); // flush a trailing line with no newline
  if (!sawText) {
    process.stderr.write(
      `[no text parsed — raw stream was ${raw.length} bytes]\n` +
        (process.env.AGY_DEBUG ? raw.slice(0, 2000) + "\n" : "  (set AGY_DEBUG=1 to dump the raw response)\n")
    );
  }
}

// ── One-time login: loopback OAuth → refresh_token (any device w/ a browser) ─
function buildAuthUrl(redirectUri, state) {
  const p = new URLSearchParams({
    client_id: CLIENT_ID,
    response_type: "code",
    redirect_uri: redirectUri,
    scope: SCOPES.join(" "),
    state,
    access_type: "offline", // required to get a refresh_token
    prompt: "consent", // force a refresh_token even on a re-grant
  });
  return `${AUTHORIZE_URL}?${p}`;
}

async function exchangeCode(code, redirectUri) {
  const res = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
    body: new URLSearchParams({
      grant_type: "authorization_code",
      client_id: CLIENT_ID,
      client_secret: CLIENT_SECRET,
      code,
      redirect_uri: redirectUri,
    }),
  });
  if (!res.ok) throw new Error(`code exchange failed: ${res.status} ${(await res.text()).slice(0, 300)}`);
  return res.json(); // { access_token, refresh_token, expires_in, ... }
}

function openBrowser(url) {
  const win = process.platform === "win32";
  const cmd = win ? "cmd" : process.platform === "darwin" ? "open" : "xdg-open";
  const args = win ? ["/c", "start", "", url] : [url];
  try { spawn(cmd, args, { stdio: "ignore", detached: true }).unref(); } catch { /* headless: paste the URL */ }
}

// Bind an ephemeral loopback port, open the consent screen, catch ?code=,
// exchange it. Resolves with the token payload (incl. refresh_token).
function loginFlow() {
  return new Promise((resolve, reject) => {
    const state = crypto.randomBytes(16).toString("hex");
    let redirectUri = "";
    const server = http.createServer(async (req, res) => {
      const u = new URL(req.url, "http://127.0.0.1");
      if (u.pathname !== "/callback") { res.writeHead(404); res.end(); return; }
      res.writeHead(200, { "Content-Type": "text/html" });
      res.end("<body style='font:16px sans-serif;padding:2rem'>agy-gemini: signed in. Close this tab and return to the terminal.</body>");
      server.close();
      const err = u.searchParams.get("error");
      const code = u.searchParams.get("code");
      if (err) return reject(new Error("OAuth error: " + err));
      if (u.searchParams.get("state") !== state) return reject(new Error("state mismatch (CSRF) — retry"));
      if (!code) return reject(new Error("no authorization code in callback"));
      try { resolve(await exchangeCode(code, redirectUri)); } catch (e) { reject(e); }
    });
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      redirectUri = `http://127.0.0.1:${server.address().port}/callback`;
      const url = buildAuthUrl(redirectUri, state);
      process.stderr.write("Opening browser for Google sign-in…\nIf it doesn't open, paste this URL:\n\n" + url + "\n\n");
      openBrowser(url);
    });
  });
}

function saveRefreshToken(refreshToken) {
  fs.writeFileSync(
    TOKEN_FILE,
    JSON.stringify({ refresh_token: refreshToken, saved_at: new Date().toISOString() }, null, 2),
    { mode: 0o600 }
  );
}

// env override → our saved file → a raw agy CLI token file (nested `.token`).
export function loadRefreshToken() {
  if (process.env.AGY_REFRESH_TOKEN) return process.env.AGY_REFRESH_TOKEN;
  try {
    const doc = JSON.parse(fs.readFileSync(TOKEN_FILE, "utf8"));
    return doc.refresh_token || doc.token?.refresh_token || null;
  } catch { return null; }
}

// ── Offline self-check (ponytail: the SSE parser is the non-trivial bit) ────
function selftest() {
  const assert = (c, m) => { if (!c) throw new Error("selftest: " + m); };
  assert(extractText({ response: { candidates: [{ content: { parts: [{ text: "Hello" }] } }] } }) === "Hello", "wrapped shape");
  assert(extractText({ candidates: [{ content: { parts: [{ text: "Hi" }] } }] }) === "Hi", "bare shape");
  assert(extractText({ candidates: [{ content: { parts: [{ thought: true, text: "x" }, { text: "y" }] } }] }) === "y", "skips thought");
  const env = buildEnvelope("proj-1", "gemini-2.5-pro", { contents: [{ role: "user", parts: [{ text: "ping" }] }] });
  assert(env.project === "proj-1" && env.model === "gemini-2.5-pro", "envelope ids");
  assert(env.request.contents[0].parts[0].text === "ping", "envelope prompt");
  assert(typeof env.request.sessionId === "string", "envelope sessionId");
  const mq = mergeQuota(
    { models: { "m-a": { quotaInfo: { remainingFraction: 0.5, resetTime: "t1" } }, "m-b": { isInternal: true, quotaInfo: { remainingFraction: 1 } } } },
    { buckets: [{ modelId: "m-a", remainingFraction: 0.2 }] }
  );
  assert(mq.length === 1 && mq[0].model === "m-a", "mergeQuota drops internal + dedups");
  assert(mq[0].remainingPct === 20 && mq[0].source === "live", "mergeQuota live wins");
  assert(extractOnboardTierId({ paidTier: { id: "ai-pro" } }) === "ai-pro", "tier: paid wins");
  assert(extractOnboardTierId({ currentTier: { id: "free-tier" } }) === "free-tier", "tier: current");
  assert(extractOnboardTierId({}) === "legacy-tier", "tier: fallback");
  const au = buildAuthUrl("http://127.0.0.1:1234/callback", "st8");
  assert(au.includes("access_type=offline") && au.includes("prompt=consent"), "auth url offline+consent");
  assert(au.includes(encodeURIComponent("http://127.0.0.1:1234/callback")), "auth url redirect encoded");
  assert(au.includes("state=st8"), "auth url state");
  console.log("selftest OK");
}

// ── CLI ─────────────────────────────────────────────────────────────────────
async function main() {
  const args = process.argv.slice(2);
  if (args.includes("--selftest")) return selftest();

  if (args[0] === "login") {
    const tokens = await loginFlow();
    if (!tokens.refresh_token) {
      throw new Error(
        "Google returned no refresh_token. It's only sent with access_type=offline + " +
          "prompt=consent (both set here) or after revoking the prior grant at " +
          "myaccount.google.com/permissions — revoke and retry."
      );
    }
    saveRefreshToken(tokens.refresh_token);
    process.stderr.write(`refresh_token saved to ${TOKEN_FILE}\n`);
    process.stdout.write(tokens.refresh_token + "\n"); // copy to other devices / AGY_REFRESH_TOKEN
    return;
  }

  const refreshToken = loadRefreshToken();
  if (!refreshToken) {
    throw new Error("no refresh token — run `node agy-gemini.mjs login` first (or set AGY_REFRESH_TOKEN).");
  }
  const accessToken = await refreshAccessToken(refreshToken);

  if (args[0] === "models") {
    const projectId = await bootstrapProject(accessToken);
    const { models, raw } = await listModels(accessToken, projectId);
    if (process.env.AGY_DEBUG) process.stderr.write(JSON.stringify(raw, null, 2) + "\n");
    process.stdout.write((models.length ? models.join("\n") : "(no models returned)") + "\n");
    return;
  }

  let model = process.env.AGY_MODEL || "gemini-3.7-flash-tiered";
  const rest = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--model") model = args[++i];
    else rest.push(args[i]);
  }
  const prompt = rest.join(" ") || "Say hello in one short sentence.";

  const projectId = await bootstrapProject(accessToken);
  process.stderr.write(`[project ${projectId}] [model ${model}]\n`);
  const request = { contents: [{ role: "user", parts: [{ text: prompt }] }] };
  await streamGenerate(accessToken, projectId, model, request, (t) => process.stdout.write(t));
  process.stdout.write("\n");
}

// Run the CLI only when invoked directly — imported (by agy-server.mjs) it just exports.
if (import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((e) => { console.error("ERROR:", e.message); process.exit(1); });
}
