# CI J 类 action / wait / assert 函数说明

<!-- METADATA:STATUS=ReviewDraft,OWNER=intern_ci_lead,SESSION=79 -->

本文记录 CI 用例剧本中可引用的 action、wait 和 assert helper 语义。当前阶段先作为设计契约，worker 实现时应把新增 helper 走显式 registry 流程，不能依赖正则扫描。

## 基本规则

1. action 负责制造或清理测试条件，可以修改本 case 私有资源。
2. wait 只负责等待异步系统到达可判定状态，不做断言。
3. assert 只检查一个客观事实，不写主观描述。
4. 第 5 阶段 J 用例不允许 action 添加、删除、切换 workspace；workspace 必须由 lead 在测试前预置。
5. worker 只能创建和清理自己派生的 intern、task、session、tmux、branch、MR/PR、report 和临时目录。
6. workspace mode 的枚举值使用代码侧命名：`repo_dotdir`、`metadata_branch`、`local_only`。文档里的 “meta branch” 指 `metadata_branch`。

## action 函数

### `action_cleanup_case_namespace(case_id, worker_id, resources)`

清理当前 worker 派生的测试资源。

参数：

- `case_id`: 用例编号，例如 `J_0001`。
- `worker_id`: 执行 worker 标识。
- `resources`: 允许清理的资源类型列表，例如 `intern`、`task`、`session`、`branch`、`change_request`、`report`、`temp_dir`。

要求：

- 只能清理由 `case_id` 和 `worker_id` 派生命名的资源。
- 不得删除、不重建、不切换 lead 预置 workspace。
- 不得关闭 protected branch 保护规则。

### `action_record_baseline(workspace_id, target_branch)`

记录执行前基线。

产物：

- `before_rev`: target branch 当前 revision。
- `baseline_task_ids`: workspace 内已有 task id 集合。
- `workspace_record`: workspace provider、repo、mode、target branch。

### `action_create_task(task_id, workspace_id, title, body)`

通过 CLI 创建一个未分配 task。

要求：

- task 初始 assignee 必须为空。
- task body 必须写清业务目标、目标文件和期望文本。
- 该 action 不能分配 task。

### `action_create_intern(intern, workspace_id, backend)`

通过 CLI 创建测试 intern，并创建真实飞书 intern 群。

要求：

- `backend` 当前阶段只使用 `codex`。
- report 必须记录 intern name、chat_id、workspace_id、provider 和 backend。
- 创建 intern 不是本组 J_0001/J_0002 的被测重点，但必须作为现场证据保留。

### `action_start_intern_session(intern, workspace_id)`

启动测试 intern 的对话 session。

要求：

- session 必须绑定指定 workspace 和 intern。
- report 必须记录 session id、tmux target、启动时间和 readiness evidence。

### `action_send_feishu_prompt(chat_id, text)`

向真实 intern 飞书群发送一条用户 prompt。

产物：

- `message_id`: 飞书消息 id。
- `sent_at`: 发送时间。

要求：

- prompt 必须像真实用户，不暴露 case id、worker id、run id、workspace mode、provider 或测试断言。
- task assignment 只能通过该 action 发送的 prompt 触发。

### `action_send_slash_command(chat_id, text)`

向真实 intern 飞书群发送 slash 命令。

要求：

- `text` 必须是用户真实可输入的命令，例如 `/stop`、`/screenshot`。
- report 必须记录 message_id、sent_at、chat_id、intern 和 relay route evidence。
- slash 命令不应被当作普通 prompt 追加到 LLM 对话。

### `action_prepare_long_running_probe(workspace_id, probe_path, expected_tokens)`

在 lead 预置 local workspace 中准备可控的耗时 probe。

要求：

- 只允许写入本 case 专用路径。
- probe 必须能稳定运行足够久，让 `/stop` 在运行中命中。
- report 必须记录 probe 路径、内容 hash 和期望输出 token。
- 如果 workspace 已由 lead 预置好 probe，该 action 只校验 probe 存在和可执行。

### `action_click_card_button(chat_id, intern, question_id, answer)`

通过 CI Feishu mock 模拟用户点击卡片按钮。

要求：

