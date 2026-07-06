import time
from typing import Any

from CI.assertions import core as core_assertions
from CI.cases.base import CaseDefinition
from CI.helpers.product_cli_helper import tail


SCENARIO_IDS = (
    "J_0014.s01_seed_sender_receiver_sessions",
    "J_0014.s02_peer_send_delivered_and_visible",
    "J_0014.s03_missing_target_undeliverable",
    "J_0014.s04_ambiguous_target_undeliverable",
    "J_0014.s05_invalid_mode_http_400",
)


CASE = CaseDefinition(
    id="J_0014_peer_send_routing_error_contract",
    name="Peer send routing and error contract",
    description=(
        "Validates peer send delivered or queued transport evidence plus unknown target, "
        "ambiguous target, and invalid mode error contracts. This is J-scoped because it "
        "starts live intern sessions and verifies message visibility in the receiver pane."
    ),
    stage="remote",
    timeout_seconds=1800,
    kind="f_daemon_relay_api",
    tags=("J", "peer", "daemon", "relay", "intern-session"),
    parallel_safe=False,
    extra={
        "ci_stage": "J",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "cli.internctl",
            "create_feishu_group",
            "use_feishu_group",
            "create_intern",
            "create_task",
            "start_intern_session",
            "send_user_message",
            "peer.transport_send",
            "relay.read_chat_presence",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "native.peer_transport_delivered",
            "native.peer_transport_target_scoped",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "feishu_chat:ci_j_0014_sender", "mode": "exclusive"},
            {"resource": "feishu_chat:ci_j_0014_receiver", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_j_0014_sender", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_j_0014_receiver", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_j_0014", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "session:axis_intern_agents_backup:intern_ci_j_0014_sender", "mode": "exclusive"},
            {"resource": "session:axis_intern_agents_backup:intern_ci_j_0014_receiver", "mode": "exclusive"},
            {"resource": "task:axis_intern_agents_backup:task_ci_j_0014_peer_transport", "mode": "exclusive"},
            {"resource": "tmux:ci_j_0014", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_j_0014_project_a", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_j_0014_project_b", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_j_0014",
            "workspace:ci_j_0014_project_a",
            "workspace:ci_j_0014_project_b",
            "intern:intern_ci_j_0014_sender",
            "intern:intern_ci_j_0014_receiver",
            "task:task_ci_j_0014_peer_transport",
        ),
        "run_mode": "remote_deployed_api",
        "journey_steps": (
            "Start sender and receiver intern sessions.",
            "Send a peer message and verify it becomes visible in the receiver session pane.",
            "Verify missing, ambiguous, and invalid-mode routing errors.",
        ),
        "notes": (
            "Moved from F_0014 because it starts live intern sessions and inspects receiver-pane message visibility.",
            "No natural-language business reply is required, but the live intern session boundary makes this J-scoped.",
        ),
    },
)


