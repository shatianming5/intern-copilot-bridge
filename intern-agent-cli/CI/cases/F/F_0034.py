import time
from typing import Any

from CI.cases.base import CaseDefinition
from CI.helpers.native_error import NativeCaseError


CASE = CaseDefinition(
    id="F_0034_policy_env_idle_codex_auto_restart_contract",
    name="Policy env Idle Codex auto-restart contract",
    description=(
        "Existing-deployment debug validation covering case-scoped relay policy/machine-config mutation, "
        "daemon policy sync, redacted Codex session env materialization, Idle Codex auto-restart, and unchanged-policy replay dedupe."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_policy_env_idle_codex_auto_restart_contract",
    tags=("F", "policy", "codex", "daemon", "debug", "existing_deployment"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "actions": (
            "cli.internctl",
            "policy.machine_config_marker",
            "policy.daemon_sync_existing_deployment",
            "session.codex_start_restart_remote",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0034", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0034_idle", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "policy_state:ci_f_0034_policy", "mode": "exclusive"},
            {"resource": "relay:machine_connection", "mode": "read"},
            {"resource": "relay:policy", "mode": "write"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0034_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "relay_machine:required",
            "relay_policy_state:ci_f_0034_policy",
            "workspace_metadata:ci_f_0034_workspace",
            "intern:intern_ci_f_0034_idle",
            "artifact:ci_f_0034",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.policy_env_idle_codex_auto_restart_consistent",),
        "scenario_ids": (
            "F_0034.s01_reset_case_namespace",
            "F_0034.s02_create_idle_codex_intern",
            "F_0034.s03_prepare_restartable_codex_session",
            "F_0034.s04_trigger_policy_sync_with_codex_env_change",
            "F_0034.s05_wait_for_daemon_policy_sync",
            "F_0034.s06_session_env_hash_changed_redacted",
            "F_0034.s07_idle_codex_auto_restart",
            "F_0034.s08_policy_env_restart_record",
            "F_0034.s09_trigger_policy_sync_same_marker",
            "F_0034.s10_no_duplicate_restart_for_unchanged_policy",
            "F_0034.s11_working_skip_contract_documented",
            "F_0034.s12_retained_idle_codex_scene_green",
        ),
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, bootstrap, allocate Feishu groups, or restart relay.",
            "Uses case-scoped machine_config state for the current machine and restores only the previous shared relay policy state; successful runs retain workspace, intern, group, and session evidence.",
            "If the deployed debug machine cannot safely mutate relay policy state, the native runner fails with ci_capability_gap_policy_mutation_driver.",
            "Report evidence redacts secret material and records only hashes, key names, restart summaries, and file paths.",
        ),
    },
)


def run_f_policy_env_idle_codex_auto_restart_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}
    workspace: dict[str, Any] | None = None
    interns: list[str] = []

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_idle_codex_intern() -> dict[str, Any]:
        nonlocal workspace
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0034_workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        created = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "idle", intern_type="codex", repo_url=str(repo)))
        interns.append(created["intern"])
        metadata = self.require_checks(
            self.ctx.action.intern.metadata_status_consistent_remote(workspace, created["intern"], expected_status="Idle")
        )
        state.update({"repo": repo, "workspace": workspace, "intern": created["intern"], "created": created, "metadata": metadata})
        return {"workspace": workspace, "intern": created["intern"], "metadata": metadata, "business_prompt_sent": False}

    def s03_prepare_restartable_codex_session() -> dict[str, Any]:
        assert workspace is not None
        intern = state["intern"]
        status = self.ctx.action.session.start_for_workspace_remote(workspace, intern)
        ready = self.ctx.action.session.wait_tmux_input_ready_remote(str(status.get("tmux_session") or intern), timeout=240)
        before = self.ctx.action.policy.session_fingerprint_remote(workspace, intern)
        baseline_report = self.ctx.action.policy.codex_env_report_remote()
        state.update({"session_before_policy": before, "baseline_codex_env": baseline_report})
        return {"session_status": status, "fingerprint": before, "codex_env": baseline_report, "ready": ready, "business_prompt_sent": False}

    def s04_trigger_policy_sync_with_codex_env_change() -> dict[str, Any]:
        marker = f"ci_f_0034_policy_{self.resource_namespace}_{self.run_token}"
        mutation = self.ctx.action.policy.machine_config_marker_remote(field_key="codex_lb_mode", marker=marker)
        machine_id = str(mutation["machine_id"])
        restart = self.ctx.action.policy.daemon_sync_existing_deployment_remote("F_0034 policy sync", machine_id=machine_id)
        state.update({"policy_marker": marker, "policy_mutation": mutation, "policy_sync_restart": restart, "machine_id": machine_id})
        return {"marker": marker, "mutation": mutation, "sync_driver": "single_daemon_reconnect_policy_sync", "daemon_restart": restart}

    def s05_wait_for_daemon_policy_sync() -> dict[str, Any]:
        baseline = state.get("baseline_codex_env") if isinstance(state.get("baseline_codex_env"), dict) else {}
        baseline_hash = str(baseline.get("hash") or "")
        deadline = time.time() + 180
        last: dict[str, Any] = {}
        while time.time() < deadline:
            codex = self.ctx.action.policy.codex_env_report_remote()
            last = codex
            if codex.get("hash") and codex.get("hash") != baseline_hash and codex.get("changed") is True:
                state["changed_codex_env"] = codex
                return {"baseline_hash": baseline_hash, "changed_codex_env": codex}
            time.sleep(3)
        raise NativeCaseError(
            "product_bug_policy_sync_not_pulled: daemon did not materialize changed Codex policy env",
            details={"baseline_hash": baseline_hash, "last_codex_env": last, "mutation": state.get("policy_mutation")},
        )

    def s06_session_env_hash_changed_redacted() -> dict[str, Any]:
        baseline = state.get("baseline_codex_env") if isinstance(state.get("baseline_codex_env"), dict) else {}
        codex = state.get("changed_codex_env") if isinstance(state.get("changed_codex_env"), dict) else self.ctx.action.policy.codex_env_report_remote()
        leaked_value_keys = [key for key in ("env", "secret_env") if key in codex]
        ok = bool(codex.get("hash")) and codex.get("hash") != baseline.get("hash") and not leaked_value_keys
        if not ok:
            raise NativeCaseError(
                "product_bug_session_env_not_materialized: Codex env hash did not change or report exposed raw env values",
                details={"baseline": baseline, "codex": codex, "leaked_value_keys": leaked_value_keys},
            )
        state["changed_hash"] = str(codex.get("hash") or "")
        return {"baseline": baseline, "codex": codex, "secrets_redacted": True, "leaked_value_keys": leaked_value_keys}

    def s07_idle_codex_auto_restart() -> dict[str, Any]:
        assert workspace is not None
        restart = self.ctx.action.policy.wait_session_policy_restart_remote(
            workspace,
            state["intern"],
            state["session_before_policy"],
            expected_hash=str(state.get("changed_hash") or ""),
            timeout=300,
        )
        state["session_after_policy"] = restart["after"]
        return restart | {"reason": "codex_env_changed"}

    def s08_policy_env_restart_record() -> dict[str, Any]:
        intern = state["intern"]
        after = state.get("session_after_policy") or {}
        codex = state.get("changed_codex_env") or {}
        self.require(
            "policy_env_restart_record_targets_idle_codex",
            after.get("session_status", {}).get("running") is True
            and after.get("pane_pids") != state.get("session_before_policy", {}).get("pane_pids")
            and bool(codex.get("hash")),
            {"intern": intern, "after": after, "codex_env": codex},
        )
        return {"intern": intern, "ok": True, "reason": "codex_env_changed", "session_after": after, "codex_env": codex}

    def s09_trigger_policy_sync_same_marker() -> dict[str, Any]:
        assert workspace is not None
        before = self.ctx.action.policy.session_fingerprint_remote(workspace, state["intern"])
        machine_id = str(state.get("machine_id") or self.ctx.action.policy.current_daemon_machine_id_remote())
        replay = self.ctx.action.policy.daemon_sync_existing_deployment_remote("F_0034 unchanged policy replay", machine_id=machine_id)
        codex = self.ctx.action.policy.codex_env_report_remote()
        state.update({"before_policy_replay": before, "replay": replay, "replay_codex_env": codex})
        return {"before": before, "replay": replay, "codex_env": codex, "marker": state.get("policy_marker")}

    def s10_no_duplicate_restart_for_unchanged_policy() -> dict[str, Any]:
        assert workspace is not None
        codex = state.get("replay_codex_env") if isinstance(state.get("replay_codex_env"), dict) else self.ctx.action.policy.codex_env_report_remote()
        self.require(
            "unchanged_policy_replay_hash_stable",
            codex.get("hash") == state.get("changed_hash"),
            {"changed_hash": state.get("changed_hash"), "replay_codex_env": codex},
        )
        return self.ctx.action.policy.assert_no_duplicate_policy_restart_remote(
            workspace,
            state["intern"],
            state["before_policy_replay"],
            expected_hash=str(state.get("changed_hash") or ""),
            timeout=45,
        )

    def s11_working_skip_contract_documented() -> dict[str, Any]:
        return {
            "working_skip_contract_documented": True,
            "working_dialogue_created": False,
            "contract": "daemon policy env restart queues Working sessions as pending_working; F_0034 does not create a real Working dialogue.",
        }

    def s12_retained_idle_codex_scene_green() -> dict[str, Any]:
        assert workspace is not None
        intern = state["intern"]
        status = self.ctx.action.session.status_for_workspace_remote(workspace, intern)
        green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
            workspace,
            intern,
            expected_type="codex",
            timeout=min(180, self.args.timeout),
        )
        self.require(
            "f0034_retained_idle_codex_session_running",
            status.get("running") is True,
            {"session_status": status, "green_light": green, "retained_scene": True},
        )
        return {"session_status": status, "green_light": green, "retained_scene": True, "business_prompt_sent": False}

    try:
        self.run_ordered_scenarios([
            ("F_0034.s01_reset_case_namespace", s01_reset_case_namespace),
            ("F_0034.s02_create_idle_codex_intern", s02_create_idle_codex_intern),
            ("F_0034.s03_prepare_restartable_codex_session", s03_prepare_restartable_codex_session),
            ("F_0034.s04_trigger_policy_sync_with_codex_env_change", s04_trigger_policy_sync_with_codex_env_change),
            ("F_0034.s05_wait_for_daemon_policy_sync", s05_wait_for_daemon_policy_sync),
            ("F_0034.s06_session_env_hash_changed_redacted", s06_session_env_hash_changed_redacted),
            ("F_0034.s07_idle_codex_auto_restart", s07_idle_codex_auto_restart),
            ("F_0034.s08_policy_env_restart_record", s08_policy_env_restart_record),
            ("F_0034.s09_trigger_policy_sync_same_marker", s09_trigger_policy_sync_same_marker),
            ("F_0034.s10_no_duplicate_restart_for_unchanged_policy", s10_no_duplicate_restart_for_unchanged_policy),
            ("F_0034.s11_working_skip_contract_documented", s11_working_skip_contract_documented),
            ("F_0034.s12_retained_idle_codex_scene_green", s12_retained_idle_codex_scene_green),
        ])
    finally:
        restore: dict[str, Any] = {}
        mutation = state.get("policy_mutation") if isinstance(state.get("policy_mutation"), dict) else {}
        if mutation:
            restore["policy_restore"] = self.ctx.action.policy.restore_machine_config_marker_remote(mutation)
            try:
                machine_id = str(mutation.get("machine_id") or self.ctx.action.policy.current_daemon_machine_id_remote())
                restore["policy_restore_sync"] = self.ctx.action.policy.daemon_sync_existing_deployment_remote("F_0034 restore policy", machine_id=machine_id)
            except Exception as exc:  # noqa: BLE001
                restore["policy_restore_sync_error"] = str(exc)
        if restore:
            restore["retained_scene"] = {"workspace": workspace, "interns": interns, "session_stop": False, "workspace_cleanup": False}
            self.artifacts["f0034_policy_restore"] = restore
