# CI Not-Promoted Action/Assertion Report

<!-- Updated: Session 159 -->

This report tracks action/assertion-looking logic that still lives in owning
case files, plus the reason it has not yet been promoted. The default
expectation is promotion unless the logic is genuinely case-specific.
Transitional `remote_*.py` / `remote_journeys.py` shards were deleted in
Session 108, and `CI/runner/remote_worker.py` was deleted in Session 152.

| Candidate | Current count | Current location | Status | Reason not promoted yet / next action |
|---|---:|---|---|---|
| Active F/J local runners | 0 | N/A | Removed | Session 158 converted `F_0052_session_resume_cli_claude_contract` to a real remote F case and removed the last active local-F runner path. |
| Session/Claude exit/restart helpers | 3 cases | `F_0043.py`, `F_0052.py`, `J_0033.py` | Pending promotion | Duplicate tmux current-command probing, `/exit` shell waits, resume-hint execution, Claude UUID discovery, and restart/resume UUID assertions should move into `CI/actions/session.py` and `CI/assertions/session.py`. Existing Codex helpers already cover part of this surface; Claude needs matching assertion helpers. |
| Intern create/delete attempt helpers | 2 cases | `F_0008.py`, `F_0010.py` | Pending promotion | Existing intern actions cover happy-path create/delete and fixture cleanup, but failed create attempts, ordinary non-force delete attempts, runtime-metadata fallback delete checks, and force retry evidence still duplicate raw `internctl` command construction in cases. |
| Daemon reconnect recovery checks | 1 case | `F_0037.py` | Partially promoted | `ctx.action.policy.*` owns most policy/reconnect behavior, but the case still directly stops the daemon and inlines workspace/chat registry recovery and policy mtime checks. Add a paired daemon stop action and consider reusable reconnect-recovery assertions. |
| Source-driver mock Feishu helper use | 8 cases | `F_0016.py`, `F_0017.py`, `F_0018.py`, `F_0019.py`, `F_0020.py`, `F_0035.py`, `F_0036.py`, `F_0041.py` | Keep helper-owned for now | These cases use design-owned `self.mock_feishu.*` source-driver helpers rather than worker wrappers. Do not force-promote until a stable higher-level remote driver action contract is defined. |
| Small skill/config CLI probes | 2 cases | `F_0029.py`, `F_0031.py` | Low-priority promotion | These cases contain small direct command probes such as skill target git head and config format-check status/toggle. Promote only if the same probes recur in new F/J cases. |

## Maintenance Rule

Session 159 audit note: registry usage counts alone are not a reliable
not-promoted signal because historical/stage-contract/local-fixture action and
assertion ids remain in registry metadata. The active body-level scan looked for
direct command/assertion/helper implementations in `CI/cases/F/*.py` and
`CI/cases/J/*.py`. The actionable reuse gaps are the rows above, with
session/Claude restart-resume helpers first, intern create/delete attempt
helpers second, and daemon reconnect recovery helpers third.

Session 152 C7.8/C7.9 note: `CI/runner/remote_worker.py` and the
`NativeRemoteCase` public surface are no longer active not-promoted items. The
remote argv entrypoint now lives in the existing stage owner as
`CI.runner.stage_3_F.run_remote_case_argv()`, with the thin `StageRemoteCase`
bridge delegating to `RemoteCaseContext` plus `RemoteCaseLifecycleMixin`.
Package/deploy verification now requires `CI/runner/stage_3_F.py`, and guard
coverage requires the old worker file to stay absent.

Session 125 C7.1 note: inline generated action registry data is no longer a
not-promoted item. `ACTION_SPECS` / `ACTION_RESOURCE_LOCKS` moved from action
implementation modules to `CI/actions/registry_data.py`, and
`CI/actions/registry.py` now loads specs/locks from that registry data layer.

Session 128 C7.2 note: `create_local_repo()` and `repo_dir()` are no longer
worker adapters. Local git repo fixture creation now lives in
`ctx.action.workspace.local_repo_fixture_remote()`, with active F/J cases and
skill git-source fixture creation cut over. Workspace entry/local-enabled/display
helpers also moved to `ctx.action.workspace.entry_remote()` and
`workspace_assertions.*`. Session 128 also moved nonprotected/GitHub repo
resolution, workspace create argv construction, failed create attempts,
workspace record/sync/absent/no-extra-record checks, metadata root and
metadata-branch checks, and business branch revision checks into
`ctx.action.workspace.*` plus `workspace_assertions.*`. The corresponding
`NativeRemoteCase` methods are deleted and guarded by
`test_c7_2_local_repo_fixture_is_not_remote_worker_adapter`.