- 用于真实用户可点击的卡片按钮路径。
- report 必须记录 `question_id`、`answer`、relay callback response、visible audit message id 和 daemon route evidence。
- 如果当前 runner 或 mock 缺少该 action，实现本 J 用例时必须补齐，不能删减按钮点击场景。

### `action_submit_card_form(chat_id, intern, question_id, form_value, question_keys)`

通过 CI Feishu mock 模拟用户提交卡片表单。

要求：

- `question_keys` 必须与卡片上的问题顺序一致。
- 单选值使用 `q_i_select`。
- 多选值使用 `q_i_multiselect`，值为列表。
- 自由输入使用 `q_i_input`。
- `form_value` 必须包含 `submit`。
- report 必须记录 form payload、relay callback response、visible audit message id 和 daemon route evidence。
- 如果当前 runner 或 mock 缺少单选、多选或自由输入字段支持，实现本 J 用例时必须补齐，不能删减表单场景。

### `action_collect_report(case_id, artifacts)`

收集用例报告和证据。

要求：

- report 至少包含 chat_id、prompt message_id、task id、assignment evidence、MR/PR evidence、target revision、状态变化、cleanup namespace 和失败分类。

### `action_workspace_migrate_mode(repo_url, target_mode, metadata_branch)`

创建 workspace metadata mode 迁移 PR/MR，用于替代旧的 in-place mode switch。

要求：

- 调用前必须确认同 repo 的 relay workspace record 已 delete globally。
- 必须调用 `internctl workspace migrate-mode --repo-url <repo> --target <repo_dotdir|metadata_branch>`。
- report 必须记录 command、target mode、migration branch、PR/MR base branch、migrated metadata files 和 PR/MR URL。
- 迁移 PR/MR 必须包含真实 `.intern_workspace` metadata 内容，不能只有 `workspace-mode-migration.json` marker。

### `action_readd_workspace_after_migration(repo_url, target_mode)`

在迁移 PR/MR merge 后，用目标 mode 重新添加 workspace。

要求：

- 通过 `internctl workspace create ... --mode <target_mode>` 和 `workspace enable` 建立本机维护关系。
- same repo + same mode 的已有 relay record 只能复用，不能创建第二条逻辑 workspace。
- report 必须记录 workspace id、metadata mode、metadata root 和 resolver 可读取的 task/status/knowledge/skill metadata evidence。

### `action_attempt_workspace_mode_change(workspace_id, from_mode, to_mode)`

尝试切换 workspace mode，返回 CLI/daemon 结果；用于 F 类拒绝路径。

要求：

- 该 action 不假设成功。
- report 必须保留 returncode、stdout/stderr 或 JSON payload。

## wait 函数

### `wait_for_feishu_message_delivered(chat_id, message_id, timeout_s)`

等待指定飞书消息进入已投递状态。

### `wait_for_status_meta(intern, status, task_id, timeout_s)`

等待 intern `status.md` 第三行 METADATA 达到指定 `STATUS` 和 `TASK`。

示例：

```text
wait_for_status_meta(intern, status="Working", task_id=task_id, timeout_s=120)
```

### `wait_for_turn_started(intern, task_id, timeout_s)`

等待当前 intern 对应 task 的 tmux/Codex turn 启动。

### `wait_for_session_turn_started(intern, after_message_id, timeout_s)`

等待非 task 对话的新一轮 tmux/Codex turn 启动。

### `wait_for_turn_finished(intern, task_id, timeout_s)`

等待当前 intern 对应 task 的 tmux/Codex turn 结束。后续 assert 默认发生在该 wait 之后，避免在 intern 仍工作到一半时检查最终状态。

### `wait_for_stop_applied(intern, turn_id, timeout_s)`

等待 `/stop` 已作用到指定 turn。

成功条件：

- 对应进程、stream 或 pending run 已停止。
- 飞书群出现可判定的 stop 成功反馈。
- intern 能接收新的 prompt。

### `wait_for_screenshot_image(chat_id, after_message_id, timeout_s)`

等待 `/screenshot` 在飞书群中产生图片消息。

产物：

- `image_message_id`
- `image_key`
- `mime_type`
- `size`

### `wait_for_change_request_open(provider, task_id, timeout_s)`

等待当前 task 对应的 Codeup MR 或 GitHub PR 创建并进入 open 状态。

