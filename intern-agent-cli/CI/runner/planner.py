from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from CI.actions.registry import action_resource_locks
from CI.cases.base import CaseDefinition
from CI.cases.resources import ResourceLock, conflicts_between, locks_for_case, missing_resource_locks, resource_lock_errors


PLANNER_SCHEMA = "intern-agents.ci-planner.v1"
DEFAULT_PROJECT = "axis_intern_agents_backup"


def _stage(case: CaseDefinition) -> str:
    raw = case.extra.get("ci_stage") or case.extra.get("stage") or ""
    if raw:
        return str(raw).upper()
    prefix = case.id.split("_", 1)[0]
    return prefix.upper() if prefix in {"F", "J"} else "legacy"


def _node_id(case_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "_", case_id)
    if value and value[0].isdigit():
        value = "case_" + value
    return value or "case_unknown"


def _case_namespace(case: CaseDefinition) -> str:
    parts = case.id.split("_", 2)
    if len(parts) >= 2:
        return "ci_" + "_".join(parts[:2]).lower()
    return "ci_" + _node_id(case.id).lower()


def _case_project(case: CaseDefinition) -> str:
    return str(case.extra.get("project") or DEFAULT_PROJECT)


def _lock_template_context(case: CaseDefinition) -> dict[str, str]:
    namespace = _case_namespace(case)
    context = {
        "case_id": namespace,
        "project": _case_project(case),
        "full_case_id": case.id,
        "workspace_scope": f"{namespace}:*",
        "intern_scope": f"{namespace}:*",
        "task_scope": f"{namespace}:*",
        "artifact_id": namespace,
        "tmux_scope": namespace,
        "feishu_chat_scope": namespace,
        "team_scope": f"{namespace}:*",
        "helper_scope": namespace,
        "fixture_scope": namespace,
        "policy_scope": namespace,
        "source_scope": namespace,
        "owner_scope": namespace,
        "repo_scope": namespace,
        "transcript_scope": namespace,
        "hook_scope": namespace,
        "session_scope": namespace,
        "skill_scope": namespace,
    }
    overrides = case.extra.get("lock_params") or {}
    if not isinstance(overrides, dict):
        raise ValueError(f"CaseDefinition.extra['lock_params'] must be a mapping for {case.id}")
    return {**context, **{str(key): str(value) for key, value in overrides.items()}}


def _case_action_ids(case: CaseDefinition) -> tuple[str, ...]:
    actions = case.extra.get("actions") or ()
    if isinstance(actions, str):
        return (actions,)
    return tuple(str(action) for action in actions)


def _expand_action_resource_template(template: str, case: CaseDefinition) -> str:
    try:
        return template.format(**_lock_template_context(case))
    except KeyError as exc:
        raise KeyError(f"action resource lock template {template!r} for {case.id} references unknown lock param {exc}") from exc


def action_locks_for_case(case: CaseDefinition) -> list[ResourceLock]:
    locks: list[ResourceLock] = []
    for action_id in _case_action_ids(case):
        for lock in action_resource_locks(action_id):
            locks.append(ResourceLock(
                resource=_expand_action_resource_template(str(lock["resource"]), case),
                mode=str(lock["mode"]),
                source=f"action:{action_id}.resource_locks",
            ))
    return locks


def planner_locks_for_case(case: CaseDefinition) -> list[ResourceLock]:
    return locks_for_case(case) + action_locks_for_case(case)


def missing_lock_entries(cases: list[CaseDefinition]) -> list[dict[str, str]]:
    return [
        {
            "case_id": case.id,
            "name": case.name,
            "stage": _stage(case),
            "reason": "CaseDefinition.extra['resource_locks'] is required by the F/J planner",
        }
        for case in cases
        if missing_resource_locks(case)
    ]


def invalid_lock_entries(cases: list[CaseDefinition]) -> list[dict[str, Any]]:
    return [
        {
            "case_id": case.id,
            "name": case.name,
            "stage": _stage(case),
            "errors": resource_lock_errors(case),
        }
        for case in cases
        if resource_lock_errors(case)
    ]


