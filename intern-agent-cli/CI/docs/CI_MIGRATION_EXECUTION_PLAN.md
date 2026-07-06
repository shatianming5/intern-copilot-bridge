# CI Migration Execution Plan

This document is retained only because `CI_ARCHITECTURE_DESIGN.md` referred to
an execution plan during migration. The active migration plan for task416 is
`CI_ACTION_ASSERTION_MIGRATION_PLAN.md`.

Current execution state:

- `CI_ACTION_ASSERTION_MIGRATION_PLAN.md` is the active tracker.
- Original Plan vS92 rounds R1-R16 are complete.
- Session 112 completed C1/7 by deleting `CI/cases/F/local_intern_session_cli.py`
  and moving the then-active local F fixture runner into `F_0052.py`.
- Session 113 completed C2/7 by moving `REMOTE_CASE_RUNNERS` and remote case-id
  dispatch ownership from `CI/runner/remote_worker.py` into `CI/cases/registry.py`.
- Session 114 completed C3/7 by moving source-driver mock Feishu fake classes,
  message/card evidence, machine-config fixture helpers, and card parsers into
  `CI/helpers/mock_feishu_helper.py`.
- Session 117 completed C4/7 by cutting active workspace/intern/session/tmux,
  green-light, restart/resume, and provider-live case scripts over to direct
  action/assertion/helper usage.
- Session 119 completed C5/7 by cutting active task/TreeView/skill/source/
  policy/group/relay case scripts over to direct action/assertion/helper usage.
- Session 120 completed C6/7 by removing dead J/dialogue-only runner helpers
  from `remote_worker.py` and moving the active J_0033 token wait into the
  owning case.
- Session 121 rejected a C7 implementation that would have deleted
  `CI/runner/remote_worker.py` by moving its class body into a new `RemoteCase`
  monolith. That approach preserves the wrong responsibility boundary.
- Session 122 expands C7 into nine bounded rounds, C7.1-C7.9. Current tracker
  state moved forward in Session 128.
- Session 125 completed C7.1 by moving generated action registry specs/resource
  locks out of action implementation modules into `CI/actions/registry_data.py`.
  Current tracker state moved to C7 expansion 1/9 complete.
- Session 126 advanced C7.2 by moving local repo fixture creation to
  `ctx.action.workspace.local_repo_fixture_remote()`, cutting active F/J cases
  and skill git-source fixtures over to that action, deleting the old worker
  local repo adapter and dead unregistered runner scenario bodies, and replacing
  workspace entry/local-enabled/display helpers with action/assertion surfaces.
- Session 128 completed C7.2 by moving workspace record/sync/absent/no-extra
  checks, metadata root/metadata-branch checks, business branch baseline/head
  evidence, nonprotected/GitHub repo resolution, create argv construction, and
  failed create attempts out of `NativeRemoteCase` into
  `ctx.action.workspace.*` plus `workspace_assertions.*`. Current tracker state
  is original R1-R16 complete, correction C1-C6 complete, C7 expansion 2/9
  complete, 7/9 remaining. Current round is C7.3: cut over
  intern/session/tmux/status residual adapters.
- Session 129 advanced C7.3 but did not complete it. The first C7.3 batch moved
  `status_json()`, `runtime_dir()`, and `session_registry()` call sites to
  `ctx.action.intern.status_json_remote()`,
  `ctx.action.intern.runtime_dir_remote()`, and
  `ctx.action.session.registry_remote()`, deleted the corresponding
  `NativeRemoteCase` wrappers, and added guard coverage. Tracker state remains
  C7 expansion 2/9 complete, 7/9 remaining, with C7.3 in progress.
- Session 130 advanced C7.3 but did not complete it. The second C7.3 batch moved
  fixture intern creation and no-team/non-Codex fixture sanity to
  `ctx.action.intern.create_fixture_case_remote()` and
  `ctx.action.intern.no_team_or_non_codex_fixture_remote()`, deleted the
  corresponding `NativeRemoteCase` wrappers, and extended guard coverage.
  Tracker state remains C7 expansion 2/9 complete, 7/9 remaining, with C7.3 in
  progress.
