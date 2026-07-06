#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[1]
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

from CI.helpers import deployment_primitives as full_primitives
from CI.actions.registry import load_action_definitions
from CI.assertions.registry import load_assertion_definitions
from CI.cases.audit import audit_action_assertion_contracts
from CI.cases.registry import load_case_sets, load_cases
from CI.runner.reporting import resolve_report_path, write_json
from CI.runner.runner import REPO_ROOT, run_debug_ci
from CI.cases.selector import select_cases


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full CI on debug or new machines.")
    parser.add_argument(
        "--machines",
        choices=["debug", "new"],
        default="debug",
        help="debug uses the hard-coded two-machine pool; new is reserved for the future machine service.",
    )
    parser.add_argument("--case", action="append", default=[], help="Case id/name to run. Use --case full for the full enabled set on debug machines.")
    parser.add_argument("--case-list", default="", help="JSON file or inline JSON list/object containing case ids or names.")
    parser.add_argument(
        "--case-set",
        default="",
        help=(
            "Named set derived from active F/J CASE metadata, for example "
            "F, J, core, full, or native."
        ),
    )
    parser.add_argument("--list-cases", action="store_true", help="List registered CI cases and exit.")
    parser.add_argument("--list-actions", action="store_true", help="List registered CI actions and exit.")
    parser.add_argument("--list-assertions", action="store_true", help="List registered CI assertions and exit.")
    parser.add_argument("--list-case-sets", action="store_true", help="List derived CI case sets and exit.")
    parser.add_argument("--audit-registry", action="store_true", help="Audit active F/J case action/assertion references and exit.")
    parser.add_argument("--details", action="store_true", help="Show description, parameters, resources, notes and related metadata for list commands.")
    parser.add_argument("--fields", default="", help="Comma-separated output fields for list commands, for example id,description,parameters,notes.")
    parser.add_argument("--json", action="store_true", help="Emit JSON for list commands.")
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=3,
        help="Maximum number of remote cases to run concurrently after the setup gate.",
    )
    parser.add_argument("--command-timeout", type=int, default=3600, help="Per local or remote command timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Resolve case selection and report shape without touching debug machines.")
    parser.add_argument(
        "--emit-conflict-graph",
        action="store_true",
        help="Emit strict F/J resource-lock planner artifacts: plan.json, conflict graph DOT/Mermaid/JSON, and schedule waves.",
    )
    parser.add_argument(
        "--use-existing-deployment",
        action="store_true",
        help=(
            "Run selected remote cases against the already deployed debug daemon/relay. "
            "Skips unit, package, Feishu cleanup, remote reset, deploy, repo cleanup, and bootstrap; "
            "syncs only the current CI harness to the debug machines."
        ),
    )
    parser.add_argument(
        "--stop-after",
        choices=["remote_bootstrap"],
        default="",
        help="Stop after a named stage succeeds; used to verify deploy/bootstrap without running remote cases.",
    )
    parser.add_argument("--report", default="", help="Report path; defaults to /tmp/intern_agent_CI/<run_id>/report.json.")
    parser.add_argument("--run-id", default="", help="Run id used in the default report directory and remote artifact paths.")
    parser.add_argument("--repo-root", default="")
    parser.add_argument("--protected-repo", default=full_primitives.DEFAULT_PROTECTED_REPO)
    parser.add_argument("--nonprotected-repo", default=full_primitives.DEFAULT_NONPROTECTED_REPO)
    parser.add_argument("--remote-work-root", default="")
    parser.add_argument("--feishu-app-id", default="")
    parser.add_argument("--feishu-app-secret", default="")
    parser.add_argument("--owner-mobile", default=os.environ.get("ENTERPRISE_CI_OWNER_MOBILE") or full_primitives.DEFAULT_ENTERPRISE_CI_OWNER_MOBILE)
    parser.add_argument("--codeup-token-env", default="CODEUP_ACCESS_TOKEN")
    parser.add_argument(
        "--codeup-ssh-key",
        default=os.environ.get("CODEUP_SSH_KEY", ""),
        help="Explicit Codeup SSH private key. Defaults to the codeup.aliyun.com IdentityFile from ~/.ssh/config.",
    )
    return parser.parse_args(argv)