def build_conflict_graph(cases: list[CaseDefinition]) -> dict[str, Any]:
    lock_map = {
        case.id: [] if resource_lock_errors(case) else planner_locks_for_case(case)
        for case in cases
    }
    edges: list[dict[str, Any]] = []
    for left_index, left in enumerate(cases):
        for right in cases[left_index + 1:]:
            conflicts = conflicts_between(
                left,
                right,
                left_locks=lock_map[left.id],
                right_locks=lock_map[right.id],
            )
            if not conflicts:
                continue
            edges.append({
                "left": left.id,
                "right": right.id,
                "reasons": [item.to_dict() for item in conflicts],
                "summary": "; ".join(item.reason for item in conflicts[:3])
                + ("; ..." if len(conflicts) > 3 else ""),
            })
    return {
        "schema": PLANNER_SCHEMA + ".conflict_graph",
        "nodes": [
            {
                "case_id": case.id,
                "name": case.name,
                "stage": _stage(case),
                "kind": case.kind,
                "resource_locks": [lock.to_dict() for lock in lock_map[case.id]],
                "case_resource_locks": [] if resource_lock_errors(case) else [lock.to_dict() for lock in locks_for_case(case)],
                "action_resource_locks": [lock.to_dict() for lock in action_locks_for_case(case)],
                "missing_resource_locks": missing_resource_locks(case),
                "resource_lock_errors": resource_lock_errors(case),
            }
            for case in cases
        ],
        "edges": edges,
    }


def _has_conflict(case_id: str, wave_case_ids: list[str], edge_map: set[tuple[str, str]]) -> bool:
    for other in wave_case_ids:
        key = tuple(sorted((case_id, other)))
        if key in edge_map:
            return True
    return False


def build_schedule_waves(cases: list[CaseDefinition], conflict_graph: dict[str, Any]) -> dict[str, Any]:
    edge_map = {tuple(sorted((edge["left"], edge["right"]))) for edge in conflict_graph.get("edges", [])}
    waves: list[dict[str, Any]] = []
    for case in cases:
        placed = False
        for wave in waves:
            case_ids = list(wave["cases"])
            if not _has_conflict(case.id, case_ids, edge_map):
                wave["cases"].append(case.id)
                placed = True
                break
        if not placed:
            waves.append({"wave": len(waves), "cases": [case.id]})
    return {
        "schema": PLANNER_SCHEMA + ".schedule_waves",
        "waves": waves,
    }