- Session 131 advanced C7.3 but did not complete it. The third C7.3 batch
  deleted the session start/stop/status convenience wrappers and online helpers
  from `NativeRemoteCase`; active cases and remaining worker internals now call
  existing `ctx.action.session.*_for_workspace_remote()` actions directly.
  Tracker state remains C7 expansion 2/9 complete, 7/9 remaining, with C7.3 in
  progress.
- Session 132 advanced C7.3 but did not complete it. The fourth C7.3 batch
  deleted Codex session-id/resume provider wrappers from `NativeRemoteCase`;
  active Codex F/J cases now call existing session action roots and
  `session_assertions.*` directly, with
  `RemoteCaseLifecycleMixin.require_classified_checks(...)` handling classified
  result rows. Tracker state remains C7 expansion 2/9 complete, 7/9 remaining,
  with C7.3 in progress.
- Session 133 advanced C7.3 but did not complete it. The fifth C7.3 batch
  deleted tmux capture/send/wait wrappers, the session restart wrapper,
  provider live/process/env wrappers, and the Claude policy-token wrapper from
  `NativeRemoteCase`; F_0043 now uses
  `ctx.action.session.prepare_claude_policy_token_remote()`. Tracker state
  remains C7 expansion 2/9 complete, 7/9 remaining, with C7.3 in progress.
- Session 134 advanced C7.3 but did not complete it. The sixth C7.3 batch
  promoted intern list item, metadata/status consistency, tree projection, and
  session registry entry writes to action roots, then deleted the matching
  worker wrappers. Tracker state remains C7 expansion 2/9 complete, 7/9
  remaining, with C7.3 in progress.
- Session 136 advanced C7.3 but did not complete it. The seventh C7.3 batch
  promoted policy/reconnect behavior into `CI/actions/policy.py`, converted
  policy registry entries to real ctx actions, cut F_0034/F_0037 over to
  `ctx.action.policy.*`, and deleted the matching worker helpers. Tracker state
  remains C7 expansion 2/9 complete, 7/9 remaining, with C7.3 in progress
  because cleanup/delete lifecycle adapters remain.
- Session 137 advanced C7.3 but did not complete it. The eighth C7.3 batch
  promoted cleanup/delete lifecycle evidence into intern/session action roots,
  cut F_0008/F_0010/F_0025/F_0026 over to those roots, and deleted the matching
  worker wrappers. Tracker state remains C7 expansion 2/9 complete, 7/9
  remaining, with C7.3 in progress because other residual worker adapters
  remain.
- Session 138 advanced C7.3 but did not complete it. The ninth C7.3 batch
  deleted thin lifecycle wrappers for case reset, session key formatting,
  fixture workspace cleanup, metadata resolver, intern checkout repo lookup,
  delete intern/workspace, and unscoped session start/status/stop. Tracker state
  remains C7 expansion 2/9 complete, 7/9 remaining, with C7.3 in progress
  because other residual worker adapters remain.
- Session 139 advanced C7.4 but did not complete it. The first Feishu/relay
  residual batch cut active F/J cases over to `ctx.action.feishu.*` and
  `ctx.action.relay_daemon.*`, then deleted worker wrappers for chat lookup,
  question polling, owner identity, relay registry, green-light, group config,
  relay/daemon lookup, no-group fixtures, and fixture daemon restart. Tracker
  state remains C7 expansion 2/9 complete, 7/9 remaining.
- Session 140 advanced the source/TreeView residual surface but did not count a
  round complete. Focused tests now call `ctx.action.source_contract.*` and
  `ctx.action.treeview.*` directly, and `NativeRemoteCase` no longer exposes
  extension source candidate, product source evidence, deployed source
  contract, TreeView projection, workspace tree projection, or context-menu
  command wrappers. Tracker state remains C7 expansion 2/9 complete, 7/9
  remaining.
