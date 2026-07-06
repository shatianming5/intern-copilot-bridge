# CI Action/Assertion Reuse Report

<!-- Updated: Session 137 -->

This report tracks reusable behavior promoted out of remote case shards into
`CI/actions/`, `CI/assertions/`, or approved helpers. A reuse count of `1` is
allowed when the behavior is semantically common and should not remain embedded
inside a case script.

Counts are from current source scans and include `NativeRemoteCase`
compatibility wrapper call sites. Transitional remote shard files were deleted
in Session 108; remote case dispatch now points directly at owning
`F_XXXX.py` / `J_XXXX.py` functions.

| Promoted id / function | Type | Current reuse count | Current callers / path | Why promoted |
|---|---:|---:|---|---|
| `intern.remote_create_fixture` / `InternActions.create_fixture_remote()` | action | 1 shared action caller | `InternActions.create_fixture_case_remote()` composes the raw product create operation for all case-scoped fixture interns | Creating a Codex/Claude fixture intern is a common product operation, not case-private script logic. The raw action performs `internctl create` and returns metadata/runtime/session evidence; case-scoped naming, bookkeeping, and checks are handled by `intern.remote_create_fixture_case`. |
| `intern.remote_create_fixture_case` / `InternActions.create_fixture_case_remote()` | action | 27 | Active F/J cases directly call `ctx.action.intern.create_fixture_case_remote()` for Codex and Claude fixture interns, including F_0044/F_0045 Claude fixtures and J_0033 Codex resume fixture | Case-scoped fixture intern naming, created-intern bookkeeping, cleanup artifact storage, and reusable fixture contract check generation are common across F/J scripts. Session 130 removed `NativeRemoteCase.create_fixture_intern()` and moved callers to this action root. |
| `fixture_intern_contract_checks()` | assertion | 27 | `InternActions.create_fixture_case_remote()` returns these checks and active cases apply them through `self.require_checks(...)` | The status/knowledge/runtime/hook/session/role/team contract is reusable across Codex, Claude, team, and duplicate/scope cases. |
| `intern.remote_no_team_or_non_codex_fixture` / `InternActions.no_team_or_non_codex_fixture_remote()` | action | 2 | F_0025 and F_0028 call `ctx.action.intern.no_team_or_non_codex_fixture_remote()` for TreeView/group-mode fixture sanity scenarios | Gathering case-namespace intern list and session registry evidence for fixture sanity is reusable intern state probing. Session 130 removed `NativeRemoteCase.assert_no_team_or_non_codex_fixture()`. |
| `no_team_or_non_codex_fixture_checks()` | assertion | 2 | `InternActions.no_team_or_non_codex_fixture_remote()` returns these checks and active cases apply them through `self.require_checks(...)` | Fixture sanity for “no team intern or non-Codex backend leaked into this Codex-only projection” is a reusable assertion. |
| `intern.remote_create` / `InternActions.create_remote()` | action | 14 | `intern.remote_create_case` and compatibility setup paths use the same product operation, including Claude creation through `intern_type="claude"` | Normal intern creation is common. Claude creation uses the same action with `intern_type="claude"` because the product command is the same `internctl create` contract with a backend parameter. |
| `intern.remote_create_case` / `InternActions.create_case_remote()` | action | 9 | Active F/J cases directly call `ctx.action.intern.create_case_remote()` for case-scoped intern names, including F_0007/F_0010/F_0023/F_0028/F_0043 | Case-scoped intern-name construction and created-intern bookkeeping are reusable across F/J scripts. C4 moved active cases off `NativeRemoteCase.create_intern()` while keeping naming in the intern action root instead of duplicating it in cases. |
| `intern.remote_runtime_dir` / `InternActions.runtime_dir_remote()` | action | 10 | F_0010/F_0043 and remaining worker intern cleanup/session-map internals directly call `ctx.action.intern.runtime_dir_remote()` | Resolving a target intern runtime directory from `(project, workspace_id, intern)` is common intern state evidence. Session 129 removed the `NativeRemoteCase.runtime_dir()` adapter and moved callers to the intern action root. |
| `intern.remote_cleanup_fixture` / `InternActions.cleanup_fixture_remote()` | action | 1 shared cleanup caller | `NativeRemoteCase.cleanup_fixture_workspace()` composes this action while workspace cleanup remains a runner lifecycle concern | Fixture intern cleanup is common lifecycle behavior: it resolves runtime metadata, removes case-scoped intern/task/runtime residue, and prunes matching session registry entries with path safety checks. Session 137 moved this logic out of the worker method body into the intern action root. |
| `intern.remote_no_artifacts` / `InternActions.no_artifacts_remote()` | action | 2 | F_0008 uses this action for duplicate/invalid create rollback artifact checks | Runtime/session/relay residue collection after failed intern creation is reusable rollback evidence. The action gathers evidence and returns `no_intern_artifacts_checks()` rows for cases to apply. |
| `intern.remote_removed` / `InternActions.removed_remote()` | action | 4 | F_0010 uses this action for normal, force, runtime-only, and metadata-source delete cleanup checks | Intern deletion cleanup evidence is reusable across delete guards: status/knowledge/runtime/task/session/relay/tmux residue collection belongs in the intern action root while case scenarios decide expected delete policy. |
| `workspace.remote_create_enable` / `WorkspaceActions.create_enable_remote()` | action | 56 | `NativeRemoteCase.create_workspace()` compatibility wrapper used by F/J case-owned scripts and runner-local legacy remote cases; workspace case bodies now live in `F_0001.py`-`F_0006.py`, `F_0021.py`, and `F_0022.py` | Creating and enabling a workspace is a common product operation. The action owns the `internctl workspace create` + `workspace enable` command construction and returns stable workspace evidence for case scripts. |
| `workspace.remote_create_case` / `WorkspaceActions.create_case_remote()` | action | 40 | Active F/J cases directly call `ctx.action.workspace.create_case_remote()` for case-scoped workspace display/name generation and create/enable evidence | Case-scoped workspace naming and bookkeeping are reusable across F/J scripts. C4 moved active cases off `NativeRemoteCase.create_workspace()` while keeping naming in the workspace action root. |
| `workspace.remote_nonprotected_repo` / `WorkspaceActions.nonprotected_repo_remote()` and `workspace.remote_github_nonprotected_repo_detail` / `WorkspaceActions.github_nonprotected_repo_detail_remote()` | action | 6 | F_0001/F_0002/F_0005/F_0006/F_0021 read the Codeup nonprotected repo through the action root; F_0003 resolves the GitHub repo through the action root | Test repo resolution is common workspace setup evidence. Session 128 removed the `NativeRemoteCase.require_nonprotected_repo()` and `resolve_github_repo()` adapters. |
| `workspace.remote_create_args` / `WorkspaceActions.create_args_remote()` and `workspace.remote_attempt_create` / `WorkspaceActions.attempt_create_remote()` | action | 6 | F_0001/F_0002/F_0004 use create argv evidence for GUI/CLI equivalence; F_0005 uses failed create attempts for duplicate/bad repo/bad branch rollback checks | CLI argv construction and failed create evidence are common workspace behavior, not owning-case-only logic. Session 128 removed `workspace_create_args()` and `attempt_workspace_add()` from the worker. |
| `workspace.remote_git_default_head` / `WorkspaceActions.git_default_head_remote()` and `workspace.remote_business_branch_unchanged_checks` / `WorkspaceActions.business_branch_unchanged_checks_remote()` | action | 5 | F_0002 and F_0021 record default branch baselines; F_0002/F_0021 verify unchanged business branch revisions after workspace add/delete | Git branch baseline/head evidence is reusable source-control evidence for metadata_branch and delete/preserve contracts. Session 128 removed the worker git baseline/head wrappers. |
| `workspace.remote_record_checks`, `workspace.remote_relay_sync_checks`, `workspace.remote_absent_checks`, `workspace.remote_no_extra_records_checks`, `workspace.remote_metadata_root_checks`, and `workspace.remote_metadata_branch_created_checks` | action | 14 | F_0001-F_0005 collect workspace record/sync/absent/no-extra/metadata-root/metadata-branch assertion evidence through `ctx.action.workspace.*` | These actions collect remote daemon/relay/metadata/git evidence while pure pass/fail logic remains in `workspace_assertions.*`. Session 128 removed the matching `NativeRemoteCase.assert_*` wrappers. |
| `workspace_assertions.require_checks()` and `workspace_assertions.metadata_branch_created_checks()` | assertion | 17 | F_0001-F_0005 and F_0021 apply workspace assertion result rows directly from owning case scripts | Applying assertion check rows and composing metadata-branch root plus remote-branch visibility are reusable assertion mechanics. They prevent case scripts from depending on worker assertion wrappers. |
| `session.remote_start_for_workspace` / `SessionActions.start_for_workspace_remote()` | action | 10 | Active F/J cases directly call `ctx.action.session.start_for_workspace_remote()` for scoped Codex/Claude session startup | Starting a scoped Codex/Claude session is a common operation. The action owns the `internctl session start <intern> --project <project> --type <type> --no-attach` command plus running/session bookkeeping; cases keep scenario-specific assertions. |
| `session.remote_registry` / `SessionActions.registry_remote()` | action | 9 | F_0008/F_0026 and remaining worker cleanup/session-map internals directly call `ctx.action.session.registry_remote()` | Reading `.intern_sessions.json` is common session registry evidence. Session 129 removed the `NativeRemoteCase.session_registry()` adapter so cases and remaining worker internals use the session action root directly. |
| `session.remote_delete_registry_entry` / `SessionActions.delete_registry_entry_remote()` | action | 1 | F_0026 uses this action to remove one case-scoped session map entry when simulating disposed active chat state | Deleting one scoped session registry entry is a reusable registry mutation with stable safety boundaries. It is kept as an action so active-chat cases do not hand-edit `.intern_sessions.json`. |
| `session.remote_resource_lookup` / `SessionActions.resource_lookup_remote()` | action | 1 | `SessionActions.active_intern_from_resource_remote()` and `NativeRemoteCase.session_resource_lookup()` compatibility wrapper | Looking up a VS Code chat/session resource in `.intern_sessions.json` is common session-registry evidence. It is promoted even with one current direct reuse because active-intern resolution, TreeView active chat handling, and future J active-chat journeys should not duplicate registry scanning. |
| `session.remote_active_intern_from_resource` / `SessionActions.active_intern_from_resource_remote()` | action | 2 | `NativeRemoteCase.active_intern_from_session_resource()` compatibility wrapper used by `F_0026.s13_simulate_active_chat_b` and `F_0026.s15_simulate_unknown_chat` | Resolving the active intern from a session resource is reusable session/TreeView evidence construction. The action returns lookup/list-match evidence; scenario pass/fail remains in the wrapper/case. |
| `session.remote_product_latest_codex_session_id` / `SessionActions.product_latest_codex_session_id_remote()` | action | 2 | `SessionActions.codex_session_id_evidence_remote()` and `NativeRemoteCase.product_latest_codex_session_id()` compatibility wrapper | Product durable Codex UUID discovery is a reusable probe. It must stay outside individual restart cases so F no-prompt fresh-start policy and J same-session UUID policy can share the same evidence source. |
| `session.remote_codex_session_id_evidence` / `SessionActions.codex_session_id_evidence_remote()` | action | 9 | `NativeRemoteCase.codex_session_id_evidence()` compatibility wrapper used by F_0009/F_0027/F_0033, F_0026, and J_0033 restart/resume scenarios | Codex session UUID evidence construction is reusable product probing. The action owns product helper/transcript candidate scanning; wrappers and assertions keep capability-gap/product-bug classification. |
| `session.remote_restart_for_workspace` / `SessionActions.restart_for_workspace_remote()` | action | 5 | Active Codex/Claude F/J restart scenarios directly call `ctx.action.session.restart_for_workspace_remote()` from F_0009, F_0027, F_0033, F_0043, and J_0033 | Restarting a scoped Codex/Claude session is a common product operation. The action only executes `internctl session restart` and returns stdout/status markers; F/J policy assertions decide whether fresh start or resume is acceptable. |
| `session.remote_wait_resume_hint` / `SessionActions.wait_resume_this_intern_hint_remote()` | action | 1 | `NativeRemoteCase.wait_resume_this_intern_hint()` compatibility wrapper used by the owning Codex resume-hint case script | Polling tmux for the generic `Resume this intern` hint is reusable. The action returns hint evidence without deciding case-specific retry behavior or provider-specific UUID failure classification. |
| `session.remote_tmux_provider_processes` / `SessionActions.tmux_provider_processes_remote()` | action | 4 | F_0043 calls it directly for Claude process checks; provider-live/manual-resume actions also reuse it internally | Provider process discovery from a tmux pane process tree is reusable across Codex and Claude live checks. The action owns process-tree scanning and provider command matching; case assertions decide live/failure semantics. |
| `session.remote_tmux_environment_values` / `SessionActions.tmux_environment_values_remote()` | action | 1 | F_0043 directly calls `ctx.action.session.tmux_environment_values_remote()` for Claude runtime policy/env materialization checks | Reading tmux session environment values is reusable tmux/session evidence. It is promoted even with one current active caller because C4 removes the last tmux-env wrapper from `NativeRemoteCase` and future provider env checks should not duplicate `tmux show-environment` handling. |
| `session.remote_wait_provider_live` / `SessionActions.wait_provider_session_live_remote()` | action | 2 | F_0043 calls it through a case-local classifier helper for Claude start/restart live gates | Waiting for session status, tmux presence, provider process match, and input readiness is a common provider-live action. The case keeps product-bug classification such as `product_bug_claude_session_not_live`. |
| `session.remote_wait_codex_live_after_manual_resume` / `SessionActions.wait_codex_live_after_manual_resume_remote()` | action | 1 | J_0033 directly calls `ctx.action.session.wait_codex_live_after_manual_resume_remote()` for manual resume live probing | Codex manual resume live probing is reusable for J same-session journeys. It is promoted with one current caller because the action owns product probing while J keeps paid-agent journey semantics and UUID assertions. |
| `codex_session_id_available_check()` | assertion | 3 | `NativeRemoteCase.assert_codex_session_id_available()` compatibility wrapper used by J_0033 strict same-session UUID gates | Availability of a real Codex UUID is a reusable assertion and must remain separate from evidence collection so F can allow fresh start while J requires same-session continuity. |
| `codex_restart_output_requires_resume_check()` | assertion | 1 | `NativeRemoteCase.assert_codex_restart_output_requires_resume()` compatibility wrapper used by J_0033 restart after a real user turn | Strict resume-required restart output is a reusable J-level policy assertion even with one active caller; it encodes the paid-agent same-session contract. |
| `codex_restart_output_allows_fresh_start_check()` | assertion | 3 | `NativeRemoteCase.assert_codex_restart_output_allows_fresh_start()` compatibility wrapper used by F_0009/F_0027/F_0033 no-prompt restart cases | F-level no-prompt restart accepts fresh start or resume as long as the session starts cleanly. This assertion keeps that policy explicit and prevents J-only UUID requirements from leaking into F. |
| `codex_session_id_equal_checks()` | assertion | 1 | `NativeRemoteCase.assert_codex_session_id_equal()` compatibility wrapper retained for same-UUID checks; `J_0033.py` still uses a case-specific classifier wrapper to preserve journey failure classification | Same-session UUID equality is reusable; the active journey wraps it to preserve a case-specific classification label while reusing the shared evidence/assertion path. |
| `resume_hint_command_contract_check()` | assertion | 2 | `NativeRemoteCase.assert_resume_hint_command_contract()` compatibility wrapper used by F_0033 and J_0033 Codex resume-hint command checks | Resume-hint command structure is reusable across providers. Provider/case-specific classification remains in wrappers or case code where the report contract differs. |
| `source_contract.extension_source_candidates` / `SourceContractActions.extension_source_candidates()` | action | 1 | `NativeRemoteCase.extension_source_candidates()` compatibility wrapper; also establishes the shared candidate path order used by source evidence actions | Source candidate discovery is reusable source/package evidence. It is promoted even with one current wrapper because package/source candidate fallback order must not be duplicated in case scripts. |
| `source_contract.product_source_evidence` / `SourceContractActions.product_source_evidence()` | action | 7 | `NativeRemoteCase.product_source_evidence()` compatibility wrapper used by F_0025/F_0026/F_0028/F_0034/F_0037 TreeView/daemon source-marker scenarios | Product source marker lookup is reusable evidence construction. The action owns candidate scanning and marker line discovery; source-marker assertions decide pass/fail. |
| `source_contract.deployed_extension_dist` / `SourceContractActions.deployed_extension_dist()` | action | 3 | `SourceContractActions.deployed_contract_remote()` shared source-contract action, direct F_0027 deployed GUI source evidence, and focused source-contract tests | Reading deployed `dist/extension.js` is common source/package evidence. The action owns bundle read/path/length evidence; pure source-contract assertions own contract checks. It no longer has a `NativeRemoteCase._deployed_extension_dist()` wrapper. |
| `source_contract.deployed_extension_package` / `SourceContractActions.deployed_extension_package()` | action | 3 | `SourceContractActions.deployed_contract_remote()` for Claude contracts, F_0032 local menu assertion helper, and focused source/menu tests | Reading deployed `package.json` is reusable package evidence for TreeView/menu contracts. The action owns JSON loading and object validation; menu assertions own required command checks. It no longer has a runner wrapper. |
| `source_contract.deployed_view_item_commands` / `SourceContractActions.deployed_view_item_commands()` | action | 2 | `SourceContractActions.deployed_contract_remote()` for Claude/package menu contracts and focused source reporting tests | Extracting `view/item/context` command rows is reusable package evidence. The action returns rows/visible commands while `treeview` and `source_contract` assertions decide command requirements. It no longer has a runner wrapper. |
| `source_contract.deployed_cli_source_text` / `SourceContractActions.deployed_cli_source_text()` | action | 2 | `SourceContractActions.deployed_contract_remote()` for F_0045 Claude skill/group source contract and focused source tests | Reading bundled CLI source text is reusable source evidence; current active source contracts need `commands/skill.py`, and future CLI source contracts should reuse the same action. It no longer has a runner wrapper. |
| `source_contract.deployed_contract` / `SourceContractActions.deployed_contract_remote()` | action | 10 | `NativeRemoteCase.deployed_source_contract()` adapter used by F_0021, F_0022, F_0023, F_0024, F_0029, F_0030, F_0031, F_0032, F_0044, and F_0045 source-contract surfaces | Shared deployed source-contract orchestration is reusable: it gathers deployed bundle/package/CLI evidence and calls pure source-contract assertions while keeping case-owned artifact keys and report shaping outside individual runner helpers. |
| `source_contract.dist_contract_results` / `SourceContractActions.dist_contract_results()` | action | 1 | Source-contract action root; current active adapters mostly receive complete pure assertion contracts directly, while this action remains the shared normalizer for generated dist check lists | Normalizing dist check rows into ok/failed/checks evidence is reusable. It is kept promoted so future source-contract checks do not rebuild report shape in case code. |
| `source_contract.dist_command_block` / `SourceContractActions.dist_command_block()` | action | 1 | Source-contract action root; no current active case calls it directly after pure assertion migration, but it remains the shared command-block evidence primitive for source-contract refinements | Dist command block extraction is semantically reusable and should not be reimplemented in case scripts. It remains promoted with one current root/API use because future source-contract refinements need the same evidence shape. |
| `task.remote_parse_status_metadata` / `TaskActions.parse_status_metadata_remote()` | action | 3 | `NativeRemoteCase.parse_status_metadata()` compatibility wrapper used by fixture cleanup and metadata/status consistency checks; task action also reuses it internally for status update evidence | Parsing status.md METADATA is common task/intern state evidence. It is promoted so cleanup, status projection, and task fixture setup do not each reimplement line parsing. |
| `task.remote_write_fixture` / `TaskActions.write_fixture_remote()` | action | 6 | `NativeRemoteCase.write_fixture_task()` compatibility wrapper used by F/J task fixture setup, delete guard, TreeView projection, and J_0033 resume journey setup | Writing a case-scoped task README/history/knowledge fixture is reusable task setup. The action owns line-3 metadata file shape; scenarios decide what task state should prove. |
| `task.remote_write_readme_fixture` / `TaskActions.write_readme_fixture_remote()` | action | 11 | `NativeRemoteCase._write_task_treeview_readme()` compatibility wrapper used by F_0023/F_0024/F_0032 task TreeView source/projection fixtures, including malformed metadata-line fixtures | Task README fixture generation is reusable task TreeView evidence. The action intentionally supports malformed line-4 fixtures because parser-contract cases need controlled bad input. |
| `task.remote_write_intern_status_metadata` / `TaskActions.write_intern_status_metadata_remote()` | action | 9 | `NativeRemoteCase.write_status_metadata()` compatibility wrapper used by task/status/TreeView cases and by `task.remote_write_working_fixture` | Updating intern status metadata is common task-state fixture setup. The action owns metadata/table rewrite mechanics; assertions/cases decide whether Working/Idle/task/PR state is correct. |
| `task.remote_write_working_fixture` / `TaskActions.write_working_fixture_remote()` | action | 2 | `NativeRemoteCase.write_working_task_fixture()` compatibility wrapper used by F_0010 delete guard and composed from task/status metadata primitives | Seeding a Working intern with an InProgress task is reusable across delete guard, status tree, and future task journeys. It is promoted even with few current call sites because the combined fixture has a stable product-state meaning. |
| `task.remote_seed_treeview_intern_status` / `TaskActions.seed_treeview_intern_status_remote()` | action | 2 | `NativeRemoteCase._seed_task_treeview_intern_status()` compatibility wrapper used by F_0023 task TreeView projection fixtures | TreeView task status seeding is reusable fixture evidence for task projection contracts. The action owns status.md/knowledge.md fixture shape; task-list/parser checks remain in case/assertion layers. |
| `skill.remote_run_json` / `SkillActions.run_json_remote()` | action | 17 | `NativeRemoteCase._run_skill_json()` compatibility wrapper used by F_0029/F_0030/F_0032/F_0045 skill list/add/enable/disable source and group parity scenarios | Executing `internctl skill` JSON commands is common skill product probing. The action owns command construction and JSON parsing; scenario-specific scope/farm expectations stay outside the action. |
| `skill.remote_run_cmd` / `SkillActions.run_cmd_remote()` | action | 6 | `NativeRemoteCase._run_skill_cmd()` compatibility wrapper used by skill update/remove/invalid-source/local-update gap scenarios | Non-JSON skill CLI execution is common product evidence. The action returns structured stdout/stderr/returncode evidence while wrappers preserve legacy `CompletedProcess` call shape during migration. |
| `skill.remote_read_json_path` / `SkillActions.read_json_path_remote()` | action | 10 | `NativeRemoteCase._read_json_path()` compatibility wrapper used for `.intern_skill.json` repo/personal snapshots across Codex and Claude skill cases | Reading skill config JSON is reusable skill state evidence. It belongs in the skill action root rather than ad hoc file reads in case scripts. |
| `skill.remote_source_target` / `SkillActions.source_target_remote()` | action | 3 | `NativeRemoteCase._skill_target()` compatibility wrapper used by F_0029 invalid/source target and add-source scenarios | Resolving `.skill_sources/<key>` target path is reusable skill source evidence. The action computes path only; existence and rollback contracts stay in case/assertion logic. |
| `skill.remote_farm_rel_for_type` / `SkillActions.farm_rel_for_type_remote()` | action | 1 | `NativeRemoteCase._skill_farm_rel_for_type()` compatibility wrapper and `skill.remote_farm_entries` / `skill.remote_farm_link` internals | Provider-specific farm rel mapping is semantically common even with one wrapper because Codex and Claude cases must share the same `.agents/skills` versus `.claude/skills` source of truth. |
| `skill.remote_farm_entries` / `SkillActions.farm_entries_remote()` | action | 7 | `NativeRemoteCase._skill_farm_entries()` and `_skill_farm_entries_for_type()` compatibility wrappers used by Codex and Claude skill scope/farm cases | Listing skill farm entries is reusable state evidence for repo/personal enable/disable/sync contracts. The action owns provider-specific farm path selection and sorted entry collection. |
| `skill.remote_farm_link` / `SkillActions.farm_link_remote()` | action | 5 | `NativeRemoteCase._skill_farm_link()` and `_skill_farm_link_for_type()` compatibility wrappers used by Codex/Claude farm sync checks | Resolving a skill farm link path is reusable evidence. Cases decide symlink/existence expectations for the specific scenario. |
| `skill.remote_write_source_fixture` / `SkillActions.write_source_fixture_remote()` | action | 4 | `NativeRemoteCase._write_skill_source()` compatibility wrapper, `skill.remote_git_source_fixture`, `skill.remote_update_git_source_fixture`, and focused skill action tests | Writing SKILL.md fixture content is reusable skill source setup. Git repo commit/update composition now uses explicit git fixture actions instead of runner composer helpers. |
| `skill.remote_git_source_fixture` / `SkillActions.git_source_fixture_remote()` | action | 4 | F_0029, F_0030, F_0032, and F_0045 git skill source setup | Creating a case-scoped git skill source repo with seed `SKILL.md` and commit evidence is reusable fixture setup. Cases still decide which skill CLI/product contract to verify after the fixture exists. |
| `skill.remote_update_git_source_fixture` / `SkillActions.update_git_source_fixture_remote()` | action | 1 | F_0029 git skill source update scenario | Updating a case-scoped git skill source repo is the reusable counterpart to initial git fixture creation. It is promoted with one current caller because update scenarios should share commit/head evidence construction rather than reimplement git commands in cases. |
| `feishu.remote_wait_green_light` / `FeishuActions.wait_current_scene_green_light_remote()` | action | 7 | Active F/J cases directly call `ctx.action.feishu.wait_current_scene_green_light_remote()` for Codex/Claude group light checks | Green-light state is a reusable read/probe of user-visible Feishu group status. The action owns relay `/api/scene` polling; case assertions decide whether the observed state satisfies a scenario. |
| `feishu.remote_group_mode_cli` / `FeishuActions.group_mode_cli_remote()` | action | 9 | `NativeRemoteCase.run_group_mode_cli()` compatibility wrapper used by Codex and Claude group-mode cases | Setting trigger/detail mode through `internctl group` is common Feishu group configuration behavior. The action now owns CLI execution and JSON evidence; scenario-specific product contract assertions stay in case/assertion layers. |
| `feishu.remote_group_config` / `FeishuActions.group_config_remote()` | action | 7 | `NativeRemoteCase.group_config()` compatibility wrapper used by Codex and Claude group-mode cases | Reading trigger/detail mode through relay APIs is common Feishu group configuration evidence. The action now owns relay API reads and response shaping. |
| `feishu.remote_set_group_config_direct` / `FeishuActions.set_group_config_direct_remote()` | action | 2 | `NativeRemoteCase.set_group_config_direct()` compatibility wrapper used by group-mode setup/seed scenarios | Directly seeding trigger/detail modes is reusable case setup behavior. The action owns relay API writes and read-back evidence; wrapper keeps optional native report assertion. |
| `feishu.remote_wait_question_poll` / `FeishuActions.wait_question_poll_remote()` | action | 8 | `NativeRemoteCase.wait_question_poll()` compatibility wrapper used by F_0015 config/helper card callback scenarios and `run_codex_rui_card_owner()` | Polling daemon `/api/question/poll` is common Feishu card/question state evidence. The action only polls and filters owner/status; scenario-specific card/status assertions stay in wrapper or case layers. |
| `relay_daemon.remote_owner_identity_payload` / `RelayDaemonActions.owner_identity_payload_remote()` | action | 1 | `NativeRemoteCase.owner_identity_payload()` compatibility wrapper used by F_0013 relay chat project-scope lifecycle | Owner identity payload construction is relay/daemon registry setup evidence. It is promoted with one current caller because relay chat create cases and future owner-scoped J/monitor checks should not duplicate runtime policy owner parsing. |
| `relay_daemon.remote_relay_chat_lookup` / `RelayDaemonActions.relay_chat_lookup_remote()` | action | 8 | `NativeRemoteCase.relay_chat_lookup()` compatibility wrapper used by F_0012/F_0013/F_0034/F_0037 daemon/relay registry lifecycle cases | Direct relay `/api/chat/lookup` evidence is common registry read behavior. The action owns query construction and relay read; scenario-specific expectations about chat presence/removal stay in case/wrapper layers. |
| `relay_daemon.remote_daemon_group_list_entry` / `RelayDaemonActions.daemon_group_list_entry_remote()` | action | 4 | `NativeRemoteCase.daemon_group_list_entry()` compatibility wrapper used by F_0012 daemon group create/sync/delete/missing-project scenarios | Daemon `/api/group/list` filtering is reusable daemon registry evidence. The action owns list retrieval and project/intern filtering; cases keep mutation contract assertions. |
| `relay_daemon.remote_chat_lookup` / `RelayDaemonActions.chat_lookup_remote()` | action | 2 | `NativeRemoteCase.chat_lookup()` compatibility wrapper used by F_0037 reconnect registry retention scenarios | Daemon `/api/chat/lookup` fixture evidence is common runtime registry read behavior. The action returns lookup evidence while the wrapper keeps the existing `chat_id` visible assertion. |
| `relay_daemon.remote_write_no_group_chat_fixture` / `RelayDaemonActions.write_no_group_chat_fixture_remote()` | action | 1 | `NativeRemoteCase.write_no_group_chat_fixture()` compatibility wrapper for no-group registry fixture setup | Writing a case-scoped `.feishu_registry` no-group mapping is reusable fixture setup, even though current active case callers have been reduced. It belongs in relay/daemon actions so future F/J fixture scripts do not duplicate registry filename, chat id, and payload shaping. |
| `relay_daemon.remote_remove_no_group_chat_fixtures` / `RelayDaemonActions.remove_no_group_chat_fixtures_remote()` | action | 1 | `NativeRemoteCase.remove_no_group_chat_fixtures()` compatibility wrapper for no-group registry cleanup | Removing no-group registry fixture files is paired setup cleanup for the same reusable fixture surface. The action owns file removal; case logic decides whether cleanup is appropriate for the scenario. |
| `relay_daemon.remote_restart_daemon_for_fixture_registry` / `RelayDaemonActions.restart_daemon_for_fixture_registry_remote()` | action | 1 | `NativeRemoteCase.restart_daemon_for_fixture_registry()` compatibility wrapper for fixture registry refresh | Restarting daemon to reload fixture registry is reusable setup behavior for registry-backed fixture cases. The action owns CLI command/status evidence; wrapper keeps the native report `daemon_restarted_*` check. |
| `treeview.remote_item_projection` / `TreeViewActions.item_projection_remote()` | action | 13 | `NativeRemoteCase.tree_item_projection()` compatibility wrapper used by Claude and TreeView/task/skill case-owned scripts | TreeItem projection is common read/evidence construction. The action owns list/status/session evidence collection and projection shaping; scenario-specific checks remain in assertion or case layers. |
| `treeview.remote_workspace_projection` / `TreeViewActions.workspace_projection_remote()` | action | 2 | `NativeRemoteCase.workspace_tree_projection()` compatibility wrapper used by TreeView grouping cases | Workspace-level TreeView grouping is reusable projection evidence. The action now owns active/inactive ordering and grouping; wrapper only writes native list-match checks. |
| `treeview.remote_context_menu_commands` / `TreeViewActions.context_menu_commands_remote()` | action | 1 | `NativeRemoteCase.context_menu_commands_for_view_item()` compatibility wrapper used by F_0028 group-mode TreeView menu coverage | Reading package.json TreeView context-menu commands is reusable source/UI evidence. It is promoted even with one current product caller because source-contract menu checks should not duplicate package lookup and context-value parsing. |

