# scripts

Personal automation scripts and tools.

## LLM / proxy

- **`agy-gemini.mjs`** — zero-dep Node client that reaches Gemini / Claude / GPT through Google Antigravity's backend on a Google AI Pro plan. Commands: `login` (one-time browser OAuth), `models` (live catalog), and chat (`node agy-gemini.mjs "prompt"`). Refresh token saved to `~/.agy-gemini.json`.
- **`agy-server.mjs`** — local OpenAI-compatible endpoint (`/v1/chat/completions`, `/v1/models`) plus a tiny dashboard (quota bars + local API-key management) built on `agy-gemini.mjs`. Binds `127.0.0.1:8787`.
- **`cc.bat`** — launch Claude Code against a local OmniRoute proxy. Reads its token from `.env` (see setup).

## skills

- **`skill_discovery.py`**, **`skills.ps1`**, **`skills.sh`** — skill-discovery helpers.
- **`skills-rs/`** — Rust version (source only; `cargo build --release`).
- **`SKILL_DISCOVERY_GUIDE.md`** — notes.

## misc

- **`flatten.py`** — recursively bring all files in a directory tree up to the top level.
- **`ppt2pdf.py`** — recursively convert PPTs in a directory to PDFs. Handy for [NotebookLM](https://notebooklm.google.com/).
- **`md-rs/`** — `mdv`, renders markdown in the terminal with ANSI colors (headings by color, code, lists, quotes, links). `mdv file.md` clears the screen and shows the file's path as a colored header; `-i` renders inline without clearing; or pipe via stdin. Source only; `cargo build --release`, then copy `target/release/mdv.exe` onto your PATH.

## setup

Secrets live in a gitignored `.env` at the repo root (never committed):

```
groq=<groq api key>
omniroute=<omniroute proxy token>   # consumed by cc.bat
```
