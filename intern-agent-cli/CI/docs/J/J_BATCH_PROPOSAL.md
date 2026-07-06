# J batch proposal

<!-- METADATA:TYPE=J,STATUS=ReviewDraft,OWNER=intern_ci_lead,SESSION=12 -->

本文整理下一批建议推进的 J 类真实用户旅程用例。目标是先把边界和优先级讨论清楚，再按主管确认后的批次拆分实现；本 proposal 不等于分配 worker，也不等于声明已通过。

Session 12 consolidation rule: prefer broader user journeys over one J per small
feature point. When several small scripts describe the same user intent, fold
them into a multi-prompt bundle and keep low-level state/API invariants in F.
Current consolidation decisions:

- `J_0090` is the outgoing Feishu artifact delivery bundle and folds the old
  `J_0011` rich-content split draft.
- `J_0021` is the inbound attachment bundle and folds `J_0022`, `J_0023`,
  `J_0034`, and `J_0035`.
- Generic agent behavior is out of scope. Keep only plugin/product journeys and
  explicit built-in prompt contracts.
- Team/peer/control drafts should be proposed in scenario bundles, not as one
  card per tiny behavior.

作者规则以 [`J_SCRIPT_AUTHORING.md`](J_SCRIPT_AUTHORING.md) 为准。本文只提出批次建议；缺 helper 或产品能力时，必须标为实现前置或 product gap，不能把真实用户故事降级成低层 API/local fixture。

## J/F 边界

J 用例只覆盖真实用户旅程：需要给 intern 发送自然语言 prompt、等待 agent 回复、验证用户可见卡片/图片/上下文延续，或验证 prompt 触发后的 session resume 语义。

以下仍属于 F，不进入本批 J：

- 纯 CLI/API/registry/source contract。
- 只检查飞书群创建、绿灯、机器切换提示、callback route，但不触发 agent 工作。
- GUI 是 CLI wrapper 的路径；仍通过 CLI/source contract 验证。
- no-prompt restart：没有真实 agent turn 时允许 fresh start，只要求 session 启动成功；same-session UUID 由 J 覆盖。

## 资源命名规则

后续新 J case 必须显式带 J 前缀，避免与 F case 或未来 J case 资源冲突：

- namespace: `ci_j_XXXX`
- workspace: `ci_j_XXXX_workspace_<run_id>`
- intern: `intern_ci_j_XXXX_<backend>_<run_id>`
- task/resource prefix: `task_ci_j_XXXX`
- report/artifact prefix: `j_XXXX_<purpose>`

已经实现或已分配的旧资源命名不在本 proposal 内强制重命名。

## 当前已实现 J

| ID | 状态 | 覆盖 |
|----|------|------|
| `J_0014` | Implemented | live peer send：真实 sender/receiver session、receiver pane 可见、unknown/ambiguous/invalid mode 错误。 |
| `J_0033` | Implemented | Codex 真实 `hi` turn 后 `/exit` 输出 `Resume this intern`，执行提示命令和 GUI-equivalent restart 均保持同一 Codex UUID。 |

## 第一批建议推进

### [`J_0007` Real card interaction journey](J_0007.md)

来源：已有 root-level `J_0007` review draft，已整理为 `docs/J/J_0007.md` 的权威 ReviewDraft。

为什么是 J：需要 intern 根据用户 prompt 发卡片，CI 模拟真实用户点击/提交后，intern 必须拿到答案并自然回复；这不是单纯 callback API route。

核心路径：

1. 用户要求发 A/B 按钮卡，点击 A，intern 回复 A。
2. 用户要求发 A/B + 自由输入卡，提交 `输出C`，intern 回复 C。
3. 用户要求同一张卡里包含单选和多选，提交 B + X/Z，intern 回复 B、X、Z。

关键断言：

- 每个 prompt 只产生一张待回答卡。
- card controls 与 prompt 约束一致。
- callback 成功后 pending question 清理。
- intern 回复包含用户选择，且不包含未选择项。

实现前置：真实 Feishu card click/form submit helper、question card waiter、pending answer verifier。

### [`J_0008` Stop / continue / screenshot journey](J_0008.md)

来源：已有 root-level `J_0008` review draft，已整理为 `docs/J/J_0008.md` 的权威 ReviewDraft。

为什么是 J：需要用户启动一个真实长任务，运行中 `/stop`，再只说“你继续”，并验证 agent 的后续回复和 screenshot 图片消息。

核心路径：

1. 用户要求运行可控慢脚本 `scripts/j0008_slow_probe.sh`。
2. probe 输出 started token 后发送 `/stop`。
3. 用户只发送 `你继续`。
4. 断言新 turn 没有被塞入完整旧 prompt 或 full additional context，但 intern 能围绕同一 probe 继续。
5. 发送 `/screenshot`，群里必须出现图片消息。

关键断言：

- stop 命中正在运行的 turn，session 后续仍可接收 prompt。
- continuation turn input 只包含新 prompt，不重复注入旧长 prompt。
- screenshot 是图片消息，有 image key/mime，不是文本失败。

实现前置：turn input capture、stop waiter、screenshot image waiter。

### [`J_0001` Codeup protected lifecycle journey](J_0001.md)

来源：已有 root-level `J_0001` review draft，已整理为 `docs/J/J_0001.md` 的权威 ReviewDraft。

为什么是 J：assignment、continue、merge approval 都来自真实飞书用户 prompt；必须验证 agent 在 protected repo + `metadata_branch` 下走 MR 和 closeout，而不是 direct push 或 CLI assignment。

核心路径：

1. lead 预置 Codeup protected workspace 和未分配 task。
2. 用户 prompt：`分配任务 <task_id>`。
3. 用户 prompt：`继续`。
4. intern 打开 Codeup MR，业务 diff 不含 metadata。
5. 用户 prompt：`没问题，可以merge`。
6. MR merge 后 task closeout，intern 回 Idle。

关键断言：

