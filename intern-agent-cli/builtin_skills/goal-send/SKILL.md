---
name: goal-send
description: "Set or cancel another intern's pressing goal through the local daemon goal API. Use when a permitted coordinator or independent intern needs to drive a target tmux intern's active goal. Discover the daemon http_port from FEISHU_DAEMON_ADDR_FILE or /tmp/feishu_daemon.json and verify delivered vs undeliverable responses."
---

# goal-send

Use the local daemon goal API to set or cancel a target intern's pressing goal.
This channel is separate from ordinary peer-send text.

For coordinator-to-team_lead task assignment, do not put the full request only
in a goal. First send the concrete request with `peer-send`, then use this
goal API to press the team lead to complete that already-sent request.

## Endpoint Discovery

Read the daemon address JSON from `${FEISHU_DAEMON_ADDR_FILE}` if set, otherwise
`/tmp/feishu_daemon.json`, and use its `http_port`:

```bash
PORT="$(python3 - <<'PY'
import json, os
path = os.environ.get("FEISHU_DAEMON_ADDR_FILE") or "/tmp/feishu_daemon.json"
with open(path, "r", encoding="utf-8") as f:
    print(json.load(f)["http_port"])
PY
)"
```

## Set Goal

```json
POST /api/intern/goal/set
{
  "from_intern_name": "<your_name>",
  "to_intern_name": "<target_name>",
  "to_project": "<target_project>",
  "content": "goal text",
  "client_goal_id": "optional-stable-id"
}
```

`content` is required for set. The daemon clears the target's previous goal,
sets the new one, and returns only after its confirmation window succeeds or
fails.

## Cancel Goal

```json
POST /api/intern/goal/cancel
{
  "from_intern_name": "<your_name>",
  "to_intern_name": "<target_name>",
  "to_project": "<target_project>"
}
```

`content` is optional for cancel.

## Boundaries

- `coordinator -> team_lead` goal set/cancel is allowed.
- For coordinator task assignment, goal is a follow-up pressure mechanism after
  the concrete request has been delivered by `peer-send`.
- `independent -> independent` is allowed only for same-project, same-daemon
  tmux targets.
- Copilot and non-tmux targets return `unsupported_target`.
- Independent interns cannot set goals for team-role interns, and workers do
  not use this API to control team leads.

## Response Handling

Check both HTTP status and JSON body. `status=delivered` means the daemon
confirmed the goal command reached the target tmux session. `status=undeliverable`
uses a non-2xx status and a stable `reason`, such as `ambiguous_target`,
`session_not_running`, `target_outdated`, or `unconfirmed`.
