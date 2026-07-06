from __future__ import annotations
from typing import Any

from CI.assertions import surface as surface_assertions
from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0002_codeup_metadata_branch_workspace_add_remove",
    name="Codeup metadata_branch workspace add/remove",
    description="Validate Codeup metadata_branch workspace add/delete and prove business branch revision is unchanged.",
    stage="remote",
    timeout_seconds=1800,
    kind="f_workspace_gui",
    tags=("F", "workspace", "gui", "codeup", "metadata_branch"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "run_mode": "existing_debug_native_remote",
        "actions": (
            "workspace.reset_case_namespace",
            "workspace.record_git_baseline",
            "gui.workspace.add",
            "workspace.cli_create",
            "workspace.wait_registered",
            "workspace.cli_delete",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "native.workspace_gui_cli_equivalent",
            "native.workspace_record",
            "native.metadata_branch_created",
            "native.business_branch_unchanged",
            "native.workspace_removed",
        ),
        "lock_params": {
            "workspace_scope": "ci_f_0002",
        },
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0002", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "repo:codeup:nonprotected-test", "mode": "read"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0002", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0002",
            "debug-pool",
            "daemon",
            "relay",
            "codeup-repo:nonprotected-test",
            "workspace_metadata",
        ),
        "scenario_ids": (
            "F_0002.s01_reset_case_namespace",
            "F_0002.s02_record_git_baseline",
            "F_0002.s03_gui_add_codeup_metadata_branch_workspace",
            "F_0002.s04_gui_add_cli_equivalent",
            "F_0002.s05_wait_workspace_registered",
            "F_0002.s06_workspace_record",
            "F_0002.s07_metadata_branch_created",
            "F_0002.s08_business_branch_unchanged_after_add",
            "F_0002.s09_cli_workspace_delete",
            "F_0002.s10_workspace_removed",
            "F_0002.s11_business_branch_unchanged_after_delete",
        ),
        "native_reset_case": False,
        "notes": (
            "Uses the nonprotected Codeup test repo and metadata branch intern_workspace.",
            "The case reset deletes only display/workspace ids with the ci_f_0002_ prefix.",
            "No intern or Feishu group is created.",
        ),
    },
)


def run_f_workspace_codeup_metadata_branch_add_remove(case: Any) -> None:
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
        baseline = self.ctx.action.workspace.git_default_head_remote(repo, name="F_0002 business branch baseline")
        state.update({"repo": repo, "baseline": baseline})
        return baseline

    def s03() -> dict[str, Any]:
        display = self.remote_context.stage_workspace_display("codeup_meta")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="codeup_meta",
            display_name=display,
            provider="codeup",
            repo_url=state["repo"],
            mode="metadata_branch",
            metadata_branch="intern_workspace",
        )
        state.update({"display": display, "workspace": workspace})
        return {"workspace": workspace}

    def s04() -> dict[str, Any]:
        workspace = state["workspace"]
        return gui_cli_equivalent(
            "gui_add_workspace_codeup_metadata_branch_cli_equivalent",
            gui_command="intern.addWorkspace",
            cli="workspace create --provider codeup --mode metadata_branch",
            actual_commands=[
                self.ctx.action.workspace.create_args_remote(
                    provider="codeup",
                    repo_url=state["repo"],
                    mode="metadata_branch",
                    display_name=state["display"],
                    metadata_branch="intern_workspace",
                ),
                [*self.internctl, "workspace", "enable", str(workspace["workspace_id"]), "--json"],
            ],
        )

    def s05() -> dict[str, Any]:
        return self.ctx.action.workspace.wait_registered_remote(state["display"])

    def s06() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.record_checks_remote(
                state["display"],
                provider="codeup",
                mode="metadata_branch",
                repo_url=state["repo"],
            ),
        )

    def s07() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.metadata_branch_created_checks_remote(state["workspace"], provider="codeup"),
        )

    def s08() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.business_branch_unchanged_checks_remote(
                state["baseline"],
                label="F_0002 business branch after add",
            ),
        )

    def s09() -> dict[str, Any]:
        return {"delete": self.ctx.action.workspace.delete_remote(state["workspace"])}

    def s10() -> dict[str, Any]:
        return self.ctx.action.workspace.wait_removed_remote(state["display"])

    def s11() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.business_branch_unchanged_checks_remote(
                state["baseline"],
                label="F_0002 business branch after delete",
            ),
        )

    self.run_ordered_scenarios([
        ("F_0002.s01_reset_case_namespace", s01),
        ("F_0002.s02_record_git_baseline", s02),
        ("F_0002.s03_gui_add_codeup_metadata_branch_workspace", s03),
        ("F_0002.s04_gui_add_cli_equivalent", s04),
        ("F_0002.s05_wait_workspace_registered", s05),
        ("F_0002.s06_workspace_record", s06),
        ("F_0002.s07_metadata_branch_created", s07),
        ("F_0002.s08_business_branch_unchanged_after_add", s08),
        ("F_0002.s09_cli_workspace_delete", s09),
        ("F_0002.s10_workspace_removed", s10),
        ("F_0002.s11_business_branch_unchanged_after_delete", s11),
    ])
