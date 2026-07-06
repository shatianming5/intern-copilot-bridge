# J 剧本总览

<!-- METADATA:STATUS=ReviewDraft,OWNER=intern_ci_lead,SESSION=12 -->

本文整理当前已经写成独立文档的 J 类真实用户旅程剧本。单个用例的权威剧本仍以对应 `J_*.md` 为准；本文只做索引、分组和继续补写时的上下文压缩。`J_0014` 已写入 J registry；其他早期 J 仍是 review draft，尚未宣称实机通过。

Session 12 supervisor guidance: avoid one J per tiny feature point. Future
reviews should prefer multi-prompt journey bundles and fold duplicated small
drafts into the bundle that best represents the user's real workflow.

## 当前 proposal

- [`J_SCRIPT_AUTHORING.md`](J_SCRIPT_AUTHORING.md)：J 剧本作者指南，定义 J/F 边界、`action` / `wait` / `assert` 写法、自然用户 prompt、资源命名、retained scene、workspace migration 禁用规则和 review checklist。
- [`J_BATCH_PROPOSAL.md`](J_BATCH_PROPOSAL.md)：下一批 J 用例 review draft，明确 J/F 边界、`ci_j_XXXX` 资源命名规则、第一批建议推进的 `J_0001`、`J_0002`、`J_0007`、`J_0008`、`J_0009` 到 `J_0100`，以及只有在主管确认后才可重写的 workspace migration 独占矩阵。
- [`J_0001.md`](J_0001.md)：Codeup protected repo + `metadata_branch` 真实飞书分配、继续、批准 merge journey。
- [`J_0002.md`](J_0002.md)：GitHub protected repo + `metadata_branch` 真实飞书分配、继续、批准 merge journey。
- [`J_0003.md`](J_0003.md)：GitHub `repo_dotdir -> metadata_branch` 显式 migration PR + re-add/reuse journey。
- [`J_0004.md`](J_0004.md)：Codeup `repo_dotdir -> metadata_branch` 显式 migration MR + re-add/reuse journey。
- [`J_0005.md`](J_0005.md)：GitHub `metadata_branch -> repo_dotdir` 显式 migration PR + re-add/reuse journey。
- [`J_0006.md`](J_0006.md)：Codeup `metadata_branch -> repo_dotdir` 显式 migration MR + re-add/reuse journey。
- [`J_0007.md`](J_0007.md)：真实 Feishu 卡片交互 journey，覆盖 A/B 按钮、自由输入、单选、多选和 pending question cleanup。
- [`J_0008.md`](J_0008.md)：真实 `/stop`、`你继续` continuation、`/screenshot` 图片回传 journey。
- [`J_0009.md`](J_0009.md)：Codex `/goal` 设置、Replace/Cancel 确认卡、status/clear 和 lifecycle 去重 journey。
- [`J_0010.md`](J_0010.md)：Team Mode coordinator -> team lead -> worker 真实 delegation journey。
- [`J_0011.md`](J_0011.md)：Retired/folded into `J_0090`; outgoing rich-content split 不再单独实现。
- [`J_0012.md`](J_0012.md)：默认 builtin skills 在真实 agent 使用和 compaction/resume 后仍可发现的 journey。
- [`J_0013.md`](J_0013.md)：Codex repo/personal skill enable/disable 后真实 agent 可见性和使用 journey。
- [`J_0015.md`](J_0015.md)：Feishu group trigger/detail/no-collapse mode 对真实 Codex turn 的用户可见影响 journey。
- [`J_0016.md`](J_0016.md)：protected MR/PR review feedback 后更新同一 change request，再经明确批准 merge 的 journey。
- [`J_0017.md`](J_0017.md)：Claude plan approval card、free-text refine、明确 approve 后才编辑文件的 journey。
- [`J_0018.md`](J_0018.md)：Codex 长 turn 运行中收到追加指令，并按最新用户要求完成回复的 journey。
- [`J_0019.md`](J_0019.md)：Codex 工作中遇到 policy/env pending restart，完成后重启并继续对话的 journey。
- [`J_0020.md`](J_0020.md)：Machine helper 启动、诊断对话、invite owner、stop/restart 后继续可用的 journey。
- [`J_0021.md`](J_0021.md)：inbound attachment bundle，分 phase 覆盖 Markdown、图片、附件-only、以及多附件/富文本内容。
- [`J_0022.md`](J_0022.md)：Retired/folded into `J_0021`; 图片附件检查不再单独实现。
- [`J_0023.md`](J_0023.md)：Retired/folded into `J_0021`; attachment-only 后续消费不再单独实现。
- [`J_0024.md`](J_0024.md)：Retired；模糊指令下是否主动澄清/发卡属于 agent/prompt 行为，不作为当前 J。
- [`J_0025.md`](J_0025.md)：Codex `request_user_input` 双路径信号不会生成重复有效卡片的 journey。
- [`J_0026.md`](J_0026.md)：receiver Working 时 sender 发送 next-turn peer message，忙完后可见的 journey。
- [`J_0027.md`](J_0027.md)：同名 intern 跨 project 时 peer-send 只命中目标 project 的 journey。
- [`J_0028.md`](J_0028.md)：`goal-send` 未授权拒绝、coordinator 授权设置/清理 worker goal 的 journey。
- [`J_0029.md`](J_0029.md)：Codex compaction/resume 后仍保持当前 task/branch/newest instruction 的 journey。
- [`J_0030.md`](J_0030.md)：`/esc` 清掉 pending input/question 后，新 prompt 不被旧状态污染的 journey。
- [`J_0031.md`](J_0031.md)：intern 自然使用 Feishu 群成员能力并保护 raw id/secret 的 journey。
- [`J_0032.md`](J_0032.md)：machine helper 遇到敏感操作先 AskUser，用户拒绝后转只读路径的 journey。
- [`J_0034.md`](J_0034.md)：Retired/folded into `J_0021`; 多附件顺序作为 attachment bundle phase 覆盖。
- [`J_0035.md`](J_0035.md)：Retired/folded into `J_0021`; rich post 混合内容作为 attachment bundle phase 覆盖。
- [`J_0036.md`](J_0036.md)：question card timeout/stale 后重新提问，旧答案不能生效的 journey。
- [`J_0037.md`](J_0037.md)：replacement question 出现后，旧卡 callback 被隔离的 journey。
- [`J_0038.md`](J_0038.md)：pending question 可由普通文本回答，且不被重复当作新 prompt 的 journey。
- [`J_0039.md`](J_0039.md)：pending question 期间 `/stop` 让旧卡 stale，新 prompt 干净执行的 journey。
- [`J_0040.md`](J_0040.md)：Codex idle session restart 后同一 Feishu group 仍可 follow-up 的 journey。
- [`J_0041.md`](J_0041.md)：main-bot readonly status 与实际 intern group follow-up 一致的 journey。
- [`J_0042.md`](J_0042.md)：Claude plan approval cancel/deny 后无编辑且 stale approve 无效的 journey。
- [`J_0043.md`](J_0043.md)：Claude 真实 turn 后 `/exit`、manual resume、GUI-equivalent restart 同 UUID journey。
- [`J_0044.md`](J_0044.md)：Claude group trigger/detail mode 对真实 Claude turn 生效的 journey。
- [`J_0045.md`](J_0045.md)：Claude 真实使用 repo/personal skill，并验证 disable 后不能继续声称使用 repo skill 的 journey。
- [`J_0046.md`](J_0046.md)：protected task 打开 PR/MR 但明确不 merge 的 journey。
- [`J_0047.md`](J_0047.md)：merge approval 自然语言边界，模糊夸赞不等于批准 merge 的 journey。
- [`J_0048.md`](J_0048.md)：review 反馈 focused test failed 后更新同一 PR/MR 的 journey。
- [`J_0049.md`](J_0049.md)：Retired；protected merge-flow 变体不再穷举。
- [`J_0050.md`](J_0050.md)：supervisor 取消 active task 后不提交、不 merge 的 journey。
- [`J_0051.md`](J_0051.md)：Retired；generic code review-only 不属于插件/内置 prompt J。
- [`J_0052.md`](J_0052.md)：Retired；generic no-tools/no-edits instruction 不作为 J。
- [`J_0053.md`](J_0053.md)：内置 prompt 合约：用户已有 dirty worktree 时，intern 保护用户改动。
- [`J_0054.md`](J_0054.md)：stop-hook checklist 修正后的用户可见最终回复 journey。
- [`J_0055.md`](J_0055.md)：Retired；generic latest-instruction precedence 已由产品 running supplement `J_0018` 承担。
- [`J_0056.md`](J_0056.md)：Retired；generic evidence-path reporting 不作为独立 J。
- [`J_0057.md`](J_0057.md)：内置 prompt 合约：用户明确禁止 web/latest 时，intern 只用本地上下文。
- [`J_0058.md`](J_0058.md)：内置 prompt 合约：用户要求 latest verification 时，intern 走 approved lookup 并给 source/date。
- [`J_0059.md`](J_0059.md)：Retired；minimal diff/no refactor 是通用 coding-agent 风格。
- [`J_0060.md`](J_0060.md)：Retired；refactor-with-tests 是通用 coding-agent workflow。
- [`J_0061.md`](J_0061.md)：用户在长任务中途询问状态，intern 汇报并继续的 journey。
- [`J_0062.md`](J_0062.md)：Retired；read-only redirect 是通用 instruction-following。
- [`J_0063.md`](J_0063.md)：Retired；user patch snippet 是通用 coding-agent workflow。
- [`J_0064.md`](J_0064.md)：Retired；preserve formatting 是通用编辑风格。
- [`J_0065.md`](J_0065.md)：Retired；bilingual summary 是通用沟通质量。
- [`J_0066.md`](J_0066.md)：Team coordinator 将小调查拆给两个 worker 并聚合归因的 journey。
- [`J_0067.md`](J_0067.md)：worker 需要澄清时经 coordinator 回到用户再继续的 journey。
- [`J_0068.md`](J_0068.md)：worker 命中受控失败后诚实向 coordinator/escalation 汇报的 journey。
- [`J_0069.md`](J_0069.md)：worker 失败后 coordinator 重新分配给另一个 worker 的 journey。
- [`J_0070.md`](J_0070.md)：coordinator 汇总状态时包含 busy worker 且不误报完成的 journey。
- [`J_0071.md`](J_0071.md)：跨 team 权限边界，非所属 worker 不能被静默指挥的 journey。
- [`J_0072.md`](J_0072.md)：peer message 带附件不支持时 agent 诚实报告能力边界的 journey。
- [`J_0073.md`](J_0073.md)：intern-to-intern artifact review 后回传 review 结果的 journey。
- [`J_0074.md`](J_0074.md)：no-collapse 长输出 follow-up message 顺序与完整性的 journey。
- [`J_0075.md`](J_0075.md)：Retired；language preference 是通用输出偏好。
- [`J_0076.md`](J_0076.md)：Retired；strict table output 是通用格式跟随。
- [`J_0077.md`](J_0077.md)：Retired；typo correction 是通用 instruction-following。
- [`J_0078.md`](J_0078.md)：Retired；compare-before-act 是通用 planning 行为。
- [`J_0079.md`](J_0079.md)：内置 prompt safety：潜在破坏性命令前先披露风险并等待确认。
- [`J_0080.md`](J_0080.md)：内置 prompt safety：用户明确批准后只做 scoped cleanup。
- [`J_0081.md`](J_0081.md)：Retired；generic reproduction-only request 不作为 J。
- [`J_0082.md`](J_0082.md)：Retired；generic repro-to-fix workflow 不作为 J。
- [`J_0083.md`](J_0083.md)：内置 prompt 合约：只回滚 intern 自己上一处修改，不动用户改动。
- [`J_0084.md`](J_0084.md)：Retired；generic branch status summary 不作为 J。
- [`J_0085.md`](J_0085.md)：Retired；concise final answer 是通用沟通风格，hook checklist 由 `J_0054` 覆盖。
- [`J_0086.md`](J_0086.md)：goal replace/clear 后 Feishu 可见 footer/status 不复活 stale goal 的 journey。
- [`J_0087.md`](J_0087.md)：missing-value goal 先 blocked，主管补值后 resume 同一目标生命周期的 journey。
- [`J_0088.md`](J_0088.md)：case-scoped compaction 后内部 summary 不作为 Feishu 用户答案外泄的 journey。
- [`J_0089.md`](J_0089.md)：Retired；bad hook state recovery 不作为 J journey。
- [`J_0090.md`](J_0090.md)：多轮 outgoing artifact delivery bundle，覆盖小图片/小文件发送和 oversized 失败 notice/fallback 的 journey。
- [`J_0091.md`](J_0091.md)：Retired；generic failure evidence-path reporting 不作为 J，project/session isolation 由 `J_0092` 覆盖。
- [`J_0092.md`](J_0092.md)：跨 project 同名 intern 的 session/group 隔离 journey。
- [`J_0093.md`](J_0093.md)：coordinator status 只汇总自己 owned team/worker 的 journey。
- [`J_0094.md`](J_0094.md)：无 team 的 independent intern 拒绝伪造 delegation 的 journey。
- [`J_0095.md`](J_0095.md)：coordinator 创建/选择 scoped task 并 handoff 给 team lead 的 journey。
- [`J_0096.md`](J_0096.md)：machine helper stop/reconnect 后仍指向预期 saved profile 的 journey。
- [`J_0097.md`](J_0097.md)：helper preferred backend 不可用时 fallback/blocked claim 诚实的 journey。
- [`J_0098.md`](J_0098.md)：detail summary 模式下真实 tool-heavy turn 仍可读的 journey。
- [`J_0099.md`](J_0099.md)：running supplement near-final 时 `/stop` 不被 stale final 覆盖的 journey。
- [`J_0100.md`](J_0100.md)：policy restart/resume 后 Feishu group 仍回到 agent-ready 的 journey。

