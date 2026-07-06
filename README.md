# intern-copilot-bridge

Tooling to run **GitHub Copilot CLI** as a managed "intern" agent that talks to
a Feishu (Lark) group in real time — plus the underlying **intern-agent CLI**
(daemon / relay / hooks) it builds on.

This lets you drive a Copilot-CLI session from a Feishu chat: send it messages,
watch its replies stream back token-by-token with rich formatting, tool
progress, cost footer, `/compact` feedback, and long-reply continuation.

> 📖 **New here? Read [`docs/OVERVIEW.md`](docs/OVERVIEW.md)** — a friendly
> end-to-end tour of the whole system with ASCII diagrams.

## Layout

```
copilot-bridge/            # the Copilot ⇄ Feishu bridge (self-contained)
  copilot_stream.py        # streaming outbound bridge (events.jsonl + tmux pane → Feishu)
  copilot_keeper.py        # supervisor: keeps tmux session + stream poller alive
  daemon_watchdog.py       # self-heal: detects daemon inbound-wedge (deadlock) via
                           #   log signature, SIGUSR1 thread-dump + auto-restart (cron)
  copilot_env.sh           # cross-platform env (node path, token, allow-all)
  convert_intern.py        # convert a claude-type intern to a copilot-CLI intern
  claude_handoff.py        # extract a claude .jsonl transcript → HANDOFF markdown
  patch_*.py               # daemon patches (copilot liveness + no-respawn)
  vendor/feishu_api.py     # Feishu post/image/card API (rich markdown rendering)
  legacy/copilot_bridge.py # v1 (non-streaming), kept for reference

intern-agent-cli/          # the intern-agent CLI this bridge plugs into
  scripts/daemon/          # local daemon (routes Feishu ⇄ intern)
  scripts/relay/           # relay client/server
  commands/, lib/, ...     # internctl subcommands + libraries
```

## How the bridge works

- A `tmux` session runs `copilot --resume=<SID> --allow-all` under a keeper loop
  (Copilot idle-shuts-down after ~7 min → the loop auto-resumes).
- **Inbound** (Feishu → Copilot): the daemon types your message into the pane.
- **Outbound** (Copilot → Feishu): `copilot_stream.py` tails the session
  `events.jsonl` (committed segments) and the tmux pane (in-progress text), and
  streams to Feishu via `send → repeated update → finalize` on one message.
- Feishu caps edits at ~17/message, so growth is a smooth ~1s cadence with
  automatic rollover to continuation messages for long replies.
- **Inbound self-heal** (`daemon_watchdog.py`, run from cron every ~2 min): the
  daemon's relay message handler can wedge on a rare pathological inbound message
  (deadlock), silently killing Feishu→intern delivery while the CLI keeps running
  and streaming out. The watchdog detects the signature (a trailing
  `[RELAY_CLIENT] Feishu msg for …` log line with no follow-up `TMUX_SEND` for
  >90 s), captures a `SIGUSR1` faulthandler thread-dump for root-cause, then
  restarts the daemon with the correct env. It also restarts a dead daemon, and
  self-discovers pid/log/script from the daemon's pidfile + `/proc`.

Registered as a claude-type intern with `external_managed: true` so the daemon
detects it as online (via a `copilot` child-process check) but never respawns a
provider CLI over it.

## Configuration (no secrets committed)

All credentials/infra are read from the environment or local runtime config that
is **git-ignored** (`enterprise_policy/`, `.feishu_registry/`, `.copilot_token`,
`policy.json`, `*.env`, `state/`, …). Notable env vars the CLI reads:

| Env var | Purpose |
|---|---|
| `COPILOT_GITHUB_TOKEN` | Copilot auth on headless boxes (macOS uses keychain) |
| `INTERN_OWNER_MOBILE` | Feishu owner mobile for owner-notify |
| `CI_FEISHU_APP_ID` / `CI_FEISHU_APP_SECRET` | Feishu app creds (CI) |
| `CODEX_LB_BASE_URL` / `CODEX_LB_API_KEY` | Codex load-balancer (optional) |
| `CLAUDE_BASE_URL` | Anthropic-compatible gateway (optional) |
| `CI_DEBUG_HOST_A` / `CI_DEBUG_HOST_B` / `CI_SSH_PROXY_HOST` | CI infra |

## Quick start (bridge)

```bash
# 1. deps: Node + Copilot CLI
npm i -g @github/copilot

# 2. env
source copilot-bridge/copilot_env.sh   # picks up ~/.local/node-*/bin + token

# 3. register an intern in .intern_sessions.json (type=claude, external_managed,
#    provider=copilot, tmux_session=cop_<name>) and drop its Feishu chat id in
#    .feishu_registry/<project>__<intern>.json

# 4. run the keeper (starts the tmux copilot session + stream poller)
python3 copilot-bridge/copilot_keeper.py
```

## Notes

- `intern-agent-cli/` is vendored from the intern-agent-helper VS Code extension
  bundle; `vendor/feishu_api.py` likewise. All hardcoded secrets/PII/internal
  hosts have been replaced with environment-variable reads.
- This repo intentionally contains **no** tokens, app secrets, phone numbers, or
  internal IPs. See `.gitignore` for the runtime paths that must never be
  committed.
