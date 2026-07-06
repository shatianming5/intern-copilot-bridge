from __future__ import annotations

import json
from pathlib import Path

from CI.cases.base import CaseDefinition
from CI.cases.registry import load_case_sets, load_cases, validate_case_set_references


def _case_list_values(value: str) -> list[str]:
    if not value:
        return []
    stripped = value.lstrip()
    if stripped.startswith(("{", "[")):
        raw = value
    else:
        path = Path(value).expanduser()
        try:
            raw = path.read_text(encoding="utf-8") if path.is_file() else value
        except OSError:
            raw = value
    data = json.loads(raw)
    if isinstance(data, list):
        return [str(item) for item in data]
    if isinstance(data, dict):
        for key in ("cases", "case_ids", "ids"):
            items = data.get(key)
            if isinstance(items, list):
                return [str(item) for item in items]
    raise ValueError("--case-list must be a JSON list or object with cases/case_ids/ids")


def _resolve_case(value: str, cases: list[CaseDefinition]) -> CaseDefinition:
    wanted = value.strip()
    for case in cases:
        if case.id == wanted or case.name == wanted:
            return case
    raise KeyError(f"unknown CI case id/name: {value}")


def select_cases(
    *,
    case_values: list[str],
    case_list: str,
    case_set: str,
    include_disabled: bool = False,
) -> list[CaseDefinition]:
    requested: list[str] = []
    explicit_selection = bool(case_values or case_list or case_set)
    all_cases = load_cases(include_disabled=True)
    sets = load_case_sets()
    validate_case_set_references(all_cases, sets)
    for value in case_values:
        if value == "full":
            requested.extend(sets["full"])
        else:
            requested.append(value)
    requested.extend(_case_list_values(case_list))
    if case_set:
        if case_set not in sets:
            raise KeyError(f"unknown CI case set: {case_set}")
        requested.extend(sets[case_set])
    if not requested and not explicit_selection:
        requested.extend(sets["full"])

    selected: list[CaseDefinition] = []
    seen: set[str] = set()
    for value in requested:
        case = _resolve_case(value, all_cases)
        if case.id in seen:
            continue
        seen.add(case.id)
        if case.enabled or include_disabled:
            selected.append(case)
    return selected
