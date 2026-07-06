# server3 (<INTERNAL_HOST>) — 5 claude interns → copilot-CLI (2026-07-06)

## Why
- Escape the 2-core gateway (<GPU_HOST>:4142) bottleneck: copilot uses GitHub's
  backend directly. server3 = 64 cores / 247GB (plenty).
- Same config: Claude Opus 4.8 · 1M context · effort max · allow-all.

## Auth (the unlock)
- copilot reads token from env `COPILOT_GITHUB_TOKEN` (also GH_TOKEN/GITHUB_TOKEN).
- Extracted Mac token: `security find-generic-password -s copilot-cli -w` (gho_, 40ch).
- On server3: stored in `~/.copilot_token` (600), exported by `~/work-agents/copilot_env.sh`.
- NO interactive /login needed — env token works on any machine.

## Per-intern architecture (registered as claude-type + external_managed)
- tmux `cop_<x>` runs `copilot --resume=<SID> --allow-all` under keeper loop.
- Inbound: daemon `_send_to_claude_tmux` (send-keys) — unchanged claude path.
- Outbound: `copilot_bridge.py` poller reads session events.jsonl → daemon
  `/api/message/send` → Feishu group (same chat_id).
- Inheritance: `claude_handoff.py` extracts claude jsonl → `HANDOFF_FROM_CLAUDE.md`
  (first task + last 140KB dialogue); intern told to read it + continue.

## The 5 interns (all online, verified)
| intern | tmux | copilot SID | claude src jsonl | chat_id |
|---|---|---|---|---|
| intern_dd | cop_dd | 92b18288 | edc6d2c5 (6.7MB) | <CHAT_ID> |
| intern_123 | cop_123 | a9d3d05c | 3dcb040a (4.7MB) | <CHAT_ID> |
| intern_clade | cop_clade | 693313c5 | 6b324263 (73KB) | <CHAT_ID> |
| intern_srv3 | cop_srv3 | 42e04187 | fe1898c6 (29KB) | <CHAT_ID> |
| intern_skill | cop_skill | 0a48c2dd | 888af6fe (3.6MB) | <CHAT_ID> |

## Daemon patches (BOTH copies: .vscode-server-insiders + .intern-agent)
1. `_is_claude_process_running`: also detect `copilot` child (needle). bak.copilotlive
2. `_iter_local_provider_sessions_for_restart`: skip external_managed/copilot →
   never respawn claude over cop_*. bak.extmgd
- Restart daemon ONLY with `WORK_AGENTS_ROOT=/home/tianming.sha/work-agents` set,
  else it can't find enterprise_policy/daemon/_owner.json and exits.
  `cd ~/work-agents && export WORK_AGENTS_ROOT=$PWD && source enterprise_policy/daemon/user.env && internctl.py daemon restart`

## Supervision
- `~/work-agents/copilot_keeper.py` (detached, PID via cron): reads
  `.copilot_interns.json`, keeps each cop_<x> tmux (resume) + poller alive.
- cron `*/3 * * * *` respawns keeper if it dies.
- copilot idle-shutdown ~7min → keeper loop resumes SID within ~3s.

## Verified end-to-end
- inbound (daemon→tmux send-keys) + inherited context + outbound (poller→Feishu 200)
- dd: "R083=778, waiting step2000→R084" (exact)
- 123: "Session29 all-10-card crash, supervisor said serial-redo" (exact)
- skill: "skill2env, SkillCenter paper" (exact)
- 0 claude processes; relay connected; all 5 in sync_online.

## Rollback
- Old claude sessions killed (not recoverable as live), but claude jsonl transcripts
  remain in ~/.claude/projects/. Registration .bak files: none (edited in place);
  to revert an intern to claude: set type=claude, remove external_managed/provider/
  copilot_sid, tmux_session back to ia_intern_*, `internctl session resume`.

## Real-time streaming fix (2026-07-06, copilot_bridge.py v2)
Problem: v1 poller只在 idle 后发一次 pending[-1](只一段、无实时更新)。claude/codex 用
hook 驱动的 FeishuModule 做 send→反复 update→finalize 就地编辑一条消息(带工具进度)。
Fix: 重写 copilot_bridge.py 为流式:
- 读 events.jsonl,按 user.message / turn_start 起新turn;累积 assistant.message.content
  (逐段 append) + tool.execution_start(⎿ toolName args)行;pending_tools 计数。
- 首内容→ /api/message/send 得 message_id;之后内容变化且间隔≥2.5s→ /api/message/update
  就地编辑;pending_tools==0 且 idle≥8s→ /api/message/finalize(HARD_FINALIZE 90s 兜底)。
- render 尾部截断 3600 字符(Feishu 上限);working 时加 "⏳ …"。
验证:intern_srv3 一条消息被就地编辑 7 次(send+5update+finalize,18→819字符);dd/skill
实时流式各自真实工作。daemon API:send{intern_name,project,text}→message_id、
update/finalize{message_id,text}。
注:Mac 的 intern_quant 仍用旧 poller(copilot_bridge_quant.py),如需同样流式可套用 v2。

