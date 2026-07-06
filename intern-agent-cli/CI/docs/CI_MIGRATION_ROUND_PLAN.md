# CI Migration Round Plan

> Historical tracker only. Do not use this file as the active migration plan.
> The active task416 migration tracker is
> `CI_ACTION_ASSERTION_MIGRATION_PLAN.md`, which is complete as of Session 109:
> Plan vS92, 6 batches, 16 rounds, 16/16 complete, 0 remaining.

This is an execution tracker for `CI_ARCHITECTURE_DESIGN.md`.
It does not change the design. The design document remains the authority for the final directory layout and module boundaries.

## Progress Reporting Rule

Every implementation session starts with this line:

```text
CI migration progress: batch <B>/7, round <R>/<TOTAL_REMAINING>, current round <round-id> <title>; remaining <N> rounds; expected completion in <N> focused sessions if no merge conflicts.
```

The end of each session must record:

- round id completed or still active;
- exact files moved/removed;
- verification commands and results;
- remaining round count after the commit.

## Fixed Target

Final active layout is the one in `CI_ARCHITECTURE_DESIGN.md`:

```text
intern-cli/CI/
  run_ci.py
  helpers/
  actions/
  assertions/
  cases/
    F/
    J/
    selector.py
    resources.py
  docs/
    F/
    J/
    CI_ARCHITECTURE_DESIGN.md
  runner/
    __init__.py
    stage_0_preflight.py
    stage_1_unit_test.py
    stage_2_package_deploy.py
    stage_3_F.py
    stage_4_J.py
    planner.py
    scheduler.py
    reporting.py
    runner.py
  tests/
```

No design adjustment is allowed during this migration.

Target-incompatible active paths must be removed before closeout:

- `intern-cli/CI/native_remote.py`
- `intern-cli/CI/remote_cases/`
- `intern-cli/CI/runner.py`
- `intern-cli/CI/stage_preflight.py`
- `intern-cli/CI/report.py`
- legacy `intern-cli/CI/case_slots/`
- legacy `c_*` active registry behavior
- active imports of `CI.native_remote`, `CI.remote_cases`, `CI.selection`,
  `CI.stage_preflight`, or `CI.report`

## Current Baseline

As of Session 158 after the F0052 correction on
`intern_ci_lead/task416_ci_f_post_merge_followup`:

- Active registry is F/J only: 43 cases, F=41, J=2, remote=43, local=0.
- Action registry listing passes with 350 actions.
- Assertion registry listing passes with 111 assertions.
- Case sets are `native=43`, `core=41`, `full=41`, `F=41`, and `J=2`.
- Registry audit passes with missing action/assertion refs=0, legacy
  action/assertion refs=0, and `stage_preflight_errors=0`.
- `case_slots/` has been removed from the repository.
- `CI/assertion/` has been migrated to `CI/assertions/`.
- `CI/selection.py` has been migrated to `CI/cases/selector.py`.
- `CI/native_remote.py`, `CI/remote_cases/`, `CI/remote_case_runner.py`, and
  `CI/runner/remote_worker.py` have been removed. The remote argv entrypoint is
  `CI.runner.stage_3_F.run_remote_case_argv()`, with F/J scenario bodies owned
  by `CI/cases/F/F_XXXX.py` and `CI/cases/J/J_XXXX.py`.
- Top-level `CI/runner.py`, `CI/stage_preflight.py`, `CI/planner.py`, and
  `CI/report.py` have been removed. Active runner implementation is under
  `CI/runner/`, including
  `stage_0_preflight.py`, `stage_1_unit_test.py`,
  `stage_2_package_deploy.py`, `stage_3_F.py`, `stage_4_J.py`,
  `planner.py`, `scheduler.py`, `reporting.py`, and `runner.py`.
- Active CI tests live under `intern-cli/CI/tests/`.
- Active F/J docs live under `intern-cli/CI/docs/F/` and
  `intern-cli/CI/docs/J/`.
- No active F/J case uses a local runner. `F_0052_session_resume_cli_claude_contract`
  is now a real remote F case.
- Latest master contract is that workspace mode is fixed at add time. Old
  in-place mode switch CLI/daemon/relay paths must stay removed; F_0006 is a
  negative removed-surface contract, not a mode-switch success story.

## Round Count Summary

Updated after Session 63: B7.R4 is complete; no implementation rounds remain.

Session 66 audit correction: the B7.R4 closeout overstated completion because
active remote execution still depends on the monolithic
`intern-cli/CI/remote_case_runner.py`. That file is outside the target design as
an active executor. The design document is unchanged; this tracker is reopened
only to finish the missing Phase 6 remote executor decomposition.

| Batch | Scope | Total Rounds | Done | Remaining |
|---|---|---:|---:|---:|
| Planning | Round tracker and progress protocol | 1 | 1 | 0 |
| Batch 1 | Shared evidence/reporting helpers | 2 | 2 | 0 |
| Batch 2 | Remote machine/product CLI helpers | 4 | 4 | 0 |
| Batch 3 | Product surface helpers | 4 | 4 | 0 |
| Batch 4 | Actions/assertions cutover | 5 | 5 | 0 |
| Batch 5 | Native remote retirement by domain | 10 | 10 | 0 |
| Batch 6 | Runner package/stage split | 5 | 5 | 0 |
| Batch 7 | Final cleanup/docs/tests contract | 4 | 4 | 0 |

Expected remaining focused sessions after the B7.R4 commit: 0.
Session 66 corrective remaining focused sessions: 6.
Small adjacent rounds may be combined only when they share the same verification surface and do not hide progress.

## Post-Completion Correction: Remote Executor Decomposition

Fixed progress line for Session 66:

```text
CI migration progress: post-completion correction, round P1/6, current round remote lifecycle/report split; remaining 6 rounds; expected completion in 6 focused sessions if no merge conflicts.
```

Session 78 progress: P4 in progress. Current next progress line:

```text
CI migration progress: post-completion correction, round P4/3, current round skill/policy/task projection assertion split; remaining 3 rounds; expected completion in 3 focused sessions if no merge conflicts.
```

Corrective rounds:

