"""Intern registry helpers for enterprise session/state metadata."""

from __future__ import annotations

import os
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# ── 常量 ──────────────────────────────────────
WORK_AGENTS_ROOT: str = os.environ.get("WORK_AGENTS_ROOT") or os.getcwd()

# intern name 白名单
NAME_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*$")

# 新建 intern 严格前缀：必须 intern_xxx（历史非 intern_ 前缀的 intern 仅读取/删除，不受此校验影响）
NEW_NAME_PATTERN: re.Pattern[str] = re.compile(r"^intern_[a-z0-9_]+$")

# METADATA 行正则  <!-- METADATA:KEY=VALUE,... -->
_METADATA_RE: re.Pattern[str] = re.compile(r"<!--\s*METADATA:(?P<body>.+?)\s*-->")

INTERN_ROLES: tuple[str, ...] = ("independent", "coordinator", "team_lead", "worker")
DEFAULT_INTERN_ROLE = "independent"


@dataclass
class InternInfo:
    """单个 intern 的注册信息。"""

    name: str
    status: str = "Unknown"
    task: str = ""
    role: str = DEFAULT_INTERN_ROLE
    team_id: str = ""
    type: str = "copilot"
    hook_state_exists: bool = False
    coordinator_id: str = ""
    anchor_project: str = ""
    anchor_repo_path: str = ""
    extra: dict[str, str] = field(default_factory=dict)


def validate_name(name: str) -> bool:
    """校验 intern 名称是否合法（用于读取/删除路径，兼容历史名）。"""
    return bool(NAME_PATTERN.match(name))


def validate_new_name(name: str) -> bool:
    """新建 intern 名称校验：必须以 intern_ 开头，仅含小写字母/数字/下划线。

    历史非 intern_ 前缀的 intern（如 cela/bob/yang）仍可通过 validate_name 被读取/删除，
    但新建必须走此严格校验。
    """
    return bool(NEW_NAME_PATTERN.match(name))


def parse_status_md(path: str | Path) -> dict[str, str]:
    """解析 status.md，返回 METADATA 字段字典。

    Returns:
        {"status": "...", "task": "...", "role": "...", "team_id": "..."}  解析失败返回空 dict。
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                m = _METADATA_RE.search(line)
                if m:
                    result: dict[str, str] = {}
                    for pair in m.group("body").split(","):
                        if "=" not in pair:
                            continue
                        key, value = pair.split("=", 1)
                        result[key.strip().lower()] = value.strip()
                    role = result.get("role", DEFAULT_INTERN_ROLE)
                    result["role"] = role if role in INTERN_ROLES else DEFAULT_INTERN_ROLE
                    return result
    except OSError:
        pass
    return {}


def _read_status_table_fields(path: str | Path) -> dict[str, str]:
    fields: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, TypeError):
        return fields
    for line in text.splitlines():
        match = re.match(r"\|\s*([^|]+?)\s*\|\s*([^|]*?)\s*\|", line)
        if not match:
            continue
        label = re.sub(r"\s+", " ", match.group(1).strip().lower())
        value = match.group(2).strip()
        if label in {"branch", "current branch", "当前分支"}:
            fields["branch"] = value
        elif label in {"pr", "current pr", "当前 pr", "当前pr", "pull request"}:
            fields["pr"] = value
    return fields


def _enterprise_sessions_path() -> Path:
    return Path(WORK_AGENTS_ROOT) / ".intern_sessions.json"


def _load_enterprise_sessions() -> dict[str, dict]:
    path = _enterprise_sessions_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, dict)}


def _enterprise_status_path(entry: dict) -> str:
    intern_dir = entry.get("intern_dir") or ""
    if intern_dir:
        state_path = Path(intern_dir) / ".hook_state.json"
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            state = {}
        resolver = state.get("metadata_resolver") if isinstance(state.get("metadata_resolver"), dict) else {}
        status_path = resolver.get("status_path") or ""
        if status_path:
            return str(status_path)
    return ""


def list_enterprise_interns() -> list[InternInfo]:
    result: list[InternInfo] = []
    seen: set[tuple[str, str]] = set()
    for key, entry in sorted(_load_enterprise_sessions().items()):
        intern_dir = str(entry.get("intern_dir") or "")
        intern_name = str(entry.get("intern_name") or key)
        project = str(entry.get("project") or "")
        workspace_id = str(entry.get("workspace_id") or "")
        if not intern_dir or not validate_name(intern_name):
            continue
        identity = (workspace_id or project, intern_name)
        if identity in seen:
            continue
        seen.add(identity)
        status_path = _enterprise_status_path(entry)
        meta = parse_status_md(status_path)
        table_fields = _read_status_table_fields(status_path)
        role = str(entry.get("role") or meta.get("role") or DEFAULT_INTERN_ROLE)
        if role == "helper":
            role = "helper"
        elif role not in INTERN_ROLES:
            role = DEFAULT_INTERN_ROLE
        info = InternInfo(
            name=intern_name,
            status=meta.get("status", "Unknown"),
            task=meta.get("task", ""),
            role=role,
            team_id=meta.get("team_id", ""),
            type=str(entry.get("type") or "copilot"),
            hook_state_exists=os.path.isfile(os.path.join(intern_dir, ".hook_state.json")),
            extra={
                "project": project,
                "workspace_id": workspace_id,
                "intern_dir": intern_dir,
                "session_key": key,
                "status_path": status_path,
                "branch": table_fields.get("branch", ""),
                "pr": table_fields.get("pr", ""),
            },
        )
        result.append(info)
    return result


def list_interns() -> list[InternInfo]:
    """Return enterprise interns discovered from the scoped session registry."""
    return list_enterprise_interns()