- assignment evidence 指向真实 Feishu message。
- 未批准 merge 前 target branch 不前进。
- merge event 晚于批准 prompt。
- 业务 MR 不混入 `.intern_workspace` / status / history / task knowledge。

### [`J_0002` GitHub protected lifecycle journey](J_0002.md)

来源：已有 root-level `J_0002` review draft，已整理为 `docs/J/J_0002.md` 的权威 ReviewDraft。

与 `J_0001` 同路径，provider 替换为 GitHub protected PR。该 case 用于防止 Codeup-only 逻辑掩盖 GitHub provider 差异。

关键断言与 `J_0001` 相同，但 evidence 使用 GitHub PR、target branch 和 merge event。

### [`J_0043` Claude real-turn exit/resume same-session journey](J_0043.md)

新 proposal，对应 `F_0043` 的 J 补充，已整理为 `docs/J/J_0043.md` 的权威 ReviewDraft。

为什么是 J：`F_0043` 不发送业务 prompt，只验证 Claude intern lifecycle 和 no-prompt resume/restart contract。要证明“用户实际使用过 Claude 后，/exit 提示命令和 restart 能回到同一个 Claude 会话”，必须先发真实 prompt 并等待 Claude 回复。

核心路径：

1. 创建 `type=claude` intern 和 case-scoped Feishu group。
2. start Claude session，断言 group 绿灯。
3. 用户发送 `hi` + unique token，等待 Claude 回复该 token 或可验证的等价确认。
4. 从 durable Claude session source 捕获 UUID。
5. 用户在 Claude pane `/exit`，等待 tmux shell 输出 `Resume this intern`。
6. 断言提示命令包含 `internctl session resume <intern> --project <workspace> --type claude`。
7. 在同一 tmux pane 执行提示命令，等待 Claude live。
8. 断言 resumed UUID 等于真实 turn 后的 UUID。
9. 执行 GUI-equivalent `internctl session restart ... --type claude --no-attach`。
10. 断言 stdout 是 `restarted via resume <uuid>` 且 UUID 不变，最终 group 仍绿灯。

关键断言：

- `status/list/session map/group registry` 均为 `type=claude`。
- Claude 回复前不做 same-session UUID 要求；回复后必须能捕获 durable UUID。
- `/exit` 没有提示命令、提示命令不可执行、restart fresh、UUID 改变均为 product bug。
- Claude token/policy evidence 必须 redacted。

### [`J_0045` Claude skill visibility and use journey](J_0045.md)

新 proposal，对应 `F_0045` 的 J 补充，已整理为 `docs/J/J_0045.md` 的权威 ReviewDraft。

为什么是 J：`F_0045` 可以脚本化验证 `.claude/skills`、registry、TreeView projection 和 group mode，但“Claude intern 实际能看到并按 skill 工作”需要问 intern，属于 J。

核心路径：

1. 准备 case-scoped Claude skill，`SKILL.md` 要求在被使用时输出唯一 token 和固定格式。
2. 启用 repo skill 给 Claude intern，start session。
3. 用户 prompt：要求 Claude 使用该 skill 处理一个小输入。
4. 等待 Claude 回复。
5. 断言回复包含 skill token、固定格式和输入处理结果。
6. 禁用 repo skill 后要求 Claude 不能继续声称成功使用该 repo skill；再启用 personal skill，验证真实 agent 可见性恢复。

关键断言：

- enabled 时 Claude 能按 skill 明确约定输出。
- disabled 后 Claude 不能继续声称成功使用该 repo skill，personal enable 后可重新按 skill contract 输出。
- skill farm 仍由 F 验证；J 只验证真实 agent 可见性和用户结果。

实现前置：Claude real prompt/reply helper、skill fixture、可 redacted 的 Claude runtime evidence。

## Session 6 code-informed additions

这些新增草稿来自当前代码入口和近期任务复盘，不是已有 root-level J 的简单搬运。

### [`J_0009` Codex goal lifecycle and Feishu confirmation journey](J_0009.md)

为什么是 J：`/goal` 设置、Replace goal 确认卡、Cancel/Replace 分支和后续 lifecycle 去重都需要真实 Codex 目标状态与用户选择。

核心路径：

1. 用户发送 `/goal finish a small visible checklist`。
2. 普通 prompt 触发带 active goal 的后续回复。
3. 用户发送第二个 `/goal`，出现 Replace goal 确认卡。
4. Cancel 后旧 goal 保留；再次发送并 Replace 后新 goal 生效。
5. `/goal status` 和 `/goal clear` 验证最终状态。

关键断言：

- active goal evidence 来自 Codex pane/source/goal snapshot，不只看 pending text。
- Replace 确认必须等用户卡片选择。
- Cancel 不替换，Replace 只产生一次 lifecycle event。
- clear 后未来 turn 不复活旧 goal footer。

### [`J_0010` Team coordinator delegation journey](J_0010.md)

为什么是 J：Team Mode 的 CLI/TreeView 合约可以由 F 测，但用户真正需要的是“我让 coordinator 组织 team，worker 收到任务并回复”的跨 intern journey。

核心路径：

1. 创建 Codex coordinator、team lead、worker，并绑定 team。
2. 用户 prompt coordinator：请把小检查任务交给 runtime team。
3. coordinator/team lead 走 team assignment path 创建 worker task。
4. peer-send 通知 worker。
5. 用户 prompt worker 接受 task，worker 回复计划。
6. coordinator/team lead 汇总 worker 当前状态。

关键断言：

- TreeView 中 coordinator、team、lead、worker 不扁平化。
- worker task metadata 记录 team/lead/worker 关系。
- peer-send 是 daemon delivered，worker session 可见。
- worker acceptance/status/reply 指向同一 task。

实现前置：multi-intern prompt/reply helper、TreeView team snapshot、peer-send delivery waiter、safe Team Mode cleanup。

### [`J_0011` Feishu rich content natural split journey](J_0011.md)

Status: Retired/folded into `J_0090` during Session 12 consolidation.