## Maintenance Rule

Session 100 relocation note: workspace scenario bodies are no longer held in
`CI/cases/F/remote_workspace.py`. Session 108 deleted that transitional shard;
all workspace case scripts live in their owning `F_XXXX.py` files while still
calling the promoted workspace actions/assertions through `NativeRemoteCase`
compatibility wrappers.

Session 101 relocation note: intern/session scenario bodies are no longer held
in `CI/cases/F/remote_intern_session.py`. Session 108 deleted that transitional
shard; `F_0007.py`, `F_0008.py`, `F_0009.py`, `F_0010.py`, and `F_0033.py`
own their scripts while continuing to reuse the promoted intern, session,
Feishu, task, workspace, and assertion wrappers.

Session 102 relocation note: config/helper scenario bodies are no longer held
in `CI/cases/F/remote_config_helper.py`. Session 108 deleted that transitional
shard; `F_0015.py` through `F_0020.py`, `F_0035.py`, `F_0036.py`, and
`F_0041.py` own their scripts while continuing to reuse the promoted
Feishu question/card, relay/daemon, session, and assertion wrappers.

Session 103 relocation note: daemon/relay scenario bodies are no longer held
in `CI/cases/F/remote_daemon_relay.py`. Session 108 deleted that transitional
shard; `F_0011.py`, `F_0012.py`, `F_0013.py`, `F_0034.py`, and `F_0037.py`
own their scripts while continuing to reuse the promoted relay/daemon lookup,
Feishu group, session, workspace, policy, and assertion wrappers.

