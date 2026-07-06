from __future__ import annotations
from typing import Any

from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0005_workspace_duplicate_invalid_add_rollback",
    name="Workspace duplicate and invalid add rollback",
    description="Validate duplicate/idempotent and invalid workspace add paths return stable results and leave no partial registry/relay records.",
    stage="remote",
    timeout_seconds=1800,
    kind="f_workspace_gui",
    tags=("F", "workspace", "gui", "rollback", "codeup"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "run_mode": "existing_debug_native_remote",
        "actions": (
            "workspace.reset_case_namespace",
            "workspace.cli_create",
            "workspace.attempt_create",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "native.workspace_record",
            "native.workspace_create_failed_rollback",
            "native.relay_workspace_sync",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0005", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "repo:codeup:nonprotected-test", "mode": "read"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0005", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0005",
            "debug-pool",
            "daemon",
            "relay",
            "codeup-repo:nonprotected-test",
            "workspace_metadata",
        ),
        "scenario_ids": (
            "F_0005.s01_reset_case_namespace",
            "F_0005.s02_create_baseline_workspace",
            "F_0005.s03_baseline_workspace_registered",
            "F_0005.s04_attempt_duplicate_display_name",
            "F_0005.s05_duplicate_create_rejected_or_reused",
            "F_0005.s06_no_extra_workspace_records_after_duplicate",
            "F_0005.s07_attempt_bad_repo_url",
            "F_0005.s08_bad_repo_create_failed",
            "F_0005.s09_bad_repo_no_workspace_record",
            "F_0005.s10_attempt_missing_metadata_branch",
            "F_0005.s11_bad_branch_create_failed",
            "F_0005.s12_bad_branch_no_workspace_record",
            "F_0005.s13_relay_has_no_bad_workspace",
        ),
        "native_reset_case": False,
        "notes": (
            "Exact same workspace create may be rejected as duplicate or idempotently reuse the existing workspace id.",
            "Expected-failure attempts intentionally do not perform CI-side rollback.",
            "Residual records after failed create are product rollback evidence, not CI cleanup.",
            "No intern or Feishu group is created.",
        ),
    },
)


def run_f_workspace_duplicate_invalid_rollback(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def s01() -> dict[str, Any]:
        return self.ctx.action.workspace.reset_stage_namespace_remote()

    def s02() -> dict[str, Any]:
        repo = self.ctx.action.workspace.nonprotected_repo_remote()
        display = self.remote_context.stage_workspace_display("base")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="base",
            display_name=display,
            provider="codeup",
            repo_url=repo,
            mode="repo_dotdir",
        )
        state.update({"repo": repo, "base_display": display, "base_workspace": workspace})
        return {"workspace": workspace}

    def s03() -> dict[str, Any]:
        return self.ctx.action.workspace.wait_registered_remote(state["base_display"])

    def s04() -> dict[str, Any]:
        attempt = self.ctx.action.workspace.attempt_create_remote(
            provider="codeup",
            repo_url=state["repo"],
            mode="repo_dotdir",
            display_name=state["base_display"],
        )
        state["duplicate_attempt"] = attempt
        return attempt

    def s05() -> dict[str, Any]:
        attempt = state["duplicate_attempt"]
        if attempt.get("failed_at"):
            return workspace_assertions.require_checks(
                self,
                workspace_assertions.workspace_attempt_failed_check(attempt, "duplicate_display_name"),
            )
        detail = {
            "attempt": attempt,
            "base_workspace": state["base_workspace"],
            "accepted_contract": "exact same workspace create may be idempotent and reuse the existing workspace",
        }
        self.require(
            "workspace_duplicate_create_idempotent_reuse",
            str(attempt.get("workspace_id") or "") == str(state["base_workspace"].get("workspace_id") or ""),
            detail,
        )
        return detail

    def s06() -> dict[str, Any]:
        detail = workspace_assertions.require_checks(
            self,
            self.ctx.action.workspace.no_extra_records_checks_remote(
                self.remote_context.stage_workspace_prefix(),
                allowed_displays={state["base_display"]},
            ),
        )
        detail["baseline_delete"] = self.ctx.action.workspace.delete_remote(state["base_workspace"])
        detail["baseline_removed"] = self.ctx.action.workspace.wait_removed_remote(state["base_display"])
        return detail

    def s07() -> dict[str, Any]:
        display = self.remote_context.stage_workspace_display("bad_repo")
        attempt = self.ctx.action.workspace.attempt_create_remote(
            provider="codeup",
            repo_url="git@codeup.invalid/missing.git",
            mode="repo_dotdir",
            display_name=display,
        )
        state.update({"bad_repo_display": display, "bad_repo_attempt": attempt})
        return attempt

    def s08() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            workspace_assertions.workspace_attempt_failed_check(state["bad_repo_attempt"], "repo_unreachable"),
        )

    def s09() -> dict[str, Any]:
        return workspace_assertions.require_checks(self, self.ctx.action.workspace.absent_checks_remote(state["bad_repo_display"]))

    def s10() -> dict[str, Any]:
        display = self.remote_context.stage_workspace_display("bad_branch")
        missing_branch = f"missing_branch_f0005_{self.run_token}"
        attempt = self.ctx.action.workspace.attempt_create_remote(
            provider="codeup",
            repo_url=state["repo"],
            mode="metadata_branch",
            display_name=display,
            metadata_branch=missing_branch,
        )
        state.update({"bad_branch_display": display, "bad_branch": missing_branch, "bad_branch_attempt": attempt})
        return attempt

    def s11() -> dict[str, Any]:
        return workspace_assertions.require_checks(
            self,
            workspace_assertions.workspace_attempt_failed_check(state["bad_branch_attempt"], "branch_unavailable"),
        )

    def s12() -> dict[str, Any]:
        return workspace_assertions.require_checks(self, self.ctx.action.workspace.absent_checks_remote(state["bad_branch_display"]))

    def s13() -> dict[str, Any]:
        records = self.ctx.action.workspace.prefix_records_remote(self.remote_context.stage_workspace_prefix())
        bad = []
        for source, items in records.items():
            for item in items:
                display = workspace_assertions.workspace_display(item)
                if display.startswith(self.remote_context.stage_workspace_prefix() + "bad"):
                    bad.append({"source": source, "record": item})
        detail = {"records": records, "bad_records": bad}
        self.require("relay_has_no_bad_workspace_records", not bad, detail)
        return detail

    self.run_ordered_scenarios([
        ("F_0005.s01_reset_case_namespace", s01),
        ("F_0005.s02_create_baseline_workspace", s02),
        ("F_0005.s03_baseline_workspace_registered", s03),
        ("F_0005.s04_attempt_duplicate_display_name", s04),
        ("F_0005.s05_duplicate_create_rejected_or_reused", s05),
        ("F_0005.s06_no_extra_workspace_records_after_duplicate", s06),
        ("F_0005.s07_attempt_bad_repo_url", s07),
        ("F_0005.s08_bad_repo_create_failed", s08),
        ("F_0005.s09_bad_repo_no_workspace_record", s09),
        ("F_0005.s10_attempt_missing_metadata_branch", s10),
        ("F_0005.s11_bad_branch_create_failed", s11),
        ("F_0005.s12_bad_branch_no_workspace_record", s12),
        ("F_0005.s13_relay_has_no_bad_workspace", s13),
    ])
