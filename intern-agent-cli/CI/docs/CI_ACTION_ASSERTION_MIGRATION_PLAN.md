# CI Action/Assertion Migration Plan

<!-- Updated: Session 156 -->

This plan governs the remaining CI migration work for task416. It exists to
avoid unbounded one-helper-at-a-time migration and to make each session report
progress against a fixed batch/round list.

## Baseline

Session 92 baseline:

- Active cases: 43 total, 41 F and 2 J.
- Transitional remote execution code still present:
  - `intern-cli/CI/runner/remote_worker.py`: 4903 lines.
  - `intern-cli/CI/cases/F/remote_*.py`: 6871 lines across 6 files.
  - `intern-cli/CI/cases/J/remote_journeys.py`: 525 lines.
- Reports already maintained:
  - `intern-cli/CI/docs/CI_ACTION_ASSERTION_REUSE_REPORT.md`
  - `intern-cli/CI/docs/CI_NOT_PROMOTED_ACTION_ASSERTION_REPORT.md`
- Promoted entries currently tracked in the reuse report: 14.
- Final target remains unchanged: reusable behavior belongs in `actions/`,
  `assertions/`, or approved helpers; case-specific scripts belong in
  `F_XXXX.py` / `J_XXXX.py`; transitional `remote_*.py` shards are removed.

## Progress Contract

At the start and end of each future migration session, report:

```text
Plan vS92: Batch B?/6, Round R?/16
Completed rounds: N/16
Remaining rounds: M/16
This round scope: <named batch item>
Expected report changes: <reuse/not-promoted rows>
Expected deletion/shrink target: <files or adapters affected>
```

If a hidden dependency changes the round count, update this document first and
state the reason before doing more migration code. The design itself is not
changed by updating the accounting.

## Current Execution Ledger

Session 137 correction execution:

- Session 109 correctly verified that transitional `CI/cases/F/remote_*.py`
  shards and `CI/cases/J/remote_journeys.py` were deleted.
- Session 109 incorrectly treated that as full migration close. Two active
  design-incompatible files remain:
  - `intern-cli/CI/cases/F/local_intern_session_cli.py` is not a case
    definition; it is a local fixture runner for the current local F case
    `F_0052_session_resume_cli_claude_contract`.
  - `intern-cli/CI/runner/remote_worker.py` is still the active remote case
    worker imported by `stage_3_F.native_remote_case_script()`.
- The design is unchanged. The execution tracker is reopened only to remove
  these missed active implementation files.
- The C1-C6 correction rounds are complete. The previous C7 wording was too
  coarse: deleting `CI/runner/remote_worker.py` by moving its class body into a
  new `RemoteCase` object would preserve the monolith under a new name. Session
  121 rejected that approach and kept C7 pending.
- Session 122 fixes the remaining plan at 9 rounds, C7.1-C7.9, which satisfies
  the supervisor limit of at most 10 more rounds. A round is complete only when
  the named file/surface is moved or deleted, focused validation passes,
  reports are updated, and the branch is pushed.
- C7.1 is complete: generated `ACTION_SPECS` / `ACTION_RESOURCE_LOCKS` data was
  moved from action implementation modules into `CI/actions/registry_data.py`.
  `CI/actions/policy.py` was deleted because it only contained registry data.
  `CI/actions/registry.py` now reads specs/locks from the registry data layer,
  while action modules retain behavior classes/functions.
- C7.2 is complete: `create_local_repo()` moved from `NativeRemoteCase` into
  `ctx.action.workspace.local_repo_fixture_remote()` and all active F/J callers
  plus skill git-source fixture creation were cut over. Dead unregistered
  runner scenario bodies that depended on the old local repo adapter were
  deleted. `workspace_display()`, `_workspace_entry()`, and `_local_enabled()`
  worker helpers were replaced by `workspace_assertions.workspace_display()`,
  `ctx.action.workspace.entry_remote()`, and
  `workspace_assertions.workspace_local_enabled()`. Session 128 finished the
  same round by moving nonprotected/GitHub repo resolution, workspace create
  argv construction, failed create attempts, workspace record/sync/absent/no
  extra-record checks, metadata root/metadata-branch checks, and business branch
  revision checks into `ctx.action.workspace.*` plus
  `workspace_assertions.*`. The corresponding `NativeRemoteCase` compatibility
  methods are deleted and guarded against reintroduction.
