---
name: internctl-operations
description: "Use internctl for supported intern runtime operations: list, inspect, create, delete, session lifecycle, group settings, helper runtime checks, and team helpers. Always scope intern operations by project when available, and never hand-create intern metadata or runtime directories."
---

# internctl-operations

Use `internctl` for intern lifecycle and runtime maintenance. These operations
update multiple product-owned surfaces: state-v1 registry, hook state, metadata,
runtime directories, cloned repos, tmux sessions, daemon notifications, Feishu
groups, and rollback cleanup. Do not hand-create or hand-delete
`.intern_workspace/interns/<name>`, task runtime directories, `.hook_state.json`,
or session registry entries as a substitute for these commands.

## Scope Rule

The authoritative target is `(project, internName)`. Same intern names can exist
across projects, so pass `--project <PROJECT>` whenever the command accepts it
and the project is known. If the user supplied a project in the current request
or additional context, use that exact value.

## Inspect

```bash
internctl list --json
internctl status <name> --project <project> --json
internctl session status <name> --project <project> --json
```

Use `list --json` before destructive or ambiguous work. Use `status --json` to
confirm role, type, project, task, and current status before deciding the next
operation.

## Create

```bash
internctl create <name> \
  --project <project> \
  --type <codex|claude|copilot> \
  --role <independent|coordinator|team_lead|worker>
```

Creating an intern must use `internctl create`. It is the supported path for
real intern creation, including metadata, runtime setup, clone, hook state,
registry updates, Feishu group setup, daemon refresh, and rollback on failure.

Common additions:

```bash
internctl create <name> --project <project> --type codex --role independent
internctl create <name> --project <project> --type codex --role team_lead --team-name <team>
internctl create <name> --project <project> --type codex --role worker --team-name <team>
internctl create <name> --project <project> --type codex --role coordinator --coordinator-id <coordinator_id>
```

## Session Lifecycle

```bash
internctl session start <name> --project <project> --type <codex|claude> --no-attach
internctl session status <name> --project <project> --json
internctl session resume <name> --project <project> --type <codex|claude> --json
internctl session restart <name> --project <project> --type <codex|claude> --no-attach
internctl session stop <name> --project <project>
```

Use `restart` for the normal GUI-equivalent "restart intern" action. Use
`resume` when you specifically need to re-enter an existing managed session and
inspect its JSON result. Use `stop` only when the user requested a stop or when
it is part of an explicit maintenance flow.

## Delete

```bash
internctl delete <name> --project <project> --confirm
```

Before delete, run `internctl status <name> --project <project> --json`. Do not
delete a Working intern unless the user explicitly asked for that specific
intern to be deleted or replaced. `--force` is only for explicit
recovery/rollback cases, such as cleaning a known failed create, and should be
named in the user-facing explanation:

```bash
internctl delete <name> --project <project> --confirm --force
```

## Group Settings

Use group commands for the target intern's Feishu group settings:

```bash
internctl group trigger-mode <name> --project <project> --mode <all|at_only> --json
internctl group detail-mode <name> --project <project> --mode <full|summary> --json
```

These commands talk to the local daemon. If they fail because the daemon address
is unavailable, report that and start or diagnose the daemon through supported
commands instead of editing relay or registry files directly.

## Machine Helper

Machine helper commands are machine-scoped; they do not take `--project`.

```bash
internctl helper status --json
internctl helper start --issue "<short issue>" --json
internctl helper stop --json
internctl helper invite-owner --issue "<short issue>" --json
internctl helper migrate <host:port> --json
```

Use these for local helper runtime smoke checks, owner-assist requests, and
machine migration prompts. Do not infer a business-project intern from helper
state unless a separate `internctl list/status` result proves the target.

## Team Helpers

When managing workspace teams, prefer the supported team commands:

```bash
internctl team list --project <project> --json
internctl team status <team_id> --project <project> --json
internctl team create <team_id> --project <project> --worker-count <n> --type <codex|claude|copilot>
internctl team assign-worker-task <team_id> <worker_name> \
  --project <project> \
  --task-id <task_id> \
  --title "<title>" \
  --background "<background>" \
  --goal "<goal>" \
  --acceptance "<acceptance>"
internctl team delete <team_id> --project <project> --confirm
```

Team delete follows the same destructive safeguards as intern delete. Use
`--force` only for explicit recovery/rollback.