| Round | Title | Target Movement | Completion Evidence |
|---|---|---|---|
| P1 | remote lifecycle/report split | Move scenario recording, pass/fail lifecycle, product-bug evidence, report writing, and failure classification out of `remote_case_runner.py` into existing target modules: `runner/reporting.py` for report/lifecycle formatting and `assertions/core.py` for generic assertion primitives. | `remote_case_runner.py` no longer owns lifecycle/report primitives; focused lifecycle tests pass. |
| P2 | remote command/http context split | Move `run_cmd`, JSON CLI, daemon/relay HTTP, Feishu credentials, and base URL helpers into `helpers/product_cli_helper.py` / remote context helpers. | Remote executor delegates command/http surfaces to helpers; focused command/http tests pass. |
| P3 | workspace/intern/session action split | Move workspace, intern, session registry, cleanup, tmux, and green-light operations into `actions/` plus helper adapters. | Domain cases use `ctx.action.*` or helper adapters; no workspace/session primitive remains in the executor. |
| P4 | source/treeview/skill/policy assertion split | Move source-contract, TreeView projection, skill farm, policy/reconnect, and task projection assertions into `assertions/` or narrowly documented case-specific helpers. | Reusable assertion logic is registered or explicitly documented as case-specific; executor has no domain assertions. |
| P5 | run_ci-owned remote worker split | Replace the monolithic class with runner-owned internal remote worker logic that loads a case, builds remote context, executes the case module, and writes the report. This is not a new public `remote_run` entrypoint: `run_ci.py` remains the only user-facing CLI, and stage 3/4 own the SSH invocation detail. | `stage_3_F.py` / `stage_4_J.py` execute the internal runner worker; no active test path imports `CI.remote_case_runner`; no standalone public remote-run CLI exists. |
| P6 | delete monolith and final audit | Delete `remote_case_runner.py` and update package verification/docs/tests. | Active runtime scan proves no `CI.remote_case_runner` dependency; full registry/list/audit and focused remote dry-run gates pass. |

The six rounds are implementation checkpoints, not design changes. If a round
finds a product bug, the bug is reported separately; the CI refactor should not
hide the bug by changing assertions.

P1 completed in Session 66:

- Removed the design-incompatible draft helper path before committing it.
- Moved remote scenario lifecycle, report generation, failure classification,
  product-bug evidence aggregation, and write-report behavior into existing
  target module `CI/runner/reporting.py`.
- Moved generic native require check construction into existing target module
  `CI/assertions/core.py`.
- `remote_case_runner.py` now inherits `RemoteCaseLifecycleMixin` and no longer
  defines those lifecycle/report methods directly.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/remote_case_runner.py intern-cli/CI/runner/reporting.py intern-cli/CI/assertions/core.py intern-cli/CI/tests/test_ci_remote_context.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_remote_context.py intern-cli/CI/tests/test_ci_reporting_helpers.py intern-cli/CI/tests/test_ci_source_reporting_actions.py --tb=short` -> `12 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session66_p1_list_cases.json`
  - `git diff --check`
  - `test ! -e tmp`

P2 completed in Session 68:

- Moved command/check/error handling for product CLI calls into existing target
  module `CI/helpers/remote_context.py`, backed by
  `CI/helpers/product_cli_helper.py`.
- Moved JSON CLI object parsing adapter, daemon/relay base URL lookup, generic
  HTTP JSON wrappers, daemon/relay request wrappers, non-object response
  checks, redacted HTTP step recording, and Feishu credential loading into
  `RemoteCaseContext`.
- `remote_case_runner.py` now keeps only thin compatibility delegates for
  `run_cmd`, `json_cmd`, `daemon_base`, `relay_base`, `http_json`,
  `relay_json`, `request_json`, `daemon_request_json`,
  `relay_request_json`, `request_any_json`, and `feishu_credentials`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/helpers/remote_context.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_remote_context.py intern-cli/CI/tests/test_ci_f_transport_app_cases.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_remote_context.py intern-cli/CI/tests/test_ci_batch2_helpers.py intern-cli/CI/tests/test_ci_f_transport_app_cases.py --tb=short` -> `23 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json`
  - `git diff --check`
  - `test ! -e tmp`

P3 progress in Session 69:

- Added design-approved action roots `ctx.action.workspace`,
  `ctx.action.intern`, and `ctx.action.session` by registering
  `WorkspaceActions`, `InternActions`, and `SessionActions`.
- Added ctx actions and explicit resource locks for remote workspace list,
  workspace doctor, workspace delete, intern create/delete/metadata
  resolve/status/list, session start/status/stop, scoped session
  start/status/stop, and reusable tmux session name/capture/joined
  capture/input-ready/send primitives.
- `RemoteCaseContext` now exposes itself to `CaseContext` as
  `ctx.remote_context`, so action roots can use the remote command/http context
  without importing the remote executor.
- `remote_case_runner.py` now delegates those workspace/intern/session/tmux
  primitives to `ctx.action.*` and no longer owns their command bodies.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/actions/workspace.py intern-cli/CI/actions/intern.py intern-cli/CI/actions/session.py intern-cli/CI/actions/registry.py intern-cli/CI/helpers/remote_context.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_workspace_intern_session_actions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_workspace_intern_session_actions.py intern-cli/CI/tests/test_ci_remote_context.py intern-cli/CI/tests/test_ci_batch2_helpers.py intern-cli/CI/tests/test_ci_remote_intern_session_cases.py intern-cli/CI/tests/test_ci_remote_workspace_cases.py --tb=short` -> `31 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-actions --json >/tmp/intern_agent_CI/session69_p3_list_actions.json` -> 247 actions, new P3 ctx actions present
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session69_p3_list_cases.json` -> 43 cases
  - `git diff --check`
  - `test ! -e tmp`
- P3 is not complete yet: case initial cleanup, green Feishu group-light
  checks, and remaining workspace/session registry cleanup assertions are
  still owned directly by `remote_case_runner.py`.

P3 progress in Session 70:

- Added design-approved `ctx.action.feishu` by registering `FeishuActions`.
- Added ctx actions and explicit resource locks for relay registry entry
  lookup, relay registry wait, current scene green-light wait, and registry
  absent evidence.
- Moved session registry loading and runtime path derivation into
  `RemoteCaseContext`, and added `ctx.action.session.registry_entries_for_remote`
  for workspace/intern scoped session registry evidence.