Session 104 relocation note: TreeView/task/skill scenario bodies are no longer
held in `CI/cases/F/remote_treeview_task_skill.py`. Session 108 deleted that
transitional shard; `F_0023.py` through `F_0032.py` own their scripts while
continuing to reuse the promoted task, skill, TreeView, source-contract,
session, workspace, Feishu, and assertion wrappers.

Session 106 relocation note: Claude and J scenario bodies are no longer held in
`CI/cases/F/remote_claude.py` or `CI/cases/J/remote_journeys.py`. Session 108
deleted those transitional shards; `F_0043.py`, `F_0044.py`, `F_0045.py`,
`J_0014.py`, and `J_0033.py` own their scripts while continuing to reuse
the promoted intern, session, Feishu, skill, TreeView, source-contract,
workspace, task, and assertion wrappers.

Session 107 runner-slimming note: source-contract adapter builders and skill
git fixture composers are no longer declared in `CI/runner/remote_worker.py`.
Source-contract orchestration now goes through
`source_contract.deployed_contract`, and git skill fixture setup now goes
through `skill.remote_git_source_fixture` /
`skill.remote_update_git_source_fixture`. `remote_worker.py` retains only the
small `deployed_source_contract()` adapter that applies the shared contract and
stores the case artifact.

Session 108 shard deletion note, updated by Session 113 C2: `CI/cases/F/remote_*.py`
and `CI/cases/J/remote_journeys.py` are deleted. Remote case-id dispatch now
lives in `CI/cases/registry.py` as lazy `REMOTE_CASE_RUNNERS` paths, and each
entry points at the owning case module function. This did not add a new
action/assertion row because dispatch is case registry infrastructure, not
product behavior.