为什么是 J：历史任务已经证明直接调 hook/daemon/API 不足以代表真实产品路径；必须让 intern 自然使用 `feishu-messaging` skill 后继续说话。

核心路径：

1. Prompt：先发一句短进展，再生成小 PNG 并用飞书发出，发完补一句下一步。
2. 断言 text-before、image message、text-after 是三个按顺序出现的 message id。
3. Prompt：重复一次小文件报告。
4. 断言 file message 后的后续文本也 rollover 到新 message id。

关键断言：

- rich content 由 agent 的 skill/tool output 触发，不是 runner 直接调用 daemon/API。
- image/file 前的文本不再被后续更新。
- image/file 后的文本是新的 Feishu message id。

实现前置：Feishu timeline helper、skill output parser、direct-injection guard。

### [`J_0012` Builtin skills compaction recovery journey](J_0012.md)

为什么是 J：F 可以证明 builtin skills 被同步到 farm；但 compaction 后 agent 是否还能发现并使用它们，需要真实 agent turn。

核心路径：

1. Fresh Codex intern 无 repo/personal skill config。
2. Prompt sender 用内置能力发送一个小 artifact 到飞书群。
3. 强制 compaction/resume。
4. Prompt sender 给 receiver 发 peer message。
5. Prompt coordinator 用 goal-send 设置并清除 worker goal。

关键断言：

- `feishu-messaging`、`peer-send`、`goal-send` 的效果均来自 agent 使用 builtin skill。
- compaction/resume 后仍可发现。
- peer/goal 作用域和权限边界正确。

实现前置：compaction/resume helper、skill-use evidence、receiver/goal source evidence、coordinator role fixture。

## Session 7 live-turn and review-flow additions

这些新增草稿继续按当前代码和日常主管工作流补齐真实用户旅程。它们都需要 live agent prompt/reply 或真实 Feishu card/timeline evidence，不能用本地 fixture 或低层 API 直接替代。

### [`J_0013` Codex skill visibility and use journey](J_0013.md)

为什么是 J：`F_0030`/`F_0054` 能证明 skill 源、farm 和保护 contract，但不能证明 Codex 真正在用户 prompt 中看到并按 skill 工作。

核心路径：

1. 启用 deterministic repo skill。
2. Prompt Codex 使用该 skill 处理 `alpha`。
3. repo disable 后再要求使用，不能继续声称成功。
4. personal enable 到目标 intern 后再次使用成功。
5. comparison intern 不应看到 personal skill。

关键断言：

- enabled repo skill 回复包含固定 token、输入和处理结果。
- disabled repo skill 不再产出成功使用 token。
- personal skill 只对目标 intern 生效。
- farm/config evidence 与真实回复一致。

实现前置：Codex skill-use helper、skill fixture、必要时的 skill sync/restart helper。

### [`J_0015` Feishu group mode live-turn journey](J_0015.md)

为什么是 J：`/config` 和 group config contract 属于 F；配置是否改变真实 Codex 会话的唤醒、详情渲染和长消息行为，需要 J。

核心路径：

1. `trigger_mode=all` 下普通消息唤醒 Codex。
2. `trigger_mode=at_only` 下普通群聊不唤醒。
3. @/明确指定 intern 后可以唤醒。
4. `detail_mode=summary` 与 `full` 对 tool-backed turn timeline 有可见差异。
5. `no_collapse_mode=on` 的长回复以有序 continuation messages 出现。

关键断言：

- no-turn window 能证明 at-only 普通群聊没有启动 turn。
- addressed message 在 at-only 下会启动 turn。
- summary/full/no-collapse 都用 Feishu timeline evidence 验证。

实现前置：mention/addressed-message driver、bounded no-turn waiter、timeline classifier。

### [`J_0016` Review feedback update-before-merge journey](J_0016.md)

为什么是 J：protected lifecycle 的直通路径已有 `J_0001`/`J_0002`，但主管常见的“先 review 要求修改，再批准 merge”需要真实对话和 provider change request evidence。

核心路径：

1. Prompt 分配 protected repo 小任务并继续。
2. intern 打开一个 MR/PR，target branch 不变。
3. Prompt 要求把内容改成另一句，并明确不要 merge。
4. 同一个 MR/PR 收到新 revision，未开重复 change request。
5. Prompt 明确批准 merge。

关键断言：

- review feedback 更新同一 change request。
- target branch 在批准前不前进。
- merge 后内容是 revised 版本，业务 diff 不含 metadata。

实现前置：provider revision evidence、same MR/PR update detector、before/after diff guard。

### [`J_0017` Plan approval refine-card journey](J_0017.md)

为什么是 J：card 格式和 callback 解析可以由 F 覆盖，但用户读计划、自由回复 refine、再明确批准执行的监督流程必须走真实 Claude turn 和 Feishu card。

核心路径：

1. Prompt Claude 先计划，等确认后再编辑一个 harmless 文件。
2. Feishu 出现 plan approval card，且文件未变。
3. 选择自由回复，要求收窄计划。
4. Claude refine plan，仍不编辑。
5. 选择 `reviewEdits` 或 `acceptEdits` 后才执行。

关键断言：

- free-text refine 不是 approval。
- approval 作用于最新/refined plan。
- 只有目标文件发生一行预期修改。

实现前置：ExitPlanMode/plan-card waiter、free-reply submit helper、file diff guard、redacted Claude evidence。

### [`J_0018` Running supplement latest-instruction journey](J_0018.md)

为什么是 J：运行中补充指令是 live turn 语义；F 可以验证 payload/source contract，但不能证明真实 agent 最终按最新用户消息回复。

核心路径：

1. Prompt Codex 做一个慢的只读检查，并要求最终用 bullet list。
2. turn active 时发送补充消息，改成 markdown table，并加入新 token。
3. 等最终回复。

关键断言：

- supplement 发生在 active turn 期间。
- 最终回复包含 supplement token 且是 table-like，不只是旧 bullet-only 要求。
- report/transcript 把 supplement message id 关联到 active turn。
- 没有后续 stale final 覆盖正确回复。