## 共用规则

1. 剧本语法统一为 `action` / `wait` / `assert`，helper 语义见 `J_ACTIONS.md`。
2. 第 5 阶段 J 是 intern 级并发：lead 预置 workspace，worker 只能创建自己的 intern、session、task、branch、MR/PR、report 和临时目录。
3. 第 5 阶段 J 不允许添加、删除或切换 workspace。
4. task 可以由 CLI action 创建，但 assignment 必须来自真实飞书 prompt，不能由 CLI 直接分配。
5. merge 必须由真实飞书 prompt 明确批准后发生；默认不能 merge，也不能 direct push。
6. 业务 MR/PR 不能混入 `.intern_workspace`、status、history 或 task knowledge。
7. report 必须记录 chat/message id、task/intern/workspace、MR/PR、target revision、关键 diff、callback/slash evidence 和 cleanup evidence。
8. workspace mode 固定在 add time；J 成功路径禁止使用 `workspace mode set`、daemon `/mode/set`、relay `/mode/set` 或原地 "change repo mode"。
9. 如需 workspace mode 迁移，只能写显式 `internctl workspace migrate-mode` + migration PR/MR merge + re-add/reuse workspace 的独占 journey；`local_only` 与 remote modes 的互转拒绝属于 F。

## 已写用例表

