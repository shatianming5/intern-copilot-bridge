#!/usr/bin/env python3
"""Copilot-CLI -> Feishu streaming bridge (v3).

Direct-to-Feishu (like claude/codex hooks) rich streaming that matches the
claude/codex UX and adds token-smooth growth:

  #1 rich inline markdown (**bold** `code` [links] *italic* --- ```code```)
  #2 ctx/cost footer (📊 ctx k/k · ⬆in ⬇out)
  #3 user-prompt echo header (🧑 用户: ... + divider), opened immediately
  #4 long-reply continuation rollover (Feishu ~17-edit / 28KB caps)
  #5 semantic tool summaries (Bash:/Read:/Edit:/Search:/Todo/...)
  #6 per-tool ✅/❌ status
  #7 images/screenshots/files sent as image/file messages
  #8 interactive question cards (AskUser / option menus)
  #9 detail_mode-ish suppression, restart continuity (state persisted)

Char-level streaming: events.jsonl only has completed segments, so the
in-progress segment is overlaid from the tmux pane (copilot streams tokens
there). Feishu caps edits at ~17/message, so growth is a smooth ~1s cadence
(not literally per-char, which Feishu forbids) with continuation rollover.

Env: COP_SID, COP_INTERN, COP_PROJECT, COP_HOME, COP_TMUX,
     COP_POLICY_JSON (enterprise_policy/daemon/policy.json),
     COP_REGISTRY_DIR (.feishu_registry).
"""
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import feishu_api as fa  # noqa: E402

SID = os.environ["COP_SID"]
INTERN = os.environ["COP_INTERN"]
PROJECT = os.environ["COP_PROJECT"]
HOME = os.environ.get("COP_HOME", os.path.expanduser("~"))
TMUX = os.environ.get("COP_TMUX", "")
WA = os.path.join(HOME, "work-agents")
POLICY_JSON = os.environ.get(
    "COP_POLICY_JSON", os.path.join(WA, "enterprise_policy", "daemon", "policy.json"))
REGISTRY_DIR = os.environ.get(
    "COP_REGISTRY_DIR", os.path.join(WA, ".feishu_registry"))

EVENTS = os.path.join(HOME, ".copilot", "session-state", SID, "events.jsonl")
LOG = os.path.join(WA, f".copstream_{INTERN}.log")
STATE = os.path.join(WA, f".copstream_{INTERN}.state")

TICK = 0.8                 # loop cadence (also min interval between Feishu edits)
IDLE_FINALIZE = 6.0        # s of no events + no pending tools => finalize turn
HARD_FINALIZE = 120.0      # force finalize even if a tool-complete was missed
MAX_EDITS = 17             # Feishu edit cap (leave headroom to 20)
MAX_BODY = 28000           # Feishu post body cap (30KB - safety)
SPINNER = "\n\n⏳ 处理中..."

# copilot tool names that are "noisy" (suppressed in the tool line but the
# assistant prose around them still shows) — mirrors claude summary mode intent.
_NOISY_TOOLS = {"view", "read_bash", "grep", "glob", "bash"}


def log(msg):
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass


# ── credentials + chat_id + token ─────────────────────────────────────────
_tok_cache = {"token": None, "exp": 0}


def _creds():
    p = json.load(open(POLICY_JSON, encoding="utf-8"))
    f = p.get("feishu") or {}
    return f.get("app_id", ""), f.get("app_secret", "")


def token():
    now = time.time()
    if _tok_cache["token"] and now < _tok_cache["exp"] - 300:
        return _tok_cache["token"]
    aid, sec = _creds()
    t = fa.get_tenant_token(aid, sec)
    if t:
        _tok_cache["token"] = t
        _tok_cache["exp"] = now + 6600
    return t


def chat_id():
    f = os.path.join(REGISTRY_DIR, f"{PROJECT}__{INTERN}.json")
    try:
        d = json.load(open(f, encoding="utf-8"))
        return d.get("chatId") or d.get("chat_id")
    except Exception:
        return None