实现前置：active-turn waiter、running-supplement sender、transcript/timeline extractor、format classifier。

### [`J_0019` Pending restart live conversation journey](J_0019.md)

为什么是 J：`F_0034` 覆盖 policy sync、env materialization、Idle restart 和 replay de-dupe；但用户真实在群里和 Codex 对话时，restart requirement 应先 pending，不应杀掉正在运行的 turn。

核心路径：

1. Prompt Codex 做一个稍慢的只读检查。
2. turn active 时触发 case-scoped policy/env update，要求 Codex restart。
3. 断言目标 intern 进入 pending restart，而原 turn 继续运行。
4. 原 turn 完成后，pending restart 自动完成。
5. Prompt 同一 Feishu group，Codex 重启后仍可回复。

关键断言：

- policy marker 触发后 Working turn 不被杀。
- pending/restarted progress 与目标 intern、policy marker 关联。
- restart 完成晚于原 turn final answer。
- follow-up 仍在同一 group/chat/project/intern scope。

实现前置：active-turn waiter、safe policy marker driver、restart progress collector、same-group follow-up verifier。

### [`J_0020` Machine helper live diagnosis journey](J_0020.md)

为什么是 J：`/helper` 卡片、callback、daemon registry、stale chat id 等可以由 F 覆盖；但用户真正关心的是 helper start 后能不能在 helper 群里和一个真实 helper 对话。

核心路径：

1. 在普通 intern 群通过 `/helper start` 启动可用 debug machine 的 helper。
2. 进入 helper 群，Prompt 一个 harmless read-only diagnosis。
3. helper 回复 diagnosis token、机器上下文和安全摘要，不泄露 secret。
4. 通过 `/helper invite-owner` 验证 owner 可见性。
5. `/helper stop` 后再 `/helper start`。
6. 新/重绑定 helper 群能回复 follow-up，不复用 deleted/stale chat id。

关键断言：

- helper start 产生真实可用 helper runtime/group。
- helper 回复来自 helper group 对话，不是 normal group 的低层 API 回显。
- invite-owner 只影响目标 helper group。
- stop/restart 后 active binding 有效且不 stale。

实现前置：真实 helper card action driver 或等价 source-driver、helper group timeline reader、machine helper runtime/session evidence、secret redaction classifier。

## Session 8 attachment / decision / scope additions

这批继续从当前代码和历史 bug/task 里挑 J 级真实用户旅程。纯附件下载、pending state schema、peer/goal API matrix、helper card/API 仍留给 F；这里都要求真实消息、agent prompt/reply、用户决策或 helper group 证据。

### [`J_0021` Feishu inbound attachment bundle journey](J_0021.md)

为什么是 J：附件 route/persist 可以由 F 覆盖，但用户真实会发送文件、图片、附件-only、多附件或富文本内容，并期待 intern 在明确业务任务里使用这些内容。

核心路径：

1. Phase 1：Markdown 发布说明附件 + 同条文字指令，要求输出 release token、两条风险、建议 owner。
2. Phase 2：图片附件 + 同条文字指令，要求读出顶部 token 和底部数字，或诚实说明不支持。
3. Phase 3：CSV 附件-only，先观察 receipt/idle/no-empty-turn，再用下一条 prompt 要求输出 high-priority 行数和 csv token。
4. Phase 4：两个附件或富文本 post + 同条文字指令，要求按发送顺序列 token 并指出 blocker。
5. 每个 phase 等回复、receipt 或 no-turn 窗口稳定后再进入下一段。

关键断言：同条附件+文字指令可消费，attachment-only 不启动空 turn，下一条明确 prompt 消费一次，图片看不到必须诚实，多附件/富文本顺序不乱，旧附件不跨 phase 复用。

### [`J_0022` Feishu image attachment inspection journey](J_0022.md)

Status: Retired/folded into `J_0021` during Session 12 consolidation.

图片 token 读取或诚实 unsupported 已作为 `J_0021` Phase 2 覆盖。

### [`J_0023` Attachment-only then text follow-up journey](J_0023.md)

Status: Retired/folded into `J_0021` during Session 12 consolidation.

Attachment-only no-empty-turn 和后续明确 prompt 消费已作为 `J_0021` Phase 3 覆盖。

### [`J_0024` Ambiguous user request clarification journey](J_0024.md)

Status: Retired during Session 12 review.

退休原因：该草案主要测试 agent 面对模糊指令时是否主动澄清/发卡。除非产品 prompt 明确要求这种行为，否则这是 agent/prompt 行为，不是当前 J 产品功能；通用卡片能力也已经由其他覆盖承担。

Do not fold `J_0025`/`J_0036`-`J_0039` into this rejected bundle.

### [`J_0025` Request-user-input duplicate suppression journey](J_0025.md)

为什么是 J：双卡 bug 的核心是用户看到多张卡、点击后状态错乱；需要真实卡片和回答链路。

核心路径：

1. Prompt Codex 发一个 A/B 决策。
2. Feishu 只出现一张有效 pending card。
3. 用户选择 A。
4. Codex 收到一次答案并继续。

关键断言：重复 PreTool/TUI 信号被 adopt/suppress；无重复 active card，无重复答案注入。

### [`J_0026` Busy receiver queued peer message journey](J_0026.md)

为什么是 J：用户要求“下轮告诉他”时，不能打断正在 Working 的 receiver；必须验证真实 sender/receiver 对话。

核心路径：

1. receiver 开始慢 turn。
2. sender 通过自然 prompt 给 receiver 发 next-turn message。
3. daemon 记录 queued，receiver 当前 turn 不被打断。
4. receiver turn 结束后看到 queued token。

关键断言：不 interrupt、queued 可见、receiver scope 正确。

### [`J_0027` Same-name cross-project peer scope journey](J_0027.md)

为什么是 J：compound key 可以由 F 测，但真实 agent 使用 peer-send 时不能把消息发给另一个 project 的同名 intern。

核心路径：