- C7.3 is in progress. Session 129 completed the first C7.3 batch by moving
  `status_json()`, `runtime_dir()`, and `session_registry()` call sites to
  action roots: `ctx.action.intern.status_json_remote()`,
  `ctx.action.intern.runtime_dir_remote()`, and
  `ctx.action.session.registry_remote()`. The corresponding
  `NativeRemoteCase` compatibility methods are deleted and guarded against
  reintroduction. Session 130 completed the second C7.3 batch by moving fixture
  intern creation and no-team/non-Codex fixture sanity to
  `ctx.action.intern.create_fixture_case_remote()` and
  `ctx.action.intern.no_team_or_non_codex_fixture_remote()`, with reusable
  assertion checks applied through `self.require_checks(...)`. The
  corresponding `NativeRemoteCase` compatibility methods are deleted and
  guarded against reintroduction. Session 131 completed the third C7.3 batch by
  deleting `session_start_for_workspace()`, `session_stop_for_workspace()`,
  `session_status_for_workspace()`, `is_session_online()`, and
  `is_codex_online()` worker adapters; active cases and worker internals now
  use `ctx.action.session.start_for_workspace_remote()`,
  `ctx.action.session.stop_for_workspace_remote()`, and
  `ctx.action.session.status_for_workspace_remote()` directly. Session 132
  completed the fourth C7.3 batch by deleting Codex session-id/resume provider
  helper wrappers from `NativeRemoteCase`; active Codex F/J cases now call
  `ctx.action.session.codex_session_id_evidence_remote()` and the shared
  `session_assertions.*` checks directly, with report classification applied
  through `RemoteCaseLifecycleMixin.require_classified_checks(...)`. C7.3 is
  not counted complete yet. Session 133 completed the fifth C7.3 batch by
  deleting basic tmux capture/send/wait worker wrappers, the session restart
  report wrapper, provider live/process/env wrappers, and the Claude policy
  token evidence wrapper. `claude.prepare_policy_token` is now a real
  `ctx.action.session.prepare_claude_policy_token_remote` action. Remaining
  residuals include metadata/status/session-map adapters and policy/reconnect
  helpers still owned by the worker. Session 134 completed the sixth C7.3 batch
  by promoting intern list item, metadata/status consistency, tree projection,
  and session registry entry writes to action roots. Active cases now call
  `ctx.action.intern.*` and `ctx.action.session.write_registry_entry_remote()`
  directly, and the matching worker wrappers are deleted. Session 136 completed
  the seventh C7.3 batch by reintroducing `CI/actions/policy.py` as a real
  behavior root, converting `policy.machine_config_marker` and
  `policy.daemon_sync_existing_deployment` from stage contracts into ctx
  actions, adding the shared policy/reconnect action surfaces, cutting F_0034
  and F_0037 over to `ctx.action.policy.*`, and deleting the matching worker
  helpers. Session 137 completed the eighth C7.3 batch by moving fixture intern
  cleanup, no-artifacts evidence, removed-intern evidence, intern list JSON, and
  session registry entry deletion to intern/session action roots. F_0008,
  F_0010, F_0025, and F_0026 now call those roots directly, and the worker no
  longer defines the matching wrappers. Session 138 completed the ninth C7.3
  batch by deleting thin lifecycle wrappers for case reset, session registry
  key formatting, fixture workspace cleanup, metadata resolver, intern checkout
  repo lookup, delete intern/workspace, and unscoped session start/status/stop.
  Runner entrypoints now call the existing action roots directly. C7.3 is still
  not counted complete because other residual adapters remain in the worker.
- Session 139 advanced the C7.4 Feishu/group residual surface but did not count
  the round complete. Active F/J cases no longer call worker wrappers for
  chat lookup, question polling, or owner identity; they call
  `ctx.action.feishu.*` / `ctx.action.relay_daemon.*` directly. The matching
  thin worker wrappers for relay registry lookup, green-light polling, group
  config/mode, relay/daemon chat lookup, no-group fixtures, daemon restart, and
  relay registry absent evidence were deleted and guarded against
  reintroduction.
- Session 140 advanced the source/TreeView residual surface but did not count a
  round complete. Source evidence, deployed source contract, TreeView
  projection, workspace tree projection, and context-menu command thin wrappers
  were deleted from `NativeRemoteCase`. Tests now compose the existing
  `ctx.action.source_contract.*` and `ctx.action.treeview.*` roots directly, and
  guard coverage rejects restoring those worker definitions.
- Session 141 advanced the task/skill/metadata-root residual surface but did
  not count a round complete. Workspace metadata-root resolution was promoted
  to `ctx.action.workspace.metadata_root_remote()`, active F/J cases no longer
  call worker task/skill/metadata-root helpers, and task/skill report shaping
  that is case-specific now lives in owning case files.
- Session 142 advanced the mock Feishu source-driver residual surface but did
  not count a round complete. Active slash/config/helper source-driver cases
  now call `self.mock_feishu.*` remote-aware helper methods directly, and the
  relay-driver context/message/card/action/config worker wrappers were deleted
  from `NativeRemoteCase`.
- Session 143 advanced the mock TreeView/GUI residual surface but did not count
  a round complete. Active workspace GUI-equivalence cases now compose
  `self.mock_treeview.cli_equivalence(...)` with
  `surface_assertions.treeview_cli_equivalent_detail(...)`, focused tests call
  `MockTreeViewHelper` directly, and TreeView event/equivalence wrappers were
  deleted from `NativeRemoteCase`.
- Session 144 advanced the workspace/stage residual surface but did not count a
  round complete. Dead `NativeRemoteCase` stage workspace reset and workspace
  registry polling helpers were deleted; the existing
  `ctx.action.workspace.reset_stage_namespace_remote()` /
  `case_initial_reset_remote(...)` action roots remain the only reset
  implementations.
- Session 145 advanced the remote context residual surface but did not count a
  round complete. `NativeRemoteCase` now delegates missing attributes and pure
  command/HTTP/identity/stage helpers to `RemoteCaseContext` through
  `__getattr__`, removing duplicate field mirroring and pass-through methods
  without adding a new runner/helper file.
