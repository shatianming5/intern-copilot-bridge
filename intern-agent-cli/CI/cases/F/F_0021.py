from typing import Any
from CI.assertions import source_contract as source_contract_assertions
from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0021_workspace_disable_delete_gui_contract",
    name="Workspace disable vs delete GUI contract",
    description=(
        "Existing-deployment debug validation for VS Code TreeView Remove Workspace versus "
        "Stop Maintaining Project contracts, including cancel, typed-name mismatch, axis "
        "workspace normal routing, CLI equivalents, registry side effects, and repo preservation."
    ),
    stage="remote",
    timeout_seconds=600,
    kind="f_workspace_disable_delete_gui_contract",
    tags=("F", "workspace", "treeview", "gui", "debug", "existing_deployment"),
    parallel_safe=True,
    extra={
        "ci_stage": "F",
        "actions": (
            "workspace.namespace_reset",
            "workspace.create",
            "gui.workspace.remove",
            "gui.workspace.stop_maintain",
            "collect_artifacts",
            "export_report",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0021", "mode": "exclusive"},
            {"resource": "extension_bundle:treeview_contract", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "repo:codeup:nonprotected-test", "mode": "read"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0021_delete", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0021_disable", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:treeview_contract",
            "workspace_metadata:ci_f_0021_disable",
            "workspace_metadata:ci_f_0021_delete",
            "codeup-repo:nonprotected-test",
            "artifact:ci_f_0021",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.workspace_disable_delete_gui_consistent",),
        "scenario_ids": (
            "F_0021.s01_reset_case_namespace",
            "F_0021.s02_create_disable_workspace",
            "F_0021.s03_create_delete_workspace",
            "F_0021.s04_wait_disable_visible",
            "F_0021.s05_wait_delete_visible",
            "F_0021.s06_cancel_remove_workspace",
            "F_0021.s07_cancel_remove_no_disable_cli",
            "F_0021.s08_cancel_remove_workspace_still_enabled",
            "F_0021.s09_confirm_remove_workspace",
            "F_0021.s10_remove_maps_to_workspace_disable",
            "F_0021.s11_wait_disable_state",
            "F_0021.s12_disabled_workspace_hidden",
            "F_0021.s13_disabled_registry_definition_retained",
            "F_0021.s14_cancel_stop_maintain",
            "F_0021.s15_cancel_stop_definition_retained",
            "F_0021.s16_stop_typed_name_mismatch",
            "F_0021.s17_typed_mismatch_no_delete_cli",
            "F_0021.s18_typed_mismatch_workspace_still_enabled",
            "F_0021.s19_confirm_stop_maintain",
            "F_0021.s20_stop_maps_to_workspace_delete",
            "F_0021.s21_wait_workspace_removed",
            "F_0021.s22_deleted_state_removed",
            "F_0021.s23_workspace_repo_preserved",
            "F_0021.s24_axis_remove_uses_workspace_disable",
            "F_0021.s25_axis_remove_retains_workspace_definition",
            "F_0021.s26_axis_typed_mismatch_has_no_delete_cli",
            "F_0021.s27_axis_stop_uses_workspace_delete",
            "F_0021.s28_axis_workspace_repo_preserved",
        ),
        "notes": (
            "This case is loaded from intern-cli/CI/cases/F.",
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, restart relay, allocate Feishu groups, or run an LLM session.",
            "GUI modal/handler semantics are verified from the deployed extension bundle; workspace side effects are verified with deployed internctl on debug machines.",
            "Remove Workspace uses a local_only workspace and validates local disable; Stop Maintaining Project uses a Codeup workspace and validates global delete.",
        ),
    },
)


def run_f_workspace_disable_delete_gui_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def visible(workspace_key: str, label: str, expected: bool = True) -> dict[str, Any]:
        workspace = state[workspace_key]
        entry = self.ctx.action.workspace.entry_remote(workspace, name=label)
        actual = entry is not None and workspace_assertions.workspace_local_enabled(entry)
        self.require(label.replace(" ", "_"), actual is expected, {"workspace": workspace, "entry": entry, "expected": expected})
        return {"workspace": workspace, "entry": entry, "visible": actual, "tree_visibility_inference": "deployed TreeView uses getEnabledProjects"}

    def s01_reset_case_namespace() -> dict[str, Any]:
        return {"case_initial_reset": self.artifacts.get("case_initial_reset", {})}

    def s02_create_disable_workspace() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0021_disable")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="disable", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
        state["disable_repo"] = repo
        state["disable_workspace"] = workspace
        return {"workspace": workspace, "repo": str(repo)}

    def s03_create_delete_workspace() -> dict[str, Any]:
        repo = self.ctx.action.workspace.nonprotected_repo_remote()
        baseline = self.ctx.action.workspace.git_default_head_remote(repo, name="F_0021 delete workspace repo baseline")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="delete", provider="codeup", repo_url=repo, mode="repo_dotdir")
        state["delete_repo"] = repo
        state["delete_baseline"] = baseline
        state["delete_workspace"] = workspace
        return {"workspace": workspace, "repo": repo, "baseline": baseline}

    def s06_cancel_remove_workspace() -> dict[str, Any]:
        state["dist_contract"] = source_contract_assertions.require_deployed_contract(self, "f0021_workspace_disable_delete_gui", "f0021_deployed_gui_source_contract")
        return {"cancel_path": "deployed bundle requires modal confirmation before workspace disable CLI", "source_contract": state["dist_contract"]}

    def s09_confirm_remove_workspace() -> dict[str, Any]:
        workspace = state["disable_workspace"]
        result = self.json_cmd("F_0021 workspace disable", [*self.internctl, "workspace", "disable", str(workspace["workspace_id"]), "--json"], timeout=120)
        state["disable_result"] = result
        return {"disable": result}

    def s13_disabled_registry_definition_retained() -> dict[str, Any]:
        workspace = state["disable_workspace"]
        entry = self.ctx.action.workspace.entry_remote(workspace, name="F_0021 disabled workspace retained")
        self.require("f0021_disabled_workspace_definition_retained", entry is not None and not workspace_assertions.workspace_local_enabled(entry), {"workspace": workspace, "entry": entry})
        return {"workspace": workspace, "entry": entry}

    def s19_confirm_stop_maintain() -> dict[str, Any]:
        workspace = state["delete_workspace"]
        deleted = self.ctx.action.workspace.delete_remote(workspace)
        state["delete_result"] = deleted
        return {"delete": deleted}

    def s21_wait_workspace_removed() -> dict[str, Any]:
        workspace = state["delete_workspace"]
        entry = self.ctx.action.workspace.entry_remote(workspace, name="F_0021 deleted workspace absent")
        self.require("f0021_deleted_workspace_absent", entry is None, {"workspace": workspace, "entry": entry})
        return {"workspace": workspace, "entry": entry}

    def s23_workspace_repo_preserved() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.business_branch_unchanged_checks_remote(
                state["delete_baseline"],
                label="F_0021 delete workspace remote repo preserved",
            ),
        )

    def source_contract() -> dict[str, Any]:
        return {"source_contract": state.get("dist_contract") or source_contract_assertions.require_deployed_contract(self, "f0021_workspace_disable_delete_gui", "f0021_deployed_gui_source_contract")}

    self.run_ordered_scenarios([
        ("F_0021.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0021.s02_create_disable_workspace", s02_create_disable_workspace),
        ("F_0021.s03_create_delete_workspace", s03_create_delete_workspace),
        ("F_0021.s04_wait_disable_visible", lambda: visible("disable_workspace", "F_0021 disable workspace visible", True)),
        ("F_0021.s05_wait_delete_visible", lambda: visible("delete_workspace", "F_0021 delete workspace visible", True)),
        ("F_0021.s06_cancel_remove_workspace", s06_cancel_remove_workspace),
        ("F_0021.s07_cancel_remove_no_disable_cli", source_contract),
        ("F_0021.s08_cancel_remove_workspace_still_enabled", lambda: visible("disable_workspace", "F_0021 cancel remove still enabled", True)),
        ("F_0021.s09_confirm_remove_workspace", s09_confirm_remove_workspace),
        ("F_0021.s10_remove_maps_to_workspace_disable", source_contract),
        ("F_0021.s11_wait_disable_state", lambda: visible("disable_workspace", "F_0021 disabled state", False)),
        ("F_0021.s12_disabled_workspace_hidden", lambda: visible("disable_workspace", "F_0021 disabled tree hidden", False)),
        ("F_0021.s13_disabled_registry_definition_retained", s13_disabled_registry_definition_retained),
        ("F_0021.s14_cancel_stop_maintain", source_contract),
        ("F_0021.s15_cancel_stop_definition_retained", lambda: visible("delete_workspace", "F_0021 cancel stop retained", True)),
        ("F_0021.s16_stop_typed_name_mismatch", source_contract),
        ("F_0021.s17_typed_mismatch_no_delete_cli", source_contract),
        ("F_0021.s18_typed_mismatch_workspace_still_enabled", lambda: visible("delete_workspace", "F_0021 typed mismatch still enabled", True)),
        ("F_0021.s19_confirm_stop_maintain", s19_confirm_stop_maintain),
        ("F_0021.s20_stop_maps_to_workspace_delete", source_contract),
        ("F_0021.s21_wait_workspace_removed", s21_wait_workspace_removed),
        ("F_0021.s22_deleted_state_removed", s21_wait_workspace_removed),
        ("F_0021.s23_workspace_repo_preserved", s23_workspace_repo_preserved),
        ("F_0021.s24_core_remove_rejected", source_contract),
        ("F_0021.s25_core_remove_warning", source_contract),
        ("F_0021.s26_core_stop_rejected", source_contract),
        ("F_0021.s27_core_stop_no_delete_cli", source_contract),
        ("F_0021.s28_core_workspace_still_enabled", source_contract),
    ])