# ── #5 semantic tool summaries (copilot tool names) ───────────────────────
def _short(s, n=80):
    s = str(s or "").strip().replace("\n", " ")
    return s[:n] + ("…" if len(s) > n else "")


def tool_summary(name, args):
    args = args if isinstance(args, dict) else {}
    if name == "bash":
        cmd = _short(args.get("command") or args.get("cmd") or "", 90)
        first = cmd.split(None, 1)[0] if cmd else ""
        if first in ("rg", "grep", "find", "fd"):
            return f"Search: `{cmd}`"
        if first in ("cat", "sed", "tail", "head", "nl", "wc", "less"):
            return f"Read: `{cmd}`"
        if first == "ls":
            return f"List: `{cmd}`"
        if first == "git":
            return f"Git: `{cmd}`"
        return f"Bash: `{cmd}`"
    if name in ("view", "read_file"):
        return f"Read: `{_short(args.get('path') or args.get('filePath') or '?')}`"
    if name == "create":
        return f"Write: `{_short(args.get('path') or '?')}`"
    if name in ("edit", "str_replace", "str_replace_editor", "multi_edit"):
        return f"Edit: `{_short(args.get('path') or args.get('filePath') or '?')}`"
    if name in ("grep",):
        return f"Grep: `{_short(args.get('pattern') or args.get('query') or '?', 60)}`"
    if name in ("glob",):
        return f"Glob: `{_short(args.get('pattern') or '?', 60)}`"
    if name == "read_bash":
        return "Read shell output"
    if name in ("web_search",):
        return f"WebSearch: `{_short(args.get('query') or '?', 60)}`"
    if name in ("web_fetch",):
        return f"WebFetch: `{_short(args.get('url') or '?')}`"
    if name in ("sql",):
        return f"SQL: `{_short(args.get('query') or args.get('description') or '?', 70)}`"
    if name in ("store_memory",):
        return "📝 store_memory"
    if name in ("view_image",):
        return f"Viewed image: `{_short(args.get('path') or '?')}`"
    if name in ("task",):
        return f"SubAgent: `{_short(args.get('description') or args.get('prompt') or '?')}`"
    if name in ("ask_user",):
        return f"AskUser: {_short(args.get('question') or '?', 60)}"
    return name


# ── #2 footer ─────────────────────────────────────────────────────────────
def _k(t):
    return f"{t/1000:.0f}k" if t >= 10000 else f"{t/1000:.1f}k"


def footer(usage):
    if not usage:
        return ""
    parts = []
    out = usage.get("out", 0)
    if out:
        parts.append(f"⬇{_k(out)} tok")
    aic = usage.get("aic")
    if aic:
        parts.append(f"💳 {aic} AIC")
    tier = usage.get("tier")
    if tier:
        parts.append("1M ctx" if tier == "long_context" else tier)
    return ("📊 " + " · ".join(parts)) if parts else ""


_AIC_RE = re.compile(r"Session:\s*([\d.]+)\s*AIC")


def pane_aic():
    """Extract 'Session: X AIC used' from the pane footer (copilot cost unit)."""
    if not TMUX:
        return None
    try:
        r = subprocess.run(["tmux", "capture-pane", "-t", TMUX, "-p"],
                           capture_output=True, text=True, timeout=3)
    except Exception:
        return None
    m = _AIC_RE.search(r.stdout or "")
    return m.group(1) if m else None


