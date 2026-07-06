---
name: feishu-messaging
description: "Send local images or files out to the supervisor Feishu group, or list that group's members. Use when the user asks to send/post/share a screenshot, chart, report, plan, design, data file, or attachment to Feishu. Pass --intern-name and --project exactly from the current additionalContext; credentials come from enterprise policy/secrets."
---

# feishu-messaging

Send local artifacts to the current intern's supervisor Feishu group, or list
members of that group. This skill is for outbound files/images and diagnostics,
not ordinary plain-text chat and not Feishu doc editing.

## Call Contract

Always pass both values exactly from the current turn's additionalContext:

```bash
--intern-name <NAME>
--project <PROJECT>
```

Do not infer either value from cwd, environment variables, file paths, repo
names, or the supervisor's wording. Feishu registry lookup is scoped by
`(project, intern_name)` so same-named interns in other projects do not receive
the artifact.

## Commands

```bash
python3 "{{CLI_ROOT}}/builtin/feishu_messaging/send_image.py" \
  --intern-name <NAME> \
  --project <PROJECT> \
  --file /tmp/chart.png \
  --msg "short note"

python3 "{{CLI_ROOT}}/builtin/feishu_messaging/send_file.py" \
  --intern-name <NAME> \
  --project <PROJECT> \
  --file ./plan.md \
  --msg "short note"

python3 "{{CLI_ROOT}}/builtin/feishu_messaging/list_chat_members.py" \
  --intern-name <NAME> \
  --project <PROJECT>
```

Images support PNG, JPG, JPEG, GIF, BMP, and WEBP up to 10 MB. Files support any
type up to 30 MB. `list_chat_members.py` writes JSON to stdout.

## Credentials And Registry

Credentials come from enterprise policy and secrets:

- `enterprise_policy/daemon/policy.json` with `feishu.app_id`
- `feishu.app_secret` from daemon policy or `enterprise_policy/relay/secrets.json`

Do not create or read `key.txt`, and do not ask the user for raw `oc_*` or
`ou_*` IDs. Missing policy, missing secrets, invalid permissions, or missing
project-scoped registry entries are configuration errors to report with command
output.