- Session 146 advanced the reset evidence residual surface but did not count a
  round complete. `workspace.remote_case_initial_reset_evidence` now owns the
  reusable reset-evidence check; active F/J cases call that action root through
  `self.require_checks(...)`, and `NativeRemoteCase.reset_namespace_evidence()`
  was deleted.
- Session 147 advanced the context naming residual surface but did not count a
  round complete. Case-scoped `task_id()` / `file_name()` naming helpers moved
  to `RemoteCaseContext`, and the corresponding `NativeRemoteCase` methods were
  deleted. Active cases still resolve `self.task_id(...)` through the existing
  context proxy while the worker import path remains transitional.
- Session 148 advanced the assertion residual surface but did not count a round
  complete. HTTP status checks moved from `NativeRemoteCase.require_http_status()`
  to `CI.assertions.core.require_http_status(...)`; F_0012, F_0013, and J_0014
  call the assertion helper directly while preserving the prior
  `status_code`/`body` evidence shape. The matching worker method was deleted
  and guard coverage prevents active cases or the worker from restoring it.
  Latest delta: `remote_worker.py` 268 -> 244, `assertions/core.py` 277 -> 319,
  with registry counts unchanged at actions=353 and assertions=114.
- Session 149 advanced the daemon/relay residual surface but did not count a
  round complete. Daemon log marker polling moved from
  `NativeRemoteCase.wait_daemon_log_contains()` into
  `ctx.action.relay_daemon.wait_daemon_log_contains_remote()`, registered as
  `relay_daemon.remote_wait_daemon_log_contains`; F_0015 calls the action root
  directly, and guard coverage prevents active cases or the worker from
  restoring the wrapper. Latest delta: `remote_worker.py` 244 -> 203,
  `relay_daemon.py` 96 -> 125, `registry_data.py` +19 data lines, actions=354
  and assertions=114.
- Session 150 advanced the workspace/intern convenience residual surface but
  did not count a round complete. `NativeRemoteCase.create_workspace()`,
  `workspace_list()`, `workspace_doctor()`, and `create_intern()` were deleted;
  focused tests now call `ctx.action.workspace.create_case_remote()`,
  `list_remote()`, `doctor_remote()`, and the existing intern action roots
  directly. Guard coverage prevents these worker methods from returning.
  Latest delta: `remote_worker.py` 203 -> 157, with cases=43, actions=354, and
  assertions=114 unchanged.
- Session 151 advanced the reporting/lifecycle residual surface but did not
  count a round complete. Unused worker helpers `best_effort_cleanup()`,
  `_repo_mode_expected()`, and `_transport_health()` were deleted. The active
  contract scenario recorder moved from `NativeRemoteCase` to
  `RemoteCaseLifecycleMixin`, and failure classification moved to the existing
  `CI.helpers.reporting` helper owner so runner/reporting stayed below 500
  lines. Latest delta: `remote_worker.py` 157 -> 107, `runner/reporting.py`
  502 -> 462, `helpers/reporting.py` 98 -> 164, with cases=43, actions=354,
  and assertions=114 unchanged.
- C1 is complete: `CI/cases/F/local_intern_session_cli.py` was deleted. The
  intermediate local F runner in `CI/cases/F/F_0052.py` was superseded in
  Session 158; `F_0052` now runs as a real remote F case.
- C2 is complete: remote case-id dispatch ownership moved from
  `CI/runner/remote_worker.py` to `CI/cases/registry.py`. The runner worker now
  resolves case-owned functions through the case registry and no longer imports
  all `F_XXXX.py` / `J_XXXX.py` modules.
- C3 is complete: source-driver mock Feishu fake API/registry/websocket classes,
  source-driver module loading, message/card ingress evidence construction, and
  card parser helpers moved to `CI/helpers/mock_feishu_helper.py`. The runner
  worker keeps only compatibility wrappers for source-driver cases.
- C4 is complete: active F/J case scripts no longer call `NativeRemoteCase`
  compatibility methods for workspace/intern/session/tmux/green-light/restart
  operations. Case scripts now call `ctx.action.workspace.*`,
  `ctx.action.intern.*`, `ctx.action.session.*`, `ctx.action.feishu.*`, or
  case-local classifier helpers directly. A guard test rejects those C4 wrapper
  call tokens from active `F_XXXX.py` / `J_XXXX.py` files.
- C5 is complete: active F/J case scripts no longer call `NativeRemoteCase`
  compatibility methods for task fixtures, Task TreeView seed helpers, skill
  CLI/source/farm helpers, source-contract adapters, TreeView projection
  wrappers, Feishu group config/registry wrappers, or relay/daemon lookup
  wrappers. Case scripts now use existing action roots, source-contract and
  TreeView assertion helpers, or case-local conversion logic where a scenario
  still needs legacy report shape. A guard test rejects those C5 wrapper call
  tokens from active `F_XXXX.py` / `J_XXXX.py` files.
- C6 is complete: the remaining dialogue-only helpers, task/PR/merge waiters,
  old Codex RUI live-card dialogue runner, and `wait_tmux_token_count()` wrapper
  were removed from `CI/runner/remote_worker.py`. The active J_0033 token wait
  now lives as case-local journey logic. A guard test rejects C6 dialogue
  wrapper tokens from active cases and from the runner worker.

Current corrected execution checkpoint:

