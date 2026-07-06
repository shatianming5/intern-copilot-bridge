---
name: peer-send
description: "Send a text message or control request to another intern through the local daemon peer-send API. Use for intern-to-intern coordination, queued next-turn delivery, stop/esc control, or replies to peer batches. Discover the daemon http_port from FEISHU_DAEMON_ADDR_FILE or /tmp/feishu_daemon.json and respect role/team boundaries."
---

# peer-send

Use the local daemon `POST /api/intern/peer/send` endpoint to send text to
another intern. This is for intern-to-intern coordination, not for supervisor
Feishu artifact uploads.

For coordinator-to-team_lead task assignment, use peer-send first to deliver
the concrete request. If sustained pressure is needed, set a separate goal only
after the request has been sent.

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

## Request

```json
{
  "from_intern_name": "<your_name>",
  "to_intern_name": "<peer_name>",
  "to_project": "<peer_project>",
  "mode": "default",
  "content": "message text",
  "attachments": []
}
```

`mode` is required and must be one of:

- `default`: queue normal text for the target intern.
- `next`: queue text for the target's next idle turn.
- `stop`: high-priority control lane. `content` may be empty.

`to_project` may be omitted only when the target name is unambiguous. If the
daemon returns `ambiguous_target`, retry with the chosen `to_project`.

## Role Boundaries

- `independent -> independent` is allowed.
- `coordinator -> team_lead` is allowed.
- Coordinator task assignment to a team lead should be carried by this message;
  a later goal may press completion but should not replace the request body.
- `team_lead -> coordinator` is allowed only with `default`.
- `team_lead -> worker` is allowed.
- Workers should use their mailbox path instead of peer-send to contact a
  team lead.
- Independent and team-role interns cannot bypass coordinator/team rules.

## Response Handling

Check both HTTP status and JSON body. `status=delivered` means the target daemon
accepted the message or control job; it does not mean the target LLM has read or
completed the work. For `status=undeliverable`, inspect `reason`, `message`, and
`remediation` before retrying.