## intern_quant 迁移 Mac→.134 (2026-07-06)
目标:量化挖矿 intern 从本地 Mac 搬到挖矿主机 .134(与 miner 同机,不再 SSH 远控)。
- .134 = zechuan@<GPU_HOST>:20134,host 73F3-5x4090-134,32c/503G,5×4090D;已有 ~/work-agents+运行中 daemon(relay ws://<INTERNAL_HOST>,instance 73F3-5x4090-134:22)+ ~/Jiami 挖矿(opencode harness,qlh/claude-opus-4.8[1m] xhigh)。/home/zechuan/Jiami 与 /home/zechuan/.vscode-server 均软链到 /mnt/HDD1_3TB/zechuan/。
- 装 Node v22 + copilot 1.0.68 + token(~/.copilot_token)。
- **真会话迁移(copilot→copilot,非 handoff)**:rsync Mac ~/.copilot/session-state/7480adbc(47MB events.jsonl 22515行)→.134,sed 改路径 /Users/tommy/Downloads/Jiami→/home/zechuan/Jiami、/Users/tommy→/home/zechuan(6913→0 refs,JSON 全有效)。tmux cop_quant 跑 copilot --resume=7480adbc,Session:113492 AIC 完整继承。
- daemon 补丁:.134 是较老版本,_is_claude_process_running 只认 pane_current_command 无子进程检测→用 patch_copilot_live_134.py 加 _is_tmux_cli_child_process_running+copilot 检测;external_managed skip 补丁同 server3。
- 注册:.intern_sessions.json quant:intern_quant(copilot/external_managed/cop_quant/sid),.feishu_registry/quant__intern_quant.json(chatId <CHAT_ID> 从 Mac 复制),.copilot_interns.json keeper 配置。
- **切换**:Mac 侧 .intern_sessions.json 删 quant + registry 改 .migrated_off + internctl daemon restart(Mac 释放 chat,只剩 intern_1/intern_zhi);.134 daemon restart(须 export WORK_AGENTS_ROOT=/home/zechuan/work-agents + source ~/.relay_env)→"Registered 1 local targets:['intern_quant']"+sync_online,.134 认领 chat。
- 验证:intern_quant online=True;发消息 intern 实测确认"我在 .134 本机(hostname 73F3-5x4090-134),迁移后不再 SSH,当前 round 62,miner 活着";流式 send→update→finalize 就地编辑;keeper+cron */3 守护。
- 默认配置:.134 ~/.copilot/settings.json = {logLevel:all, effortLevel:max, contextTier:long_context, model:claude-opus-4.8};~/.bashrc export COPILOT_ALLOW_ALL=1。cop_quant 重启后 Opus 4.8·1M 确认。
- **daemon 重启口诀(.134)**:cd ~/work-agents; export WORK_AGENTS_ROOT=/home/zechuan/work-agents; set -a; source ~/.relay_env; set +a; internctl.py daemon restart。

## 富文本流式桥 copilot_stream.py v3 (2026-07-06) — 修复9点+逐字流式
替换旧 copilot_bridge.py(只在 idle 发一次纯文本)。新桥**直连飞书**(像 claude/codex hook,不走 daemon),复用 vendor/feishu_api.py(富 build_post_content)。凭据从 enterprise_policy/daemon/policy.json 的 feishu.app_id/app_secret 取 tenant token。
- **逐字流式**:events.jsonl 只有 committed 段(无 token delta),故用 **tmux pane tail** 叠加"正在打字"的段;每~0.8-1.5s 编辑一次,消息平滑增长。Feishu 硬限每消息 ~17 编辑(MAX_UPDATES=17,code 230072)→真·逐字符不可能,已做到 Feishu 允许的最平滑。pane_tail 提取最后 ● 块,streaming 标志+stale_tail/last_prose 去重防止重复/上一轮闪现。**坑**:capture-pane 目标用 `cop_x` 不能用 `=cop_x`(后者报 can't find pane)。
- **9 点修复**:①富markdown(bold/链接渲染,行内code被飞书strip=claude同限)②footer(📊 ⬇tok·💳AIC·1M ctx,AIC从pane「Session:X AIC」抽)③🧑用户回显头(user.message 立即建消息)④长回复续条(committed 段边界滚动,mid-stream 冻结不乱切)⑤语义工具摘要(Bash:/Read:/Edit:/List:/Grep:...)⑥逐工具✅/❌(读 tool.execution_complete.success)⑦图片(_maybe_send_media 检测结果里图片路径→upload_image+send_image,best-effort)⑧提问卡片(ask_user→send_interactive 飞书卡片)⑨重启续接(启动关掉孤儿消息spinner)+detail_mode(默认full)。
- 关键:pane_busy() 读「Working·esc cancel」判活;produced 标志防 thinking gap 早finalize;pane 空/不可读则回退纯 events(不比旧版差)。
- 文件:Mac ~/work-agents-staging/{copilot_stream.py,copilot_keeper.py(BRIDGE改copilot_stream.py,env加COP_TMUX,marker copstream:),vendor/feishu_api.py}。各机 ~/work-agents/ 同步。日志 .copstream_<intern>.log。
- 实测:srv3 长回复 live=36→683 平滑增长12编辑1消息;工具轮 ✅ List:`ls -la`;2500字回复2次滚动续条;quant .134 回读 12 个 bold tag 渲染成功。6 intern(5 server3+quant)全切,0 旧 bridge。

## /compact 顺畅化 (2026-07-06)
问题:copilot intern 执行 /compact "不顺畅"。根因=**大 context 压缩耗时长且飞书零反馈**——quant 实测压缩耗时 **102 秒**,旧桥期间飞书什么都不显示,看着像卡死。
- 机制本身可靠:daemon `_send_native_slash_to_tmux`(type `/compact` -l + 0.5s + Enter)在 copilot 上稳定执行(3 次测试均触发 session.compaction_start/complete;copilot 打 `/compact` 会弹自动补全菜单但单 Enter 精确匹配即执行)。events 里是 `session.compaction_start`/`session.compaction_complete`(非 user.message,故不会产生挂起 turn)。
- 修复(copilot_stream.py):①处理 session.compaction_start→飞书发"🗜 正在压缩上下文…",compaction_complete→edit 成"✅ 上下文已压缩,窗口已释放(耗时Xs)";②pane_busy() 认"Compacting"为忙 + _compact["active"] 标志→压缩期间**绝不 finalize** 当前 turn(防 mid-turn 自动压缩把回复劈成两条);③模块级 _compact 状态跨 start/complete 两事件。
- 实测:srv3 压缩 32s、quant 压缩 102s,飞书均正确显示 🗜→✅ 进度。daemon 另发"✅ 已向 X 发送 /compact"receipt,与桥的进度反馈互补。

## 飞书 token 过期修复 (2026-07-06)
症状:部分 intern(实测 intern_123)飞书群突然不显示回复,poller 日志刷 code 99991663 "Invalid access token"(累计1503次)。根因:copilot_stream.py 的 token() 缓存 6600s,但飞书 tenant token ~7200s 过期后被服务端提前失效,且 send_new/edit 遇 401 仍复用死 token 不刷新→死循环失败。
修复:①token(force=True) 支持强制刷新,缓存降到 5400s(远早于 TTL);②_is_token_err 识别 99991663/99991661/99991664/Invalid access token;③send_new/edit 遇 token 错→token(force=True) 强刷+重试一次。部署 server3(5)+.134(quant)+Mac(mosaic)全部 7 intern,restart 后 new-token-errors=0,intern_123 恢复流式。
排障口诀:intern 飞书不显示→先 grep 99991663 ~/work-agents/.copstream_<intern>.log;有则确认在跑的 copilot_stream.py 含 "def token(force"。

## Mac keeper 常驻修复:launchd (2026-07-06)
Mac 上 copilot_keeper.py 用 nohup/子壳启动反复随 bash session 退出而死(macOS 无 setsid,cron 会触发权限提示卡住)→mosaic intern poller 断、飞书不显示。
修复:launchd agent ~/Library/LaunchAgents/com.intern.copilot-keeper.plist(ProgramArguments=python3 copilot_keeper.py,RunAtLoad+KeepAlive,EnvironmentVariables 带 INTERN_OWNER_MOBILE=<OWNER_MOBILE>+HOME,WorkingDirectory=~/work-agents)。launchctl load 后 keeper ppid=1 完全脱离、崩溃自拉起、重启存活。检查:launchctl list|grep copilot-keeper。server3/.134 仍用 nohup+cron(Linux 有 setsid,OK)。
Mac intern_mosaic:SID 1874cd7a-6900-4a57-aee9-5328289d255e,chat <CHAT_ID>,cwd /Users/tommy/mosaic-alpha,继承 claude 56c1954e(抗锚定三件套 AliasMap/SingleAgentProposer/AliasedLegacyProposer,实测答对)。

## GitHub 推送 (2026-07-06)
全套 intern-agent + copilot 桥推到 github.com/shatianming5/intern-copilot-bridge(private,462 文件)。脱敏:relay.py 的 codex_lb_api_key、deployment_config.py 的 feishu app_secret/app_id/owner_mobile、内网 IP(10.100.x)、手机号 <OWNER_MOBILE> 全改成 os.environ.get 读环境变量;删所有 .bak;.gitignore 排除 enterprise_policy/.feishu_registry/.copilot_token/policy.json/state/*.log。最终扫描 0 secret。推送用临时 GH_TOKEN(用后从 remote url 清除)。