- Plan version: `vS92`.
- Original migration rounds: 16/16 complete.
- Correction C1-C6 rounds: complete.
- C7 expansion rounds: 9/9 complete.
- Remaining C7 expansion rounds: 0/9.
- Current correction position: C7.9 complete after final audit.
- Current correction scope: completed; remaining oversized files are classified
  with accepted design ownership and no design-outside helper or remote monolith
  remains.
- Latest delta: Session 154 removed the obsolete `setup_basic`,
  `cli_create_intern`, `mock_feishu`, and `f_runtime_foundation` execution
  branches from `stage_3_F.py`; moved native-report summary compaction into
  `CI.helpers.reporting`; moved F_0052 local-case aggregation into the owning
  `F_0052.py`; and added guard coverage preventing those legacy stage3 runner
  branches from returning. `stage_3_F.py` is now 497 lines, down from the
  Session 152 997-line state. `helpers/reporting.py` is 257 lines, `F_0052.py`
  is 489 lines, actions stayed at 354, assertions stayed at 114, and cases
  stayed at 43.
- Session 158 delta: `F_0052_session_resume_cli_claude_contract` is no longer a
  local mocked fixture. It is a real remote F case registered in
  `REMOTE_CASE_RUNNERS`; `stage_3_F.run_f_local_cases()` was removed; stage
  preflight rejects any F/J `CaseDefinition.stage != "remote"`; active registry
  gates show cases=43, F=41, J=2, actions=350, assertions=111, and no
  non-remote F/J cases.

Session 155 deployment primitive split delta:

- Deleted root-level `CI/full_primitives.py`.
- Added design-owned deployment helpers under `CI/helpers/`:
  `deployment_config.py`, `deployment_payloads.py`, `deployment_remote.py`,
  `deployment_provider_policy.py`, `deployment_services.py`, and the thin
  `deployment_primitives.py` facade.
- Extended `remote_machine_helper.py` with the shared SSH/remote-machine
  helpers used by deployment stages.
- Deployment helper line counts are below 500:
  `deployment_primitives.py` 65, `deployment_config.py` 121,
  `deployment_payloads.py` 380, `deployment_remote.py` 195,
  `deployment_provider_policy.py` 352, `deployment_services.py` 239,
  `remote_machine_helper.py` 337.

Session 156 final oversized-file audit:

| File | Lines | Current classification |
|---|---:|---|
| `CI/actions/registry_data.py` | 6093 | Reasonable generated/static registry metadata: 354 action specs and resource locks intentionally split away from action behavior in C7.1. |
| `CI/cases/F/F_0043.py` | 966 | Reasonable complex F case: Claude lifecycle/restart/resume contract with provider-specific scenario script kept in owning case. |
| `CI/helpers/mock_feishu_helper.py` | 830 | Reasonable domain helper: mock Feishu relay-driver API/registry/websocket and visible `[CI模拟]` evidence live under helpers, not runner. |
| `CI/actions/session.py` | 824 | Reasonable domain action root: session/tmux/provider actions reused by Codex, Claude, F, and J cases. |
| `CI/actions/workspace.py` | 746 | Reasonable domain action root: workspace CLI, metadata-root, source-control, reset, and evidence helpers. |
| `CI/actions/intern.py` | 635 | Reasonable domain action root: intern create/delete/status/metadata/runtime cleanup evidence. |
| `CI/actions/feishu_mock.py` | 624 | Reasonable action/self-test owner for mock Feishu ingress and relay source-driver checks; it remains an action root because it drives reusable mock ingress operations and its self-test CLI is part of that action surface. |
| `CI/cases/J/J_0033.py` | 513 | Reasonable complex J journey: paid-agent exit/resume same-session script belongs in owning J case. |
| `CI/tests/test_ci_entrypoints.py` | 1919 | Reasonable test-only integration/guard matrix across CLI entrypoints, stage dispatch, package/deploy policy, list gates, and deleted legacy imports; not runtime CI implementation. |
| `CI/tests/test_ci_workspace_intern_session_actions.py` | 1903 | Reasonable test-only action-root matrix covering workspace/intern/session/TreeView/Feishu behavior after wrapper removal; not runtime CI implementation. |
| `CI/tests/test_ci_remote_case_dispatch.py` | 608 | Reasonable test-only migration guard for remote dispatch and deleted wrapper surfaces. |
| `CI/tests/test_ci_source_contract_assertions.py` | 593 | Reasonable test-only source-contract matrix for task/workspace/skill/TreeView/Claude adapters. |

Session 156 completion gates:

- `intern-cli/CI/full_primitives.py`, `CI/runner/remote_worker.py`,
  `CI/native_remote.py`, `CI/remote_case_runner.py`, and
  `CI/helpers/remote_case_lifecycle.py` are absent.
- Repo-root `tmp/` is absent.
- Current >500-line audit has no unclassified runtime implementation file and
  no root-level or runner-owned monolith.
- Focused validation passed: py_compile, focused pytest, registry/list gates,
  and `git diff --check`.
- R7 completed the task/skill evidence helper surface by adding `CI/actions/task.py`
  and `CI/actions/skill.py`, promoting reusable task metadata fixture writes,
  intern status metadata updates, skill CLI execution, skill config reads, and
  skill farm/source path evidence. Remaining skill git-source composers are
  recorded as adapter-until-case-move because they mix repo fixture creation
  and case-specific git commits.