# ── char-streaming: pane tail extraction ──────────────────────────────────
def pane_busy():
    """True if the copilot pane is actively generating (spinner visible).
    None if the pane can't be read (caller should fall back to time-based)."""
    if not TMUX:
        return None
    try:
        r = subprocess.run(["tmux", "capture-pane", "-t", TMUX, "-p"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return None
    except Exception:
        return None
    for ln in r.stdout.splitlines():
        s = ln.strip()
        if "esc cancel" in s and ("Working" in s or "B esc" in s or "KiB esc" in s):
            return True
        if "Compacting" in s:          # compaction is active work, keep turn open
            return True
    return False


_BORDER = re.compile(r"\s*┃\s*$")
_TIME_TAIL = re.compile(r"\s+\d{1,2}:\d{2}\s*$")


def pane_tail():
    """Extract the in-progress assistant text block from the tmux pane.

    Returns the text of the last '●'-prefixed assistant block (before the
    spinner/footer), or '' if not confidently found. Defensive: any parse
    trouble => '' (fall back to events-only)."""
    if not TMUX:
        return ""
    try:
        r = subprocess.run(["tmux", "capture-pane", "-t", TMUX, "-p"],
                           capture_output=True, text=True, timeout=3)
        if r.returncode != 0:
            return ""
        raw = r.stdout.splitlines()
    except Exception:
        return ""
    # strip right border + trailing timestamps
    lines = []
    for ln in raw:
        ln = _BORDER.sub("", ln.rstrip())
        lines.append(ln)
    # find the spinner/footer boundary from the bottom
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        s = lines[i].strip()
        if ("Working ·" in s and "esc cancel" in s) or s.startswith("❯") or \
           ("Session:" in s and "AIC" in s) or set(s) <= set("─"):
            end = i
        elif s:
            break
    # find last assistant block start '● ' above end
    start = None
    for i in range(end - 1, -1, -1):
        s = lines[i].lstrip()
        if s.startswith("● "):
            start = i
            break
        # stop if we hit a tool/user marker (block boundary)
        if s.startswith(("$ ", "❯ ", "🧑", "⎿")) or s[:2] in ("MD", "$ "):
            break
    if start is None:
        return ""
    block = []
    for i in range(start, end):
        s = lines[i]
        st = s.lstrip()
        if i == start:
            st = st[2:]  # drop "● "
        elif st.startswith(("$ ", "❯ ", "⎿")) or (st.startswith("●") and st[:2] != "● "):
            break  # next tool/marker
        block.append(st.rstrip())
    text = "\n".join(block).strip()
    text = _TIME_TAIL.sub("", text)
    return text if len(text) >= 2 else ""


# ── rich send/update via vendored feishu_api (direct) ─────────────────────
def _compose(header, segments, live_tail, ftr, spinner):
    parts = []
    if header:
        parts.append(header)
    for seg in segments:
        parts.append(seg)
    if live_tail:
        parts.append(live_tail)
    text = "\n".join(p for p in parts if p).strip()
    if ftr:
        text += "\n" + ftr
    if spinner:
        text += SPINNER
    return text


def _body_ok(text):
    try:
        return fa.estimate_post_body_size(text) <= MAX_BODY
    except Exception:
        return len(text.encode("utf-8")) <= MAX_BODY


class Turn:
    """Holds the streaming state for one user request."""
    def __init__(self):
        self.header = ""
        self.segments = []        # committed lines (prose / tool with status)
        self.tool_idx = {}        # toolCallId -> segment index (for ✅/❌)
        self.msg_id = None
        self.msg_base = 0         # first segment index shown in current message
        self.edits = 0
        self.usage = {}
        self.pending_tools = 0
        self.last_activity = time.time()
        self.done = False
        self.last_sent = ""
        self.last_edit_at = 0.0
        self.live_tail = ""        # pane overlay of the in-progress segment
        self.streaming = False     # True while a new prose segment is being typed
        self.last_prose = ""       # last committed assistant prose (for dedup)
        self.idle_since = 0.0      # when the pane first went idle this turn
        self.produced = False      # got >=1 assistant.message/tool (finalize gate)
        self.stale_tail = ""       # pane content at turn open (suppress until it changes)


def _persist(turn):
    try:
        json.dump({"msg_id": turn.msg_id, "header": turn.header,
                   "segments": turn.segments, "msg_base": turn.msg_base,
                   "edits": turn.edits, "done": turn.done},
                  open(STATE, "w"), ensure_ascii=False)
    except Exception:
        pass


def send_new(chat, text):
    t = token()
    if not t:
        return None
    mid, err = fa.send_message(t, chat, text)
    if err:
        log(f"send err: {err}")
    return mid


def edit(mid, text):
    t = token()
    if not t:
        return False, "no token"
    return fa.update_message(t, mid, text)


# compaction feedback state (a real /compact takes ~30-40s; show progress)
_compact = {"msg_id": None, "t": 0.0, "active": False}


def _compact_notify(chat, phase):
    now = time.time()
    if phase == "start":
        _compact["active"] = True
        _compact["t"] = now
        mid = send_new(chat, "🗜 正在压缩上下文以释放窗口，请稍候…")
        _compact["msg_id"] = mid
        log("compaction start")
    else:  # complete
        _compact["active"] = False
        dur = int(now - _compact["t"]) if _compact["t"] else 0
        txt = "✅ 上下文已压缩，窗口已释放" + (f"（耗时 {dur}s）" if dur >= 2 else "")
        if _compact["msg_id"]:
            edit(_compact["msg_id"], txt)
        else:
            send_new(chat, txt)
        _compact["msg_id"] = None
        log(f"compaction complete {dur}s")


def main():
    chat = chat_id()
    if not chat:
        log("FATAL: no chat_id")
        return
    log(f"stream v3 start: intern={INTERN} sid={SID} chat={chat} tmux={TMUX}")
    # #9 restart continuity: close any orphaned (unfinalized) message from a
    # previously-killed poller so no ⏳ spinner is left hanging.
    try:
        if os.path.exists(STATE):
            prev = json.load(open(STATE))
            if prev.get("msg_id") and not prev.get("done"):
                base = prev.get("segments", [])[prev.get("msg_base", 0):]
                head = prev.get("header", "") if prev.get("msg_base", 0) == 0 else "（接上条消息）"
                txt = _compose(head, base, "", "", False) + "\n\n（会话已恢复）"
                edit(prev["msg_id"], txt)
                log(f"closed orphaned msg {prev['msg_id']}")
    except Exception as ex:
        log(f"orphan-close skip: {ex}")
    offset = os.path.getsize(EVENTS) if os.path.exists(EVENTS) else 0
    turn = Turn()
    turn.done = True  # no active turn yet

    def flush(spinner=True, force=False):
        """Compose current view and push to Feishu (update / rollover / send)."""
        now = time.time()
        if not force and (now - turn.last_edit_at) < TICK:
            return
        base = turn.segments[turn.msg_base:]
        header = turn.header if turn.msg_base == 0 else "（接上条消息）"
        live = turn.live_tail
        text = _compose(header, base, live, footer(turn.usage), spinner)
        if text == turn.last_sent and not force:
            return
        # rollover on edit-cap or body-size — but only on committed-segment
        # boundaries. Mid-stream (pane tail active), freeze instead of splitting
        # the growing text messily; the segment will commit and split cleanly.
        if turn.msg_id and (turn.edits >= MAX_EDITS or not _body_ok(text)):
            if turn.live_tail:
                return  # streaming: hold until segment commits
            fin = _compose(header, base, "", footer(turn.usage), False) + "\n\n(续下条...)"
            edit(turn.msg_id, fin)
            turn.msg_base = len(turn.segments)
            base = []
            cont = _compose("（接上条消息）", base, "", footer(turn.usage), spinner)
            mid = send_new(chat, cont)
            if mid:
                turn.msg_id = mid
                turn.edits = 0
                turn.last_sent = cont
                turn.last_edit_at = now
                log(f"rollover -> new msg {mid}")
            return
        if turn.msg_id is None:
            mid = send_new(chat, text)
            if mid:
                turn.msg_id = mid
                turn.edits = 0
                turn.last_sent = text
                turn.last_edit_at = now
                log(f"SEND msg={mid} len={len(text)} live={len(turn.live_tail)}")
        else:
            ok, err = edit(turn.msg_id, text)
            if ok:
                turn.edits += 1
                turn.last_sent = text
                turn.last_edit_at = now
                log(f"EDIT #{turn.edits} len={len(text)} live={len(turn.live_tail)}")
            elif err and ("230072" in str(err) or "230025" in str(err)):
                # edit cap / too long reached reactively -> force rollover next
                turn.edits = MAX_EDITS
                log(f"edit-limit reactive: {err}")
        _persist(turn)

    def finalize():
        if turn.done or turn.msg_id is None:
            turn.done = True
            return
        base = turn.segments[turn.msg_base:]
        header = turn.header if turn.msg_base == 0 else "（接上条消息）"
        text = _compose(header, base, "", footer(turn.usage), False)
        edit(turn.msg_id, text)
        log(f"finalize msg {turn.msg_id} segs={len(turn.segments)} preview={text[:200]!r}")
        turn.done = True
        _persist(turn)

    turn.live_tail = ""
    while True:
        try:
            size = os.path.getsize(EVENTS) if os.path.exists(EVENTS) else 0
            if size < offset:
                offset = 0
            new_events = False
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
                        new_events = True
                        _handle(e, turn, chat, flush, finalize)
            # char-streaming: overlay in-progress pane tail only while a new
            # prose segment is being typed (not during tools / after commit)
            if not turn.done and turn.streaming and turn.pending_tools == 0:
                pt = pane_tail()
                # suppress stale content still visible from the previous turn
                if pt and turn.stale_tail and (pt == turn.stale_tail or pt in turn.stale_tail):
                    pt = ""
                elif pt and turn.stale_tail:
                    turn.stale_tail = ""   # diverged -> new generation began
                # dedup: suppress if it just mirrors the last committed prose
                if pt and turn.last_prose and (
                        pt == turn.last_prose or pt in turn.last_prose
                        or turn.last_prose.endswith(pt)):
                    pt = ""
                turn.live_tail = pt or ""
            else:
                turn.live_tail = ""
            # #2 refresh AIC cost from pane footer
            if not turn.done:
                aic = pane_aic()
                if aic:
                    turn.usage["aic"] = aic
            if not turn.done:
                flush(spinner=True)
                # finalize decision: prefer pane idle signal over pure time gap
                busy = pane_busy()
                if _compact["active"]:
                    busy = True   # never finalize a turn while compaction runs
                idle = time.time() - turn.last_activity
                if busy is True:
                    turn.idle_since = 0.0
                elif busy is False and turn.produced:
                    if not turn.idle_since:
                        turn.idle_since = time.time()
                    # pane idle + brief grace + no pending tools => done
                    if turn.pending_tools == 0 and (time.time() - turn.idle_since) >= 3.0 \
                            and idle >= 2.0:
                        finalize()
                elif busy is None and turn.produced:  # pane unreadable -> time fallback
                    if (turn.pending_tools == 0 and idle >= IDLE_FINALIZE) or idle >= HARD_FINALIZE:
                        finalize()
                if turn.produced and idle >= HARD_FINALIZE:
                    finalize()
            time.sleep(TICK)
        except Exception as ex:
            log(f"loop err: {ex}")
            time.sleep(2)


def _handle(e, turn, chat, flush, finalize):
    t = e.get("type")
    d = e.get("data") or {}
    if t in ("user.message", "assistant.turn_start", "assistant.message",
             "tool.execution_start", "tool.execution_complete", "assistant.turn_end"):
        turn.last_activity = time.time()

    if t == "user.message":
        # #3 open immediately with echoed prompt + divider + spinner
        if not turn.done:
            finalize()
        prompt = (d.get("content") or "").strip()
        # skip our own injected system/handoff style prompts? show as user
        turn.__init__()
        turn.header = f"🧑 用户: {prompt}\n---" if prompt else ""
        turn.done = False
        turn.streaming = True
        turn.stale_tail = pane_tail()   # remember prior content to suppress flash
        turn.last_activity = time.time()
        flush(spinner=True, force=True)

    elif t == "assistant.turn_start":
        turn.streaming = True

    elif t == "assistant.message":
        if turn.done:
            turn.__init__(); turn.done = False
        c = (d.get("content") or "").strip()
        if c:
            turn.segments.append(c)      # #1 rich markdown (rendered by feishu_api)
            turn.live_tail = ""          # authoritative replaces pane preview
            turn.last_prose = c
            turn.streaming = False       # committed; wait for next turn_start
            turn.produced = True
        # #2 usage: copilot assistant.message carries cumulative outputTokens
        ot = d.get("outputTokens")
        if isinstance(ot, int) and ot > turn.usage.get("out", 0):
            turn.usage["out"] = ot
        if d.get("contextTier"):
            turn.usage["tier"] = d["contextTier"]
        flush(spinner=True)

    elif t == "tool.execution_start":
        name = d.get("toolName") or "tool"
        # #8 interactive card for ask_user
        if name == "ask_user":
            _send_question_card(chat, d.get("arguments") or {})
        summary = tool_summary(name, d.get("arguments"))
        turn.segments.append(f"⎿ {summary}")     # #5 semantic
        cid = d.get("toolCallId")
        if cid:
            turn.tool_idx[cid] = len(turn.segments) - 1
        turn.pending_tools += 1
        turn.live_tail = ""
        turn.streaming = False
        turn.produced = True
        flush(spinner=True)

    elif t == "tool.execution_complete":
        turn.pending_tools = max(0, turn.pending_tools - 1)
        cid = d.get("toolCallId")
        ok = d.get("success", True)
        if cid in turn.tool_idx:                  # #6 per-tool status
            i = turn.tool_idx[cid]
            mark = "✅" if ok else "❌"
            if not turn.segments[i].startswith(("✅", "❌")):
                turn.segments[i] = f"{mark} {turn.segments[i][2:].lstrip()}" \
                    if turn.segments[i].startswith("⎿ ") else f"{mark} {turn.segments[i]}"
        # #7 image/file detection from result
        _maybe_send_media(chat, d)
        if turn.pending_tools == 0:
            turn.streaming = True          # next prose segment may start
        flush(spinner=True)

    elif t == "assistant.turn_end":
        turn.last_activity = time.time()

    elif t == "session.compaction_start":
        _compact_notify(chat, "start")
        turn.last_activity = time.time()

    elif t == "session.compaction_complete":
        _compact_notify(chat, "complete")
        turn.last_activity = time.time()


def _send_question_card(chat, args):
    try:
        q = str(args.get("question") or "").strip()
        choices = args.get("choices") or []
        if not q:
            return
        elems = [{"tag": "div", "text": {"tag": "lark_md", "content": f"**❓ {q}**"}}]
        if choices:
            opts = "\n".join(f"{i+1}. {c}" for i, c in enumerate(choices))
            elems.append({"tag": "div", "text": {"tag": "lark_md", "content": opts}})
            elems.append({"tag": "note", "elements": [
                {"tag": "plain_text", "content": "直接回复选项编号或文字即可"}]})
        card = {"config": {"wide_screen_mode": True},
                "header": {"template": "orange",
                           "title": {"tag": "plain_text", "content": "需要你的决策"}},
                "elements": elems}
        t = token()
        if t:
            fa.send_interactive(t, chat, card)
            log(f"sent question card ({len(choices)} choices)")
    except Exception as ex:
        log(f"question card err: {ex}")


_IMG_RE = re.compile(r"(/[^\s'\"]+\.(?:png|jpg|jpeg|gif|webp))", re.IGNORECASE)


def _maybe_send_media(chat, d):
    try:
        res = d.get("result")
        text = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False) if res else ""
        m = _IMG_RE.search(text or "")
        if not m:
            return
        path = m.group(1)
        if not os.path.isfile(path):
            return
        t = token()
        if not t:
            return
        key, err = fa.upload_image(t, path)
        if key:
            fa.send_image(t, chat, key)
            log(f"sent image {path}")
    except Exception as ex:
        log(f"media err: {ex}")


if __name__ == "__main__":
    main()
