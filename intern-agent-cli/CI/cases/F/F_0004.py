from __future__ import annotations
from pathlib import Path
from typing import Any

from CI.assertions import surface as surface_assertions
from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0004_local_workspace_add_remove",
    name="Local workspace add/remove",
    description="Validate local_only workspace add/disable using a case-private local git repo, preserving the repo after workspace removal from the local view.",
    stage="remote",
    timeout_seconds=1200,
    kind="f_workspace_gui",
    tags=("F", "workspace", "gui", "local", "local_only"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "run_mode": "existing_debug_native_remote",
        "actions": (
            "workspace.reset_case_namespace",
            "workspace.create_temp_git_repo",
            "gui.workspace.add",
            "workspace.cli_create",
            "workspace.wait_registered",
            "workspace.cli_disable",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "native.workspace_gui_cli_equivalent",
            "native.workspace_record",
            "native.no_remote_credentials_used",
            "native.workspace_disabled",
            "native.local_repo_preserved",
        ),
        "lock_params": {
            "workspace_scope": "ci_f_0004",
        },
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0004", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "repo:local:ci_f_0004", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0004", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0004",
            "debug-pool",
            "daemon",
            "relay",
            "local-repo:ci_f_0004",
            "workspace_metadata",
        ),
        "scenario_ids": (
            "F_0004.s01_reset_case_namespace",
            "F_0004.s02_create_temp_git_repo",
            "F_0004.s03_gui_add_local_workspace",
            "F_0004.s04_gui_add_cli_equivalent",
            "F_0004.s05_wait_workspace_registered",
            "F_0004.s06_workspace_record",
            "F_0004.s07_no_remote_provider_credentials_used",
            "F_0004.s08_cli_workspace_disable",
            "F_0004.s09_workspace_disabled",
            "F_0004.s10_temp_repo_preserved",
        ),
        "native_reset_case": False,
        "notes": (
            "The local repo lives under this case artifact directory and is not cleaned at case end.",
            "local_only workspaces are local-authority records; Remove Workspace maps to local disable, not global relay delete.",
            "The case reset deletes only display/workspace ids with the ci_f_0004_ prefix.",
            "No intern or Feishu group is created.",
        ),
    },
)


def run_f_workspace_local_add_remove(case: Any) -> None:
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
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0004_local_repo")
        state["repo"] = str(repo)
        return {"local_repo": str(repo)}

    def s03() -> dict[str, Any]:
        display = self.remote_context.stage_workspace_display("local")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="local",
            display_name=display,
            provider="local",
            repo_url=state["repo"],
            mode="local_only",
            local_path=state["repo"],
        )
        state.update({"display": display, "workspace": workspace, "step_start": len(self.steps)})
        return {"workspace": workspace}

    def s04() -> dict[str, Any]:
        workspace = state["workspace"]
        return gui_cli_equivalent(
            "gui_add_workspace_local_cli_equivalent",
            gui_command="intern.addWorkspace",
            cli="workspace create --provider local --mode local_only",
            actual_commands=[
                self.ctx.action.workspace.create_args_remote(
                    provider="local",
                    repo_url=state["repo"],
                    mode="local_only",
                    display_name=state["display"],
                ),
                [*self.internctl, "workspace", "enable", str(workspace["workspace_id"]), "--json", "--local-path", state["repo"]],
            ],
        )

    def s05() -> dict[str, Any]:
        return self.ctx.action.workspace.wait_registered_remote(state["display"], require_relay=False)

    def s06() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.record_checks_remote(
                state["display"],
                provider="local",
                mode="local_only",
                repo_path=state["repo"],
            ),
        )

    def s07() -> dict[str, Any]:
        commands = [str(step.get("cmd") or "") for step in self.steps[int(state.get("step_start") or 0):]]
        bad = [cmd for cmd in commands if "CODEUP_ACCESS_TOKEN" in cmd or "GITHUB_TOKEN" in cmd or "--provider codeup" in cmd or "--provider github" in cmd]
        detail = {"commands": commands, "violations": bad}
        self.require("local_workspace_no_remote_credentials_used", not bad, detail)
        return detail

    def s08() -> dict[str, Any]:
        workspace = state["workspace"]
        result = self.json_cmd("F_0004 workspace disable", [*self.internctl, "workspace", "disable", str(workspace["workspace_id"]), "--json"], timeout=120)
        state["disable_result"] = result
        return {"disable": result}

    def s09() -> dict[str, Any]:
        workspace = state["workspace"]
        entry = self.ctx.action.workspace.entry_remote(workspace, name="F_0004 disabled local workspace retained")
        self.require("local_workspace_disabled_not_globally_deleted", entry is not None and not workspace_assertions.workspace_local_enabled(entry), {"workspace": workspace, "entry": entry})
        return {"workspace": workspace, "entry": entry, "disabled": True}

    def s10() -> dict[str, Any]:
        repo = Path(state["repo"])
        detail = {"local_repo": str(repo), "git_dir": str(repo / ".git"), "readme": str(repo / "README.md")}
        self.require("local_repo_preserved_after_workspace_delete", (repo / ".git").is_dir() and (repo / "README.md").is_file(), detail)
        return detail

    self.run_ordered_scenarios([
        ("F_0004.s01_reset_case_namespace", s01),
        ("F_0004.s02_create_temp_git_repo", s02),
        ("F_0004.s03_gui_add_local_workspace", s03),
        ("F_0004.s04_gui_add_cli_equivalent", s04),
        ("F_0004.s05_wait_workspace_registered", s05),
        ("F_0004.s06_workspace_record", s06),
        ("F_0004.s07_no_remote_provider_credentials_used", s07),
        ("F_0004.s08_cli_workspace_disable", s08),
        ("F_0004.s09_workspace_disabled", s09),
        ("F_0004.s10_temp_repo_preserved", s10),
    ])
