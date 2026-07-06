# peer_send — intern 之间点对点对话

A intern 向本机 daemon 的 `/api/intern/peer/send` 发文本给 B intern；daemon 同步返回目标 transport/queue acceptance 回执。

## 请求体

```json
{
  "from_intern_name": "<your_name>",
  "to_intern_name":   "<peer_name>",
  "to_project":       "<peer_proj>",     // 可选；省略时由 relay 解析，多候选返 ambiguous_target+candidates 让你选
  "mode":             "default",         // 必填；default|next|stop
  "content":          "...",             // ≤4KB；"/esc" 是特殊命令打断 B 当前 turn
  "attachments": [                          // 可选；仅 cli intern 收件支持
    {"kind": "image|file", "filename": "...", "bytes_b64": "..."}
  ]
}
```

## mode

- `default`: 对 Claude/Codex 目标先进入目标 daemon 的 per-target queue；队列会按目标串行注入 tmux。目标 busy 时，default 可以排在 next 前进入 pending input。
- `next`: 对 Claude/Codex 目标先进入同一个 per-target queue；队列只在目标 idle 时注入 next，不打断当前 turn。
- `stop`: 对 Claude/Codex 目标进入高优先级 control lane；`content` 可为空。Copilot 暂不支持。

## role contract

- `independent` 只能和 `independent` 互发 peer send；`independent` 与 team 三角色互发会被拒绝，返回 `team_only_accepts_supervisor_tasks_via_coordinator`，message 为 `team只允许coordinator从主管接受任务`。
- `coordinator -> team_lead` 允许 `default`、`next`、`stop`。
- `team_lead -> coordinator` 只允许 `default`。
- `team_lead -> worker` 允许 `default`、`next`、`stop`；分配新实现任务时，team_lead 必须先创建当前 workspace metadata 的 `.intern_workspace/tasks/<task_id>/` 标准 task 文档，再用 peer send 通知 worker 接受该 task。
- `worker -> team_lead` 不走 peer send，返回 `worker_to_team_lead_use_mailbox`。
- `coordinator -> worker`、`worker -> coordinator`、team role 同角色之间会被拒绝。

## 响应

- `{"status": "delivered", "kind": "queued|stop|esc", "msg_id": "...", "queue_depth": N}`：目标 daemon 已接受该 job。对 Claude/Codex 文本消息，`queued` 表示进入目标 per-target queue，不表示已经写入 tmux。
- `{"status": "delivered"}`：Copilot 目标已推给当前 active 的 VS Code window。
- `{"status": "undeliverable", "reason": "<X>"}`，X ∈ `offline` / `tmux_session_missing` / `session_not_running` / `queue_full` / `attachment_persist_failed` / `unknown_target` / `ambiguous_target`（附 candidates）/ `unsupported_target` / `unsupported_mode` / `unsupported_attachment_target` / `relay_unreachable` / `source_outdated`（发送方 daemon 太旧，跨机请求没有 mode 或 role contract 字段，需要升级）/ `target_outdated`（接收方机器插件太旧，不支持 peer、peer mode 或 role contract；daemon 会给主管发飞书提示升级）/ `team_only_accepts_supervisor_tasks_via_coordinator` / `worker_to_team_lead_use_mailbox` / `coordinator_to_worker_use_team_lead` / `worker_to_coordinator_use_team_lead` / `same_role_team_channel_not_supported` / `unsupported_mode_for_team` / `not_same_team` / `role_not_allowed`
- 对 `offline` / `tmux_session_missing` / `session_not_running`，响应会附加 `message` 和 `remediation`。若目标与发送方同机，`remediation.action=restart_session_via_daemon`，发送方可调用本机 daemon/session restart 能力尝试重启目标 session 后重试；若不同机，`remediation.action=notify_supervisor`，发送方应通知主管协助在目标机器修复。
- HTTP 400 — `invalid_from` / `content_empty` / `content_too_long` / `self_send` / `missing_field` / `invalid_mode`

## 注意

- `delivered` 只表示消息到达目标 daemon 的 transport/queue 边界，不表示目标 LLM 已经读完、开始处理或会回复。
- 对 Claude/Codex tmux intern，`delivered/kind=queued` 表示目标 daemon 已把 job 放入该 intern 的串行队列；队列会批量拼接当前可运行消息后注入 tmux。不要因为短时间没有回复就重复发送同一内容。
- 队列可能把多条 peer 消息合并为一个 batch 注入给目标 intern；目标会看到来源、顺序和每条 `msg_id`。
- Codex transcript/pane ack 是目标 daemon worker 的后台诊断，不进入本接口同步响应。
- 对 Copilot intern，`delivered` 表示消息已推给当前 active 的 VS Code window。
- B 处理完**可能**反向调同接口给你发回复。
