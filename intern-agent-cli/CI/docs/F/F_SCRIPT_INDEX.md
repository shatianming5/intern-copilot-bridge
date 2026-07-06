# F 剧本第一批总览

<!-- METADATA:STATUS=InProgress,OWNER=intern_ci_lead,SESSION=62 -->

本文是 F 阶段剧本总览。F 只验证插件、CLI、GUI handler、daemon、relay 和飞书 slash/API 的功能正确性，不要求 intern/agent 完成业务任务，不用自然语言 prompt 驱动 agent 做代码改动。

## 落点规则

1. 剧本文档放在 `intern-cli/CI/docs/F/`。
2. 主管 review 通过后，worker 的实现 case 放在 `intern-cli/CI/cases/F/`。
3. 旧 `intern-cli/CI/case_slots` 目录已移除；registry 只从 `intern-cli/CI/cases/F` 和 `intern-cli/CI/cases/J` 发现 F/J。
4. 每个 case 必须先通过 deploy gate；case 初始化时只 reset 本 case namespace，case 结束后保留现场。
5. 每个 worker 任务必须实现剧本里的全部 action、wait、assert，并在 `debug-a` 或 `debug-b` 远端测试通过后申请 review。
6. GUI 是 CLI 的壳。凡是 GUI 操作不能被对应 CLI 精确表达，应作为 bug 写入 report，不能在 CI 里绕过。

## 剧本格式

每个 F 剧本的 `## 剧本` 段都使用 `# / 类型 / 操作 / 注释` 表格。`操作` 列保留 worker 后续要实现或调用的 action/wait/assert DSL；`注释` 列说明该步骤模拟什么用户或系统行为、等待什么状态、断言什么产品合约。

## 第一批用例表

| ID | 分组 | 目标 | 建议分包 |
|----|------|------|----------|
| `F_0001` | workspace/GUI | Codeup `repo_dotdir` workspace add/remove | workspace action 包 |
| `F_0002` | workspace/GUI | Codeup `metadata_branch` workspace add/remove | workspace action 包 |
| `F_0003` | workspace/GUI | GitHub workspace add/remove | workspace action 包 |
| `F_0004` | workspace/GUI | local workspace add/remove | workspace action 包 |
| `F_0005` | workspace/GUI | duplicate/invalid workspace add rollback | workspace action 包 |
| `F_0006` | workspace/GUI | workspace mode switch removed contract | workspace action 包 |
| `F_0007` | intern/session | intern create/status GUI/CLI equivalence | intern action 包 |
| `F_0008` | intern/session | duplicate/invalid intern create rollback | intern action 包 |
| `F_0009` | intern/session | Codex session start/restart startup lifecycle | intern action 包 |
| `F_0010` | intern/session | intern delete and force delete guard | intern action 包 |
| `F_0011` | daemon API | daemon status/readiness API | daemon/relay API 包 |
| `F_0012` | daemon API | daemon group proxy and registry mutation | daemon/relay API 包 |
| `F_0013` | relay API | relay chat create/delete project scope | daemon/relay API 包 |
| `F_0014` | moved | moved to `J_0014_peer_send_routing_error_contract` because it starts live intern sessions and checks receiver pane visibility | J 包 |
| `F_0015` | relay/API | question card callback autofill and cleanup | daemon/relay API 包 |
| `F_0016` | slash | `/config` trigger/detail/no-collapse mode persistence | slash/helper 包 |
| `F_0017` | slash/helper | `/helper` open/start/status | slash/helper 包 |
| `F_0018` | slash/helper | helper machine selection, detailed mode, stop | slash/helper 包 |
| `F_0019` | slash/main bot | main bot readonly `/status`/`/list`/`/debug` | slash/helper 包 |
| `F_0020` | slash/error | slash routing errors, RBAC, unknown fallback | slash/helper 包 |

## 第二批 GUI 用例表

| ID | 分组 | 目标 | 建议分包 |
|----|------|------|----------|
| `F_0021` | workspace/GUI | workspace disable vs delete/stop-maintain contract | task402 / worker5 |
| `F_0022` | workspace/GUI | workspace enable/re-enable/doctor and TreeView refresh | task402 / worker5 |
| `F_0023` | task/GUI | task grouping, README open, tooltip, PR formatting and metadata parse boundary | task403 / worker6 |
| `F_0024` | task/GUI | task delete item/QuickPick/cancel/InProgress/no-task contract | task403 / worker6 |

## 第三批 TreeView GUI 用例表

