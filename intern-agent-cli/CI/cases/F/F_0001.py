from __future__ import annotations
from typing import Any

from CI.assertions import surface as surface_assertions
from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0001_codeup_repo_dotdir_workspace_add_remove",
    name="Codeup repo_dotdir workspace add/remove",
    description="Validate Codeup repo_dotdir workspace add/remove through the GUI-equivalent CLI path with daemon and relay evidence.",
    stage="remote",
    timeout_seconds=1800,
    kind="f_workspace_gui",
    tags=("F", "workspace", "gui", "codeup", "repo_dotdir"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "run_mode": "existing_debug_native_remote",
        "actions": (
            "workspace.reset_case_namespace",
            "gui.workspace.add",
            "workspace.cli_create",
            "workspace.wait_registered",
            "gui.workspace.remove",
            "workspace.cli_delete",
            "workspace.wait_removed",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "native.workspace_gui_cli_equivalent",
            "native.workspace_record",
            "native.workspace_metadata_root",
            "native.relay_workspace_sync",
            "native.workspace_removed",
        ),
        "lock_params": {
            "workspace_scope": "ci_f_0001",
        },
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0001", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "repo:codeup:nonprotected-test", "mode": "read"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0001", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0001",
            "debug-pool",
            "daemon",
            "relay",
            "codeup-repo:nonprotected-test",
            "workspace_metadata",
        ),
        "scenario_ids": (
            "F_0001.s01_reset_case_namespace",
            "F_0001.s02_gui_add_codeup_repo_dotdir_workspace",
            "F_0001.s03_gui_add_cli_equivalent",
            "F_0001.s04_wait_workspace_registered",
            "F_0001.s05_workspace_record",
            "F_0001.s06_repo_dotdir_metadata_root",
            "F_0001.s07_relay_workspace_sync",
            "F_0001.s08_gui_remove_workspace",
            "F_0001.s09_wait_workspace_removed",
            "F_0001.s10_workspace_state_removed",
        ),
        "native_reset_case": False,
        "notes": (
            "Uses the already deployed debug daemon/relay and does not package, deploy, reset, or restart shared services.",
            "The case reset deletes only display/workspace ids with the ci_f_0001_ prefix.",
            "No intern or Feishu group is created.",
        ),
    },
)


def run_f_workspace_codeup_repo_dotdir_add_remove(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def gui_cli_equivalent(label: str, *, gui_command: str, cli: str, actual_commands: list[list[str]]) -> dict[str, Any]:
        detail = self.mock_treeview.cli_equivalence(gui_command, cli=cli, actual_commands=actual_commands)
        assertion = surface_assertions.treeview_cli_equivalent_detail(detail)
        self.require(label, assertion["ok"], assertion)
        return detail

    def s01() -> dict[str, Any]:
        return self.ctx.action.workspace.reset_stage_namespace_remote()

    def s02() -> dict[str, Any]:
        repo = self.ctx.action.workspace.nonprotected_repo_remote()
        display = self.remote_context.stage_workspace_display("codeup_dotdir")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="codeup_dotdir",
            display_name=display,
            provider="codeup",
            repo_url=repo,
            mode="repo_dotdir",
        )
        state.update({"repo": repo, "display": display, "workspace": workspace})
        return {"workspace": workspace}

    def s03() -> dict[str, Any]:
        workspace = state["workspace"]
        return gui_cli_equivalent(
            "gui_add_workspace_codeup_repo_dotdir_cli_equivalent",
            gui_command="intern.addWorkspace",
            cli="workspace create --provider codeup --mode repo_dotdir",
            actual_commands=[
                self.ctx.action.workspace.create_args_remote(
                    provider="codeup",
                    repo_url=state["repo"],
                    mode="repo_dotdir",
                    display_name=state["display"],
                ),
                [*self.internctl, "workspace", "enable", str(workspace["workspace_id"]), "--json"],
            ],
        )

    def s04() -> dict[str, Any]:
        return self.ctx.action.workspace.wait_registered_remote(state["display"])

    def s05() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.record_checks_remote(
                state["display"],
                provider="codeup",
                mode="repo_dotdir",
                repo_url=state["repo"],
            ),
        )

    def s06() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.metadata_root_checks_remote(state["workspace"], "repo_dotdir", provider="codeup"),
        )

    def s07() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.relay_sync_checks_remote(state["display"], provider="codeup"),
        )

    def s08() -> dict[str, Any]:
        return {"delete": self.ctx.action.workspace.delete_remote(state["workspace"])}

    def s09() -> dict[str, Any]:
        return self.ctx.action.workspace.wait_removed_remote(state["display"])

    def s10() -> dict[str, Any]:
        return workspace_assertions.require_checks(self, self.ctx.action.workspace.absent_checks_remote(state["display"]))

    self.run_ordered_scenarios([
        ("F_0001.s01_reset_case_namespace", s01),
        ("F_0001.s02_gui_add_codeup_repo_dotdir_workspace", s02),
        ("F_0001.s03_gui_add_cli_equivalent", s03),
        ("F_0001.s04_wait_workspace_registered", s04),
        ("F_0001.s05_workspace_record", s05),
        ("F_0001.s06_repo_dotdir_metadata_root", s06),
        ("F_0001.s07_relay_workspace_sync", s07),
        ("F_0001.s08_gui_remove_workspace", s08),
        ("F_0001.s09_wait_workspace_removed", s09),
        ("F_0001.s10_workspace_state_removed", s10),
    ])