1. 两个 project 各有同名 receiver。
2. project A sender 给本项目 receiver 发 token。
3. project A receiver 收到，project B receiver 不收到。
4. project-less ambiguous path 拒绝或澄清。

关键断言：`(project, internName)` scope 可靠，歧义不静默选目标。

### [`J_0028` Goal-send permission boundary journey](J_0028.md)

为什么是 J：goal API 权限是 F；agent 自然使用 `goal-send` skill 时的成功/拒绝反馈是 J。

核心路径：

1. independent intern 尝试给 worker 设置 goal，被拒绝。
2. coordinator 给 worker 设置 goal，成功。
3. coordinator 清理该 goal。

关键断言：未授权不变更，授权可设置，clear 只清 case goal。

### [`J_0029` Compaction preserves task state journey](J_0029.md)

为什么是 J：compaction 后用户只说“继续”，intern 是否仍记得当前 task/branch 是真实会话体验。

核心路径：

1. Feishu 分配 tiny task。
2. intern 读 task 并回 plan。
3. force compaction/resume。
4. 用户只说继续。
5. intern 继续同一 task/branch。

关键断言：task identity、branch context、新用户指令都保留。

### [`J_0030` Slash esc clears pending input journey](J_0030.md)

为什么是 J：`/esc` 是用户从卡住的 pending input 恢复对话的路径。

核心路径：

1. 诱导一个 pending question/input。
2. 用户发 `/esc`。
3. pending 被清理。
4. 用户发新 prompt。
5. 新回复不带旧 pending 污染。

关键断言：session 仍可用，旧卡不能后续污染新 turn。

### [`J_0031` Feishu group member awareness journey](J_0031.md)

为什么是 J：member-listing 脚本是 F；intern 自然使用该能力给用户友好摘要是 J。

核心路径：

1. 用户问当前群有哪些成员/owner 是否在。
2. intern 使用 group member capability。
3. 回复成员摘要和 token。

关键断言：结果来自真实 member baseline，不泄露 raw `ou_`/`oc_`/secret。

### [`J_0032` Machine helper sensitive action approval journey](J_0032.md)

为什么是 J：helper prompt/source 可由 F 看，但“遇到凭据/敏感动作先问用户，用户拒绝后不继续”必须走真实 helper 对话。

核心路径：

1. 用户让 helper 做可能需要 token/ssh key 的诊断。
2. helper 发 AskUser/request_user_input 说明风险。
3. 用户拒绝。
4. helper 不执行敏感动作，转只读检查。

关键断言：先问、deny 生效、不泄露/伪造 secret。

### [`J_0034` Multi-attachment ordered processing journey](J_0034.md)

Status: Retired/folded into `J_0021` during Session 12 consolidation.

Ordered multi-attachment token handling and blocker identification are covered
as `J_0021` Phase 4.

### [`J_0035` Feishu rich post mixed-content journey](J_0035.md)

Status: Retired/folded into `J_0021` during Session 12 consolidation.

Rich post text plus image/file ordering and honest unsupported image behavior
are covered as `J_0021` Phase 4.

### [`J_0036` Question card timeout and re-ask journey](J_0036.md)

为什么是 J：用户错过卡片后重新提问/回答是实际监督流程。

核心路径：生成 card -> 过期/stale -> 旧 card answer 被拒绝 -> intern re-ask -> fresh answer 生效。

关键断言：stale answer 不能改当前状态，fresh card 控制最终行为。

### [`J_0037` Replaced question stale-card isolation journey](J_0037.md)

为什么是 J：用户 refine 后旧卡还在飞书里，误点旧卡不能污染新问题。

核心路径：旧 question -> replacement question -> 点击旧卡 -> 旧 callback rejected/ignored -> 回答新卡 -> final 使用新答案。

关键断言：superseded card 不能回答新 pending question。

### [`J_0038` Pending question answered by normal text journey](J_0038.md)

为什么是 J：用户可能直接发文字答复，而不是点击卡片。

核心路径：free-text pending question -> 用户普通消息回答 -> pending 消费该文本 -> 同一文本不再作为新 prompt 重复处理。

关键断言：answer routing 和 prompt routing 不 double-route。

### [`J_0039` Stop while pending question journey](J_0039.md)

为什么是 J：`J_0008` 覆盖 running turn stop；这里覆盖 pending decision 卡住时 `/stop` 的用户恢复路径。

核心路径：pending card -> `/stop` -> card stale/cancelled -> late answer rejected -> fresh prompt 正常。

关键断言：stop 清 pending，late answer 不污染后续。

### [`J_0040` Idle session restart follow-up journey](J_0040.md)

为什么是 J：restart 命令输出是 F；用户关心重启后同一个飞书群还能不能继续聊。

核心路径：before prompt -> product restart idle session -> registry/group 不重复 -> same group follow-up -> after reply。

关键断言：group binding 连续、无 duplicate intern/group/session。

### [`J_0041` Main-bot status to group follow-up journey](J_0041.md)

为什么是 J：main-bot readonly status 是 F；用户用 status 找到 intern group 并继续是 J。

核心路径：main bot `/status`/equivalent -> no agent turn -> 用户在 reported group follow-up -> intern reply。

关键断言：readonly 不启动 agent，status group 与实际回答 group 一致。

### [`J_0042` Claude plan cancel no-edit journey](J_0042.md)

为什么是 J：`J_0017` 覆盖 refine/approve；cancel/deny 也是主管真实审批路径。

核心路径：Claude plan card -> user cancel/deny -> no file diff -> stale approve rejected -> fresh prompt works。

关键断言：cancel 不编辑，stale approval 不能执行旧计划。

### [`J_0044` Claude group mode live-turn journey](J_0044.md)

为什么是 J：`F_0045` 可证明配置 parity；真实 Claude turn 是否受 trigger/detail mode 影响需要 J。

核心路径：trigger all normal message wakes -> at-only normal chatter no turn -> addressed prompt wakes -> summary/full detail 可见差异。

关键断言：Claude live turn 与 group config evidence 一致。

## Session 9 supervisor and coding-control additions

