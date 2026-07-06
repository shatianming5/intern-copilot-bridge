from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from CI.actions import feishu as feishu_actions
from CI.actions import intern as intern_actions
from CI.actions import policy as policy_actions
from CI.actions import relay_daemon as relay_daemon_actions
from CI.actions import registry_data
from CI.actions import session as session_actions
from CI.actions import skill as skill_actions
from CI.actions import source_contract as source_contract_actions
from CI.actions import task as task_actions
from CI.actions import treeview as treeview_actions
from CI.actions import workspace as workspace_actions
from CI.actions import cli as cli_actions
from CI.actions import codeup as codeup_actions
from CI.actions import feishu_mock as feishu_mock_actions
from CI.actions import reporting as reporting_actions
from CI.actions import treeview_mock as treeview_mock_actions
from CI.cases.resources import LOCK_MODES


@dataclass(frozen=True)
class ParameterDefinition:
    name: str
    description: str
    required: bool = True
    default: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "required": self.required,
            "default": self.default,
        }


@dataclass(frozen=True)
class ActionDefinition:
    id: str
    title: str
    description: str
    category: str
    kind: str
    callable_path: str = ""
    parameters: tuple[ParameterDefinition, ...] = ()
    returns: str = ""
    resources: tuple[str, ...] = ()
    gui_command: str = ""
    cli_equivalent: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "kind": self.kind,
            "callable_path": self.callable_path,
            "parameters": [item.to_dict() for item in self.parameters],
            "returns": self.returns,
            "resources": list(self.resources),
            "resource_locks": [dict(item) for item in action_resource_locks(self.id)],
            "gui_command": self.gui_command,
            "cli_equivalent": self.cli_equivalent,
            "notes": list(self.notes),
        }


ACTION_ROOT_FACTORIES: dict[str, Callable[[Any], Any]] = {
    "cli": cli_actions.CliActions,
    "codeup": codeup_actions.CodeupActions,
    "feishu": feishu_actions.FeishuActions,
    "feishu_mock": feishu_mock_actions.FeishuMockActions,
    "intern": intern_actions.InternActions,
    "policy": policy_actions.PolicyActions,
    "relay_daemon": relay_daemon_actions.RelayDaemonActions,
    "reporting": reporting_actions.ReportingActions,
    "session": session_actions.SessionActions,
    "skill": skill_actions.SkillActions,
    "source_contract": source_contract_actions.SourceContractActions,
    "task": task_actions.TaskActions,
    "treeview": treeview_actions.TreeViewActions,
    "treeview_mock": treeview_mock_actions.TreeViewMockActions,
    "workspace": workspace_actions.WorkspaceActions,
}


def _iter_action_specs() -> list[dict[str, Any]]:
    return registry_data.iter_action_specs()


def _definition_from_spec(spec: dict[str, Any]) -> ActionDefinition:
    return ActionDefinition(
        id=str(spec["id"]),
        title=str(spec["title"]),
        description=str(spec["description"]),
        category=str(spec["category"]),
        kind=str(spec["kind"]),
        callable_path=str(spec.get("callable_path") or ""),
        parameters=tuple(ParameterDefinition(**item) for item in spec.get("parameters", ())),
        returns=str(spec.get("returns") or ""),
        resources=tuple(str(item) for item in spec.get("resources", ())),
        gui_command=str(spec.get("gui_command") or ""),
        cli_equivalent=str(spec.get("cli_equivalent") or ""),
        notes=tuple(str(item) for item in spec.get("notes", ())),
    )


def _load_action_resource_locks() -> dict[str, tuple[dict[str, str], ...]]:
    return registry_data.action_resource_locks_by_id()


ACTION_DEFINITIONS: tuple[ActionDefinition, ...] = tuple(_definition_from_spec(item) for item in _iter_action_specs())
_ACTION_RESOURCE_LOCKS_BY_ID: dict[str, tuple[dict[str, str], ...]] = _load_action_resource_locks()


def action_resource_locks(action_id: str) -> tuple[dict[str, str], ...]:
    try:
        return _ACTION_RESOURCE_LOCKS_BY_ID[action_id]
    except KeyError as exc:
        raise KeyError(f"CI action {action_id} missing explicit resource_locks") from exc


def load_action_definitions() -> list[ActionDefinition]:
    return list(ACTION_DEFINITIONS)


def action_by_id(action_id: str) -> ActionDefinition:
    for action in ACTION_DEFINITIONS:
        if action.id == action_id:
            return action
    raise KeyError(f"unknown CI action: {action_id}")


def action_categories(action_ids: tuple[str, ...] | list[str]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for action_id in action_ids:
        action = action_by_id(action_id)
        result.setdefault(action.category, set()).add(action.id)
    return result


def validate_action_registry() -> None:
    ids = [item.id for item in ACTION_DEFINITIONS]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate CI action registry ids")
    action_ids = set(ids)
    lock_ids = set(_ACTION_RESOURCE_LOCKS_BY_ID)
    missing_locks = sorted(action_ids - lock_ids)
    if missing_locks:
        raise ValueError("CI actions missing explicit resource_locks: " + ", ".join(missing_locks))
    orphan_locks = sorted(lock_ids - action_ids)
    if orphan_locks:
        raise ValueError("resource_locks declared for unknown CI actions: " + ", ".join(orphan_locks))
    for action in ACTION_DEFINITIONS:
        if not action.description:
            raise ValueError(f"CI action {action.id} missing description")
        for index, lock in enumerate(action_resource_locks(action.id)):
            resource = str(lock.get("resource") or "").strip()
            mode = str(lock.get("mode") or "").strip()
            if not resource:
                raise ValueError(f"CI action {action.id} resource_locks[{index}] missing resource")
            if mode not in LOCK_MODES:
                raise ValueError(f"CI action {action.id} resource_locks[{index}] invalid mode: {mode}")
        if action.kind == "ctx_action":
            if not action.callable_path.startswith("ctx.action."):
                raise ValueError(f"CI action {action.id} has invalid callable_path")
            _, _, root_name, method_name = action.callable_path.split(".", 3)
            factory = ACTION_ROOT_FACTORIES.get(root_name)
            if factory is None or not hasattr(factory, method_name):
                raise ValueError(f"CI action {action.id} callable does not exist: {action.callable_path}")
    registered_callables = {item.callable_path for item in ACTION_DEFINITIONS if item.kind == "ctx_action"}
    for root_name, factory in ACTION_ROOT_FACTORIES.items():
        for method_name, value in factory.__dict__.items():
            if method_name.startswith("_") or not callable(value):
                continue
            callable_path = f"ctx.action.{root_name}.{method_name}"
            if callable_path not in registered_callables:
                raise ValueError(f"unregistered CI ctx action: {callable_path}")
