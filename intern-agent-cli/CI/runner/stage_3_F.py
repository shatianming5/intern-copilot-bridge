from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

from CI.helpers import deployment_primitives as full_primitives
from CI.cases import registry as case_registry
from CI.cases.base import CaseDefinition
from CI.helpers.native_error import NativeCaseError
from CI.helpers.reporting import native_report_summary_from_stdout
from CI.helpers.remote_context import RemoteCaseContext, remote_runtime_namespace
from CI.helpers.remote_machine_helper import tmux_input_prompt_index
from CI.runner.reporting import RemoteCaseLifecycleMixin, finalize_case_result, now, run_command
from CI.runner.scheduler import plan_remote_cases


REMOTE_STAGE_MODULE = "CI.runner.stage_3_F"

F_TRANSPORT_APP_KINDS = {
    "f_repo_mode_resource_checkout_matrix",
    "f_cross_machine_peer_transport_smoke",
    "f_goal_transport_smoke",
    "f_helper_service_start_stop_smoke",
    "f_feishu_app_minimal_auth_main_bot_smoke",
    "f_daemon_relay_api",
}
F_WORKSPACE_GUI_KIND = "f_workspace_gui"
F_INTERN_SESSION_KINDS = {
    "f_intern_session_remote",
}
F_TASK_TREEVIEW_REMOTE_KINDS = {
    "f_task_treeview_projection_contract",
    "f_task_delete_gui_contract",
}
F_SKILL_CONFIG_TREEVIEW_REMOTE_KINDS = {
    "f_skill_source_treeview_projection_mutation",
    "f_codex_skill_repo_personal_enable_contract",
    "f_treeview_top_level_config_status_contract",
    "f_treeview_menu_visibility_context_contract",
}
F_CLAUDE_TREEVIEW_SKILL_GROUP_REMOTE_KINDS = {
    "f_claude_treeview_projection_command_parity_contract",
    "f_claude_skill_farm_group_parity_contract",
}
F_WORKSPACE_TREEVIEW_REMOTE_KINDS = {
    "f_workspace_disable_delete_gui_contract",
    "f_workspace_enable_doctor_refresh_contract",
}
F_POLICY_RECONNECT_REMOTE_KINDS = {
    "f_policy_env_idle_codex_auto_restart_contract",
    "f_daemon_reconnect_registry_policy_resync_contract",
}
J_USER_JOURNEY_REMOTE_KINDS = {
    "j_policy_reconcile_same_session_journey",
}


