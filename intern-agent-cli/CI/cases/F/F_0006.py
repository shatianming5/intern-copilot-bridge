from __future__ import annotations
import urllib.parse
from typing import Any
from CI.helpers.product_cli_helper import tail

from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0006_workspace_mode_switch_contract",
    name="Workspace mode switch removed contract",
    description="Validate workspace mode is fixed at add time and old mode switch surfaces are unavailable.",
    stage="remote",
    timeout_seconds=1800,
    kind="f_workspace_gui",
    tags=("F", "workspace", "gui", "mode", "codeup", "local_only"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "run_mode": "existing_debug_native_remote",
        "actions": (
            "workspace.reset_case_namespace",
            "workspace.cli_create",
            "workspace.wait_registered",
            "workspace.cli_delete",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "native.workspace_gui_cli_equivalent",
            "native.workspace_record",
            "native.workspace_mode_switch_contract",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0006", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "repo:codeup:nonprotected-test", "mode": "read"},
            {"resource": "repo:local:ci_f_0006", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0006", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0006",
            "debug-pool",
            "daemon",
            "relay",
            "codeup-repo:nonprotected-test",
            "local-repo:ci_f_0006",
            "workspace_metadata",
        ),
        "scenario_ids": (
            "F_0006.s01_reset_case_namespace",
            "F_0006.s02_create_codeup_repo_dotdir_workspace",
            "F_0006.s03_workspace_mode_cli_removed",
            "F_0006.s04_workspace_mode_daemon_api_removed",
            "F_0006.s05_workspace_mode_relay_api_removed",
            "F_0006.s06_workspace_mode_unchanged",
            "F_0006.s07_delete_workspace_record_only",
        ),
        "native_reset_case": False,
        "notes": (
            "Workspace mode switch commands are intentionally removed; migration uses workspace migrate-mode.",
            "The case reset deletes only display/workspace ids with the ci_f_0006_ prefix.",
            "No intern or Feishu group is created.",
        ),
    },
)


def run_f_workspace_mode_switch_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def s01() -> dict[str, Any]:
        return self.ctx.action.workspace.reset_stage_namespace_remote()

    def s02() -> dict[str, Any]:
        repo = self.ctx.action.workspace.nonprotected_repo_remote()
        display = self.remote_context.stage_workspace_display("codeup")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="codeup_mode",
            display_name=display,
            provider="codeup",
            repo_url=repo,
            mode="repo_dotdir",
        )
        state.update({"repo": repo, "remote_display": display, "remote_workspace": workspace, "remote_initial": dict(workspace)})
        return {"workspace": workspace}

    def s03() -> dict[str, Any]:
        workspace = state["remote_workspace"]
        result = self.run_cmd(
            "removed workspace metadata-mode CLI",
            [*self.internctl, "workspace", "mode", "set", str(workspace["workspace_id"]), "--mode", "metadata_branch", "--json"],
            timeout=60,
            check=False,
        )
        report = {
            "returncode": result.returncode,
            "stdout": tail(result.stdout, 1000),
            "stderr": tail(result.stderr, 1000),
        }
        self.require("workspace_mode_cli_removed", result.returncode != 0, report)
        return report

    def s04() -> dict[str, Any]:
        workspace = state["remote_workspace"]
        response = self.daemon_request_json(
            "removed daemon workspace metadata-mode API",
            "POST",
            f"/api/workspaces/{urllib.parse.quote(str(workspace['workspace_id']))}/mode/set",
            {"mode": "metadata_branch"},
            timeout=60,
            check=False,
        )
        self.require("workspace_mode_daemon_api_removed", response.get("status_code") == 404 or response.get("error") == "not found", response)
        return response

    def s05() -> dict[str, Any]:
        workspace = state["remote_workspace"]
        response = self.relay_request_json(
            "removed relay workspace metadata-mode API",
            "POST",
            f"/api/workspaces/{urllib.parse.quote(str(workspace['workspace_id']))}/mode/set",
            {"mode": "metadata_branch"},
            timeout=60,
            check=False,
        )
        self.require("workspace_mode_relay_api_removed", response.get("status_code") == 404 or response.get("error") == "not found", response)
        return response

    def s06() -> dict[str, Any]:
        current = self.ctx.action.workspace.find_record_remote(state["remote_display"], source="local") or {}
        initial = state["remote_initial"]
        detail = {"initial": initial, "current": current}
        self.require(
            "workspace_mode_record_unchanged",
            current.get("provider") == initial.get("provider")
            and current.get("repo_url") == initial.get("repo_url")
            and current.get("metadata_mode") == initial.get("mode")
            and workspace_assertions.workspace_display(current) == initial.get("display"),
            detail,
        )
        return detail

    def s07() -> dict[str, Any]:
        deleted = self.ctx.action.workspace.delete_remote(state["remote_workspace"])
        state["deleted"] = deleted
        return deleted

    self.run_ordered_scenarios([
        ("F_0006.s01_reset_case_namespace", s01),
        ("F_0006.s02_create_codeup_repo_dotdir_workspace", s02),
        ("F_0006.s03_workspace_mode_cli_removed", s03),
        ("F_0006.s04_workspace_mode_daemon_api_removed", s04),
        ("F_0006.s05_workspace_mode_relay_api_removed", s05),
        ("F_0006.s06_workspace_mode_unchanged", s06),
        ("F_0006.s07_delete_workspace_record_only", s07),
    ])
