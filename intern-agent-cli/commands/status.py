"""internctl status <name> [--json] — 显示 intern 详情。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from lib.intern_registry import (
    DEFAULT_INTERN_ROLE,
    parse_status_md,
    validate_name,
)
from lib.state_v1 import StateStore, list_state_interns


def _enterprise_status(name: str, project: str) -> dict[str, object] | None:
    store = StateStore()
    matches = [
        item for item in list_state_interns(store, project or None)
        if item.get("name") == name
    ]
    if len(matches) != 1:
        return None
    item = matches[0]
    intern_dir = str(item.get("intern_dir") or "")
    hook_state_path = Path(intern_dir) / ".hook_state.json"
    resolver: dict[str, object] = {}
    try:
        state = json.loads(hook_state_path.read_text(encoding="utf-8"))
        if isinstance(state.get("metadata_resolver"), dict):
            resolver = state["metadata_resolver"]
    except Exception:
        resolver = {}
    meta = parse_status_md(str(resolver.get("status_path") or ""))
    return {
        "name": name,
        "status": meta.get("status", "Unknown"),
        "task": meta.get("task", ""),
        "role": meta.get("role", DEFAULT_INTERN_ROLE),
        "team_id": meta.get("team_id", ""),
        "coordinator_id": "",
        "anchor_project": "",
        "anchor_repo_path": "",
        "type": item.get("type") or "codex",
        "project": item.get("project") or None,
        "workspace_id": item.get("workspace_id") or None,
        "intern_dir": intern_dir or None,
        "hook_state_exists": hook_state_path.is_file(),
    }


def setup_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """注册 status 子命令。"""
    p = subparsers.add_parser("status", help="显示 intern 详情")
    p.add_argument("name", help="intern 名称")
    p.add_argument("--project", default="", help="enterprise 模式下用于消除同名 intern 歧义")
    p.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON 格式")
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    """执行 status 命令。"""
    name: str = args.name

    if not validate_name(name):
        print(f"❌ 名称无效: '{name}'", file=sys.stderr)
        return 1

    info = _enterprise_status(name, args.project or "")
    if info is None:
        print(f"❌ intern '{name}' 不存在", file=sys.stderr)
        return 1

    if args.as_json:
        data = {
            "name": info["name"],
            "status": info["status"],
            "task": info["task"] or None,
            "role": info["role"],
            "team_id": info["team_id"] or None,
            "coordinator_id": info["coordinator_id"] or None,
            "anchor_project": info["anchor_project"] or None,
            "anchor_repo_path": info["anchor_repo_path"] or None,
            "type": info["type"],
            "project": info["project"],
            "workspace_id": info["workspace_id"],
            "intern_dir": info["intern_dir"],
            "hook_state_exists": info["hook_state_exists"],
        }
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print(f"Name:              {info['name']}")
    print(f"Status:            {info['status']}")
    print(f"Role:              {info['role']}")
    print(f"Team:              {info['team_id'] or '-'}")
    if info["project"]:
        print(f"Project:           {info['project']}")
    if info["workspace_id"]:
        print(f"Workspace:         {info['workspace_id']}")
    if info["role"] == "coordinator":
        print(f"Coordinator ID:    {info['coordinator_id'] or '-'}")
        print(f"Anchor project:    {info['anchor_project'] or '-'}")
        print(f"Anchor repo:       {info['anchor_repo_path'] or '-'}")
    print(f"Task:              {info['task'] or '-'}")
    print(f"Hook state file:   {'✓ exists' if info['hook_state_exists'] else '✗ not found'}")

    return 0
