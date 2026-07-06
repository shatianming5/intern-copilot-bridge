from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from CI.actions.registry import load_action_definitions, validate_action_registry
from CI.assertions.registry import load_assertion_definitions, validate_assertion_registry
from CI.cases.base import CaseDefinition
from CI.cases.registry import load_cases
from CI.runner.stage_0_preflight import validate_stage_preflight


LEGACY_ID_RE = re.compile(r"(^c_\d{4}(?:_|$)|case_slots)", re.IGNORECASE)
VALID_STAGE_PREFIXES = {"F", "J"}


def _case_prefix(case_id: str) -> str:
    return case_id.split("_", 1)[0] if "_" in case_id else ""


def _active_stage_cases(cases: Sequence[CaseDefinition]) -> list[CaseDefinition]:
    return [
        case
        for case in cases
        if case.enabled and _case_prefix(case.id) in VALID_STAGE_PREFIXES
    ]


def _flatten_ids(raw: Any) -> Iterable[str]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, Mapping):
        if "name" in raw:
            return (str(raw["name"]),)
        values: list[str] = []
        for value in raw.values():
            values.extend(_flatten_ids(value))
        return tuple(values)
    if isinstance(raw, Iterable):
        values = []
        for item in raw:
            values.extend(_flatten_ids(item))
        return tuple(values)
    return (str(raw),)


def _case_ref_ids(case: CaseDefinition, key: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.strip() for item in _flatten_ids(case.extra.get(key)) if item.strip()))


def _missing_refs(
    *,
    cases: Sequence[CaseDefinition],
    key: str,
    registered_ids: set[str],
) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for case in cases:
        refs = _case_ref_ids(case, key)
        unknown = sorted(set(refs) - registered_ids)
        if unknown:
            missing.append({"case_id": case.id, key: unknown})
    return missing


def _legacy_refs(
    *,
    cases: Sequence[CaseDefinition],
    key: str,
) -> list[dict[str, Any]]:
    legacy: list[dict[str, Any]] = []
    for case in cases:
        refs = _case_ref_ids(case, key)
        found = sorted(ref for ref in refs if LEGACY_ID_RE.search(ref))
        if found:
            legacy.append({"case_id": case.id, key: found})
    return legacy


def _legacy_registered_ids(ids: Iterable[str]) -> list[str]:
    return sorted(item for item in ids if LEGACY_ID_RE.search(item))


def audit_action_assertion_contracts(
    cases: Sequence[CaseDefinition] | None = None,
) -> dict[str, Any]:
    all_cases = list(cases) if cases is not None else load_cases(include_disabled=True)
    active_cases = _active_stage_cases(all_cases)
    actions = load_action_definitions()
    assertions = load_assertion_definitions()
    action_ids = {item.id for item in actions}
    assertion_ids = {item.id for item in assertions}

    registry_errors: list[str] = []
    for validator in (validate_action_registry, validate_assertion_registry):
        try:
            validator()
        except Exception as exc:  # pragma: no cover - exact registry fault is reported to the caller.
            registry_errors.append(str(exc))

    missing_actions = _missing_refs(cases=active_cases, key="actions", registered_ids=action_ids)
    missing_assertions = _missing_refs(cases=active_cases, key="assertions", registered_ids=assertion_ids)
    legacy_case_actions = _legacy_refs(cases=active_cases, key="actions")
    legacy_case_assertions = _legacy_refs(cases=active_cases, key="assertions")
    legacy_registry_actions = _legacy_registered_ids(action_ids)
    legacy_registry_assertions = _legacy_registered_ids(assertion_ids)
    stage_preflight = validate_stage_preflight(active_cases)

    referenced_actions = {
        action
        for case in active_cases
        for action in _case_ref_ids(case, "actions")
    }
    referenced_assertions = {
        assertion
        for case in active_cases
        for assertion in _case_ref_ids(case, "assertions")
    }

    errors: list[str] = [
        *registry_errors,
        *[f"{item['case_id']} missing actions: {', '.join(item['actions'])}" for item in missing_actions],
        *[f"{item['case_id']} missing assertions: {', '.join(item['assertions'])}" for item in missing_assertions],
        *[f"{item['case_id']} legacy action refs: {', '.join(item['actions'])}" for item in legacy_case_actions],
        *[f"{item['case_id']} legacy assertion refs: {', '.join(item['assertions'])}" for item in legacy_case_assertions],
    ]
    if legacy_registry_actions:
        errors.append(f"legacy registered action ids: {', '.join(legacy_registry_actions)}")
    if legacy_registry_assertions:
        errors.append(f"legacy registered assertion ids: {', '.join(legacy_registry_assertions)}")

    return {
        "schema": "intern-agents.ci-action-assertion-audit.v1",
        "ok": not errors,
        "status": "passed" if not errors else "failed",
        "summary": {
            "active_fj_cases": len(active_cases),
            "registered_actions": len(actions),
            "registered_assertions": len(assertions),
            "referenced_actions": len(referenced_actions),
            "referenced_assertions": len(referenced_assertions),
            "missing_action_refs": sum(len(item["actions"]) for item in missing_actions),
            "missing_assertion_refs": sum(len(item["assertions"]) for item in missing_assertions),
            "legacy_action_refs": sum(len(item["actions"]) for item in legacy_case_actions) + len(legacy_registry_actions),
            "legacy_assertion_refs": sum(len(item["assertions"]) for item in legacy_case_assertions) + len(legacy_registry_assertions),
            "stage_preflight_errors": len(stage_preflight.get("errors", [])),
        },
        "missing_actions": missing_actions,
        "missing_assertions": missing_assertions,
        "legacy_case_actions": legacy_case_actions,
        "legacy_case_assertions": legacy_case_assertions,
        "legacy_registry_actions": legacy_registry_actions,
        "legacy_registry_assertions": legacy_registry_assertions,
        "stage_preflight": {
            "ok": stage_preflight.get("ok", False),
            "status": stage_preflight.get("status", ""),
            "errors": stage_preflight.get("errors", []),
        },
        "errors": errors,
    }
