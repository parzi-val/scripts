#!/usr/bin/env bash
# ==============================================================================
# Universal AI Agent Skill Discovery, Retrieval & Cross-Harness Manager (Bash)
# ==============================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUST_BIN="${SCRIPT_DIR}/skills"
RUST_EXE="${SCRIPT_DIR}/skills.exe"
PY_SCRIPT="${SCRIPT_DIR}/skill_discovery.py"

if [ -x "$RUST_BIN" ]; then
    exec "$RUST_BIN" "$@"
elif [ -x "$RUST_EXE" ]; then
    exec "$RUST_EXE" "$@"
elif command -v python3 &>/dev/null; then
    exec python3 "$PY_SCRIPT" "$@"
elif command -v python &>/dev/null; then
    exec python "$PY_SCRIPT" "$@"
else
    echo "[FAIL] Neither Rust binary nor Python 3 found." >&2
    exit 1
fi
