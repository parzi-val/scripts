"""Checks for agents-dash parsing. Run: python test_agents_dash.py"""
import json
import os
import tempfile

import agents_dash as ad

assert ad.clean("hi <system-reminder>x</system-reminder> there") == "hi there"
assert ad.clean("<command-name>/btw</command-name><command-message>btw</command-message>"
                "<command-args>is it on</command-args>") == "/btw is it on"
assert ad.clean(r"from uds:\\.\pipe\LOCAL\cc-msg-abc123 ok") == "from [pipe] ok"
assert ad.slug(r"D:\seriesn\research") == "D--seriesn-research"
assert ad.request_of({"origin": {"kind": "peer", "name": "manager", "body": "hey"}}) == {"text": "hey", "from": "manager"}
assert ad.request_of({"type": "user", "message": {"content": [{"type": "tool_result"}]}}) is None
assert ad.proc_info(os.getpid(), "1") is None  # live PID, wrong start time: stale registry entry

# Transcript tail: newest title/request/reply win; tool results and sidechains are skipped.
records = [
    {"type": "custom-title", "customTitle": "old-name"},
    {"type": "user", "origin": {"kind": "human"}, "message": {"content": "first ask"}, "cwd": "D:\\x"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "first answer"}]}},
    {"type": "custom-title", "customTitle": "new-name"},
    {"type": "user", "origin": {"kind": "human"}, "message": {"content": "second ask"}, "cwd": "D:\\x"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "second answer"}]}},
    {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
    {"type": "user", "message": {"content": [{"type": "tool_result", "content": "noise"}]}},
    {"type": "assistant", "isSidechain": True, "message": {"content": [{"type": "text", "text": "subagent"}]}},
]
with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False, encoding="utf-8") as f:
    f.write("\n".join(json.dumps(r) for r in records) + "\n")
try:
    # A tiny chunk size forces lines to be split across reads.
    lines = [json.loads(l) for l in ad.tail_lines(f.name, chunk=7) if l.strip()]
    assert lines == records[::-1]
    info = ad.read_transcript(f.name)
    # Reading stops once title, request, reply and cwd are found, so older names past that point are skipped.
    assert info["names"] == ["new-name"]
    assert info["request"]["text"] == "second ask" and info["reply"]["text"] == "second answer"
    assert info["cwd"] == "D:\\x"
finally:
    os.unlink(f.name)
print("ok")