- `remote_case_runner.py` now delegates relay registry entry, relay registry
  wait, current scene green-light wait, relay registry absent evidence,
  session registry loading, runtime dir, and scoped session registry entries to
  context/action adapters.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/actions/feishu.py intern-cli/CI/actions/session.py intern-cli/CI/actions/registry.py intern-cli/CI/helpers/remote_context.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_workspace_intern_session_actions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_workspace_intern_session_actions.py intern-cli/CI/tests/test_ci_remote_context.py intern-cli/CI/tests/test_ci_batch2_helpers.py intern-cli/CI/tests/test_ci_remote_intern_session_cases.py intern-cli/CI/tests/test_ci_remote_workspace_cases.py --tb=short` -> `33 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-actions --json >/tmp/intern_agent_CI/session70_p3_list_actions.json` -> 252 actions, new Session 70 actions present
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session70_p3_list_cases.json` -> 43 cases
  - `git diff --check`
  - `test ! -e tmp`
- P3 is still not complete: `case_initial_reset` target discovery/deletion,
  local registry cleanup, session registry rewrite, and state-dir cleanup are
  still owned directly by `remote_case_runner.py`.

P3 completed in Session 71:

- Added design-approved ctx actions and explicit resource locks for
  `workspace.remote_case_initial_reset` and `feishu.remote_wait_chat_lookup`.
- Moved case initial reset target discovery/deletion, group cleanup, local
  registry cleanup, session registry rewrite, and state-dir cleanup into
  `CI/actions/workspace.py`.
- Moved daemon chat lookup wait into `CI/actions/feishu.py`.
- `remote_case_runner.py` now delegates P3 workspace/intern/session/tmux,
  relay registry, green-light, chat lookup, session registry, runtime path,
  and case initial cleanup surfaces to `RemoteCaseContext` or `ctx.action.*`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/actions/feishu.py intern-cli/CI/actions/workspace.py intern-cli/CI/actions/registry.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_workspace_intern_session_actions.py`
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_action_registry() ...`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_workspace_intern_session_actions.py intern-cli/CI/tests/test_ci_remote_context.py intern-cli/CI/tests/test_ci_batch2_helpers.py intern-cli/CI/tests/test_ci_remote_intern_session_cases.py intern-cli/CI/tests/test_ci_remote_workspace_cases.py --tb=short` -> `34 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-actions --json >/tmp/intern_agent_CI/session71_p3_list_actions.json` -> 254 actions, new Session 71 actions present
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session71_p3_list_cases.json` -> 43 cases
  - `git diff --check`
  - `test ! -e tmp`

P4 progress in Session 72:

- Moved reusable workspace assertion predicates into `CI/assertions/workspace.py`:
  workspace record visibility/provider/mode/repo checks, relay workspace sync,
  metadata-root mode checks, business branch unchanged, workspace absence/extra
  record checks, workspace create failure classification, failed-attempt checks,
  and no-Feishu-group allocation checks.
- Updated `remote_case_runner.py` workspace assertion methods to collect
  evidence and delegate pass/fail decisions to `CI.assertions.workspace`
  while preserving existing native `require()` report entries.
- Updated `assert_gui_cli_equivalent()` to use existing design-approved
  `CI.assertions.surface.treeview_cli_equivalent_detail()` instead of directly
  checking the helper output in the executor.
- Added focused tests in `CI/tests/test_ci_workspace_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/workspace.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_workspace_assertions.py intern-cli/CI/tests/test_ci_mock_treeview_helper.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_workspace_assertions.py intern-cli/CI/tests/test_ci_remote_workspace_cases.py intern-cli/CI/tests/test_ci_workspace_intern_session_actions.py intern-cli/CI/tests/test_ci_mock_treeview_helper.py intern-cli/CI/tests/test_ci_surface_assertions.py intern-cli/CI/tests/test_ci_source_reporting_actions.py --tb=short` -> `28 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session72_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session72_p4_list_assertions.json` -> 113 assertions
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 is not complete yet: intern/session, task projection, skill/TreeView
  projection, and policy/reconnect assertion logic still has direct executor
  checks that need to move to `CI/assertions/` or documented case-local
  helpers.

P4 progress in Session 73:

- Moved reusable intern artifact/projection assertion predicates into
  `CI/assertions/intern.py`: metadata/status/session type consistency,
  TreeView/list projection contains, no intern artifacts, and intern removed
  filesystem/session/relay/tmux checks.
- Moved reusable Codex session restart/resume assertion predicates into
  `CI/assertions/session.py`: Codex UUID extraction, session id available,
  strict resume-required restart output, F-level fresh-or-resume restart
  output, same-session-id comparison, and Resume-this-intern command contract.
- Updated `remote_case_runner.py` intern/session assertion methods to collect
  evidence and delegate pass/fail decisions to `CI.assertions.intern` /
  `CI.assertions.session` while preserving existing native `require()` and
  `require_classified_contract()` report entries.
- Added focused tests in `CI/tests/test_ci_intern_session_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/intern.py intern-cli/CI/assertions/session.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_intern_session_assertions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_intern_session_assertions.py intern-cli/CI/tests/test_ci_workspace_assertions.py intern-cli/CI/tests/test_ci_remote_intern_session_cases.py intern-cli/CI/tests/test_ci_remote_workspace_cases.py intern-cli/CI/tests/test_ci_workspace_intern_session_actions.py intern-cli/CI/tests/test_ci_mock_treeview_helper.py intern-cli/CI/tests/test_ci_surface_assertions.py intern-cli/CI/tests/test_ci_source_reporting_actions.py --tb=short` -> `37 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session73_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session73_p4_list_assertions.json` -> 113 assertions
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 is not complete yet: task projection, skill/TreeView projection, and
  policy/reconnect assertion logic still has direct executor checks.

P4 progress in Session 75:

- Moved reusable deployed TreeView package menu predicates into
  `CI/assertions/treeview.py`: `view/item/context` command row extraction,
  required menu command checks, and `commandPalette` hidden command checks.
- Updated `remote_case_runner.py` menu assertion methods to delegate pass/fail
  decisions to `CI.assertions.treeview` while preserving existing native
  `require()` report entries and caller artifact shapes.
