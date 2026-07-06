# F GUI coverage matrix

<!-- METADATA:STATUS=InProgress,OWNER=intern_ci_lead,SESSION=15 -->

本文从 VS Code GUI 用户入口反推 F 阶段覆盖面。权威 GUI 入口来自 `vscode-extension/package.json` 的 commands/menus/views，以及 `vscode-extension/src/extension.ts`、`ui/internTree.ts`、`ui/treeCommandHandlers.ts`、`ui/internManager.ts` 的实际 handler。

Session 11 约定：setup webview 相关功能不做 F 剧本；后续新增 TreeView 用例先走 `F_TREEVIEW_PROPOSAL.md`，经主管 review 后再写具体 step-by-step 剧本。

Session 15 约定：当前 F 只测 Codex intern，不做 Codex 之外 intern；Team 功能还不 ready，不纳入当前 F。

## 当前已覆盖

| 维度 | 已覆盖剧本 | 覆盖内容 |
|------|------------|----------|
| Workspace | `F_0001`-`F_0006` | Codeup `repo_dotdir`/`metadata_branch` 添加，GitHub 添加，本地 workspace 添加，重复/非法添加回滚，workspace mode switch removed 负向契约，部分 GUI/CLI equivalence。 |
| Task | `F_0023`-`F_0024` 已批准分发 | Task grouping/README/tooltip/metadata parsing 和 deleteTask GUI contract 已有正式剧本，worker6 正在实现；`F_0023` 的第 3 行 METADATA contract 当前已验证出 parser bug evidence。 |
| Intern | `F_0007`-`F_0010`，`F_0025`-`F_0028` | Codex intern 创建/status/session/delete 主线已有第一批；TreeView projection、active/session map、Codex session context command、Feishu group mode context menu 已补第三批正式剧本。 |
| Skill | `F_0029`-`F_0030` | Codex skill source、repo/personal enable、promote 和 farm sync 已补正式剧本；Copilot shared skills 不纳入当前 F。 |
| Feishu slash/helper | `F_0016`-`F_0020` | `/config` 三类 mode，`/helper` open/start/status/stop/机器选择，main bot readonly slash，slash error/RBAC。 |
| Daemon/relay API | `F_0011`-`F_0013`、`F_0015` | daemon status/readiness、group proxy、relay chat、question card callback。`F_0014` peer send visibility 已迁到 `J_0014`，因为它启动 live intern sessions 并检查 receiver pane。严格说这不是 GUI，但它们支撑 GUI/飞书侧状态。 |

## Workspace 缺口

- `intern.removeWorkspace` 与 `intern.stopMaintainProject` 语义拆分未完整覆盖：前者是本机 disable，后者是 relay/workspace registry delete，第一批只粗略覆盖 remove/delete，没有验证二者的不同确认文案、CLI 映射和副作用边界。
- 删除/停用的取消路径缺失：modal 取消、typed workspace name 不匹配、`axis_intern_agents` 按普通 workspace remove/stop-maintain 路由。
- workspace enable/disable/re-enable、`workspace doctor`、disabled workspace 隐藏 interns/tasks/skills、再次 enable 后 TreeView 恢复未覆盖。
- `intern.openSetup`、`intern.selectProjects` 与 workspace 列表联动不进入 F 剧本：setup 相关无法稳定测试，按主管约定排除。
- `intern.refreshTree` 的 metadata sync、workspace list reload、PR-discovered interns 刷新未覆盖。
- 多 workspace 并存时的同名/同 repo 边界不足：同 repo 不同 mode、同 display name、跨 provider 同名 display 的错误分类和回滚。

## Task 缺口

- TreeView task 分组未覆盖：`InProgress`/`Open`/`Completed` 分组顺序、计数、Completed 默认收起、task tooltip 的 status/assignee/PR 展示。
- 点击 task 打开 README 未覆盖：README 存在时应 `vscode.open`，不存在时不能抛 UI 错。
- `intern.deleteTask` 未覆盖：从 task item 删除、无 tree item 时 QuickPick 删除、确认取消、Open/Completed 删除成功、InProgress 拒绝删除、无任务提示。
- `internal task-list` 与 `internal task-delete` 的 GUI/CLI contract 未覆盖：project scope、branch/commit evidence、删除后刷新 TreeView。
- task metadata 解析边界未覆盖：第 3 行 METADATA、缺 assignee、PR URL/#N/纯数字展示、畸形 metadata 的错误/降级行为。
- GUI 目前没有“创建 task/分配 task”的普通入口；team worker task 分配在 team CLI/peer 流程里。如果产品期望 GUI 管 task 创建，这应作为 GUI 功能缺口单独提出。

## Intern 缺口

