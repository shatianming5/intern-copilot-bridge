from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass
class TreeViewActions:
    ctx: Any

    def _remote(self) -> Any:
        remote = getattr(self.ctx, "remote_context", None)
        if remote is None:
            raise RuntimeError("ctx.action.treeview.* requires RemoteCaseContext")
        return remote

    def item_projection_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        focus_intern: str = "",
    ) -> dict[str, Any]:
        items = self.ctx.action.intern.list_json_remote()
        matches = [
            dict(item) for item in items
            if item.get("name") == intern
            and item.get("workspace_id") == workspace["workspace_id"]
            and item.get("project") == workspace["display"]
        ]
        if len(matches) != 1:
            return {
                "name": intern,
                "project": str(workspace["display"]),
                "workspace_id": str(workspace["workspace_id"]),
                "list_items": items,
                "list_matches": matches,
                "list_match_count": len(matches),
                "status_json": {},
                "session_entries": {},
            }

        item = matches[0]
        status = self.ctx.action.intern.status_json_remote(workspace, intern)
        intern_type = str(item.get("type") or status.get("type") or "codex")
        current_task = str(status.get("task") or item.get("task") or item.get("currentTask") or "")
        current_pr = str(status.get("pr") or item.get("pr") or item.get("currentPR") or "")
        state = str(status.get("status") or item.get("status") or "")
        session_status = (
            self.ctx.action.session.status_for_workspace_remote(workspace, intern, check=False)
            if intern_type in {"claude", "codex"}
            else {}
        )
        online = session_status.get("running") is True
        is_focus = bool(focus_intern and intern == focus_intern)
        if intern_type == "claude" and online:
            product_icon = {"id": "terminal", "color": "charts.green"}
        elif intern_type == "codex" and online:
            product_icon = {"id": "rocket", "color": "charts.blue"}
        elif is_focus:
            product_icon = {"id": "star-full", "color": "charts.yellow"}
        elif intern_type == "copilot" and online:
            product_icon = {"id": "circle-filled", "color": "charts.green"}
        else:
            product_icon = {
                "id": "rocket" if intern_type == "codex" else "terminal" if intern_type == "claude" else "account",
                "color": "",
            }
        context_value = {
            "claude": "intern-claude",
            "codex": "intern-codex",
            "copilot": "intern",
        }.get(intern_type, "intern")
        type_label = {
            "claude": "Claude",
            "codex": "Codex",
            "copilot": "copilot",
        }.get(intern_type, intern_type)
        return {
            "name": intern,
            "project": str(workspace["display"]),
            "workspace_id": str(workspace["workspace_id"]),
            "type": intern_type,
            "state": state,
            "status": state,
            "current_task": current_task,
            "current_pr": current_pr,
            "online": online,
            "focus": is_focus,
            "context_value": context_value,
            "icon": product_icon,
            "description": f"{state}{' | ' + current_task if current_task else ''}",
            "tooltip": {
                "name": intern,
                "type": type_label,
                "project": str(workspace["display"]),
                "state": state,
                "current_task": current_task,
                "current_pr": current_pr,
            },
            "command_args": {"name": intern, "project": str(workspace["display"])},
            "list_item": item,
            "list_items": items,
            "list_matches": matches,
            "list_match_count": len(matches),
            "status_json": status,
            "session_status": session_status,
            "session_entries": self.ctx.action.session.registry_entries_for_remote(workspace, intern),
        }

    def workspace_projection_remote(
        self,
        workspace: dict[str, Any],
        interns: list[str],
        *,
        focus_intern: str = "",
    ) -> dict[str, Any]:
        items = [
            self.item_projection_remote(workspace, intern, focus_intern=focus_intern)
            for intern in interns
        ]
        state_order = {"Working": 0, "Idle": 1}
        items.sort(key=lambda item: (
            0 if item["name"] == focus_intern else 1,
            0 if item.get("online") else 1,
            state_order.get(str(item.get("state") or ""), 3),
            str(item["name"]),
        ))
        active = [item for item in items if item["name"] == focus_intern or item.get("online")]
        inactive = [item for item in items if item not in active]
        return {
            "workspace": workspace,
            "items": items,
            "active_items": active,
            "inactive_items": inactive,
            "inactive_group": {
                "label": f"Other Interns ({len(inactive)})",
                "collapsed": True,
                "items": inactive,
            } if inactive else {},
        }

    def context_menu_commands_remote(self, view_item: str) -> dict[str, Any]:
        remote = self._remote()
        commands: list[str] = []
        source_path = ""
        checked: list[str] = []
        for path in remote.source_contract.extension_source_candidates("package.json"):
            checked.append(str(path))
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            source_path = str(path)
            commands.extend(remote.mock_treeview.context_menu_commands(data, view_item))
            break
        return {
            "view_item": view_item,
            "commands": commands,
            "source_path": source_path,
            "checked_paths": checked,
        }