- Added focused tests in `CI/tests/test_ci_treeview_projection_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/treeview.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_treeview_projection_assertions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_treeview_projection_assertions.py intern-cli/CI/tests/test_ci_mock_treeview_helper.py intern-cli/CI/tests/test_ci_surface_assertions.py --tb=short` -> `11 passed`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_remote_treeview_task_skill_cases.py intern-cli/CI/tests/test_claude_treeview_skill_group_cases.py --tb=short` -> `9 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session75_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session75_p4_list_assertions.json` -> 113 assertions
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 is not complete yet: task/skill source contract and policy/reconnect
  assertion logic still has direct executor checks.

P4 progress in Session 76:

- Moved reusable task TreeView deployed bundle source-contract predicates into
  `CI/assertions/source_contract.py`: `F_0023` task TreeView projection bundle
  contract and `F_0024` task delete GUI bundle contract.
- Updated `remote_case_runner.py` `_f0023_dist_contract` and
  `_f0024_dist_contract` to delegate pass/fail decisions to
  `CI.assertions.source_contract` while preserving existing native `require()`
  report entries and artifact keys.
- Added focused tests in `CI/tests/test_ci_source_contract_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/source_contract.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_source_contract_assertions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_source_contract_assertions.py intern-cli/CI/tests/test_task_treeview_cases.py intern-cli/CI/tests/test_ci_remote_treeview_task_skill_cases.py --tb=short` -> `13 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session76_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session76_p4_list_assertions.json` -> 113 assertions
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 is not complete yet: skill source/farm projection contract and
  policy/reconnect assertion logic still has direct executor checks.

P4 progress in Session 77:

- Moved reusable skill source/scope deployed bundle source-contract predicates
  into `CI/assertions/source_contract.py`: `F_0029` skill source TreeView
  contract and `F_0030` Codex skill scope contract.
- Updated `remote_case_runner.py` `_f0029_dist_contract` and
  `_f0030_dist_contract` to delegate pass/fail decisions to
  `CI.assertions.source_contract` while preserving existing native `require()`
  report entries and artifact keys.
- Extended focused tests in `CI/tests/test_ci_source_contract_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/source_contract.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_source_contract_assertions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_source_contract_assertions.py intern-cli/CI/tests/test_task_treeview_cases.py intern-cli/CI/tests/test_ci_remote_treeview_task_skill_cases.py --tb=short` -> `18 passed`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_treeview_projection_assertions.py intern-cli/CI/tests/test_claude_treeview_skill_group_cases.py --tb=short` -> `7 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session77_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session77_p4_list_assertions.json` -> 113 assertions
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 is not complete yet: Claude skill/group source contract and
  policy/reconnect assertion logic still has direct executor checks.

P4 progress in Session 78:

- Moved reusable Claude deployed source-contract predicates into
  `CI/assertions/source_contract.py`: `F_0044` Claude TreeView command parity
  contract and `F_0045` Claude skill/group source contract.
- Updated `remote_case_runner.py` `_f0044_dist_contract` and
  `_f0045_source_contract` to delegate pass/fail decisions to
  `CI.assertions.source_contract` while preserving existing artifact keys.
- Extended focused tests in `CI/tests/test_ci_source_contract_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/source_contract.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_source_contract_assertions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_source_contract_assertions.py --tb=short` -> `15 passed`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_claude_treeview_skill_group_cases.py intern-cli/CI/tests/test_ci_remote_claude_cases.py --tb=short` -> `9 passed`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_remote_treeview_task_skill_cases.py intern-cli/CI/tests/test_ci_treeview_projection_assertions.py --tb=short` -> `10 passed`
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session78_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session78_p4_list_assertions.json` -> 113 assertions
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 is not complete yet: treeview top-level/menu source residuals and
  policy/reconnect assertion logic still has direct executor checks.

P4 progress in Session 79:

- Confirmed design boundary from supervisor feedback: do not add
  `CI/helpers/remote_case_lifecycle.py`; remote lifecycle/reporting remains
  under `CI/runner/reporting.py` and stage runner modules.
- Moved reusable TreeView top-level/menu deployed bundle source-contract
  predicates into `CI/assertions/source_contract.py`: `F_0031` TreeView
  top-level/config/status contract and `F_0032` TreeView menu visibility
  contract.
- Updated `remote_case_runner.py` `_f0031_dist_contract` and
  `_f0032_dist_contract` to delegate pass/fail decisions to
  `CI.assertions.source_contract` while preserving existing artifact keys.
- Extended focused tests in `CI/tests/test_ci_source_contract_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/source_contract.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_source_contract_assertions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_source_contract_assertions.py --tb=short` -> `20 passed`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_remote_treeview_task_skill_cases.py intern-cli/CI/tests/test_ci_treeview_projection_assertions.py intern-cli/CI/tests/test_claude_treeview_skill_group_cases.py --tb=short` -> `13 passed`
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session79_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session79_p4_list_assertions.json` -> 113 assertions
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 is not complete yet: `F_0021/F_0022` source residuals and
  policy/reconnect assertion logic still has direct executor checks.

P4 progress in Session 80:

- Moved reusable workspace TreeView deployed bundle source-contract predicates
  into `CI/assertions/source_contract.py`: `F_0021` workspace disable/delete
  GUI contract and `F_0022` workspace enable/doctor/refresh contract.
- Updated `remote_case_runner.py` `_f0021_dist_contract` and
  `_f0022_dist_contract` to delegate pass/fail decisions to
  `CI.assertions.source_contract` while preserving existing artifact keys.
- Deleted now-unused source-contract adapter residuals from
  `remote_case_runner.py`: `_compact_js`, `_require_dist_contract`,
  `_dist_contract_results`, and `_dist_command_block`.
- Extended focused tests in `CI/tests/test_ci_source_contract_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/source_contract.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_source_contract_assertions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_source_contract_assertions.py --tb=short` -> `25 passed`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_remote_workspace_cases.py intern-cli/CI/tests/test_ci_f_transport_app_cases.py --tb=short` -> `12 passed`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_remote_treeview_task_skill_cases.py intern-cli/CI/tests/test_ci_treeview_projection_assertions.py intern-cli/CI/tests/test_claude_treeview_skill_group_cases.py --tb=short` -> `13 passed`
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session80_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session80_p4_list_assertions.json` -> 113 assertions
  - `rg "_require_dist_contract|_dist_contract_results\\(|self\\._compact_js|_dist_command_block\\(" intern-cli/CI/remote_case_runner.py` -> no matches
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 is not complete yet: policy/reconnect assertion logic still has direct
  executor checks.

P4 progress in Session 81:

