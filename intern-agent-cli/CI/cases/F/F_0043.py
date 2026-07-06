import json
from pathlib import Path
import re
import shlex
import time
from typing import Any

from CI.cases.base import CaseDefinition
from CI.helpers.native_error import NativeCaseError
from CI.helpers.product_cli_helper import tail


UUID_PATTERN = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"


def claude_session_uuid_from_text(text: str) -> str:
    patterns = (
        rf"(?:^|\s)--session-id(?:=|\s+){UUID_PATTERN}",
        rf"(?:^|\s)--resume(?:=|\s+){UUID_PATTERN}",
        rf"\bresumed\s+{UUID_PATTERN}",
    )
    for line in reversed(str(text or "").splitlines()):
        for pattern in patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                return match.group(1)
    return ""


def claude_session_uuid_from_processes(processes: dict[str, Any]) -> str:
    matches = processes.get("matches") if isinstance(processes, dict) else []
    resume_id = ""
    for item in matches if isinstance(matches, list) else []:
        args = str(item.get("args_tail") or "") if isinstance(item, dict) else ""
        current_session = claude_session_uuid_from_text(" ".join(line for line in args.splitlines() if "--session-id" in line))
        if current_session:
            return current_session
        current_resume = claude_session_uuid_from_text(" ".join(line for line in args.splitlines() if "--resume" in line))
        if current_resume and not resume_id:
            resume_id = current_resume
    return resume_id


def claude_live_session_uuid_from_evidence(session_id_after: dict[str, Any]) -> str:
    candidates = session_id_after.get("candidates") if isinstance(session_id_after, dict) else []
    live_candidates = [
        item for item in candidates if isinstance(item, dict) and item.get("kind") == "live_session" and item.get("session_id")
    ]
    live_candidates.sort(key=lambda item: float(item.get("mtime") or 0), reverse=True)
    return str(live_candidates[0].get("session_id") or "") if live_candidates else ""


def claude_resumed_uuid_from_current_evidence(
    *,
    processes: dict[str, Any],
    session_id_after: dict[str, Any],
    pane_text: str,
) -> dict[str, str]:
    for source, value in (
        ("process_args", claude_session_uuid_from_processes(processes)),
        ("live_session", claude_live_session_uuid_from_evidence(session_id_after)),
        ("pane_text", claude_session_uuid_from_text(pane_text)),
        ("session_id_evidence", str(session_id_after.get("session_id") or "") if isinstance(session_id_after, dict) else ""),
    ):
        if value:
            return {"session_id": value, "source": source}
    return {"session_id": "", "source": ""}


SCENARIO_IDS = (
    "F_0043.s01_reset_case_namespace",
    "F_0043.s02_create_local_only_workspace",
    "F_0043.s03_prepare_claude_policy_token_redacted",
    "F_0043.s04_cli_create_claude_intern",
    "F_0043.s05_list_status_metadata_session_type",
    "F_0043.s06_feishu_group_type_claude",
    "F_0043.s07_start_claude_session_no_prompt",
    "F_0043.s08_wait_claude_session_live",
    "F_0043.s08_1_feishu_group_green_light",
    "F_0043.s09_claude_runtime_files_and_env",
    "F_0043.s10_exit_claude_to_tmux_shell",
    "F_0043.s11_wait_tmux_shell_after_provider_exit",
    "F_0043.s12_capture_resume_this_intern_hint",
    "F_0043.s13_assert_resume_hint_command_contract",
    "F_0043.s14_run_resume_hint_in_tmux",
    "F_0043.s15_wait_claude_live_after_manual_resume",
    "F_0043.s16_assert_manual_resume_uuid_stable",
    "F_0043.s17_restart_claude_session_resume",
    "F_0043.s18_assert_restart_resume_uuid",
    "F_0043.s19_wait_claude_live_after_restart",
    "F_0043.s20_restart_claude_session_second_resume",
    "F_0043.s21_assert_resume_uuid_stable",
    "F_0043.s22_session_scope_stable_relay_connected",
    "F_0043.s23_final_feishu_group_green_light",
)