def run_f_peer_send_routing_error_contract(case: Any) -> None:
    self = case
    scenario_prefix = self.case_id.split("_peer_send", 1)[0]
    repo = self.ctx.action.workspace.local_repo_fixture_remote("j0014_peer")
    workspace_a = self.ctx.action.workspace.create_case_remote(
        suffix="project_a",
        provider="local",
        repo_url=str(repo),
        mode="local_only",
        local_path=str(repo),
    )
    workspace_b = self.ctx.action.workspace.create_case_remote(
        suffix="project_b",
        provider="local",
        repo_url=str(repo),
        mode="local_only",
        local_path=str(repo),
    )
    project_a = str(workspace_a["display"])
    project_b = str(workspace_b["display"])
    sender = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace_a, "sender", repo_url=str(repo)))["intern"]
    receiver = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace_a, "receiver", repo_url=str(repo)))["intern"]
    state: dict[str, Any] = {
        "workspace_a": workspace_a,
        "workspace_b": workspace_b,
        "project_a": project_a,
        "project_b": project_b,
        "sender": sender,
        "receiver": receiver,
    }

    def s01_seed_sender_receiver_sessions() -> dict[str, Any]:
        self.ctx.action.session.start_remote(workspace_a, sender)
        self.ctx.action.session.start_remote(workspace_a, receiver)
        sender_chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace_a, sender, timeout=self.args.timeout)
        receiver_chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace_a, receiver, timeout=self.args.timeout)
        state.update({"sender_chat": sender_chat, "receiver_chat": receiver_chat})
        return {"sender_chat": sender_chat, "receiver_chat": receiver_chat}

    def s02_peer_send_delivered_and_visible() -> dict[str, Any]:
        token = f"J0014_PING_{int(time.time())}"
        result = self.http_json(
            f"{scenario_prefix} peer next send",
            "POST",
            "/api/intern/peer/send",
            {
                "from_intern_name": sender,
                "to_intern_name": receiver,
                "to_project": project_a,
                "mode": "next",
                "content": f"J0014 ping {token}",
            },
            timeout=self.args.timeout,
        )
        self.require("j0014_peer_delivery_status", result.get("status") in {"delivered", "queued"}, result)
        deadline = time.time() + 90
        pane = ""
        while time.time() < deadline:
            pane = self.ctx.action.session.tmux_capture_remote(receiver, lines=240)
            if token in pane:
                break
            time.sleep(2)
        self.require("j0014_peer_message_visible_on_receiver", token in pane, {"token": token, "target_tail": tail(pane, 2000), "result": result})
        state["delivery"] = {"result": result, "token": token}
        return state["delivery"]

    def s03_missing_target_undeliverable() -> dict[str, Any]:
        result = self.http_json(
            f"{scenario_prefix} peer missing target",
            "POST",
            "/api/intern/peer/send",
            {
                "from_intern_name": sender,
                "to_intern_name": self.remote_context.identity("missing_peer"),
                "to_project": project_a,
                "mode": "next",
                "content": "missing target probe",
            },
            timeout=90,
        )
        self.require("j0014_missing_target_unknown", result.get("status") == "undeliverable" and result.get("reason") == "unknown_target", result)
        state["missing_target"] = result
        return result

    def s04_ambiguous_target_undeliverable() -> dict[str, Any]:
        duplicate = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace_b, "receiver", repo_url=str(repo)))["intern"]
        self.require("j0014_duplicate_receiver_same_name", duplicate == receiver, {"duplicate": duplicate, "receiver": receiver})
        created_b = self.http_json(f"{scenario_prefix} receiver project B group create", "POST", "/api/group/create", {"project": project_b, "intern_name": receiver}, timeout=120)
        result = self.http_json(
            f"{scenario_prefix} peer ambiguous target",
            "POST",
            "/api/intern/peer/send",
            {
                "from_intern_name": sender,
                "to_intern_name": receiver,
                "mode": "next",
                "content": "ambiguous target probe",
            },
            timeout=90,
        )
        self.require(
            "j0014_ambiguous_target_candidates",
            result.get("status") == "undeliverable" and result.get("reason") == "ambiguous_target" and bool(result.get("candidates")),
            {"result": result, "project_b_group": created_b},
        )
        state["ambiguous_target"] = {"result": result, "project_b_group": created_b}
        return state["ambiguous_target"]

    def s05_invalid_mode_http_400() -> dict[str, Any]:
        invalid = self.daemon_request_json(
            f"{scenario_prefix} peer invalid mode",
            "POST",
            "/api/intern/peer/send",
            {
                "from_intern_name": sender,
                "to_intern_name": receiver,
                "to_project": project_a,
                "mode": "invalid",
                "content": "invalid mode probe",
            },
            timeout=60,
            check=False,
        )
        detail = core_assertions.require_http_status(self.require, "j0014_invalid_mode_http_400", invalid, 400, error_contains="invalid_mode")
        state["invalid_mode"] = detail
        return detail

    self.run_ordered_scenarios([
        (f"{scenario_prefix}.s01_seed_sender_receiver_sessions", s01_seed_sender_receiver_sessions),
        (f"{scenario_prefix}.s02_peer_send_delivered_and_visible", s02_peer_send_delivered_and_visible),
        (f"{scenario_prefix}.s03_missing_target_undeliverable", s03_missing_target_undeliverable),
        (f"{scenario_prefix}.s04_ambiguous_target_undeliverable", s04_ambiguous_target_undeliverable),
        (f"{scenario_prefix}.s05_invalid_mode_http_400", s05_invalid_mode_http_400),
    ])
    self.artifacts["peer_send_routing_error_contract"] = state