| ID | 阶段 | 资源 | 主路径 | 当前用途 |
|----|------|------|--------|----------|
| [`J_0001`](J_0001.md) | 第 5 阶段 | Codeup protected + `metadata_branch` | 飞书分配 task -> 继续产出 MR -> 明确批准 merge | ReviewDraft，需预置可 merge 测试 repo |
| [`J_0002`](J_0002.md) | 第 5 阶段 | GitHub protected + `metadata_branch` | 飞书分配 task -> 继续产出 PR -> 明确批准 merge | ReviewDraft，需预置可 merge 测试 repo |
| [`J_0003`](J_0003.md) | 第 6 阶段 | GitHub `repo_dotdir -> metadata_branch` | migration PR merge + re-add/reuse | ReviewDraft，exclusive workspace |
| [`J_0004`](J_0004.md) | 第 6 阶段 | Codeup `repo_dotdir -> metadata_branch` | migration MR merge + re-add/reuse | ReviewDraft，exclusive workspace |
| [`J_0005`](J_0005.md) | 第 6 阶段 | GitHub `metadata_branch -> repo_dotdir` | migration PR merge + re-add/reuse | ReviewDraft，exclusive workspace |
| [`J_0006`](J_0006.md) | 第 6 阶段 | Codeup `metadata_branch -> repo_dotdir` | migration MR merge + re-add/reuse | ReviewDraft，exclusive workspace |
| [`J_0007`](J_0007.md) | 第 5 阶段 | case-scoped local/throwaway workspace | 真实飞书卡片按钮、自由输入、单选、多选 | ReviewDraft，需补 card helper |
| [`J_0008`](J_0008.md) | 第 5 阶段 | case-scoped local/throwaway workspace | `/stop` 后“你继续”与 `/screenshot` 图片回传 | ReviewDraft，需补 stop/screenshot/turn helper |
| [`J_0009`](J_0009.md) | 第 5 阶段 | case-scoped Codex intern/group | `/goal` active -> replace Cancel/Replace -> status/clear | ReviewDraft，需补 goal/card evidence helper |
| [`J_0010`](J_0010.md) | 第 5/Team 阶段 | case-scoped Team Mode resources | coordinator 委派 team lead/worker，peer-send worker task | ReviewDraft，需补 multi-intern/team helper |
| [`J_0011`](J_0011.md) | Retired | case-scoped Codex intern/group | rich content split | Retired/folded into `J_0090` outgoing artifact delivery bundle |
| [`J_0012`](J_0012.md) | 第 5 阶段 | case-scoped Codex interns | builtin skills agent 使用与 compaction 后可发现性 | ReviewDraft，需补 compaction/skill-use helper |
| [`J_0013`](J_0013.md) | 第 5 阶段 | case-scoped Codex interns/skills | repo skill use、repo disable、personal skill isolation | ReviewDraft，需补 Codex skill-use helper |
| `J_0014` | daemon/relay live-session journey | local workspaces + live intern sessions | peer send delivered/visible、unknown/ambiguous target、invalid mode | 已从 `F_0014` 迁入 registry |
| [`J_0015`](J_0015.md) | 第 5 阶段 | case-scoped Codex intern/group | trigger all/at-only、detail summary/full、no-collapse live turn | ReviewDraft，需补 mention/no-turn/timeline helper |
| [`J_0016`](J_0016.md) | 第 5 阶段 | protected test repo | review feedback 更新同一 MR/PR，批准后 merge | ReviewDraft，需补 provider revision helper |
| [`J_0017`](J_0017.md) | 第 5 阶段 | case-scoped Claude workspace | plan approval card、free reply refine、approve 后编辑 | ReviewDraft，需补 plan-card callback helper |
| [`J_0018`](J_0018.md) | 第 5 阶段 | case-scoped Codex intern/group | 长 turn 运行中追加指令，最终按最新要求回复 | ReviewDraft，需补 running supplement helper |
| [`J_0019`](J_0019.md) | 第 5 阶段 | case-scoped Codex intern/group | Working turn 中 policy/env pending restart，结束后重启并继续 | ReviewDraft，需补 policy marker/restart progress helper |
| [`J_0020`](J_0020.md) | 第 5 阶段 | case-scoped helper group | helper start 后真实诊断对话、invite owner、stop/restart 可用性 | ReviewDraft，需补 helper live group helper |
| [`J_0021`](J_0021.md) | 第 5 阶段 | case-scoped Codex intern/group | inbound attachment bundle: markdown/image/attachment-only/multi-rich phases | ReviewDraft，需补 Feishu attachment/timeline helpers |
| [`J_0022`](J_0022.md) | Retired | case-scoped Codex intern/group | image attachment inspection | Retired/folded into `J_0021` Phase 2 |
| [`J_0023`](J_0023.md) | Retired | case-scoped Codex intern/group | attachment-only follow-up | Retired/folded into `J_0021` Phase 3 |
| [`J_0024`](J_0024.md) | Retired | case-scoped Codex intern/group | ambiguous request clarification | Retired：Session 12 判定为 agent/prompt 行为或重复卡片覆盖，不作为当前 J |
| [`J_0025`](J_0025.md) | 第 5 阶段 | case-scoped Codex intern/group | `request_user_input` 重复信号只保留一张有效卡 | ReviewDraft，需补 duplicate card evidence |
| [`J_0026`](J_0026.md) | 第 5 阶段 | case-scoped Codex sender/receiver | busy receiver next-turn peer message queued then visible | ReviewDraft，需补 busy/queue peer helper |
| [`J_0027`](J_0027.md) | 第 5 阶段 | two case-scoped projects | 同名 receiver 跨 project peer-send scope isolation | ReviewDraft，需补 multi-project helper |
| [`J_0028`](J_0028.md) | 第 5/Team 阶段 | coordinator/worker resources | goal-send permission denied + authorized set/clear | ReviewDraft，需补 role/goal evidence helper |
| [`J_0029`](J_0029.md) | 第 5 阶段 | case-scoped Codex task | compaction 后继续同一 task/branch | ReviewDraft，需补 compaction task evidence |
| [`J_0030`](J_0030.md) | 第 5 阶段 | case-scoped Codex intern/group | `/esc` 清 pending 后新 prompt 干净执行 | ReviewDraft，需补 esc/pending-state helper |
| [`J_0031`](J_0031.md) | 第 5 阶段 | case-scoped Codex intern/group | 自然列 Feishu group members 并 redacted | ReviewDraft，需补 group member baseline/helper |
| [`J_0032`](J_0032.md) | 第 5 阶段 | case-scoped helper group | helper 敏感操作 AskUser，deny 后只读 fallback | ReviewDraft，需补 helper AskUser/redaction helper |
| `J_0033` | Codex session journey | local workspace + case-scoped Feishu group | `hi` 触发真实 Codex UUID -> `/exit` 提示命令 -> resume/restart 同 UUID | 已从 `F_0033` 同会话断言拆出并实现 |
| [`J_0034`](J_0034.md) | Retired | case-scoped Codex intern/group | multi-attachment order | Retired/folded into `J_0021` Phase 4 |
| [`J_0035`](J_0035.md) | Retired | case-scoped Codex intern/group | rich post mixed content | Retired/folded into `J_0021` Phase 4 |
| [`J_0036`](J_0036.md) | 第 5 阶段 | case-scoped Codex intern/group | question timeout/stale 后 re-ask | ReviewDraft，需补 stale-card control |
| [`J_0037`](J_0037.md) | 第 5 阶段 | case-scoped Codex intern/group | replacement question 旧卡隔离 | ReviewDraft，需补 replacement-card evidence |
| [`J_0038`](J_0038.md) | 第 5 阶段 | case-scoped Codex intern/group | pending question 普通文本回答不 double-route | ReviewDraft，需补 text-answer correlation |
| [`J_0039`](J_0039.md) | 第 5 阶段 | case-scoped Codex intern/group | `/stop` 清 pending question 后 late answer 无效 | ReviewDraft，需补 stop/pending helper |
| [`J_0040`](J_0040.md) | 第 5 阶段 | case-scoped Codex intern/group | idle restart 后同群 follow-up | ReviewDraft，需补 restart/group continuity helper |
| [`J_0041`](J_0041.md) | 第 5 阶段 | main-bot + intern group | main-bot readonly status 后 group follow-up 一致 | ReviewDraft，需补 main-bot/no-turn helper |
| [`J_0042`](J_0042.md) | 第 5 阶段 | case-scoped Claude workspace | plan cancel/deny no edit，stale approve 无效 | ReviewDraft，需补 Claude cancel-card helper |
| [`J_0043`](J_0043.md) | Claude session journey | case-scoped local/throwaway workspace | Claude 真实 turn 后 `/exit` 提示命令 -> resume/restart 同 UUID | ReviewDraft，需补 Claude prompt/reply helper |
| [`J_0044`](J_0044.md) | 第 5 阶段 | case-scoped Claude group | trigger all/at-only/detail summary/full live turn | ReviewDraft，需补 Claude group timeline helper |
| [`J_0045`](J_0045.md) | Claude skill journey | case-scoped local/throwaway workspace | Claude 真实使用 repo/personal skill，disable 后不再声称 repo skill 可用 | ReviewDraft，需补 Claude prompt/reply helper |
| [`J_0046`](J_0046.md) | 第 5 阶段 | protected test repo | open PR/MR but do not merge | ReviewDraft，需补 provider no-merge guard |
| [`J_0047`](J_0047.md) | 第 5 阶段 | protected test repo | merge approval phrasing boundary | ReviewDraft，需补 merge event ordering helper |
| [`J_0048`](J_0048.md) | 第 5 阶段 | protected test repo | failed test review updates same PR/MR | ReviewDraft，需补 failing fixture/revision helper |
| [`J_0049`](J_0049.md) | Retired | protected test repo | rebase/update before merge variant | Retired：Session 10 判定 protected merge-flow 变体不再穷举 |
| [`J_0050`](J_0050.md) | 第 5 阶段 | case-scoped task | supervisor cancels active task | ReviewDraft，需补 cancellation status helper |
| [`J_0051`](J_0051.md) | Retired | review fixture | generic code review only | Retired：Session 10 判定为通用 LLM 行为，不属于插件/内置 prompt J |
| [`J_0052`](J_0052.md) | Retired | case-scoped intern | explanation only, no tools/edits | Retired：generic no-tools/no-edits instruction 不作为 J |
| [`J_0053`](J_0053.md) | 第 5 阶段 | dirty worktree fixture | preserve user-owned dirty changes | ReviewDraft，需补 dirty diff guard |
| [`J_0054`](J_0054.md) | 第 5 阶段 | case-scoped intern | stop-hook checklist correction | ReviewDraft，需补 hook feedback evidence |
| [`J_0055`](J_0055.md) | Retired | case-scoped edit | latest review instruction beats old plan | Retired：generic precedence；product running supplement 由 `J_0018` 覆盖 |
| [`J_0056`](J_0056.md) | Retired | evidence artifact | concrete evidence path reporting | Retired：generic reporting instruction 不作为独立 J |
| [`J_0057`](J_0057.md) | 第 5 阶段 | local fixture | no-web/local-only answer | ReviewDraft，需补 external-access guard |
| [`J_0058`](J_0058.md) | 第 5 阶段 | external lookup | latest verification with source/date | ReviewDraft，需补 approved lookup evidence |
| [`J_0059`](J_0059.md) | Retired | code fixture | minimal diff, no refactor | Retired：generic coding style |
| [`J_0060`](J_0060.md) | Retired | code/test fixture | requested refactor with tests | Retired：generic coding workflow |
| [`J_0061`](J_0061.md) | 第 5 阶段 | long-turn intern | mid-turn status request then continue | ReviewDraft，需补 active status helper |
| [`J_0062`](J_0062.md) | Retired | read-only fixture | redirect to safe read-only path | Retired：generic instruction-following |
| [`J_0063`](J_0063.md) | Retired | patch fixture | user patch snippet context-check | Retired：generic coding workflow |
| [`J_0064`](J_0064.md) | Retired | formatting fixture | preserve formatting/no churn | Retired：generic editing style |
| [`J_0065`](J_0065.md) | Retired | evidence fixture | bilingual summary factual consistency | Retired：generic communication quality |
| [`J_0066`](J_0066.md) | 第 5/Team 阶段 | coordinator + 2 workers | split work and aggregate worker results | ReviewDraft，需补 multi-worker evidence |
| [`J_0067`](J_0067.md) | 第 5/Team 阶段 | coordinator + worker | worker clarification routed to user | ReviewDraft，需补 clarification relay evidence |
| [`J_0068`](J_0068.md) | 第 5/Team 阶段 | failing worker fixture | worker failure escalation | ReviewDraft，需补 failure aggregation evidence |
| [`J_0069`](J_0069.md) | 第 5/Team 阶段 | coordinator + two workers | reassignment after worker failure | ReviewDraft，需补 reassignment evidence |
| [`J_0070`](J_0070.md) | 第 5/Team 阶段 | busy worker fixture | status rollup with busy worker | ReviewDraft，需补 busy/status evidence |
| [`J_0071`](J_0071.md) | 第 5/Team 阶段 | two teams | cross-team permission boundary | ReviewDraft，需补 permission evidence |
| [`J_0072`](J_0072.md) | 第 5 阶段 | peer + attachment | unsupported attachment honesty | ReviewDraft，需补 peer attachment evidence |
| [`J_0073`](J_0073.md) | 第 5 阶段 | two interns + artifact | intern artifact review | ReviewDraft，需补 artifact review evidence |
| [`J_0074`](J_0074.md) | 第 5 阶段 | no-collapse group | long output follow-up ordering | ReviewDraft，需补 continuation ordering helper |
| [`J_0075`](J_0075.md) | Retired | language preference fixture | user language preference switch | Retired：generic output preference |
| [`J_0076`](J_0076.md) | Retired | format fixture | strict markdown table output | Retired：generic format-following |
| [`J_0077`](J_0077.md) | Retired | typo/correction fixture | corrected intent execution | Retired：generic instruction-following |
| [`J_0078`](J_0078.md) | Retired | compare fixture | compare before act | Retired：generic planning behavior |
| [`J_0079`](J_0079.md) | 第 5 阶段 | risky command fixture | risk disclosure before destructive command | ReviewDraft，需补 confirmation/no-op guard |
| [`J_0080`](J_0080.md) | 第 5 阶段 | cleanup fixture | approved scoped cleanup | ReviewDraft，需补 cleanup scope guard |
| [`J_0081`](J_0081.md) | Retired | repro fixture | reproduction steps only | Retired：generic bug repro reporting |
| [`J_0082`](J_0082.md) | Retired | repro/fix fixture | fix after repro | Retired：generic repro-to-fix workflow |
| [`J_0083`](J_0083.md) | 第 5 阶段 | rollback fixture | rollback only intern's change | ReviewDraft，需补 user-change guard |
| [`J_0084`](J_0084.md) | Retired | branch fixture | branch status summary | Retired：generic operational summary |
| [`J_0085`](J_0085.md) | Retired | long evidence fixture | concise final closeout | Retired：generic final-answer style |
| [`J_0086`](J_0086.md) | 第 5 阶段 | long-running goal state fixture | replace/clear visible status and footnote/footer state | ReviewDraft，需补 goal footer extractor |
| [`J_0087`](J_0087.md) | 第 5 阶段 | blocked goal fixture | blocked goal resume after missing value | ReviewDraft，需补 goal lifecycle reader |
| [`J_0088`](J_0088.md) | 第 5 阶段 | compaction fixture | auto-compaction no Feishu summary leak | ReviewDraft，需补 no-noise timeline helper |
| [`J_0089`](J_0089.md) | Retired | bad hook state fixture | recovery without transcript flood | Retired：Session 12 判定 hook-state recovery 修法过多，且 intern 不应判断本地 hook state 正确性 |
| [`J_0090`](J_0090.md) | 第 5 阶段 | outgoing artifacts | image/file send plus oversized failure notice | ReviewDraft，需补 send-result/timeline evidence |
| [`J_0091`](J_0091.md) | Retired | failure report fixture | scoped evidence path reporting | Retired：generic closeout/reporting behavior；path scope 留给 F，project/session isolation 由 `J_0092` 覆盖 |
| [`J_0092`](J_0092.md) | 第 5 阶段 | same-name projects | session isolation | ReviewDraft，需补 multi-project session checker |
| [`J_0093`](J_0093.md) | 第 5/Team 阶段 | owned/unrelated teams | coordinator owner-filter status | ReviewDraft，需补 owner-scope checker |
| [`J_0094`](J_0094.md) | 第 5 阶段 | independent intern | no-team delegation refusal | ReviewDraft，需补 no-mutation guard |
| [`J_0095`](J_0095.md) | 第 5/Team 阶段 | coordinator + team lead | scoped task handoff | ReviewDraft，需补 handoff evidence |
| [`J_0096`](J_0096.md) | 第 5 阶段 | machine helper profile | saved profile reconnect | ReviewDraft，需补 helper profile evidence |
| [`J_0097`](J_0097.md) | 第 5 阶段 | helper backend fixture | backend fallback honesty | ReviewDraft，需补 runtime backend evidence |
| [`J_0098`](J_0098.md) | 第 5 阶段 | summary detail group | summary readability | ReviewDraft，需补 readability classifier |
| [`J_0099`](J_0099.md) | 第 5 阶段 | running supplement fixture | stop during finalization | ReviewDraft，需补 ordering checker |
| [`J_0100`](J_0100.md) | 第 5 阶段 | policy restart fixture | restart shell continuity | ReviewDraft，需补 restart readiness verifier |

