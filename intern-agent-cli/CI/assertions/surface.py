from __future__ import annotations

from typing import Any


def feishu_visible_operation_detail(
    result: dict[str, Any],
    *,
    expected_prefix: str = "[CI模拟]",
    contains: str = "",
) -> dict[str, Any]:
    visible = result.get("visible_message") if isinstance(result, dict) else None
    visible = visible if isinstance(visible, dict) else {}
    text = str(visible.get("text") or "")
    return {
        "ok": bool(text.startswith(expected_prefix) and (not contains or contains in text)),
        "text": text,
        "expected_prefix": expected_prefix,
        "contains": contains,
    }


def feishu_retained_scene_detail(result: dict[str, Any]) -> dict[str, Any]:
    visible = result.get("visible_message") if isinstance(result, dict) else None
    visible = visible if isinstance(visible, dict) else {}
    retained_scene = result.get("retained_scene", visible.get("retained_scene"))
    cleanup_policy = result.get("cleanup_policy", visible.get("cleanup_policy"))
    end_cleanup = result.get("end_cleanup", visible.get("end_cleanup"))
    return {
        "ok": retained_scene is True and cleanup_policy == "start_only" and end_cleanup is False,
        "retained_scene": retained_scene,
        "cleanup_policy": cleanup_policy,
        "end_cleanup": end_cleanup,
    }


def treeview_no_business_prompt_detail(evidence: dict[str, Any]) -> dict[str, Any]:
    business_prompt_sent = bool(evidence.get("business_prompt_sent")) if isinstance(evidence, dict) else False
    return {
        "ok": not business_prompt_sent,
        "business_prompt_sent": business_prompt_sent,
        "event": evidence.get("event") if isinstance(evidence, dict) else "",
        "gui_command": evidence.get("gui_command") if isinstance(evidence, dict) else "",
    }


def treeview_cli_equivalent_detail(evidence: dict[str, Any]) -> dict[str, Any]:
    equivalent = bool(evidence.get("equivalent")) if isinstance(evidence, dict) else False
    return {
        "ok": equivalent,
        "gui_command": evidence.get("gui_command") if isinstance(evidence, dict) else "",
        "expected_cli": evidence.get("expected_cli") if isinstance(evidence, dict) else "",
        "actual_commands": evidence.get("actual_commands") if isinstance(evidence, dict) else [],
    }
