import json
from pathlib import Path
import time
from typing import Any

from CI.assertions import session as session_assertions
from CI.cases.base import CaseDefinition
from CI.helpers.native_error import NativeCaseError
from CI.helpers.product_cli_helper import tail


PROVIDERS: dict[str, dict[str, str]] = {
    "codex": {
        "policy_args_key": "INTERN_CODEX_POLICY_ARGS",
        "default_args_key": "INTERN_CODEX_DEFAULT_ARGS",
        "hash_key": "CODEX_POLICY_ENV_HASH",
        "marker_key": "CI_BUG0065_CODEX_POLICY_MARKER",
    },
    "claude": {
        "policy_args_key": "INTERN_CLAUDE_POLICY_ARGS",
        "default_args_key": "INTERN_CLAUDE_DEFAULT_ARGS",
        "hash_key": "CLAUDE_POLICY_ENV_HASH",
        "marker_key": "CI_BUG0065_CLAUDE_POLICY_MARKER",
    },
}


SCENARIO_IDS = (
    "J_0065.s01_reset_case_namespace",
    "J_0065.s02_create_workspace_tasks_and_provider_groups",
    "J_0065.s03_start_provider_sessions",
    "J_0065.s04_send_real_prompts_and_capture_session_ids",
    "J_0065.s05_poison_tmux_policy_args_without_policy_change",
    "J_0065.s06_trigger_unchanged_policy_reconcile",
    "J_0065.s07_assert_local_reconcile_resume_same_session_ids",
    "J_0065.s08_mutate_relay_provider_policy_env",
    "J_0065.s09_assert_relay_policy_reconcile_resume_same_session_ids",
    "J_0065.s10_restore_relay_provider_policy_env",
    "J_0065.s11_assert_restore_reconcile_resume_same_session_ids",
    "J_0065.s12_ask_previous_token_after_restore",
    "J_0065.s13_final_session_scope_and_green_lights",
)


