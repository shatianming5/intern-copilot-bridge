from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CLI_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = CLI_ROOT.parent
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

from CI.helpers import deployment_primitives as full_primitives  # noqa: E402
from CI.cases.base import CaseDefinition
from CI.cases.registry import load_case_sets, load_cases, validate_case_set_promotion_policy, validate_registry_tree
from CI.machines import DEFAULT_DEBUG_WORK_ROOT, debug_machines
from CI.runner.reporting import (
    finalize_case_result,
    now,
    summarize_steps,
    write_json,
)
from CI.cases.selector import select_cases
from CI.runner.planner import create_planner_report
from CI.runner.scheduler import _case_resources, _declared_scenarios, plan_remote_cases
from CI.runner.stage_1_unit_test import run_unit_stage
from CI.runner.stage_2_package_deploy import (
    bootstrap_remote_services,
    deploy_remote_package,
    make_remote_payloads,
    reset_remote_ci_state,
    resolve_claude_access_token,
    resolve_claude_base_url,
    resolve_codex_lb_api_key,
    resolve_codex_lb_base_url,
    run_feishu_shared_cleanup,
    run_package_stage,
    run_shared_repo_cleanup,
    stage_existing_ci_harness,
)
from CI.runner.stage_3_F import (
    F_CLAUDE_TREEVIEW_SKILL_GROUP_REMOTE_KINDS,
    F_INTERN_SESSION_KINDS,
    F_POLICY_RECONNECT_REMOTE_KINDS,
    F_SKILL_CONFIG_TREEVIEW_REMOTE_KINDS,
    F_TASK_TREEVIEW_REMOTE_KINDS,
    F_TRANSPORT_APP_KINDS,
    F_WORKSPACE_GUI_KIND,
    F_WORKSPACE_TREEVIEW_REMOTE_KINDS,
    run_f_remote_cases,
)
from CI.runner.stage_4_J import run_j_remote_cases
from CI.runner.stage_0_preflight import validate_stage_preflight


SCHEMA = "intern-agents.ci-report.v1"

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


def _step(name: str, title: str, result: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": name,
        "title": title,
        "ok": False,
        "status": "pending",
    }
    if result:
        body.update(result)
    body.update(extra)
    if body["status"] == "pending":
        body["status"] = "passed" if body.get("ok") else "failed"
    return body


def _artifact_dir(report_path: Path) -> Path:
    return report_path.parent


def run_light_ci(
    *,
    report_path: Path,
    run_id: str,
    repo_root: Path = REPO_ROOT,
    parallel_workers: int = 3,
    command_timeout: int = 3600,
    dry_run: bool = False,
) -> dict[str, Any]:
    artifact_dir = _artifact_dir(report_path)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "entrypoint": "intern-cli/CI/run_light_ci.py",
        "machines_source": "",
        "repo_root": str(repo_root),
        "report_dir": str(artifact_dir),
        "started_at": now(),
        "light": {},
        "remote": {},
        "steps": [],
        "ok": False,
    }
    unit = run_unit_stage(root=repo_root, artifact_dir=artifact_dir, timeout=command_timeout, parallel_workers=parallel_workers, dry_run=dry_run)
    report["steps"].append(_step("unit", "Run PR unit test gate", unit))
    package = run_package_stage(root=repo_root, artifact_dir=artifact_dir, timeout=command_timeout, dry_run=dry_run)
    report["steps"].append(_step("package", "Build package gate", package))
    report["light"] = {"unit": unit, "package": package}
    report["summary"] = summarize_steps(report["steps"])
    report["ok"] = report["summary"]["failed"] == 0 and report["summary"]["skipped"] == 0
    report["finished_at"] = now()
    write_json(report_path, report)
    return report


