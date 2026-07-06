"""Runtime env switch policy composition."""

from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
from typing import Any

from lib.enterprise_paths import machine_config_state_path as _machine_config_state_path


ENV_SWITCH_SCHEMA = "intern-agents.env-switches.v1"
ENV_SWITCH_STATE_SCHEMA = "intern-agents.env-switch-state.v1"
STATE_SCHEMA = ENV_SWITCH_STATE_SCHEMA


class MachineConfigPolicyError(ValueError):
    """Raised when env switch policy or submitted state is invalid."""


def machine_config_state_path(work_root: str | os.PathLike[str]) -> Path:
    return _machine_config_state_path(work_root)


def env_switch_schema(policy: dict[str, Any] | None) -> dict[str, Any]:
    raw = policy.get("env_switches") if isinstance(policy, dict) else {}
    groups: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        raw_groups = raw.get("groups")
        if isinstance(raw_groups, list):
            groups.extend(group for group in raw_groups if isinstance(group, dict))
    return {
        "schema": str(raw.get("schema") or ENV_SWITCH_SCHEMA) if isinstance(raw, dict) else ENV_SWITCH_SCHEMA,
        "groups": groups,
    }


def load_machine_config_state(work_root: str | os.PathLike[str]) -> dict[str, Any]:
    path = machine_config_state_path(work_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema": STATE_SCHEMA, "machines": {}}
    except Exception as exc:
        raise MachineConfigPolicyError(f"invalid env switch state: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise MachineConfigPolicyError(f"invalid env switch state: {path}")
    machines = data.get("machines")
    if not isinstance(machines, dict):
        machines = {}
    normalized = {}
    for machine_id, record in machines.items():
        if not isinstance(record, dict):
            continue
        enabled_groups = record.get("enabled_groups")
        if not isinstance(enabled_groups, list):
            enabled_groups = []
        group_values = record.get("group_values")
        if not isinstance(group_values, dict):
            group_values = {}
        entry = dict(record)
        entry["enabled_groups"] = [str(item) for item in enabled_groups if str(item)]
        entry["group_values"] = {
            str(group_key): {str(k): str(v) for k, v in values.items()}
            for group_key, values in group_values.items()
            if isinstance(values, dict)
        }
        normalized[str(machine_id)] = entry
    data["schema"] = str(data.get("schema") or STATE_SCHEMA)
    data["machines"] = normalized
    return data


def env_switch_state_for_machine(work_root: str | os.PathLike[str], machine_id: str) -> dict[str, Any]:
    machine_id = str(machine_id or "").strip()
    if not machine_id:
        return {"schema": ENV_SWITCH_STATE_SCHEMA, "exists": False, "enabled_groups": [], "group_values": {}}
    state = load_machine_config_state(work_root)
    record = (state.get("machines") or {}).get(machine_id)
    if not isinstance(record, dict):
        return {"schema": ENV_SWITCH_STATE_SCHEMA, "exists": False, "enabled_groups": [], "group_values": {}}
    enabled_groups = record.get("enabled_groups")
    group_values = record.get("group_values")
    return {
        "schema": ENV_SWITCH_STATE_SCHEMA,
        "exists": True,
        "enabled_groups": list(enabled_groups) if isinstance(enabled_groups, list) else [],
        "group_values": dict(group_values) if isinstance(group_values, dict) else {},
    }


def save_env_switch_state(
    *,
    work_root: str | os.PathLike[str],
    policy: dict[str, Any],
    machine_id: str,
    enabled_groups: list[Any],
    group_values: dict[str, Any] | None = None,
    operator_open_id: str = "",
    operation_id: str = "",
) -> dict[str, Any]:
    machine_id = str(machine_id or "").strip()
    if not machine_id:
        raise MachineConfigPolicyError("machine_id is required")
    schema = env_switch_schema(policy)
    normalized = normalize_env_switch_state(schema, enabled_groups, group_values or {})
    state = load_machine_config_state(work_root)
    machines = dict(state.get("machines") or {})
    current = machines.get(machine_id) if isinstance(machines.get(machine_id), dict) else {}
    next_record = dict(current)
    next_record.update({
        "enabled_groups": normalized["enabled_groups"],
        "group_values": normalized["group_values"],
        "operator_open_id": operator_open_id,
        "operation_id": operation_id,
    })
    machines[machine_id] = next_record
    changed_groups = sorted(set(normalized["enabled_groups"]) ^ set(current.get("enabled_groups") or []))
    current_values = current.get("group_values") if isinstance(current.get("group_values"), dict) else {}
    changed_values = sorted(
        key for key in set(normalized["group_values"]) | set(current_values)
        if current_values.get(key) != normalized["group_values"].get(key)
    )
    _write_json_atomic(
        machine_config_state_path(work_root),
        {"schema": STATE_SCHEMA, "machines": machines},
        mode=0o600,
    )
    return {
        "schema": "intern-agents.env-switch-save-result.v1",
        "ok": True,
        "state_path": os.fspath(machine_config_state_path(work_root)),
        "machine_id": machine_id,
        "enabled_groups": normalized["enabled_groups"],
        "group_values": normalized["group_values"],
        "changed_groups": changed_groups,
        "changed_values": changed_values,
    }


def normalize_env_switch_state(
    schema: dict[str, Any],
    enabled_groups: list[Any],
    group_values: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(enabled_groups, list):
        raise MachineConfigPolicyError("enabled_groups must be a list")
    if not isinstance(group_values, dict):
        raise MachineConfigPolicyError("group_values must be an object")
    group_map = _env_switch_group_map(schema)
    normalized_enabled: list[str] = []
    normalized_values: dict[str, dict[str, str]] = {}
    for raw_key in enabled_groups:
        key = str(raw_key or "").strip()
        if not key:
            continue
        group = group_map.get(key)
        if not group:
            raise MachineConfigPolicyError(f"unknown env_switch group: {key}")
        if key not in normalized_enabled:
            normalized_enabled.append(key)
    for raw_key, raw_values in group_values.items():
        key = str(raw_key or "").strip()
        if not key:
            continue
        group = group_map.get(key)
        if not group:
            raise MachineConfigPolicyError(f"unknown env_switch group: {key}")
        values = _normalized_group_values(group, raw_values if isinstance(raw_values, dict) else {})
        if values:
            normalized_values[key] = values
    for key in normalized_enabled:
        if key in normalized_values:
            continue
        group = group_map.get(key)
        if not group:
            continue
        values = _normalized_group_values(group, {})
        if values:
            normalized_values[key] = values
    return {
        "schema": ENV_SWITCH_STATE_SCHEMA,
        "enabled_groups": normalized_enabled,
        "group_values": normalized_values,
    }


def policy_with_env_switch_state(
    *,
    work_root: str | os.PathLike[str],
    policy: dict[str, Any],
    machine_id: str,
) -> dict[str, Any]:
    switch_state = env_switch_state_for_machine(work_root, machine_id)
    return policy_with_env_switches(
        policy=policy,
        enabled_groups=switch_state.get("enabled_groups", []) if switch_state.get("exists") else None,
        group_values=switch_state.get("group_values", {}),
    )


def policy_with_env_switches(
    *,
    policy: dict[str, Any],
    enabled_groups: list[Any] | None = None,
    group_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    switch_schema = env_switch_schema(policy)
    switch_state = normalize_env_switch_state(
        switch_schema,
        _default_enabled_switch_keys(switch_schema) if enabled_groups is None else enabled_groups,
        group_values or {},
    )
    if not switch_state["enabled_groups"]:
        return policy
    effective = deepcopy(policy)
    for group_key in _ordered_enabled_switch_keys(switch_schema, switch_state["enabled_groups"]):
        group = _env_switch_group_map(switch_schema).get(group_key)
        if not group:
            continue
        patch = group.get("policy_patch") if isinstance(group.get("policy_patch"), dict) else None
        if isinstance(patch, dict):
            values = switch_state["group_values"].get(group_key, {})
            errors = _group_validation_errors(group, values)
            if errors:
                raise MachineConfigPolicyError(f"invalid env_switch group {group_key}: {'; '.join(errors)}")
            effective = _deep_merge_policy(effective, _render_policy_patch(patch, values))
    return effective


def env_switch_report(
    *,
    policy: dict[str, Any],
    work_root: str | os.PathLike[str],
    machine_id: str,
) -> dict[str, Any]:
    schema = env_switch_schema(policy)
    state = env_switch_state_for_machine(work_root, machine_id)
    default_enabled = _default_enabled_switch_keys(schema) if not state.get("exists") else []
    enabled_groups = _ordered_enabled_switch_keys(
        schema,
        [*default_enabled, *state.get("enabled_groups", [])],
    )
    group_values = state.get("group_values") if isinstance(state.get("group_values"), dict) else {}
    available_groups = []
    invalid_groups = []
    for group in schema.get("groups") or []:
        if not isinstance(group, dict):
            continue
        key = str(group.get("key") or "").strip()
        if not key:
            continue
        description = str(group.get("description") or "").strip()
        values = _normalized_group_values(group, group_values.get(key) if isinstance(group_values.get(key), dict) else {})
        enabled = key in enabled_groups
        errors = _group_validation_errors(group, values) if enabled else []
        if not description:
            errors.append("description is required")
        item = {
            "key": key,
            "title": str(group.get("title") or group.get("label") or key),
            "description": description,
            "enabled": enabled,
            "default_enabled": bool(group.get("default_enabled")),
            "enable_codex": bool(group.get("enable_codex")),
            "enable_claude": bool(group.get("enable_claude")),
            "fields": _redacted_group_fields(group, values),
            "valid": not errors,
            "errors": errors,
        }
        available_groups.append(item)
        if errors:
            invalid_groups.append({"key": key, "errors": errors})
    codex_groups = [item["key"] for item in available_groups if item["enabled"] and item["enable_codex"] and item["valid"]]
    claude_groups = [item["key"] for item in available_groups if item["enabled"] and item["enable_claude"] and item["valid"]]
    return {
        "schema": ENV_SWITCH_SCHEMA,
        "machine_id": machine_id,
        "available_groups": available_groups,
        "enabled_groups": enabled_groups,
        "codex_groups": codex_groups,
        "claude_groups": claude_groups,
        "invalid_groups": invalid_groups,
    }


def _env_switch_group_map(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for group in schema.get("groups") or []:
        if not isinstance(group, dict):
            continue
        key = str(group.get("key") or "").strip()
        if key:
            result[key] = group
    return result


def _ordered_enabled_switch_keys(schema: dict[str, Any], enabled_groups: list[Any]) -> list[str]:
    enabled = {str(item or "").strip() for item in enabled_groups if str(item or "").strip()}
    ordered = []
    for group in schema.get("groups") or []:
        if not isinstance(group, dict):
            continue
        key = str(group.get("key") or "").strip()
        if key and key in enabled and key not in ordered:
            ordered.append(key)
    return ordered


def _default_enabled_switch_keys(schema: dict[str, Any]) -> list[str]:
    result = []
    for group in schema.get("groups") or []:
        if not isinstance(group, dict):
            continue
        key = str(group.get("key") or "").strip()
        if key and group.get("default_enabled") is True:
            result.append(key)
    return result


def _normalized_group_values(group: dict[str, Any], raw_values: dict[str, Any]) -> dict[str, str]:
    raw_values = raw_values if isinstance(raw_values, dict) else {}
    values: dict[str, str] = {}
    for field in group.get("fields") or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        if key in raw_values:
            values[key] = str(raw_values.get(key) or "")
        elif "default" in field:
            values[key] = str(field.get("default") or "")
    return values


def _group_validation_errors(group: dict[str, Any], values: dict[str, str]) -> list[str]:
    errors: list[str] = []
    for field in group.get("fields") or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            errors.append("field key is required")
            continue
        value = values.get(key, "")
        if field.get("required") is True and not value:
            errors.append(f"{key} is required")
        field_type = str(field.get("type") or "string")
        if value and field_type == "env_name" and not _is_env_name(value):
            errors.append(f"{key} must be an environment variable name")
    return errors


def _redacted_group_fields(group: dict[str, Any], values: dict[str, str]) -> list[dict[str, Any]]:
    result = []
    for field in group.get("fields") or []:
        if not isinstance(field, dict):
            continue
        key = str(field.get("key") or "").strip()
        if not key:
            continue
        result.append({
            "key": key,
            "type": str(field.get("type") or "string"),
            "label": str(field.get("label") or field.get("title") or key),
            "description": str(field.get("description") or ""),
            "required": bool(field.get("required")),
            "value": values.get(key, ""),
            "secret": bool(field.get("secret")),
        })
    return result


def _render_policy_patch(patch: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    def render(value: Any) -> Any:
        if isinstance(value, str):
            rendered = value
            for key, replacement in values.items():
                rendered = rendered.replace("{{" + key + "}}", replacement)
            return rendered
        if isinstance(value, list):
            return [render(item) for item in value]
        if isinstance(value, dict):
            return {str(k): render(v) for k, v in value.items()}
        return deepcopy(value)
    return render(patch)


def _is_env_name(value: str) -> bool:
    if not value:
        return False
    first = value[0]
    return (first == "_" or "A" <= first <= "Z") and all(
        char == "_" or "A" <= char <= "Z" or "0" <= char <= "9" for char in value
    )


def _deep_merge_policy(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in patch.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = _deep_merge_policy(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _write_json_atomic(path: Path, data: dict[str, Any], *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.chmod(mode)
    tmp.replace(path)
    path.chmod(mode)