Session 109 final audit note, updated by Session 113 C2: the promoted rows
remain valid after the transitional shard deletion. Remote dispatch coverage is
42 remote cases to 42 case-registry runner entries with no missing or extra
entries. There are no active imports of deleted `CI.cases.F.remote_*` or
`CI.cases.J.remote_journeys` modules, and no new action/assertion row is needed
for remote dispatch.

Session 114 C3 note: mock Feishu source-driver fixtures moved from
`CI/runner/remote_worker.py` to `CI/helpers/mock_feishu_helper.py`. This is a
helper ownership correction, not a new action/assertion id: the existing
Feishu action metadata remains authoritative, and source-driver F cases now use
`self.mock_feishu.*` for card parsing and fake source-driver evidence.

Session 117 C4 note: active F/J cases no longer call `NativeRemoteCase`
workspace/intern/session/tmux/green-light/restart/provider-live compatibility
methods. Case scripts now call action roots directly for these operations, with
case-local classifier helpers only where product-bug classification differs
between F no-prompt, J paid-agent, Codex, and Claude restart semantics.

Session 119 C5 note: active F/J cases no longer call `NativeRemoteCase`
task/TreeView/skill/source/policy/group/relay compatibility wrappers. Case
scripts now call existing `task`, `skill`, `source_contract`, `treeview`,
`feishu`, and `relay_daemon` action/assertion surfaces directly. Where a case
still needs legacy report shape, the conversion stays in the owning case rather
than in a runner wrapper.

