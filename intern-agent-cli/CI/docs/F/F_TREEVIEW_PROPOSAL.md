# F TreeView proposal

<!-- METADATA:STATUS=ApprovedWithScope,OWNER=intern_ci_lead,SESSION=15 -->

本文先从 VS Code TreeView 功能面提出 F 用例补充方案，不展开成具体 step-by-step 剧本。主管 review 通过后，再按批准的分包写正式 `F_xxxx.md`。

## 约定

- Setup webview 相关功能不做 F 剧本：`intern.setupRefresh`、`intern.setupAutoFix`、`intern.selectProjects`、`intern.openSetup`、`intern.backToAgents` 不纳入后续 F proposal。
- Codex 之外的 intern 不做当前 F 剧本：不创建 Claude/Copilot/`not_now` intern。
- Team 功能还不 ready，不做当前 F 剧本：`enableTeamMode`、coordinator/team nodes、team create/assign/delete 全部 out-of-scope。
- TreeView F 只测插件 GUI handler、TreeDataProvider projection、CLI 等价 contract、metadata/registry 状态一致性；不测 agent 自然语言能力。
- 每个 proposal group 后续最多拆成 1-2 个 F case，避免按每个菜单项散成几十上百个小 case。
- GUI 是 CLI 的壳：如果某个 TreeView 操作不能被 CLI 精确表达，worker 实现时应作为 GUI/CLI contract bug 报告。

## 当前状态

| 维度 | 当前已有 | 评价 |
|------|----------|------|
| Workspace | `F_0001`-`F_0006`，以及已起草但未分发的 `F_0021`-`F_0022` | 主干 add/mode/error rollback 已有；TreeView disable/delete/enable/doctor/refresh 刚补了草案，需要你 review 是否保留。 |
| Task | 已起草但未分发的 `F_0023`-`F_0024` | 第一批没有 task GUI；刚补了 task grouping/README/tooltip/delete 草案，需要你 review 是否保留。 |
| Intern | `F_0007`-`F_0010`，已补 `F_0025`-`F_0028` | Codex intern create/session/delete 主线已有；Codex TreeView projection、active/session map、Codex session context command、group mode context menu 已补正式剧本。 |
| Skill | 已补 `F_0029`-`F_0030` | Codex skill source、repo/personal enable/promote/farm sync 已补正式剧本；Copilot shared skills不进入当前 F。 |
| Team | 不做当前 F | Team mode 和 coordinator/team nodes 暂不 ready，按主管要求放着。 |
| 其他 TreeView | 已补 `F_0031`-`F_0032` | `refreshTree`、format check top-level item、language switch、status bar、menu visibility/contextValue 已补正式剧本。Setup 明确排除。 |

## Proposal: Workspace

建议保留 2 个 case group：

| Group | 覆盖目标 | 覆盖命令/节点 | 备注 |
|-------|----------|---------------|------|
| W1 workspace lifecycle baseline | add/mode/error rollback 的主干回归 | `intern.addWorkspace`、workspace node、`intern.removeWorkspace` | 已由 `F_0001`-`F_0006` 覆盖为主；无需继续扩。 |
| W2 workspace TreeView state | disable vs delete、enable/re-enable、doctor、TreeView 隐藏/恢复、refresh | `intern.removeWorkspace`、`intern.stopMaintainProject`、workspace node、`intern.refreshTree` | 对应已起草的 `F_0021`-`F_0022`；建议作为一个 worker 包。 |

暂不建议补的 workspace 项：

- Setup project selection，因为 setup 不做 F。
- 每个 provider/mode 的重复排列组合，第一批已经覆盖 enough provider/mode matrix。

## Proposal: Task

建议保留 2 个 case group：

| Group | 覆盖目标 | 覆盖命令/节点 | 备注 |
|-------|----------|---------------|------|
| T1 task projection | task group 顺序/计数、README open、tooltip、PR formatting、METADATA 第 3 行解析 | workspace 下 task group、task item | 对应已起草的 `F_0023`。 |
| T2 task mutation | deleteTask item path、QuickPick path、取消确认、Open/Completed success、InProgress reject、no-task warning | `intern.deleteTask` | 对应已起草的 `F_0024`。 |

暂不建议补的 task 项：

- GUI task 创建/分配，因为当前普通 TreeView 没有创建 task 的用户入口；team worker task 分配属于 team 维度。
- 按每种 PR URL host 单独拆 case；建议在 T1 内用 URL、`#N`、纯数字三种代表格式覆盖。