Session 129 C7.3 note: `status_json()`, `runtime_dir()`, and
`session_registry()` are no longer `NativeRemoteCase` adapters. Active F/J cases
and worker internals now use `ctx.action.intern.status_json_remote()`,
`ctx.action.intern.runtime_dir_remote()`, and
`ctx.action.session.registry_remote()`. The corresponding wrapper definitions
are deleted and guarded by
`test_c7_3_status_runtime_registry_are_not_remote_worker_adapters`. C7.3 remains
in progress because fixture intern creation/sanity, session start/stop/status,
Codex session-id/resume/provider helpers, and tmux capture/send/wait wrappers
still need promotion or case-local relocation.

Session 130 C7.3 note: `create_fixture_intern()`,
`remember_fixture_intern_cleanup()`, and
`assert_no_team_or_non_codex_fixture()` are no longer `NativeRemoteCase`
adapters. Active F/J cases use
`ctx.action.intern.create_fixture_case_remote()` and
`ctx.action.intern.no_team_or_non_codex_fixture_remote()`, then apply returned
checks with `self.require_checks(...)`. C7.3 remains in progress because
session start/stop/status wrappers, Codex session-id/resume/provider helpers,
and tmux capture/send/wait wrappers still need promotion or case-local
relocation.

Session 131 C7.3 note: `session_start_for_workspace()`,
`session_stop_for_workspace()`, `session_status_for_workspace()`,
`is_session_online()`, and `is_codex_online()` are no longer
`NativeRemoteCase` adapters. Active F/J cases and remaining worker internals use
existing session actions directly. C7.3 remains in progress because Codex
session-id/resume/provider helpers and tmux capture/send/wait wrappers still
need promotion or case-local relocation.

Session 132 C7.3 note: Codex session-id/resume helpers are no longer
`NativeRemoteCase` adapters. Active F/J cases use existing session actions for
UUID evidence, resume-hint polling, and manual-resume live probing, and apply
existing `session_assertions.*` checks through
`RemoteCaseLifecycleMixin.require_classified_checks(...)`. C7.3 remains in
progress because tmux capture/send/wait wrappers and any remaining
provider-specific restart/report compatibility still need promotion or
case-local relocation.

Session 133 C7.3 note: tmux capture/send/wait wrappers,
`session_restart_for_workspace()`, provider live/process/env wrappers, and
`claude_policy_token_evidence()` are no longer `NativeRemoteCase` adapters.
Active callers use existing session action roots directly, and F_0043 uses
`ctx.action.session.prepare_claude_policy_token_remote()` for redacted Claude
policy evidence. C7.3 remains in progress because metadata/status/session-map
adapters and policy/reconnect helpers still need promotion or case-local
relocation before the worker can be deleted.

Session 134 C7.3 note: `intern_list_item()`,
`assert_metadata_status_consistent()`, `assert_tree_projection_contains()`,
`session_registry_entries_for()`, `write_session_map_entry()`,
`session_resource_lookup()`, `active_intern_from_session_resource()`, and task
metadata writer wrappers are no longer `NativeRemoteCase` adapters. Active
callers use intern/session/task action roots directly. C7.3 remains in progress
because policy/reconnect helpers and remaining cleanup/delete lifecycle adapters
still need promotion or case-local relocation before the worker can be deleted.

Session 136 C7.3 note: policy/reconnect helpers are no longer
`NativeRemoteCase` adapters. F_0034 and F_0037 use `ctx.action.policy.*`;
`policy.machine_config_marker` and `policy.daemon_sync_existing_deployment` are
real ctx actions again, with additional registered policy action ids for
fingerprints, env reports, restart waits, relay machine state, daemon lifecycle,
and no-global-reset checks. C7.3 remains in progress because cleanup/delete
lifecycle adapters still need promotion or case-local relocation before the
worker can be deleted.

Session 137 C7.3 note: cleanup/delete lifecycle wrappers for fixture intern
cleanup, no-artifacts checks, removed-intern checks, intern list JSON, and
session registry entry deletion are no longer `NativeRemoteCase` adapters.
F_0008, F_0010, F_0025, and F_0026 use intern/session action roots directly,
and guard tests reject the removed wrapper definitions/case calls. C7.3 remains
in progress because other residual worker adapters still need promotion or
case-local relocation before the worker can be deleted.

