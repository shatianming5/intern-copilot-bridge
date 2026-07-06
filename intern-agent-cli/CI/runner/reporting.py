from __future__ import annotations

import json
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from CI.assertions.core import native_require_check
from CI.helpers.native_error import NativeCaseError
from CI.helpers.reporting import (
    build_scenario_record,
    classify_remote_case_failure,
    redact_report_text,
    redact_report_value,
    scenario_display_name,
)


Runner = Callable[..., subprocess.CompletedProcess[str]]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def tail(text: str, limit: int = 8000) -> str:
    value = text or ""
    return value if len(value) <= limit else value[-limit:]


def _status_from_ok(*, status: str | None, ok: bool | None) -> str:
    normalized = (status or "").strip().lower()
    if normalized in {"passed", "failed", "skipped"}:
        return normalized
    if ok is True:
        return "passed"
    if ok is False:
        return "failed"
    return "skipped"


def normalize_scenarios(
    case_id: str,
    scenarios: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None,
    *,
    fallback_name: str = "",
    status: str | None = None,
    ok: bool | None = None,
) -> list[dict[str, Any]]:
    raw_scenarios = list(scenarios or [])
    if not raw_scenarios:
        scenario_status = _status_from_ok(status=status, ok=ok)
        entry = {
            "scenario_id": f"{case_id}.default",
            "name": fallback_name or "Default case result",
            "status": scenario_status,
            "ok": scenario_status == "passed",
        }
        if scenario_status in {"failed", "skipped"}:
            entry["failure_reason"] = status or scenario_status
        return [entry]

    normalized: list[dict[str, Any]] = []
    for item in raw_scenarios:
        if not isinstance(item, dict):
            raise TypeError("scenario entries must be dictionaries")
        scenario_id = item.get("scenario_id") or item.get("id")
        if not scenario_id:
            raise ValueError("scenario entry missing scenario_id")
        scenario_status = _status_from_ok(status=item.get("status"), ok=item.get("ok"))
        entry: dict[str, Any] = {
            "scenario_id": str(scenario_id),
            "name": str(item.get("name") or scenario_id),
            "status": scenario_status,
            "ok": scenario_status == "passed",
        }
        for key in ("failure_reason", "skip_reason", "details", "artifacts"):
            if key in item:
                entry[key] = item[key]
        normalized.append(entry)
    return normalized


def summarize_scenarios(scenarios: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(scenarios),
        "passed": sum(1 for item in scenarios if item.get("status") == "passed"),
        "failed": sum(1 for item in scenarios if item.get("status") == "failed"),
        "skipped": sum(1 for item in scenarios if item.get("status") == "skipped"),
    }


