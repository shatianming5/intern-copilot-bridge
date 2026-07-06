#!/usr/bin/env python3
"""Streaming outbound bridge: Copilot-CLI session events.jsonl -> Feishu group,
matching the claude/codex real-time UX (one message per user request, edited
live as the agent narrates + runs tools, finalized when the turn goes idle).

Per user request (a copilot multi-step response):
  1. first narration/tool  -> POST /api/message/send  (get message_id)
  2. new content (throttled) -> POST /api/message/update (edit in place)
  3. turn goes idle          -> POST /api/message/finalize (clean final text)

Config via env: COP_SID, COP_INTERN, COP_PROJECT, COP_HOME, COP_DAEMON_JSON.
"""
import json
import os
import time
import urllib.request

SID = os.environ["COP_SID"]
INTERN = os.environ["COP_INTERN"]
PROJECT = os.environ["COP_PROJECT"]
HOME = os.environ.get("COP_HOME", os.path.expanduser("~"))
DAEMON_JSON = os.environ.get("COP_DAEMON_JSON", "/tmp/feishu_daemon.json")

EVENTS = os.path.join(HOME, ".copilot", "session-state", SID, "events.jsonl")
LOG = os.path.join(HOME, "work-agents", f".copbridge_{INTERN}.log")

UPDATE_INTERVAL = 2.5     # min seconds between Feishu edits
IDLE_FINALIZE = 8.0       # seconds of no events + no pending tools => turn done
HARD_FINALIZE = 90.0      # force-finalize even if a tool-complete was missed
MAX_CHARS = 3600          # Feishu-safe cap (show tail if longer)
POLL = 1.0


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


def daemon_url():
    with open(DAEMON_JSON) as f:
        return f"http://localhost:{json.load(f)['http_port']}"


def _post(path, payload):
    req = urllib.request.Request(
        daemon_url() + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "replace") or "{}")


def send(text):
    return _post("/api/message/send",
                 {"intern_name": INTERN, "project": PROJECT, "text": text}).get("message_id", "")


def update(mid, text):
    _post("/api/message/update", {"message_id": mid, "text": text})


def finalize(mid, text):
    _post("/api/message/finalize", {"message_id": mid, "text": text})


def compact_args(args):
    if not isinstance(args, dict):
        return ""
    for k in ("command", "cmd", "script"):
        if args.get(k):
            return str(args[k]).strip().splitlines()[0][:80]
    for k in ("path", "filePath", "file_path", "query", "url", "pattern", "prompt"):
        if args.get(k):
            return str(args[k]).strip()[:80]
    return ""


def render(lines, working):
    parts = []
    for it in lines:
        if it[0] == "text":
            parts.append(it[1])
        else:
            _, tn, ca = it
            parts.append(f"⎿ {tn}" + (f"  {ca}" if ca else ""))
    text = "\n".join(p for p in parts if p).strip()
    if len(text) > MAX_CHARS:
        text = "…(前文略)\n" + text[-MAX_CHARS:]
    if working:
        text = (text + "\n\n⏳ …") if text else "⏳ …"
    return text


def main():
    # start at end of file (don't replay history on (re)start)
    offset = os.path.getsize(EVENTS) if os.path.exists(EVENTS) else 0
    state = {"mid": None, "lines": [], "last_render": "", "last_activity": 0.0,
             "last_update": 0.0, "pending_tools": 0, "turn_open": False}
    log(f"stream-bridge start: intern={INTERN} sid={SID} offset={offset}")

    def maybe_flush():
        text = render(state["lines"], working=True)
        if not text.strip("⏳ …\n"):
            return
        now = time.time()
        if state["mid"] is None:
            try:
                state["mid"] = send(text)
                state["last_render"], state["last_update"] = text, now
                log(f"send mid={state['mid']} ({len(text)}c)")
            except Exception as ex:
                log(f"send failed: {ex}")
        elif text != state["last_render"] and now - state["last_update"] >= UPDATE_INTERVAL:
            try:
                update(state["mid"], text)
                state["last_render"], state["last_update"] = text, now
                log(f"update mid={state['mid']} ({len(text)}c)")
            except Exception as ex:
                log(f"update failed: {ex}")

    def do_finalize():
        if state["mid"] is not None:
            final = render(state["lines"], working=False)
            try:
                finalize(state["mid"], final)
                log(f"finalize mid={state['mid']} ({len(final)}c): {final[:70]!r}")
            except Exception as ex:
                log(f"finalize failed: {ex}")
        state.update(mid=None, lines=[], last_render="", turn_open=False, pending_tools=0)

    while True:
        try:
            size = os.path.getsize(EVENTS) if os.path.exists(EVENTS) else 0
            if size < offset:
                offset = 0
            if size > offset:
                with open(EVENTS, "rb") as f:
                    f.seek(offset)
                    buf = f.read()
                nl = buf.rfind(b"\n")
                if nl >= 0:
                    offset += nl + 1
                    for raw in buf[:nl + 1].split(b"\n"):
                        if not raw.strip():
                            continue
                        try:
                            e = json.loads(raw.decode("utf-8", "replace"))
                        except Exception:
                            continue
                        t = e.get("type")
                        d = e.get("data") or {}
                        if t in ("user.message", "assistant.turn_start", "assistant.message",
                                 "tool.execution_start", "tool.execution_complete", "assistant.turn_end"):
                            if not state["turn_open"] and t in ("user.message", "assistant.turn_start", "assistant.message"):
                                state.update(turn_open=True, lines=[], mid=None, last_render="", pending_tools=0)
                            state["last_activity"] = time.time()
                        if t == "assistant.message":
                            c = (d.get("content") or "").strip()
                            if c:
                                state["lines"].append(("text", c))
                                maybe_flush()
                        elif t == "tool.execution_start":
                            state["lines"].append(("tool", d.get("toolName") or "tool", compact_args(d.get("arguments"))))
                            state["pending_tools"] += 1
                            maybe_flush()
                        elif t == "tool.execution_complete":
                            state["pending_tools"] = max(0, state["pending_tools"] - 1)
            if state["turn_open"] and state["mid"] is not None:
                idle = time.time() - state["last_activity"]
                if (state["pending_tools"] == 0 and idle >= IDLE_FINALIZE) or idle >= HARD_FINALIZE:
                    do_finalize()
            elif state["turn_open"] and state["mid"] is None and (time.time() - state["last_activity"]) >= IDLE_FINALIZE:
                state["turn_open"] = False
            time.sleep(POLL)
        except Exception as ex:
            log(f"loop error: {ex}")
            time.sleep(2)


if __name__ == "__main__":
    main()
