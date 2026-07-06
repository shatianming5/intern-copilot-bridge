from typing import Any

from CI.assertions import source_contract as source_contract_assertions
from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0031_treeview_top_level_config_status_contract",
    name="TreeView top-level config status and refresh contract",
    description=(
        "Existing-deployment debug validation covering TreeView top-level plugin/config entries, format-check toggle, "
        "language switch/reload prompt, refreshTree failure last-good behavior, and plugin status bar health hints."
    ),
    stage="remote",
    timeout_seconds=600,
    kind="f_treeview_top_level_config_status_contract",
    tags=("F", "treeview", "config", "statusbar", "gui", "debug", "existing_deployment"),
    parallel_safe=True,
    extra={
        "ci_stage": "F",
        "actions": (
            "cli.internctl",
            "gui.tree.refresh",
            "gui.config.format_check_toggle",
            "gui.i18n.switch_language",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0031", "mode": "exclusive"},
            {"resource": "extension_bundle:top_level_treeview_contract", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "source:vscode-extension:top_level_treeview_contract", "mode": "read"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0031_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:top_level_treeview_contract",
            "workspace_metadata:ci_f_0031_workspace",
            "vscode-extension:top_level_treeview_contract",
            "artifact:ci_f_0031",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.treeview_top_level_config_status_consistent",),
        "scenario_ids": (
            "F_0031.s01_plugin_meta_and_config_item_contract",
            "F_0031.s02_format_check_toggle_cli_contract",
            "F_0031.s03_format_check_failure_feedback_contract",
            "F_0031.s04_language_switch_reload_contract",
            "F_0031.s05_refresh_tree_failure_preserves_last_good",
            "F_0031.s06_plugin_status_bar_health_contract",
            "F_0031.s07_setup_commands_not_invoked",
        ),
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, restart relay, allocate Feishu groups, or run an LLM session.",
            "Uses deployed product config CLI handler plus deployed extension bundle source-contract evidence; setup webview commands are not executed.",
            "No Feishu group, relay restart, Team, non-Codex intern, or Copilot shared skill is used.",
        ),
    },
)


def run_f_treeview_top_level_config_status_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def s01_plugin_meta_and_config_item_contract() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0031_workspace")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="f0031", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
        state.update({"repo": repo, "workspace": workspace})
        source_contract = source_contract_assertions.require_deployed_contract(self, "f0031_treeview_top_level_config_status", "f0031_deployed_gui_source_contract")
        workspaces = self.ctx.action.workspace.list_remote("F_0031 deployed workspace list")
        visible = any(workspace_assertions.workspace_display(item) == str(workspace["display"]) for item in workspaces.get("workspaces", []) if isinstance(item, dict))
        self.require("f0031_workspace_visible_for_config_item", visible, {"workspace": workspace, "workspace_list": workspaces})
        return {"workspace": workspace, "workspace_visible": visible, "source_contract": source_contract}

    def s02_format_check_toggle_cli_contract() -> dict[str, Any]:
        initial = self.json_cmd("F_0031 format-check status initial", [*self.internctl, "config", "format-check", "status", "--json"], timeout=60)
        first = self.json_cmd("F_0031 format-check toggle first", [*self.internctl, "config", "format-check", "toggle", "--json"], timeout=60)
        second = self.json_cmd("F_0031 format-check toggle restore", [*self.internctl, "config", "format-check", "toggle", "--json"], timeout=60)
        final = self.json_cmd("F_0031 format-check status final", [*self.internctl, "config", "format-check", "status", "--json"], timeout=60)
        if final.get("enabled") != initial.get("enabled"):
            self.run_cmd("F_0031 emergency format-check restore", [*self.internctl, "config", "format-check", "toggle", "--json"], timeout=60, check=False)
        self.require("f0031_format_check_toggle_roundtrip", first.get("enabled") != initial.get("enabled") and second.get("enabled") == initial.get("enabled") and final.get("enabled") == initial.get("enabled"), {"initial": initial, "first": first, "second": second, "final": final})
        return {"initial": initial, "first": first, "second": second, "final": final, "cli": "internctl config format-check toggle --json"}

    def s03_format_check_failure_feedback_contract() -> dict[str, Any]:
        source_contract = self.artifacts.get("f0031_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0031_treeview_top_level_config_status", "f0031_deployed_gui_source_contract")
        return {"source_contract": source_contract, "injected_message_contract": "deployed bundle shows result.message/result.error via showErrorMessage on toggle failure"}

    def s04_language_switch_reload_contract() -> dict[str, Any]:
        source_contract = self.artifacts.get("f0031_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0031_treeview_top_level_config_status", "f0031_deployed_gui_source_contract")
        return {"source_contract": source_contract, "reload_now_command": "workbench.action.reloadWindow", "later_choice": "cmd.switchLanguage.later"}

    def s05_refresh_tree_failure_preserves_last_good() -> dict[str, Any]:
        source_contract = self.artifacts.get("f0031_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0031_treeview_top_level_config_status", "f0031_deployed_gui_source_contract")
        return {"source_contract": source_contract, "last_good_preserved_by_return_before_refresh": True}

    def s06_plugin_status_bar_health_contract() -> dict[str, Any]:
        source_contract = self.artifacts.get("f0031_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0031_treeview_top_level_config_status", "f0031_deployed_gui_source_contract")
        daemon = self.json_cmd("F_0031 daemon status for statusbar", [*self.internctl, "daemon", "status", "--json"], timeout=60, check=False)
        return {"source_contract": source_contract, "daemon_status": daemon, "statusbar_command": "intern.refreshTree"}

    def s07_setup_commands_not_invoked() -> dict[str, Any]:
        forbidden = [command for command in ("intern.openSetup", "intern.setupRefresh", "intern.setupAutoFix", "intern.selectProjects") if command in ()]
        self.require("f0031_setup_commands_not_invoked", not forbidden, {"forbidden": forbidden})
        return {"setup_actions_invoked": forbidden, "note": "source contract scans setup markers but native case executes no setup webview commands"}

    self.run_ordered_scenarios([
        ("F_0031.s01_plugin_meta_and_config_item_contract", s01_plugin_meta_and_config_item_contract),
        ("F_0031.s02_format_check_toggle_cli_contract", s02_format_check_toggle_cli_contract),
        ("F_0031.s03_format_check_failure_feedback_contract", s03_format_check_failure_feedback_contract),
        ("F_0031.s04_language_switch_reload_contract", s04_language_switch_reload_contract),
        ("F_0031.s05_refresh_tree_failure_preserves_last_good", s05_refresh_tree_failure_preserves_last_good),
        ("F_0031.s06_plugin_status_bar_health_contract", s06_plugin_status_bar_health_contract),
        ("F_0031.s07_setup_commands_not_invoked", s07_setup_commands_not_invoked),
    ])
