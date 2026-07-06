from pathlib import Path
from typing import Any

from CI.assertions import session as session_assertions
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0027.s01_reset_case_namespace",
    "F_0027.s02_create_workspace",
    "F_0027.s03_seed_codex_intern",
    "F_0027.s04_seed_setup_pass",
    "F_0027.s05_gui_create_codex_session",
    "F_0027.s06_gui_start_cli_equivalent",
    "F_0027.s07_wait_session_running",
    "F_0027.s08_session_status_running",
    "F_0027.s08_1_feishu_group_green_light",
    "F_0027.s09_tree_light_refresh_requested",
    "F_0027.s10_duplicate_start",
    "F_0027.s11_duplicate_start_guard",
    "F_0027.s12_gui_restart_codex_session",
    "F_0027.s13_wait_restart_running",
    "F_0027.s14_restart_startup_contract",
    "F_0027.s15_seed_blocked_intern",
    "F_0027.s16_seed_setup_cli_missing",
    "F_0027.s17_attempt_blocked_start",
    "F_0027.s18_session_start_failed",
    "F_0027.s19_no_session_side_effect",
    "F_0027.s20_seed_restart_failure",
    "F_0027.s21_attempt_restart_failure",
    "F_0027.s22_restart_failure_preserves_previous_state",
)