Session 138 C7.3 note: thin lifecycle wrappers for case reset, session registry
key formatting, fixture workspace cleanup, metadata resolver, intern checkout
repo lookup, delete intern/workspace, and unscoped session start/status/stop are
no longer `NativeRemoteCase` adapters. Runner-local reset and best-effort
cleanup now call workspace/session action roots directly; F_0026 uses explicit
workspace-scoped session keys in its owning assertion. C7.3 remains in progress
because other residual worker adapters still need promotion or case-local
relocation before the worker can be deleted.

Session 139 C7.4 note: Feishu/relay thin wrappers for chat lookup, question
polling, owner identity, relay registry entry/wait, current-scene green light,
relay registry absent evidence, group config/mode mutation, relay/daemon chat
lookup, no-group registry fixtures, and fixture daemon restart are no longer
`NativeRemoteCase` adapters. Active F/J cases call the existing Feishu and
relay_daemon action roots directly. C7.4 remains in progress because
relay-driver/mock Feishu helpers and other Feishu/group residuals still need
promotion or case-local relocation before the worker can be deleted.

Session 140 residual note: source-contract and TreeView thin wrappers for
extension source candidates, product source evidence, deployed source
contracts, item projection, workspace projection, and context-menu command
lookup are no longer `NativeRemoteCase` adapters. Focused tests now call
`ctx.action.source_contract.*` and `ctx.action.treeview.*` roots directly, with
guard coverage preventing the removed worker definitions from returning.
`remote_worker.py` remains a pending correction item until the final entrypoint
and remaining residual adapters are deleted.

Session 141 residual note: task/skill/metadata-root wrappers for task TreeView
README/status seeding, skill CLI/config/farm/source evidence, workspace metadata
root lookup, task grouping/tooltip formatting, and skill JSON report shaping are
no longer `NativeRemoteCase` adapters. Reusable metadata-root resolution is now
`ctx.action.workspace.metadata_root_remote()`. Case-specific task grouping,
tooltip, F_0022 TreeView fixture composition, and F_0045 JSON report shaping now
live in their owning case files. Guard coverage prevents the removed worker
definitions and active-case calls from returning.

Session 142 residual note: mock Feishu source-driver wrappers for relay driver
context setup, message ingress, card callback ingress, handler source evidence,
machine-config schema/root setup, config snapshots, and machine-config state are
no longer `NativeRemoteCase` adapters. These now live under the existing
`CI/helpers/mock_feishu_helper.py` ownership as `self.mock_feishu.*` methods,
and active source-driver F cases call that helper directly. Guard coverage
prevents the removed worker definitions and active-case calls from returning.

Session 143 residual note: mock TreeView/GUI wrappers for CLI equivalence,
command events, context-menu clicks, quickpick selections, input events, and
no-group allocation checks are no longer `NativeRemoteCase` adapters. Active
F_0001/F_0002/F_0004 case checks compose `self.mock_treeview.cli_equivalence()`
with `surface_assertions.treeview_cli_equivalent_detail()`, and focused tests
call `MockTreeViewHelper` directly. Guard coverage prevents the removed worker
definitions and active-case calls from returning.

Session 144 residual note: dead workspace/stage reset adapters are no longer
`NativeRemoteCase` methods. The old `reset_stage_workspace_namespace()` body and
its private workspace record polling helpers were removed; stage and case reset
behavior stays owned by `ctx.action.workspace.reset_stage_namespace_remote()`
and `ctx.action.workspace.case_initial_reset_remote(...)`. Guard coverage
prevents the removed worker definitions from returning.

Session 145 residual note: pure context/transport forwarding is no longer
duplicated as explicit `NativeRemoteCase` methods. `NativeRemoteCase` delegates
missing attributes to its `RemoteCaseContext`, so `run_cmd`, `json_cmd`,
HTTP/relay request helpers, identity/stage naming helpers, and context fields
resolve from the existing context owner. Remaining `NativeRemoteCase` methods
are still a pending correction item until the worker import path is deleted.

Session 146 residual note: case initial reset evidence is no longer a
`NativeRemoteCase` method. `workspace.remote_case_initial_reset_evidence` now
reuses the existing reset artifact or runs the case reset action, then returns a
`case_initial_reset_ok` check for native report writing. Active F/J cases call
the workspace action root directly through `self.require_checks(...)`.

Session 147 residual note: case-scoped task/file naming helpers are no longer
`NativeRemoteCase` methods. `RemoteCaseContext.task_id()` and
`RemoteCaseContext.file_name()` own this naming; active calls continue through
the context proxy until the worker import path is removed.