- Moved reusable policy/reconnect predicates into `CI/assertions/policy.py`:
  Idle Codex policy env restart, unchanged policy replay no duplicate restart,
  unchanged replay keeps Codex session, daemon start/restart connected, and no
  relay restart/global reset.
- Updated `remote_case_runner.py` `_wait_session_policy_restart`,
  `_assert_no_duplicate_policy_restart`, `_daemon_restart_for_policy_sync`,
  `_start_single_daemon`, and `_assert_no_relay_restart_or_global_reset` to
  delegate pass/fail decisions to `CI.assertions.policy` while keeping command
  execution, polling, error classification, and report adapter behavior in the
  executor.
- Added focused tests in `CI/tests/test_ci_policy_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/policy.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_policy_assertions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_policy_assertions.py --tb=short` -> `8 passed`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_f_policy_reconnect_cases.py intern-cli/CI/tests/test_ci_remote_workspace_cases.py --tb=short` -> `9 passed`
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session81_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session81_p4_list_assertions.json` -> 113 assertions
  - `rg "current_pids != before_pids|before_pids and current_pids|forbidden_steps|not forbidden|after\\.get\\(\\\"running\\\"\\).*relay_connected|status\\.get\\(\\\"running\\\"\\).*relay_connected" intern-cli/CI/remote_case_runner.py` -> no matches
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 is not complete yet: remaining direct domain assertion logic in
  `remote_case_runner.py` still needs one more audit before P5.

P4 progress in Session 82:

- Completed one remaining reusable assertion audit slice by moving fixture
  intern creation contract checks into `CI/assertions/intern.py`.
- Added `fixture_intern_contract_checks()` for status.md, knowledge.md,
  runtime, `.hook_state.json`, session registry type, status role, and team
  metadata.
- Updated `remote_case_runner.py` `create_fixture_intern()` to keep command
  execution, metadata/runtime/session sampling, and native report adapter
  behavior while delegating pass/fail decisions to `CI.assertions.intern`.