### `wait_for_change_request_merged(provider, change_id, timeout_s)`

等待 Codeup MR 或 GitHub PR 进入 merged 状态。

### `wait_for_report_artifact(case_id, timeout_s)`

等待 runner 写出当前 case report。

### `wait_for_workspace_mode(workspace_id, metadata_mode, timeout_s)`

等待 workspace record 和 metadata resolver 均显示指定 mode。

### `wait_for_question_card(chat_id, intern, after_message_id, timeout_s)`

等待指定 intern 群在 `after_message_id` 之后出现一张待回答卡片。

产物：

- `question_id`
- `card_message_id`
- `question_keys`
- `card_schema`

### `wait_for_card_callback_applied(intern, question_id, timeout_s)`

等待卡片回调已写入 daemon pending question，并唤醒等待中的 intern turn。

### `wait_for_intern_reply_after(chat_id, after_message_id, timeout_s)`

等待 intern 在指定消息之后回复。

## assert 函数

### `assert_case_namespace_clean(case_id, worker_id)`

确认执行前不存在同命名空间残留的 intern、task、session、branch、MR/PR、report 或临时目录。

### `assert_task_unassigned(task_id)`

确认 task metadata 中 assignee 为空，history 中没有分配记录。

### `assert_task_resolvable(task_id, workspace_id, metadata_mode)`

确认 task 在指定 workspace 和 mode 下可读取，且 task id、title、assignee、status 没有因为 mode 切换丢失或错位。

### `assert_status_meta(intern, status, task_id)`

确认 intern `status.md` 第三行 METADATA 的 `STATUS` 和 `TASK` 与期望一致。

### `assert_session_ready(intern, workspace_id)`

确认 intern session 已可接收飞书消息，且绑定指定 workspace。

### `assert_long_running_probe_started(intern, turn_id, expected_token)`

确认耗时 probe 已在 intern turn 中启动，且输出或日志包含指定 token。

### `assert_stop_succeeded(intern, turn_id, stop_message_id)`

确认 `/stop` 成功停止指定 turn，并且 stop 反馈可追溯到 `stop_message_id`。

### `assert_session_accepts_prompt_after_stop(intern, previous_turn_id, new_turn_id)`

确认 `/stop` 后 intern 没有卡死，新的用户 prompt 能启动新的 turn。

### `assert_turn_input_contains(turn_id, required_text)`

确认指定 turn 的 LLM 输入、tmux 注入或 daemon route payload 包含指定用户文本。

### `assert_turn_input_excludes(turn_id, forbidden_texts)`

确认指定 turn 的 LLM 输入、tmux 注入或 daemon route payload 不包含指定文本。

用于 `/stop` 后继续场景时，至少排除被停止的完整长 prompt 和 full additional context 标识。

### `assert_continuation_after_stop(reply_message_id, expected_subject_tokens)`

确认 `/stop` 后的“继续”确实推进上一段工作，而不是回复“不知道继续什么”或重新完整解释上下文。

### `assert_workspace_scope(workspace_id, provider, repo_url, metadata_mode, target_branch)`

确认 workspace record 与 lead 预置资源声明一致。

### `assert_workspace_mode(workspace_id, metadata_mode)`

确认 workspace record、metadata resolver 和 intern/task 解析入口使用同一个 metadata mode。

### `assert_workspace_mode_migration_result(workspace_id, metadata_mode)`

确认 migration PR/MR merge 后重新添加的 workspace 使用目标 mode，且 resolver 能读取迁移前已有的 task/status/history/knowledge/skill metadata。

### `assert_workspace_mode_unchanged(workspace_id, metadata_mode)`

确认一次失败的 mode change 没有修改 workspace mode、metadata root 或 provider/repo 记录。

### `assert_assignment_source(task_id, intern, message_id)`

确认 task assignee 为指定 intern，且分配证据指向指定飞书 prompt message_id；不得存在 CLI assignment 记录。

### `assert_no_extra_task_created(workspace_id, baseline_task_ids)`

确认 prompt 没有误创建额外 task。

### `assert_same_task_session(intern, task_id, previous_session_id)`

确认当前运行仍属于同一个 intern、同一个 task 和同一个 session 线索。

### `assert_intern_group_preserved(intern, chat_id)`