- R8 moved all eight workspace scenario bodies from the transitional
  `CI/cases/F/remote_workspace.py` shard into their owning `F_0001.py` through
  `F_0006.py`, `F_0021.py`, and `F_0022.py` files. The shard now only routes
  case ids to case-owned functions and no longer declares `run_f_workspace_*`
  bodies.
- R9 moved all five intern/session scenario bodies from the transitional
  `CI/cases/F/remote_intern_session.py` shard into their owning `F_0007.py`,
  `F_0008.py`, `F_0009.py`, `F_0010.py`, and `F_0033.py` files. The shard now
  only routes case ids to case-owned functions and no longer declares
  `run_f_*` bodies.
- R10 moved all nine config/helper scenario bodies from the transitional
  `CI/cases/F/remote_config_helper.py` shard into their owning `F_0015.py`
  through `F_0020.py`, `F_0035.py`, `F_0036.py`, and `F_0041.py` files. The
  shard now only routes case ids to case-owned functions and no longer declares
  `run_f_*` bodies.
- R11 moved all five daemon/relay scenario bodies from the transitional
  `CI/cases/F/remote_daemon_relay.py` shard into their owning `F_0011.py`,
  `F_0012.py`, `F_0013.py`, `F_0034.py`, and `F_0037.py` files. The shard now
  only routes case ids to case-owned functions and no longer declares
  `run_f_*` bodies.
- R12 moved all ten TreeView/task/skill scenario bodies from the transitional
  `CI/cases/F/remote_treeview_task_skill.py` shard into their owning
  `F_0023.py` through `F_0032.py` files. The shard now only routes case ids to
  case-owned functions and no longer declares `run_f_*` bodies.
- R13 moved all three Claude scenario bodies from `CI/cases/F/remote_claude.py`
  into `F_0043.py`, `F_0044.py`, and `F_0045.py`, and both J scenario bodies
  from `CI/cases/J/remote_journeys.py` into `J_0014.py` and `J_0033.py`. Both
  shards now only route case ids to case-owned functions and no longer declare
  scenario bodies.
- R14 moved source-contract scenario adapters and skill git fixture composers
  out of `CI/runner/remote_worker.py`: source-contract checks now go through
  `source_contract.deployed_contract`, git skill fixtures go through
  `skill.remote_git_source_fixture` /
  `skill.remote_update_git_source_fixture`, and the runner is down to 4320
  lines. The old `_fXXXX` source-contract wrapper names and
  `_create_git_skill_source()` / `_update_git_skill_source()` helper names are
  absent from active runner/case/test scans.
- R15 deleted the transitional shard files under `CI/cases/F/remote_*.py` and
  `CI/cases/J/remote_journeys.py`. Session 113 C2 moved the remaining
  `REMOTE_CASE_RUNNERS` dispatch map from `CI/runner/remote_worker.py` to
  `CI/cases/registry.py`, with entries pointing at owning `F_XXXX.py` /
  `J_XXXX.py` scenario functions. Active source scans have no imports of
  `CI.cases.F.remote_*` or `CI.cases.J.remote_journeys`.
- R16 ran the final migration audit and report close. The active registry has
  43 cases, 42 remote cases, and 42 remote runner entries; missing and extra
  runner entries are both empty. Forbidden transitional files are absent:
  `CI/cases/F/remote_*.py`, `CI/cases/J/remote_journeys.py`,
  `CI/remote_case_runner.py`, and `CI/helpers/remote_case_lifecycle.py`.
  Active scans show no stale shard imports, no repo-root `tmp`, and no
  unrecorded not-promoted action/assertion candidates.

Round completion is counted only when the round's full surface is handled:

1. Promoted reusable behavior is added under `CI/actions/` or
   `CI/assertions/`.
2. Case/wrapper behavior that remains in transitional files is recorded in
   `CI_NOT_PROMOTED_ACTION_ASSERTION_REPORT.md` with an accepted reason.
3. Focused tests and registry/list validation pass for the affected surface.
4. `CI_ACTION_ASSERTION_REUSE_REPORT.md`, task metadata, and this plan are
   updated.
5. The branch is committed and pushed.

## Round Ledger

