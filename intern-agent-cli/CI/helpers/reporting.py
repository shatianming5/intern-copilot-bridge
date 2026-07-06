from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any


NATIVE_REPORT_SUMMARY_PREFIX = "__INTERN_CI_NATIVE_REPORT_SUMMARY__"
SENSITIVE_REPORT_KEYS = {"app_secret", "tenant_access_token", "app_ticket", "authorization"}
SENSITIVE_REPORT_TERMS = ("app_secret", "tenant_access_token", "app_ticket", "authorization")
BEARER_TOKEN_RE = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", flags=re.IGNORECASE)


def redact_report_text(value: str) -> str:
    redacted = BEARER_TOKEN_RE.sub("Bearer <redacted>", value)
    for term in SENSITIVE_REPORT_TERMS:
        redacted = re.sub(re.escape(term), "<redacted>", redacted, flags=re.IGNORECASE)
    return redacted


def redact_report_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            normalized = key_text.lower().replace("-", "_")
            if normalized in SENSITIVE_REPORT_KEYS:
                redacted[f"redacted_{len(redacted) + 1}"] = "<redacted>"
            else:
                redacted[redact_report_text(key_text)] = redact_report_value(item)
        return redacted
    if isinstance(value, list):
        return [redact_report_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_report_value(item) for item in value]
    if isinstance(value, Path):
        return redact_report_text(str(value))
    if isinstance(value, str):
        return redact_report_text(value)
    return value


def scenario_display_name(scenario_id: str) -> str:
    return scenario_id.rsplit(".", 1)[-1].replace("_", " ")


def scenario_summary(scenarios: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(scenarios),
        "passed": sum(1 for item in scenarios if item.get("status") == "passed"),
        "failed": sum(1 for item in scenarios if item.get("status") == "failed"),
        "skipped": sum(1 for item in scenarios if item.get("status") == "skipped"),
    }


def failure_index(scenarios: list[dict[str, Any]]) -> dict[str, Any]:
    failed = [str(item.get("scenario_id")) for item in scenarios if item.get("status") == "failed"]
    return {"count": len(failed), "scenario_ids": failed}


def classify_remote_case_failure(exc: BaseException) -> str:
    text = str(exc).lower()
    for classification in (
        "product_bug_restart_fresh_without_session_id",
        "product_bug_restart_missing_resume_session_id",
        "product_bug_restart_loses_session_id",
        "product_bug_exit_resume_hint_missing",
        "product_bug_exit_resume_hint_command_invalid",
        "product_bug_exit_resume_hint_loses_session_id",
        "product_bug_claude_create_type_drift",
        "product_bug_claude_group_type_drift",
        "product_bug_claude_session_not_live",
        "product_bug_claude_exit_resume_hint_missing",
        "product_bug_claude_exit_resume_hint_command_invalid",
        "product_bug_claude_exit_resume_hint_not_executable",
        "product_bug_claude_exit_resume_hint_loses_uuid",
        "product_bug_claude_restart_not_resume",
        "product_bug_claude_restart_loses_uuid",
        "ci_capability_gap_claude_token_policy",
        "ci_capability_gap_claude_runtime",
        "ci_capability_gap_claude_uuid_discovery",
    ):
        if classification in text:
            return classification
    for marker in (
        "product_bug_policy_sync_not_pulled",
        "product_bug_session_env_not_materialized",
        "product_bug_idle_codex_not_restarted",
        "product_bug_policy_replay_duplicate_restart",
        "product_bug_daemon_reconnect_not_registered",
        "product_bug_reconnect_workspace_registry_lost",
        "product_bug_reconnect_chat_lookup_lost",
        "product_bug_reconnect_policy_sync_missing",
    ):
        if marker in text:
            return marker
    if "ci_capability_gap_" in text:
        return "ci_capability_gap"
    if "assertion failed" in text:
        if "intern_removed" in text and "tmux" in text and '"returncode": 0' in text:
            return "product_bug_force_delete_tmux_residue"
        return "ci_assertion_or_product_bug"
    if "product bug" in text or "product_contract" in text:
        return "product_bug"
    if "environment_missing" in text or " is required" in text:
        return "environment_missing"
    if any(marker in text for marker in (
        "workspace_record_absent_",
        "no_extra_workspace_records_",
        "relay_has_no_bad_workspace_records",
        "workspace_attempt_failed_",
        "workspace removal timed out",
        "workspace_mode_change_rejected_",
        "workspace_provider_repo_display_unchanged",
        "business_branch_after",
    )):
        return "product_bug"
    if any(marker in text for marker in ("permission denied", "auth", "credential", "token", "ssh")):
        return "environment_auth_or_credentials"
    if any(marker in text for marker in ("connection refused", "relay", "daemon", "http", "websocket")):
        return "environment_relay_daemon"
    if any(marker in text for marker in ("timed out", "timeout")):
        return "environment_timeout"
    return "unknown_runtime_failure"