## 第 5 阶段优先实现组

### `J_0001` / `J_0002` protected lifecycle

覆盖目标：protected repo + `metadata_branch` 下的标准任务生命周期。两者剧本相同，provider evidence 不同：`J_0001` 使用 Codeup MR，`J_0002` 使用 GitHub PR。

准备动作：

- cleanup 本 case namespace。
- 记录 target branch `before_rev` 和 baseline task ids。
- 创建未分配 task，task body 只要求改 `README.md` 的固定业务文本。
- 创建 codex intern 和真实飞书 intern 群。
- 断言 workspace provider/repo/mode/target branch 与 lead 预置一致。
- 断言 task 未分配、intern Idle、target revision 未变化。

剧本：

1. prompt: `分配任务 {task_id}。`
2. wait: 飞书消息 delivered、intern status 进入 Working、turn finished。
3. assert: assignment evidence 指向该飞书 message，task 仍是当前 task，没有误创建额外 task。
4. prompt: `继续`
5. wait: turn finished，MR/PR open。
6. assert: MR/PR 关联 task 和 target branch，diff 包含固定业务文本，不包含 metadata，target branch 仍是 `before_rev`。
7. prompt: `没问题，可以merge`
8. wait: MR/PR merged，intern 回到 Idle。
9. assert: merge event 晚于批准 prompt，target branch 前进，目标文件包含固定文本，未 direct push，task closeout 完成，未额外发 AskUser 决策卡。