Session 128 C7.2 note: active F/J workspace cases no longer call
`NativeRemoteCase` workspace/source-control wrappers for nonprotected/GitHub
repo resolution, workspace create argv construction, failed create attempts,
workspace record/sync/absent/no-extra-record checks, metadata root and
metadata-branch checks, or business branch revision checks. Those surfaces now
live in `WorkspaceActions` and `workspace_assertions`, with guard coverage in
`test_ci_remote_case_dispatch.py`.

Session 129 C7.3 note: active F/J cases and remaining worker internals no
longer call `NativeRemoteCase.status_json()`, `NativeRemoteCase.runtime_dir()`,
or `NativeRemoteCase.session_registry()`. Status reads use the existing
`intern.status_json_remote` action, runtime directory resolution uses the new
`intern.remote_runtime_dir` action, and session registry reads use the new
`session.remote_registry` action. C7.3 is still in progress for the larger
intern/session/tmux/status surface.

Session 130 C7.3 note: active F/J cases no longer call
`NativeRemoteCase.create_fixture_intern()` or
`NativeRemoteCase.assert_no_team_or_non_codex_fixture()`. Fixture creation uses
`intern.remote_create_fixture_case`, which composes the raw fixture create
action and returns `fixture_intern_contract_checks()` rows for
`self.require_checks(...)`. No-team/non-Codex fixture sanity uses
`intern.remote_no_team_or_non_codex_fixture` plus
`no_team_or_non_codex_fixture_checks()`.

