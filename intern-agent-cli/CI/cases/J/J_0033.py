import os
import re
import time
from typing import Any

from CI.actions.session import parse_resume_this_intern_hint_text
from CI.assertions import session as session_assertions
from CI.cases.base import CaseDefinition
from CI.helpers.product_cli_helper import tail


SCENARIO_IDS = (
    "J_0033.s01_reset_case_namespace",
    "J_0033.s02_create_workspace_task_and_codex_group",
    "J_0033.s03_start_codex_session",
    "J_0033.s04_feishu_group_green_light_before_prompt",
    "J_0033.s05_send_user_hi_prompt_and_wait_reply",
    "J_0033.s06_capture_codex_session_id_after_user_turn",
    "J_0033.s07_exit_codex_to_tmux_shell",
    "J_0033.s08_capture_resume_this_intern_hint",
    "J_0033.s09_assert_resume_hint_command_contract",
    "J_0033.s10_run_resume_hint_in_tmux",
    "J_0033.s11_wait_codex_live_after_manual_resume",
    "J_0033.s12_assert_manual_resume_same_session_id",
    "J_0033.s13_restart_codex_session_after_user_turn",
    "J_0033.s14_assert_restart_resume_same_session_id",
    "J_0033.s15_final_session_scope_and_green_light",
)


