from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from CI.cases.base import CaseDefinition


LOCK_READ = "read"
LOCK_WRITE = "write"
LOCK_EXCLUSIVE = "exclusive"
LOCK_MODES = {LOCK_READ, LOCK_WRITE, LOCK_EXCLUSIVE}


@dataclass(frozen=True)
class ResourceLock:
    resource: str
    mode: str = LOCK_EXCLUSIVE
    source: str = "case"
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        data = {
            "resource": self.resource,
            "mode": self.mode,
            "source": self.source,
        }
        if self.detail:
            data["detail"] = self.detail
        return data


@dataclass(frozen=True)
class ResourceConflict:
    left_case_id: str
    right_case_id: str
    resource: str
    left_mode: str
    right_mode: str
    reason: str
    left_source: str = ""
    right_source: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "left": self.left_case_id,
            "right": self.right_case_id,
            "resource": self.resource,
            "left_mode": self.left_mode,
            "right_mode": self.right_mode,
            "reason": self.reason,
            "left_source": self.left_source,
            "right_source": self.right_source,
        }


def _as_sequence(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return (value,)
    try:
        return tuple(value)
    except TypeError:
        return (value,)


def _normalize_mode(raw: Any) -> str:
    mode = str(raw or LOCK_EXCLUSIVE).strip().lower()
    if mode not in LOCK_MODES:
        raise ValueError(f"invalid resource lock mode: {raw!r}")
    return mode


def _resource(raw: Any) -> str:
    value = str(raw).strip()
    if not value:
        raise ValueError("resource_locks entry must declare a non-empty resource")
    return value


def resource_lock_errors(case: CaseDefinition) -> list[dict[str, str]]:
    errors: list[dict[str, str]] = []
    for index, item in enumerate(_as_sequence(case.extra.get("resource_locks"))):
        prefix = f"resource_locks[{index}]"
        if isinstance(item, ResourceLock):
            if not item.resource.strip():
                errors.append({"field": f"{prefix}.resource", "reason": "resource must be non-empty"})
            if item.mode not in LOCK_MODES:
                errors.append({"field": f"{prefix}.mode", "reason": f"mode must be one of {sorted(LOCK_MODES)}"})
            continue
        if not isinstance(item, dict):
            errors.append({"field": prefix, "reason": "entry must be a mapping with resource/mode or a ResourceLock"})
            continue
        if "resource" not in item:
            errors.append({"field": f"{prefix}.resource", "reason": "resource key is required"})
            continue
        if not str(item.get("resource") or "").strip():
            errors.append({"field": f"{prefix}.resource", "reason": "resource must be non-empty"})
        if "mode" in item and str(item.get("mode") or "").strip().lower() not in LOCK_MODES:
            errors.append({"field": f"{prefix}.mode", "reason": f"mode must be one of {sorted(LOCK_MODES)}"})
    return errors


def locks_for_case(case: CaseDefinition) -> list[ResourceLock]:
    explicit = _as_sequence(case.extra.get("resource_locks"))
    locks: list[ResourceLock] = []
    for item in explicit:
        if isinstance(item, ResourceLock):
            locks.append(item)
            continue
        if isinstance(item, dict):
            resource = _resource(item.get("resource"))
            locks.append(ResourceLock(
                resource=resource,
                mode=_normalize_mode(item.get("mode")),
                source=str(item.get("source") or "case.resource_locks"),
                detail=str(item.get("detail") or ""),
            ))
            continue
        raise ValueError("resource_locks entries must be mappings or ResourceLock objects")

    return _dedupe_locks(locks)


def missing_resource_locks(case: CaseDefinition) -> bool:
    return not bool(_as_sequence(case.extra.get("resource_locks")))


def _dedupe_locks(locks: list[ResourceLock]) -> list[ResourceLock]:
    strength = {LOCK_READ: 0, LOCK_WRITE: 1, LOCK_EXCLUSIVE: 2}
    by_resource: dict[str, ResourceLock] = {}
    for lock in locks:
        current = by_resource.get(lock.resource)
        if current is None or strength[lock.mode] > strength[current.mode]:
            by_resource[lock.resource] = lock
    return [by_resource[key] for key in sorted(by_resource)]


def resources_overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    if left.endswith(":*"):
        return right.startswith(left[:-1])
    if right.endswith(":*"):
        return left.startswith(right[:-1])
    return False


def locks_conflict(left: ResourceLock, right: ResourceLock) -> bool:
    if not resources_overlap(left.resource, right.resource):
        return False
    return not (left.mode == LOCK_READ and right.mode == LOCK_READ)


def conflicts_between(
    left_case: CaseDefinition,
    right_case: CaseDefinition,
    *,
    left_locks: list[ResourceLock] | None = None,
    right_locks: list[ResourceLock] | None = None,
) -> list[ResourceConflict]:
    left_items = left_locks if left_locks is not None else locks_for_case(left_case)
    right_items = right_locks if right_locks is not None else locks_for_case(right_case)
    conflicts: list[ResourceConflict] = []
    for left in left_items:
        for right in right_items:
            if locks_conflict(left, right):
                resource = left.resource if left.resource == right.resource else f"{left.resource} <> {right.resource}"
                conflicts.append(ResourceConflict(
                    left_case_id=left_case.id,
                    right_case_id=right_case.id,
                    resource=resource,
                    left_mode=left.mode,
                    right_mode=right.mode,
                    reason=f"{resource} {left.mode} vs {right.mode}",
                    left_source=left.source,
                    right_source=right.source,
                ))
    return conflicts
