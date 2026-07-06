import re
from typing import Any
from CI.assertions import session as session_assertions
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0009.s01_reset_case_namespace",
    "F_0009.s02_create_workspace_and_codex_intern",
    "F_0009.s03_gui_start_session_cli_equivalent",
    "F_0009.s04_session_running_tmux_status",
    "F_0009.s04_1_feishu_group_green_light",
    "F_0009.s05_capture_codex_session_id_before_restart",
    "F_0009.s06_gui_restart_starts_codex_session",
    "F_0009.s07_cli_session_status_payload",
)


CASE = CaseDefinition(
    id="F_0009_codex_session_lifecycle_no_prompt",
    name="F_0009_codex_session_lifecycle_no_prompt",
    description=(
        "Validates Codex session start, restart startup, tmux, and status lifecycle without sending a business prompt."
    ),
    stage="remote",
    timeout_seconds=2400,
    kind="f_intern_session_remote",
    tags=("F", "intern", "session", "codex", "tmux", "gui", "cli"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "session.codex_start_restart_remote",
            "session.codex_capture_id_remote",
            "gui.session.create_codex",
            "gui.session.restart_codex",
            "start_intern_session",
            "daemon.read_status",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "ctx.action_ok",
            "native.session_running",
            "native.feishu_group_green_light",
            "native.session_restart_policy",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0009_codex", "mode": "exclusive"},
            {"resource": "llm:codex", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0009", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "session:axis_intern_agents_backup:intern_ci_f_0009_codex", "mode": "exclusive"},
            {"resource": "tmux:ci_f_0009", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0009_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0009",
            "workspace:ci_f_0009_workspace",
            "intern:intern_ci_f_0009_codex",
            "session:intern_ci_f_0009_codex",
            "llm:codex",
            "tmux",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy_llm_no_business_prompt",
        "notes": (
            "Do not send natural-language business prompts to the Codex session.",
            "The run may leave the session running for supervisor inspection.",
        ),
    },
)


def run_f_codex_session_lifecycle_no_prompt(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace_and_codex_intern() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        codex = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "codex", intern_type="codex", repo_url=str(repo)))
        no_group = self.ctx.action.feishu.relay_registry_absent_evidence_remote(workspace, codex["intern"])
        self.require(
            "relay_registry_absent_" + re.sub(r"[^A-Za-z0-9_]+", "_", codex["intern"]),
            not no_group["entry"] and not no_group["lookup"].get("chat_id"),
            no_group,
        )
        state.update({"repo": repo, "workspace": workspace, "intern": codex["intern"], "metadata": codex["metadata"]})
        return {"repo": str(repo), "workspace": workspace, "codex": codex, "no_group": no_group}

    def s03_gui_start_session_cli_equivalent() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        status = self.ctx.action.session.start_for_workspace_remote(workspace, intern)
        state["session_before"] = status
        return {
            "session_status": status,
            "gui_command": "intern.createCodexSession",
            "cli_equivalent": "internctl session start <intern> --project <project> --type codex --no-attach",
        }

    def s04_session_running_tmux_status() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        status = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        tmux_session = str(status.get("tmux_session") or intern)
        ready = self.ctx.action.session.wait_tmux_input_ready_remote(tmux_session, timeout=240)
        self.require("codex_session_running_tmux_exists_" + intern, status.get("running") is True, status)
        state["tmux_session_before"] = tmux_session
        return {"session_status": status, "tmux_session": tmux_session, "ready": ready, "business_prompt_sent": False}

    def s04_1_feishu_group_green_light() -> dict[str, Any]:
        green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
            state["workspace"],
            state["intern"],
            expected_type="codex",
            timeout=min(180, self.args.timeout),
        )
        return {"green_light": green, "session_status": state.get("session_before"), "business_prompt_sent": False}

    def s05_capture_codex_session_id_before_restart() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        evidence = self.ctx.action.session.codex_session_id_evidence_remote(workspace, intern)
        state["session_id_before"] = evidence
        return evidence

    def s06_gui_restart_starts_codex_session() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        before = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        restart = self.ctx.action.session.restart_for_workspace_remote(workspace, intern, session_type="codex")
        self.require_classified_contract(
            "f0009_restart_command_succeeded_" + intern,
            restart.get("returncode") == 0,
            str(restart.get("failure_classification") or "ci_assertion_or_product_bug"),
            restart,
        )
        after = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        tmux_session = str(after.get("tmux_session") or before.get("tmux_session") or intern)
        ready = self.ctx.action.session.wait_tmux_input_ready_remote(tmux_session, timeout=240)
        before_evidence = state["session_id_before"]
        output_result = session_assertions.codex_restart_output_allows_fresh_start_check(
            "f0009_restart_output_fresh_or_resume",
            restart,
        )
        self.require_classified_checks(output_result)
        output_contract = output_result["detail"]
        after_evidence = self.ctx.action.session.codex_session_id_evidence_remote(workspace, intern)
        state["session_after"] = after
        return {
            "before": before,
            "after": after,
            "before_session_id_evidence": before_evidence,
            "after_session_id_evidence": after_evidence,
            "restart_stdout": restart.get("stdout", ""),
            "restart_stderr": restart.get("stderr", ""),
            "restart_policy": output_contract["restart_policy"],
            "ready": ready,
            "business_prompt_sent": False,
            "gui_command": "intern.restartCodexSession",
            "cli_equivalent": "internctl session restart <intern> --project <project> --type codex --no-attach",
        }

    def s07_cli_session_status_payload() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        status = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        daemon = self.http_json("F_0009 daemon status", "GET", "/api/status", timeout=30)
        self.require(
            "codex_session_status_payload_running_relay_connected",
            status.get("running") is True and daemon.get("relay_connected") is True,
            {"session_status": status, "daemon": daemon},
        )
        return {"session_status": status, "daemon": daemon, "retained_scene": state, "business_prompt_sent": False}

    self.run_ordered_scenarios([
        ("F_0009.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0009.s02_create_workspace_and_codex_intern", s02_create_workspace_and_codex_intern),
        ("F_0009.s03_gui_start_session_cli_equivalent", s03_gui_start_session_cli_equivalent),
        ("F_0009.s04_session_running_tmux_status", s04_session_running_tmux_status),
        ("F_0009.s04_1_feishu_group_green_light", s04_1_feishu_group_green_light),
        ("F_0009.s05_capture_codex_session_id_before_restart", s05_capture_codex_session_id_before_restart),
        ("F_0009.s06_gui_restart_starts_codex_session", s06_gui_restart_starts_codex_session),
        ("F_0009.s07_cli_session_status_payload", s07_cli_session_status_payload),
    ])