Session 131 C7.3 note: `NativeRemoteCase.session_start_for_workspace()`,
`NativeRemoteCase.session_stop_for_workspace()`,
`NativeRemoteCase.session_status_for_workspace()`, `is_session_online()`, and
`is_codex_online()` are deleted. Active F/J cases and remaining worker internals
use the existing session action roots directly for start/stop/status evidence;
no new action id was needed.

Session 132 C7.3 note: Codex session-id/resume provider wrappers are deleted
from `NativeRemoteCase`. F_0009, F_0027, F_0033, and J_0033 now use
`ctx.action.session.codex_session_id_evidence_remote()`,
`ctx.action.session.wait_resume_this_intern_hint_remote()`,
`ctx.action.session.wait_codex_live_after_manual_resume_remote()`, and
`session_assertions.*` directly. No new action/assertion id was needed; the
only new shared report helper is
`RemoteCaseLifecycleMixin.require_classified_checks(...)`, which applies
classified assertion result rows without reintroducing worker adapters.

Session 133 C7.3 note: basic tmux capture/send/wait wrappers, session restart
report wrapper, provider live/process/env wrappers, and the Claude policy token
evidence wrapper are deleted from `NativeRemoteCase`. Existing session action
roots are now used directly for tmux, restart, provider-live, process, and env
evidence. The existing registry id `claude.prepare_policy_token` now has a real
ctx-action implementation at
`ctx.action.session.prepare_claude_policy_token_remote`; F_0043 calls that
action directly and no longer depends on a worker method for redacted Claude
policy materialization evidence.