| Round | Batch | Scope | Completion after round | Remaining after round | Current status |
|---:|---|---|---:|---:|---|
| R1 | B1 | Governance and source/menu closure | 1/16 | 15/16 | Complete |
| R2 | B2 | Feishu question surface | 2/16 | 14/16 | Complete |
| R3 | B2 | Daemon/relay registry lookup surface | 3/16 | 13/16 | Complete |
| R4 | B2 | Fixture setup and fixture assertions | 4/16 | 12/16 | Complete |
| R5 | B2 | Session/provider primitives | 5/16 | 11/16 | Complete |
| R6 | B3 | Source/package contract adapters | 6/16 | 10/16 | Complete |
| R7 | B3 | Task/skill evidence helpers | 7/16 | 9/16 | Complete |
| R8 | B4 | Case body relocation: workspace | 8/16 | 8/16 | Complete |
| R9 | B4 | Case body relocation: intern/session | 9/16 | 7/16 | Complete |
| R10 | B4 | Case body relocation: config/helper | 10/16 | 6/16 | Complete |
| R11 | B4 | Case body relocation: daemon/relay | 11/16 | 5/16 | Complete |
| R12 | B4 | Case body relocation: TreeView/task/skill | 12/16 | 4/16 | Complete |
| R13 | B4 | Case body relocation: Claude and J | 13/16 | 3/16 | Complete |
| R14 | B5 | Runner worker slimming | 14/16 | 2/16 | Complete |
| R15 | B5 | Shard deletion | 15/16 | 1/16 | Complete |
| R16 | B6 | Final audit and report close | 16/16 | 0/16 | Superseded by Session 110 correction audit |
| C1 | Correction | Delete/migrate local F fixture runner | C1/7 | 6/7 | Complete |
| C2 | Correction | Move remote dispatch ownership out of `remote_worker.py` | C2/7 | 5/7 | Complete |
| C3 | Correction | Move source-driver/mock Feishu relay-driver helpers | C3/7 | 4/7 | Complete |
| C4 | Correction | Cut over workspace/intern/session wrappers | C4/7 | 3/7 | Complete |
| C5 | Correction | Cut over task/TreeView/skill/source/policy wrappers | C5/7 | 2/7 | Complete |
| C6 | Correction | Move J/dialogue-only primitives to J cases/actions/assertions | C6/7 | 1/7 | Complete |
| C7 | Correction | Delete `CI/runner/remote_worker.py` and final audit | C7 expanded | 9/9 | Replanned in Session 122 |
| C7.1 | Correction | Move generated action registry data out of action implementation modules | C7.1/9 | 8/9 | Complete |
| C7.2 | Correction | Cut over local repo/workspace record/source-control adapters | C7.2/9 | 7/9 | Complete |
| C7.3 | Correction | Cut over intern/session/tmux/status residual adapters | C7.3/9 | 6/9 | Complete by Session 152: no active case imports `NativeRemoteCase`; generic session/tmux/status behavior is under context/actions/assertions or owning cases |
| C7.4 | Correction | Cut over Feishu relay-driver, question, green-light, group residual adapters | C7.4/9 | 5/9 | Complete by Session 152: Feishu/group behavior is under existing Feishu/relay actions and mock helpers; old worker file is deleted |
| C7.5 | Correction | Cut over task/metadata/source-contract/TreeView residual adapters | C7.5/9 | 4/9 | Complete by Session 152: task/source/TreeView calls use action/helper ownership; old worker file is deleted |
| C7.6 | Correction | Cut over policy/reconnect/daemon lifecycle residual adapters | C7.6/9 | 3/9 | Complete by Session 152: policy/reconnect evidence uses action ownership; old worker file is deleted |
| C7.7 | Correction | Cut over Claude/Codex provider-specific residual adapters in F_0043/J_0033 | C7.7/9 | 2/9 | Complete by Session 152: provider-specific logic lives in session actions/assertions or owning F/J cases; old worker file is deleted |
| C7.8 | Correction | Replace stage/test imports and remove `NativeRemoteCase` public surface | C7.8/9 | 1/9 | Complete in Session 152 |
| C7.9 | Correction | Delete `CI/runner/remote_worker.py`, final scans, and oversized-file audit | C7.9/9 | 0/9 | Complete in Session 156: worker deleted, `stage_3_F.py` below 500, root `CI/full_primitives.py` split, forbidden paths absent, and all remaining >500-line CI files classified as reasonable |

## Batch/Round Plan