- create intern backend matrix 当前只保留 Codex；Copilot、Claude、`not_now` 不进入当前 F。
- team mode 角色和 team GUI 暂不覆盖：`enableTeamMode`、`createTeamForCoordinator`、`assignTeamToCoordinator`、`deleteTeam`、`forceDeleteTeam` 均标记 out-of-scope。
- context menu session 命令聚焦 Codex：`openChatForIntern`、`createCodexSession`、`restartCodexSession` 在 `intern-codex` 下的可见性和行为需要覆盖；Claude/Team coordinator variant 不进入当前 F。
- active intern/status bar 已由 `F_0026` 补正式剧本：Chat session resource 切换、`.intern_sessions.json` project-scoped key、状态栏 active intern 显示、unknown/empty session 时清空 active intern。
- TreeView projection 已由 `F_0025` 补正式剧本：online/offline 分组、focus 排序、PR tooltip、same intern name across workspaces 的 project disambiguation。
- delete intern 边界第一批 `F_0010` 已覆盖主线；若后续还要细化取消确认或 same-name delete GUI，可作为小补充，但当前不新开 Team/non-Codex 相关 delete。
- GUI 右键设置 Feishu group mode 已由 `F_0028` 补正式剧本：`setTriggerModeAll`、`setTriggerModeAtOnly`、`setDetailModeFull`、`setDetailModeSummary` 与 `internctl group trigger-mode/detail-mode` 的 contract。第一批 slash `/config` 覆盖飞书卡片，不覆盖 VS Code TreeView context menu。

## Skill 缺口

- Skill Sources TreeView 已由 `F_0029` 补正式剧本：workspace 下 `Skill Sources`、package 节点、skill 节点、empty state、`SKILL.md` 打开、add/update/remove source。
- repo/personal enable 已由 `F_0030` 补正式剧本：repo enable/disable、personal enable/disable、repo-enabled 时 personal reject、personal promote to repo、Codex farm sync。
- 无 Codex intern 时拒绝可作为 `F_0030` 的扩展断言或后续小补充；当前不为了 Claude/Copilot 创建 fixture。
- personal -> repo promote 未覆盖：存在 personal holders 时 repo enable 应先确认 promote，并清理 personal enable。
- `syncFarm` 未覆盖：UI 写操作后 farm sync，activate/enterprise refresh 后 farm sync，冲突/error evidence。
- Copilot shared skills 不纳入当前 F；后续若恢复 Copilot scope 再单独 proposal。

## 其他 GUI 维度

- Setup webview：不做 F 剧本，相关命令和交互按 out-of-scope 记录。
- 本机配置：format check toggle、language switch/reload prompt、`intern.openChatOnSwitch`、`intern.outerRepoPullIntervalMs` 的 GUI 行为。
- 插件健康状态：status bar daemon/relay/hooks warning、daemon version/hash tooltip、connectivity grace window、点击 status bar refresh。
- Activation/refresh：activate 后 hooks 初始化、Codex LB provider config、outer repo pull、metadata sync error 展示、PR discovery。
- GUI 菜单可见性：不同 `viewItem` contextValue 下命令是否正确出现/隐藏。特别是 danger group、inline group 和 commandPalette `when=false` contract。

## 建议第二批 GUI F 剧本

| 建议 ID | 维度 | 目标 |
|---------|------|------|
| `F_0021` | workspace GUI | disable vs delete/stop-maintain contract，确认取消、typed name mismatch、`axis_intern_agents` 正常 remove/stop-maintain 路由。 |
| `F_0022` | workspace GUI | enable/disable/re-enable、doctor、TreeView project/intern/task/skill projection 刷新。 |
| `F_0023` | task GUI | task tree grouping、README open、tooltip/PR formatting、metadata parse boundaries。 |
| `F_0024` | task GUI | `deleteTask` item/QuickPick paths，Open/Completed success，InProgress reject，no-task warning。 |
| `F_0025` | intern GUI | Codex intern TreeView projection、online/offline、active focus、same-name project scope。 |
| `F_0026` | intern GUI | Codex active intern/status bar/session map、open chat、same-name cross project disambiguation。 |
| `F_0027` | intern GUI | Codex session context commands create/restart/status、LB prerequisite 和 failure rollback。 |
| `F_0028` | intern GUI | VS Code TreeView Codex Feishu group mode context menu：trigger/detail modes 与 CLI/group config 同步。 |
| `F_0029` | skill GUI | skill source add/update/remove、TreeView projection、`SKILL.md` open。 |
| `F_0030` | skill GUI | Codex repo/personal enable/disable/promote/conflict and farm sync。 |
| `F_0031` | config/status GUI | format check toggle、language switch、plugin health status bar、refreshTree error reporting。 |
| `F_0032` | menu GUI | TreeView menu visibility/contextValue audit，Codex/workspace/task/skill only。 |

## 分发依赖建议

- `F_0021`-`F_0022` 共享 workspace state/action，可同包。
- `F_0023`-`F_0024` 共享 task metadata builder、TreeView projection assert 和 `internal task-*` action，可同包。
- `F_0025`-`F_0028` 共享 Codex intern/session/statusbar/group-mode action，可拆两包：projection/active 与 session/group-mode。
- Team 不分发 F。
- `F_0029`-`F_0030` 共享 skill source fixture 和 Codex farm assert，建议同一个 worker 或按 source -> enable 顺序分发。
- `F_0031`-`F_0032` 可独立分发；setup、Team、非 Codex intern、Copilot shared skills 均应在 case 中显式断言未执行。
