from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from CI.assertions import core as core_assertions
from CI.assertions import feishu as feishu_assertions
from CI.assertions import intern as intern_assertions
from CI.assertions import policy as policy_assertions
from CI.assertions import session as session_assertions
from CI.assertions import source_contract as source_contract_assertions
from CI.assertions import treeview as treeview_assertions
from CI.assertions import workspace as workspace_assertions
from CI.assertions.core import CaseAssertions


@dataclass(frozen=True)
class AssertionDefinition:
    id: str
    title: str
    description: str
    kind: str
    callable_path: str = ""
    parameters: tuple[dict[str, Any], ...] = ()
    returns: str = ""
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "kind": self.kind,
            "callable_path": self.callable_path,
            "parameters": list(self.parameters),
            "returns": self.returns,
            "notes": list(self.notes),
        }


_ASSERTION_DOMAIN_MODULES = (
    core_assertions,
    workspace_assertions,
    intern_assertions,
    session_assertions,
    feishu_assertions,
    treeview_assertions,
    policy_assertions,
    source_contract_assertions,
)


def _iter_assertion_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    for module in _ASSERTION_DOMAIN_MODULES:
        specs.extend(dict(item) for item in module.ASSERTION_SPECS)
    return sorted(specs, key=lambda item: int(item["order"]))


def _definition_from_spec(spec: dict[str, Any]) -> AssertionDefinition:
    return AssertionDefinition(
        id=str(spec["id"]),
        title=str(spec["title"]),
        description=str(spec["description"]),
        kind=str(spec["kind"]),
        callable_path=str(spec.get("callable_path") or ""),
        parameters=tuple(dict(item) for item in spec.get("parameters", ())),
        returns=str(spec.get("returns") or ""),
        notes=tuple(str(item) for item in spec.get("notes", ())),
    )


ASSERTION_DEFINITIONS: tuple[AssertionDefinition, ...] = tuple(
    _definition_from_spec(item) for item in _iter_assertion_specs()
)


def load_assertion_definitions() -> list[AssertionDefinition]:
    return list(ASSERTION_DEFINITIONS)


def assertion_by_id(assertion_id: str) -> AssertionDefinition:
    for assertion in ASSERTION_DEFINITIONS:
        if assertion.id == assertion_id:
            return assertion
    raise KeyError(f"unknown CI assertion: {assertion_id}")


def validate_assertion_registry() -> None:
    ids = [item.id for item in ASSERTION_DEFINITIONS]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate CI assertion registry ids")
    for assertion in ASSERTION_DEFINITIONS:
        if not assertion.description:
            raise ValueError(f"CI assertion {assertion.id} missing description")
        if assertion.kind == "ctx_assertion":
            if not assertion.callable_path.startswith("ctx.assertion."):
                raise ValueError(f"CI assertion {assertion.id} has invalid callable_path")
            method_name = assertion.callable_path.rsplit(".", 1)[-1]
            if not hasattr(CaseAssertions, method_name):
                raise ValueError(f"CI assertion {assertion.id} callable does not exist: {assertion.callable_path}")
    registered_callables = {item.callable_path for item in ASSERTION_DEFINITIONS if item.kind == "ctx_assertion"}
    for method_name, value in CaseAssertions.__dict__.items():
        if method_name.startswith("_") or not callable(value):
            continue
        callable_path = f"ctx.assertion.{method_name}"
        if callable_path not in registered_callables:
            raise ValueError(f"unregistered CI ctx assertion: {callable_path}")