def failure_index(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [str(item.get("scenario_id")) for item in scenarios if item.get("status") == "failed"]
    return {
        "count": len(failed),
        "scenario_ids": failed,
    }


def finalize_case_result(result: dict[str, Any], *, case_id: str, name: str) -> dict[str, Any]:
    finalized = dict(result)
    scenarios = normalize_scenarios(
        case_id,
        finalized.get("scenarios"),
        fallback_name=name,
        status=finalized.get("status"),
        ok=finalized.get("ok"),
    )
    summary = summarize_scenarios(scenarios)
    failures = failure_index(scenarios)
    finalized["case_id"] = case_id
    finalized["name"] = name
    finalized["scenarios"] = scenarios
    finalized["scenario_summary"] = summary
    if failures["count"]:
        finalized["failure_index"] = failures
        finalized["ok"] = False
        finalized["status"] = "failed"
        if not finalized.get("failure_reason"):
            finalized["failure_reason"] = "failed scenarios: " + ", ".join(failures["scenario_ids"])
    elif str(finalized.get("status") or "").strip().lower() == "failed" or (
        finalized.get("ok") is False
        and str(finalized.get("status") or "").strip().lower() != "skipped"
    ):
        finalized["ok"] = False
        finalized["status"] = "failed"
        if not finalized.get("failure_reason"):
            finalized["failure_reason"] = "case result failed"
    elif summary["skipped"] and summary["skipped"] == summary["total"]:
        finalized["ok"] = False
        finalized["status"] = "skipped"
    else:
        finalized["ok"] = True
        finalized["status"] = "passed" if finalized["ok"] else _status_from_ok(
            status=finalized.get("status"),
            ok=finalized.get("ok"),
        )
    return finalized


def command_artifact(command_result: dict[str, Any], *, limit: int = 8000) -> dict[str, Any]:
    return {
        "cmd": command_result.get("cmd", ""),
        "cwd": command_result.get("cwd", ""),
        "returncode": command_result.get("returncode"),
        "stdout_tail": tail(str(command_result.get("stdout") or ""), limit=limit),
        "stderr_tail": tail(str(command_result.get("stderr") or ""), limit=limit),
    }


def normalize_artifact_paths(paths: list[str | Path] | tuple[str | Path, ...], *, base_dir: Path | None = None) -> list[dict[str, str]]:
    artifacts: list[dict[str, str]] = []
    base = base_dir.resolve() if base_dir else None
    for value in paths:
        raw = Path(value)
        absolute = raw if raw.is_absolute() else ((base or Path.cwd()) / raw)
        absolute = absolute.resolve()
        if base:
            try:
                relative = absolute.relative_to(base)
            except ValueError:
                relative = raw
        else:
            relative = raw
        artifacts.append({
            "relative_path": str(relative),
            "absolute_path": str(absolute),
        })
    return artifacts


def run_command(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    runner: Runner = subprocess.run,
    dry_run: bool = False,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "cmd": " ".join(shlex.quote(part) for part in cmd),
        "cwd": str(cwd),
        "ok": False,
        "status": "skipped" if dry_run else "running",
        "returncode": None,
        "stdout": "",
        "stderr": "",
        "duration_seconds": 0.0,
    }
    if dry_run:
        entry["failure_reason"] = "dry run"
        return entry
    started = time.time()
    kwargs: dict[str, Any] = {
        "cwd": str(cwd),
        "capture_output": True,
        "text": True,
        "timeout": timeout,
    }
    if env is not None:
        kwargs["env"] = env
    result = runner(cmd, **kwargs)
    entry.update({
        "duration_seconds": round(time.time() - started, 3),
        "returncode": result.returncode,
        "stdout": tail(result.stdout),
        "stderr": tail(result.stderr),
        "ok": result.returncode == 0,
        "status": "passed" if result.returncode == 0 else "failed",
    })
    if result.returncode != 0:
        entry["failure_reason"] = f"command exited with rc={result.returncode}"
    return entry


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def resolve_report_path(raw: str, *, run_id: str) -> Path:
    if raw:
        path = Path(raw).expanduser().resolve()
        if raw.endswith("/") or path.suffix == "":
            return path / "report.json"
        return path
    return Path("/tmp/intern_agent_CI") / run_id / "report.json"


def summarize_steps(steps: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "passed": sum(1 for step in steps if step.get("status") == "passed"),
        "failed": sum(1 for step in steps if step.get("status") == "failed"),
        "skipped": sum(1 for step in steps if step.get("status") == "skipped"),
    }


class RemoteCaseLifecycleMixin:
    """Remote case lifecycle and report behavior used by the remote entrypoint."""

    def _record_contract_scenario(
        self,
        scenario_id: str,
        ok: bool,
        *,
        details: dict[str, Any] | None = None,
        failure_reason: str = "",
        classification: str = "",
    ) -> None:
        self._record_scenario(
            scenario_id,
            status="passed" if ok else "failed",
            started=time.time(),
            details=details or {},
            reason="" if ok else failure_reason,
            classification="" if ok else classification,
        )
        if not ok and classification:
            self.failure_classification = classification
            self.artifacts["failure_classification"] = classification

    def require(self, name: str, condition: bool, detail: dict[str, Any] | None = None) -> None:
        check = native_require_check(name, condition, detail)
        self.checks.append(check)
        if not condition:
            raise NativeCaseError(str(check["failure_reason"]))

    def require_checks(self, result: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any] | list[dict[str, Any]]:
        checks = result.get("checks", []) if isinstance(result, dict) else result
        for check in checks:
            self.require(str(check["name"]), bool(check["ok"]), check.get("detail") if isinstance(check.get("detail"), dict) else {})
        return result

    def require_classified_checks(self, result: dict[str, Any] | list[dict[str, Any]]) -> dict[str, Any] | list[dict[str, Any]]:
        checks = result.get("checks", []) if isinstance(result, dict) else result
        for check in checks:
            self.require_classified_contract(
                str(check["name"]),
                bool(check["ok"]),
                str(check.get("classification") or "ci_assertion_or_product_bug"),
                check.get("detail") if isinstance(check.get("detail"), dict) else {},
            )
        return result

    def classify_failure(self, exc: BaseException) -> str:
        return classify_remote_case_failure(exc)

    def scenario_name(self, scenario_id: str) -> str:
        return scenario_display_name(scenario_id)

    def scenario_context(self) -> dict[str, Any]:
        return {
            "resource_namespace": self.resource_namespace,
            "machine_id": self.machine_id(),
            "artifact_dir": str(self.artifact_dir),
        }

    def _record_scenario(
        self,
        scenario_id: str,
        *,
        status: str,
        started: float,
        details: dict[str, Any] | None = None,
        reason: str = "",
        classification: str = "",
    ) -> None:
        self.scenarios.append(build_scenario_record(
            scenario_id,
            status=status,
            duration_seconds=time.time() - started,
            context=self.scenario_context(),
            details=details,
            reason=reason,
            classification=classification,
        ))

    def run_ordered_scenarios(self, scenarios: list[tuple[str, Callable[[], dict[str, Any] | None]]]) -> None:
        first_error: BaseException | None = None
        first_failed = ""
        for scenario_id, action in scenarios:
            started = time.time()
            if first_error is not None:
                self._record_scenario(
                    scenario_id,
                    status="skipped",
                    started=started,
                    reason=f"blocked by prior failure: {first_failed}",
                    details={"blocked_by": first_failed},
                )
                continue
            try:
                details = action() or {}
                self._record_scenario(scenario_id, status="passed", started=started, details=details)
            except Exception as exc:  # noqa: BLE001
                classification = self.classify_failure(exc)
                self.failure_classification = classification
                self.artifacts["failure_classification"] = classification
                failure_details = getattr(exc, "details", {})
                if not isinstance(failure_details, dict):
                    failure_details = {}
                self._record_scenario(
                    scenario_id,
                    status="failed",
                    started=started,
                    reason=str(exc),
                    details=failure_details,
                    classification=classification,
                )
                first_error = exc
                first_failed = scenario_id
        if first_error is not None:
            raise NativeCaseError(f"{self.case_id} failed at {first_failed}: {first_error}") from first_error

    def scenario_summary(self) -> dict[str, int]:
        return self.ctx.action.reporting.scenario_summary(self.scenarios)

    def failure_index(self) -> dict[str, Any]:
        return self.ctx.action.reporting.failure_index(self.scenarios)

    def require_classified_contract(
        self,
        name: str,
        condition: bool,
        classification: str,
        detail: dict[str, Any] | None = None,
    ) -> None:
        evidence = detail or {}
        self.checks.append({
            "name": name,
            "ok": bool(condition),
            "detail": evidence,
            "failure_classification": "" if condition else classification,
        })
        if not condition:
            raise NativeCaseError(
                f"{classification}: {name}: {json.dumps(redact_report_value(evidence), ensure_ascii=False)[:1200]}",
                details={"failure_classification": classification, "evidence": evidence},
            )

    def require_product_bug_evidence(self, name: str, condition: bool, detail: dict[str, Any] | None = None) -> None:
        check = {"name": name, "ok": bool(condition), "detail": detail or {}, "failure_classification": "product_bug"}
        self.checks.append(check)
        if not condition:
            evidence = detail or {}
            failure_details = {"product_bug_evidence": evidence}
            if "finding_summaries" in evidence:
                failure_details["product_bug_finding_summaries"] = evidence["finding_summaries"]
            raise NativeCaseError(
                f"product bug: {name}: {json.dumps(evidence, ensure_ascii=False)[:1200]}",
                details=failure_details,
            )

    def collect_product_bug_evidence(
        self,
        state: dict[str, Any],
        name: str,
        condition: bool,
        *,
        expected: str,
        actual: str,
        detail: dict[str, Any],
        handler_evidence: dict[str, Any],
    ) -> dict[str, Any]:
        evidence = {
            "name": name,
            "ok": bool(condition),
            "expected_behavior": expected,
            "actual_behavior": actual,
            "detail": detail,
            "handler_evidence": handler_evidence,
            "failure_classification": "product_bug",
        }
        self.checks.append({
            "name": name,
            "ok": bool(condition),
            "detail": evidence,
            "failure_classification": "product_bug" if not condition else "",
        })
        if not condition:
            finding = dict(evidence)
            state.setdefault("product_bug_findings", []).append(finding)
            self.artifacts.setdefault("product_bug_findings", []).append(finding)
        return evidence

    def aggregate_product_bug_findings(self, state: dict[str, Any], name: str) -> dict[str, Any]:
        findings = list(state.get("product_bug_findings") or [])
        detail = self.ctx.action.reporting.product_bug_aggregate(findings)
        self.require_product_bug_evidence(name, not findings, detail)
        return detail

    def report(self, *, ok: bool, error: str = "") -> dict[str, Any]:
        failure_index_data = self.failure_index()
        return {
            "schema": "intern-agents.ci-native-remote-case.v1",
            "case_id": self.case_id,
            "ok": ok,
            "status": "passed" if ok else "failed",
            "started_at": self.args.started_at,
            "finished_at": now(),
            "work_root": str(self.work_root),
            "repo_root": str(self.repo_root),
            "artifact_dir": str(self.artifact_dir),
            "resource_namespace": self.resource_namespace,
            "machine_id": self.machine_id(),
            "steps": self.ctx.action.reporting.redact_value(self.steps)["redacted"],
            "checks": self.ctx.action.reporting.redact_value(self.checks)["redacted"],
            "scenarios": self.ctx.action.reporting.redact_value(self.scenarios)["redacted"],
            "scenario_summary": self.scenario_summary(),
            "failure_index": self.ctx.action.reporting.redact_value(failure_index_data)["redacted"],
            "failure_classification": redact_report_text(self.failure_classification),
            "artifacts": self.ctx.action.reporting.redact_value(self.artifacts)["redacted"],
            "created": self.ctx.action.reporting.redact_value(self.created)["redacted"],
            "failure_reason": redact_report_text(error),
        }

    def write_report(self, data: dict[str, Any]) -> None:
        self.file_artifacts.write_report(self.report_path, data)