关键失败面：

- CLI assignment 替代飞书 prompt。
- protected repo 被当作 local、错误 provider 或非保护 repo。
- 未批准就 merge，或 direct push 绕过 MR/PR。
- metadata 混入业务 MR/PR。

### `J_0007` card interaction

覆盖目标：真实 intern 飞书群内 request_user_input/card callback 的用户可操作路径。

准备动作：

- cleanup 本 case namespace。
- 使用 lead 预置 local workspace 创建 codex intern 和 session。
- 断言 workspace 是 `local_only`，session ready。

剧本：

1. prompt: 让 intern 发只有 A/B 两个选项的卡片。
2. wait/assert: 只出现一张待回答卡，卡片按钮是 A/B。
3. action: 点击 A。
4. wait/assert: callback 成功，pending question 被 A 回答并清理，intern 回复 A 且不包含 B。
5. prompt: 让 intern 发 A/B 加自由输入的卡片。
6. wait/assert: 只出现一张待回答卡，卡片有 A/B 和自由输入。
7. action: 表单提交 `输出C`。
8. wait/assert: pending question 答案是 `输出C`，intern 回复 C 且不误判为 A/B。
9. prompt: 让 intern 在一张卡里同时提供 A/B/C 单选和 X/Y/Z 多选。
10. action: 表单提交单选 B、多选 X/Z。
11. wait/assert: callback 成功，intern 回复 B、X、Z，pending question 不残留。