Session 134 C7.3 note: metadata/status/session-map projection wrappers are
deleted from `NativeRemoteCase`. New reusable intern action roots collect
single intern list item evidence, metadata/status/session type consistency
checks, and tree projection checks. A new session action root writes
case-scoped `.intern_sessions.json` entries for active-chat/TreeView fixtures.
Active F_0007, F_0026, F_0034, F_0043, and F_0044 call these actions directly
and apply returned checks with `self.require_checks(...)`.

Session 136 C7.3 note: policy/reconnect behavior is no longer owned by
`NativeRemoteCase`. `CI/actions/policy.py` now owns machine_config marker
mutation/restore, daemon policy fingerprint, redacted session/Codex env
reports, policy session fingerprints, policy-triggered restart waits,
unchanged-policy duplicate-restart checks, relay machine online/offline waits,
single-daemon start/restart evidence, daemon status/machine-id reads, and the
no-relay-restart/global-reset safety assertion. F_0034 and F_0037 call
`ctx.action.policy.*` directly.

Session 137 C7.3 note: cleanup/delete lifecycle evidence is no longer owned by
`NativeRemoteCase` worker wrappers for no-artifacts, removed-intern, fixture
intern cleanup, intern list JSON, or session map entry deletion. F_0008,
F_0010, F_0025, and F_0026 call intern/session action roots directly, while the
remaining runner lifecycle cleanup path composes `intern.remote_cleanup_fixture`
instead of carrying cleanup logic in the worker.

Session 120 C6 note: no new reusable action/assertion id was added. The removed
dialogue task/merge helpers and BUG_0010 Codex RUI runner were dead, unregistered
legacy paths. The only active J_0033 token wait is intentionally case-local
because it is a paid-agent journey assertion tied to that scenario's prompt.

- When moving logic out of `remote_*.py` or `remote_worker.py`, add or update a
  row here with the promoted action/assertion id and a scan-based reuse count.
- If a promoted item is only used once, record why it is semantically reusable.
- Do not count scenario ids as reuse; count product-operation or assertion call
  sites.