def _dot_label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def conflict_graph_dot(conflict_graph: dict[str, Any]) -> str:
    lines = ["graph ci_conflict_graph {"]
    lines.append("  rankdir=LR;")
    for node in conflict_graph.get("nodes", []):
        case_id = str(node["case_id"])
        missing = "\\nmissing resource_locks" if node.get("missing_resource_locks") else ""
        label = f"{case_id}\\n{node.get('stage', '')}:{node.get('kind', '')}{missing}"
        lines.append(f'  {_node_id(case_id)} [label="{_dot_label(label)}"];')
    for edge in conflict_graph.get("edges", []):
        left = _node_id(str(edge["left"]))
        right = _node_id(str(edge["right"]))
        label = _dot_label(str(edge.get("summary") or "resource conflict"))
        lines.append(f'  {left} -- {right} [label="{label}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"


def conflict_graph_mermaid(conflict_graph: dict[str, Any]) -> str:
    lines = ["graph LR"]
    for node in conflict_graph.get("nodes", []):
        case_id = str(node["case_id"])
        missing = "<br/>missing resource_locks" if node.get("missing_resource_locks") else ""
        label = f"{case_id}<br/>{node.get('stage', '')}:{node.get('kind', '')}{missing}"
        lines.append(f'  {_node_id(case_id)}["{label}"]')
    for edge in conflict_graph.get("edges", []):
        left = _node_id(str(edge["left"]))
        right = _node_id(str(edge["right"]))
        label = str(edge.get("summary") or "resource conflict").replace('"', "'")
        lines.append(f'  {left} -- "{label}" --- {right}')
    return "\n".join(lines) + "\n"


def build_plan(cases: list[CaseDefinition]) -> dict[str, Any]:
    missing = missing_lock_entries(cases)
    invalid = invalid_lock_entries(cases)
    conflict_graph = build_conflict_graph(cases)
    schedule_waves = build_schedule_waves(cases, conflict_graph)
    failure_parts = []
    if missing:
        failure_parts.append(f"{len(missing)} selected cases missing explicit resource_locks")
    if invalid:
        failure_parts.append(f"{len(invalid)} selected cases have invalid resource_locks")
    return {
        "schema": PLANNER_SCHEMA,
        "ok": not missing and not invalid,
        "status": "passed" if not missing and not invalid else "failed",
        "failure_reason": "; ".join(failure_parts),
        "selected_cases": [
            {
                "case_id": case.id,
                "name": case.name,
                "stage": _stage(case),
                "kind": case.kind,
                "parallel_safe": case.parallel_safe,
                "locks": [] if resource_lock_errors(case) else [lock.to_dict() for lock in planner_locks_for_case(case)],
                "case_locks": [] if resource_lock_errors(case) else [lock.to_dict() for lock in locks_for_case(case)],
                "action_locks": [lock.to_dict() for lock in action_locks_for_case(case)],
                "missing_resource_locks": missing_resource_locks(case),
                "resource_lock_errors": resource_lock_errors(case),
            }
            for case in cases
        ],
        "missing_resource_locks": missing,
        "invalid_resource_locks": invalid,
        "conflict_graph": conflict_graph,
        "schedule_waves": schedule_waves,
        "summary": {
            "case_count": len(cases),
            "missing_resource_lock_count": len(missing),
            "invalid_resource_lock_count": len(invalid),
            "conflict_edge_count": len(conflict_graph.get("edges", [])),
            "wave_count": len(schedule_waves.get("waves", [])),
        },
    }


def write_plan_artifacts(plan: dict[str, Any], artifact_dir: Path) -> dict[str, str]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "plan_json": artifact_dir / "plan.json",
        "conflict_graph_json": artifact_dir / "conflict_graph.json",
        "conflict_graph_dot": artifact_dir / "conflict_graph.dot",
        "conflict_graph_mermaid": artifact_dir / "conflict_graph.mmd",
        "schedule_waves_json": artifact_dir / "schedule_waves.json",
    }
    paths["plan_json"].write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["conflict_graph_json"].write_text(json.dumps(plan["conflict_graph"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["conflict_graph_dot"].write_text(conflict_graph_dot(plan["conflict_graph"]), encoding="utf-8")
    paths["conflict_graph_mermaid"].write_text(conflict_graph_mermaid(plan["conflict_graph"]), encoding="utf-8")
    paths["schedule_waves_json"].write_text(json.dumps(plan["schedule_waves"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {key: str(path) for key, path in paths.items()}


def create_planner_report(cases: list[CaseDefinition], artifact_dir: Path) -> dict[str, Any]:
    plan = build_plan(cases)
    paths = write_plan_artifacts(plan, artifact_dir)
    return {
        "ok": bool(plan["ok"]),
        "status": plan["status"],
        "schema": PLANNER_SCHEMA + ".report",
        "summary": plan["summary"],
        "failure_reason": plan["failure_reason"],
        "artifacts": paths,
        "plan": {
            "selected_cases": plan["selected_cases"],
            "missing_resource_locks": plan["missing_resource_locks"],
            "invalid_resource_locks": plan["invalid_resource_locks"],
            "schedule_waves": plan["schedule_waves"],
            "conflict_edge_count": plan["summary"]["conflict_edge_count"],
        },
    }