def _run_stage_remote_cases(
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
    f_results = run_f_remote_cases(
        cases=cases,
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
    j_results = run_j_remote_cases(
        cases=cases,
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
    offset = len(f_results)
    for result in j_results:
        if "schedule_order" in result:
            result["schedule_order"] = int(result["schedule_order"]) + offset
    return f_results + j_results


def run_debug_ci(
    *,
    report_path: Path,
    run_id: str,
    cases: list[CaseDefinition],
    repo_root: Path = REPO_ROOT,
    parallel_workers: int = 3,
    command_timeout: int = 3600,
    dry_run: bool = False,
    protected_repo: str = full_primitives.DEFAULT_PROTECTED_REPO,
    nonprotected_repo: str = full_primitives.DEFAULT_NONPROTECTED_REPO,
    remote_work_root: str = DEFAULT_DEBUG_WORK_ROOT,
    feishu_app_id: str = "",
    feishu_app_secret: str = "",
    owner_mobile: str = "",
    codeup_token_env: str = "CODEUP_ACCESS_TOKEN",
    codeup_ssh_key: str = "",
    stop_after: str = "",
    use_existing_deployment: bool = False,
    emit_conflict_graph: bool = False,
) -> dict[str, Any]:
    artifact_dir = _artifact_dir(report_path)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    prefix = run_id
    remote_cases = list(cases)
    preflight = validate_stage_preflight(cases)
    machines = [] if not preflight.get("ok") else debug_machines()
    remote_case_plan = [] if not preflight.get("ok") else plan_remote_cases(cases=remote_cases, machines=machines, parallel_workers=parallel_workers)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "run_id": run_id,
        "revision": _git_revision(repo_root),
        "entrypoint": "intern-cli/CI/run_ci.py",
        "machines_source": "debug",
        "report_dir": str(artifact_dir),
        "repo_root": str(repo_root),
        "started_at": now(),
        "selected_cases": [case.to_registry_entry() for case in cases],
        "preflight": preflight,
        "planner": {},
        "stop_after": stop_after,
        "use_existing_deployment": use_existing_deployment,
        "steps": [],
        "light": {},
        "local": {"case_plan": [], "cases": []},
        "shared_cleanup": {},
        "remote": {
            "machines": machines,
            "case_plan": remote_case_plan,
            "deploy": {},
            "bootstrap": {},
            "cases": [],
        },
        "ok": False,
    }

    if not preflight.get("ok"):
        report["steps"].append(_step("case_stage_preflight", "Validate CI case F/J stage contracts", preflight))
        return _finish_report(report, report_path)

    if emit_conflict_graph:
        planner = create_planner_report(cases, artifact_dir / "planner")
        report["planner"] = planner
        report["steps"].append(_step("resource_lock_planner", "Build F/J resource-lock conflict graph", planner))
        if not planner.get("ok"):
            return _finish_report(report, report_path)

    if use_existing_deployment:
        ci_harness = stage_existing_ci_harness(
            machines=machines,
            repo_root=repo_root,
            artifact_dir=artifact_dir,
            work_root=remote_work_root,
            run_id=run_id,
            timeout=command_timeout,
            dry_run=dry_run,
        )
        report["remote"]["ci_harness"] = ci_harness
        report["steps"].append(_step("ci_harness_sync", "Sync current CI harness to debug machines", ci_harness))
        if not ci_harness.get("ok") and not dry_run:
            return _finish_report(report, report_path)
        case_results = _run_stage_remote_cases(
            cases=cases,
            machines=machines,
            work_root=remote_work_root,
            expected_machines=len(machines),
            protected_repo=protected_repo,
            nonprotected_repo=nonprotected_repo,
            cwd=repo_root,
            timeout=command_timeout,
            parallel_workers=parallel_workers,
            dry_run=dry_run,
            ci_harness_root=str(ci_harness.get("harness_root") or ""),
        )
        remote_ok = bool(case_results) and all(item.get("ok") for item in case_results)
        report["remote"]["cases"] = case_results
        report["steps"].append(_step("remote_cases", "Run debug remote cases on existing deployment", {
            "ok": remote_ok,
            "status": "passed" if remote_ok else ("skipped" if dry_run else "failed"),
            "cases": case_results,
            "failure_reason": "" if remote_ok else ("dry run" if dry_run else "; ".join(
                item.get("failure_reason", item.get("case_id", "case failed"))
                for item in case_results
                if not item.get("ok")
            )),
        }))
        return _finish_report(report, report_path)

    app_id = full_primitives.resolve_feishu_app_id(feishu_app_id)
    app_secret = full_primitives.resolve_feishu_app_secret(feishu_app_secret)

    unit = run_unit_stage(root=repo_root, artifact_dir=artifact_dir, timeout=command_timeout, parallel_workers=parallel_workers, dry_run=dry_run)
    report["steps"].append(_step("unit", "Run PR unit test gate", unit))
    report["light"] = {"unit": unit}
    if not unit.get("ok") and not dry_run:
        for name, title in [
            ("package", "Build VSIX package"),
            ("feishu_cleanup", "Clean Feishu test app chats once"),
            ("remote_state_reset", "Reset debug CI namespace before deploying"),
            ("remote_deploy", "Deploy package to debug machines"),
            ("remote_bootstrap", "Start debug relay and daemons"),
            ("remote_cases", "Run debug remote cases"),
        ]:
            report["steps"].append(_step(name, title, {"ok": False, "status": "skipped", "failure_reason": "unit stage failed"}))
        return _finish_report(report, report_path)

    package = run_package_stage(root=repo_root, artifact_dir=artifact_dir, timeout=command_timeout, dry_run=dry_run)
    report["steps"].append(_step("package", "Build VSIX package", package))
    report["light"] = {"unit": unit, "package": package}

    if not package.get("ok") and not dry_run:
        report["steps"].append(_step("feishu_cleanup", "Clean Feishu test app chats once", {"ok": False, "status": "skipped", "failure_reason": "package stage failed"}))
        report["steps"].append(_step("remote_state_reset", "Reset debug CI namespace before deploying", {"ok": False, "status": "skipped", "failure_reason": "package stage failed"}))
        report["steps"].append(_step("remote_deploy", "Deploy package to debug machines", {"ok": False, "status": "skipped", "failure_reason": "package stage failed"}))
        report["steps"].append(_step("remote_bootstrap", "Start debug relay and daemons", {"ok": False, "status": "skipped", "failure_reason": "package stage failed"}))
        report["steps"].append(_step("remote_cases", "Run debug remote cases", {"ok": False, "status": "skipped", "failure_reason": "package stage failed"}))
        return _finish_report(report, report_path)

    feishu_cleanup = run_feishu_shared_cleanup(
        root=repo_root,
        artifact_dir=artifact_dir,
        app_id=app_id,
        app_secret=app_secret,
        timeout=command_timeout,
        dry_run=dry_run,
    )
    report["shared_cleanup"]["feishu"] = feishu_cleanup
    report["steps"].append(_step("feishu_cleanup", "Clean Feishu test app chats once", feishu_cleanup))
    if not feishu_cleanup.get("ok") and not dry_run:
        report["steps"].append(_step("remote_state_reset", "Reset debug CI namespace before deploying", {"ok": False, "status": "skipped", "failure_reason": "Feishu cleanup failed"}))
        report["steps"].append(_step("remote_deploy", "Deploy package to debug machines", {"ok": False, "status": "skipped", "failure_reason": "Feishu cleanup failed"}))
        report["steps"].append(_step("remote_bootstrap", "Start debug relay and daemons", {"ok": False, "status": "skipped", "failure_reason": "Feishu cleanup failed"}))
        report["steps"].append(_step("remote_cases", "Run debug remote cases", {"ok": False, "status": "skipped", "failure_reason": "Feishu cleanup failed"}))
        return _finish_report(report, report_path)

    if dry_run:
        report["steps"].append(_step("remote_state_reset", "Reset debug CI namespace before deploying", {"ok": False, "status": "skipped", "failure_reason": "dry run"}))
        report["steps"].append(_step("remote_deploy", "Deploy package to debug machines", {"ok": False, "status": "skipped", "failure_reason": "dry run"}))
        report["steps"].append(_step("remote_bootstrap", "Start debug relay and daemons", {"ok": False, "status": "skipped", "failure_reason": "dry run"}))
        case_results = _run_stage_remote_cases(
            cases=cases,
            machines=machines,
            work_root=remote_work_root,
            expected_machines=len(machines),
            protected_repo=protected_repo,
            nonprotected_repo=nonprotected_repo,
            cwd=repo_root,
            timeout=command_timeout,
            parallel_workers=parallel_workers,
            dry_run=True,
        )
        report["remote"]["cases"] = case_results
        report["steps"].append(_step("remote_cases", "Run debug remote cases", {"ok": False, "status": "skipped", "cases": case_results, "failure_reason": "dry run"}))
        return _finish_report(report, report_path)

    codeup_token = os.environ.get(codeup_token_env) or ""
    codex_lb_base_url = resolve_codex_lb_base_url()
    codex_lb_api_key = resolve_codex_lb_api_key()
    claude_access_token = resolve_claude_access_token()
    claude_base_url = resolve_claude_base_url()
    relay_host = full_primitives.machine_relay_host(machines[0])
    try:
        resolved_codeup_ssh_key = full_primitives.resolve_codeup_ssh_key(Path.home(), codeup_ssh_key)
        payloads = make_remote_payloads(
            artifact_dir=artifact_dir,
            vsix_path=Path(package["vsix"]),
            prefix=prefix,
            relay_host=relay_host,
            feishu_app_id=app_id,
            feishu_app_secret=app_secret,
            owner_mobile=owner_mobile or os.environ.get("ENTERPRISE_CI_OWNER_MOBILE") or full_primitives.DEFAULT_ENTERPRISE_CI_OWNER_MOBILE,
            codeup_token=codeup_token,
            codeup_ssh_key=resolved_codeup_ssh_key,
            codex_lb_base_url=codex_lb_base_url,
            codex_lb_api_key=codex_lb_api_key,
            claude_access_token=claude_access_token,
            claude_base_url=claude_base_url,
        )
    except Exception as exc:  # noqa: BLE001
        report["steps"].append(_step("remote_payload", "Build remote payloads", {"ok": False, "status": "failed", "failure_reason": str(exc)}))
        return _finish_report(report, report_path)

    remote_state_reset = reset_remote_ci_state(
        machines=machines,
        work_root=remote_work_root,
        cwd=repo_root,
        timeout=command_timeout,
        dry_run=dry_run,
    )
    report["steps"].append(_step("remote_state_reset", "Reset debug CI namespace before deploying", remote_state_reset))
    if not remote_state_reset.get("ok") and not dry_run:
        return _finish_report(report, report_path)

    remote_deploy = deploy_remote_package(
        machines=machines,
        payloads=payloads,
        work_root=remote_work_root,
        cwd=repo_root,
        timeout=command_timeout,
        dry_run=dry_run,
    )
    report["remote"]["deploy"] = {"machines": remote_deploy.get("machines", [])}
    report["steps"].append(_step("remote_deploy", "Deploy package to debug machines", remote_deploy))
    if not remote_deploy.get("ok") and not dry_run:
        return _finish_report(report, report_path)

    repo_cleanup = run_shared_repo_cleanup(
        cases=cases,
        machine=machines[0],
        work_root=remote_work_root,
        protected_repo=protected_repo,
        nonprotected_repo=nonprotected_repo,
        cwd=repo_root,
        timeout=command_timeout,
        dry_run=dry_run,
    )
    report["shared_cleanup"]["repos"] = repo_cleanup
    report["steps"].append(_step("remote_shared_cleanup", "Clean shared repos and open PRs once", repo_cleanup))
    if not repo_cleanup.get("ok") and not dry_run:
        return _finish_report(report, report_path)

    bootstrap = bootstrap_remote_services(
        machines=machines,
        payloads=payloads,
        work_root=remote_work_root,
        cwd=repo_root,
        timeout=command_timeout,
        dry_run=dry_run,
        codex_lb_base_url=codex_lb_base_url,
        claude_base_url=claude_base_url,
    )
    report["remote"]["bootstrap"] = bootstrap
    report["steps"].append(_step("remote_bootstrap", "Start debug relay and daemons", bootstrap))
    if not bootstrap.get("ok") and not dry_run:
        return _finish_report(report, report_path)
    if stop_after == "remote_bootstrap":
        return _finish_report(report, report_path)

    case_results = _run_stage_remote_cases(
        cases=cases,
        machines=machines,
        work_root=remote_work_root,
        expected_machines=len(machines),
        protected_repo=protected_repo,
        nonprotected_repo=nonprotected_repo,
        cwd=repo_root,
        timeout=command_timeout,
        parallel_workers=parallel_workers,
        dry_run=dry_run,
    )
    remote_ok = bool(case_results) and all(item.get("ok") for item in case_results)
    report["remote"]["cases"] = case_results
    report["steps"].append(_step("remote_cases", "Run debug remote cases", {
        "ok": remote_ok,
        "status": "passed" if remote_ok else "failed",
        "cases": case_results,
        "failure_reason": "" if remote_ok else "; ".join(
            item.get("failure_reason", item.get("case_id", "case failed"))
            for item in case_results
            if not item.get("ok")
        ),
    }))
    return _finish_report(report, report_path)


def _finish_report(report: dict[str, Any], report_path: Path) -> dict[str, Any]:
    report["summary"] = summarize_steps(report["steps"])
    report["ok"] = report["summary"]["failed"] == 0 and report["summary"]["skipped"] == 0 and report["summary"]["passed"] > 0
    report["finished_at"] = now()
    write_json(report_path, report)
    return report


def _git_revision(root: Path) -> str:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root), capture_output=True, text=True, timeout=30)
    except Exception:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""
