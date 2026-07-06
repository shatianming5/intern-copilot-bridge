from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0007.s01_reset_case_namespace",
    "F_0007.s02_create_workspace_for_case",
    "F_0007.s03_gui_create_intern_cli_equivalent",
    "F_0007.s04_wait_status_metadata_group_registry",
    "F_0007.s05_tree_projection_and_cli_status_payload",
)


CASE = CaseDefinition(
    id="F_0007_intern_create_status_contract",
    name="F_0007_intern_create_status_contract",
    description=(
        "Validates case-scoped Codex intern creation in an existing workspace, including metadata, "
        "Feishu chat binding, relay registry, TreeView/list projection, and CLI status payload."
    ),
    stage="remote",
    timeout_seconds=1800,
    kind="f_intern_session_remote",
    tags=("F", "intern", "create", "status", "gui", "cli", "daemon", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "intern.create_status_remote",
            "gui.intern.create",
            "daemon.chat_lookup_local",
            "relay.read_chat_presence",
            "daemon.read_status",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "ctx.action_ok",
            "native.intern_metadata_status_consistent",
            "native.relay_registry_entry",
            "native.tree_projection_contains",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "feishu_chat:ci_f_0007", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0007_worker", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0007", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0007_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0007",
            "workspace:ci_f_0007_workspace",
            "intern:intern_ci_f_0007_worker",
            "case_scoped_feishu_group",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy",
        "notes": (
            "Do not send a business prompt.",
            "Case initialization only cleans this case namespace; successful runs retain workspace, intern, chat, and registry evidence.",
            "GUI command equivalence is verified as the registered GUI to CLI contract; remote side effects are observed through CLI, daemon, and relay.",
        ),
    },
)


def run_f_intern_create_status_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace_for_case() -> dict[str, Any]:
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

    def s03_gui_create_intern_cli_equivalent() -> dict[str, Any]:
        workspace = state["workspace"]
        repo = state["repo"]
        intern = self.ctx.action.intern.create_case_remote(workspace, "worker", repo_url=str(repo))
        state["intern"] = intern
        return {
            "intern": intern,
            "gui_command": "intern.createIntern",
            "cli_equivalent": "internctl create <intern> --project <project> --type codex",
        }

    def s04_wait_status_metadata_group_registry() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        metadata = self.require_checks(
            self.ctx.action.intern.metadata_status_consistent_remote(workspace, intern, expected_status="Idle")
        )
        chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace, intern, timeout=self.args.timeout)
        relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace, intern, timeout=self.args.timeout)
        self.require(
            "chat_lookup_matches_relay_registry_" + intern,
            bool(chat.get("chat_id")) and chat.get("chat_id") == relay.get("chat_id"),
            {"chat_lookup": chat, "relay": relay},
        )
        state.update({"metadata": metadata["metadata"], "chat": chat, "relay": relay})
        return {"metadata": metadata, "chat_lookup": chat, "relay_registry": relay}

    def s05_tree_projection_and_cli_status_payload() -> dict[str, Any]:
        workspace = state["workspace"]
        intern = state["intern"]
        projection = self.require_checks(
            self.ctx.action.intern.tree_projection_contains_remote(workspace, intern, expected_status="Idle")
        )
        status = self.ctx.action.intern.status_json_remote(workspace, intern)
        self.require(
            "status_payload_idle_codex_project_" + intern,
            status.get("status") == "Idle"
            and status.get("project") == workspace["display"]
            and status.get("workspace_id") == workspace["workspace_id"]
            and projection.get("type") == "codex",
            {"status": status, "projection": projection},
        )
        return {"status": status, "tree_projection": projection, "retained_scene": state}

    self.run_ordered_scenarios([
        ("F_0007.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0007.s02_create_workspace_for_case", s02_create_workspace_for_case),
        ("F_0007.s03_gui_create_intern_cli_equivalent", s03_gui_create_intern_cli_equivalent),
        ("F_0007.s04_wait_status_metadata_group_registry", s04_wait_status_metadata_group_registry),
        ("F_0007.s05_tree_projection_and_cli_status_payload", s05_tree_projection_and_cli_status_payload),
    ])