CASE = CaseDefinition(
    id="J_0065_policy_reconcile_same_session_journey",
    name="Policy reconcile same-session journey",
    description=(
        "Creates real Codex and Claude turns to obtain durable provider session IDs, then verifies local launch-env "
        "drift reconcile and relay policy env reconcile both restart/resume without changing those IDs."
    ),
    stage="remote",
    timeout_seconds=5400,
    kind="j_policy_reconcile_same_session_journey",
    tags=("J", "policy", "codex", "claude", "session", "restart", "resume", "feishu", "debug"),
    parallel_safe=False,
    extra={
        "ci_stage": "J",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "create_feishu_group",
            "create_intern",
            "create_task",
            "send_user_message",
            "workspace.remote_case_initial_reset_evidence",
            "workspace.remote_local_repo_fixture",
            "workspace.remote_create_case",
            "intern.remote_create_fixture_case",
            "task.remote_write_fixture",
            "session.remote_start_for_workspace",
            "session.remote_wait_provider_live",
            "session.remote_tmux_send",
            "session.remote_codex_session_id_evidence",
            "session.remote_tmux_environment_values",
            "session.remote_tmux_provider_processes",
            "policy.session_env_report",
            "policy.session_fingerprint",
            "policy.current_daemon_machine_id",
            "policy.relay_provider_env_marker",
            "policy.restore_relay_provider_env_marker",
            "policy.daemon_sync_existing_deployment",
            "claude.prepare_policy_token",
            "relay.read_chat_presence",
            "daemon.read_status",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "native.session_running",
            "native.feishu_group_green_light",
            "native.codex_session_id_resume",
            "native.claude_resume_uuid_stable",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "feishu_chat:ci_j_0065", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_j_0065_codex", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_j_0065_claude", "mode": "exclusive"},
            {"resource": "llm:codex", "mode": "read"},
            {"resource": "llm:claude", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_j_0065", "mode": "exclusive"},
            {"resource": "policy_alias:sk-xiaohan.yi", "mode": "read"},
            {"resource": "policy_state:ci_j_0065_policy", "mode": "exclusive"},
            {"resource": "relay:policy", "mode": "write"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "session:axis_intern_agents_backup:intern_ci_j_0065_codex", "mode": "exclusive"},
            {"resource": "session:axis_intern_agents_backup:intern_ci_j_0065_claude", "mode": "exclusive"},
            {"resource": "task:axis_intern_agents_backup:task_ci_j_0065_policy_reconcile", "mode": "exclusive"},
            {"resource": "tmux:ci_j_0065", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_j_0065_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_j_0065",
            "workspace:ci_j_0065_workspace",
            "intern:intern_ci_j_0065_codex",
            "intern:intern_ci_j_0065_claude",
            "task:task_ci_j_0065_policy_reconcile",
            "case_scoped_feishu_group",
            "llm:codex",
            "llm:claude",
            "policy_alias:sk-xiaohan.yi",
            "tmux",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy_llm_user_prompt_journey",
        "journey_steps": (
            "Create case-scoped Codex and Claude interns with Feishu groups and fixture tasks.",
            "Send one real prompt to each provider and capture the durable provider session id.",
            "Poison local tmux launch env while policy hash is unchanged, trigger daemon reconcile, and require the same session id.",
            "Mutate relay provider policy env marker, trigger daemon sync, and require the same session id.",
            "Restore relay policy marker, trigger daemon sync, and require the same session id again.",
            "After restore, ask each provider to recall the first business token without including it in the new prompt.",
        ),
        "notes": (
            "This is J-scoped because it sends real user prompts to Codex and Claude.",
            "The case mutates only case-scoped provider env marker keys and restores relay policy in finally.",
            "Successful runs retain workspace, interns, groups, sessions, and report evidence for review.",
        ),
    },
)


def _provider_report(case: Any, provider: str) -> dict[str, Any]:
    report = case.ctx.action.policy.session_env_report_remote()
    providers = report.get("providers") if isinstance(report.get("providers"), dict) else {}
    return providers.get(provider) if isinstance(providers.get(provider), dict) else {}


def _provider_pids(processes: dict[str, Any]) -> list[str]:
    return [
        str(item.get("pid"))
        for item in (processes.get("matches") or [])
        if isinstance(item, dict) and str(item.get("pid") or "")
    ]


def _pane_pids(fingerprint: dict[str, Any]) -> list[str]:
    return [str(pid) for pid in (fingerprint.get("pane_pids") or []) if str(pid)]


def _redacted_env_presence(values: dict[str, str]) -> dict[str, Any]:
    return {
        key: {
            "present": bool(value),
            "length": len(value),
            "hash_marker": value if key.endswith("_POLICY_ENV_HASH") else "",
        }
        for key, value in values.items()
    }


def _runtime_keys(provider: str) -> tuple[str, ...]:
    spec = PROVIDERS[provider]
    return (
        spec["policy_args_key"],
        spec["default_args_key"],
        spec["hash_key"],
        spec["marker_key"],
        "INTERN_NAME",
        "INTERN_DIR",
        "PROJECT_NAME",
        "INTERN_WORKSPACE_ID",
        "WORK_AGENTS_ROOT",
    )


def _runtime_snapshot(
    case: Any,
    workspace: dict[str, Any],
    intern: str,
    provider: str,
) -> dict[str, Any]:
    fingerprint = case.ctx.action.policy.session_fingerprint_remote(workspace, intern)
    tmux_session = str(fingerprint.get("tmux_session") or intern)
    env = case.ctx.action.session.tmux_environment_values_remote(tmux_session, _runtime_keys(provider))
    processes = case.ctx.action.session.tmux_provider_processes_remote(tmux_session, provider)
    return {
        "fingerprint": fingerprint,
        "tmux_session": tmux_session,
        "tmux_env": env,
        "provider_processes": processes,
        "pane_pids": _pane_pids(fingerprint),
        "provider_pids": _provider_pids(processes),
    }


def _restarted(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return bool(
        (before.get("provider_pids") and after.get("provider_pids") and before.get("provider_pids") != after.get("provider_pids"))
        or (before.get("pane_pids") and after.get("pane_pids") and before.get("pane_pids") != after.get("pane_pids"))
    )


def run_j_policy_reconcile_same_session_journey(case: Any) -> None:
    self = case
    state: dict[str, Any] = {"providers": {}}

    def provider_state(provider: str) -> dict[str, Any]:
        providers = state.setdefault("providers", {})
        if not isinstance(providers, dict):
            state["providers"] = {}
            providers = state["providers"]
        return providers.setdefault(provider, {})

    def wait_prompt_token_visible(provider: str, tmux_session: str, token: str, *, timeout: int) -> dict[str, Any]:
        deadline = time.time() + timeout
        last = ""
        auth_error_markers = (
            "API Error: 401",
            "Invalid bearer token",
            "Please run /login",
            "authentication failed",
            "invalid api key",
            "invalid_api_key",
        )
        while time.time() < deadline:
            last = self.ctx.action.session.tmux_capture_remote(tmux_session, lines=3000)
            tail_text = last[-6000:]
            count = last.count(token)
            if any(marker.lower() in tail_text.lower() for marker in auth_error_markers):
                classification = (
                    "ci_capability_gap_claude_token_policy"
                    if provider == "claude"
                    else "ci_capability_gap_codex_auth_policy"
                )
                self.require_classified_contract(
                    f"j0065_{provider}_prompt_provider_auth_ready",
                    False,
                    classification,
                    {
                        "provider": provider,
                        "token": token,
                        "token_count": count,
                        "auth_error_markers": auth_error_markers,
                        "tail": tail(tail_text, 3000),
                    },
                )
            ready = False
            try:
                ready = self.ctx.action.session.wait_tmux_input_ready_remote(tmux_session, timeout=5).get("ready") is True
            except Exception:
                ready = False
            if count >= 2 and ready:
                return {"provider": provider, "visible": True, "token": token, "token_count": count, "tail": tail(tail_text, 1000)}
            time.sleep(3)
        detail = {"provider": provider, "visible": False, "token": token, "token_count": last.count(token), "tail": tail(last, 2000)}
        self.require(f"j0065_{provider}_prompt_token_visible_after_user_turn", False, detail)
        return detail

    def wait_recalled_token_visible(
        provider: str,
        tmux_session: str,
        token: str,
        *,
        prompt_marker: str,
        timeout: int,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = self.ctx.action.session.tmux_capture_remote(tmux_session, lines=3000)
            tail_text = last[-6000:]
            marker_index = tail_text.rfind(prompt_marker)
            answer_region = tail_text[marker_index:] if marker_index >= 0 else tail_text
            token_after_prompt = answer_region.count(token)
            ready = False
            try:
                ready = self.ctx.action.session.wait_tmux_input_ready_remote(tmux_session, timeout=5).get("ready") is True
            except Exception:
                ready = False
            if token_after_prompt >= 1 and ready:
                return {
                    "provider": provider,
                    "remembered": True,
                    "token": token,
                    "prompt_marker": prompt_marker,
                    "token_after_prompt": token_after_prompt,
                    "final_token_count": last.count(token),
                    "tail": tail(tail_text, 1200),
                }
            time.sleep(3)
        marker_index = last.rfind(prompt_marker)
        answer_region = last[marker_index:] if marker_index >= 0 else last
        detail = {
            "provider": provider,
            "remembered": False,
            "token": token,
            "prompt_marker": prompt_marker,
            "token_after_prompt": answer_region.count(token),
            "final_token_count": last.count(token),
            "tail": tail(last, 3000),
        }
        self.require_classified_contract(
            f"j0065_{provider}_restore_reconcile_remembers_previous_token",
            False,
            "product_bug_restart_loses_conversation_context",
            detail,
        )
        return detail

    def claude_session_id_evidence(provider: str) -> dict[str, Any]:
        current = provider_state(provider)
        runtime = self.ctx.action.intern.runtime_dir_remote(state["workspace"], current["intern"])
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
            if not session_assertions.uuid_like(session_id) or not same_runtime(data.get("cwd")):
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
            for offset, raw_line in enumerate(reversed(lines[-120:]), start=1):
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
            "schema": "intern-agents.ci.claude-session-id-evidence.v1",
            "provider": provider,
            "workspace": str(state["workspace"].get("display") or ""),
            "workspace_id": str(state["workspace"].get("workspace_id") or ""),
            "intern": current["intern"],
            "runtime_dir": runtime_resolved,
            "home": str(home),
            "session_id": str(candidates[0]["session_id"]) if candidates else "",
            "available": bool(candidates),
            "candidates": candidates[:8],
            "failure_classification": "" if candidates else "ci_capability_gap_claude_uuid_discovery",
        }

    def session_id_evidence(provider: str) -> dict[str, Any]:
        current = provider_state(provider)
        if provider == "codex":
            return self.ctx.action.session.codex_session_id_evidence_remote(state["workspace"], current["intern"])
        return claude_session_id_evidence(provider)

    def require_session_id_available(provider: str, label: str, evidence: dict[str, Any]) -> str:
        session_id = str(evidence.get("session_id") or "")
        classification = "ci_capability_gap_session_id_discovery" if provider == "codex" else "ci_capability_gap_claude_uuid_discovery"
        self.require_classified_contract(
            f"j0065_{provider}_{label}_session_id_available",
            session_assertions.uuid_like(session_id),
            classification,
            evidence,
        )
        return session_id

    def require_same_session_id(provider: str, label: str, before_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
        after_id = require_session_id_available(provider, label, evidence)
        classification = "product_bug_restart_loses_session_id" if provider == "codex" else "product_bug_claude_restart_loses_uuid"
        detail = {
            "provider": provider,
            "before_session_id": before_id,
            "after_session_id": after_id,
            "after_evidence": evidence,
        }
        self.require_classified_contract(
            f"j0065_{provider}_{label}_same_session_id",
            before_id == after_id,
            classification,
            detail,
        )
        return detail | {"same_session_id": True}

    def wait_restart_converged(provider: str, before: dict[str, Any], expected_env: dict[str, str], *, timeout: int = 420) -> dict[str, Any]:
        deadline = time.time() + timeout
        current = provider_state(provider)
        last: dict[str, Any] = {}
        while time.time() < deadline:
            snapshot = _runtime_snapshot(self, state["workspace"], current["intern"], provider)
            env = snapshot["tmux_env"]
            running = (snapshot["fingerprint"].get("session_status") or {}).get("running") is True
            restarted = _restarted(before, snapshot)
            env_matches = all(env.get(key, "") == value for key, value in expected_env.items())
            last = {
                "provider": provider,
                "running": running,
                "restarted": restarted,
                "env_matches": env_matches,
                "runtime": {
                    "tmux_session": snapshot["tmux_session"],
                    "pane_pids": snapshot["pane_pids"],
                    "provider_pids": snapshot["provider_pids"],
                    "tmux_env_presence": _redacted_env_presence(env),
                },
                "expected_env_presence": _redacted_env_presence(expected_env),
                "before_provider_pids": before.get("provider_pids"),
                "before_pane_pids": before.get("pane_pids"),
            }
            if running and restarted and env_matches:
                live = self.ctx.action.session.wait_provider_session_live_remote(
                    state["workspace"],
                    current["intern"],
                    provider=provider,
                    timeout=min(180, max(30, int(deadline - time.time()))),
                )
                if live.get("ready", {}).get("ready") is True:
                    return last | {"snapshot": snapshot, "live": live}
            time.sleep(3)
        raise NativeCaseError(
            f"product_bug_policy_runtime_drift_not_restarted: {provider} policy reconcile did not restart/converge after real turn",
            details=last,
        )

    def s01_reset_case_namespace() -> dict[str, Any]:
        relay = self.relay_json("J_0065 relay status baseline", "GET", "/api/status", timeout=60)
        state["relay_before"] = relay
        reset = self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())
        return {"relay_before": relay, "reset": reset}

    def s02_create_workspace_tasks_and_provider_groups() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("j0065_workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix=f"workspace_{self.run_token}",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        metadata_root = self.ctx.action.workspace.metadata_root_remote(workspace)
        claude_token = self.ctx.action.session.prepare_claude_policy_token_remote()
        created: dict[str, Any] = {}
        for provider in PROVIDERS:
            task_id = self.task_id(f"{provider}_policy_reconcile")
            task = self.ctx.action.task.write_fixture_remote(metadata_root, task_id, status="Open", assignee="")
            evidence = self.require_checks(
                self.ctx.action.intern.create_fixture_case_remote(
                    workspace,
                    provider,
                    intern_type=provider,
                    repo_url=str(repo),
                    skip_feishu_group=False,
                    skip_status_notify=True,
                )
            )
            intern = evidence["intern"]
            chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace, intern, timeout=self.args.timeout)
            relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace, intern, timeout=self.args.timeout)
            provider_state(provider).update({"intern": intern, "task_id": task_id, "task": task, "chat": chat, "relay": relay})
            created[provider] = {"intern": intern, "task_id": task_id, "chat_lookup": chat, "relay_registry": relay}
        state.update({"repo": str(repo), "workspace": workspace, "metadata_root": str(metadata_root), "claude_token": claude_token})
        return {"workspace": workspace, "created": created, "claude_token": claude_token}

    def s03_start_provider_sessions() -> dict[str, Any]:
        started: dict[str, Any] = {}
        for provider in PROVIDERS:
            current = provider_state(provider)
            status = self.ctx.action.session.start_for_workspace_remote(state["workspace"], current["intern"], session_type=provider)
            tmux_session = str(status.get("tmux_session") or current["intern"])
            live = self.ctx.action.session.wait_provider_session_live_remote(
                state["workspace"],
                current["intern"],
                provider=provider,
                timeout=360,
            )
            green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
                state["workspace"],
                current["intern"],
                expected_type=provider,
                timeout=min(180, self.args.timeout),
            )
            current.update({"tmux_session": tmux_session, "start_status": status, "start_live": live, "green_before_prompt": green})
            started[provider] = {"session_status": status, "live": live, "green_light": green}
        return {"started": started}

    def s04_send_real_prompts_and_capture_session_ids() -> dict[str, Any]:
        captured: dict[str, Any] = {}
        for provider in PROVIDERS:
            current = provider_state(provider)
            token = f"READY_J0065_{provider.upper()}_{int(time.time())}_{self.run_token}"
            prompt = "Please reply exactly with this token and do not run commands: " + token
            self.ctx.action.session.tmux_send_remote(str(current["tmux_session"]), prompt)
            visible = wait_prompt_token_visible(provider, str(current["tmux_session"]), token, timeout=self.args.timeout)
            evidence = session_id_evidence(provider)
            session_id = require_session_id_available(provider, "after_user_turn", evidence)
            snapshot = _runtime_snapshot(self, state["workspace"], current["intern"], provider)
            report = _provider_report(self, provider)
            current.update({
                "prompt_token": token,
                "prompt_visible": visible,
                "session_id_before": session_id,
                "session_id_before_evidence": evidence,
                "baseline_runtime": snapshot,
                "baseline_report": report,
            })
            captured[provider] = {
                "token": token,
                "visible": visible,
                "session_id": session_id,
                "runtime": {
                    "tmux_session": snapshot["tmux_session"],
                    "pane_pids": snapshot["pane_pids"],
                    "provider_pids": snapshot["provider_pids"],
                    "tmux_env_presence": _redacted_env_presence(snapshot["tmux_env"]),
                },
                "env_report": report,
                "business_prompt_sent": True,
            }
        return {"captured": captured, "business_prompt_sent": True}

    def s05_poison_tmux_policy_args_without_policy_change() -> dict[str, Any]:
        poisoned_all: dict[str, Any] = {}
        for provider in PROVIDERS:
            spec = PROVIDERS[provider]
            current = provider_state(provider)
            baseline = current["baseline_runtime"]
            baseline_env = baseline["tmux_env"]
            tmux_session = str(baseline["tmux_session"])
            stale_default_args = f"--ci-j0065-stale-{provider}-default-args"
            stale_policy_args = f"--ci-j0065-stale-{provider}-policy-args"
            self.run_cmd(
                f"J_0065 poison {provider} default args",
                ["tmux", "set-environment", "-t", f"={tmux_session}", spec["default_args_key"], stale_default_args],
                timeout=30,
            )
            self.run_cmd(
                f"J_0065 poison {provider} policy args",
                ["tmux", "set-environment", "-t", f"={tmux_session}", spec["policy_args_key"], stale_policy_args],
                timeout=30,
            )
            if baseline_env.get(spec["hash_key"]):
                self.run_cmd(
                    f"J_0065 keep {provider} policy hash stable",
                    ["tmux", "set-environment", "-t", f"={tmux_session}", spec["hash_key"], baseline_env[spec["hash_key"]]],
                    timeout=30,
                )
            poisoned = self.ctx.action.session.tmux_environment_values_remote(
                tmux_session,
                (spec["default_args_key"], spec["policy_args_key"], spec["hash_key"]),
            )
            self.require(
                f"j0065_{provider}_tmux_args_poisoned_hash_unchanged",
                poisoned.get(spec["default_args_key"]) == stale_default_args
                and poisoned.get(spec["policy_args_key"]) == stale_policy_args
                and poisoned.get(spec["hash_key"]) == baseline_env.get(spec["hash_key"], ""),
                {"provider": provider, "poisoned_presence": _redacted_env_presence(poisoned)},
            )
            current["poisoned_env"] = poisoned
            poisoned_all[provider] = {"poisoned_presence": _redacted_env_presence(poisoned)}
        return {"poisoned": poisoned_all, "business_prompt_sent": True}

    def s06_trigger_unchanged_policy_reconcile() -> dict[str, Any]:
        machine_id = self.ctx.action.policy.current_daemon_machine_id_remote()
        sync = self.ctx.action.policy.daemon_sync_existing_deployment_remote(
            "J_0065 unchanged policy runtime reconcile",
            machine_id=str(machine_id),
        )
        state.update({"machine_id": str(machine_id), "local_sync": sync})
        return {"machine_id": str(machine_id), "daemon_sync": sync, "business_prompt_sent": True}

    def s07_assert_local_reconcile_resume_same_session_ids() -> dict[str, Any]:
        results: dict[str, Any] = {}
        for provider in PROVIDERS:
            spec = PROVIDERS[provider]
            current = provider_state(provider)
            baseline = current["baseline_runtime"]
            baseline_env = baseline["tmux_env"]
            result = wait_restart_converged(
                provider,
                baseline,
                {
                    spec["default_args_key"]: baseline_env.get(spec["default_args_key"], ""),
                    spec["policy_args_key"]: baseline_env.get(spec["policy_args_key"], ""),
                    spec["hash_key"]: baseline_env.get(spec["hash_key"], ""),
                },
            )
            evidence = session_id_evidence(provider)
            same = require_same_session_id(provider, "local_reconcile", current["session_id_before"], evidence)
            current.update({"runtime_after_local_reconcile": result["snapshot"], "session_id_after_local_reconcile": evidence})
            results[provider] = {key: value for key, value in (result | same).items() if key != "snapshot"}
        return {"local_reconcile": results, "business_prompt_sent": True}

    def s08_mutate_relay_provider_policy_env() -> dict[str, Any]:
        machine_id = str(state.get("machine_id") or self.ctx.action.policy.current_daemon_machine_id_remote())
        mutations: dict[str, Any] = {}
        for provider in PROVIDERS:
            marker = f"ci_j0065_{provider}_{self.resource_namespace}_{self.run_token}"
            mutation = self.ctx.action.policy.relay_provider_env_marker_remote(
                provider=provider,
                marker=marker,
                env_key=PROVIDERS[provider]["marker_key"],
            )
            provider_state(provider).update({"relay_marker": marker, "relay_mutation": mutation})
            mutations[provider] = mutation
        sync = self.ctx.action.policy.daemon_sync_existing_deployment_remote(
            "J_0065 relay provider policy env sync",
            machine_id=machine_id,
        )
        state["relay_mutation_sync"] = sync
        return {"mutations": mutations, "daemon_sync": sync, "business_prompt_sent": True}

    def s09_assert_relay_policy_reconcile_resume_same_session_ids() -> dict[str, Any]:
        results: dict[str, Any] = {}
        for provider in PROVIDERS:
            spec = PROVIDERS[provider]
            current = provider_state(provider)
            before = current.get("runtime_after_local_reconcile") or current["baseline_runtime"]
            marker = str(current["relay_marker"])
            result = wait_restart_converged(provider, before, {spec["marker_key"]: marker})
            evidence = session_id_evidence(provider)
            same = require_same_session_id(provider, "relay_policy_reconcile", current["session_id_before"], evidence)
            current.update({"runtime_after_relay_change": result["snapshot"], "session_id_after_relay_change": evidence})
            results[provider] = {key: value for key, value in (result | same).items() if key != "snapshot"}
        return {"relay_policy_reconcile": results, "business_prompt_sent": True}

    def s10_restore_relay_provider_policy_env() -> dict[str, Any]:
        machine_id = str(state.get("machine_id") or self.ctx.action.policy.current_daemon_machine_id_remote())
        restore: dict[str, Any] = {}
        for provider in PROVIDERS:
            mutation = provider_state(provider).get("relay_mutation")
            restore[provider] = self.ctx.action.policy.restore_relay_provider_env_marker_remote(
                mutation if isinstance(mutation, dict) else {}
            )
        sync = self.ctx.action.policy.daemon_sync_existing_deployment_remote(
            "J_0065 restore relay provider policy env",
            machine_id=machine_id,
        )
        state["relay_restore_sync"] = sync
        return {"restore": restore, "daemon_sync": sync, "business_prompt_sent": True}

    def s11_assert_restore_reconcile_resume_same_session_ids() -> dict[str, Any]:
        results: dict[str, Any] = {}
        for provider in PROVIDERS:
            spec = PROVIDERS[provider]
            current = provider_state(provider)
            mutation = current.get("relay_mutation") if isinstance(current.get("relay_mutation"), dict) else {}
            expected_marker = str(mutation.get("previous_value") or "") if mutation.get("previous_present") else ""
            before = current.get("runtime_after_relay_change") or current.get("runtime_after_local_reconcile") or current["baseline_runtime"]
            result = wait_restart_converged(provider, before, {spec["marker_key"]: expected_marker})
            evidence = session_id_evidence(provider)
            same = require_same_session_id(provider, "relay_restore_reconcile", current["session_id_before"], evidence)
            current.update({"runtime_after_relay_restore": result["snapshot"], "session_id_after_relay_restore": evidence})
            results[provider] = {key: value for key, value in (result | same).items() if key != "snapshot"}
        return {"relay_restore_reconcile": results, "business_prompt_sent": True}

    def s12_ask_previous_token_after_restore() -> dict[str, Any]:
        recalled: dict[str, Any] = {}
        for provider in PROVIDERS:
            current = provider_state(provider)
            token = str(current.get("prompt_token") or "")
            snapshot = current.get("runtime_after_relay_restore") or current.get("runtime_after_relay_change") or current["baseline_runtime"]
            tmux_session = str(snapshot.get("tmux_session") or current["tmux_session"])
            prompt_marker = f"MEMORY_CHECK_J0065_{provider.upper()}_{int(time.time())}_{self.run_token}"
            prompt = (
                f"Memory check id: {prompt_marker}. "
                "Earlier in this same conversation I asked you to reply with a READY_J0065 token. "
                "Reply now with exactly that previous full token and nothing else. Do not run commands."
            )
            self.ctx.action.session.tmux_send_remote(tmux_session, prompt)
            visible = wait_recalled_token_visible(
                provider,
                tmux_session,
                token,
                prompt_marker=prompt_marker,
                timeout=min(420, self.args.timeout),
            )
            evidence = session_id_evidence(provider)
            same = require_same_session_id(provider, "post_restore_memory_prompt", current["session_id_before"], evidence)
            current.update({
                "post_restore_memory_prompt": visible,
                "session_id_after_memory_prompt": evidence,
            })
            recalled[provider] = visible | same
        return {"post_restore_memory": recalled, "business_prompt_sent": True}

    def s13_final_session_scope_and_green_lights() -> dict[str, Any]:
        final: dict[str, Any] = {}
        daemon = self.http_json("J_0065 daemon status", "GET", "/api/status", timeout=30)
        for provider in PROVIDERS:
            current = provider_state(provider)
            status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], current["intern"])
            entries = self.ctx.action.session.registry_entries_for_remote(state["workspace"], current["intern"])
            green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
                state["workspace"],
                current["intern"],
                expected_type=provider,
                timeout=min(180, self.args.timeout),
            )
            detail = {
                "provider": provider,
                "session_status": status,
                "session_entries": entries,
                "green_light": green,
                "daemon": daemon,
                "session_id_before": current.get("session_id_before"),
                "chat": current.get("chat"),
                "relay": current.get("relay"),
            }
            self.require(
                f"j0065_final_{provider}_session_scope_stable",
                status.get("running") is True and daemon.get("relay_connected") is True and bool(entries),
                detail,
            )
            final[provider] = detail
        return {"final": final, "business_prompt_sent": True}

    try:
        self.run_ordered_scenarios([
            ("J_0065.s01_reset_case_namespace", s01_reset_case_namespace),
            ("J_0065.s02_create_workspace_tasks_and_provider_groups", s02_create_workspace_tasks_and_provider_groups),
            ("J_0065.s03_start_provider_sessions", s03_start_provider_sessions),
            ("J_0065.s04_send_real_prompts_and_capture_session_ids", s04_send_real_prompts_and_capture_session_ids),
            ("J_0065.s05_poison_tmux_policy_args_without_policy_change", s05_poison_tmux_policy_args_without_policy_change),
            ("J_0065.s06_trigger_unchanged_policy_reconcile", s06_trigger_unchanged_policy_reconcile),
            ("J_0065.s07_assert_local_reconcile_resume_same_session_ids", s07_assert_local_reconcile_resume_same_session_ids),
            ("J_0065.s08_mutate_relay_provider_policy_env", s08_mutate_relay_provider_policy_env),
            ("J_0065.s09_assert_relay_policy_reconcile_resume_same_session_ids", s09_assert_relay_policy_reconcile_resume_same_session_ids),
            ("J_0065.s10_restore_relay_provider_policy_env", s10_restore_relay_provider_policy_env),
            ("J_0065.s11_assert_restore_reconcile_resume_same_session_ids", s11_assert_restore_reconcile_resume_same_session_ids),
            ("J_0065.s12_ask_previous_token_after_restore", s12_ask_previous_token_after_restore),
            ("J_0065.s13_final_session_scope_and_green_lights", s13_final_session_scope_and_green_lights),
        ])
    finally:
        restore: dict[str, Any] = {}
        for provider in PROVIDERS:
            mutation = provider_state(provider).get("relay_mutation")
            if isinstance(mutation, dict) and mutation:
                restore[provider] = self.ctx.action.policy.restore_relay_provider_env_marker_remote(mutation)
        if restore:
            try:
                machine_id = str(state.get("machine_id") or self.ctx.action.policy.current_daemon_machine_id_remote())
                restore["policy_restore_sync"] = self.ctx.action.policy.daemon_sync_existing_deployment_remote(
                    "J_0065 finally restore relay provider policy env",
                    machine_id=machine_id,
                )
            except Exception as exc:  # noqa: BLE001
                restore["policy_restore_sync_error"] = str(exc)
            self.artifacts["j0065_policy_restore"] = restore
