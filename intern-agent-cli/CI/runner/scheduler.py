from __future__ import annotations

from typing import Any

from CI.cases.base import CaseDefinition


def _case_resources(case: CaseDefinition) -> tuple[str, ...]:
    resources = case.extra.get("resources", ())
    if isinstance(resources, str):
        return (resources,)
    return tuple(str(item) for item in resources)


def _case_requires_relay_machine(case: CaseDefinition) -> bool:
    return any(
        resource.startswith("relay_machine") or resource.startswith("relay_policy_state")
        for resource in _case_resources(case)
    )


def _machine_for_case(case: CaseDefinition, machine_pool: list[dict[str, Any]], offset: int) -> dict[str, Any]:
    if _case_requires_relay_machine(case):
        for machine in machine_pool:
            if machine.get("role") == "relay" or int(machine.get("index") or 0) == 0:
                return machine
    return machine_pool[offset % len(machine_pool)]


def _case_scenario_ids(case: CaseDefinition) -> tuple[str, ...]:
    scenarios = case.extra.get("scenario_ids", ())
    if isinstance(scenarios, str):
        return (scenarios,)
    return tuple(str(item) for item in scenarios)


def _declared_scenarios(case: CaseDefinition, *, status: str, reason: str = "") -> list[dict[str, Any]]:
    scenarios = []
    for scenario_id in _case_scenario_ids(case):
        entry = {
            "scenario_id": scenario_id,
            "name": scenario_id.rsplit(".", 1)[-1].replace("_", " "),
            "status": status,
            "ok": status == "passed",
        }
        if reason and status == "skipped":
            entry["skip_reason"] = reason
        elif reason and status == "failed":
            entry["failure_reason"] = reason
        scenarios.append(entry)
    return scenarios


def _is_setup_gate(case: CaseDefinition) -> bool:
    return case.enabled and (case.kind == "setup_basic" or case.smoke_scenarios == ("setup",))


def _plan_entry(
    *,
    case: CaseDefinition,
    case_index: int,
    machine: dict[str, Any],
    schedule_order: int,
    wave: int,
    slot: int,
    serial_reason: str = "",
    setup_gate: bool = False,
) -> dict[str, Any]:
    return {
        "case_id": case.id,
        "name": case.name,
        "case_index": case_index,
        "schedule_order": schedule_order,
        "concurrency_wave": wave,
        "concurrency_slot": slot,
        "machine": dict(machine),
        "machine_id": machine.get("id", ""),
        "machine_host": machine.get("host", ""),
        "declared_resources": list(_case_resources(case)),
        "parallel_safe": case.parallel_safe,
        "serial_reason": serial_reason,
        "setup_gate": setup_gate,
    }


def plan_remote_cases(
    *,
    cases: list[CaseDefinition],
    machines: list[dict[str, Any]],
    parallel_workers: int,
) -> list[dict[str, Any]]:
    if not cases:
        return []
    machine_pool = machines or [{}]
    max_workers = min(max(1, int(parallel_workers or 1)), len(machine_pool))
    setup_items = [(index, case) for index, case in enumerate(cases) if _is_setup_gate(case)]
    regular_items = [(index, case) for index, case in enumerate(cases) if not _is_setup_gate(case)]
    plan: list[dict[str, Any]] = []
    wave = 0
    schedule_order = 0

    for setup_offset, (case_index, case) in enumerate(setup_items):
        machine = _machine_for_case(case, machine_pool, setup_offset)
        plan.append(_plan_entry(
            case=case,
            case_index=case_index,
            machine=machine,
            schedule_order=schedule_order,
            wave=wave,
            slot=0,
            serial_reason="setup gate",
            setup_gate=True,
        ))
        schedule_order += 1
        wave += 1

    current_wave_entries = 0
    current_wave_resources: set[str] = set()
    regular_offset = 0
    for case_index, case in regular_items:
        resources = set(_case_resources(case))
        serial_reason = ""
        if not case.parallel_safe:
            serial_reason = "parallel_safe=false"
            if current_wave_entries:
                wave += 1
                current_wave_entries = 0
                current_wave_resources = set()
            slot = 0
            machine = _machine_for_case(case, machine_pool, regular_offset)
            plan.append(_plan_entry(
                case=case,
                case_index=case_index,
                machine=machine,
                schedule_order=schedule_order,
                wave=wave,
                slot=slot,
                serial_reason=serial_reason,
            ))
            schedule_order += 1
            regular_offset += 1
            wave += 1
            current_wave_entries = 0
            current_wave_resources = set()
            continue

        conflicts = sorted(resources & current_wave_resources)
        if conflicts:
            serial_reason = "resource conflict: " + ", ".join(conflicts)
            wave += 1
            current_wave_entries = 0
            current_wave_resources = set()
        elif current_wave_entries >= max_workers:
            wave += 1
            current_wave_entries = 0
            current_wave_resources = set()

        machine = _machine_for_case(case, machine_pool, regular_offset)
        plan.append(_plan_entry(
            case=case,
            case_index=case_index,
            machine=machine,
            schedule_order=schedule_order,
            wave=wave,
            slot=current_wave_entries,
            serial_reason=serial_reason,
        ))
        schedule_order += 1
        regular_offset += 1
        current_wave_entries += 1
        current_wave_resources.update(resources)
    return plan