def build_scenario_record(
    scenario_id: str,
    *,
    status: str,
    duration_seconds: float,
    context: dict[str, Any],
    details: dict[str, Any] | None = None,
    reason: str = "",
    classification: str = "",
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "scenario_id": scenario_id,
        "name": scenario_display_name(scenario_id),
        "status": status,
        "ok": status == "passed",
        "duration_seconds": round(duration_seconds, 3),
        "details": {**context, **(details or {})},
    }
    if status == "failed":
        entry["failure_reason"] = reason
        entry["details"]["failure_classification"] = classification
    if status == "skipped":
        entry["skip_reason"] = reason
    return entry


def product_bug_aggregate_detail(findings: list[Any]) -> dict[str, Any]:
    finding_summaries = [
        {
            "name": item.get("name"),
            "expected_behavior": item.get("expected_behavior"),
            "actual_behavior": item.get("actual_behavior"),
            "failure_classification": item.get("failure_classification"),
            "handler_evidence": item.get("handler_evidence"),
        }
        for item in findings
        if isinstance(item, dict)
    ]
    return {"findings": findings, "finding_summaries": finding_summaries, "count": len(findings)}


def _short_report_text(value: Any, limit: int = 1000) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit]


def _slim_product_bug_summaries(value: Any) -> list[dict[str, Any]]:
    result = []
    for item in value or []:
        if not isinstance(item, dict):
            continue
        result.append({
            "name": item.get("name"),
            "expected_behavior": _short_report_text(item.get("expected_behavior"), 220),
            "actual_behavior": _short_report_text(item.get("actual_behavior"), 360),
            "failure_classification": item.get("failure_classification"),
        })
    return result


def native_report_summary(data: dict[str, Any]) -> dict[str, Any]:
    scenarios = []
    for item in data.get("scenarios") or []:
        if not isinstance(item, dict):
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        slim = {
            "scenario_id": item.get("scenario_id"),
            "name": item.get("name"),
            "status": item.get("status"),
            "ok": item.get("ok"),
        }
        has_product_bug_summary = "product_bug_finding_summaries" in details
        for key in ("failure_reason", "skip_reason"):
            if item.get(key):
                slim[key] = _short_report_text(item[key], 240 if has_product_bug_summary else 1000)
        include_details = item.get("status") != "passed" or has_product_bug_summary
        slim_details = {
            key: _slim_product_bug_summaries(details[key]) if key == "product_bug_finding_summaries" else details[key]
            for key in (
                "resource_namespace",
                "machine_id",
                "artifact_dir",
                "failure_classification",
                "product_bug_finding_summaries",
            )
            if key in details
        } if include_details else {}
        if slim_details:
            slim["details"] = slim_details
        scenarios.append(slim)
    return {
        "case_id": data.get("case_id"),
        "status": data.get("status"),
        "ok": data.get("ok"),
        "scenarios": scenarios,
        "scenario_summary": data.get("scenario_summary") or {},
        "failure_index": data.get("failure_index") or {},
        "failure_reason": _short_report_text(data.get("failure_reason"), 300),
        "failure_classification": data.get("failure_classification") or "",
        "resource_namespace": data.get("resource_namespace") or "",
        "machine_id": data.get("machine_id") or "",
    }


def native_report_summary_from_path(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return native_report_summary(data if isinstance(data, dict) else {})


def native_report_summary_json_from_path(path: str | Path) -> str:
    return json.dumps(native_report_summary_from_path(path), ensure_ascii=False, separators=(",", ":"))


def native_report_summary_prefixed_line(path: str | Path) -> str:
    return NATIVE_REPORT_SUMMARY_PREFIX + native_report_summary_json_from_path(path)


def native_report_summary_from_stdout(stdout: str) -> dict[str, Any]:
    for line in reversed((stdout or "").splitlines()):
        if not line.startswith(NATIVE_REPORT_SUMMARY_PREFIX):
            continue
        raw = line[len(NATIVE_REPORT_SUMMARY_PREFIX):]
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