这批继续补日常主管如何约束 agent 的真实旅程。多数 case 需要受控代码 fixture、provider change request 或 tool/diff evidence；纯 provider API、diff parser、tool log parser 仍留给 F/helper。

### Protected task supervision: `J_0046`-`J_0050`

- [`J_0046`](J_0046.md)：open PR/MR but no merge。用户明确说先等 review，intern 只能打开 change request 并等待。
- [`J_0047`](J_0047.md)：merge approval phrasing boundary。模糊夸赞不等于 merge approval，只有明确批准后才 merge。
- [`J_0048`](J_0048.md)：failed test review update。review 反馈 focused test failed 后，intern 更新同一个 MR/PR 并报告 rerun evidence。
- [`J_0049`](J_0049.md)：Retired。Session 10 review 判定 protected merge-flow 变体不再穷举。
- [`J_0050`](J_0050.md)：supervisor cancels active task。用户取消任务后，intern 不提交、不 merge，并保留/报告 partial state。

核心断言：所有 merge 都必须晚于明确批准；review feedback 更新同一 change request；target branch 在 no-merge/rebase/cancel 阶段不前进。

### Conversation mode controls: `J_0051`-`J_0054`

- [`J_0051`](J_0051.md)：Retired。Session 10 review 判定为通用 code review LLM 行为，不属于插件/内置 prompt J。
- [`J_0052`](J_0052.md)：Retired。Session 12 audit 判定为 generic no-tools/no-edits instruction，不属于产品/plugin/内置 prompt J。
- [`J_0053`](J_0053.md)：dirty worktree preservation。仅作为内置 prompt 合约覆盖：用户已有 unrelated dirty change，intern 不覆盖、不 stage、不 commit。
- [`J_0054`](J_0054.md)：stop-hook checklist correction。最终可见回复包含 checklist，hook correction 不改变 repo state。

核心断言：只保留内置 prompt 或 hook/product 合约；普通用户偏好不单独成 J。

### Instruction and evidence trust: `J_0055`-`J_0058`

- [`J_0055`](J_0055.md)：Retired。generic latest-instruction precedence；产品 running supplement 已由 `J_0018` 覆盖。
- [`J_0056`](J_0056.md)：Retired。generic evidence-path reporting 不作为独立 J。
- [`J_0057`](J_0057.md)：no web/latest verification。仅作为内置 prompt lookup policy 覆盖。
- [`J_0058`](J_0058.md)：latest verification。仅作为内置 prompt current/latest verification policy 覆盖。

核心断言：只测内置 prompt lookup policy；不要把通用“证据汇报质量”扩成 J。

### Coding style and reporting controls: `J_0059`-`J_0065`

- [`J_0059`](J_0059.md)：Retired。generic minimal-diff style。
- [`J_0060`](J_0060.md)：Retired。generic refactor-with-tests workflow。
- [`J_0061`](J_0061.md)：mid-turn status request then continue。
- [`J_0062`](J_0062.md)：Retired。generic read-only redirect instruction。
- [`J_0063`](J_0063.md)：Retired。generic patch-snippet application workflow。
- [`J_0064`](J_0064.md)：Retired。generic formatting preference。
- [`J_0065`](J_0065.md)：Retired。generic bilingual summary quality。

核心断言：`J_0061` 保留为 running supplement/product delivery path；其余通用 coding/reporting preferences 不作为 J。

### Team and cross-intern controls: `J_0066`-`J_0073`

- [`J_0066`](J_0066.md)：Team multi-worker delegation aggregation。coordinator 拆给两个 worker，最终汇总必须包含并归因两个 worker 的结果。
- [`J_0067`](J_0067.md)：worker clarification routed through coordinator。worker 需要缺失输入时，coordinator 向用户澄清，再把答案带回 worker。
- [`J_0068`](J_0068.md)：worker failure escalation。worker 命中受控失败后报告 blocker evidence，coordinator 不伪造成成功。
- [`J_0069`](J_0069.md)：worker reassignment after failure。第一个 worker 失败后，coordinator 明确重新分配给第二个 worker 并汇总替代结果。
- [`J_0070`](J_0070.md)：coordinator status rollup with busy worker。汇总时必须保留 busy/in-progress 状态，不能误报完成。
- [`J_0071`](J_0071.md)：cross-team permission boundary。跨 team 指挥必须拒绝或请求正确 routing，不能静默命中非所属 worker。
- [`J_0072`](J_0072.md)：peer message with attachment unsupported honesty。附件不能随 peer message 真实传递时，agent 要诚实说明能力边界。
- [`J_0073`](J_0073.md)：intern-to-intern artifact review。一个 intern 请求另一个 intern review artifact，结果回传给原用户。

核心断言：Team/peer 的结构性 contract 留给 F；J 只看真实 coordinator/worker/sender/receiver 对话、状态和用户可见汇总。

### Interaction-control additions: `J_0074`-`J_0085`

- [`J_0074`](J_0074.md)：long no-collapse follow-up ordering。长输出分多条时顺序和 continuation 完整。
- [`J_0075`](J_0075.md)：Retired。generic language preference。
- [`J_0076`](J_0076.md)：Retired。generic strict table output。
- [`J_0077`](J_0077.md)：Retired。generic typo correction。
- [`J_0078`](J_0078.md)：Retired。generic compare-before-act planning。
- [`J_0079`](J_0079.md)：risk disclosure before destructive command。仅作为内置 prompt destructive-command safety 覆盖。
- [`J_0080`](J_0080.md)：approved scoped cleanup。仅作为内置 prompt cleanup scope safety 覆盖。
- [`J_0081`](J_0081.md)：Retired。generic reproduction-only request。
- [`J_0082`](J_0082.md)：Retired。generic repro-to-fix workflow。
- [`J_0083`](J_0083.md)：rollback only intern's last change。仅作为内置 prompt own-change rollback/user-change preservation 覆盖。
- [`J_0084`](J_0084.md)：Retired。generic branch status summary。
- [`J_0085`](J_0085.md)：Retired。generic concise final answer style。

