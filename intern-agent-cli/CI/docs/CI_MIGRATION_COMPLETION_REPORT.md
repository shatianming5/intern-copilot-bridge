# CI Migration Completion Report

## Summary

The initial CI target layout described by `CI_ARCHITECTURE_DESIGN.md` was
completed on `intern_ci_lead/task416_ci_f_post_merge_followup` in Session 63.
The follow-up action/assertion migration and transitional remote shard deletion
reached the original R16 close in Session 109, but Session 110 supervisor audit
found two active implementation files still outside the final design boundary.
Session 112 completed the local runner correction. Session 113 moved remote
case-id dispatch into the case registry. Session 114 moved mock Feishu
source-driver helpers into `CI/helpers/mock_feishu_helper.py`. Session 117 cut
active workspace/intern/session/tmux/green-light/restart case scripts over to
direct action/assertion/helper usage. Session 119 cut active task/TreeView/
skill/source/policy/group/relay case scripts over to direct action/assertion/
helper usage. Session 120 removed the remaining dead J/dialogue-only helper
chain from `remote_worker.py` and moved the active J_0033 token wait into the
owning case. Session 152 moved the final remote argv entrypoint into the
existing stage owner (`CI.runner.stage_3_F.run_remote_case_argv()`), updated
package/deploy and VSIX verification to require `CI/runner/stage_3_F.py`,
updated tests away from `CI.runner.remote_worker` / `NativeRemoteCase`, and
deleted `CI/runner/remote_worker.py`. The final oversized-file reasonability
audit remains tracked in `CI_ACTION_ASSERTION_MIGRATION_PLAN.md`. Session 154
trimmed `CI/runner/stage_3_F.py` below 500 lines by deleting obsolete legacy
kind-specific runner branches and moving duplicated native report summary logic
to `CI.helpers.reporting`. Session 155 deleted root-level
`CI/full_primitives.py` and split deployment primitives into design-owned
`CI/helpers/deployment_*` modules plus shared remote-machine helpers, all below
500 lines. Session 156 completed the final oversized-file audit: every
remaining >500-line CI Python file is now classified as generated registry
data, a domain action/helper owner, an owning F/J case script, or a test-only
guard matrix. Session 158 converted `F_0052_session_resume_cli_claude_contract`
from a local mocked fixture into a real remote F case and removed the last active
local-F runner path; active F/J cases are now required to use `stage="remote"`.

The active CI layout now follows the target structure:

- public entrypoint: `intern-cli/CI/run_ci.py`
- helpers: `intern-cli/CI/helpers/`
- actions: `intern-cli/CI/actions/`
- assertions: `intern-cli/CI/assertions/`
- cases and case selection/resources: `intern-cli/CI/cases/`
- F scripts: `intern-cli/CI/cases/F/`
- J scripts: `intern-cli/CI/cases/J/`
- F/J docs: `intern-cli/CI/docs/F/` and `intern-cli/CI/docs/J/`
- runner stages, planner, scheduler, and reporting: `intern-cli/CI/runner/`
- CI system tests: `intern-cli/CI/tests/`

## Legacy Removal

The active runtime no longer uses or ships the removed migration paths:

- `intern-cli/CI/native_remote.py`
- `intern-cli/CI/remote_cases/`
- `intern-cli/CI/runner.py`
- `intern-cli/CI/runner/remote_worker.py`
- `intern-cli/CI/stage_preflight.py`
- `intern-cli/CI/planner.py`
- `intern-cli/CI/report.py`
- `intern-cli/CI/selection.py`
- `intern-cli/CI/assertion/`
- legacy `case_slots/`
- root-level local runner fixtures such as `local_askuser.py`,
  `local_intern_session_cli.py`, `workspace_local.py`, and related removed
  local modules

No active F/J case uses a local runner. The design-incompatible
`intern-cli/CI/cases/F/local_intern_session_cli.py` file is deleted, and
`F_0052_session_resume_cli_claude_contract` now runs through the normal remote
case registry on debug deployment.

## Final Session 63 Verification

Commands and results:

- `git fetch origin master`: `origin/master@cb26a9da7` is already an ancestor
  of the branch; no rebase was required.