- Extended focused tests in `CI/tests/test_ci_intern_session_assertions.py`.
- Focused validation passed:
  - `python3 -m py_compile intern-cli/CI/assertions/intern.py intern-cli/CI/remote_case_runner.py intern-cli/CI/tests/test_ci_intern_session_assertions.py`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_intern_session_assertions.py --tb=short` -> `7 passed`
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests/test_ci_remote_intern_session_cases.py intern-cli/CI/tests/test_ci_remote_claude_cases.py --tb=short` -> `11 passed`
  - `PYTHONPATH=intern-cli python3 - <<'PY' ... validate_assertion_registry() ...` -> passed
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/session82_p4_list_cases.json` -> 43 cases
  - `PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/session82_p4_list_assertions.json` -> 113 assertions
  - `rg "_status_exists|_knowledge_exists|_runtime_exists|_hook_state_exists|_session_registry_type|_status_role|_status_team|_status_no_team" intern-cli/CI/remote_case_runner.py` -> no matches
  - `git diff --check`
  - `test ! -e tmp`
  - `test ! -e intern-cli/CI/helpers/remote_case_lifecycle.py`
- P4 reusable assertion split has covered the planned reusable
  source/treeview/skill/task/policy/reconnect/fixture-intern contracts. The
  remaining direct checks are scenario-specific or tied to case ownership and
  should move during P5 domain entrypoint/case module extraction rather than
  extending P4 indefinitely.

Current implementation pointer:

- Session 83 corrected the P5 boundary: no standalone public `remote_run`
  entrypoint is allowed. `run_ci.py` remains the only public CI CLI.
- Session 83 P5 progress moved the active remote executor from
  `CI/remote_case_runner.py` to runner-internal `CI/runner/remote_worker.py`.
  Stage 3 invokes it through `python3 -` and an import of
  `run_remote_case_argv()`, not by executing a second CI entrypoint file.
- `CI/remote_case_runner.py` no longer exists in the working tree, package
  verification now requires `CI/runner/remote_worker.py`, and active runtime
  scan over `run_ci.py`, `runner/`, and `verify_vsix_package.py` has no
  `CI.remote_case_runner`, `remote_case_runner.py`, `REMOTE_CASE_RUNNER_REL`,
  or standalone `remote_run` match.
- Session 83 P6 audit confirms active runtime/package/test paths no longer
  depend on `CI.remote_case_runner`; remaining text matches are historical
  migration notes or negative regression assertions.
- Current progress line after Session 83 P6 audit: `CI migration progress: post-completion correction complete; remote_case_runner active dependency removed; remaining 0 rounds.`

## Planning Round

### P0: publish round plan

Goal:

- Add this document.
- Send it to the supervisor Feishu group.
- Update Session metadata.

Acceptance:

- Document committed and pushed.
- Supervisor has the file.
- Future sessions can report batch/round/remaining count from this document.

## Batch 1: Shared Evidence And Reporting

Status: complete.

### B1.R1: reporting helper extraction

Done:

- `CI/helpers/reporting.py` owns redaction, scenario summaries, failure index, and product-bug aggregate details.

### B1.R2: source evidence helper extraction

Done:

- `CI/helpers/source_evidence.py` owns source/handler evidence.

## Batch 2: Remote Machine And Product CLI Helpers

Status: complete.

### B2.R1: product CLI helper

Done:

- `CI/helpers/product_cli_helper.py` owns product command execution and JSON stdout parsing adapters.

### B2.R2: remote machine HTTP/helper primitives

Done:

- `CI/helpers/remote_machine_helper.py` owns HTTP JSON request adapters.

### B2.R3: tmux helper primitives

Done:

- Tmux capture/send/update-prompt detection and prompt artifact writing are helper-backed.

### B2.R4: file/artifact helper primitives

Done:

- JSON path/object loading, atomic JSON writing, and report writing adapters are helper-backed.

## Batch 3: Product Surface Helpers

Status: complete.

### B3.R1: source contract helper

Done:

- `CI/helpers/source_contract_helper.py` owns source/bundled artifact scans.

### B3.R2: mock Feishu helper

Done:

- `CI/helpers/mock_feishu_helper.py` owns visible `[CI模拟]` operation evidence and retained-scene metadata for relay-driver simulation.

### B3.R3: mock TreeView helper

Done:

- `CI/helpers/mock_treeview_helper.py` owns GUI command/tree item/context menu/QuickPick/input evidence shapes.

### B3.R4: real Feishu ingress helper boundary

Done:

- `CI/helpers/mock_feishu_helper.py` owns reusable real Feishu ingress/callback evidence construction:
  handler entrypoint gaps, source-driver metadata, message/card result envelopes,
  response summaries, card value/button traversal, action summaries, form values,
  and stable card text serialization.
- `NativeRemoteCase` keeps old method names as adapters while no longer owning
  the reusable surface traversal/envelope logic.
- F_0041/config-helper focused tests pass.

## Batch 4: Actions And Assertions Cutover

Status: 5/5 done.

### B4.R1: helper-backed actions

Done:

- TreeView mock, Feishu mock, source contract, and reporting actions are registered and helper-backed.

### B4.R2: assertions package cutover

Done:

- `CI/assertion/` was removed and `CI/assertions/` is active.

### B4.R3: split action registry into domain modules

Done:

- `actions/registry.py` is now a catalog loader: it imports domain modules, builds
  `ActionDefinition` objects from domain specs, and merges explicit domain
  `ACTION_RESOURCE_LOCKS`.
- Action definitions and locks are owned by target domain modules:
  `workspace.py`, `intern.py`, `session.py`, `feishu.py`, `treeview.py`,
  `policy.py`, `relay_daemon.py`, and `source_contract.py`.
- `run_ci --list-actions --json` output is byte-for-byte equivalent as JSON data
  to the pre-migration snapshot: 227 actions, same order, same fields, same locks.

### B4.R4: split assertion registry into domain modules

Done:

- `assertions/registry.py` is now a catalog loader: it imports target domain
  modules, builds `AssertionDefinition` objects from domain specs, and validates
  ctx assertion callables against `CaseAssertions`.
- Assertion specs are owned by target domain modules:
  `core.py`, `workspace.py`, `intern.py`, `session.py`, `feishu.py`,
  `treeview.py`, `policy.py`, and `source_contract.py`.
- `run_ci --list-assertions --json` output is byte-for-byte equivalent as JSON
  data to the pre-migration snapshot: 112 assertions, same order, same fields.

### B4.R5: action/assertion contract audit

Done:

- `CI/cases/audit.py` provides a reusable active F/J action/assertion contract audit.
- `run_ci --audit-registry --json` emits the machine-readable audit report and
  returns nonzero only for action/assertion registry contract failures.
- `stage_preflight.py` now validates F/J case assertion references in addition to
  action references.
- Session 43 audit report `/tmp/intern_agent_CI/session43_b4r5_registry_audit.json`
  passed: 43 active F/J cases, 227 registered actions, 112 registered assertions,
  97 referenced actions, 55 referenced assertions, zero missing action refs, zero
  missing assertion refs, zero legacy action refs, and zero legacy assertion refs.
- The audit report also records two existing J_0014 stage-preflight boundary
  errors as child evidence without mixing them into the action/assertion contract
  result; those are not missing-id defects.

## Batch 5: Native Remote Retirement By Domain

Status: remote context extracted; workspace, intern/session, daemon/relay/policy,
config/helper, task/treeview/skill, Claude, and J journey scenario bodies moved
out of `native_remote.py`; the active remote worker now lives under
`CI/runner/remote_worker.py`; helper retirement remains.

### B5.R1: remote context extraction

Done:

- Added `CI/helpers/remote_context.py` with `RemoteCaseContext`,
  `remote_resource_namespace()`, and `remote_runtime_namespace()`.
- `RemoteCaseContext` owns shared native remote state construction: paths, CLI
  command argv, environment, product/remote/tmux/file/source/mock helpers,
  `CaseContext`, scenario/check containers, artifacts, created resources, and run token.
- `NativeRemoteCase.__init__` now consumes `RemoteCaseContext` and keeps existing
  public attributes as adapters for current scenario bodies.
- Remote domain Protocols now declare `remote_context: RemoteCaseContext`, so
  domain code can consume the extracted context without importing `CI.native_remote`.
- Focused tests passed through the new context.

### B5.R2: workspace domain body move

Done.

Scope:

- Move `F_0001`-`F_0006`, `F_0021`, `F_0022` bodies out of `native_remote.py`.

Result:

- `CI/remote_cases/workspace.py` now owns the eight workspace scenario body
  functions and dispatches via callable runners.
- `NativeRemoteCase` no longer declares `run_f_workspace_*` methods.
- Focused workspace/context tests passed after the move.

Acceptance:

- Workspace domain focused tests pass.
- No workspace scenario body remains in `native_remote.py`.

### B5.R3: intern/session domain body move

Done.

Scope:

- Move `F_0007`-`F_0010`, `F_0033` bodies out of `native_remote.py`.

Result:

- `CI/remote_cases/intern_session.py` now owns the five intern/session
  scenario body functions and dispatches via callable runners.
- `NativeRemoteCase` no longer declares the migrated intern/session F methods.
- Focused intern/session/context tests passed after the move.

Acceptance:

- Intern/session focused tests pass.
- No intern/session scenario body remains in `native_remote.py`.

### B5.R4: daemon/relay/policy domain body move

Done.

Scope:

- Move `F_0011`-`F_0013`, `F_0034`, `F_0037` bodies out of `native_remote.py`.

Result:

- `CI/remote_cases/daemon_relay.py` now owns the five daemon/relay/policy
  scenario body functions and dispatches via callable runners.
- `NativeCaseError` moved to `CI/helpers/native_error.py` so migrated domain
  functions and `NativeRemoteCase.run_ordered_scenarios()` share the same error
  type without importing `CI.native_remote` from a domain module.
- `NativeRemoteCase` no longer declares the migrated daemon/relay/policy F methods.
- Focused daemon/relay/context tests passed after the move.

Acceptance:

- Daemon/relay/policy focused tests pass.
- No daemon/relay/policy scenario body remains in `native_remote.py`.

### B5.R5: config/helper/transport domain body move

Done.

Scope:

- Move `F_0015`-`F_0020`, `F_0035`, `F_0036`, `F_0041` bodies out of `native_remote.py`.

Result:

- `CI/remote_cases/config_helper.py` now owns the nine config/helper/transport
  scenario body functions and dispatches via callable runners.
- `NativeRemoteCase` no longer declares the migrated config/helper F methods.
- Focused config/helper/context tests passed after the move.

Acceptance:

- Config/helper focused tests pass.
- No config/helper scenario body remains in `native_remote.py`.

### B5.R6: task/treeview/skill domain body move

Done.

Scope:

- Move `F_0023`-`F_0032` bodies out of `native_remote.py`.

Result:

- `CI/remote_cases/treeview_task_skill.py` now owns the ten task/treeview/skill
  scenario body functions and dispatches via callable runners.
- `NativeRemoteCase` no longer declares the migrated task/treeview/skill F methods.
- Focused task/treeview/skill/context tests passed after the move.

Acceptance:

- Task/TreeView/skill focused tests pass.
- No task/treeview/skill scenario body remains in `native_remote.py`.

### B5.R7: Claude domain body move

Done.

Scope:

- Move `F_0043`-`F_0045` bodies out of `native_remote.py`.

Result:

- `CI/remote_cases/claude.py` now owns the three Claude scenario body
  functions and dispatches via callable runners.
- `NativeRemoteCase` no longer declares the migrated Claude F methods.
- Focused Claude/context tests passed after the move.

Acceptance:

- Claude focused tests pass.
- No Claude scenario body remains in `native_remote.py`.

### B5.R8: J journey body move

Done.

Scope:

- Move `J_0014` and `J_0033` bodies out of `native_remote.py`.

Result:

- `CI/remote_cases/journeys.py` now owns the two J journey scenario body
  functions and dispatches via callable runners.
- `NativeRemoteCase` no longer declares the migrated J methods.
- Focused journey/context tests passed after the move.

Acceptance:

- J focused tests pass.
- No J journey scenario body remains in `native_remote.py`.

### B5.R9: replace remote entrypoint

Done.

Scope:

- Replace `CI/native_remote.py` CLI entrypoint with a target-compatible remote runner module.
- Update `runner/runner.py` remote command construction to call the new module.

Result:

- Added `CI/remote_case_runner.py` as the remote debug-machine CLI entrypoint.
- Remote case scripts, shared-cleanup scripts, CI harness staging checks, and
  VSIX package required runtime member now use `CI/remote_case_runner.py`.
- `native_remote.py` remains only as temporary implementation/helper storage
  until B5.R10.

Superseded by Session 83 P5/P6: the active remote worker moved to
`CI/runner/remote_worker.py`, and `CI/remote_case_runner.py` was removed from
the working tree.

Acceptance:

- Remote harness script no longer calls `CI/native_remote.py`.
- Existing-deployment dry-run and focused remote command tests pass.

### B5.R10: delete native_remote and remote_cases

Completed in Session 54.

Scope:

- Delete `intern-cli/CI/native_remote.py`.
- Delete `intern-cli/CI/remote_cases/`.
- Update tests so active runtime imports target modules directly.

Acceptance:

- `test ! -e intern-cli/CI/native_remote.py`.
- `test ! -e intern-cli/CI/remote_cases`.
- Active import scan finds no `CI.native_remote` or `CI.remote_cases`.
- `run_ci --list-cases`, `--list-actions`, `--list-assertions` pass.

## Batch 6: Runner Package And Stage Split

Status: complete.

### B6.R1: runner package cutover

Completed in Session 55.

Scope:

- Move top-level `runner.py` into `runner/runner.py`.
- Add `runner/__init__.py`.
- Update imports to use `CI.runner.runner` for implementation functions.

Acceptance:

- `test ! -e intern-cli/CI/runner.py`.
- `run_ci.py` and `run_light_ci.py` import from `CI.runner.runner`.
- Focused runner tests pass.

### B6.R2: stage 0 preflight module

Completed in Session 56.

Scope:

- Move top-level `stage_preflight.py` into `runner/stage_0_preflight.py`.

Acceptance:

- `test ! -e intern-cli/CI/stage_preflight.py`.
- Stage preflight tests pass.

### B6.R3: planner/scheduler split

Completed in Session 57.

Scope:

- Move planner and scheduling logic into `runner/planner.py` and `runner/scheduler.py`.
- Preserve JSON/DOT/Mermaid conflict graph output.

Acceptance:

- Planner dry-run emits the same artifact set.
- Resource-lock tests pass.

### B6.R4: stage 1 and stage 2 modules

Completed in Session 58.

Scope:

- Move unit/light CI and package/deploy orchestration into `stage_1_unit_test.py` and `stage_2_package_deploy.py`.

Acceptance:

- Unit-only and dry-run package/deploy commands preserve report schema.
- No deploy/package command is run during this refactor validation unless explicitly requested.

### B6.R5: stage 3/4 and runner facade

Completed in Session 59.

Scope:

- Move F and J execution paths into `stage_3_F.py` and `stage_4_J.py`.
- Keep `runner/runner.py` as the orchestrating facade.

Acceptance:

- F/J selection and stage order tests pass.
- `run_ci.py` remains the only public CLI entrypoint.

## Batch 7: Final Cleanup And Contract Tightening

Status: complete.

### B7.R1: CI system tests target layout

Done in Session 60.

Scope:

- Move active CI system tests into `intern-cli/CI/tests/` or explicitly classify non-moved tests as product tests.
- Delete or rewrite old c-series fixture tests that are no longer valid CI architecture tests.

Acceptance:

- Active CI refactor tests live under the target `CI/tests/` layout.
- No active CI test imports deleted legacy modules.

Session 60 result:

- Moved active `test_ci_*.py` CI system tests from `intern-cli/tests/` into `intern-cli/CI/tests/`.
- Deleted obsolete c-series active case tests that no longer match the F/J-only registry.
- Rewrote retained runner/resource/registry tests to use F/J or synthetic F fixtures instead of `c_*` registry fixtures.
- Updated tests that still expected old `full` case-set exclusion or old `ci_runner._is_local_case` private runner API.
- Verified `intern-cli/CI/tests/test_ci_*.py` -> `202 passed`, TreeView/Claude CI tests -> `7 passed`, registry/list gates -> 43 cases with F=41/J=2 and 0 legacy refs.

### B7.R2: docs layout cleanup

Done in Session 61.

Scope:

- Keep `docs/F`, `docs/J`, and `CI_ARCHITECTURE_DESIGN.md`.
- Move or delete root-level stale CI docs that are no longer part of F/J scripts or architecture.
- Keep `README.md` and `AUTHORING.md` only as project-mandated operational docs.

Acceptance:

- No docs instruct new work to use deleted legacy paths.
- F/J script indexes are under `docs/F` and `docs/J`.

Session 61 result:

- Moved `F_0051.md` into `docs/F/`.
- Moved `J_0001.md`-`J_0008.md`, `J_SCRIPT_INDEX.md`, and J action helper docs into `docs/J/`.
- Deleted stale root `BUG0010_REQUEST_USER_INPUT_CASES.md`.
- Replaced stale `CI_MIGRATION_EXECUTION_PLAN.md` contents with a pointer to `CI_MIGRATION_ROUND_PLAN.md`, avoiding outdated intermediate path guidance while preserving the design-doc reference.
- Updated `AUTHORING.md` to point CI system tests at `intern-cli/CI/tests/test_ci_*.py`.
- Added `test_ci_docs_follow_target_f_j_layout` to enforce the layout.

### B7.R3: final active import and filesystem audit

Done in Session 62.

Scope:

- Add/maintain tests proving deleted paths cannot be imported or found.
- Scan for active references to deleted paths.

Acceptance:

- No active references to `CI.native_remote`, `CI.remote_cases`, `CI.selection`, `CI.stage_preflight`, `CI.assertion`, or `case_slots`.

Session 62 result:

- Moved the only active local F runner from root `intern-cli/CI/local_intern_session_cli.py` to `intern-cli/CI/cases/F/local_intern_session_cli.py`.
- Deleted root legacy local runner/fixture modules that no longer have active F/J registry entries: `local_askuser.py`, `local_debug_diagnostics.py`, `local_f_gui_report_skill.py`, `local_f_hook_context_guard.py`, `local_f_workspace_treeview.py`, `local_peer_mailbox_goal.py`, `local_task_treeview.py`, `local_vscode_treeview.py`, `setup_config_skill_security_local.py`, and `workspace_local.py`.
- Removed old local platform callback support from `runner/runner.py`, `runner/stage_3_F.py`, and `runner/stage_4_J.py`; `stage_3_F.is_local_case()` now recognizes only active local F kinds.
- Removed stale `c_0003`/`c_0010` remote scenario bodies and shared cleanup dispatch from `remote_case_runner.py`; active F/J shared cleanup now reports explicit no-op evidence.
- Updated `run_ci --case-set` help and active authoring/docs text to describe current F/J/core/full/native sets without old stage names.
- Added `test_ci_active_runtime_has_no_legacy_imports_or_root_local_runners` to enforce deleted root local runners and active runtime legacy import absence.
- Verification passed: `test_ci_*.py` -> `201 passed`; TreeView/Claude support tests -> `5 passed`; active runtime legacy scan passed; root legacy local file absence check passed; F_0052 dry-run and local run both `ok=true`; registry/list gates stayed at 43 cases with F=41/J=2, actions=227, assertions=112, audit passed. Session 158 superseded the F_0052 local-run part of this historical result.

### B7.R4: final focused verification and report

Done in Session 63.

Scope:

- Run the final non-deploy validation suite for the refactor branch.
- Produce a short migration completion report.

Acceptance:

- `py_compile` on active CI modules passes.
- Focused pytest passes.
- `run_ci --list-cases`, `--list-actions`, `--list-assertions`, `--list-case-sets` pass.
- `git diff --check` passes.
- `test ! -e tmp` passes.

Session 63 result:

- Added explicit `resource_locks` to `J_0014_peer_send_routing_error_contract`
  and `J_0033_codex_exit_resume_same_session_journey`.
- Added journey-level action metadata to `J_0014` so stage preflight can
  classify it as a real J journey instead of a capability-only case.
- Moved root `CI/report.py` to `CI/runner/reporting.py` and updated active
  imports.
- Added `CI_MIGRATION_COMPLETION_REPORT.md`.
- Verification passed:
  - `py_compile` on all `intern-cli/CI` Python files.
  - `PYTHONPATH=intern-cli python3 -m pytest -q intern-cli/CI/tests --tb=short`
    -> `206 passed, 1 warning`.
  - Registry/list gates -> 43 cases with F=41/J=2, remote=42/local=1,
    actions=227, assertions=112, case sets `native=43/core=41/full=41/F=41/J=2`.
  - Registry audit -> `ok=true`, `stage_preflight_errors=0`.
  - `F_0052_session_resume_cli_claude_contract` local run ->
    `/tmp/intern_agent_CI/session63_b7r4_f0052_local_final3.json`,
    `passed=1 failed=0 skipped=0`. Session 158 superseded this local fixture
    with a remote F case.
  - Mixed F/J planner dry-run contract ->
    `/tmp/intern_agent_CI/session63_b7r4_fj_existing_dry_final3_contract.json`,
    planner `ok=true`, case_count=2, missing/invalid resource locks=0,
    conflict_edge_count=1, wave_count=2, and all graph artifacts present.
  - Active runtime legacy scan passed, including absence of root `CI.report`
    imports; root legacy local/report file absence check passed;
    `git diff --check` passed; `test ! -e tmp` passed.
- No package/deploy, VSIX install, hook install, relay restart, master push, or
  product code change was performed.

## Session Start Example

Use the current implementation pointer above as the next session progress line.
The old "0 remaining" migration-complete state was superseded by the
post-completion corrective rounds while `remote_case_runner.py` was still an
active executor; Session 83 removed that active dependency.

```text
CI migration progress: post-completion correction complete; remote_case_runner active dependency removed; remaining 0 rounds.
```

## Standard Verification Per Round

Every round must run:

```bash
python3 -m py_compile <changed python files>
PYTHONPATH=intern-cli python3 -m pytest -q <focused tests>
PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-cases --fields id,stage,kind --json >/tmp/intern_agent_CI/<run_id>_list_cases.json
git diff --check
test ! -e tmp
```

Additional registry rounds also run:

```bash
PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-actions --json >/tmp/intern_agent_CI/<run_id>_list_actions.json
PYTHONPATH=intern-cli python3 intern-cli/CI/run_ci.py --list-assertions --json >/tmp/intern_agent_CI/<run_id>_list_assertions.json
```

Final cleanup rounds also run active reference scans:

```bash
rg -n "CI\\.native_remote|CI\\.remote_cases|CI\\.selection|CI\\.stage_preflight|CI\\.assertion|case_slots" intern-cli
```
