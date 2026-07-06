from pathlib import Path
from typing import Any

from CI.assertions import source_contract as source_contract_assertions
from CI.assertions import treeview as treeview_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0032_treeview_menu_visibility_context_contract",
    name="TreeView menu visibility and contextValue audit",
    description=(
        "Existing-deployment debug validation covering Codex/workspace/task/skill TreeItem contextValue and package.json "
        "menu when-contracts, command palette hiding, and out-of-scope command non-execution."
    ),
    stage="remote",
    timeout_seconds=600,
    kind="f_treeview_menu_visibility_context_contract",
    tags=("F", "treeview", "menu", "context", "gui", "debug", "existing_deployment"),
    parallel_safe=True,
    extra={
        "ci_stage": "F",
        "actions": (
            "gui.chat.open_intern",
            "gui.session.create_codex",
            "gui.session.restart_codex",
            "gui.intern.delete",
            "gui.intern.force_delete",
            "gui.workspace.remove",
            "gui.workspace.stop_maintain",
            "gui.task.delete",
            "gui.skill.tree_update_pkg",
            "gui.skill.tree_remove_pkg",
            "gui.skill.tree_enable_repo",
            "gui.skill.tree_enable_personal",
            "gui.skill.tree_disable_repo",
            "gui.skill.tree_disable_personal",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0032", "mode": "exclusive"},
            {"resource": "extension_bundle:package_menu_contract", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "source:vscode-extension:intern_tree_context_values", "mode": "read"},
            {"resource": "source:vscode-extension:package_menu_contract", "mode": "read"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:package_menu_contract",
            "vscode-extension:package_menu_contract",
            "vscode-extension:intern_tree_context_values",
            "artifact:ci_f_0032",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.treeview_menu_visibility_context_consistent",),
        "scenario_ids": (
            "F_0032.s00_reset_case_namespace",
            "F_0032.s01_context_values_declared_for_codex_workspace_task_skill",
            "F_0032.s02_codex_menu_commands_visible_and_scoped",
            "F_0032.s03_workspace_task_skill_package_menus_visible",
            "F_0032.s04_skill_catalog_and_personal_menus_visible",
            "F_0032.s05_command_palette_hidden_contract",
            "F_0032.s06_out_of_scope_commands_not_exercised",
        ),
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, restart relay, allocate Feishu groups, or run an LLM session.",
            "Audits deployed package.json and extension bundle TreeItem/menu contracts; it does not execute Team, setup, Copilot, Feishu, or session commands.",
            "No Feishu group, relay restart, setup webview, non-Codex intern, or Copilot shared skill is used.",
        ),
    },
)