确认 mode 切换后 intern 仍绑定同一个真实飞书群，且没有被误删、重建或换群。

### `assert_single_question_card(chat_id, after_message_id, card_message_id)`

确认指定 prompt 之后、卡片点击之前，只出现一张待回答卡片。

### `assert_card_button_options(card_message_id, labels)`

确认卡片包含指定按钮选项，且没有重复按钮。

### `assert_card_has_free_input(card_message_id, question_key)`

确认卡片对指定问题提供自由输入表单。

### `assert_card_form_questions(card_message_id, expected_questions)`

确认卡片表单的问题顺序、类型和选项符合预期。

`expected_questions` 示例：

```json
[
  {"key": "请选择一个优先级", "type": "single", "options": ["A", "B", "C"]},
  {"key": "请选择要处理的项", "type": "multi", "options": ["X", "Y", "Z"]}
]
```

### `assert_card_callback_success(callback_result, question_id)`

确认 CI mock card callback 成功路由到 daemon，且 visible audit message 已写入真实群。

### `assert_pending_question_answered(intern, question_id, expected_answer)`

确认 daemon pending question 已被指定答案回答，并且没有残留 pending。

`expected_answer` 可以是字符串，也可以是按 question key 组织的字典。

### `assert_intern_reply_contains(message_id, required_tokens, forbidden_tokens=None)`

确认 intern 回复包含指定客观 token；可选确认不包含不应出现的 token。

### `assert_screenshot_image_message(message_id, image_key, mime_type)`

确认飞书群消息是图片消息，包含可下载 image key，且 mime type 是图片类型。

### `assert_no_screenshot_text_only_failure(chat_id, after_message_id)`

确认 `/screenshot` 没有只返回文本错误、权限失败或空响应。

### `assert_change_request(provider, change_id, task_id, target_branch, state)`

确认 MR/PR 的 provider、task 关联、target branch 和状态匹配。

### `assert_change_request_diff_contains(change_id, file_path, expected_text)`

确认 MR/PR diff 中包含指定业务文件和期望文本。

### `assert_business_diff_excludes_metadata(change_id)`

确认 MR/PR diff 不包含 `.intern_workspace`、status、history、task_knowledge 等 metadata。

### `assert_target_revision_unchanged(target_branch, before_rev)`

确认 target branch revision 仍等于 `before_rev`。

### `assert_target_revision_advanced(target_branch, before_rev)`

确认 target branch revision 已不同于 `before_rev`。

### `assert_target_contains_expected_change(target_branch, file_path, expected_text)`

确认 target branch 指定文件包含期望业务文本。

### `assert_no_direct_push(provider, target_branch, before_rev, after_rev)`

确认 target branch 变化只能由 MR/PR merge event 解释，不能由 direct push 解释。

### `assert_merge_after_prompt(change_id, approval_message_id)`

确认 merge event 时间晚于授权 prompt 对应 message_id。

### `assert_merge_result(provider, change_id, target_branch, before_rev, file_path, expected_text)`

组合确认 MR/PR 已 merged、target branch revision 已前进、目标文件包含期望文本，且 provider evidence 一致。

### `assert_task_closeout(task_id, intern)`

确认 task metadata 已 Completed，intern status METADATA 为 Idle 且 TASK 为空。

### `assert_feishu_message_linked(chat_id, message_id, required_tokens)`

确认指定飞书消息包含客观 token，例如 task id、MR/PR URL、merge revision。

### `assert_no_askuser_merge_decision(chat_id, after_message_id)`

确认审批 merge 路径没有额外发 AskUser/request_user_input 卡片要求用户二次决定。

### `assert_mode_change_rejected(result, from_mode, to_mode, reason_kind)`

确认 mode change 返回失败，错误原因属于期望类别，且不是 traceback、timeout 或无结构错误。

建议 `reason_kind`：

- `local_mode_transition_forbidden`: `repo_dotdir`/`metadata_branch` 与 `local_only` 之间的转换被拒绝。

### `assert_report_contains_evidence(case_id, required_fields)`

确认 report 包含指定字段和证据链接。

### `assert_cleanup_result(case_id, worker_id, preserved_workspace_id)`

确认 cleanup 后本 worker 派生资源已清理，且指定预置 workspace 仍存在、provider/mode/target branch 未变化。
