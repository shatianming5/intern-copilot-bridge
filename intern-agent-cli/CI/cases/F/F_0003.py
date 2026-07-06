from __future__ import annotations
from typing import Any

from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0003_github_workspace_add_remove",
    name="GitHub workspace add/remove",
    description="Validate GitHub workspace add/remove for repo_dotdir and metadata_branch modes through the GUI-equivalent CLI path.",
    stage="remote",
    timeout_seconds=1800,
    kind="f_workspace_gui",
    tags=("F", "workspace", "gui", "github", "repo_dotdir", "metadata_branch"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "run_mode": "existing_debug_native_remote",
        "actions": (
            "workspace.reset_case_namespace",
            "workspace.resolve_github_repo",
            "gui.workspace.add",
            "workspace.cli_create",
            "workspace.cli_delete",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "native.workspace_record",
            "native.relay_workspace_sync",
            "native.metadata_branch_created",
            "native.workspace_removed",
        ),
        "lock_params": {
            "workspace_scope": "ci_f_0003",
        },
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0003", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "repo:github:test", "mode": "read"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0003", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0003",
            "debug-pool",
            "daemon",
            "relay",
            "github-repo:test",
            "workspace_metadata",
        ),
        "scenario_ids": (
            "F_0003.s01_reset_case_namespace",
            "F_0003.s02_resolve_github_test_repo",
            "F_0003.s03_gui_add_github_repo_dotdir_workspace",
            "F_0003.s04_repo_dotdir_workspace_record",
            "F_0003.s05_repo_dotdir_relay_workspace_sync",
            "F_0003.s06_delete_repo_dotdir_workspace",
            "F_0003.s07_repo_dotdir_workspace_removed",
            "F_0003.s08_gui_add_github_metadata_branch_workspace",
            "F_0003.s09_metadata_branch_workspace_record",
            "F_0003.s10_metadata_branch_created",
            "F_0003.s11_delete_metadata_branch_workspace",
            "F_0003.s12_metadata_branch_workspace_removed",
        ),
        "native_reset_case": False,
        "notes": (
            "Resolves GitHub repo from INTERN_CI_GITHUB_NONPROTECTED_REPO, ENTERPRISE_CI_GITHUB_TEST_REPO, then CI default.",
            "Missing GitHub repo env alone is not environment_missing; auth/provider failure is classified from concrete command evidence.",
            "No intern or Feishu group is created.",
        ),
    },
)


def run_f_workspace_github_add_remove(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def s01() -> dict[str, Any]:
        return self.ctx.action.workspace.reset_stage_namespace_remote()

    def s02() -> dict[str, Any]:
        detail = self.ctx.action.workspace.github_nonprotected_repo_detail_remote()
        repo = str(detail["github_repo"])
        state["repo"] = repo
        return detail

    def s03() -> dict[str, Any]:
        display = self.remote_context.stage_workspace_display("github_dotdir")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="github_dotdir",
            display_name=display,
            provider="github",
            repo_url=state["repo"],
            mode="repo_dotdir",
        )
        state.update({"dotdir_display": display, "dotdir_workspace": workspace})
        return {"workspace": workspace}

    def s04() -> dict[str, Any]:
        self.ctx.action.workspace.wait_registered_remote(state["dotdir_display"])
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.record_checks_remote(
                state["dotdir_display"],
                provider="github",
                mode="repo_dotdir",
                repo_url=state["repo"],
            ),
        )

    def s05() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.relay_sync_checks_remote(state["dotdir_display"], provider="github"),
        )

    def s06() -> dict[str, Any]:
        return {"delete": self.ctx.action.workspace.delete_remote(state["dotdir_workspace"])}

    def s07() -> dict[str, Any]:
        return self.ctx.action.workspace.wait_removed_remote(state["dotdir_display"])

    def s08() -> dict[str, Any]:
        display = self.remote_context.stage_workspace_display("github_meta")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="github_meta",
            display_name=display,
            provider="github",
            repo_url=state["repo"],
            mode="metadata_branch",
            metadata_branch="intern_workspace",
        )
        state.update({"meta_display": display, "meta_workspace": workspace})
        return {"workspace": workspace}

    def s09() -> dict[str, Any]:
        self.ctx.action.workspace.wait_registered_remote(state["meta_display"])
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.record_checks_remote(
                state["meta_display"],
                provider="github",
                mode="metadata_branch",
                repo_url=state["repo"],
            ),
        )

    def s10() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.metadata_branch_created_checks_remote(state["meta_workspace"], provider="github"),
        )

    def s11() -> dict[str, Any]:
        return {"delete": self.ctx.action.workspace.delete_remote(state["meta_workspace"])}

    def s12() -> dict[str, Any]:
        return self.ctx.action.workspace.wait_removed_remote(state["meta_display"])

    self.run_ordered_scenarios([
        ("F_0003.s01_reset_case_namespace", s01),
        ("F_0003.s02_resolve_github_test_repo", s02),
        ("F_0003.s03_gui_add_github_repo_dotdir_workspace", s03),
        ("F_0003.s04_repo_dotdir_workspace_record", s04),
        ("F_0003.s05_repo_dotdir_relay_workspace_sync", s05),
        ("F_0003.s06_delete_repo_dotdir_workspace", s06),
        ("F_0003.s07_repo_dotdir_workspace_removed", s07),
        ("F_0003.s08_gui_add_github_metadata_branch_workspace", s08),
        ("F_0003.s09_metadata_branch_workspace_record", s09),
        ("F_0003.s10_metadata_branch_created", s10),
        ("F_0003.s11_delete_metadata_branch_workspace", s11),
        ("F_0003.s12_metadata_branch_workspace_removed", s12),
    ])
