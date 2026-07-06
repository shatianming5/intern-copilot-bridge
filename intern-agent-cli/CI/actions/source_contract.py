from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Any

from CI.assertions import source_contract as source_contract_assertions
from CI.helpers.source_contract_helper import SourceContractHelper
from CI.helpers.native_error import NativeCaseError


@dataclass
class SourceContractActions:
    ctx: Any
    helper: SourceContractHelper = field(init=False)

    def __post_init__(self) -> None:
        self.helper = SourceContractHelper(repo_root=self.ctx.repo_root, work_root=self.ctx.work_root)

    def extension_source_candidates(self, rel_path: str) -> dict[str, Any]:
        candidates = self.helper.extension_source_candidates(rel_path)
        return {
            "rel_path": rel_path,
            "candidates": [str(path) for path in candidates],
        }

    def product_source_evidence(self, rel_path: str, markers: list[str]) -> dict[str, Any]:
        return self.helper.product_source_evidence(rel_path, markers)

    def dist_contract_results(self, checks: list[dict[str, Any]]) -> dict[str, Any]:
        normalized = [(str(item.get("name") or ""), bool(item.get("ok"))) for item in checks]
        return self.helper.dist_contract_results(normalized)

    def deployed_cli_source_text(self, rel_path: str) -> dict[str, Any]:
        evidence = self.helper.deployed_cli_source_text(rel_path)
        return {
            **evidence,
            "text_length": len(str(evidence.get("text") or "")),
        }

    def deployed_extension_dist(self) -> dict[str, Any]:
        try:
            text = self.helper.deployed_extension_dist()
        except FileNotFoundError as exc:
            raise NativeCaseError(str(exc)) from exc
        return {
            "bundle": str(self.helper.extension_bundle_path()),
            "text": text,
            "text_length": len(text),
            "exists": True,
        }

    def deployed_extension_package(self) -> dict[str, Any]:
        path = self.ctx.work_root / "extension" / "package.json"
        if not path.is_file():
            raise NativeCaseError(f"deployed extension package.json missing: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise NativeCaseError(f"deployed extension package.json is not an object: {path}")
        return {
            "path": str(path),
            "package": data,
            "exists": True,
        }

    def deployed_view_item_commands(self, package: dict[str, Any], view_item: str) -> dict[str, Any]:
        menus = package.get("contributes", {}).get("menus", {})
        entries = menus.get("view/item/context", []) if isinstance(menus, dict) else []
        marker = f"viewItem == {view_item}"
        rows: list[dict[str, str]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            when = str(entry.get("when") or "")
            if marker not in when:
                continue
            rows.append({
                "command": str(entry.get("command") or ""),
                "when": when,
                "group": str(entry.get("group") or ""),
            })
        return {
            "view_item": view_item,
            "rows": rows,
            "visible": [row["command"] for row in rows],
        }

    def dist_command_block(self, command: str, *, window: int = 5000) -> dict[str, Any]:
        dist = self.helper.deployed_extension_dist()
        block = self.helper.dist_command_block(dist, command, window=window)
        return {
            "command": command,
            "found": bool(block),
            "block": block,
            "block_length": len(block),
            "bundle": str(self.helper.extension_bundle_path()),
        }

    def deployed_contract_remote(self, contract_id: str) -> dict[str, Any]:
        dist = str(self.deployed_extension_dist().get("text") or "")
        bundle = str(self.helper.extension_bundle_path())
        if contract_id == "f0021_workspace_disable_delete_gui":
            return source_contract_assertions.workspace_disable_delete_gui_dist_contract(dist, bundle=bundle)
        if contract_id == "f0022_workspace_enable_doctor_refresh":
            return source_contract_assertions.workspace_enable_doctor_refresh_dist_contract(dist, bundle=bundle)
        if contract_id == "f0023_task_treeview_projection":
            return source_contract_assertions.task_treeview_projection_dist_contract(dist, bundle=bundle)
        if contract_id == "f0024_task_delete_gui":
            return source_contract_assertions.task_delete_gui_dist_contract(dist, bundle=bundle)
        if contract_id == "f0029_skill_source_treeview":
            return source_contract_assertions.skill_source_treeview_dist_contract(dist, bundle=bundle)
        if contract_id == "f0030_codex_skill_scope":
            return source_contract_assertions.codex_skill_scope_dist_contract(dist, bundle=bundle)
        if contract_id == "f0031_treeview_top_level_config_status":
            return source_contract_assertions.treeview_top_level_config_status_dist_contract(dist, bundle=bundle)
        if contract_id == "f0032_treeview_menu_visibility":
            return source_contract_assertions.treeview_menu_visibility_dist_contract(dist, bundle=bundle)
        if contract_id == "f0044_claude_treeview_command":
            package_evidence = self.deployed_extension_package()
            package = package_evidence["package"]
            claude_rows = self.deployed_view_item_commands(package, "intern-claude")["rows"]
            return source_contract_assertions.claude_treeview_command_dist_contract(
                dist,
                list(claude_rows),
                bundle=bundle,
                package_path=str(package_evidence["path"]),
            )
        if contract_id == "f0045_claude_skill_group":
            package = self.deployed_extension_package()["package"]
            skill_source = self.deployed_cli_source_text("commands/skill.py")
            skill_text = str(skill_source.get("text") or "")
            return source_contract_assertions.claude_skill_group_source_contract(
                dist,
                skill_text=skill_text,
                skill_cli_source={key: value for key, value in skill_source.items() if key not in {"text", "text_length"}},
                claude_group_rows=list(self.deployed_view_item_commands(package, "intern-claude")["rows"]),
                skill_disabled=list(self.deployed_view_item_commands(package, "skill-item-catalog-disabled")["rows"]),
                skill_repo=list(self.deployed_view_item_commands(package, "skill-item-catalog-enabled-repo")["rows"]),
                skill_personal=list(self.deployed_view_item_commands(package, "skill-item-enabled-personal")["rows"]),
                bundle=bundle,
            )
        raise NativeCaseError(f"unsupported deployed source contract id: {contract_id}")