- Session 141 advanced the task/skill/metadata-root residual surface but did
  not count a round complete. `workspace.remote_metadata_root` was added as a
  reusable action, active F/J cases were cut over to it or to direct task/skill
  action roots, and `NativeRemoteCase` no longer exposes task TreeView seeding,
  skill CLI/config/farm/source, metadata-root, task grouping/tooltip, or skill
  JSON report wrappers. Tracker state remains C7 expansion 2/9 complete, 7/9
  remaining.
- Session 142 advanced the mock Feishu source-driver residual surface but did
  not count a round complete. Remote-aware source-driver context/message/card
  helpers now live on `MockFeishuHelper`, active source-driver F cases call
  `self.mock_feishu.*` directly, and `NativeRemoteCase` no longer exposes
  relay-driver context/message/card/evidence/config wrappers. Tracker state
  remains C7 expansion 2/9 complete, 7/9 remaining.
- Session 143 advanced the mock TreeView/GUI residual surface but did not count
  a round complete. Workspace GUI-equivalence cases now compose
  `self.mock_treeview.cli_equivalence()` with
  `surface_assertions.treeview_cli_equivalent_detail()`, focused tests call
  `MockTreeViewHelper` directly, and `NativeRemoteCase` no longer exposes
  TreeView event/equivalence/no-group allocation wrappers. Tracker state
  remains C7 expansion 2/9 complete, 7/9 remaining.
- Session 144 advanced the workspace/stage residual surface but did not count a
  round complete. The unused `NativeRemoteCase.reset_stage_workspace_namespace()`
  implementation and its workspace registry polling helpers were deleted; the
  existing workspace action roots remain the reset owners. `remote_worker.py` is
  now below 500 lines, but the design target still requires deleting the worker
  import path. Tracker state remains C7 expansion 2/9 complete, 7/9 remaining.
- Session 145 advanced the remote context residual surface but did not count a
  round complete. `NativeRemoteCase` delegates pure context attributes and
  command/HTTP/identity/stage helpers to `RemoteCaseContext`, deleting duplicate
  field mirroring and pass-through methods. `remote_worker.py` is now 284 lines;
  the design target still requires deleting the worker import path. Tracker
  state remains C7 expansion 2/9 complete, 7/9 remaining.
- Session 146 advanced the reset evidence residual surface but did not count a
  round complete. `workspace.remote_case_initial_reset_evidence` was added as a
  registered workspace action, active reset scenarios call it directly, and
  `NativeRemoteCase.reset_namespace_evidence()` was deleted. Tracker state
  remains C7 expansion 2/9 complete, 7/9 remaining.
- Session 147 advanced the context naming residual surface but did not count a
  round complete. `task_id()` and `file_name()` moved from `NativeRemoteCase` to
  `RemoteCaseContext`, with focused tests covering proxy delegation. Tracker
  state remains C7 expansion 2/9 complete, 7/9 remaining.
- Session 148 advanced the assertion residual surface but did not count a round
  complete. `require_http_status()` moved from `NativeRemoteCase` to
  `CI.assertions.core`, F_0012/F_0013/J_0014 call the assertion helper directly,
  and guard tests prevent the worker method or active-case wrapper call from
  returning. `remote_worker.py` is now 244 lines; tracker state remains C7
  expansion 2/9 complete, 7/9 remaining.
- Session 149 advanced the daemon/relay residual surface but did not count a
  round complete. `wait_daemon_log_contains()` moved from `NativeRemoteCase` to
  `RelayDaemonActions.wait_daemon_log_contains_remote()`, F_0015 calls the
  action root directly, and guard tests prevent the worker method or
  active-case wrapper call from returning. `remote_worker.py` is now 203 lines;
  tracker state remains C7 expansion 2/9 complete, 7/9 remaining.