## Proposal: Intern

建议新增 4 个 Codex-only case group，已落正式剧本：

| Group | 覆盖目标 | 覆盖命令/节点 | 依赖 |
|-------|----------|---------------|------|
| I1 Codex intern TreeView projection | Codex icon/contextValue、Idle/Working/currentTask/PR tooltip、same-name cross workspace project scope、online/offline/focus 排序 | intern node、intern group、tooltip、status bar active marker | 对应 `F_0025`；依赖 intern metadata builder、session map fixture。 |
| I2 Codex active/session routing | active intern、status bar、project-scoped session map、Open Chat、unknown session clear | `intern.openChatForIntern`、ActiveInternController、status bar | 对应 `F_0026`；不发送业务 prompt。 |
| I3 Codex session context commands | create/restart Codex session、LB prerequisite、running guard、failure rollback | `intern.createCodexSession`、`intern.restartCodexSession` | 对应 `F_0027`；依赖 headless session action。 |
| I4 Codex group mode menu | TreeView 右键 trigger/detail mode 与 group config 同步，跨 project 同名隔离 | `intern.setTriggerMode*`、`intern.setDetailMode*` | 对应 `F_0028`；与 slash `/config` 区分开测。 |

不再扩 create backend matrix 到 Claude/Copilot/`not_now`，也不测 Team coordinator variant。

## Proposal: Skill

建议新增 2 个 Codex skill case group，已落正式剧本：

| Group | 覆盖目标 | 覆盖命令/节点 | 依赖 |
|-------|----------|---------------|------|
| S1 skill source projection/mutation | workspace 下 Skill Sources、empty state、package/skill 节点、open `SKILL.md`、add source、update source、remove source cascade | `skill-project`、`skill-pkg`、`skill-item`、`intern.skill.addSource`、`intern.skill.tree.updatePkg`、`intern.skill.tree.removePkg` | 对应 `F_0029`；需要本 case 专用 local skill source fixture。 |
| S2 Codex repo/personal enable contract | repo enable/disable、personal enable/disable、repo-enabled 时 personal reject、personal promote to repo、Codex farm sync | `intern.skill.tree.enableRepo`、`enablePersonal`、`disableRepo`、`disablePersonal` | 对应 `F_0030`；需要至少 2 个 Codex intern fixture。 |

Skill 建议作为独立 worker 包，不要和 intern/session 混在一起。原因是 skill fixture 和 farm sync 有共享 action，拆散会造成重复实现。Copilot shared skills 暂不做。

## Proposal: Team

Team 是 TreeView 的独立维度，但当前不做 F：

- 不写 Team 剧本。
- 不创建 Team worker task。
- 只在 menu/context audit 中断言 Team command 未被本 case 执行。

## Proposal: Other TreeView

建议保留 2 个轻量 group：

| Group | 覆盖目标 | 覆盖命令/节点 | 备注 |
|-------|----------|---------------|------|
| O1 TreeView menu visibility contract | 不同 `contextValue` 下菜单项出现/隐藏：intern-codex、workspace、task、skill-pkg、skill-item | `package.json` menu `when` + TreeItem contextValue | 对应 `F_0032`；Team、Setup、Copilot shared、非 Codex intern 排除。 |
| O2 top-level config/status items | plugin meta item、format check toggle、language switch、refreshTree error reporting、status bar health refresh | top-level config/action/meta items、`intern-agent.toggleFormatCheck`、`intern.switchLanguage`、`intern.refreshTree` | 对应 `F_0031`；setup 排除；reload 只断言 command。 |

## 建议执行顺序

1. Review 当前 proposal，先确认哪些 group 要进入第二批正式剧本。
2. 若保留已起草的 `F_0021`-`F_0024`，先把它们作为 workspace/task GUI 包给你 review；不分发 worker。
3. `F_0025`-`F_0028` 已写成 Codex-only intern 正式剧本，后续按 action 依赖分包。
4. `F_0029`-`F_0030` 已写成 Codex skill 正式剧本，建议独立 skill worker 包。
5. `F_0031`-`F_0032` 已写成 config/status/menu audit 正式剧本，可独立分发。

## 暂定不做

- Setup webview 和 setup commands。
- Codex 之外的 intern：Claude、Copilot、`not_now`。
- Team 功能。
- Copilot shared skills。
- VSIX 安装/发布、hooks 覆盖、reload window 的真实执行。
- 每个 command 单独一个 F case 的细粒度拆分。
- 需要重启 relay 的场景。