CASE = CaseDefinition(
    id="J_0033_codex_exit_resume_same_session_journey",
    name="Codex exit resume same-session journey",
    description=(
        "Starts a real Codex intern journey, sends a user hi prompt to create a durable "
        "Codex session UUID, exits to tmux, executes the printed Resume this intern command, "
        "and verifies manual resume plus restart preserve the same UUID."
    ),
    stage="remote",
    timeout_seconds=3600,
    kind="f_intern_session_remote",
    tags=("J", "codex", "intern", "session", "restart", "resume", "tmux", "feishu"),
    parallel_safe=False,
    extra={
        "ci_stage": "J",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "create_feishu_group",
            "create_intern",
            "create_task",
            "start_intern_session",
            "send_user_message",
            "session.codex_start_restart_remote",
            "session.codex_capture_id_remote",
            "daemon.read_status",
            "relay.read_chat_presence",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "ctx.action_ok",
            "native.session_running",
            "native.feishu_group_green_light",
            "native.codex_exit_resume_hint",
            "native.codex_session_id_resume",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "feishu_chat:ci_j_0033", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_j_0033_codex", "mode": "exclusive"},
            {"resource": "llm:codex", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_j_0033", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "session:axis_intern_agents_backup:intern_ci_j_0033_codex", "mode": "exclusive"},
            {"resource": "task:axis_intern_agents_backup:task_ci_j_0033_codex_resume", "mode": "exclusive"},
            {"resource": "tmux:ci_j_0033", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_j_0033_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_j_0033",
            "workspace:ci_j_0033_workspace",
            "intern:intern_ci_j_0033_codex",
            "task:task_ci_j_0033_codex_resume",
            "case_scoped_feishu_group",
            "llm:codex",
            "tmux",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy_llm_user_prompt_journey",
        "journey_steps": (
            "Create a case-scoped workspace, task anchor, Codex intern, and Feishu group.",
            "Start Codex and send a minimal hi prompt so Codex creates a durable session UUID.",
            "Exit Codex, capture the printed Resume this intern command, execute it, and require the same UUID.",
            "Run the GUI-equivalent restart command and require restarted via resume with the same UUID.",
        ),
        "notes": (
            "This is J-scoped because it sends a real user prompt and waits for a Codex reply.",
            "F_0033 remains no-prompt and may accept fresh restart when no durable Codex UUID exists.",
            "Case initialization cleans only the ci_j_0033 namespace; successful runs retain scene evidence.",
        ),
    },
)


def run_j_codex_exit_resume_same_session_journey(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}
    shell_commands = {"", "bash", "sh", "zsh", "fish", "tmux"}

    def current_tmux_command(tmux_session: str) -> dict[str, Any]:
        result = self.run_cmd(
            f"tmux current command {tmux_session}",
            ["tmux", "display-message", "-p", "-t", f"={tmux_session}:", "#{pane_current_command}"],
            timeout=30,
            check=False,
        )
        return {
            "returncode": result.returncode,
            "current_command": result.stdout.strip(),
            "stderr": result.stderr,
        }

    def send_shell_command(tmux_session: str, command: str) -> dict[str, Any]:
        safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "_", tmux_session).strip("_") or "session"
        command_file = self.artifact_dir / f"{safe_session}-{time.time_ns()}.shell.txt"
        command_file.write_text(command, encoding="utf-8")
        buffer_name = f"ci-{safe_session}-{time.time_ns()}"
        clear = self.run_cmd(
            f"tmux clear shell command {tmux_session}",
            ["tmux", "send-keys", "-t", f"={tmux_session}:", "C-u"],
            timeout=30,
            check=False,
        )
        load = self.run_cmd(
            f"tmux load shell command {tmux_session}",
            ["tmux", "load-buffer", "-b", buffer_name, str(command_file)],
            timeout=30,
            check=False,
        )
        paste = self.run_cmd(
            f"tmux paste shell command {tmux_session}",
            ["tmux", "paste-buffer", "-d", "-p", "-b", buffer_name, "-t", f"={tmux_session}:"],
            timeout=30,
            check=False,
        )
        submit = self.run_cmd(
            f"tmux submit shell command {tmux_session}",
            ["tmux", "send-keys", "-t", f"={tmux_session}:", "Enter"],
            timeout=30,
            check=False,
        )
        detail = {
            "tmux_session": tmux_session,
            "command": command,
            "clear_returncode": clear.returncode,
            "load_returncode": load.returncode,
            "paste_returncode": paste.returncode,
            "submit_returncode": submit.returncode,
        }
        self.require_classified_contract(
            "j0033_tmux_shell_command_sent_" + state.get("intern", "unknown"),
            all(detail[key] == 0 for key in ("clear_returncode", "load_returncode", "paste_returncode", "submit_returncode")),
            "product_bug_exit_resume_hint_not_executable",
            detail,
        )
        return detail

    def wait_prompt_token_visible(tmux_session: str, token: str, *, min_count: int, timeout: int) -> dict[str, Any]:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = self.ctx.action.session.tmux_capture_remote(tmux_session, lines=3000)
            tail_text = last[-6000:]
            still_working = "Working (" in tail_text or "Running UserPromptSubmit hook" in tail_text or "Running Stop hook" in tail_text
            count = last.count(token)
            if count >= min_count and not still_working:
                return {
                    "visible": True,
                    "token": token,
                    "token_count": count,
                    "tail": tail(tail_text, 1000),
                }
            time.sleep(3)
        detail = {
            "visible": False,
            "token": token,
            "token_count": last.count(token),
            "tail": tail(last, 2000),
        }
        self.require("j0033_prompt_token_visible_after_user_turn", False, detail)
        return detail

    def wait_resume_hint_after_exit(tmux_session: str, *, timeout: int) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        extra_enter_count = 0
        next_extra_enter_at = time.time() + 10
        while time.time() < deadline:
            pane_text = self.ctx.action.session.tmux_capture_joined_remote(tmux_session, lines=320)
            parsed = parse_resume_this_intern_hint_text(pane_text)
            command = current_tmux_command(tmux_session)
            last = {
                "tmux_session": tmux_session,
                "parsed": parsed,
                "current_command": command,
                "extra_enter_count": extra_enter_count,
                "pane_tail": tail(pane_text, 5000),
            }
            if parsed:
                return parsed | last
            current = str(command.get("current_command") or "")
            if current not in shell_commands and extra_enter_count < 3 and time.time() >= next_extra_enter_at:
                extra = self.run_cmd(
                    f"tmux extra enter after codex exit {tmux_session}",
                    ["tmux", "send-keys", "-t", f"={tmux_session}:", "Enter"],
                    timeout=30,
                    check=False,
                )
                last["extra_enter"] = {"returncode": extra.returncode, "stderr": extra.stderr}
                extra_enter_count += 1
                next_extra_enter_at = time.time() + 12
            time.sleep(2)
        self.require_classified_contract(
            "j0033_codex_exit_resume_hint_present_" + state.get("intern", "unknown"),
            False,
            "product_bug_exit_resume_hint_missing",
            last,
        )
        return last

    def assert_same_codex_session_id(label: str, before: dict[str, Any], after: dict[str, Any], classification: str) -> dict[str, Any]:
        before_available = session_assertions.codex_session_id_available_check(label + "_before_available", before)
        after_available = session_assertions.codex_session_id_available_check(label + "_after_available", after)
        self.require_classified_checks(before_available)
        self.require_classified_checks(after_available)
        before_id = str(before_available["session_id"])
        after_id = str(after_available["session_id"])
        detail = {
            "before_session_id": before_id,
            "after_session_id": after_id,
            "before_evidence": before,
            "after_evidence": after,
        }
        self.require_classified_contract(
            label + "_same_session_id",
            before_id == after_id,
            classification,
            detail,
        )
        return detail | {"same_session_id": True}

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace_task_and_codex_group() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        metadata_root = self.ctx.action.workspace.metadata_root_remote(workspace)
        task_id = self.task_id("codex_resume")
        task = self.ctx.action.task.write_fixture_remote(metadata_root, task_id, status="Open", assignee="")
        created = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(
            workspace,
            "codex",
            intern_type="codex",
            repo_url=str(repo),
            skip_feishu_group=False,
            skip_status_notify=True,
        ))
        intern = created["intern"]
        chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace, intern, timeout=self.args.timeout)
        relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace, intern, timeout=self.args.timeout)
        state.update({
            "repo": repo,
            "workspace": workspace,
            "metadata_root": metadata_root,
            "task_id": task_id,
            "task": task,
            "intern": intern,
            "created": created,
            "chat": chat,
            "relay": relay,
        })
        return {
            "repo": str(repo),
            "workspace": workspace,
            "task_id": task_id,
            "task": task,
            "intern": created,
            "chat_lookup": chat,
            "relay_registry": relay,
        }

    def s03_start_codex_session() -> dict[str, Any]:
        status = self.ctx.action.session.start_for_workspace_remote(state["workspace"], state["intern"], session_type="codex")
        tmux_session = str(status.get("tmux_session") or state["intern"])
        ready = self.ctx.action.session.wait_tmux_input_ready_remote(tmux_session, timeout=240)
        state["tmux_session"] = tmux_session
        return {"session_status": status, "tmux_session": tmux_session, "ready": ready}

    def s04_feishu_group_green_light_before_prompt() -> dict[str, Any]:
        green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
            state["workspace"],
            state["intern"],
            expected_type="codex",
            timeout=min(180, self.args.timeout),
        )
        return {"green_light": green}

    def s05_send_user_hi_prompt_and_wait_reply() -> dict[str, Any]:
        token = f"READY_J0033_{int(time.time())}_{os.getpid()}"
        prompt = "hi. Please reply exactly with this token and do not run commands: " + token
        self.ctx.action.session.tmux_send_remote(str(state["tmux_session"]), prompt)
        visible = wait_prompt_token_visible(str(state["tmux_session"]), token, min_count=2, timeout=self.args.timeout)
        state["prompt_token"] = token
        state["prompt_text"] = prompt
        return {
            "business_prompt_sent": True,
            "token": token,
            "visible": visible,
            "journey_action": "send_user_message",
        }

    def s06_capture_codex_session_id_after_user_turn() -> dict[str, Any]:
        evidence = self.ctx.action.session.codex_session_id_evidence_remote(state["workspace"], state["intern"])
        available = session_assertions.codex_session_id_available_check("j0033_codex_session_id_after_user_turn", evidence)
        self.require_classified_checks(available)
        session_id = str(available["session_id"])
        state["session_id_before"] = evidence
        state["session_id_before_value"] = session_id
        return evidence | {"business_prompt_sent": True}

    def s07_exit_codex_to_tmux_shell() -> dict[str, Any]:
        tmux_session = str(state["tmux_session"])
        before = self.ctx.action.session.tmux_capture_joined_remote(tmux_session, lines=160)
        clear = self.run_cmd(
            f"tmux clear codex exit input {tmux_session}",
            ["tmux", "send-keys", "-t", f"={tmux_session}:", "C-u"],
            timeout=30,
            check=False,
        )
        sent = self.run_cmd(
            f"tmux send codex exit {tmux_session}",
            ["tmux", "send-keys", "-t", f"={tmux_session}:", "/exit", "Enter"],
            timeout=30,
            check=False,
        )
        detail = {
            "tmux_session": tmux_session,
            "clear_returncode": clear.returncode,
            "send_returncode": sent.returncode,
            "stdout": sent.stdout,
            "stderr": sent.stderr,
            "pane_tail_before_exit": tail(before, 3000),
            "business_prompt_sent": True,
        }
        self.require_classified_contract(
            "j0033_codex_exit_command_sent_" + state["intern"],
            clear.returncode == 0 and sent.returncode == 0,
            "product_bug_exit_resume_hint_missing",
            detail,
        )
        return detail

    def s08_capture_resume_this_intern_hint() -> dict[str, Any]:
        detail = wait_resume_hint_after_exit(str(state["tmux_session"]), timeout=min(240, self.args.timeout))
        state["resume_hint"] = detail
        return detail | {"business_prompt_sent": True}

    def s09_assert_resume_hint_command_contract() -> dict[str, Any]:
        command = str(state["resume_hint"].get("command") or "")
        contract_result = session_assertions.resume_hint_command_contract_check(
            "j0033_codex_exit_resume_hint_command_contract",
            command,
            workspace=state["workspace"],
            intern=state["intern"],
            session_type="codex",
        )
        self.require_classified_checks(contract_result)
        contract = contract_result["detail"]
        state["resume_hint_contract"] = contract
        return contract | {"business_prompt_sent": True}

    def s10_run_resume_hint_in_tmux() -> dict[str, Any]:
        command = str(state["resume_hint"].get("command") or "")
        detail = send_shell_command(str(state["tmux_session"]), command)
        return detail | {"business_prompt_sent": True}

    def s11_wait_codex_live_after_manual_resume() -> dict[str, Any]:
        live = self.ctx.action.session.wait_codex_live_after_manual_resume_remote(
            state["workspace"],
            state["intern"],
            str(state["tmux_session"]),
            timeout=self.args.timeout,
        )
        if live.get("resume_failure_seen") or live.get("ready", {}).get("ready") is not True:
            self.require_classified_contract(
                "j0033_codex_exit_resume_hint_command_executable_" + state["intern"],
                False,
                "product_bug_exit_resume_hint_loses_session_id",
                live,
            )
        evidence = self.ctx.action.session.codex_session_id_evidence_remote(state["workspace"], state["intern"])
        resumed_id = str(live.get("resumed_session_id") or "")
        if resumed_id and not evidence.get("session_id"):
            evidence = evidence | {
                "session_id": resumed_id,
                "source": "manual_resume_pane",
                "available": True,
            }
        state["manual_resume_live"] = live
        state["session_id_after_manual_resume"] = evidence
        return {"live": live, "session_id_evidence": evidence, "business_prompt_sent": True}

    def s12_assert_manual_resume_same_session_id() -> dict[str, Any]:
        return assert_same_codex_session_id(
            "j0033_manual_resume",
            state["session_id_before"],
            state["session_id_after_manual_resume"],
            "product_bug_exit_resume_hint_loses_session_id",
        ) | {"business_prompt_sent": True}

    def s13_restart_codex_session_after_user_turn() -> dict[str, Any]:
        restart = self.ctx.action.session.restart_for_workspace_remote(state["workspace"], state["intern"], session_type="codex")
        self.require_classified_contract(
            "j0033_restart_command_succeeded_" + state["intern"],
            restart.get("returncode") == 0,
            str(restart.get("failure_classification") or "ci_assertion_or_product_bug"),
            restart,
        )
        state["restart"] = restart
        tmux_session = str(restart.get("tmux_session") or state["tmux_session"])
        state["tmux_session"] = tmux_session
        ready = self.ctx.action.session.wait_tmux_input_ready_remote(tmux_session, timeout=240)
        return restart | {
            "ready": ready,
            "business_prompt_sent": True,
            "gui_command": "intern.restartCodexSession",
            "cli_equivalent": "internctl session restart <intern> --project <project> --type codex --no-attach",
        }

    def s14_assert_restart_resume_same_session_id() -> dict[str, Any]:
        restart_result = session_assertions.codex_restart_output_requires_resume_check(
            "j0033_restart_after_user_turn",
            state["restart"],
            before_session_id=state["session_id_before_value"],
        )
        self.require_classified_checks(restart_result)
        restart_contract = restart_result["detail"]
        after = self.ctx.action.session.codex_session_id_evidence_remote(state["workspace"], state["intern"])
        output_id = str(restart_contract.get("output_session_id") or "")
        if output_id and not after.get("session_id"):
            after = after | {"session_id": output_id, "source": "restart_command_output", "available": True}
        same = assert_same_codex_session_id(
            "j0033_restart_resume",
            state["session_id_before"],
            after,
            "product_bug_restart_loses_session_id",
        )
        state["session_id_after_restart"] = after
        return restart_contract | same | {"business_prompt_sent": True}

    def s15_final_session_scope_and_green_light() -> dict[str, Any]:
        status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"])
        daemon = self.http_json("J_0033 daemon status", "GET", "/api/status", timeout=30)
        entries = self.ctx.action.session.registry_entries_for_remote(state["workspace"], state["intern"])
        green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
            state["workspace"],
            state["intern"],
            expected_type="codex",
            timeout=min(180, self.args.timeout),
        )
        detail = {
            "session_status": status,
            "daemon": daemon,
            "session_entries": entries,
            "green_light": green,
            "task_id": state["task_id"],
        }
        self.require(
            "j0033_final_session_scope_stable",
            status.get("running") is True and daemon.get("relay_connected") is True and bool(entries),
            detail,
        )
        return detail | {"business_prompt_sent": True}

    self.run_ordered_scenarios([
        ("J_0033.s01_reset_case_namespace", s01_reset_case_namespace),
        ("J_0033.s02_create_workspace_task_and_codex_group", s02_create_workspace_task_and_codex_group),
        ("J_0033.s03_start_codex_session", s03_start_codex_session),
        ("J_0033.s04_feishu_group_green_light_before_prompt", s04_feishu_group_green_light_before_prompt),
        ("J_0033.s05_send_user_hi_prompt_and_wait_reply", s05_send_user_hi_prompt_and_wait_reply),
        ("J_0033.s06_capture_codex_session_id_after_user_turn", s06_capture_codex_session_id_after_user_turn),
        ("J_0033.s07_exit_codex_to_tmux_shell", s07_exit_codex_to_tmux_shell),
        ("J_0033.s08_capture_resume_this_intern_hint", s08_capture_resume_this_intern_hint),
        ("J_0033.s09_assert_resume_hint_command_contract", s09_assert_resume_hint_command_contract),
        ("J_0033.s10_run_resume_hint_in_tmux", s10_run_resume_hint_in_tmux),
        ("J_0033.s11_wait_codex_live_after_manual_resume", s11_wait_codex_live_after_manual_resume),
        ("J_0033.s12_assert_manual_resume_same_session_id", s12_assert_manual_resume_same_session_id),
        ("J_0033.s13_restart_codex_session_after_user_turn", s13_restart_codex_session_after_user_turn),
        ("J_0033.s14_assert_restart_resume_same_session_id", s14_assert_restart_resume_same_session_id),
        ("J_0033.s15_final_session_scope_and_green_light", s15_final_session_scope_and_green_light),
    ])
