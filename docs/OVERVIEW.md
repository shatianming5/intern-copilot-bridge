# System Overview — how the intern service works

A friendly, end-to-end tour of the whole system with ASCII diagrams.

> **In one line:** it turns **GitHub Copilot CLI** into a "intern" you can talk to
> from a **Feishu (Lark) group chat** in real time — you type in the group, it does
> real work on a server (reads code, runs commands, edits files), and its reply
> **streams back** character-by-character into a single growing message. Today it
> runs **7 interns** across **3 machines**.

Infra hostnames/IPs below are redacted as `<RELAY_HOST>` / `<GPU_HOST>`; no chat
ids, tokens, phone numbers, or internal IPs are committed.

---

## 1. Global topology

```
                     ┌──────────────────────────────┐
      you (owner) ──► │        Feishu (Lark) chats ×7 │ ◄──── rich replies stream in
                     └───────────────┬──────────────┘
                                     │  Feishu event webhook  ⇅  message API
                                     ▼
                     ┌──────────────────────────────┐
                     │        Relay server            │   <RELAY_HOST>
                     │   ws://…:28081  (WebSocket)    │   routes by intern name
                     │   http://…:28080 (HTTP)        │   (public entry → reaches
                     └───┬──────────────┬─────────┬───┘    private machines)
             WS keepalive│              │         │
          ┌──────────────┘              │         └───────────────┐
          ▼                             ▼                         ▼
   ┌─────────────┐              ┌─────────────┐           ┌─────────────┐
   │  server3    │              │   GPU box   │           │   Mac       │
   │ 64c / 247G  │              │ 5×4090D     │           │  (laptop)   │
   │ 5 interns   │              │ 1 intern    │           │ 1 intern    │
   │ dd/123/clade│              │ quant       │           │ mosaic      │
   │ /srv3/skill │              │ (+ miner)   │           │             │
   └─────────────┘              └─────────────┘           └─────────────┘
```

**Why a Relay?** the three machines live on different networks (cloud / GPU
cluster / a laptop), so Feishu webhooks can't reach them directly. The Relay is
the single public entry point: each machine's daemon holds **one outbound
WebSocket** to the Relay, and messages are routed both ways by `intern_name`.

---

## 2. Process stack inside one machine

```
┌──────────────────────── inside one machine (e.g. the GPU box / quant) ────────────────────────┐
│                                                                                                │
│   ┌────────────────┐   WS keepalive   ┌──────────┐                                             │
│   │ feishu_daemon  │◄───────────────► │  Relay   │      inbound: route + auth + policy         │
│   │    .py         │                  └──────────┘                                             │
│   └──┬─────────▲───┘                                                                            │
│      │send-keys│ pidfile + log                                                                  │
│      ▼         │                                                                                │
│   ┌────────────────────────────────────────────┐                                               │
│   │ tmux: cop_<name>                           │                                                │
│   │   └─ copilot --resume=<SID> --allow-all    │ ◄── the actual Copilot CLI doing the work      │
│   └──────┬─────────────────────────────────────┘                                               │
│          │ every step (message / tool / compaction) is appended to                             │
│          ▼                                                                                      │
│   ~/.copilot/session-state/<SID>/events.jsonl                                                   │
│          │ tail (committed segments)   ┌───────────────────────┐                               │
│          ├──────────────────────────►  │  copilot_stream.py    │  outbound streaming bridge     │
│   tmux pane tail (in-progress text)──► │  (talks to Feishu     │ ───► message grows in place    │
│                                        │   directly, rich fmt) │      send → update×N → finalize │
│                                        └───────────────────────┘                               │
│                                                                                                │
│   ── supervision / self-heal ────────────────────────────────────────────────────────────     │
│   copilot_keeper.py     keeps tmux (resume) + stream poller alive                              │
│   daemon_watchdog.py    cron */2 — detects & recovers daemon inbound-wedge (deadlock)          │
│   cron */3  /  launchd  respawns the keeper if it dies (Linux=cron, macOS=launchd)             │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Full round-trip of a single message

```
  you type in Feishu:  "how is the mining doing?"
        │
        ▼  Feishu event webhook
   ┌─────────┐
   │  Relay  │  which machine owns intern_quant? → push to that daemon
   └────┬────┘
        │ WS
        ▼
   feishu_daemon._handle_relay_message
        │  ① _set_pending_supervisor_origin  (mark: this came from the owner)
        │  ② _send_to_claude_tmux → tmux send-keys types it into the pane + Enter
        ▼
   ┌──────────────────────────────────────┐
   │  copilot pane                        │  Copilot thinks → runs tools → composes reply
   │  each step appended to events.jsonl  │
   └───────┬──────────────────────────────┘
           │  a new turn appears
           ▼
   copilot_stream.py notices it → gets a Feishu tenant token
           │  send(first segment → message_id)
           │  update × N   ← edits the SAME message every ~1s, it grows smoothly "⏳…"
           │  finalize     ← after idle, drop the spinner, ✅
           ▼
   ┌─────────┐
   │ Feishu  │  one message grows from tens to hundreds of chars, with **bold**,
   │  chat   │  per-tool ✅, and a cost footer
   └─────────┘