Session 148 residual note: HTTP status assertion logic is no longer a
`NativeRemoteCase` method. `CI.assertions.core.require_http_status(...)` owns
the reusable status/body/error-fragment assertion and returns the prior
`status_code`/`body` detail shape for case evidence. F_0012, F_0013, and J_0014
call that assertion helper directly; guard coverage prevents
`self.require_http_status(...)` and `def require_http_status(...)` from
returning to active cases or the worker.

Session 149 residual note: daemon log marker polling is no longer a
`NativeRemoteCase` method. `RelayDaemonActions.wait_daemon_log_contains_remote()`
owns the reusable remote daemon-log evidence check, and the registered action id
is `relay_daemon.remote_wait_daemon_log_contains`. F_0015 calls that action root
directly; guard coverage prevents `self.wait_daemon_log_contains(...)` and
`def wait_daemon_log_contains(...)` from returning to active cases or the
worker.

Session 150 residual note: workspace/intern creation convenience is no longer a
`NativeRemoteCase` method surface. Tests and cases must use
`ctx.action.workspace.create_case_remote()`, `ctx.action.workspace.list_remote()`,
`ctx.action.workspace.doctor_remote()`, and `ctx.action.intern.create_case_remote()`
or lower-level intern action roots directly. Guard coverage prevents
`def create_workspace(...)`, `def workspace_list(...)`,
`def workspace_doctor(...)`, and `def create_intern(...)` from returning to the
worker.

Session 151 residual note: contract scenario recording is no longer a
`NativeRemoteCase` method. `RemoteCaseLifecycleMixin._record_contract_scenario()`
owns scenario row emission and failure classification artifact updates, while
the pure classification table lives in `CI.helpers.reporting.classify_remote_case_failure()`.
Unused worker-only helpers `best_effort_cleanup()`, `_repo_mode_expected()`, and
`_transport_health()` were deleted. Guard coverage prevents these definitions
from returning to the worker.

Session 100 relocation note: no workspace action/assertion-looking helper is
left in `CI/cases/F/remote_workspace.py`. R8 moved the eight workspace scenario
bodies into their owning `F_XXXX.py` files, so there is no workspace shard item
to carry in this not-promoted report.

Session 101 relocation note: no intern/session action/assertion-looking helper
is left in `CI/cases/F/remote_intern_session.py`. R9 moved the five
intern/session scenario bodies into their owning `F_XXXX.py` files, so there is
no intern/session shard item to carry in this not-promoted report.

Session 102 relocation note: no config/helper action/assertion-looking helper
is left in `CI/cases/F/remote_config_helper.py`. R10 moved the nine
config/helper scenario bodies into their owning `F_XXXX.py` files, so there is
no config/helper shard item to carry in this not-promoted report.

Session 103 relocation note: no daemon/relay action/assertion-looking helper is
left in `CI/cases/F/remote_daemon_relay.py`. R11 moved the five daemon/relay
scenario bodies into their owning `F_XXXX.py` files, so there is no
daemon/relay shard item to carry in this not-promoted report.

Session 104 relocation note: no TreeView/task/skill action/assertion-looking
helper is left in `CI/cases/F/remote_treeview_task_skill.py`. R12 moved the ten
TreeView/task/skill scenario bodies into their owning `F_XXXX.py` files, so
there is no TreeView/task/skill shard item to carry in this not-promoted
report. Session 106 updates the remaining git skill source composer row after
the final Claude shard caller moved.

Session 106 relocation note: no Claude or J action/assertion-looking helper is
left in `CI/cases/F/remote_claude.py` or `CI/cases/J/remote_journeys.py`. R13
moved all Claude/J scenario bodies into owning `F_0043.py`, `F_0044.py`,
`F_0045.py`, `J_0014.py`, and `J_0033.py`, so there is no Claude/J shard item
to carry in this not-promoted report. The remaining git skill source composer
row now points only at `CI/runner/remote_worker.py` compatibility helpers and
owning F files.

Session 107 runner-slimming note: no source-contract adapter or skill git
composer remains in `CI/runner/remote_worker.py`. The old `_fXXXX` deployed
contract wrappers and `_create_git_skill_source()` /
`_update_git_skill_source()` names are absent from active runner/case/test
scans. Any remaining action/assertion-looking residual will be audited after
R15 deletes the transitional shards.

