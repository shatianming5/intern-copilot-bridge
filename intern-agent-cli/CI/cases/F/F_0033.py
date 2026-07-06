from typing import Any
from CI.assertions import session as session_assertions
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0033.s01_reset_case_namespace",
    "F_0033.s02_create_workspace",
    "F_0033.s03_create_codex_intern",
    "F_0033.s04_start_codex_session_no_prompt",
    "F_0033.s05_wait_session_running",
    "F_0033.s06_capture_initial_codex_session_id_diagnostic",
    "F_0033.s07_resume_hint_command_contract",
    "F_0033.s08_restart_codex_session",
    "F_0033.s09_wait_restart_running",
    "F_0033.s10_restart_output_allows_fresh_start",
    "F_0033.s11_capture_codex_session_id_after_restart",
    "F_0033.s12_session_scope_stable",
)


CASE = CaseDefinition(
    id="F_0033_codex_no_prompt_exit_restart_contract",
    name="Codex no-prompt restart startup contract",
    description=(
        "Validates Codex no-prompt resume hint command shape and restart startup behavior; "
        "same-session resume after a real Codex turn belongs to J."
    ),
    stage="remote",
    timeout_seconds=2700,
    kind="f_intern_session_remote",
    tags=("F", "codex", "intern", "session", "restart", "resume", "tmux"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "session.codex_start_restart_remote",
            "session.codex_capture_id_remote",
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
            "native.session_restart_policy",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0033_codex", "mode": "exclusive"},
            {"resource": "llm:codex", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0033", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "session:axis_intern_agents_backup:intern_ci_f_0033_codex", "mode": "exclusive"},
            {"resource": "tmux:ci_f_0033", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0033_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0033",
            "workspace:ci_f_0033_workspace",
            "intern:intern_ci_f_0033_codex",
            "session:intern_ci_f_0033_codex",
            "llm:codex",
            "tmux",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy_llm_no_business_prompt",
        "notes": (
            "Do not send natural-language business prompts to the Codex session.",
            "The initial transcript id is diagnostic only; fresh restart is acceptable when no real Codex turn created a UUID.",
            "Live /exit plus executing the resume hint and proving same UUID requires an agent turn and belongs to J.",
            "The run may leave the session running for supervisor inspection.",
        ),
    },
)


def run_f_codex_no_prompt_exit_restart_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        state.update({"repo": repo, "workspace": workspace})
        return {"repo": str(repo), "workspace": workspace}

    def s03_create_codex_intern() -> dict[str, Any]:
        created = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(
            state["workspace"],
            "codex",
            intern_type="codex",
            repo_url=str(state["repo"]),
        ))
        state["intern"] = created["intern"]
        return {"intern": created, "status": self.ctx.action.intern.status_json_remote(state["workspace"], created["intern"])}

    def s04_start_codex_session_no_prompt() -> dict[str, Any]:
        status = self.ctx.action.session.start_for_workspace_remote(state["workspace"], state["intern"], session_type="codex")
        state["tmux_session"] = str(status.get("tmux_session") or state["intern"])
        return {"session_status": status, "business_prompt_sent": False}

    def s05_wait_session_running() -> dict[str, Any]:
        status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"])
        self.require("f0033_session_running_before_restart", status.get("running") is True, status)
        ready = self.ctx.action.session.wait_tmux_input_ready_remote(str(status.get("tmux_session") or state["tmux_session"]), timeout=240)
        return {"session_status": status, "ready": ready, "business_prompt_sent": False}

    def s06_capture_initial_codex_session_id_diagnostic() -> dict[str, Any]:
        evidence = self.ctx.action.session.codex_session_id_evidence_remote(state["workspace"], state["intern"])
        state["initial_session_id_evidence"] = evidence
        return evidence

    def s07_resume_hint_command_contract() -> dict[str, Any]:
        from commands import session as session_cmd

        command = session_cmd._resume_hint_command(state["intern"], str(state["workspace"].get("display") or ""), "codex")
        contract_result = session_assertions.resume_hint_command_contract_check(
            "f0033_resume_hint_command_contract",
            command,
            workspace=state["workspace"],
            intern=state["intern"],
            session_type="codex",
        )
        self.require_classified_checks(contract_result)
        contract = contract_result["detail"]
        state["resume_hint_contract"] = contract
        return contract | {"command": command, "business_prompt_sent": False, "source": "commands.session._resume_hint_command"}

    def s15_restart_codex_session() -> dict[str, Any]:
        restart = self.ctx.action.session.restart_for_workspace_remote(state["workspace"], state["intern"], session_type="codex")
        self.require_classified_contract(
            "f0033_restart_command_succeeded_" + state["intern"],
            restart.get("returncode") == 0,
            str(restart.get("failure_classification") or "ci_assertion_or_product_bug"),
            restart,
        )
        state["restart"] = restart
        return restart | {
            "gui_command": "intern.restartCodexSession",
            "cli_equivalent": "internctl session restart <intern> --project <project> --type codex --no-attach",
        }

    def s16_wait_restart_running() -> dict[str, Any]:
        status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"])
        self.require("f0033_session_running_after_restart", status.get("running") is True, status)
        state["restart_status"] = status
        return status

    def s17_restart_output_allows_fresh_start() -> dict[str, Any]:
        result = session_assertions.codex_restart_output_allows_fresh_start_check(
            "f0033_restart_output_fresh_or_resume",
            state["restart"],
        )
        self.require_classified_checks(result)
        return result["detail"]

    def s18_capture_codex_session_id_after_restart() -> dict[str, Any]:
        evidence = self.ctx.action.session.codex_session_id_evidence_remote(state["workspace"], state["intern"])
        output_id = session_assertions.codex_session_id_from_text(str(state["restart"].get("output") or ""))
        if output_id and not evidence.get("session_id"):
            evidence = {**evidence, "session_id": output_id, "source": "restart_command_output", "available": True}
        state["after_session_id_evidence"] = evidence
        return evidence

    def s20_session_scope_stable() -> dict[str, Any]:
        status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"])
        daemon = self.http_json("F_0033 daemon status", "GET", "/api/status", timeout=30)
        entries = self.ctx.action.session.registry_entries_for_remote(state["workspace"], state["intern"])
        tmux_session = str(status.get("tmux_session") or state["tmux_session"])
        tmux = self.run_cmd("F_0033 tmux has session", ["tmux", "has-session", "-t", f"={tmux_session}"], timeout=30, check=False)
        detail = {"status": status, "daemon": daemon, "entries": entries, "tmux_returncode": tmux.returncode}
        self.require(
            "f0033_session_scope_stable_after_restart",
            status.get("running") is True
            and daemon.get("relay_connected") is True
            and bool(entries)
            and tmux.returncode == 0,
            detail,
        )
        return detail

    self.run_ordered_scenarios([
        ("F_0033.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0033.s02_create_workspace", s02_create_workspace),
        ("F_0033.s03_create_codex_intern", s03_create_codex_intern),
        ("F_0033.s04_start_codex_session_no_prompt", s04_start_codex_session_no_prompt),
        ("F_0033.s05_wait_session_running", s05_wait_session_running),
        ("F_0033.s06_capture_initial_codex_session_id_diagnostic", s06_capture_initial_codex_session_id_diagnostic),
        ("F_0033.s07_resume_hint_command_contract", s07_resume_hint_command_contract),
        ("F_0033.s08_restart_codex_session", s15_restart_codex_session),
        ("F_0033.s09_wait_restart_running", s16_wait_restart_running),
        ("F_0033.s10_restart_output_allows_fresh_start", s17_restart_output_allows_fresh_start),
        ("F_0033.s11_capture_codex_session_id_after_restart", s18_capture_codex_session_id_after_restart),
        ("F_0033.s12_session_scope_stable", s20_session_scope_stable),
    ])
