from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from CI.actions.registry import action_by_id
from CI.assertions.registry import assertion_by_id
from CI.cases.base import CaseDefinition


STAGE_CAPABILITY = "F"
STAGE_USER_JOURNEY = "J"
VALID_STAGES = {STAGE_CAPABILITY, STAGE_USER_JOURNEY}
JOURNEY_REQUIRED_CATEGORIES = ("feishu_group", "intern", "task", "user_interaction")
JOURNEY_BOUNDARY_CATEGORIES = {"feishu_group", "user_interaction"}


def _case_prefix(case_id: str) -> str:
    return case_id.split("_", 1)[0] if "_" in case_id else ""


def _declared_stage(case: CaseDefinition) -> str:
    raw = case.extra.get("ci_stage") or case.extra.get("test_stage") or case.extra.get("stage")
    if raw:
        return str(raw).upper()
    prefix = _case_prefix(case.id)
    if prefix in VALID_STAGES:
        return prefix
    return ""


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


def _case_actions(case: CaseDefinition) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(action).strip() for action in _flatten_ids(case.extra.get("actions")) if str(action).strip()))


def _case_assertions(case: CaseDefinition) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(assertion).strip() for assertion in _flatten_ids(case.extra.get("assertions")) if str(assertion).strip()))


def _inferred_stage(actions: Sequence[str]) -> str:
    categories = _action_categories(actions)
    if categories & JOURNEY_BOUNDARY_CATEGORIES:
        return STAGE_USER_JOURNEY
    if categories:
        return STAGE_CAPABILITY
    return ""


def _missing_journey_categories(actions: Sequence[str]) -> list[str]:
    categories = _action_categories(actions)
    return [category for category in JOURNEY_REQUIRED_CATEGORIES if category not in categories]


def _action_categories(actions: Sequence[str]) -> set[str]:
    categories: set[str] = set()
    for action in actions:
        try:
            categories.add(action_by_id(action).category)
        except KeyError:
            continue
    return categories


def _unknown_actions(actions: Sequence[str]) -> list[str]:
    unknown: list[str] = []
    for action in actions:
        try:
            action_by_id(action)
        except KeyError:
            unknown.append(action)
    return sorted(set(unknown))


def _unknown_assertions(assertions: Sequence[str]) -> list[str]:
    unknown: list[str] = []
    for assertion in assertions:
        try:
            assertion_by_id(assertion)
        except KeyError:
            unknown.append(assertion)
    return sorted(set(unknown))


def _journey_boundary_actions(actions: Sequence[str]) -> list[str]:
    forbidden: list[str] = []
    for action in actions:
        try:
            if action_by_id(action).category in JOURNEY_BOUNDARY_CATEGORIES:
                forbidden.append(action)
        except KeyError:
            continue
    return sorted(set(forbidden))


def validate_stage_preflight(cases: Sequence[CaseDefinition]) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    errors: list[str] = []
    for case in cases:
        prefix = _case_prefix(case.id)
        stage = _declared_stage(case)
        actions = _case_actions(case)
        assertions = _case_assertions(case)
        inferred = _inferred_stage(actions)
        case_errors: list[str] = []

        if prefix in VALID_STAGES and stage != prefix:
            case_errors.append(
                f"case id prefix {prefix}_ requires ci_stage={prefix}, got {stage or '<missing>'}"
            )
        if prefix in VALID_STAGES and case.stage != "remote":
            case_errors.append(
                f"F/J cases must use CaseDefinition.stage='remote'; local fixtures belong in unit tests, got {case.stage!r}"
            )
        if stage and stage not in VALID_STAGES:
            case_errors.append(f"invalid ci_stage {stage}; expected F or J")
        if stage in VALID_STAGES and not actions:
            case_errors.append("F/J cases must declare CaseDefinition.extra['actions']")
        if stage in VALID_STAGES and not assertions:
            case_errors.append("F/J cases must declare CaseDefinition.extra['assertions']")

        unknown_actions = _unknown_actions(actions)
        if stage in VALID_STAGES and unknown_actions:
            case_errors.append("unknown actions: " + ", ".join(unknown_actions))
        unknown_assertions = _unknown_assertions(assertions)
        if stage in VALID_STAGES and unknown_assertions:
            case_errors.append("unknown assertions: " + ", ".join(unknown_assertions))

        if stage == STAGE_CAPABILITY:
            forbidden = _journey_boundary_actions(actions)
            if forbidden:
                case_errors.append(
                    "F case cannot declare real Feishu/user-interaction actions: "
                    + ", ".join(forbidden)
                    + "; move this case to J or remove those actions"
                )
            if actions and inferred != STAGE_CAPABILITY:
                case_errors.append("F case actions do not describe a deployment capability")
        elif stage == STAGE_USER_JOURNEY:
            missing = _missing_journey_categories(actions)
            if missing:
                case_errors.append(
                    "J case must cover a full user journey; missing action categories: "
                    + ", ".join(missing)
                )
            if actions and inferred != STAGE_USER_JOURNEY:
                case_errors.append("J case actions do not describe a user journey")

        if case_errors:
            errors.extend(f"{case.id}: {error}" for error in case_errors)
        entries.append({
            "case_id": case.id,
            "declared_stage": stage or "legacy",
            "inferred_stage": inferred or "unknown",
            "actions": list(actions),
            "assertions": list(assertions),
            "status": "failed" if case_errors else "passed",
            "errors": case_errors,
        })

    return {
        "schema": "intern-agents.ci-stage-preflight.v1",
        "ok": not errors,
        "status": "passed" if not errors else "failed",
        "cases": entries,
        "errors": errors,
        "failure_reason": "" if not errors else "; ".join(errors),
    }