- `python3 -m py_compile $(find intern-cli/CI -path '*/__pycache__' -prune -o -name '*.py' -print)` passed.
- `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests --tb=short`
  passed: `206 passed, 1 warning`.
- Registry/list gates passed:
  - cases: `43`, with `F=41`, `J=2`, `remote=42`, `local=1`
  - actions: `227`
  - assertions: `112`
  - case sets: `native=43`, `core=41`, `full=41`, `F=41`, `J=2`
  - registry audit: `ok=true`, `stage_preflight_errors=0`
- Historical `F_0052_session_resume_cli_claude_contract` local run passed:
  `/tmp/intern_agent_CI/session63_b7r4_f0052_local_final3.json`,
  `passed=1 failed=0 skipped=0`. Session 158 superseded this local fixture with
  a real remote F case.
- Mixed F/J planner dry-run contract passed:
  `/tmp/intern_agent_CI/session63_b7r4_fj_existing_dry_final3_contract.json`.
  The planner selected `F_0007_intern_create_status_contract` and
  `J_0033_codex_exit_resume_same_session_journey`, produced one conflict edge
  and two schedule waves, and emitted all graph artifacts:
  `plan_json`, `conflict_graph_json`, `conflict_graph_dot`,
  `conflict_graph_mermaid`, and `schedule_waves_json`.
- Active runtime legacy scan passed for CI runtime Python, excluding docs/tests:
  no active imports of `CI.native_remote`, `CI.remote_cases`, `CI.selection`,
  `CI.stage_preflight`, `CI.assertion`, no `LOCAL_PLATFORM_KINDS`, no
  `_run_local_platform`, no `CI.report`, and no root local runner imports.
- Root legacy local file absence check passed. This Session 63 note originally
  allowed `intern-cli/CI/cases/F/local_intern_session_cli.py`; Session 112
  superseded that allowance by moving the local runner into `F_0052.py`;
  Session 158 then removed the local-F runner path entirely.
- `git diff --check` passed.
- `test ! -e tmp` passed.

## Final Metadata Corrections

Session 63 also fixed the last migration-contract gaps found during final
verification:

- `J_0014_peer_send_routing_error_contract` now declares explicit
  `resource_locks` and journey-level action metadata so the stage preflight and
  conflict graph can classify it as a J journey.
- `J_0033_codex_exit_resume_same_session_journey` now declares explicit
  `resource_locks` so mixed F/J dry-runs can build a strict conflict graph.
- Root `CI/report.py` moved to `CI/runner/reporting.py`, matching the target
  runner package layout.

No product code, packaging, deployment, VSIX install, hook install, relay
restart, or master push was performed in this completion round.

## Session 156 C7.9 Final Audit

C7.9 is complete on the task branch.

Evidence:

- Forbidden legacy paths are absent: `intern-cli/CI/full_primitives.py`,
  `intern-cli/CI/runner/remote_worker.py`, `intern-cli/CI/native_remote.py`,
  `intern-cli/CI/remote_case_runner.py`, and
  `intern-cli/CI/helpers/remote_case_lifecycle.py`.
- Repo-root `tmp/` is absent.
- Current >500-line CI Python files are all classified:
  `actions/registry_data.py` is generated/static action registry data;
  `actions/session.py`, `actions/workspace.py`, `actions/intern.py`, and
  `actions/feishu_mock.py` are domain action roots;
  `helpers/mock_feishu_helper.py` is the mock Feishu domain helper;
  `cases/F/F_0043.py` and `cases/J/J_0033.py` are owning complex F/J scripts;
  the remaining >500 files are test-only guard/matrix files.
- Focused validation passed: py_compile, focused pytest, registry/list gates,
  `git diff --check`, and absence checks for root `tmp/` plus deleted legacy
  paths.

## Session 109 Action/Assertion Migration Close, Superseded

The follow-up migration tracked by
`CI_ACTION_ASSERTION_MIGRATION_PLAN.md` reached the original R16 close:

- Plan vS92: 6 batches, 16 rounds, 16/16 complete, 0 remaining.
- Active registry audit: 43 cases total, 42 remote cases, 42 runner dispatch
  entries.