主管已批准 TreeView proposal，但明确排除两块：不做 Codex 之外的 intern；不做 Team 功能。以下用例均按该 scope 写成正式剧本。

| ID | 分组 | 目标 | 建议分包 |
|----|------|------|----------|
| `F_0025` | intern/GUI | Codex intern TreeView projection、online/offline、active focus、same-name project scope | task404 / pending task399 dependency |
| `F_0026` | intern/GUI | Codex active intern、status bar、session map、Open Chat routing | task404 / pending task399 dependency |
| `F_0027` | intern/session GUI | Codex session context command、LB prerequisite、failure rollback | task404 / pending task399 dependency |
| `F_0028` | intern/group GUI | Codex Feishu group trigger/detail mode context menu 与 CLI/group config 同步 | task404 / pending task399 dependency |
| `F_0029` | skill/GUI | skill source add/update/remove、TreeView projection、`SKILL.md` open | task405 / worker4 |
| `F_0030` | skill/GUI | Codex repo/personal skill enable/disable/promote/farm sync | task405 / worker4 |
| `F_0031` | config/status GUI | plugin meta、format-check toggle、language switch、refreshTree/statusbar health | task405 / worker4 |
| `F_0032` | menu GUI | TreeView contextValue/menu visibility audit，Codex/workspace/task/skill only | task405 / worker4 |

## 第四批 correction / restart / policy / ingress 用例表

主管已批准 correction 相关修改、`F_0033` 和 `F_0034`；`F_0041` 必须全面覆盖。明确不做：`F_0038` 无 driver，`F_0039` GUI 是 CLI 壳不单测 GUI，`F_0040` 归 J 除非能直接脚本判定，`F_0042` 不做。

| ID | 分组 | 目标 | 建议分包 |
|----|------|------|----------|
| `F_0033` | intern/session | Codex no-prompt `/exit` 提示命令形状与 restart startup；same-UUID resume 归 J | lead refine |
| `F_0034` | policy/session | policy sync 后 Idle Codex env 改变自动 restart，重放不重复 | task408 / worker5 |
| `F_0035` | config/card | `/config` Cancel no-mutation，Save 仍可用 | task407 / worker3 |
| `F_0036` | machine_config/card | `/machine_config` target resolution、Cancel、operator boundary、policy sync 外壳 | task407 / worker3 |
| `F_0037` | daemon/reconnect | 单机 daemon reconnect 后 registry/chat/policy resync | task408 / worker5 |
| `F_0041` | real Feishu ingress | real relay-local slash/card/main-bot/unmapped/RBAC coverage | task409 / worker4 |

## 第五批 Claude intern 补充用例表

主管 Session 62 追加要求：Claude intern 仍有真实用户在使用，F 测试需要补足 Claude 相关功能。早先 `task404`/`task405` 的 Codex-only 口径作为历史 scope 保留；从本批新增任务开始，Claude 纳入 F 覆盖。仍不做 Team；仍不通过自然语言询问 Claude 来判断 skill 能见度，这类归 J。

| ID | 分组 | 目标 | 建议分包 |
|----|------|------|----------|
| `F_0043` | claude/session | Claude intern create/status/group type/session start/restart/exit-hint resume uuid | task410 / worker1 |
| `F_0044` | claude/TreeView | Claude TreeView projection、project scope、create/restart command CLI parity | task411 / worker6 |
| `F_0045` | claude/skill/group | Claude `.claude/skills` farm、repo/personal skill、group trigger/detail mode parity | task411 / worker6 |

## 第六批 skill / builtin / reliability 候选用例表

Session 6/7 docs review 从当前代码和历史任务补出以下 ReviewDraft。它们不触发 agent 自然语言工作，属于 F 候选；真实 agent 使用或跨 intern 委派对应由 J 覆盖。

| ID | 分组 | 目标 | 建议分包 |
|----|------|------|----------|
| `F_0054` | builtin/skill farm | builtin `peer-send`/`goal-send`/`feishu-messaging` default farm、TreeView hiding、protected mutation、tamper restore | pending review |
| `F_0055` | team/reliability | Team create Phase 2 failure rollback、ghost team cleanup、retry 和 force delete scope；早期 TreeView F scope 之外，需主管重新批准后实现 | pending review |

## GUI 覆盖矩阵

- `F_GUI_COVERAGE_MATRIX.md` 记录第一批从 VS Code GUI 角度已覆盖和缺失的功能面，并给出 `F_0021` 起的第二批 GUI F 剧本建议。
- `F_TREEVIEW_PROPOSAL.md` 记录 TreeView 维度的后续 F proposal。Session 11 起，setup 相关不做 F 剧本；Session 15 起，Codex 之外 intern 和 Team 功能也不做当前 F。