实现前置：

- `action_click_card_button`
- `action_submit_card_form`
- `wait_for_question_card`
- `wait_for_card_callback_applied`
- `assert_single_question_card`
- `assert_card_button_options`
- `assert_card_has_free_input`
- `assert_card_form_questions`
- `assert_pending_question_answered`

关键失败面：

- 同一 prompt 发多张待回答卡。
- 卡片缺按钮、自由输入、单选或多选控件。
- CI mock 无法表达真实用户可完成的点击/表单动作。
- callback 成功但 intern 没拿到答案，或 pending 未清理。

### `J_0008` stop / screenshot

覆盖目标：真实 intern 飞书群中的 `/stop` 和 `/screenshot` 用户路径，以及 stop 后 continuation 不应额外注入旧 full prompt/full context。

准备动作：

- cleanup 本 case namespace。
- 准备或校验 `scripts/j0008_slow_probe.sh`，它应输出 `J0008_PROBE_STARTED` 并运行足够久。
- 使用 lead 预置 local workspace 创建 codex intern 和 session。
- 断言 workspace 是 `local_only`，session ready。

剧本：

1. prompt: 要求运行 `scripts/j0008_slow_probe.sh` 并持续关注执行过程。
2. wait/assert: turn started，turn input 包含完整用户 prompt，probe 已输出 started token。
3. action: 发送 `/stop`。
4. wait/assert: stop delivered，指定 turn 被停止，session 后续仍可接收 prompt。
5. prompt: `你继续`
6. wait/assert: 新 turn started 并有 intern 回复；turn input 包含 `你继续`，不包含被 stop 的完整长 prompt、`additionalContext` 或 `full additional context` 标识；回复仍围绕 probe 继续。
7. action: 发送 `/screenshot`。
8. wait/assert: 群里出现图片消息，有 image key 和图片 mime type，不能只是文本失败。

实现前置：

- `action_prepare_long_running_probe`
- `action_send_slash_command`
- `wait_for_session_turn_started`
- `wait_for_stop_applied`
- `wait_for_screenshot_image`
- `assert_turn_input_contains`
- `assert_turn_input_excludes`
- `assert_continuation_after_stop`
- `assert_screenshot_image_message`
- `assert_no_screenshot_text_only_failure`

关键失败面：

- `/stop` 无反馈、未停止原 turn 或 stop 后 session 卡死。
- `你继续` 被额外塞入完整旧 prompt 或 full additional context。
- `/screenshot` 没有图片消息、发错群、无 image key 或只返回文本错误。

### `J_0009` / `J_0010` / `J_0011` / `J_0012` code-informed additions

这些是 Session 6 结合当前 CLI/插件代码和历史任务补出的 ReviewDraft：

- `J_0009` 覆盖 Codex goal lifecycle：`/goal` active、Replace goal 确认卡、Cancel/Replace 分支、`/goal status`、`/goal clear` 和 lifecycle 去重。
- `J_0010` 覆盖 Team Mode 真实委派：coordinator、team lead、worker 角色不扁平化，team lead 创建 worker task，peer-send 通知 worker，worker 真实接受并回复。
- `J_0011` 已在 Session 12 folded into `J_0090`，不再作为单独实现入口；outgoing rich content split 随 `J_0090` 的 artifact delivery bundle 覆盖。
- `J_0012` 覆盖 builtin skills 的真实可用性：新 intern 不需要 repo/personal skill config；compaction/resume 后仍能使用 `feishu-messaging`、`peer-send`、`goal-send`。

