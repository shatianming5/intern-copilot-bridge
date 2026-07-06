from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from CI.cases.base import CaseDefinition
from CI.helpers.product_cli_helper import tail


SCENARIO_IDS = (
    "F_0052.s01_reset_case_namespace",
    "F_0052.s02_create_local_only_workspace",
    "F_0052.s03_create_claude_intern_no_group",
    "F_0052.s04_prepare_claude_policy_token_redacted",
    "F_0052.s05_start_claude_session_no_prompt",
    "F_0052.s06_wait_claude_live_and_capture_uuid",
    "F_0052.s07_exit_claude_to_tmux_shell",
    "F_0052.s08_wait_tmux_shell_after_exit",
    "F_0052.s09_restart_claude_session_after_exit",
    "F_0052.s10_assert_restart_resume_uuid_stable",
    "F_0052.s11_wait_claude_live_after_restart",
)


CASE = CaseDefinition(
    id="F_0052_session_resume_cli_claude_contract",
    name="Session resume CLI Claude contract",
    description=(
        "Remote debug validation for Claude session CLI resume/restart behavior. It creates a real case-scoped "
        "Claude intern, starts a no-prompt Claude session, exits to tmux shell, then verifies internctl session "
        "restart resumes the same durable Claude UUID instead of fresh-starting or relying on a mocked fixture."
    ),
    stage="remote",
    timeout_seconds=2400,
    kind="f_intern_session_remote",
    tags=("F", "intern", "session", "claude", "tmux", "cli", "daemon", "debug"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "session.claude_start_restart_remote",
            "claude.prepare_policy_token",
            "create_intern",
            "start_intern_session",
            "daemon.read_status",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.action_ok",
            "native.intern_metadata_status_consistent",
            "native.claude_runtime_files",
            "native.claude_restart_resume_uuid",
            "native.claude_resume_uuid_stable",
            "native.claude_session_scope_stable",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0052_claude", "mode": "exclusive"},
            {"resource": "llm:claude", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0052", "mode": "exclusive"},
            {"resource": "policy_alias:sk-xiaohan.yi", "mode": "read"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "tmux:ci_f_0052", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0052_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0052",
            "workspace:ci_f_0052_workspace",
            "intern:intern_ci_f_0052_claude",
            "llm:claude",
            "policy_alias:sk-xiaohan.yi",
            "tmux",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy_llm_no_business_prompt",
        "notes": (
            "Run with --use-existing-deployment for focused validation; full F may package/deploy/bootstrap first.",
            "Does not send natural-language business prompts to Claude and does not create a Feishu group.",
            "This is a real remote F case, not a local mocked tmux/transcript fixture.",
            "Case initialization only cleans the ci_f_0052 namespace; successful runs retain workspace, intern, tmux session, and report evidence.",
        ),
    },
)


def run_f_session_resume_cli_claude_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}
    uuid_re = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
    shell_commands = {"", "bash", "sh", "zsh", "fish", "tmux"}

    def claude_latest_session_id(runtime: Path) -> dict[str, Any]:
        detail = {
            "source": "commands.session._latest_claude_session_id",
            "runtime": str(runtime),
            "session_id": "",
            "available": False,
            "error": "",
        }
        try:
            from commands import session as session_cmd

            session_id = str(session_cmd._latest_claude_session_id(str(runtime)) or "")
        except Exception as exc:  # noqa: BLE001
            detail["error"] = repr(exc)
            return detail
        detail["session_id"] = session_id
        detail["available"] = bool(uuid_re.fullmatch(session_id))
        return detail

    def current_tmux_command(tmux_session: str) -> dict[str, Any]:
        result = self.run_cmd(
            f"tmux current command {tmux_session}",
            ["tmux", "list-panes", "-t", f"={tmux_session}", "-F", "#{pane_current_command}"],
            timeout=30,
            check=False,
        )
        current = result.stdout.splitlines()[0].strip().lower() if result.stdout.strip() else ""
        return {
            "tmux_session": tmux_session,
            "returncode": result.returncode,
            "current_command": current,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def wait_tmux_shell_after_exit(tmux_session: str, *, timeout: int = 120) -> dict[str, Any]:
        deadline = time.time() + timeout
        background_prompt_handled = False
        last: dict[str, Any] = {}
        while time.time() < deadline:
            command = current_tmux_command(tmux_session)
            pane_tail = self.ctx.action.session.tmux_capture_remote(tmux_session, lines=160)
            last = {
                "tmux_session": tmux_session,
                "current": command,
                "pane_tail": tail(pane_tail, 3000),
                "background_prompt_handled": background_prompt_handled,
            }
            if command.get("returncode") == 0 and str(command.get("current_command") or "") in shell_commands:
                return {**last, "shell_ready": True}
            if not background_prompt_handled and "Background work is running" in pane_tail:
                self.run_cmd(
                    f"tmux accept claude background prompt {tmux_session}",
                    ["tmux", "send-keys", "-t", f"={tmux_session}:", "Enter"],
                    timeout=30,
                    check=False,
                )
                background_prompt_handled = True
            time.sleep(1)
        self.require_classified_contract(
            "f0052_claude_exit_returns_tmux_shell_" + state["intern"],
            False,
            "product_bug_claude_exit_resume_hint_missing",
            last,
        )
        return last

    def require_claude_live(label: str) -> dict[str, Any]:
        live = self.ctx.action.session.wait_provider_session_live_remote(
            state["workspace"],
            state["intern"],
            provider="claude",
            timeout=self.args.timeout,
        )
        self.require_classified_contract(
            f"f0052_claude_live_{label}_" + state["intern"],
            live.get("session_status", {}).get("running") is True
            and bool(live.get("processes", {}).get("matches"))
            and live.get("ready", {}).get("ready") is True,
            "product_bug_claude_session_not_live",
            live,
        )
        state["tmux_session"] = str(live.get("session_status", {}).get("tmux_session") or state.get("tmux_session") or state["intern"])
        return live

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_local_only_workspace() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace")
        display = self.remote_context.stage_workspace_display("workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace",
            display_name=display,
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        state.update({"repo": repo, "workspace": workspace})
        return {"repo": str(repo), "workspace": workspace}

    def s03_create_claude_intern_no_group() -> dict[str, Any]:
        intern = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(
            state["workspace"],
            "claude",
            intern_type="claude",
            repo_url=str(state["repo"]),
            skip_feishu_group=True,
            skip_status_notify=True,
        ))
        state["intern"] = intern["intern"]
        state["runtime"] = Path(str(intern["runtime"]))
        return {"intern": intern, "feishu_group_created": False}

    def s04_prepare_claude_policy_token_redacted() -> dict[str, Any]:
        return self.ctx.action.session.prepare_claude_policy_token_remote()

    def s05_start_claude_session_no_prompt() -> dict[str, Any]:
        start = self.run_cmd(
            f"session start {state['intern']} scoped",
            [
                *self.internctl,
                "session",
                "start",
                state["intern"],
                "--project",
                str(state["workspace"]["display"]),
                "--type",
                "claude",
                "--no-attach",
            ],
            timeout=300,
            check=False,
        )
        if start.returncode != 0:
            combined = start.stdout + start.stderr
            lowered = combined.lower()
            classification = (
                "ci_capability_gap_claude_runtime"
                if "claude executable not found" in lowered or "claude binary unavailable" in lowered
                else "ci_capability_gap_claude_token_policy"
                if any(marker in lowered for marker in ("auth", "token", "credential", "oauth", "api key"))
                else "product_bug_claude_session_not_live"
            )
            self.require_classified_contract(
                "f0052_claude_session_start_existing_deployment",
                False,
                classification,
                {"returncode": start.returncode, "stdout": start.stdout, "stderr": start.stderr},
            )
        status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"])
        state["tmux_session"] = str(status.get("tmux_session") or state["intern"])
        self.created["sessions"].append(state["tmux_session"])
        self.require_classified_contract(
            "f0052_claude_session_start_running_" + state["intern"],
            status.get("running") is True,
            "product_bug_claude_session_not_live",
            status,
        )
        return {
            "session_status": status,
            "cli_equivalent": "internctl session start <intern> --project <project> --type claude --no-attach",
            "business_prompt_sent": False,
        }

    def s06_wait_claude_live_and_capture_uuid() -> dict[str, Any]:
        live = require_claude_live("after_start")
        session_id = claude_latest_session_id(state["runtime"])
        self.require_classified_contract(
            "f0052_claude_initial_uuid_discoverable_" + state["intern"],
            session_id.get("available") is True,
            "ci_capability_gap_claude_uuid_discovery",
            {"live": live, "session_id": session_id},
        )
        state["before_uuid"] = str(session_id["session_id"])
        return {"live": live, "session_id": session_id, "business_prompt_sent": False}

    def s07_exit_claude_to_tmux_shell() -> dict[str, Any]:
        before = self.ctx.action.session.tmux_capture_remote(state["tmux_session"], lines=120)
        result = self.run_cmd(
            f"tmux send claude exit {state['tmux_session']}",
            ["tmux", "send-keys", "-t", f"={state['tmux_session']}:", "/exit", "Enter"],
            timeout=30,
            check=False,
        )
        detail = {
            "tmux_session": state["tmux_session"],
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "pane_tail_before_exit": tail(before, 3000),
            "business_prompt_sent": False,
        }
        self.require_classified_contract(
            "f0052_claude_exit_command_sent_" + state["intern"],
            result.returncode == 0,
            "product_bug_claude_exit_resume_hint_missing",
            detail,
        )
        return detail

    def s08_wait_tmux_shell_after_exit() -> dict[str, Any]:
        detail = wait_tmux_shell_after_exit(state["tmux_session"], timeout=120)
        state["post_exit_shell"] = detail
        return {**detail, "business_prompt_sent": False}

    def s09_restart_claude_session_after_exit() -> dict[str, Any]:
        restart = self.ctx.action.session.restart_for_workspace_remote(
            state["workspace"],
            state["intern"],
            session_type="claude",
        )
        state["restart"] = restart
        self.require_classified_contract(
            "f0052_claude_restart_command_succeeded_" + state["intern"],
            restart.get("returncode") == 0,
            str(restart.get("failure_classification") or "product_bug_claude_restart_not_resume"),
            restart,
        )
        return {
            **restart,
            "cli_equivalent": "internctl session restart <intern> --project <project> --type claude --no-attach",
            "business_prompt_sent": False,
        }

    def s10_assert_restart_resume_uuid_stable() -> dict[str, Any]:
        restart = state["restart"]
        before_uuid = str(state.get("before_uuid") or "")
        resume_uuid = str(restart.get("resume_uuid") or "")
        detail = {
            "before_uuid": before_uuid,
            "restart_resume_uuid": resume_uuid,
            "restart": restart,
            "post_exit_shell": state.get("post_exit_shell"),
        }
        self.require_classified_contract(
            "f0052_claude_restart_reported_resume_" + state["intern"],
            bool(restart.get("reported_resume")) and not restart.get("reported_fresh") and bool(resume_uuid),
            "product_bug_claude_restart_not_resume",
            detail,
        )
        self.require_classified_contract(
            "f0052_claude_restart_uuid_stable_" + state["intern"],
            bool(before_uuid) and before_uuid == resume_uuid,
            "product_bug_claude_restart_loses_uuid",
            detail,
        )
        state["restart_resume_uuid"] = resume_uuid
        return detail

    def s11_wait_claude_live_after_restart() -> dict[str, Any]:
        live = require_claude_live("after_restart")
        final_uuid = claude_latest_session_id(state["runtime"])
        detail = {"live": live, "final_session_id": final_uuid, "restart_resume_uuid": state.get("restart_resume_uuid")}
        self.require_classified_contract(
            "f0052_claude_final_uuid_stable_" + state["intern"],
            final_uuid.get("session_id") == state.get("restart_resume_uuid"),
            "product_bug_claude_restart_loses_uuid",
            detail,
        )
        return {**detail, "business_prompt_sent": False}

    self.run_ordered_scenarios([
        ("F_0052.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0052.s02_create_local_only_workspace", s02_create_local_only_workspace),
        ("F_0052.s03_create_claude_intern_no_group", s03_create_claude_intern_no_group),
        ("F_0052.s04_prepare_claude_policy_token_redacted", s04_prepare_claude_policy_token_redacted),
        ("F_0052.s05_start_claude_session_no_prompt", s05_start_claude_session_no_prompt),
        ("F_0052.s06_wait_claude_live_and_capture_uuid", s06_wait_claude_live_and_capture_uuid),
        ("F_0052.s07_exit_claude_to_tmux_shell", s07_exit_claude_to_tmux_shell),
        ("F_0052.s08_wait_tmux_shell_after_exit", s08_wait_tmux_shell_after_exit),
        ("F_0052.s09_restart_claude_session_after_exit", s09_restart_claude_session_after_exit),
        ("F_0052.s10_assert_restart_resume_uuid_stable", s10_assert_restart_resume_uuid_stable),
        ("F_0052.s11_wait_claude_live_after_restart", s11_wait_claude_live_after_restart),
    ])