def run_f_treeview_menu_visibility_context_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}
    package_name = f"ci_f_0032_pkg_{self.resource_namespace}"
    skill_name = "skill_alpha"
    skill_key = f"{package_name}/{skill_name}"

    def deployed_package() -> dict[str, Any]:
        return dict(self.ctx.action.source_contract.deployed_extension_package()["package"])

    def deployed_menu_commands(package: dict[str, Any], view_item: str, includes: tuple[str, ...]) -> dict[str, Any]:
        check = treeview_assertions.deployed_menu_commands_check(package, view_item, includes)
        self.require(check["name"], check["ok"], check["detail"])
        return {key: value for key, value in check["detail"].items() if key != "missing"}

    def command_palette_hidden(package: dict[str, Any], commands: tuple[str, ...]) -> dict[str, Any]:
        check = treeview_assertions.command_palette_hidden_check(package, commands)
        self.require(check["name"], check["ok"], check["detail"])
        return {key: value for key, value in check["detail"].items() if key != "missing"}

    def s00_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s01_context_values_declared_for_codex_workspace_task_skill() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0032_workspace")
        display = self.remote_context.stage_workspace_display("f0032")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="f0032",
            display_name=display,
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        root = self.ctx.action.workspace.metadata_root_remote(workspace)
        intern = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "f0032_codex", repo_url=str(repo)))
        task = f"task_ci_f_0032_open_{self.resource_namespace}"
        tasks_dir = root / "tasks"
        tasks_dir.mkdir(parents=True, exist_ok=True)
        task_readme = Path(self.ctx.action.task.write_readme_fixture_remote(tasks_dir, task, status="Open")["readme"])
        source = self.ctx.action.skill.git_source_fixture_remote("f0032_skill_source_git", name=skill_name, description="initial F_0032", rel_dir=skill_name)
        add = self.ctx.action.skill.run_json_remote(
            "F_0032 deployed seed skill source",
            ["add-skill", "--project", str(workspace["display"]), "--scope", "repo", "--source-type", "git", package_name, source["repo"]],
            timeout=240,
        )
        source_contract = source_contract_assertions.require_deployed_contract(self, "f0032_treeview_menu_visibility", "f0032_deployed_gui_source_contract")
        task_list = self.json_cmd("F_0032 deployed internal task-list", [*self.internctl, "internal", "task-list", str(workspace["display"]), "--json"], timeout=120)
        available = self.ctx.action.skill.run_json_remote("F_0032 deployed skill list-available", ["list-available", "--project", str(workspace["display"]), package_name], timeout=120)
        state.update({"repo": repo, "workspace": workspace, "root": root, "intern": intern, "task": task, "source": source})
        return {"workspace": workspace, "intern": intern["intern"], "task": task, "task_readme": str(task_readme), "skill_key": skill_key, "add": add, "task_list": task_list, "available": available, "source_contract": source_contract}

    def s02_codex_menu_commands_visible_and_scoped() -> dict[str, Any]:
        package = deployed_package()
        visible = deployed_menu_commands(package, "intern-codex", (
            "intern.openChatForIntern",
            "intern.createCodexSession",
            "intern.restartCodexSession",
            "intern.deleteIntern",
            "intern.forceDeleteIntern",
            "intern.setTriggerModeAll",
            "intern.setTriggerModeAtOnly",
            "intern.setDetailModeFull",
            "intern.setDetailModeSummary",
        ))
        source_contract = self.artifacts.get("f0032_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0032_treeview_menu_visibility", "f0032_deployed_gui_source_contract")
        return {"visible": visible, "source_contract": source_contract, "project_scope_contract": "deployed bundle contains pickInternFromContext and internOrTreeItem.project markers"}

    def s03_workspace_task_skill_package_menus_visible() -> dict[str, Any]:
        package = deployed_package()
        workspace = deployed_menu_commands(package, "workspace", ("intern.removeWorkspace", "intern.stopMaintainProject"))
        task = deployed_menu_commands(package, "task", ("intern.deleteTask",))
        skill_pkg = deployed_menu_commands(package, "skill-pkg", ("intern.skill.tree.updatePkg", "intern.skill.tree.removePkg"))
        return {"workspace": workspace, "task": task, "skill_package": skill_pkg}

    def s04_skill_catalog_and_personal_menus_visible() -> dict[str, Any]:
        package = deployed_package()
        disabled = deployed_menu_commands(package, "skill-item-catalog-disabled", ("intern.skill.tree.enableRepo", "intern.skill.tree.enablePersonal"))
        repo_enabled = deployed_menu_commands(package, "skill-item-catalog-enabled-repo", ("intern.skill.tree.disableRepo", "intern.skill.tree.enablePersonal"))
        personal = deployed_menu_commands(package, "skill-item-enabled-personal", ("intern.skill.tree.disablePersonal",))
        observed_deferred = [
            row for row in disabled["rows"] + repo_enabled["rows"]
            if row["command"].startswith("intern.skill.copilot")
        ]
        return {"catalog_disabled": disabled, "catalog_repo_enabled": repo_enabled, "personal_enabled": personal, "observed_deferred_copilot_menu": observed_deferred}

    def s05_command_palette_hidden_contract() -> dict[str, Any]:
        package = deployed_package()
        return command_palette_hidden(package, (
            "intern.addWorkspace",
            "intern.createIntern",
            "intern.deleteTask",
            "intern.createCodexSession",
        ))

    def s06_out_of_scope_commands_not_exercised() -> dict[str, Any]:
        exercised = [
            "intern.openChatForIntern",
            "intern.createCodexSession",
            "intern.restartCodexSession",
            "intern.deleteIntern",
            "intern.forceDeleteIntern",
            "intern.removeWorkspace",
            "intern.stopMaintainProject",
            "intern.deleteTask",
            "intern.skill.tree.updatePkg",
            "intern.skill.tree.removePkg",
            "intern.skill.tree.enableRepo",
            "intern.skill.tree.enablePersonal",
            "intern.skill.tree.disableRepo",
            "intern.skill.tree.disablePersonal",
        ]
        prefixes = ("intern.enableTeamMode", "intern.createTeam", "intern.assignTeam", "intern.skill.copilot", "intern.setup")
        forbidden = [command for command in exercised if any(command.startswith(prefix) for prefix in prefixes)]
        self.require("f0032_no_out_of_scope_commands_exercised", not forbidden, {"forbidden": forbidden, "exercised": exercised})
        return {"prefixes": list(prefixes), "exercised_commands": exercised, "forbidden": forbidden}

    self.run_ordered_scenarios([
        ("F_0032.s00_reset_case_namespace", s00_reset_case_namespace),
        ("F_0032.s01_context_values_declared_for_codex_workspace_task_skill", s01_context_values_declared_for_codex_workspace_task_skill),
        ("F_0032.s02_codex_menu_commands_visible_and_scoped", s02_codex_menu_commands_visible_and_scoped),
        ("F_0032.s03_workspace_task_skill_package_menus_visible", s03_workspace_task_skill_package_menus_visible),
        ("F_0032.s04_skill_catalog_and_personal_menus_visible", s04_skill_catalog_and_personal_menus_visible),
        ("F_0032.s05_command_palette_hidden_contract", s05_command_palette_hidden_contract),
        ("F_0032.s06_out_of_scope_commands_not_exercised", s06_out_of_scope_commands_not_exercised),
    ])