核心断言：只保留 no-collapse 产品机制和内置 prompt safety/rollback 合约；通用输出偏好、repro/fix 和 closeout 风格退掉。

### Goal, recovery, delivery, and continuity: `J_0086`-`J_0100`

- [`J_0086`](J_0086.md)：goal footer visible state after replace and clear。使用 missing-marker 耗时 goal，验证 Feishu footer/footnote 和 `/goal status` 不复活旧 goal。
- [`J_0087`](J_0087.md)：blocked goal resume continuation。missing-value goal 先 blocked，主管补值后 resume 同一目标生命周期。
- [`J_0088`](J_0088.md)：Codex auto-compaction Feishu noise suppression。case-scoped compaction 后，内部 summary 不作为 Feishu 用户答案泄漏。
- [`J_0089`](J_0089.md)：Retired。Session 12 review 判定 bad hook state recovery 修法空间过多，且 intern 不应判断本地 hook state 正确性；未来如需覆盖应拆到 F/hook invariant。
- [`J_0090`](J_0090.md)：Feishu artifact delivery bundle。多轮 prompt 覆盖小图片、小文件真实发送与 oversized 失败 notice/fallback；已 folded `J_0011` outgoing rich-content split。
- [`J_0091`](J_0091.md)：Retired。generic failure evidence-path reporting 不作为独立 J；project/session isolation 由 `J_0092` 覆盖，path-scope helper 留给 F。
- [`J_0092`](J_0092.md)：same-name intern session isolation。跨 project 同名 intern 不串群、不串 session。
- [`J_0093`](J_0093.md)：coordinator owner-filter status。coordinator 只汇总 owned team/worker。
- [`J_0094`](J_0094.md)：no-team delegation refusal。independent intern 不伪造不存在的 team delegation。
- [`J_0095`](J_0095.md)：coordinator workspace task handoff。coordinator 创建/选择 task 并 handoff 给 team lead。
- [`J_0096`](J_0096.md)：machine helper saved profile reconnect。helper stop/reconnect 后不静默换机器。
- [`J_0097`](J_0097.md)：helper backend fallback honesty。preferred backend 不可用时 fallback/blocked claim 与 runtime 一致。
- [`J_0098`](J_0098.md)：detail-mode summary readability。真实 tool-heavy turn 在 summary 模式下仍可读。
- [`J_0099`](J_0099.md)：stop during supplement finalization。near-final supplement 后 `/stop` 不被 stale final 覆盖。
- [`J_0100`](J_0100.md)：policy restart resume shell continuity。policy restart/resume 后同群仍回到 agent-ready。

核心断言：low-level goal/hook/session/helper/transport contract 仍留给 F；J 只看真实 Feishu timeline、prompt/reply、follow-up 可用性和产品/内置合约可见结果。通用 closeout/reporting 不单独成 J。

## F-level candidates split out

以下功能面不需要 agent 自然语言工作，已补为 F ReviewDraft，后续应按 F 任务分发而不是塞进 J：

- [`F_0054`](../F/F_0054.md)：builtin `peer-send` / `goal-send` / `feishu-messaging` 默认 farm、TreeView hiding、protected mutation、tamper restore contract。
- [`F_0055`](../F/F_0055.md)：Team create Phase 2 失败 rollback、ghost team 清理、retry 成功和 force delete scope contract。

不再补写 setup 和 Copilot intern 相关剧本：setup 当前不可测，Copilot intern 后续不准备继续维护。

## 迁移类独占批次

### `J_0003`-`J_0006` workspace mode migration journeys

这些编号的旧 root-level 草案已经 `Retired`，不能恢复为 in-place workspace mode switch。新的 replacement ReviewDraft 已写入：

- [`J_0003`](J_0003.md)：GitHub `repo_dotdir -> metadata_branch` migration PR + re-add/reuse。
- [`J_0004`](J_0004.md)：Codeup `repo_dotdir -> metadata_branch` migration MR + re-add/reuse。
- [`J_0005`](J_0005.md)：GitHub `metadata_branch -> repo_dotdir` migration PR + re-add/reuse。
- [`J_0006`](J_0006.md)：Codeup `metadata_branch -> repo_dotdir` migration MR + re-add/reuse。

这些 case 仍是第 6 阶段独占 workspace journey，执行前需要主管确认测试 repo、target branch、metadata branch、release/re-add 策略和真实 merge 权限。

禁止成功路径：

- `workspace mode set`
- daemon `/mode/set`
- relay `/mode/set`
- 原地 "change repo mode"

允许的迁移故事必须是：

1. delete/release 旧 relay workspace record。
2. action: `action_workspace_migrate_mode(repo_url, target_mode, metadata_branch)`，等价产品入口为 `internctl workspace migrate-mode --repo-url <repo> --target <repo_dotdir|metadata_branch>`。
3. wait/assert: migration PR/MR 已创建，base branch 正确，且包含真实 `.intern_workspace` metadata tree。
4. merge migration PR/MR。
5. action: `action_readd_workspace_after_migration(repo_url, target_mode)`。
6. assert: `assert_workspace_mode_migration_result(workspace_id, metadata_mode)`，确认 task/status/history/knowledge/skill metadata 可由目标 resolver 读取。

`local_only` 不能在 J 中迁入或迁出 remote modes；拒绝路径属于 F。

### Real slash/config card journeys

`F_0041`、`F_0035`、`F_0036` 已覆盖真实 Feishu ingress、card cancel/no-mutation、policy sync 等 API/用户可见控件 contract。如果不触发 agent 自然语言工作，仍留在 F；只有当用例要求 intern 根据用户卡片结果继续推理或回复时，才新增 J。

## 建议执行顺序