```

**Inbound and outbound are two independent paths** (a key design point):

- **Inbound**: `Relay → daemon → tmux send-keys` (types your text into the pane).
- **Outbound**: `events.jsonl + pane → copilot_stream.py → Feishu directly`
  (does **not** go through the daemon).

That is exactly why, when the daemon's inbound handler once deadlocked, the intern
**kept working and kept streaming out**, but new inbound messages couldn't get in —
the two paths are decoupled. (This is what `daemon_watchdog.py` now guards.)

---

## 4. Clever design decisions

| Design | Why |
|---|---|
| **Register as `claude-type + external_managed`** | Reuse the daemon's mature claude path (send-keys, liveness), but patch it to **detect but never take over**: `_is_claude_process_running` treats a `copilot` child as "online", and the restart loop skips it, so it never respawns a claude CLI over `cop_*`. |
| **`copilot --resume=<SID>`** | Copilot CLI idle-shuts-down after ~7 min; the keeper loop resumes the same SID within seconds, so **context is never lost**. |
| **Outbound talks to Feishu directly (bypasses the daemon)** | Like the claude/codex hooks, it calls the Feishu rich-text API directly so it can do **send → update×N → finalize** in-place editing (the daemon's plain `/send` can't stream). |
| **pane tail for near-char streaming** | `events.jsonl` only has *committed* segments (no token-level deltas), so the bridge also tails the tmux pane for the "currently typing" text. Feishu caps edits at ~17/message, so it is "as smooth as Feishu allows", not literally per-character. |
| **Force-refresh tenant token** | Feishu tenant tokens (~7200 s) can be invalidated early server-side; `token(force=True)` + retry-on-401 avoids a dead-token failure loop. |

---

## 5. Three layers of resilience (self-heal)

```
  Layer 1  copilot_keeper.py    ──► tmux died? resume it. stream poller died? restart it.
  Layer 2  cron */3 (Linux)     ──► keeper process itself died? bring the whole thing back.
           launchd (macOS)      ──► same (macOS has no setsid; needs launchd + explicit PATH).
  Layer 3  daemon_watchdog.py   ──► daemon inbound wedge/death? SIGUSR1 thread-dump + restart.
           cron */2
```

**Wedge signature detected by Layer 3:** a trailing
`[RELAY_CLIENT] Feishu msg for …` log line (atts=0, non-empty text) with **no**
follow-up `TMUX_SEND` for > 90 s. On hit it sends `SIGUSR1` (faulthandler dumps
all thread stacks for root-cause), then TERM/KILL + relaunch with the correct env.

---

## 6. Repo map

```
intern-copilot-bridge/
├── copilot-bridge/
│   ├── copilot_stream.py     ← outbound streaming bridge (the core, ~660 lines)
│   ├── copilot_keeper.py     ← supervisor (tmux + poller)
│   ├── daemon_watchdog.py    ← inbound-wedge self-heal
│   ├── copilot_env.sh        ← node path + token
│   ├── convert_intern.py     ← convert a claude-type intern → copilot-CLI intern
│   ├── claude_handoff.py     ← extract a claude transcript into a handoff doc
│   ├── patch_*.py            ← daemon patches (copilot liveness / no-respawn)
│   └── vendor/feishu_api.py  ← Feishu rich-text API
├── intern-agent-cli/         ← the underlying intern-agent CLI (daemon / relay / hooks)
├── docs/
│   ├── ARCHITECTURE.md       ← full evolution log (migration / streaming / compact / token)
│   └── OVERVIEW.md           ← this file
└── README.md
```

---

## 7. Glossary

| Term | Meaning |
|---|---|
| **intern** | one managed Copilot-CLI session bound to one Feishu chat |
| **Relay** | public WebSocket/HTTP server bridging Feishu ⇄ per-machine daemons |
| **daemon** (`feishu_daemon.py`) | per-machine process: routes inbound, tracks online state |
| **keeper** (`copilot_keeper.py`) | supervisor that keeps the tmux session + stream poller alive |
| **stream** (`copilot_stream.py`) | outbound bridge: session log + pane → Feishu, streaming |
| **watchdog** (`daemon_watchdog.py`) | self-heal for a wedged/dead daemon |
| **SID** | Copilot session id used with `--resume` to preserve context |
| **external_managed** | registration flag: daemon sees it online but never respawns it |