| Round | Batch | Scope | Main deliverable | Exit criteria | Status |
|---:|---|---|---|---|---|
| R1 | B1 Governance and source/menu closure | Establish this plan and finish the current TreeView context-menu promotion. | `treeview.remote_context_menu_commands`; plan doc; reports Session 92. | Focused tests pass; reports and metadata updated; future progress accounting is defined. | Complete in Session 92 |
| R2 | B2 Feishu question surface | Promote `wait_question_poll()` and related question/card poll evidence into Feishu actions; keep card/status assertions outside action. | Feishu question polling action(s), tests, report rows. | Config/helper cases still pass; no direct question polling remains in `remote_worker.py` except wrapper. | Complete in Session 93 |
| R3 | B2 Daemon/relay registry lookup surface | Promote `owner_identity_payload()`, `relay_chat_lookup()`, `daemon_group_list_entry()`, and `chat_lookup()` style lookup evidence. | Relay/daemon lookup actions with read-only resource locks. | Daemon/relay F cases still pass; report records reuse counts. | Complete in Session 94 |
| R4 | B2 Fixture setup and fixture assertions | Split `assert_no_team_or_non_codex_fixture()` plus no-group chat fixture write/remove/restart helpers. | Reusable assertion/action rows, or explicit not-promoted row if case-specific. | F_0044/F local dialogue tests retain behavior; no unreported fixture helper remains. | Complete in Session 95 |
| R5 | B2 Session/provider primitives | Promote common Codex/Claude session-id, resume-hint, provider-live, and restart-output checks where reusable. | Session actions/assertions; wrapper keeps any case-specific classification. | F/J restart coverage still passes focused tests; no weaker assertions; every session/provider residual is either promoted or explicitly recorded as deferred/case-specific. | Complete in Session 97 |
| R6 | B3 Source/package contract adapters | Finish source/package/menu adapter convergence around deployed extension/package reads. | Source-contract or TreeView/source actions; not-promoted rows only for real case adapters. | Source-contract focused tests pass; no duplicate package parsing in runner. | Complete in Session 98 |
| R7 | B3 Task/skill evidence helpers | Split task TreeView metadata helpers and skill source/farm helpers into actions/assertions/helpers as appropriate. | Task/skill reusable helpers, assertions, report rows. | F_0023-F_0032 and F_0045 focused tests pass. | Complete in Session 99 |
| R8 | B4 Case body relocation: workspace | Move `remote_workspace.py` scenario bodies into owning `F_XXXX.py` files. | Workspace F cases own their scripts; shard no longer owns workspace scenario logic. | Workspace focused tests/list-cases pass. | Complete in Session 100 |
| R9 | B4 Case body relocation: intern/session | Move `remote_intern_session.py` scenario bodies into owning `F_XXXX.py` files. | Intern/session F cases own their scripts. | Intern/session focused tests pass. | Complete in Session 101 |
| R10 | B4 Case body relocation: config/helper | Move `remote_config_helper.py` and local card-owner bodies into owning F files. | Config/helper F cases own scripts; question actions reused. | Config/helper focused tests pass. | Complete in Session 102 |
| R11 | B4 Case body relocation: daemon/relay | Move `remote_daemon_relay.py` scenario bodies into owning F files. | Daemon/relay F cases own scripts; lookup actions reused. | Daemon/relay focused tests pass. | Complete in Session 103 |
| R12 | B4 Case body relocation: TreeView/task/skill | Move `remote_treeview_task_skill.py` bodies into owning F files. | TreeView/task/skill F cases own scripts. | TreeView/task/skill focused tests pass. | Complete in Session 104 |
| R13 | B4 Case body relocation: Claude and J | Move `remote_claude.py` and `remote_journeys.py` bodies into `F_0043`-`F_0045` and J files. | Claude/J cases own scripts; no Claude scenario wrappers left. | Claude/J focused tests pass. | Complete in Session 106 |
| R14 | B5 Runner worker slimming | Reduce `remote_worker.py` to runner-owned context/dispatch/lifecycle adapters only. | Remaining product/domain logic moved out or recorded as not-promoted. | Active scan shows no action/assertion-looking unreported helpers. | Complete in Session 107 |
| R15 | B5 Shard deletion | Delete transitional `remote_*.py` shards after case bodies move. | No active imports of `CI.cases.F.remote_*` or `CI.cases.J.remote_journeys`. | `run_ci --list-cases/actions/assertions` and focused tests pass. | Complete in Session 108 |
| R16 | B6 Final audit and report close | Full migration audit against design and reports. | Final reuse/not-promoted reports; plan marked complete; metadata updated. | No forbidden files, no repo `tmp`, no unreported candidates, no stale shard imports. | Superseded by Session 110 correction audit |
| C1 | Correction: local F fixture runner | Move/delete `CI/cases/F/local_intern_session_cli.py`; case-specific script goes into `F_0052.py`, reusable pieces go to actions/assertions/helpers. | `local_intern_session_cli.py` absent; stage 3 local execution calls a case-owned or registry-owned runner. | Historical `F_0052` local dry-run/run tests pass; no non-case runner file under `cases/F`. Superseded by Session 158 remote F0052 conversion. | Complete in Session 112 |
| C2 | Correction: remote dispatch ownership | Move `REMOTE_CASE_RUNNERS` and remote case id dispatch out of `CI/runner/remote_worker.py` into the case registry/owning case layer. | `remote_worker.py` no longer imports all `F_XXXX.py` / `J_XXXX.py` modules for dispatch; stage remote execution resolves case runners through the case registry. | Remote dispatch focused tests and list-cases pass; no stale shard imports. | Complete in Session 113 |
| C3 | Correction: source-driver/mock Feishu helper relocation | Move `_RelayDriver*` fake API/registry/websocket helpers and source-driver card/message evidence from `remote_worker.py` to existing `helpers/mock_feishu_helper.py`, Feishu actions, or owning case files. | `remote_worker.py` no longer owns relay-driver fake classes or card parsing helpers. | Config/helper focused tests pass; no new design-outside helper. | Complete in Session 114 |
| C4 | Correction: workspace/intern/session wrapper cutover | Replace `NativeRemoteCase` compatibility methods for workspace, intern, session, tmux, green-light, and cleanup with direct action/assertion/helper usage from owning cases. | Workspace/intern/session F/J cases do not require `NativeRemoteCase` wrapper methods for reusable operations. | Workspace/session focused tests pass; active-case wrapper guard passes; reuse report updated for changed call counts. | Complete in Session 117 |
| C5 | Correction: task/TreeView/skill/source/policy wrapper cutover | Replace remaining task, TreeView, skill, source-contract, policy, and daemon/reconnect wrapper methods with direct action/assertion/helper usage from owning cases. | Remote case scripts use existing action/assertion roots or local case-specific helpers; no active case script depends on C5 wrapper methods. | TreeView/task/skill/policy/source focused tests pass; active-case wrapper guard passes. | Complete in Session 119 |
| C6 | Correction: J/dialogue-only primitive relocation | Move paid-agent dialogue helpers, task/PR waiters, merge guards, and journey-only tmux/codeup helpers out of `remote_worker.py` into J case files or reusable J actions/assertions. | F cases no longer carry J-only primitives; J case-specific logic is documented inline where not reusable. | J focused tests pass; active-case/worker guard rejects old dialogue tokens. | Complete in Session 120 |
| C7.1 | Correction: action registry data split | Move generated `ACTION_SPECS` / `ACTION_RESOURCE_LOCKS` data out of action implementation modules without changing registry behavior. | Action implementation files stop carrying large generated data blocks; `load_action_definitions()` and `action_resource_locks()` output is unchanged. | Action registry focused tests and list-actions pass; oversized action files shrink for data-only reasons. | Complete in Session 125 |
| C7.2 | Correction: workspace/source-control residuals | Replace `NativeRemoteCase` local repo, workspace record/list/mode/delete, branch/source-control, and GitHub repo adapters with action/assertion/helper calls or owning-case helpers. | Active F/J cases no longer need those `self.*` methods; worker loses that domain. | Workspace/source focused tests pass; active-case scan rejects the removed method tokens. | Complete in Session 128 |
| C7.3 | Correction: intern/session/tmux/status residuals | Replace remaining intern/session/tmux/status/runtime/session-registry adapters with session/intern actions or case-local helpers. | No active case relies on worker for generic session or tmux primitives. | Intern/session focused tests pass; restart semantics remain unchanged. | Complete by Session 152 |
| C7.4 | Correction: Feishu/group residuals | Replace relay-driver, question poll, chat lookup, green-light, group config, and light helper residuals with Feishu/relay actions or helpers. | Feishu behavior lives in `actions/feishu.py`, `actions/relay_daemon.py`, or `helpers/mock_feishu_helper.py`, not worker methods. | Config/helper/Feishu focused tests pass; no real relay restart. | Complete by Session 152 |
| C7.5 | Correction: task/source/TreeView residuals | Replace remaining task metadata, source-contract, TreeView projection/menu, and report-shaping adapters with action/assertion/case-local code. | Worker no longer owns task/source/TreeView product semantics. | Task/TreeView/source focused tests pass. | Complete by Session 152 |
| C7.6 | Correction: policy/reconnect/daemon lifecycle residuals | Replace policy env, daemon restart/start, relay machine state, and no-global-reset residual adapters with policy/relay actions and assertions. | Worker no longer owns daemon/policy product assertions. | Policy/reconnect focused tests pass; no package/deploy/relay restart. | Complete by Session 152 |
| C7.7 | Correction: provider-specific residuals | Replace Claude/Codex live, UUID, policy token, completed report, and provider-specific adapters in F_0043/J_0033 with actions/assertions or owning-case helpers. | Provider-specific logic is not hidden in a generic runner object. | Claude/Codex focused tests pass where they do not require deployment. | Complete by Session 152 |
| C7.8 | Correction: stage/test entrypoint cutover | Introduce the final thin remote entrypoint in the runner stage layer and update tests away from `CI.runner.remote_worker` / `NativeRemoteCase`. | Stage 3/4 imports no worker module; tests use the final entrypoint/context. | Remote dispatch and entrypoint tests pass. | Complete in Session 152 |
| C7.9 | Correction: worker deletion/final audit | Delete `CI/runner/remote_worker.py` after the active import graph is clean. | `test ! -e intern-cli/CI/runner/remote_worker.py`; no active legacy imports; no `RemoteCase` monolith replacement; no repo-root `tmp/`; `stage_3_F.py` below 500 lines; root `CI/full_primitives.py` absent. | Full focused CI migration audit passes; reports and metadata mark migration complete. | Complete in Session 156 |