这组不替代对应 F：

- Builtin skill 文件/TreeView/protection 合约应留在 F，当前 ReviewDraft 是 `F_0054`；setup 和 Copilot intern 相关场景不再补写。
- Team metadata rollback、contextValue、CLI command parity 仍应由 F 覆盖；`J_0010` 只验证用户看到的跨 intern 委派旅程。
- Rich content hook/daemon buffer 细节仍可由 F/单元测试覆盖；`J_0090` 只接受真实 agent artifact send 证据。

### `J_0013` / `J_0015` / `J_0016` / `J_0017` / `J_0018` / `J_0019` / `J_0020` live-turn additions

这些是 Session 7 继续按当前代码和日常主管工作流补出的 ReviewDraft：

- `J_0013` 覆盖 Codex skill 真实可见性：repo skill enabled 时 agent 能按 skill 输出，repo disabled 后不能继续声称成功使用，personal skill 只对目标 intern 生效。
- `J_0015` 覆盖 Feishu group mode 对 live turn 的影响：`trigger_mode=all`、`at_only`、`detail_mode=summary/full`、`no_collapse_mode=on` 都必须由真实 turn/timeline 证明。
- `J_0016` 覆盖 review feedback loop：已有 MR/PR 在 merge 前收到修改意见后更新同一个 change request，target branch 直到明确批准后才前进。
- `J_0017` 覆盖 Claude plan approval：plan card 出现后 free-text refine 不执行，只有明确选择 review/accept edits 后才允许按最新计划改文件。
- `J_0018` 覆盖运行中追加指令：Codex 长 turn active 时收到新用户消息，最终回复遵循最新指令并记录 supplement evidence。
- `J_0019` 覆盖 pending restart live conversation：Codex 正在 Working 时出现 policy/env restart requirement，先进入 pending，原 turn 完成后 restart，用户仍能在同一群继续对话。
- `J_0020` 覆盖 machine helper live diagnosis：helper 从真实 helper surface 启动后能在 helper 群回复诊断，invite-owner 可见，stop/restart 不留下 stale chat id。

这组不替代对应 F：

- `J_0013` 不替代 `F_0030`/`F_0054` 的 skill 文件、TreeView、farm、保护和 tamper-restore contract。
- `J_0015` 不替代 `/config` slash/card/source contract；它只验证配置已经生效到真实用户会话。
- `J_0017` 不替代 card formatting 或 callback route F；它只验证真实 Claude plan/refine/approve 旅程。
- `J_0018` 不替代 hook payload/source contract；它只验证 active turn 能接收并遵循最新用户补充。
- `J_0019` 不替代 `F_0034` 的 policy sync/Idle restart/source contract；它只验证 Working 会话的用户可见连续性。
- `J_0020` 不替代 `/helper` card/API F；它只验证 helper runtime 真正可对话以及 stop/restart 后仍可用。
- Team create rollback/retry 不要求 agent 自然语言，已拆为 F candidate `F_0055`，不写成 J。
- setup 和 Copilot intern 相关场景不再补写：setup 当前不可测，Copilot intern 不继续维护。

### `J_0021` 到 `J_0032` attachment / clarification / scope additions

这些是 Session 8 按附件、pending question、peer/goal scope、compaction 和 helper 安全补出的 ReviewDraft：

- `J_0021` 覆盖 inbound attachment bundle：Markdown 发布说明、图片 token、attachment-only CSV、多附件/富文本 post，要求真实 Feishu 附件、明确 wait boundary、inbox/pending 状态和 agent 回复证据；`J_0022`/`J_0023` 已 folded。
- `J_0024` 已 Retired：模糊指令下是否主动澄清/发卡属于 agent/prompt 行为；`J_0025` 仍只保留为单独 review draft，不 folded 到 `J_0024`。
- `J_0026` 到 `J_0028` 覆盖协作 scope：busy receiver next-turn peer、同名 intern 跨 project 隔离、goal-send 权限边界。
- `J_0029` 和 `J_0030` 覆盖会话恢复：compaction 后继续同一 task，`/esc` 清掉 pending 后新 prompt 不受污染。
- `J_0031` 覆盖 intern 自然使用 Feishu group member capability，并要求 raw ids/secrets redacted。
- `J_0032` 覆盖 machine helper 敏感操作审批：helper 必须 AskUser/request_user_input，用户拒绝后走只读 fallback。

这组不替代对应 F：

- 附件 extract/download/persist、pending state schema 和 prompt hook source contract 仍属于 F；J 只验证用户发送附件后 agent 能用。
- pending question card rendering/callback route 仍属于 F；J 只验证真实 agent 决策闭环。
- peer/goal API permission matrix 仍属于 F；J 只验证 agent 自然使用 skill 后的可见结果。
- helper card/API/registry 仍属于 F；J 只验证 helper 群真实对话和用户审批安全。

### `J_0034` 到 `J_0044` stale-card / restart / Claude additions

这些也是 Session 8 补出的 ReviewDraft，继续扩展用户会话恢复和 backend 差异：

- `J_0034` 和 `J_0035` 已 folded into `J_0021` Phase 4，不再作为独立附件维度实现。
- `J_0036` 到 `J_0039` 扩展 pending question 生命周期：timeout re-ask、replacement stale isolation、普通文本回答、`/stop` 清 pending question。
- `J_0040` 覆盖 Codex idle restart 后同一 Feishu group 继续可用。
- `J_0041` 覆盖 main-bot readonly status 到真实 intern group follow-up 的一致性。
- `J_0042` 覆盖 Claude plan cancel/deny negative path，补足 `J_0017` approve path。
- `J_0044` 覆盖 Claude group mode live-turn，补足 F_0045 的配置合约。

这组不替代对应 F：

- 多附件/rich post 的 Feishu 解析仍属于 F；J 只验证 agent 对用户发送内容的使用。
- stale/timeout/replacement callback 合约仍属于 F；J 只验证用户实际点击旧卡、新卡和普通文本答复后的对话结果。
- session restart command shape 仍属于 F；J 只验证 restart 后同群 follow-up。
- main-bot readonly command contract 仍属于 F；J 只验证用户用 readonly status 找到正确 intern group 后能继续。
- Claude group config parity 仍属于 F；J 只验证真实 Claude turn 行为。

### `J_0046` 到 `J_0065` supervisor / coding-control additions