CASE = CaseDefinition(
    id="F_0043_claude_intern_create_session_lifecycle_contract",
    name="F_0043_claude_intern_create_session_lifecycle_contract",
    description=(
        "Validates Claude intern creation, status/list/session-map/group type consistency, "
        "Claude no-prompt session start, live process detection, /exit Resume this intern hint execution, "
        "and repeated restart via stable resume UUID."
    ),
    stage="remote",
    timeout_seconds=3600,
    kind="f_intern_session_remote",
    tags=("F", "intern", "session", "claude", "tmux", "gui", "cli", "daemon", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "session.claude_start_restart_remote",
            "claude.prepare_policy_token",
            "gui.intern.create",
            "gui.session.create_claude",
            "gui.session.restart_claude",
            "create_intern",
            "start_intern_session",
            "daemon.read_status",
            "relay.read_chat_presence",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "ctx.action_ok",
            "native.intern_metadata_status_consistent",
            "native.relay_registry_entry",
            "native.feishu_group_green_light",
            "native.claude_intern_type_consistent",
            "native.claude_group_type",
            "native.claude_runtime_files",
            "native.claude_exit_resume_hint",
            "native.claude_manual_resume_uuid_stable",
            "native.claude_restart_resume_uuid",
            "native.claude_resume_uuid_stable",
            "native.claude_session_scope_stable",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "feishu_chat:ci_f_0043", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0043_claude", "mode": "exclusive"},
            {"resource": "llm:claude", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0043", "mode": "exclusive"},
            {"resource": "policy_alias:sk-xiaohan.yi", "mode": "read"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "task:axis_intern_agents_backup:task_ci_f_0043", "mode": "exclusive"},
            {"resource": "tmux:ci_f_0043", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0043_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0043",
            "workspace:ci_f_0043_workspace",
            "intern:intern_ci_f_0043_claude",
            "task:task_ci_f_0043",
            "case_scoped_feishu_group",
            "llm:claude",
            "policy_alias:sk-xiaohan.yi",
            "tmux",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy_llm_no_business_prompt",
        "notes": (
            "Run with --use-existing-deployment; do not package, reset/deploy, bootstrap, install VSIX/hooks, or restart relay.",
            "Do not send a natural-language business prompt to Claude and do not create a Team.",
            "Claude policy/token evidence must be redacted; the alias sk-xiaohan.yi may be reported only by label.",
            "Case initialization only cleans the ci_f_0043 namespace; successful runs retain workspace, intern, group, session, and report evidence.",
        ),
    },
)


def run_f_claude_intern_create_session_lifecycle_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}
    shell_commands = {"", "bash", "sh", "zsh", "fish", "tmux"}

    def assert_restart_resume_uuid(restart: dict[str, Any], *, label: str) -> dict[str, Any]:
        if not restart.get("reported_resume") or restart.get("reported_fresh"):
            self.require_classified_contract(
                f"claude_restart_reported_resume_{label}",
                False,
                "product_bug_claude_restart_not_resume",
                restart,
            )
        if not restart.get("resume_uuid"):
            self.require_classified_contract(
                f"claude_restart_uuid_present_{label}",
                False,
                "ci_capability_gap_claude_uuid_discovery",
                restart,
            )
        self.require_classified_contract(
            f"claude_restart_running_{label}",
            restart.get("session_status", {}).get("running") is True,
            "product_bug_claude_restart_not_resume",
            restart,
        )
        return {
            "restart": restart,
            "resume_uuid": str(restart.get("resume_uuid") or ""),
            "business_prompt_sent": False,
        }

    def require_provider_session_live(provider: str, *, timeout: int) -> dict[str, Any]:
        live = self.ctx.action.session.wait_provider_session_live_remote(
            state["workspace"],
            state["intern"],
            provider=provider,
            timeout=timeout,
        )
        if live.get("ready", {}).get("ready") is True:
            return live
        self.require_classified_contract(
            f"{provider}_session_live_{state['intern']}",
            False,
            f"product_bug_{provider}_session_not_live",
            live,
        )
        return live

    def restart_claude_session() -> dict[str, Any]:
        restart = self.ctx.action.session.restart_for_workspace_remote(
            state["workspace"],
            state["intern"],
            session_type="claude",
        )
        self.require_classified_contract(
            "claude_restart_command_succeeded_" + state["intern"],
            restart.get("returncode") == 0,
            str(restart.get("failure_classification") or "product_bug_claude_session_not_live"),
            restart,
        )
        return restart

    def current_tmux_command(tmux_session: str) -> dict[str, Any]:
        result = self.run_cmd(
            f"tmux current command {tmux_session}",
            ["tmux", "list-panes", "-t", f"={tmux_session}", "-F", "#{pane_current_command}"],
            timeout=30,
            check=False,
        )
        current = ""
        if result.stdout.strip():
            current = result.stdout.splitlines()[0].strip().lower()
        return {
            "tmux_session": tmux_session,
            "returncode": result.returncode,
            "current_command": current,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def claude_session_id_evidence(runtime: Path) -> dict[str, Any]:
        home = Path(self.env.get("HOME") or str(Path.home())).expanduser()
        runtime_resolved = str(runtime.expanduser().resolve(strict=False))
        candidates: list[dict[str, Any]] = []

        def same_runtime(raw: Any) -> bool:
            if not raw:
                return False
            try:
                return str(Path(str(raw)).expanduser().resolve(strict=False)) == runtime_resolved
            except Exception:
                return str(raw) == str(runtime)

        def add_candidate(kind: str, path: Path, data: dict[str, Any], *, line_no: int = 0) -> None:
            session_id = str(data.get("sessionId") or "")
            if not session_id or not same_runtime(data.get("cwd")):
                return
            candidates.append({
                "kind": kind,
                "path": str(path),
                "line_no": line_no,
                "session_id": session_id,
                "cwd": data.get("cwd"),
                "pid": data.get("pid"),
                "status": data.get("status"),
                "mtime": path.stat().st_mtime if path.exists() else 0,
            })

        sessions_dir = home / ".claude" / "sessions"
        for path in sorted(sessions_dir.glob("*.json")) if sessions_dir.is_dir() else []:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if isinstance(data, dict):
                add_candidate("live_session", path, data)

        projects_dir = home / ".claude" / "projects"
        for path in sorted(projects_dir.rglob("*.jsonl")) if projects_dir.is_dir() else []:
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except Exception:
                continue
            for offset, raw_line in enumerate(reversed(lines[-80:]), start=1):
                try:
                    data = json.loads(raw_line)
                except Exception:
                    continue
                if isinstance(data, dict):
                    add_candidate("project_transcript", path, data, line_no=len(lines) - offset + 1)
                    if candidates and candidates[-1]["path"] == str(path):
                        break

        candidates.sort(key=lambda item: (float(item.get("mtime") or 0), int(item.get("line_no") or 0)), reverse=True)
        return {
            "runtime": str(runtime),
            "home": str(home),
            "session_id": str(candidates[0]["session_id"]) if candidates else "",
            "candidates": candidates[:8],
        }

    def wait_tmux_shell_after_exit(tmux_session: str, *, timeout: int = 90) -> dict[str, Any]:
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
            "claude_exit_returns_tmux_shell_" + state["intern"],
            False,
            "product_bug_claude_exit_resume_hint_missing",
            last,
        )
        return last

    def capture_resume_hint(tmux_session: str) -> dict[str, Any]:
        pane_text = self.ctx.action.session.tmux_capture_joined_remote(tmux_session, lines=320)
        lines = pane_text.splitlines()
        hint_index = -1
        command = ""
        for index, line in enumerate(lines):
            if "Resume this intern" not in line:
                continue
            hint_index = index
            for candidate in lines[index + 1:index + 8]:
                stripped = candidate.strip()
                if stripped:
                    command = stripped
                    break
        resume_uuid = claude_session_uuid_from_text(pane_text)
        detail = {
            "tmux_session": tmux_session,
            "hint_index": hint_index,
            "command": command,
            "resume_uuid": resume_uuid,
            "pane_tail": tail(pane_text, 5000),
        }
        self.require_classified_contract(
            "claude_exit_resume_hint_present_" + state["intern"],
            hint_index >= 0 and bool(command),
            "product_bug_claude_exit_resume_hint_missing",
            detail,
        )
        return detail

    def validate_resume_hint_command(command: str) -> dict[str, Any]:
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            tokens = []
            parse_error = str(exc)
        else:
            parse_error = ""
        session_index = -1
        for index in range(0, max(len(tokens) - 1, 0)):
            if tokens[index] == "session" and tokens[index + 1] == "resume":
                session_index = index
                break
        project_value = ""
        type_value = ""
        intern_value = tokens[session_index + 2] if session_index >= 0 and len(tokens) > session_index + 2 else ""
        if "--project" in tokens:
            project_index = tokens.index("--project")
            if len(tokens) > project_index + 1:
                project_value = tokens[project_index + 1]
        if "--type" in tokens:
            type_index = tokens.index("--type")
            if len(tokens) > type_index + 1:
                type_value = tokens[type_index + 1]
        launcher_tokens = tokens[:session_index] if session_index >= 0 else []
        launcher_ok = any(Path(token).name in {"internctl", "internctl.py"} for token in launcher_tokens)
        detail = {
            "command": command,
            "tokens": tokens,
            "parse_error": parse_error,
            "session_index": session_index,
            "launcher_tokens": launcher_tokens,
            "intern_value": intern_value,
            "project_value": project_value,
            "type_value": type_value,
            "expected_intern": state["intern"],
            "expected_project": state["workspace"]["display"],
            "expected_type": "claude",
        }
        self.require_classified_contract(
            "claude_exit_resume_hint_command_contract_" + state["intern"],
            not parse_error
            and session_index >= 0
            and launcher_ok
            and intern_value == state["intern"]
            and project_value == state["workspace"]["display"]
            and type_value == "claude",
            "product_bug_claude_exit_resume_hint_command_invalid",
            detail,
        )
        return detail

    def wait_claude_live_after_manual_resume(tmux_session: str, *, timeout: int) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"], check=False)
            tmux = self.run_cmd(
                f"tmux has manual resume session {tmux_session}",
                ["tmux", "has-session", "-t", f"={tmux_session}"],
                timeout=30,
                check=False,
            )
            processes = self.ctx.action.session.tmux_provider_processes_remote(tmux_session, "claude") if tmux.returncode == 0 else {}
            pane_text = self.ctx.action.session.tmux_capture_joined_remote(tmux_session, lines=240)
            session_id_after = claude_session_id_evidence(self.ctx.action.intern.runtime_dir_remote(state["workspace"], state["intern"]))
            resumed = claude_resumed_uuid_from_current_evidence(
                processes=processes,
                session_id_after=session_id_after,
                pane_text=pane_text,
            )
            last = {
                "session_status": status,
                "tmux": {"returncode": tmux.returncode, "stderr": tmux.stderr},
                "processes": processes,
                "resumed_uuid": resumed["session_id"],
                "resumed_uuid_source": resumed["source"],
                "session_id_after": session_id_after,
                "pane_tail": tail(pane_text, 4000),
            }
            failure_markers = (
                "resume failed:",
                "uuid capture failed",
                "claude not live in pane",
                "KeyboardInterrupt",
                "Traceback (most recent call last)",
            )
            if any(marker in pane_text for marker in failure_markers):
                self.require_classified_contract(
                    "claude_exit_resume_hint_command_executable_" + state["intern"],
                    False,
                    "product_bug_claude_exit_resume_hint_not_executable",
                    last,
                )
            if status.get("running") is True and tmux.returncode == 0 and processes.get("matches"):
                ready: dict[str, Any] = {}
                try:
                    ready = self.ctx.action.session.wait_tmux_input_ready_remote(
                        tmux_session,
                        timeout=min(120, max(10, int(deadline - time.time()))),
                    )
                except Exception as exc:  # noqa: BLE001
                    ready = {"ready": False, "error": str(exc), "tail": tail(self.ctx.action.session.tmux_capture_joined_remote(tmux_session, lines=120), 1000)}
                last["ready"] = ready
                if ready.get("ready") is True:
                    status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"], check=False)
                    processes = self.ctx.action.session.tmux_provider_processes_remote(tmux_session, "claude")
                    pane_text = self.ctx.action.session.tmux_capture_joined_remote(tmux_session, lines=240)
                    session_id_after = claude_session_id_evidence(self.ctx.action.intern.runtime_dir_remote(state["workspace"], state["intern"]))
                    resumed = claude_resumed_uuid_from_current_evidence(
                        processes=processes,
                        session_id_after=session_id_after,
                        pane_text=pane_text,
                    )
                    last.update({
                        "session_status": status,
                        "processes": processes,
                        "resumed_uuid": resumed["session_id"],
                        "resumed_uuid_source": resumed["source"],
                        "session_id_after": session_id_after,
                        "pane_tail": tail(pane_text, 4000),
                    })
                    return last
            time.sleep(3)
        self.require_classified_contract(
            "claude_exit_resume_hint_command_executable_" + state["intern"],
            False,
            "product_bug_claude_exit_resume_hint_not_executable",
            last,
        )
        return last

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_local_only_workspace() -> dict[str, Any]:
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

    def s03_prepare_claude_policy_token_redacted() -> dict[str, Any]:
        evidence = self.ctx.action.session.prepare_claude_policy_token_remote()
        return evidence

    def s04_cli_create_claude_intern() -> dict[str, Any]:
        workspace = state["workspace"]
        repo = state["repo"]
        intern = self.ctx.action.intern.create_case_remote(workspace, "claude", repo_url=str(repo), intern_type="claude")
        state["intern"] = intern
        return {
            "intern": intern,
            "gui_command": "intern.createIntern",
            "cli_equivalent": "internctl create <intern> --project <project> --type claude",
        }

    def s05_list_status_metadata_session_type() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        metadata = self.require_checks(
            self.ctx.action.intern.metadata_status_consistent_remote(
                workspace,
                intern,
                expected_status="Idle",
                expected_type="claude",
            )
        )
        list_item = self.require_checks(self.ctx.action.intern.list_item_remote(workspace, intern))
        status = metadata["status_json"]
        session_entries = metadata["session_entries"]
        session_types = {str(entry.get("type") or "") for entry in session_entries.values() if isinstance(entry, dict)}
        detail = {
            "metadata": metadata,
            "list_item": list_item,
            "status_json": status,
            "session_types": sorted(session_types),
        }
        type_ok = (
            list_item.get("type") == "claude"
            and status.get("type") == "claude"
            and session_types == {"claude"}
        )
        finding = self.collect_product_bug_evidence(
            state,
            "product_bug_claude_create_type_drift",
            type_ok,
            expected="internctl list, internctl status --json, metadata/session map all expose type=claude",
            actual=(
                f"list.type={list_item.get('type')!r}, "
                f"status.type={status.get('type')!r}, "
                f"session_types={sorted(session_types)!r}"
            ),
            detail=detail,
            handler_evidence={"contract": "F_0043.s05_list_status_metadata_session_type"},
        )
        state["metadata"] = metadata["metadata"]
        return {**detail, "product_bug_evidence": finding}

    def s06_feishu_group_type_claude() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace, intern, timeout=self.args.timeout)
        relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace, intern, timeout=self.args.timeout)
        chat_type = str(chat.get("type") or chat.get("intern_type") or "")
        detail = {"chat_lookup": chat, "relay_registry": relay}
        group_ok = (
            bool(chat.get("chat_id"))
            and chat.get("chat_id") == relay.get("chat_id")
            and relay.get("type") == "claude"
            and (not chat_type or chat_type == "claude")
        )
        finding = self.collect_product_bug_evidence(
            state,
            "product_bug_claude_group_type_drift",
            group_ok,
            expected="case-scoped group registry/API exposes type=claude for the Claude intern",
            actual=f"chat.type={chat_type!r}, relay.type={relay.get('type')!r}, chat_id_match={chat.get('chat_id') == relay.get('chat_id')}",
            detail=detail,
            handler_evidence={"contract": "F_0043.s06_feishu_group_type_claude"},
        )
        state.update({"chat": chat, "relay": relay})
        return {**detail, "product_bug_evidence": finding}

    def s07_start_claude_session_no_prompt() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        start = self.run_cmd(
            f"session start {intern} scoped",
            [
                *self.internctl,
                "session",
                "start",
                intern,
                "--project",
                str(workspace["display"]),
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
                "claude_session_start_existing_deployment",
                False,
                classification,
                {
                    "returncode": start.returncode,
                    "stdout": start.stdout,
                    "stderr": start.stderr,
                    "debug_machine": self.machine_id(),
                    "workspace": workspace,
                    "intern": intern,
                },
            )
        status = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        self.require_classified_contract(
            "claude_session_start_running_" + intern,
            status.get("running") is True,
            "product_bug_claude_session_not_live",
            status,
        )
        state["session_start"] = status
        state["tmux_session"] = str(status.get("tmux_session") or intern)
        self.created["sessions"].append(state["tmux_session"])
        return {
            "session_status": status,
            "gui_command": "intern.createClaudeSession",
            "cli_equivalent": "internctl session start <intern> --project <project> --type claude --no-attach",
            "business_prompt_sent": False,
        }

    def s08_wait_claude_session_live() -> dict[str, Any]:
        live = require_provider_session_live(
            provider="claude",
            timeout=self.args.timeout,
        )
        state["claude_live"] = live
        state["tmux_session"] = str(live.get("session_status", {}).get("tmux_session") or state.get("tmux_session") or state["intern"])
        return {**live, "business_prompt_sent": False}

    def s08_1_feishu_group_green_light() -> dict[str, Any]:
        green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
            state["workspace"],
            state["intern"],
            expected_type="claude",
            timeout=min(180, self.args.timeout),
        )
        return {"green_light": green, "chat_lookup": state.get("chat"), "relay_registry": state.get("relay"), "business_prompt_sent": False}

    def s09_claude_runtime_files_and_env() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        runtime = self.ctx.action.intern.runtime_dir_remote(workspace, intern)
        settings_path = runtime / ".claude" / "settings.json"
        home = Path(self.env.get("HOME") or str(Path.home())).expanduser()
        user_config_path = home / ".claude.json"
        hook_state_path = runtime / ".hook_state.json"

        settings_data: dict[str, Any] = {}
        user_config: dict[str, Any] = {}
        hook_state: dict[str, Any] = {}
        for path, target in (
            (settings_path, settings_data),
            (user_config_path, user_config),
            (hook_state_path, hook_state),
        ):
            if path.is_file():
                try:
                    target.update(json.loads(path.read_text(encoding="utf-8")))
                except Exception as exc:  # noqa: BLE001
                    target.update({"parse_error": str(exc)})

        hooks = settings_data.get("hooks") if isinstance(settings_data.get("hooks"), dict) else {}
        required_hooks = {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"}
        projects = user_config.get("projects") if isinstance(user_config.get("projects"), dict) else {}
        runtime_resolved = runtime.resolve(strict=False)
        trusted_projects = []
        for raw_project, config in projects.items():
            if not isinstance(config, dict):
                continue
            try:
                same_runtime = Path(str(raw_project)).expanduser().resolve(strict=False) == runtime_resolved
            except Exception:
                same_runtime = str(raw_project) == str(runtime)
            if same_runtime and config.get("hasTrustDialogAccepted") is True:
                trusted_projects.append(str(raw_project))

        tmux_session = str(state.get("tmux_session") or intern)
        tmux_env = self.ctx.action.session.tmux_environment_values_remote(
            tmux_session,
            (
                "INTERN_DIR",
                "PROJECT_NAME",
                "INTERN_WORKSPACE_ID",
                "INTERN_NAME",
                "INTERN_REAL_CLAUDE",
                "INTERN_CLAUDE_DEFAULT_ARGS",
                "CLAUDE_SETTINGS_MTIME",
                "CLAUDE_POLICY_ENV_HASH",
            ),
        )
        session_entries = self.ctx.action.session.registry_entries_for_remote(workspace, intern)
        initial_session_id = claude_session_id_evidence(runtime)
        detail = {
            "runtime": str(runtime),
            "settings_path": str(settings_path),
            "settings_is_symlink": settings_path.is_symlink(),
            "settings_hooks": sorted(hooks.keys()),
            "user_config_path": str(user_config_path),
            "has_completed_onboarding": user_config.get("hasCompletedOnboarding") is True,
            "trusted_projects": trusted_projects,
            "hook_state_path": str(hook_state_path),
            "hook_state_project": hook_state.get("project"),
            "hook_state_workspace_id": hook_state.get("workspace_id"),
            "session_entries": session_entries,
            "tmux_env": tmux_env,
            "claude_session_id": initial_session_id,
        }
        self.require_classified_contract(
            "claude_runtime_files_and_env_" + intern,
            settings_path.is_file()
            and required_hooks.issubset(set(hooks.keys()))
            and user_config_path.is_file()
            and user_config.get("hasCompletedOnboarding") is True
            and bool(trusted_projects)
            and hook_state.get("project") == workspace["display"]
            and hook_state.get("workspace_id") == workspace["workspace_id"]
            and any(isinstance(entry, dict) and entry.get("type") == "claude" for entry in session_entries.values())
            and tmux_env.get("INTERN_DIR") == str(runtime)
            and tmux_env.get("PROJECT_NAME") == workspace["display"]
            and tmux_env.get("INTERN_WORKSPACE_ID") == workspace["workspace_id"]
            and tmux_env.get("INTERN_NAME") == intern
            and bool(tmux_env.get("INTERN_REAL_CLAUDE"))
            and bool(tmux_env.get("CLAUDE_SETTINGS_MTIME")),
            "product_bug_claude_session_not_live",
            detail,
        )
        self.require_classified_contract(
            "claude_initial_session_uuid_discoverable_" + intern,
            bool(initial_session_id.get("session_id")),
            "ci_capability_gap_claude_uuid_discovery",
            detail,
        )
        state["initial_claude_session_id"] = str(initial_session_id.get("session_id") or "")
        return detail

    def s10_exit_claude_to_tmux_shell() -> dict[str, Any]:
        tmux_session = str(state.get("tmux_session") or state["intern"])
        before = self.ctx.action.session.tmux_capture_remote(tmux_session, lines=120)
        result = self.run_cmd(
            f"tmux send claude exit {tmux_session}",
            ["tmux", "send-keys", "-t", f"={tmux_session}:", "/exit", "Enter"],
            timeout=30,
            check=False,
        )
        detail = {
            "tmux_session": tmux_session,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "pane_tail_before_exit": tail(before, 3000),
            "business_prompt_sent": False,
        }
        self.require_classified_contract(
            "claude_exit_command_sent_" + state["intern"],
            result.returncode == 0,
            "product_bug_claude_exit_resume_hint_missing",
            detail,
        )
        state["tmux_session"] = tmux_session
        return detail

    def s11_wait_tmux_shell_after_provider_exit() -> dict[str, Any]:
        detail = wait_tmux_shell_after_exit(str(state["tmux_session"]), timeout=120)
        state["post_exit_shell"] = detail
        return {**detail, "business_prompt_sent": False}

    def s12_capture_resume_this_intern_hint() -> dict[str, Any]:
        detail = capture_resume_hint(str(state["tmux_session"]))
        state["resume_hint"] = detail
        state["manual_before_uuid"] = str(state.get("initial_claude_session_id") or detail.get("resume_uuid") or "")
        return {**detail, "business_prompt_sent": False}

    def s13_assert_resume_hint_command_contract() -> dict[str, Any]:
        detail = validate_resume_hint_command(str(state["resume_hint"]["command"]))
        state["resume_hint_contract"] = detail
        return detail

    def s14_run_resume_hint_in_tmux() -> dict[str, Any]:
        tmux_session = str(state["tmux_session"])
        command = str(state["resume_hint"]["command"])
        result = self.run_cmd(
            f"tmux run claude resume hint {tmux_session}",
            ["tmux", "send-keys", "-t", f"={tmux_session}:", command, "Enter"],
            timeout=30,
            check=False,
        )
        detail = {
            "tmux_session": tmux_session,
            "command": command,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "business_prompt_sent": False,
        }
        self.require_classified_contract(
            "claude_exit_resume_hint_sent_" + state["intern"],
            result.returncode == 0,
            "product_bug_claude_exit_resume_hint_not_executable",
            detail,
        )
        return detail

    def s15_wait_claude_live_after_manual_resume() -> dict[str, Any]:
        live = wait_claude_live_after_manual_resume(str(state["tmux_session"]), timeout=self.args.timeout)
        state["manual_resume_live"] = live
        state["manual_resumed_uuid"] = str(live.get("resumed_uuid") or "")
        return {**live, "business_prompt_sent": False}

    def s16_assert_manual_resume_uuid_stable() -> dict[str, Any]:
        before_uuid = str(state.get("manual_before_uuid") or "")
        resumed_uuid = str(state.get("manual_resumed_uuid") or "")
        detail = {
            "before_uuid": before_uuid,
            "resumed_uuid": resumed_uuid,
            "resume_hint": state.get("resume_hint"),
            "manual_resume_live": state.get("manual_resume_live"),
        }
        if not before_uuid or not resumed_uuid:
            self.require_classified_contract(
                "claude_manual_resume_uuid_discoverable_" + state["intern"],
                False,
                "ci_capability_gap_claude_uuid_discovery",
                detail,
            )
        self.require_classified_contract(
            "claude_manual_resume_uuid_stable_" + state["intern"],
            before_uuid == resumed_uuid,
            "product_bug_claude_exit_resume_hint_loses_uuid",
            detail,
        )
        return detail

    def s17_restart_claude_session_resume() -> dict[str, Any]:
        restart = restart_claude_session()
        state["restart_first"] = restart
        return {
            **restart,
            "gui_command": "intern.restartClaudeSession",
            "cli_equivalent": "internctl session restart <intern> --project <project> --type claude --no-attach",
            "business_prompt_sent": False,
        }

    def s18_assert_restart_resume_uuid() -> dict[str, Any]:
        result = assert_restart_resume_uuid(state["restart_first"], label="first")
        state["first_resume_uuid"] = result["resume_uuid"]
        return result

    def s19_wait_claude_live_after_restart() -> dict[str, Any]:
        live = require_provider_session_live(
            provider="claude",
            timeout=self.args.timeout,
        )
        state["claude_live_after_restart"] = live
        state["tmux_session"] = str(live.get("session_status", {}).get("tmux_session") or state.get("tmux_session") or state["intern"])
        return {**live, "business_prompt_sent": False}

    def s20_restart_claude_session_second_resume() -> dict[str, Any]:
        restart = restart_claude_session()
        state["restart_second"] = restart
        return {**restart, "business_prompt_sent": False}

    def s21_assert_resume_uuid_stable() -> dict[str, Any]:
        second = assert_restart_resume_uuid(state["restart_second"], label="second")
        first_uuid = str(state.get("first_resume_uuid") or "")
        second_uuid = str(second.get("resume_uuid") or "")
        detail = {"first_uuid": first_uuid, "second_uuid": second_uuid, "second_restart": state["restart_second"]}
        self.require_classified_contract(
            "claude_resume_uuid_stable_" + state["intern"],
            bool(first_uuid) and first_uuid == second_uuid,
            "product_bug_claude_restart_loses_uuid",
            detail,
        )
        state["second_resume_uuid"] = second_uuid
        return detail

    def s22_session_scope_stable_relay_connected() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        status = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        tmux_session = str(status.get("tmux_session") or state.get("tmux_session") or intern)
        tmux = self.run_cmd(
            f"tmux has final session {tmux_session}",
            ["tmux", "has-session", "-t", f"={tmux_session}"],
            timeout=30,
            check=False,
        )
        processes = self.ctx.action.session.tmux_provider_processes_remote(tmux_session, "claude") if tmux.returncode == 0 else {}
        sessions = self.ctx.action.session.registry_entries_for_remote(workspace, intern)
        relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace, intern, timeout=60)
        daemon = self.http_json("F_0043 daemon status", "GET", "/api/status", timeout=30)
        detail = {
            "session_status": status,
            "tmux_session": tmux_session,
            "tmux": {"returncode": tmux.returncode, "stderr": tmux.stderr},
            "processes": processes,
            "session_entries": sessions,
            "relay_registry": relay,
            "daemon": daemon,
            "retained_scene": {
                "workspace": workspace,
                "intern": intern,
                "chat": state.get("chat"),
                "manual_before_uuid": state.get("manual_before_uuid"),
                "manual_resumed_uuid": state.get("manual_resumed_uuid"),
                "first_resume_uuid": state.get("first_resume_uuid"),
                "second_resume_uuid": state.get("second_resume_uuid"),
            },
            "business_prompt_sent": False,
        }
        self.require_classified_contract(
            "claude_session_scope_stable_" + intern,
            status.get("running") is True
            and status.get("project") == workspace["display"]
            and tmux.returncode == 0
            and bool(processes.get("matches"))
            and any(isinstance(entry, dict) and entry.get("type") == "claude" for entry in sessions.values())
            and relay.get("type") == "claude"
            and daemon.get("relay_connected") is True,
            "product_bug_claude_session_not_live",
            detail,
        )
        findings = list(state.get("product_bug_findings") or [])
        if findings:
            first_classification = str(findings[0].get("failure_classification") or "product_bug")
            summaries = [
                {
                    "name": item.get("name"),
                    "expected_behavior": item.get("expected_behavior"),
                    "actual_behavior": item.get("actual_behavior"),
                    "failure_classification": item.get("failure_classification"),
                }
                for item in findings
                if isinstance(item, dict)
            ]
            self.require_classified_contract(
                "claude_product_bug_findings",
                False,
                first_classification,
                {"findings": findings, "finding_summaries": summaries, "count": len(findings)},
            )
        return detail

    def s23_final_feishu_group_green_light() -> dict[str, Any]:
        green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
            state["workspace"],
            state["intern"],
            expected_type="claude",
            timeout=min(180, self.args.timeout),
        )
        return {
            "green_light": green,
            "after": "manual_resume_and_repeated_restart_resume",
            "retained_scene": True,
            "business_prompt_sent": False,
        }

    self.run_ordered_scenarios([
        ("F_0043.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0043.s02_create_local_only_workspace", s02_create_local_only_workspace),
        ("F_0043.s03_prepare_claude_policy_token_redacted", s03_prepare_claude_policy_token_redacted),
        ("F_0043.s04_cli_create_claude_intern", s04_cli_create_claude_intern),
        ("F_0043.s05_list_status_metadata_session_type", s05_list_status_metadata_session_type),
        ("F_0043.s06_feishu_group_type_claude", s06_feishu_group_type_claude),
        ("F_0043.s07_start_claude_session_no_prompt", s07_start_claude_session_no_prompt),
        ("F_0043.s08_wait_claude_session_live", s08_wait_claude_session_live),
        ("F_0043.s08_1_feishu_group_green_light", s08_1_feishu_group_green_light),
        ("F_0043.s09_claude_runtime_files_and_env", s09_claude_runtime_files_and_env),
        ("F_0043.s10_exit_claude_to_tmux_shell", s10_exit_claude_to_tmux_shell),
        ("F_0043.s11_wait_tmux_shell_after_provider_exit", s11_wait_tmux_shell_after_provider_exit),
        ("F_0043.s12_capture_resume_this_intern_hint", s12_capture_resume_this_intern_hint),
        ("F_0043.s13_assert_resume_hint_command_contract", s13_assert_resume_hint_command_contract),
        ("F_0043.s14_run_resume_hint_in_tmux", s14_run_resume_hint_in_tmux),
        ("F_0043.s15_wait_claude_live_after_manual_resume", s15_wait_claude_live_after_manual_resume),
        ("F_0043.s16_assert_manual_resume_uuid_stable", s16_assert_manual_resume_uuid_stable),
        ("F_0043.s17_restart_claude_session_resume", s17_restart_claude_session_resume),
        ("F_0043.s18_assert_restart_resume_uuid", s18_assert_restart_resume_uuid),
        ("F_0043.s19_wait_claude_live_after_restart", s19_wait_claude_live_after_restart),
        ("F_0043.s20_restart_claude_session_second_resume", s20_restart_claude_session_second_resume),
        ("F_0043.s21_assert_resume_uuid_stable", s21_assert_resume_uuid_stable),
        ("F_0043.s22_session_scope_stable_relay_connected", s22_session_scope_stable_relay_connected),
        ("F_0043.s23_final_feishu_group_green_light", s23_final_feishu_group_green_light),
    ])