def _case_sets_by_id(case_sets: dict[str, list[str]]) -> dict[str, list[str]]:
    by_id: dict[str, list[str]] = {}
    for set_name, case_ids in case_sets.items():
        for case_id in case_ids:
            by_id.setdefault(case_id, []).append(set_name)
    return by_id


def _selected_fields(
    *,
    explicit: str,
    default: tuple[str, ...],
    details: tuple[str, ...],
    show_details: bool,
) -> tuple[str, ...]:
    if explicit.strip():
        return tuple(item.strip() for item in explicit.split(",") if item.strip())
    return details if show_details else default


def _project_fields(rows: list[dict], fields: tuple[str, ...]) -> list[dict]:
    return [{field: row.get(field, "") for field in fields} for row in rows]


def _format_cell(value: object) -> str:
    if isinstance(value, (list, tuple)):
        if all(not isinstance(item, (dict, list, tuple)) for item in value):
            return ",".join(str(item) for item in value)
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _print_table(rows: list[dict], fields: tuple[str, ...]) -> None:
    print("\t".join(fields))
    for row in rows:
        print("\t".join(_format_cell(row.get(field, "")) for field in fields))


def _emit_rows(*, key: str, rows: list[dict], json_output: bool, fields: tuple[str, ...]) -> None:
    selected = _project_fields(rows, fields)
    if json_output:
        print(json.dumps({key: selected}, ensure_ascii=False, indent=2))
        return
    _print_table(selected, fields)