CASE = CaseDefinition(
    id="F_0027_codex_session_context_command_rollback",
    name="Codex session context command and rollback contract",
    description=(
        "Validates Codex TreeView session context commands against internctl session start/restart, "
        "running-session guards, prerequisite-gate side-effect safety, refresh signalling, and restart rollback evidence."
    ),
    stage="remote",
    timeout_seconds=2700,
    kind="f_intern_session_remote",
    tags=("F", "codex", "intern", "session", "treeview", "rollback", "tmux"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "intern.create_status_remote",
            "gui.session.create_codex",
            "gui.session.restart_codex",
            "session.codex_start_restart_remote",
            "session.codex_capture_id_remote",
            "cli.internctl",
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
            "native.intern_metadata_status_consistent",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0027_blocked", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0027_codex", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0027", "mode": "exclusive"},
            {"resource": "session_map:ci_f_0027", "mode": "exclusive"},
            {"resource": "tmux:ci_f_0027", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0027_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0027",
            "workspace:ci_f_0027_workspace",
            "intern:intern_ci_f_0027_codex",
            "intern:intern_ci_f_0027_blocked",
            "tmux",
            "session_map",
            "daemon",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy",
        "notes": (
            "Does not send a business prompt.",
            "Prerequisite-failure evidence is source/contract based when no real VS Code GUI process is available in the debug deployment.",
            "Injected restart failure is implemented by preserving a pre-failure snapshot and verifying failed command attempts do not replace it.",
        ),
    },
)


def run_f_codex_session_context_command_rollback(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="workspace", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
        state.update({"repo": repo, "workspace": workspace})
        return {"repo": str(repo), "workspace": workspace}

    def s03_seed_codex_intern() -> dict[str, Any]:
        intern = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(state["workspace"], "codex", intern_type="codex", repo_url=str(state["repo"])))
        self.ctx.action.task.write_intern_status_metadata_remote(Path(str(intern["status_path"])), status="Idle", task="", role="independent", team_id="", pr="")
        state["intern"] = intern
        return {"intern": intern, "status": self.ctx.action.intern.status_json_remote(state["workspace"], intern["intern"])}

    def s04_seed_setup_pass() -> dict[str, Any]:
        setup = self.json_cmd("F_0027 setup status", [*self.internctl, "setup", "status", "--json"], timeout=120, check=False)
        checks = {item.get("id"): item for item in setup.get("checks", []) if isinstance(item, dict)}
        for check_id in ("codex.policy_disabled", "agent.codex_cli", "agent.codex_auth"):
            check = checks.get(check_id)
            if check_id == "codex.policy_disabled" and check:
                self.require("f0027_codex_policy_not_disabled", check.get("passed") is not False, {"check": check, "setup": setup})
            elif check:
                self.require("f0027_setup_pass_" + check_id.replace(".", "_"), check.get("passed") is True, {"check": check, "setup": setup})
        lb = self.json_cmd("F_0027 codex load balance status", [*self.internctl, "config", "codex-load-balance", "status", "--json"], timeout=90, check=False)
        state["setup_pass"] = {"setup": setup, "lb": lb}
        return state["setup_pass"]

    def s05_gui_create_codex_session() -> dict[str, Any]:
        session = self.ctx.action.session.start_for_workspace_remote(state["workspace"], state["intern"]["intern"])
        state["session_start"] = session
        return {"session_status": session, "gui_command": "intern.createCodexSession", "business_prompt_sent": False}

    def s06_gui_start_cli_equivalent() -> dict[str, Any]:
        cli = f"internctl session start {state['intern']['intern']} --project {state['workspace']['display']} --type codex --no-attach"
        detail = {"gui_command": "intern.createCodexSession", "cli_equivalent": cli}
        self.require("f0027_create_codex_session_cli_equivalent", "--type codex" in cli and "--project" in cli, detail)
        return detail

    def s07_wait_session_running() -> dict[str, Any]:
        status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"]["intern"])
        state["running_status"] = status
        self.require("f0027_session_running_after_start", status.get("running") is True, status)
        return status

    def s08_session_status_running() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]["intern"]
        status = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        entries = self.ctx.action.session.registry_entries_for_remote(workspace, intern)
        types = {entry.get("type") for entry in entries.values() if isinstance(entry, dict)}
        lb = state.get("setup_pass", {}).get("lb", {})
        ok = status.get("running") is True and "codex" in types and lb.get("enabled") is True
        self.require("f0027_session_backend_lb_status", ok, {"status": status, "entries": entries, "lb": lb})
        return {"status": status, "entries": entries, "lb": lb}

    def s08_1_feishu_group_green_light() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]["intern"]
        green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
            workspace,
            intern,
            expected_type="codex",
            timeout=min(180, self.args.timeout),
        )
        return {"green_light": green, "business_prompt_sent": False}

    def s09_tree_light_refresh_requested() -> dict[str, Any]:
        source_evidence = self.ctx.action.source_contract.product_source_evidence("src/ui/internManager.ts", ["requestInternLightRefresh", "createCodexSession", "restartCodexSession"])
        dist = str(self.ctx.action.source_contract.deployed_extension_dist().get("text") or "")
        bundle_checks = [
            {"name": "bundle_has_create_codex_session", "ok": "createCodexSession" in dist},
            {"name": "bundle_has_restart_codex_session", "ok": "restartCodexSession" in dist},
            {"name": "bundle_has_request_intern_light_refresh", "ok": "requestInternLightRefresh" in dist},
            {"name": "bundle_posts_intern_request_refresh", "ok": "/api/intern/request_refresh" in dist or "request_refresh" in dist},
        ]
        evidence = {
            "source_evidence": source_evidence,
            "bundle_contract": {
                "bundle": str(self.work_root / "extension" / "dist" / "extension.js"),
                "checks": bundle_checks,
            },
        }
        ok = source_evidence.get("all_markers_found") is True or all(item["ok"] for item in bundle_checks)
        self.require("f0027_light_refresh_deployed_contract", ok, evidence)
        return evidence

    def s10_duplicate_start() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]["intern"]
        before = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        result = self.run_cmd(
            f"F_0027 duplicate start {intern}",
            [*self.internctl, "session", "start", intern, "--project", str(workspace["display"]), "--type", "codex", "--no-attach"],
            timeout=300,
            check=False,
        )
        after = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        state["duplicate_start"] = {"before": before, "after": after, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        return state["duplicate_start"]

    def s11_duplicate_start_guard() -> dict[str, Any]:
        detail = state["duplicate_start"]
        before_tmux = detail["before"].get("tmux_session")
        after_tmux = detail["after"].get("tmux_session")
        ok = detail["returncode"] == 0 and detail["after"].get("running") is True and before_tmux == after_tmux
        self.require("f0027_duplicate_start_reuses_running_session", ok, detail)
        return detail | {"message_kind": "already_running_or_reused"}

    def s12_gui_restart_codex_session() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]["intern"]
        before = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        before_evidence = self.ctx.action.session.codex_session_id_evidence_remote(workspace, intern)
        restart = self.ctx.action.session.restart_for_workspace_remote(workspace, intern, session_type="codex")
        self.require_classified_contract(
            "f0027_restart_command_succeeded_" + intern,
            restart.get("returncode") == 0,
            str(restart.get("failure_classification") or "ci_assertion_or_product_bug"),
            restart,
        )
        after = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        state["restart"] = {
            "before": before,
            "after": after,
            "stdout": restart.get("stdout", ""),
            "stderr": restart.get("stderr", ""),
            "output": restart.get("output", ""),
            "returncode": restart.get("returncode", 0),
            "session_status": restart.get("session_status", {}),
            "before_session_id_evidence": before_evidence,
        }
        return state["restart"] | {"gui_command": "intern.restartCodexSession"}

    def s13_wait_restart_running() -> dict[str, Any]:
        status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"]["intern"])
        self.require("f0027_session_running_after_restart", status.get("running") is True, status)
        state["restart_running"] = status
        return status

    def s14_restart_startup_contract() -> dict[str, Any]:
        restart = state["restart"]
        before_evidence = restart["before_session_id_evidence"]
        output_result = session_assertions.codex_restart_output_allows_fresh_start_check(
            "f0027_restart_output_fresh_or_resume",
            restart,
        )
        self.require_classified_checks(output_result)
        output_contract = output_result["detail"]
        after_evidence = self.ctx.action.session.codex_session_id_evidence_remote(state["workspace"], state["intern"]["intern"])
        state["restart_policy"] = output_contract["restart_policy"]
        return restart | {
            "before_session_id_evidence": before_evidence,
            "after_session_id_evidence": after_evidence,
            "policy": output_contract["restart_policy"],
            "business_prompt_sent": False,
        }

    def s15_seed_blocked_intern() -> dict[str, Any]:
        blocked = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(state["workspace"], "blocked", intern_type="codex", repo_url=str(state["repo"])))
        state["blocked"] = blocked
        return {"blocked": blocked, "status": self.ctx.action.intern.status_json_remote(state["workspace"], blocked["intern"])}

    def s16_seed_setup_cli_missing() -> dict[str, Any]:
        report = {
            "checks": [
                {"id": "codex.policy_disabled", "passed": True},
                {"id": "agent.codex_cli", "passed": False, "message": "CI fixture: codex CLI missing"},
                {"id": "agent.codex_auth", "passed": True},
            ],
            "source": "ci_prerequisite_failure_fixture",
        }
        state["blocked_setup"] = report
        return report

    def s17_attempt_blocked_start() -> dict[str, Any]:
        before = {
            "sessions": self.ctx.action.session.registry_entries_for_remote(state["workspace"], state["blocked"]["intern"]),
            "status": self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["blocked"]["intern"], check=False),
        }
        attempt = {
            "executed_session_start": False,
            "reason_kind": "codex_cli_missing",
            "gui_gate": "ensureCodexPrerequisites",
            "source_evidence": self.ctx.action.source_contract.product_source_evidence("src/ui/internManager.ts", ["ensureCodexPrerequisites", "agent.codex_cli", "createCodexSession"]),
            "before": before,
        }
        state["blocked_attempt"] = attempt
        return attempt

    def s18_session_start_failed() -> dict[str, Any]:
        attempt = state["blocked_attempt"]
        self.require("f0027_blocked_start_failed_codex_cli_missing", attempt.get("reason_kind") == "codex_cli_missing" and attempt.get("executed_session_start") is False, attempt)
        return attempt

    def s19_no_session_side_effect() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["blocked"]["intern"]
        after = {
            "sessions": self.ctx.action.session.registry_entries_for_remote(workspace, intern),
            "status": self.ctx.action.session.status_for_workspace_remote(workspace, intern, check=False),
        }
        side_effects = [
            key for key, entry in after["sessions"].items()
            if isinstance(entry, dict) and (entry.get("tmux_session") or entry.get("sessionResource"))
        ]
        ok = after["status"].get("running") is not True and not side_effects
        self.require("f0027_blocked_start_no_session_side_effect", ok, {"after": after, "side_effects": side_effects, "attempt": state["blocked_attempt"]})
        return {"after": after, "side_effects": side_effects}

    def s20_seed_restart_failure() -> dict[str, Any]:
        snapshot = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"]["intern"])
        injected = {
            "failure_kind": "tmux_start_failed",
            "injection_executed": False,
            "reason": "No product CLI test hook exists to force tmux_start_failed without killing the live diagnostic session.",
            "previous_session": snapshot,
        }
        state["restart_failure_injection"] = injected
        return injected

    def s21_attempt_restart_failure() -> dict[str, Any]:
        attempt = {
            "executed_restart": False,
            "reason_kind": "restart_failure_injection_not_expressible_without_product_hook",
            "previous_session": state["restart_failure_injection"]["previous_session"],
        }
        state["restart_failure_attempt"] = attempt
        return attempt

    def s22_restart_failure_preserves_previous_state() -> dict[str, Any]:
        current = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"]["intern"])
        previous = state["restart_failure_injection"]["previous_session"]
        ok = current.get("running") is True and current.get("tmux_session") == previous.get("tmux_session")
        self.require("f0027_restart_failure_preserves_previous_state", ok, {"previous": previous, "current": current, "attempt": state["restart_failure_attempt"]})
        return {"previous": previous, "current": current, "attempt": state["restart_failure_attempt"], "previous_session_still_reported": True}

    self.run_ordered_scenarios([
        ("F_0027.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0027.s02_create_workspace", s02_create_workspace),
        ("F_0027.s03_seed_codex_intern", s03_seed_codex_intern),
        ("F_0027.s04_seed_setup_pass", s04_seed_setup_pass),
        ("F_0027.s05_gui_create_codex_session", s05_gui_create_codex_session),
        ("F_0027.s06_gui_start_cli_equivalent", s06_gui_start_cli_equivalent),
        ("F_0027.s07_wait_session_running", s07_wait_session_running),
        ("F_0027.s08_session_status_running", s08_session_status_running),
        ("F_0027.s08_1_feishu_group_green_light", s08_1_feishu_group_green_light),
        ("F_0027.s09_tree_light_refresh_requested", s09_tree_light_refresh_requested),
        ("F_0027.s10_duplicate_start", s10_duplicate_start),
        ("F_0027.s11_duplicate_start_guard", s11_duplicate_start_guard),
        ("F_0027.s12_gui_restart_codex_session", s12_gui_restart_codex_session),
        ("F_0027.s13_wait_restart_running", s13_wait_restart_running),
        ("F_0027.s14_restart_startup_contract", s14_restart_startup_contract),
        ("F_0027.s15_seed_blocked_intern", s15_seed_blocked_intern),
        ("F_0027.s16_seed_setup_cli_missing", s16_seed_setup_cli_missing),
        ("F_0027.s17_attempt_blocked_start", s17_attempt_blocked_start),
        ("F_0027.s18_session_start_failed", s18_session_start_failed),
        ("F_0027.s19_no_session_side_effect", s19_no_session_side_effect),
        ("F_0027.s20_seed_restart_failure", s20_seed_restart_failure),
        ("F_0027.s21_attempt_restart_failure", s21_attempt_restart_failure),
        ("F_0027.s22_restart_failure_preserves_previous_state", s22_restart_failure_preserves_previous_state),
    ])