def _attach_plan_fields(result: dict[str, Any], plan_entry: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(result)
    for key in (
        "schedule_order",
        "concurrency_wave",
        "concurrency_slot",
        "declared_resources",
        "serial_reason",
        "setup_gate",
    ):
        enriched[key] = plan_entry[key]
    return enriched


def case_stage(case: CaseDefinition) -> str:
    raw = case.extra.get("ci_stage") or case.extra.get("stage") or ""
    if raw:
        return str(raw).upper()
    prefix = case.id.split("_", 1)[0]
    return prefix.upper() if prefix in {"F", "J"} else "legacy"


def is_f_case(case: CaseDefinition) -> bool:
    return case_stage(case) == "F"


class StageRemoteCase(RemoteCaseLifecycleMixin):
    def __init__(self, args: argparse.Namespace):
        self.remote_context = RemoteCaseContext(args=args, machine_id=self.machine_id())

    def __getattr__(self, name: str) -> Any:
        remote_context = self.__dict__.get("remote_context")
        if remote_context is not None:
            try:
                return getattr(remote_context, name)
            except AttributeError:
                pass
        raise AttributeError(f"{type(self).__name__!s} object has no attribute {name!r}")

    def _runtime_namespace(self) -> str:
        return remote_runtime_namespace(self.case_id)

    @staticmethod
    def machine_id() -> str:
        return os.environ.get("INTERN_CI_MACHINE_ID") or os.environ.get("HOSTNAME") or os.uname().nodename

    def run_shared_cleanup(self) -> None:
        self.artifacts["shared_cleanup"] = {
            "ok": True,
            "status": "passed",
            "reason": "active F/J cases have no shared repo cleanup",
        }

    def run(self) -> None:
        try:
            runner = case_registry.resolve_remote_case_runner(self.case_id)
        except KeyError as exc:
            raise NativeCaseError(f"unsupported native remote case: {self.case_id}") from exc
        runner(self)


def parse_remote_case_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a CI-native remote case on a debug machine.")
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--report", required=True)
    parser.add_argument("--expected-machines", type=int, default=1)
    parser.add_argument("--protected-repo", default="")
    parser.add_argument("--nonprotected-repo", default="")
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--started-at", default=now())
    parser.add_argument("--cleanup-shared", action="store_true")
    parser.add_argument("--reset-case", action="store_true", help="Clean this case namespace before running and preserve the scene afterwards.")
    return parser.parse_args(argv)


def run_remote_case_argv(argv: list[str]) -> int:
    args = parse_remote_case_args(argv)
    case = StageRemoteCase(args)
    try:
        if args.cleanup_shared:
            case.run_shared_cleanup()
        else:
            if args.reset_case:
                case.ctx.action.workspace.case_initial_reset_remote(
                    case.workspace_name_prefix(),
                    case.intern_name_prefix(),
                )
            case.run()
    except Exception as exc:  # noqa: BLE001
        report = case.report(ok=False, error=str(exc))
        case.write_report(report)
        print(json.dumps({"ok": False, "error": str(exc), "report": str(case.report_path)}, ensure_ascii=False))
        return 1
    report = case.report(ok=True)
    case.write_report(report)
    print(json.dumps({"ok": True, "report": str(case.report_path)}, ensure_ascii=False))
    return 0


def native_remote_case_script(
    *,
    work_root: str,
    case: CaseDefinition,
    expected_machines: int,
    protected_repo: str,
    nonprotected_repo: str,
    ci_harness_root: str = "",
    reset_case: bool = True,
) -> str:
    artifact = f"{work_root}/ci-artifacts/{case.id}"
    report = f"{artifact}/{case.id}-native.json"
    reset_case_arg = " \\\n  --reset-case" if reset_case else ""
    script_root = ci_harness_root or f"{work_root}/extension/bundled-cli"
    deployed_cli_root = f"{work_root}/extension/bundled-cli"
    return f"""
set -euo pipefail
export WORK_AGENTS_ROOT={shlex.quote(work_root)}
export PATH={shlex.quote(work_root)}/extension/bundled-cli:/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH
export PYTHONPATH={shlex.quote(script_root)}:{shlex.quote(deployed_cli_root)}:${{PYTHONPATH:-}}
set -a
[ -f {shlex.quote(work_root)}/enterprise_policy/daemon/user.env ] && . {shlex.quote(work_root)}/enterprise_policy/daemon/user.env
set +a
mkdir -p {shlex.quote(artifact)}
set +e
python3 - \
  --case-id {shlex.quote(case.id)} \
  --repo-root {shlex.quote(deployed_cli_root)} \
  --work-root {shlex.quote(work_root)} \
  --artifact-dir {shlex.quote(artifact)} \
  --report {shlex.quote(report)} \
  --expected-machines {int(expected_machines)} \
  --protected-repo {shlex.quote(protected_repo)} \
  --nonprotected-repo {shlex.quote(nonprotected_repo)} \
  --timeout {int(case.timeout_seconds or 1800)}{reset_case_arg} <<'PY'
import sys

from {REMOTE_STAGE_MODULE} import run_remote_case_argv

raise SystemExit(run_remote_case_argv(sys.argv[1:]))
PY
rc=$?
set -e
if [ -f {shlex.quote(report)} ]; then
  python3 - {shlex.quote(report)} <<'PY' || true
import sys

from CI.helpers.reporting import native_report_summary_prefixed_line

print(native_report_summary_prefixed_line(sys.argv[1]))
PY
fi
exit "$rc"
"""


def _native_remote_report_summary_from_remote(
    *,
    machine: dict[str, Any],
    report: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {}
    python = r'''
import sys

from CI.helpers.reporting import native_report_summary_json_from_path

print(native_report_summary_json_from_path(sys.argv[1]))
'''
    cmd = f"python3 - {shlex.quote(report)} <<'PY'\n{python}\nPY"
    run = run_command(
        full_primitives.ssh_base(machine) + [cmd],
        cwd=cwd,
        timeout=min(timeout, 120),
        dry_run=False,
    )
    if not run.get("ok") or not run.get("stdout"):
        return {}
    try:
        parsed = json.loads(str(run.get("stdout") or ""))
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _run_native_remote_case(
    case: CaseDefinition,
    *,
    machine: dict[str, Any],
    work_root: str,
    expected_machines: int,
    protected_repo: str,
    nonprotected_repo: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    ci_harness_root: str = "",
) -> dict[str, Any]:
    remote_artifacts = f"{work_root}/ci-artifacts/{case.id}"
    command = native_remote_case_script(
        work_root=work_root,
        case=case,
        expected_machines=expected_machines,
        protected_repo=protected_repo,
        nonprotected_repo=nonprotected_repo,
        ci_harness_root=ci_harness_root,
        reset_case=bool(case.extra.get("native_reset_case", True)),
    )
    run = run_command(
        full_primitives.ssh_base(machine) + [command],
        cwd=cwd,
        timeout=timeout,
        dry_run=dry_run,
    )
    report_path = f"{remote_artifacts}/{case.id}-native.json"
    summary = native_report_summary_from_stdout(str(run.get("stdout") or ""))
    if not summary:
        summary = _native_remote_report_summary_from_remote(
            machine=machine,
            report=report_path,
            cwd=cwd,
            timeout=timeout,
            dry_run=dry_run,
        )
    result = {
        "case_id": case.id,
        "name": case.name,
        "ok": bool(run.get("ok")),
        "status": "passed" if run.get("ok") else ("skipped" if dry_run else "failed"),
        "remote_artifacts": remote_artifacts,
        "report": report_path,
        "run": run,
        "failure_reason": "" if run.get("ok") else run.get("failure_reason", "native remote case failed"),
    }
    if summary:
        result["native_report_summary"] = summary
        for key in (
            "scenarios",
            "scenario_summary",
            "failure_index",
            "failure_classification",
            "resource_namespace",
        ):
            if key in summary and summary[key]:
                result[key] = summary[key]
        if summary.get("failure_reason") and not run.get("ok"):
            result["failure_reason"] = summary["failure_reason"]
    return result


def run_remote_case_on_machine(
    case: CaseDefinition,
    *,
    machine: dict[str, Any],
    work_root: str,
    expected_machines: int,
    protected_repo: str,
    nonprotected_repo: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    ci_harness_root: str = "",
) -> dict[str, Any]:
    if not case.enabled:
        result = {
            "case_id": case.id,
            "name": case.name,
            "ok": False,
            "status": "skipped",
            "machine_id": machine.get("id", ""),
            "failure_reason": "case disabled",
        }
    elif not case.ci_native:
        result = {
            "case_id": case.id,
            "name": case.name,
            "ok": False,
            "status": "skipped" if dry_run else "failed",
            "remote_artifacts": f"{work_root}/ci-artifacts/{case.id}",
            "failure_reason": (
                "dry run"
                if dry_run
                else "case is selected but has not been migrated to intern-cli/CI native actions/assertions"
            ),
        }
    elif case.kind in F_TRANSPORT_APP_KINDS | {F_WORKSPACE_GUI_KIND} | F_INTERN_SESSION_KINDS | F_TASK_TREEVIEW_REMOTE_KINDS | F_WORKSPACE_TREEVIEW_REMOTE_KINDS | F_SKILL_CONFIG_TREEVIEW_REMOTE_KINDS | F_POLICY_RECONNECT_REMOTE_KINDS | F_CLAUDE_TREEVIEW_SKILL_GROUP_REMOTE_KINDS | J_USER_JOURNEY_REMOTE_KINDS:
        result = _run_native_remote_case(
            case,
            machine=machine,
            work_root=work_root,
            expected_machines=expected_machines,
            protected_repo=protected_repo,
            nonprotected_repo=nonprotected_repo,
            cwd=cwd,
            timeout=case.timeout_seconds or timeout,
            dry_run=dry_run,
            ci_harness_root=ci_harness_root,
        )
    else:
        result = {
            "case_id": case.id,
            "name": case.name,
            "ok": False,
            "status": "skipped",
            "failure_reason": f"unsupported case kind: {case.kind}",
        }
    result["machine_id"] = machine.get("id", "")
    result["machine_host"] = machine.get("host", "")
    return finalize_case_result(result, case_id=case.id, name=case.name)


def run_remote_cases(
    *,
    cases: list[CaseDefinition],
    machines: list[dict[str, Any]],
    work_root: str,
    expected_machines: int,
    protected_repo: str,
    nonprotected_repo: str,
    cwd: Path,
    timeout: int,
    parallel_workers: int,
    dry_run: bool,
    ci_harness_root: str = "",
) -> list[dict[str, Any]]:
    if not cases:
        return []
    plan = plan_remote_cases(cases=cases, machines=machines, parallel_workers=parallel_workers)
    ordered: list[dict[str, Any] | None] = [None] * len(plan)
    waves = sorted({item["concurrency_wave"] for item in plan})

    def run_plan_entry(entry: dict[str, Any]) -> dict[str, Any]:
        case = cases[int(entry["case_index"])]
        result = run_remote_case_on_machine(
            case,
            machine=entry["machine"],
            work_root=work_root,
            expected_machines=expected_machines,
            protected_repo=protected_repo,
            nonprotected_repo=nonprotected_repo,
            cwd=cwd,
            timeout=timeout,
            dry_run=dry_run,
            ci_harness_root=ci_harness_root,
        )
        return _attach_plan_fields(result, entry)

    for wave in waves:
        entries = [item for item in plan if item["concurrency_wave"] == wave]
        if len(entries) == 1:
            entry = entries[0]
            try:
                ordered[entry["schedule_order"]] = run_plan_entry(entry)
            except Exception as exc:  # noqa: BLE001
                case = cases[int(entry["case_index"])]
                ordered[entry["schedule_order"]] = _attach_plan_fields(finalize_case_result({
                    "ok": False,
                    "status": "failed",
                    "failure_reason": str(exc),
                }, case_id=case.id, name=case.name), entry)
            continue

        max_workers = max(1, min(int(parallel_workers or 1), len(entries)))
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(run_plan_entry, entry): entry for entry in entries}
            for future in concurrent.futures.as_completed(future_map):
                entry = future_map[future]
                try:
                    ordered[entry["schedule_order"]] = future.result()
                except Exception as exc:  # noqa: BLE001
                    case = cases[int(entry["case_index"])]
                    ordered[entry["schedule_order"]] = _attach_plan_fields(finalize_case_result({
                        "ok": False,
                        "status": "failed",
                        "failure_reason": str(exc),
                    }, case_id=case.id, name=case.name), entry)
    return [item for item in ordered if item is not None]


def run_f_remote_cases(
    *,
    cases: list[CaseDefinition],
    machines: list[dict[str, Any]],
    work_root: str,
    expected_machines: int,
    protected_repo: str,
    nonprotected_repo: str,
    cwd: Path,
    timeout: int,
    parallel_workers: int,
    dry_run: bool,
    ci_harness_root: str = "",
) -> list[dict[str, Any]]:
    return run_remote_cases(
        cases=[case for case in cases if is_f_case(case)],
        machines=machines,
        work_root=work_root,
        expected_machines=expected_machines,
        protected_repo=protected_repo,
        nonprotected_repo=nonprotected_repo,
        cwd=cwd,
        timeout=timeout,
        parallel_workers=parallel_workers,
        dry_run=dry_run,
        ci_harness_root=ci_harness_root,
    )
