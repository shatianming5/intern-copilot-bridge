from __future__ import annotations

import json
from typing import Any

from CI.helpers import reporting


def source_markers_found_detail(evidence: dict[str, Any]) -> dict[str, Any]:
    markers = evidence.get("markers") if isinstance(evidence, dict) else None
    markers = markers if isinstance(markers, list) else []
    missing = [
        str(item.get("marker") or "")
        for item in markers
        if not isinstance(item, dict) or int(item.get("line") or 0) <= 0
    ]
    source_path = str(evidence.get("source_path") or "") if isinstance(evidence, dict) else ""
    all_markers_found = bool(evidence.get("all_markers_found")) if isinstance(evidence, dict) else False
    return {
        "ok": bool(source_path and all_markers_found and not missing),
        "source_path": source_path,
        "all_markers_found": all_markers_found,
        "missing_markers": missing,
        "markers": markers,
    }


def dist_contract_ok_detail(evidence: dict[str, Any]) -> dict[str, Any]:
    checks = evidence.get("checks") if isinstance(evidence, dict) else None
    checks = checks if isinstance(checks, list) else []
    failed = list(evidence.get("failed") or []) if isinstance(evidence, dict) else []
    if not failed:
        failed = [
            str(item.get("name") or "")
            for item in checks
            if not isinstance(item, dict) or not bool(item.get("ok"))
        ]
    return {
        "ok": bool(evidence.get("ok", not failed)) and not failed if isinstance(evidence, dict) else False,
        "failed": failed,
        "checks": checks,
        "bundle": evidence.get("bundle", "") if isinstance(evidence, dict) else "",
    }


def report_redacted_detail(value: Any) -> dict[str, Any]:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    lower_text = text.lower()
    sensitive_terms = [
        term
        for term in reporting.SENSITIVE_REPORT_TERMS
        if term.lower() in lower_text
    ]
    bearer_leaks = [
        match.group(0)
        for match in reporting.BEARER_TOKEN_RE.finditer(text)
        if match.group(0).lower() != "bearer <redacted>"
    ]
    return {
        "ok": not sensitive_terms and not bearer_leaks,
        "sensitive_terms": sensitive_terms,
        "bearer_token_leaks": bearer_leaks,
    }


def scenario_summary_consistent_detail(
    scenarios: list[dict[str, Any]],
    summary: dict[str, Any],
) -> dict[str, Any]:
    expected = reporting.scenario_summary(scenarios)
    actual = {
        "total": int(summary.get("total") or 0),
        "passed": int(summary.get("passed") or 0),
        "failed": int(summary.get("failed") or 0),
        "skipped": int(summary.get("skipped") or 0),
    }
    return {
        "ok": actual == expected,
        "expected": expected,
        "actual": actual,
    }