Session 108 shard deletion note, updated by Session 113 C2: `CI/cases/F/remote_*.py`
and `CI/cases/J/remote_journeys.py` are deleted. Remote case-id dispatch now
lives in `CI/cases/registry.py` as lazy `REMOTE_CASE_RUNNERS` paths pointing at
owning case module functions; this dispatch map is case registry infrastructure,
not an unpromoted product action/assertion.

Session 109 final audit note, updated by Session 113 C2: active scans show 43
registered cases, 42 remote cases, and 42 case-registry runner entries with no
missing or extra dispatch entries.
Deleted transitional shards have no active imports, no repo-root `tmp` exists,
and this report remains clear with zero not-promoted candidates.

Session 110 correction note: supervisor review found the Session 109 clear
state was too narrow. It only covered deleted `remote_*.py` shards and stale
imports. `CI/cases/F/local_intern_session_cli.py` and
`CI/runner/remote_worker.py` are still active implementation files requiring
correction, so this report is no longer clear.

Session 112 C1 note: `CI/cases/F/local_intern_session_cli.py` was deleted and
the intermediate local runner moved into owning case module
`CI.cases.F.F_0052`.

Session 158 note: the intermediate local runner is also gone. `F_0052` now runs
as a real remote F case through `CI.cases.registry.REMOTE_CASE_RUNNERS`, and
`stage_3_F` no longer exposes `run_f_local_cases()`.

Session 113 C2 note: `REMOTE_CASE_RUNNERS` moved from
`CI/runner/remote_worker.py` to `CI/cases/registry.py` as lazy
`case_id -> module:function` paths. `NativeRemoteCase.run()` now resolves
case-owned runner functions through `case_registry.resolve_remote_case_runner()`.
`remote_worker.py` no longer imports all F/J case modules for dispatch.

Session 114 C3 note: source-driver mock Feishu fake classes and evidence logic
now live in `CI/helpers/mock_feishu_helper.py`: `RelayDriverObj`,
`RelayDriverMemoryChatConfig`, `RelayDriverFakeAPI`,
`RelayDriverFakeRegistry`, `RelayDriverFakeRelayWS`, source driver module
loading, machine-config policy fixture setup, message/card ingress evidence,
config snapshots, machine-config state reads, and card value parsing. The
runner worker no longer exposes `_RelayDriver*` fake classes or card parser
wrapper methods; source-driver cases use `self.mock_feishu.*` for pure card
helpers.

Session 117 C4 note: active `F_XXXX.py` and `J_XXXX.py` case scripts no longer
call `NativeRemoteCase` workspace/intern/session/tmux/green-light/restart or
provider-live wrappers. `test_ci_remote_case_dispatch.py` scans those active
case files and rejects the C4 wrapper tokens. `remote_worker.py` still exists
until C7, but C4 narrowed the remaining not-promoted surface to C5/C6 wrapper
families and the final worker entrypoint.

Session 119 C5 note: active `F_XXXX.py` and `J_XXXX.py` case scripts no longer
call `NativeRemoteCase` task fixture, Task TreeView seed, skill CLI/source/farm,
source-contract, TreeView projection, group config, relay lookup, daemon lookup,
or registry fixture wrappers. `test_ci_remote_case_dispatch.py` now scans those
active case files and rejects the C5 wrapper tokens. `remote_worker.py` still
exists until C7, but the remaining not-promoted surface is now C6 J/dialogue-only
primitives plus the final worker entrypoint.

Session 120 C6 note: active J_0033 no longer calls `NativeRemoteCase`
`wait_tmux_token_count()`; token waiting is case-local journey logic. The old
local dialogue task/merge helper chain and BUG_0010 Codex RUI dialogue runner
were removed from `CI/runner/remote_worker.py`. The remaining not-promoted
surface is only the final C7 worker entrypoint/compatibility class deletion.

Session 122 C7 replan note: supervisor rejected a C7 approach that would delete
`remote_worker.py` by moving the class body into a new `RemoteCase` monolith.
C7 is now split into C7.1-C7.9. C7.1 starts with action registry data because
it is mechanically separable from behavior and reduces oversized action files
without changing action execution semantics.

- Every action/assertion-looking helper left in `remote_worker.py` or owning
  case files must have a row here.
- Accepted reasons are limited to:
  - `case-specific`: cannot be reused outside the named case.
  - `pending promotion`: reusable, but waiting on an ordered migration.
  - `adapter until case move`: reusable logic already promoted, but a wrapper is
    temporarily needed for current native report compatibility.
- Generic convenience or historical placement is not an accepted reason.