1. 先补 helpers：Feishu card click/form submit、turn input capture、screenshot image waiter、Claude/Codex prompt-reply helper、mention/no-turn waiter、timeline classifier、running supplement evidence、attachment upload/download evidence、rich-post driver、multi-intern scope evidence、stale-card evidence、provider revision guards、tool/no-edit guards、diff classifiers。
2. 并发实现低副作用 live-turn bundles：`J_0007`/`J_0008`/`J_0009` card-stop-goal bundle、`J_0013`/`J_0015` skill/group-mode bundle、`J_0018`/`J_0030`/`J_0036`-`J_0039` active-turn and pending-question bundle、`J_0021`-`J_0023`/`J_0034`/`J_0035` inbound attachment bundle、`J_0041`/`J_0043`/`J_0045` session/provider bundle, and `J_0090` outgoing artifact bundle.
3. 再实现 protected lifecycle/review-flow：`J_0001`、`J_0002`、`J_0016`，因为它们会触发真实 MR/PR merge，需要更严格的 cleanup 和审批模拟。
4. plan approval card helper 稳定后实现 `J_0017`，确保 free-text refine 与 explicit approval 可区分。
5. helper live group driver 稳定后实现 `J_0020`，避免误用低层 helper API 代替真实 helper 对话。
6. safe policy marker 与 restart progress helper 稳定后实现 `J_0019`，避免污染共享 provider/policy 状态。
7. helper AskUser 安全驱动稳定后实现 `J_0032`。
8. Team Mode/multi-intern helpers 稳定后再实现 `J_0010`、`J_0026`、`J_0027`、`J_0028`，避免 partial team/group side effect 污染共享环境。
9. compaction/resume helper 稳定后再实现 `J_0012`、`J_0029`。
10. session restart/group continuity helper 稳定后实现 `J_0040`。
11. Claude prompt/card/group helpers 稳定后实现 `J_0017`、`J_0042`、`J_0044`。
12. built-in prompt / hook fixtures 稳定后只实现保留项：`J_0053`、`J_0054`、`J_0057`、`J_0058`、`J_0061`；`J_0051`/`J_0052`/`J_0055`/`J_0056`/`J_0059`/`J_0060`/`J_0062`-`J_0065` 已 Retired。
13. Team/multi-intern helpers 稳定后继续实现 `J_0066`-`J_0073`，注意不要把 raw peer/team API matrix 当成 J pass evidence。
14. interaction-control helpers 稳定后只实现保留项：`J_0074`、`J_0079`、`J_0080`、`J_0083`；`J_0075`-`J_0078`、`J_0081`、`J_0082`、`J_0084`、`J_0085` 已 Retired。
15. goal/compaction helpers 稳定后实现 `J_0086`-`J_0088`；`J_0089` 已 Retired，不作为 J 实现。
16. delivery/evidence/session isolation/team owner/helper continuity/restart helpers 稳定后实现 `J_0090`-`J_0100` as bundled journeys, not single-feature scripts.
17. 主管确认独占测试 repo 和 merge/re-add 策略后，再实现 `J_0003`-`J_0006` migration PR/MR + re-add/reuse 矩阵。

## Open review points

- `J_0001`/`J_0002` 是否允许真实 merge 到测试 repo target branch；如果不允许，需要预置 throwaway protected repo。
- `J_0010` 是否允许在 J 中创建完整 team，还是先补 F 覆盖 Team Mode CLI/TreeView/rollback 后再上 J。
- `J_0012` 的 compaction/resume helper 应以真实 Codex compaction 操作为准，还是允许使用 accepted source-contract boundary。
- `J_0015` 的 @/mention driver 是否可用；如果只能注入低层 message payload，需要先补等价性证据。
- `J_0017` 的 plan approval card 是否能稳定通过真实 Feishu callback 驱动自由回复和 approve choices。
- `J_0019` 的 policy/env marker driver 是否足够 case-scoped；如果只能改共享 provider config，需要先补隔离方案。
- `J_0020` 的 helper start/stop/invite-owner driver 是否能触达真实 relay-local helper branches；如果只能打 daemon low-level API，需要先补等价性证据。
- `J_0021` inbound attachment bundle 的真实 Feishu attachment driver 是否可稳定下载 file/image/rich-post content；若只能构造 daemon payload，需要先补等价性证据。
- `J_0027` 的 multi-project fixture 是否允许并行创建同名 intern；若 workspace 资源不足，需要 lead 预置两个 throwaway workspaces。
- `J_0032` 的 helper AskUser path 是否能稳定触发；若 helper模型不主动问，需要改 prompt fixture 但不能让测试把敏感信息直接塞给 helper。
- `J_0036`/`J_0037` stale-card 是否允许缩短 timeout；如果不能，优先用 replacement/stale control 而不是让 CI 长时间等待。
- `J_0041` main-bot readonly status 是否能在不触发 agent turn 的前提下稳定定位 case group。
- `J_0044` Claude addressed-message driver 是否与 Codex driver 共用；如果不能，需要单独证明 at-only addressed path。
- `J_0046`-`J_0050` 是否允许使用同一 protected throwaway repo target branch；如果不能，需要 lead 提供 provider-specific isolated repos。
- `J_0057`/`J_0058` 的 external access evidence 应由 runner/tool log 捕获，避免把模型声明当作唯一证据。
- `J_0054` 是否可稳定诱导 stop-hook correction；如果不能，允许将其保留为 manual/live-observed ReviewDraft，不降级为 hook unit test。
- `J_0066`-`J_0073` 是否允许 J 创建完整 team 资源；若不允许，应先保留为 review draft，F 先补足 team/peer contract。
- `J_0088` 的 compaction driver 必须 case-scoped；不能复用会污染真实用户会话的 live hook replay。`J_0089` hook-state recovery 已 Retired。
- `J_0090` 的 outgoing artifact bundle 要控制在测试群范围：小图片/小文件走真实 agent send path，oversized delivery fixture 不能泄露原始大内容或 secret。
- `J_0096`/`J_0097` 的 helper machine/backend fixture 如果无法安全隔离，应报告 capability gap，不降级成 low-level helper API pass。
- `J_0100` 的 policy marker 必须 case-scoped；如果只能改共享 policy/provider，需要先补隔离方案。
