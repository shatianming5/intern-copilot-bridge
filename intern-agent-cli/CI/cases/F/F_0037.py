from typing import Any

from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition
from CI.helpers.native_error import NativeCaseError


CASE = CaseDefinition(
    id="F_0037_daemon_reconnect_registry_policy_resync_contract",
    name="Daemon reconnect registry and policy resync contract",
    description=(
        "Existing-deployment debug validation covering single-daemon reconnect recovery: relay machine registry online, "
        "workspace and chat lookup availability, daemon policy resync, and no relay/package/reset/deploy/bootstrap side effects."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_daemon_reconnect_registry_policy_resync_contract",
    tags=("F", "daemon", "reconnect", "registry", "policy", "debug", "existing_deployment"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "actions": (
            "cli.internctl",
            "daemon.reconnect_single_existing_deployment",
            "daemon.registry_chat_recover",
            "policy.daemon_sync_existing_deployment",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0037", "mode": "exclusive"},
            {"resource": "chat_registry:ci_f_0037", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0037_codex", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "policy_marker:ci_f_0037", "mode": "exclusive"},
            {"resource": "relay:chat_registry", "mode": "write"},
            {"resource": "relay:policy", "mode": "write"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0037_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "workspace_metadata:ci_f_0037_workspace",
            "intern:intern_ci_f_0037_codex",
            "chat_registry:ci_f_0037",
            "policy_marker:ci_f_0037",
            "artifact:ci_f_0037",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.daemon_reconnect_registry_policy_resync_consistent",),
        "scenario_ids": (
            "F_0037.s01_reset_case_namespace",
            "F_0037.s02_seed_workspace_intern_group",
            "F_0037.s03_daemon_relay_connected",
            "F_0037.s04_restart_or_disconnect_single_daemon",
            "F_0037.s05_wait_for_relay_machine_offline",
            "F_0037.s06_wait_for_daemon_reconnect",
            "F_0037.s07_relay_machine_registry_online",
            "F_0037.s08_workspace_list_available",
            "F_0037.s09_chat_lookup_available",
            "F_0037.s10_trigger_case_policy_marker",
            "F_0037.s11_daemon_policy_resync",
            "F_0037.s12_no_relay_restart_or_global_reset",
            "F_0037.s13_retained_registry_scene",
        ),
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, bootstrap, or restart relay.",
            "Uses one existing debug daemon only; if a safe single-daemon reconnect driver is unavailable, the native runner fails with ci_capability_gap_safe_daemon_restart_driver.",
            "Creates only case-scoped workspace/intern/chat registry resources and retains them after validation for现场 evidence.",
        ),
    },
)


def run_f_daemon_reconnect_registry_policy_resync_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}
    workspace: dict[str, Any] | None = None
    interns: list[str] = []

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_seed_workspace_intern_group() -> dict[str, Any]:
        nonlocal workspace
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0037_workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        created = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "codex", intern_type="codex", repo_url=str(repo)))
        interns.append(created["intern"])
        project = str(workspace["display"])
        group = self.http_json(
            "F_0037 daemon group create",
            "POST",
            "/api/group/create",
            {"project": project, "intern_name": created["intern"]},
            timeout=120,
        )
        chat_id = str(group.get("chat_id") or "")
        relay_lookup = self.ctx.action.relay_daemon.relay_chat_lookup_remote(created["intern"], project)
        self.require(
            "f0037_seed_chat_registry_visible",
            bool(chat_id) and relay_lookup.get("chat_id") == chat_id,
            {"group": group, "relay_lookup": relay_lookup},
        )
        state.update({"repo": repo, "workspace": workspace, "intern": created["intern"], "project": project, "group": group, "chat_id": chat_id})
        return {"workspace": workspace, "intern": created["intern"], "group": group, "relay_lookup": relay_lookup}

    def s03_daemon_relay_connected() -> dict[str, Any]:
        daemon = self.http_json("F_0037 daemon status baseline", "GET", "/api/status", timeout=60)
        relay = self.relay_json("F_0037 relay status baseline", "GET", "/api/status", timeout=60)
        machine_id = str(daemon.get("instance_id") or self.ctx.action.policy.current_daemon_machine_id_remote())
        online = self.ctx.action.policy.wait_relay_machine_state_remote(machine_id, online=True, timeout=60)
        policy_before = self.ctx.action.policy.daemon_policy_fingerprint_remote()
        self.require(
            "f0037_daemon_relay_connected_baseline",
            daemon.get("running") is True and daemon.get("relay_connected") is True,
            {"daemon": daemon, "relay": relay, "machine": online},
        )
        state.update({"machine_id": machine_id, "relay_before": relay, "policy_before": policy_before})
        return {"daemon": daemon, "relay": relay, "machine": online, "policy_before": policy_before}

    def s04_restart_or_disconnect_single_daemon() -> dict[str, Any]:
        before = self.ctx.action.policy.daemon_status_remote("F_0037 daemon status before stop")
        stop = self.run_cmd("F_0037 daemon stop", [*self.internctl, "daemon", "stop"], timeout=120)
        state["daemon_stop"] = {"before": before, "stdout": stop.stdout}
        return state["daemon_stop"]

    def s05_wait_for_relay_machine_offline() -> dict[str, Any]:
        offline = self.ctx.action.policy.wait_relay_machine_state_remote(str(state["machine_id"]), online=False, timeout=120)
        state["relay_offline"] = offline
        return offline

    def s06_wait_for_daemon_reconnect() -> dict[str, Any]:
        started = self.ctx.action.policy.start_single_daemon_remote("F_0037 reconnect", machine_id=str(state["machine_id"]))
        state["daemon_start"] = started
        state["policy_after_reconnect"] = self.ctx.action.policy.daemon_policy_fingerprint_remote()
        return started | {"policy_after_reconnect": state["policy_after_reconnect"]}

    def s07_relay_machine_registry_online() -> dict[str, Any]:
        online = self.ctx.action.policy.wait_relay_machine_state_remote(str(state["machine_id"]), online=True, timeout=120)
        state["relay_online"] = online
        return online

    def s08_workspace_list_available() -> dict[str, Any]:
        assert workspace is not None
        listed = self.ctx.action.workspace.list_remote("F_0037 workspace list after reconnect")
        visible = any(workspace_assertions.workspace_display(item) == str(workspace["display"]) for item in listed.get("workspaces", []) if isinstance(item, dict))
        if not visible:
            raise NativeCaseError(
                "product_bug_reconnect_workspace_registry_lost: workspace list lost case workspace after daemon reconnect",
                details={"workspace": workspace, "workspace_list": listed},
            )
        return {"workspace": workspace, "workspace_list": listed}

    def s09_chat_lookup_available() -> dict[str, Any]:
        assert workspace is not None
        project = str(state["project"])
        intern = str(state["intern"])
        daemon_lookup = self.ctx.action.relay_daemon.chat_lookup_remote(workspace, intern)
        relay_lookup = self.ctx.action.relay_daemon.relay_chat_lookup_remote(intern, project)
        expected_chat = str(state.get("chat_id") or "")
        if not (daemon_lookup.get("chat_id") == expected_chat and relay_lookup.get("chat_id") == expected_chat):
            raise NativeCaseError(
                "product_bug_reconnect_chat_lookup_lost: chat lookup lost case registry after daemon reconnect",
                details={"expected_chat_id": expected_chat, "daemon_lookup": daemon_lookup, "relay_lookup": relay_lookup},
            )
        return {"daemon_lookup": daemon_lookup, "relay_lookup": relay_lookup, "expected_chat_id": expected_chat}

    def s10_trigger_case_policy_marker() -> dict[str, Any]:
        marker = f"ci_f_0037_policy_resync_{self.resource_namespace}_{self.run_token}"
        policy = self.ctx.action.policy.daemon_policy_fingerprint_remote()
        state["policy_marker"] = marker
        state["policy_marker_fingerprint"] = policy
        return {"marker": marker, "policy_fingerprint": policy, "trigger": "daemon_reconnect_startup_policy_sync"}

    def s11_daemon_policy_resync() -> dict[str, Any]:
        before = state.get("policy_before") if isinstance(state.get("policy_before"), dict) else {}
        after = state.get("policy_after_reconnect") if isinstance(state.get("policy_after_reconnect"), dict) else self.ctx.action.policy.daemon_policy_fingerprint_remote()
        runtime_env = self.ctx.action.policy.session_env_report_remote()
        ok = after.get("exists") is True and (
            not before.get("mtime_ns") or int(after.get("mtime_ns") or 0) >= int(before.get("mtime_ns") or 0)
        )
        if not ok:
            raise NativeCaseError(
                "product_bug_reconnect_policy_sync_missing: daemon policy was not refreshed after reconnect",
                details={"policy_before": before, "policy_after": after, "runtime_env": runtime_env},
            )
        return {"policy_before": before, "policy_after": after, "runtime_env": runtime_env, "marker": state.get("policy_marker")}

    def s12_no_relay_restart_or_global_reset() -> dict[str, Any]:
        return self.ctx.action.policy.assert_no_relay_restart_or_global_reset_remote(state.get("relay_before") if isinstance(state.get("relay_before"), dict) else {})

    def s13_retained_registry_scene() -> dict[str, Any]:
        assert workspace is not None
        project = str(state["project"])
        intern = str(state["intern"])
        expected_chat = str(state.get("chat_id") or "")
        daemon_lookup = self.ctx.action.relay_daemon.chat_lookup_remote(workspace, intern)
        relay_lookup = self.ctx.action.relay_daemon.relay_chat_lookup_remote(intern, project)
        self.require(
            "f0037_retained_registry_scene",
            daemon_lookup.get("chat_id") == expected_chat and relay_lookup.get("chat_id") == expected_chat,
            {"expected_chat_id": expected_chat, "daemon_lookup": daemon_lookup, "relay_lookup": relay_lookup, "retained_scene": True},
        )
        return {
            "workspace": workspace,
            "intern": intern,
            "expected_chat_id": expected_chat,
            "daemon_lookup": daemon_lookup,
            "relay_lookup": relay_lookup,
            "retained_scene": True,
        }

    try:
        self.run_ordered_scenarios([
            ("F_0037.s01_reset_case_namespace", s01_reset_case_namespace),
            ("F_0037.s02_seed_workspace_intern_group", s02_seed_workspace_intern_group),
            ("F_0037.s03_daemon_relay_connected", s03_daemon_relay_connected),
            ("F_0037.s04_restart_or_disconnect_single_daemon", s04_restart_or_disconnect_single_daemon),
            ("F_0037.s05_wait_for_relay_machine_offline", s05_wait_for_relay_machine_offline),
            ("F_0037.s06_wait_for_daemon_reconnect", s06_wait_for_daemon_reconnect),
            ("F_0037.s07_relay_machine_registry_online", s07_relay_machine_registry_online),
            ("F_0037.s08_workspace_list_available", s08_workspace_list_available),
            ("F_0037.s09_chat_lookup_available", s09_chat_lookup_available),
            ("F_0037.s10_trigger_case_policy_marker", s10_trigger_case_policy_marker),
            ("F_0037.s11_daemon_policy_resync", s11_daemon_policy_resync),
            ("F_0037.s12_no_relay_restart_or_global_reset", s12_no_relay_restart_or_global_reset),
            ("F_0037.s13_retained_registry_scene", s13_retained_registry_scene),
        ])
    finally:
        recovery: dict[str, Any] = {}
        if state.get("machine_id"):
            try:
                status = self.ctx.action.policy.daemon_status_remote("F_0037 recovery daemon status")
                if not status.get("running"):
                    recovery["daemon_start"] = self.ctx.action.policy.start_single_daemon_remote("F_0037 recovery", machine_id=str(state["machine_id"]))
            except Exception:
                try:
                    recovery["daemon_start"] = self.ctx.action.policy.start_single_daemon_remote("F_0037 recovery", machine_id=str(state["machine_id"]))
                except Exception as exc:  # noqa: BLE001
                    recovery["daemon_start_error"] = str(exc)
        if recovery:
            recovery["retained_scene"] = {"workspace": workspace, "interns": interns, "group_delete": False, "workspace_cleanup": False}
            self.artifacts["f0037_recovery"] = recovery