- Runner dispatch audit: missing entries `[]`, extra entries `[]`.
- Transitional shard files are absent:
  `intern-cli/CI/cases/F/remote_*.py` and
  `intern-cli/CI/cases/J/remote_journeys.py`.
- Forbidden transitional entrypoints remain absent:
  `intern-cli/CI/remote_case_runner.py` and
  `intern-cli/CI/helpers/remote_case_lifecycle.py`.
- Active scans show no imports of deleted `CI.cases.F.remote_*` or
  `CI.cases.J.remote_journeys` modules.
- `CI_ACTION_ASSERTION_REUSE_REPORT.md` and
  `CI_NOT_PROMOTED_ACTION_ASSERTION_REPORT.md` were updated to Session 109;
  not-promoted candidates are clear with count 0.
- Repo-root `tmp/` is absent.

Session 109 did not change product code, package/deploy, install VSIX/hooks,
restart relay, push master, or merge the MR.

## Session 111 Correction Plan

Session 110 reopened the execution tracker without changing the design.
Session 111 fixes the remaining correction plan at 7 rounds, under the
supervisor limit of 10:

- C1: delete/migrate `local_intern_session_cli.py`.
- C2: move remote dispatch ownership out of `remote_worker.py`.
- C3: move source-driver/mock Feishu relay-driver helpers.
- C4: cut over workspace/intern/session wrappers.
- C5: cut over task/TreeView/skill/source/policy wrappers.
- C6: move J/dialogue-only primitives to J cases/actions/assertions.
- C7: delete `CI/runner/remote_worker.py` and run final audit.

Until these correction rounds are complete, this report should be treated
as a partial completion report, not a final migration closeout.

## Session 112 C1 Correction

C1 is complete:

- Deleted `intern-cli/CI/cases/F/local_intern_session_cli.py`.
- Moved the active local F fixture runner into
  `intern-cli/CI/cases/F/F_0052.py`, which now owns both the case definition
  and the case-specific local Claude resume/restart fixture.
- Updated `stage_3_F.py` to import local runner functions from `F_0052.py`.
- Validated `F_0052` dry-run and actual local run:
  - `/tmp/intern_agent_CI/task416_session112_f0052_dry.json`: scenario summary
    total=3, passed=0, failed=0, skipped=3.
  - `/tmp/intern_agent_CI/task416_session112_f0052_local.json`: scenario
    summary total=3, passed=3, failed=0, skipped=0.

Session 158 superseded this intermediate local-fixture shape: `F_0052` is now a
remote F case with 11 scenarios, selected through `REMOTE_CASE_RUNNERS`, and the
runner no longer has `run_f_local_cases()`.

## Session 113 C2 Correction

C2 is complete:

- Moved `REMOTE_CASE_RUNNERS` from `CI/runner/remote_worker.py` to
  `CI/cases/registry.py` as lazy `case_id -> module:function` paths.
- Added `resolve_remote_case_runner()` and
  `validate_remote_case_runner_registry()` to the case registry.
- `NativeRemoteCase.run()` now resolves owning case functions through
  `CI.cases.registry`; `remote_worker.py` no longer imports all `F_XXXX.py` /
  `J_XXXX.py` modules for dispatch.
- Updated focused dispatch tests to validate the registry mapping/resolver and
  worker dispatch through the registry.

Remaining correction state after C2: 2/7 complete, 5/7 remaining. Current
round is C3: move source-driver/mock Feishu relay-driver helpers out of
`remote_worker.py`.

## Session 114 C3 Correction

C3 is complete:

- Moved source-driver mock Feishu fake classes from `CI/runner/remote_worker.py`
  to `CI/helpers/mock_feishu_helper.py`: `RelayDriverObj`,
  `RelayDriverMemoryChatConfig`, `RelayDriverFakeAPI`,
  `RelayDriverFakeRegistry`, and `RelayDriverFakeRelayWS`.
- Moved source-driver module loading, machine-config policy fixture setup,
  fake context construction, message/card ingress event construction, config
  snapshots, machine-config state reads, and card value parsing into
  `MockFeishuHelper`.
- Updated source-driver F cases to call `self.mock_feishu.*` directly for pure
  card parser helpers.