- Session 150 advanced the workspace/intern convenience residual surface but did
  not count a round complete. `create_workspace()`, `workspace_list()`,
  `workspace_doctor()`, and `create_intern()` were deleted from
  `NativeRemoteCase`; tests now call workspace/intern action roots directly, and
  guard tests prevent these worker definitions from returning. `remote_worker.py`
  is now 157 lines; tracker state remains C7 expansion 2/9 complete, 7/9
  remaining.
- Session 151 advanced the reporting/lifecycle residual surface but did not
  count a round complete. Unused worker helpers `best_effort_cleanup()`,
  `_repo_mode_expected()`, and `_transport_health()` were deleted; contract
  scenario recording moved to `RemoteCaseLifecycleMixin`, and failure
  classification moved to `CI.helpers.reporting`. `remote_worker.py` is now 107
  lines and `runner/reporting.py` is 462 lines; tracker state remains C7
  expansion 2/9 complete, 7/9 remaining.
- Session 152 completed C7.8 and started C7.9. The final remote argv entrypoint
  moved into the existing stage owner as `CI.runner.stage_3_F.run_remote_case_argv()`
  with the thin `StageRemoteCase` bridge; package/deploy and VSIX verification
  now require `CI/runner/stage_3_F.py`; tests no longer import
  `CI.runner.remote_worker` or `NativeRemoteCase`; and
  `CI/runner/remote_worker.py` was deleted. Tracker state is now C7 expansion
  8/9 complete, 1/9 remaining, with the final oversized-file reasonability
  audit still open.
- Session 154 advanced C7.9. `stage_3_F.py` no longer carries legacy
  `setup_basic`, `cli_create_intern`, `mock_feishu`, or `f_runtime_foundation`
  execution branches; native report summary compaction moved to
  `CI.helpers.reporting`; F_0052 local aggregation moved to the owning case;
  and `stage_3_F.py` is now 497 lines. Focused validation, registry/list gates,
  `git diff --check`, and repo-root `tmp/` checks passed. C7.9 remains open
  because `CI/full_primitives.py` is still a 1394-line root-level deploy
  primitive file requiring the next ownership/split review.
- Session 158 superseded the intermediate F0052 local fixture: `F_0052` is now a
  real remote F case, `stage_3_F.run_f_local_cases()` is removed, and preflight
  rejects F/J cases whose `CaseDefinition.stage` is not `remote`.
- Session 155 advanced C7.9 by deleting root-level `CI/full_primitives.py` and
  splitting deployment primitives into design-owned `CI/helpers/deployment_*`
  modules plus shared `CI/helpers/remote_machine_helper.py` SSH/remote-machine
  utilities. The thin compatibility import facade is
  `CI.helpers.deployment_primitives`; all deployment helpers are below 500
  lines. Focused validation, registry/list gates, `git diff --check`,
  repo-root `tmp/` absence, and root `CI/full_primitives.py` absence passed.
- Session 156 completed C7.9. The final oversized-file audit classifies every
  remaining >500-line CI Python file as an accepted generated registry data
  file, domain action/helper owner, owning F/J case script, or test-only guard
  matrix. Forbidden legacy paths remain absent, repo-root `tmp/` is absent, and
  focused validation passed. Current tracker state is original R1-R16 complete,
  correction C1-C6 complete, C7 expansion 9/9 complete, remaining 0/9.
- Use `CI_ARCHITECTURE_DESIGN.md` for the target architecture.
- Use `docs/F/` and `docs/J/` for F/J scripts and proposals.
- Use `intern-cli/CI/tests/` for CI system tests.
- `CI_MIGRATION_ROUND_PLAN.md` is historical and should not be used as the
  current tracker.

Do not use this file as an authoring guide. It intentionally does not list old
intermediate paths, retired c-series cases, removed case slots, or deleted
temporary migration modules.