这些是 Session 9 继续朝 100 个 J 剧本补出的 ReviewDraft：

- `J_0046` 到 `J_0050` 覆盖 protected task supervision：open PR no-merge、merge approval phrasing、failed-test update、rebase request、active task cancellation。
- `J_0051` 到 `J_0054` 覆盖 conversation mode controls：review-only、explanation-only/no-tools、dirty worktree preservation、stop-hook checklist correction。
- `J_0055` 和 `J_0056` 已 Retired；`J_0057`/`J_0058` 仅保留为内置 prompt lookup policy coverage。
- `J_0059`/`J_0060`/`J_0062`-`J_0065` 已 Retired；`J_0061` 保留为 running supplement/product delivery path。

这组不替代对应 F：

- Provider branch/MR/PR API contracts and revision parsing remain F; J validates natural supervisor instructions and agent restraint.
- Tool/event logging and diff classifiers can be F helpers; J validates user-facing instruction following.
- Stop-hook template unit tests remain F/hook coverage; J validates final visible recovery.
- External lookup implementation remains product/tooling; J validates user-requested browsing/no-browsing behavior through transcript evidence.

### `J_0066` 到 `J_0073` team / cross-intern additions

这些是 Session 9 继续按 Team Mode 和 intern-to-intern 日常协作补出的 ReviewDraft：

- `J_0066` 到 `J_0071` 覆盖 multi-worker delegation、clarification、failure escalation、reassignment、busy status rollup 和 cross-team permission boundary。
- `J_0072` 和 `J_0073` 覆盖 peer 消息中附件能力边界和 artifact review 结果回传。

这组不替代 Team/peer API F：metadata、permission matrix、TreeView projection、raw peer delivery contract 仍属于 F；J 只验证真实 agent 对话与用户可见协作结果。

### `J_0074` 到 `J_0085` interaction-control additions

这些是 Session 9 按 supervisor 对输出、风险、repro、rollback 和 closeout 的控制补出的 ReviewDraft：

- `J_0074` 覆盖 no-collapse 顺序；`J_0075`-`J_0078` 已 Retired 为通用输出/规划行为。
- `J_0079` 和 `J_0080` 覆盖 risky command disclosure 与 explicit approved scoped cleanup。
- `J_0081`/`J_0082`/`J_0084`/`J_0085` 已 Retired；`J_0083` 仅保留为内置 prompt 的 own-change rollback/user-change preservation coverage。

这组不替代 format/diff/log F helper：J 只验证用户指令如何影响真实最终回复和实际文件/branch 状态。

### `J_0086` 到 `J_0100` goal / recovery / delivery continuity additions

这些是 Session 9 补到 100 个 J 剧本的 ReviewDraft，聚焦长期会话和故障恢复下用户实际看到的结果：

- `J_0086` 和 `J_0087` 覆盖 goal footer stale prevention、missing-value blocked goal resume。
- `J_0088` 覆盖 case-scoped compaction no-noise；`J_0089` 已 Retired，不再覆盖坏 hook state recovery。
- `J_0090` 覆盖 outgoing artifact delivery bundle；`J_0091` 已 Retired，不再覆盖 generic scoped evidence path closeout。
- `J_0092` 到 `J_0095` 覆盖 same-name session isolation、coordinator owner-filter、no-team refusal 和 coordinator task handoff。
- `J_0096` 和 `J_0097` 覆盖 machine helper saved-profile reconnect 与 backend fallback honesty。
- `J_0098` 到 `J_0100` 覆盖 summary readability、stop/supplement ordering 和 policy restart shell continuity。

这组不替代 low-level F/source contracts：goal API、hook parsers、Feishu transport limits、session maps、helper runtime selection 和 restart command shape 仍由 F/helper 验证；J 只验证真实 prompt/timeline/follow-up 体验。

## 第 6 阶段 workspace migration 独占组

`J_0003` 到 `J_0006` 的旧 root-level 草案已经 Retired：它们描述的 in-place workspace mode switch 不再是受支持流程，不能作为 J success path 重新启用。新的 `docs/J/J_0003.md` 到 `J_0006.md` 只覆盖显式 migration PR/MR + re-add/reuse journey。

禁止路径：

- `workspace mode set`
- daemon `/mode/set`
- relay `/mode/set`
- 原地 "change repo mode"

替代流程必须验证：

1. cleanup 本 case namespace。
2. delete globally 旧 relay workspace record。
3. action: `action_workspace_migrate_mode(repo_url, target_mode, metadata_branch)`。
4. wait/assert: migration PR/MR 已创建，base branch 正确，且包含真实 `.intern_workspace` metadata tree。
5. merge migration PR/MR。
6. action: `action_readd_workspace_after_migration(repo_url, target_mode)`。
7. assert: `assert_workspace_mode_migration_result(workspace_id, metadata_mode)`，确认 task/status/history/knowledge/skill metadata 可由目标 resolver 读取。

矩阵：

| ID | provider | 初始 mode | 切换后 mode | change request |
|----|----------|-----------|-------------|----------------|
| `J_0003` | GitHub | `repo_dotdir` | `metadata_branch` | PR |
| `J_0004` | Codeup | `repo_dotdir` | `metadata_branch` | MR |
| `J_0005` | GitHub | `metadata_branch` | `repo_dotdir` | PR |
| `J_0006` | Codeup | `metadata_branch` | `repo_dotdir` | MR |

关键失败面：

- mode 切换后 task2 丢失、assignee 错乱或无法分配。
- mode 切换导致 intern 群重建、换群或消息不可达。
- mode 切换修改 provider、repo 或 target branch。
- cleanup 删除或重建 lead 预置 workspace。

`local_only` 与远端 mode 之间的切入/切出不进入这些 J；拒绝路径已放在 `F_0051.md`。

## 继续补写入口

继续新增 J 时从 `J_0101` 开始。当前建议优先沿第 5 阶段补真实 intern 群用户路径，避免一上来扩展 workspace 级独占场景。每个新增 J 都应先写清：

- 是否第 5 阶段可并发，或第 6 阶段必须独占 workspace。
- lead 预置 workspace 名、provider、repo、metadata mode 和 backend。
- worker 可创建/清理的私有资源。
- prompt 是否是日常用户说法，不能暴露 case id、run id、provider、mode 或测试断言。
- 每个 wait 对应的异步状态，以及每个 assert 的客观 evidence。
- 如果 helper 缺失，标成实现前置，不删减真实用户可完成的动作。
- 对 workspace mode 相关故事，必须引用 migration PR/MR + re-add/reuse flow，不得写旧 in-place mode switch。