- Added a guard test that rejects `_RelayDriver*` fake classes and card parser
  wrappers on `CI.runner.remote_worker`.

Remaining correction state after C3: 3/7 complete, 4/7 remaining. Current
round is C4: cut over workspace/intern/session wrappers from `remote_worker.py`.

## Session 117 C4 Correction

C4 is complete:

- Active F/J cases no longer call `NativeRemoteCase` workspace/intern/session
  compatibility methods, including the high-level restart/resume/provider-live,
  green-light, tmux provider process, tmux environment, and resume-hint parser
  wrappers.
- Added direct action usage for case-scoped workspace/intern/session helpers and
  `session.remote_tmux_environment_values`.
- Added a focused guard test that scans active `CI/cases/F/F_*.py` and
  `CI/cases/J/J_*.py` for C4 wrapper call tokens.
- Validated registry/list gates and focused tests in Session 117.

Remaining correction state after C4: 4/7 complete, 3/7 remaining. Current
round is C5: cut over task/TreeView/skill/source/policy wrappers from
`remote_worker.py`.

## Session 119 C5 Correction

C5 is complete:

- Active F/J cases no longer call `NativeRemoteCase` task fixture, Task
  TreeView seed, skill CLI/source/farm, source-contract, TreeView projection,
  Feishu group config/registry, relay lookup, daemon lookup, or registry fixture
  wrappers.
- F cases now call existing `ctx.action.task.*`, `ctx.action.skill.*`,
  `ctx.action.feishu.*`, `ctx.action.relay_daemon.*`,
  `source_contract_assertions.require_deployed_contract()`, and
  `treeview_assertions.checked_*()` surfaces directly.
- Added `test_active_cases_do_not_call_c5_compatibility_wrappers()` so active
  `CI/cases/F/F_*.py` and `CI/cases/J/J_*.py` cannot regress to those wrapper
  tokens.
- Validated registry/list gates and focused tests in Session 119:
  registry ok; py_compile ok; focused pytest 122 passed, 1 warning; list gates
  written under `/tmp/intern_agent_CI/task416_session119_list_*.json`; C5
  wrapper scan empty; `git diff --check`; repo-root `tmp/` absent.

Remaining correction state after C5: 5/7 complete, 2/7 remaining. Current
round is C6: move J/dialogue-only primitives out of `remote_worker.py`.

## Session 120 C6 Correction

C6 is complete:

- Removed unregistered dialogue-only helper functions and methods from
  `CI/runner/remote_worker.py`: prompt builders, task/PR/merge waiters,
  tmux turn/token wait helpers, `run_local_dialogue_merge()`, and the old
  BUG_0010 Codex RUI card-owner dialogue runner.
- Moved the only active J_0033 token wait off the `NativeRemoteCase`
  compatibility method into the owning `J_0033.py` case body.
- Added a focused guard test that rejects the old C6 dialogue tokens from active
  F/J cases and from `CI/runner/remote_worker.py`.
- Validated registry/list gates and focused tests in Session 120:
  registry ok; py_compile ok; focused pytest 78 passed, 1 warning; list gates
  written under `/tmp/intern_agent_CI/task416_session120_list_*.json`; C6
  dialogue scan empty; `git diff --check`; repo-root `tmp/` absent.

Session 122 expanded the old C7 into C7.1-C7.9 after supervisor review rejected
moving the `remote_worker.py` class body into another monolith. Session 125
completed C7.1 by moving generated action registry specs/resource locks into
`CI/actions/registry_data.py`.

Session 128 completed C7.2: local repo fixture creation, workspace
entry/local-enabled/display helpers, nonprotected/GitHub repo resolution,
workspace create argv construction, failed create attempts, workspace
record/sync/absent/no-extra-record checks, metadata root/metadata-branch checks,
and business branch revision checks are now owned by `ctx.action.workspace.*`
and `workspace_assertions.*` instead of `NativeRemoteCase` wrappers.

Current correction state after C7.2: original R1-R16 complete, correction C1-C6
complete, C7 expansion 2/9 complete, 7/9 remaining. Current round is C7.3:
cut over intern/session/tmux/status residual adapters.