## Batch Summary

- B1: governance and current source/menu closure, 1 round.
- B2: common action/assertion residuals in `remote_worker.py`, 4 rounds.
- B3: source/task/skill adapters, 2 rounds.
- B4: case body relocation into `F_XXXX.py` / `J_XXXX.py`, 6 rounds.
- B5: runner slimming and transitional shard deletion, 2 rounds.
- B6: final audit, 1 round.

Total: 6 batches, 16 rounds.

After Session 139, original R1-R16, correction C1-C6, and C7.1 through C7.2
remain complete. C7.3 is in progress after its status/runtime/session-registry,
fixture intern/sanity, session start/stop/status, Codex session-id/resume,
tmux/provider, metadata/status/session-map, policy/reconnect, and cleanup/delete
batches plus thin lifecycle wrapper deletion. C7.4 is also in progress after
the first Feishu/relay thin-wrapper batch. The migration is not closed and no
new C7 expansion round is counted complete. Seven bounded C7 expansion rounds remain. The next
execution line is:

```text
Plan vS92 correction: original rounds 16/16 complete; correction C1-C6 complete; C7 expansion 2/9 complete, remaining 7/9; active slice C7.4/9 Feishu/group residual adapters, in progress after Session 139 Feishu/relay thin-wrapper cutover.
```

## Reporting Rules

- Every promoted action/assertion updates
  `CI_ACTION_ASSERTION_REUSE_REPORT.md` with a current reuse count.
- Every action/assertion-looking helper that remains unpromoted updates
  `CI_NOT_PROMOTED_ACTION_ASSERTION_REPORT.md` with a permitted reason:
  `case-specific`, `pending promotion`, or `adapter until case move`.
- A round is not complete until focused validation passes and both task metadata
  and reports are updated.
- No package/deploy/VSIX/hooks/relay restart belongs to these migration rounds.