## 依赖和分发建议

- `F_0001`-`F_0006` 共用 workspace create/delete/mode action，优先给同一个 worker。
- `F_0007`-`F_0010` 共用 intern/session registry/action，优先给同一个 worker。
- `F_0011`-`F_0013`、`F_0015` 共用 daemon/relay HTTP 和 registry assert，可给同一个 worker，或先合并 `F_0011` 的 status/readiness helper 后再分发后续 API case。`F_0014` 已迁到 `J_0014`，因为它启动 live intern sessions 并检查 receiver pane 可见性。`F_0015` 依赖 relay CI synthetic callback ingress；debug 部署中 `/api/ci/card_callback` 返回 403 时先修部署配置，不改成 local mock。
- `F_0016`-`F_0020` 共用 slash injection、message wait、mode persistence 和 RBAC assert，优先给同一个 worker。当前实现前置阻塞：已部署 CI synthetic Feishu endpoints 不能驱动真实 relay-local slash/card/main-bot handler；这组用例需要新增可部署的 relay CI driver，或经主管确认后降级为低层 API/local fixture 合约，不能直接用 `/api/ci/feishu_message`/`/api/ci/card_callback` 冒充真实 slash/card 路径。
- `F_0021`-`F_0022` 共用 workspace enable/disable/delete/TreeView projection action，优先给同一个 worker。
- `F_0023`-`F_0024` 共用 task metadata builder、TreeView task projection assert 和 `internal task-*` action，优先给同一个 worker。`F_0023` 中第 3 行 METADATA 仍是产品规则；当前 CLI 若接受第 4 行 METADATA，应记录 parser contract bug evidence。
- `F_0025`-`F_0028` 已创建 `task404_ci_f_codex_intern_treeview_followup`，但暂不 peer 分发：该包依赖 `task399` 的基础 Codex intern/session action，优先等 task399 合入或 lead 明确复用方案后分发。
- `F_0029`-`F_0032` 已创建 `task405_ci_f_skill_config_treeview_cases` 并分配给 worker4；该包与 `task401` 的 slash/helper CI driver blocker 无关，可并行推进。
- `F_0033` 与 `F_0009/F_0027` correction 共用 Codex session helper；F 内不发送业务 prompt，因此 fresh restart 可接受。Codex `/exit` 后执行提示命令并保持同一 session id 需要真实 agent turn，归 J。
- `F_0034` 与 `F_0037` 共用 daemon policy/reconnect helper，优先给 worker5。
- `F_0035`/`F_0036` 与 `F_0016/F_0017` correction 共用 relay card/helper source-driver，优先给 worker3。
- `F_0041` 是真实 Feishu ingress 覆盖，必须证明 driver 触达 relay-local handler branch；不能用 synthetic daemon injection 冒充。
- `F_0043`-`F_0045` 是 Claude intern 补充覆盖；`F_0043` 还必须覆盖 Claude `/exit` 后 `Resume this intern` 提示命令可执行并保持同一 uuid；如 Claude 运行需要 provider token，从 policy alias `sk-xiaohan.yi` 读取并 redacted，严禁把 secret 写入仓库或 report。
- `F_0054` 是 docs review 期间补出的 builtin skill F 候选，尚未分发实现；setup 和 Copilot intern 相关剧本不再补写。
- `F_0055` 是 docs review 期间按 Team create rollback 现场补出的 F 候选；它不要求 agent 自然语言工作，不替代 `J_0010` 的 team delegation journey，也不改变早期 TreeView F 包“不做 Team”的已批准 scope。
- 后续新 F/J 资源命名必须带 stage 前缀：`ci_f_00xx`、`intern_ci_f_00xx`、`task_ci_f_00xx`。已分配/已合并的 case 不在本批任务中重命名；等本批合并后由 lead 统一迁移已有命名。
- 如果拆给不同 worker，依赖前置 action/assertion 的 case 必须等前置 PR merge 后再分发。

## 共用通过标准

- report 必须记录 debug machine、case namespace、初始化 reset 结果、所有 action 输入、wait evidence、assert evidence、失败分类和现场路径。
- report 中的 action/wait/assert evidence 必须能对应回剧本每一步注释，不能只给最终 pass/fail。
- 失败不能被 cleanup 覆盖；case 结束保留 workspace/intern/group/session/registry 现场。
- 所有外部资源写入都必须带 case namespace，不能触碰主管当前工作环境。
