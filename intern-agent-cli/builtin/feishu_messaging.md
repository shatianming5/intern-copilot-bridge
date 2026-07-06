# Feishu messaging - send local artifacts to the supervisor group

Use these bundled scripts when the supervisor asks you to send a local image,
report, plan, design document, chart, data file, or other local artifact to the
current intern's Feishu group. This builtin only sends images/files, optional
short notes, and group member diagnostics. Do not use it for ordinary plain-text
chat.

## Call Contract

Always pass both values exactly as shown in the current turn's additionalContext:

```bash
--intern-name <NAME>
--project <PROJECT>
```

Do not infer either value from the current directory, environment variables, file
paths, repo names, or the supervisor's phrasing. The registry lookup is scoped by
`(project, intern_name)` so same-named interns in other projects do not receive
the artifact.

## Commands

Replace `<BUNDLE>` with the bundled CLI directory shown in this prompt path.

```bash
python3 <BUNDLE>/builtin/feishu_messaging/send_image.py \
  --intern-name intern_feature_worker3 \
  --project axis_intern_agents_backup \
  --file /tmp/chart.png \
  --msg "loss curve"

python3 <BUNDLE>/builtin/feishu_messaging/send_file.py \
  --intern-name intern_feature_worker3 \
  --project axis_intern_agents_backup \
  --file ./plan.md \
  --msg "implementation plan"

python3 <BUNDLE>/builtin/feishu_messaging/list_chat_members.py \
  --intern-name intern_feature_worker3 \
  --project axis_intern_agents_backup
```

Images support PNG/JPG/JPEG/GIF/BMP/WEBP up to 10 MB. Files support any type up
to 30 MB. `list_chat_members.py` writes JSON to stdout.

## Credentials And Registry

Credentials come from enterprise policy and secret files:

- `enterprise_policy/daemon/policy.json` with `feishu.app_id`
- `feishu.app_secret` from daemon policy or `enterprise_policy/relay/secrets.json`

Do not create or read `key.txt`, and do not ask the user for raw `oc_*` or
`ou_*` IDs. Missing policy, missing secrets, invalid permissions, or missing
project-scoped registry entries are configuration errors that should be reported
with the command output.