def _print_case_sets(*, json_output: bool, fields: str, details: bool) -> None:
    case_sets = load_case_sets()
    rows = [{"name": set_name, "count": len(case_ids), "case_ids": case_ids} for set_name, case_ids in case_sets.items()]
    output_fields = _selected_fields(
        explicit=fields,
        default=("name", "count", "case_ids"),
        details=("name", "count", "case_ids"),
        show_details=details,
    )
    if json_output:
        if fields:
            print(json.dumps({"case_sets": _project_fields(rows, output_fields)}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps({"case_sets": case_sets}, ensure_ascii=False, indent=2))
        return
    _print_table(rows, output_fields)


def _print_cases(*, json_output: bool, fields: str, details: bool, include_disabled: bool = True) -> None:
    cases = load_cases(include_disabled=include_disabled)
    case_sets = load_case_sets(cases)
    sets_by_id = _case_sets_by_id(case_sets)
    rows = []
    for case in cases:
        entry = case.to_registry_entry()
        entry["case_sets"] = sets_by_id.get(case.id, [])
        rows.append(entry)
    output_fields = _selected_fields(
        explicit=fields,
        default=("id", "stage", "kind", "enabled", "case_sets", "description"),
        details=(
            "id",
            "name",
            "description",
            "stage",
            "ci_stage",
            "kind",
            "enabled",
            "case_sets",
            "actions",
            "lock_params",
            "resource_locks",
            "resources",
            "run_mode",
            "journey_steps",
            "assertions",
            "notes",
        ),
        show_details=details,
    )
    _emit_rows(key="cases", rows=rows, json_output=json_output, fields=output_fields)


def _print_actions(*, json_output: bool, fields: str, details: bool) -> None:
    rows = [item.to_dict() for item in load_action_definitions()]
    output_fields = _selected_fields(
        explicit=fields,
        default=("id", "kind", "category", "description"),
        details=("id", "title", "description", "kind", "category", "callable_path", "gui_command", "cli_equivalent", "parameters", "returns", "resource_locks", "resources", "notes"),
        show_details=details,
    )
    _emit_rows(key="actions", rows=rows, json_output=json_output, fields=output_fields)


def _print_assertions(*, json_output: bool, fields: str, details: bool) -> None:
    rows = [item.to_dict() for item in load_assertion_definitions()]
    output_fields = _selected_fields(
        explicit=fields,
        default=("id", "kind", "description"),
        details=("id", "title", "description", "kind", "callable_path", "parameters", "returns", "notes"),
        show_details=details,
    )
    _emit_rows(key="assertions", rows=rows, json_output=json_output, fields=output_fields)


def _print_registry_audit(*, json_output: bool) -> dict:
    report = audit_action_assertion_contracts()
    if json_output:
        print(json.dumps({"registry_audit": report}, ensure_ascii=False, indent=2))
    else:
        summary = report["summary"]
        print(
            "registry audit "
            f"{report['status']}: "
            f"active_fj_cases={summary['active_fj_cases']} "
            f"missing_actions={summary['missing_action_refs']} "
            f"missing_assertions={summary['missing_assertion_refs']} "
            f"legacy_actions={summary['legacy_action_refs']} "
            f"legacy_assertions={summary['legacy_assertion_refs']}"
        )
        if report["errors"]:
            print("errors=" + json.dumps(report["errors"], ensure_ascii=False), file=sys.stderr)
    return report


def _run_new_machine_unavailable(args: argparse.Namespace, *, report_path: Path, run_id: str, repo_root: Path) -> dict:
    # TODO: connect this branch to the machine-pool service once that service exists.
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "schema": "intern-agents.ci-report.v1",
        "run_id": run_id,
        "entrypoint": "intern-cli/CI/run_ci.py",
        "machines_source": "new",
        "report_dir": str(report_path.parent),
        "repo_root": str(repo_root),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "selected_cases": [],
        "steps": [{
            "name": "new_machine_pool",
            "title": "Allocate new debug-equivalent CI machines",
            "ok": False,
            "status": "failed",
            "failure_reason": "--machines new has no implementation in intern-cli/CI and did not allocate machines",
        }],
        "light": {},
        "remote": {},
    }
    report["summary"] = {"passed": 0, "failed": 1, "skipped": 0}
    report["ok"] = False
    report["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    write_json(report_path, report)
    return report


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    run_id = args.run_id or f"ci_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}"
    report_path = resolve_report_path(args.report, run_id=run_id)
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else REPO_ROOT
    if args.list_case_sets:
        _print_case_sets(json_output=args.json, fields=args.fields, details=args.details)
        return 0
    if args.list_cases:
        _print_cases(json_output=args.json, fields=args.fields, details=args.details)
        return 0
    if args.list_actions:
        _print_actions(json_output=args.json, fields=args.fields, details=args.details)
        return 0
    if args.list_assertions:
        _print_assertions(json_output=args.json, fields=args.fields, details=args.details)
        return 0
    if args.audit_registry:
        report = _print_registry_audit(json_output=args.json)
        return 0 if report.get("ok") else 1
    if args.machines == "new":
        report = _run_new_machine_unavailable(args, report_path=report_path, run_id=run_id, repo_root=repo_root)
    else:
        cases = select_cases(
            case_values=args.case,
            case_list=args.case_list,
            case_set=args.case_set,
            include_disabled=True,
        )
        report = run_debug_ci(
            report_path=report_path,
            run_id=run_id,
            cases=cases,
            repo_root=repo_root,
            parallel_workers=args.parallel_workers,
            command_timeout=args.command_timeout,
            dry_run=args.dry_run,
            protected_repo=args.protected_repo,
            nonprotected_repo=args.nonprotected_repo,
            remote_work_root=args.remote_work_root or "/root/axis_enterprise_ci",
            feishu_app_id=args.feishu_app_id,
            feishu_app_secret=args.feishu_app_secret,
            owner_mobile=args.owner_mobile,
            codeup_token_env=args.codeup_token_env,
            codeup_ssh_key=args.codeup_ssh_key,
            stop_after=args.stop_after,
            use_existing_deployment=args.use_existing_deployment,
            emit_conflict_graph=args.emit_conflict_graph,
        )
    summary = report.get("summary", {})
    print(
        "full CI "
        f"{'passed' if report.get('ok') else 'failed'}: "
        f"passed={summary.get('passed', 0)} failed={summary.get('failed', 0)} skipped={summary.get('skipped', 0)} "
        f"report={report_path}"
    )
    if not report.get("ok"):
        errors = []
        for step in report.get("steps", []):
            if step.get("status") in {"failed", "skipped"} and step.get("failure_reason"):
                errors.append(f"{step.get('name')}: {step.get('failure_reason')}")
        if errors:
            print("errors=" + json.dumps(errors, ensure_ascii=False), file=sys.stderr)
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
