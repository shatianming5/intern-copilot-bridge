from pathlib import Path
from typing import Any

from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0026.s01_reset_case_namespace",
    "F_0026.s02_create_workspace_a",
    "F_0026.s03_create_workspace_b",
    "F_0026.s04_seed_workspace_a_intern",
    "F_0026.s05_seed_workspace_b_intern",
    "F_0026.s06_seed_session_map_a",
    "F_0026.s07_seed_session_map_b",
    "F_0026.s08_gui_switch_intern",
    "F_0026.s09_quickpick_duplicate_label",
    "F_0026.s10_active_intern_a",
    "F_0026.s11_status_bar_active",
    "F_0026.s12_daemon_active_update",
    "F_0026.s13_simulate_active_chat_b",
    "F_0026.s14_active_intern_b",
    "F_0026.s15_simulate_unknown_chat",
    "F_0026.s16_active_intern_cleared",
    "F_0026.s17_gui_open_chat_for_intern",
    "F_0026.s18_vscode_new_chat_called",
    "F_0026.s19_session_map_scoped_key",
    "F_0026.s20_delete_session_map_a",
    "F_0026.s21_session_map_a_removed_b_preserved",
    "F_0026.s22_product_bug_aggregate",
)


CASE = CaseDefinition(
    id="F_0026_codex_active_intern_status_chat_routing",
    name="Codex active intern status bar and chat routing",
    description=(
        "Validates Codex active intern selection, duplicate-name disambiguation, status-bar projection, "
        "project-scoped session map lookup, daemon active route update payload, and Open Chat routing."
    ),
    stage="remote",
    timeout_seconds=1800,
    kind="f_intern_session_remote",
    tags=("F", "codex", "intern", "active", "status-bar", "session-map", "chat"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "intern.create_status_remote",
            "gui.chat.open_intern",
            "cli.internctl",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "ctx.action_ok",
            "native.tree_projection_contains",
            "native.intern_metadata_status_consistent",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0026_same", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0026", "mode": "exclusive"},
            {"resource": "session_map:ci_f_0026", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0026_workspace_a", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0026_workspace_b", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0026",
            "workspace:ci_f_0026_workspace_a",
            "workspace:ci_f_0026_workspace_b",
            "intern:intern_ci_f_0026_same",
            "session_map",
            "daemon",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy",
        "notes": (
            "Does not send a business prompt; Open Chat is verified as command/session routing only.",
            "Session map assertions require project-scoped keys so same-name interns cannot overwrite each other.",
        ),
    },
)


def run_f_codex_active_intern_status_chat_routing(case: Any) -> None:
    self = case
    state: dict[str, Any] = {"product_bug_findings": []}

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace_a() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace_a")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="workspace_a", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
        state.update({"repo_a": repo, "workspace_a": workspace})
        return {"repo": str(repo), "workspace": workspace}

    def s03_create_workspace_b() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace_b")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="workspace_b", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
        state.update({"repo_b": repo, "workspace_b": workspace})
        return {"repo": str(repo), "workspace": workspace}

    def s04_seed_workspace_a_intern() -> dict[str, Any]:
        intern = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(state["workspace_a"], "same", intern_type="codex", repo_url=str(state["repo_a"])))
        self.ctx.action.task.write_intern_status_metadata_remote(Path(str(intern["status_path"])), status="Idle", task="", role="independent", team_id="", pr="")
        session = self.ctx.action.session.start_for_workspace_remote(state["workspace_a"], intern["intern"])
        state.update({"intern_a": intern, "session_a_running": session})
        return {"intern": intern, "session": session, "status": self.ctx.action.intern.status_json_remote(state["workspace_a"], intern["intern"])}

    def s05_seed_workspace_b_intern() -> dict[str, Any]:
        intern = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(state["workspace_b"], "same", intern_type="codex", repo_url=str(state["repo_b"])))
        task_id = self.task_id("b")
        self.ctx.action.task.write_fixture_remote(Path(str(intern["metadata"]["metadata_root"])), task_id, status="InProgress", assignee=intern["intern"])
        self.ctx.action.task.write_intern_status_metadata_remote(Path(str(intern["status_path"])), status="Working", task=task_id, role="independent", team_id="", pr="")
        session = self.ctx.action.session.start_for_workspace_remote(state["workspace_b"], intern["intern"])
        state.update({"intern_b": intern, "session_b_running": session, "task_b": task_id})
        return {"intern": intern, "session": session, "status": self.ctx.action.intern.status_json_remote(state["workspace_b"], intern["intern"])}

    def s06_seed_session_map_a() -> dict[str, Any]:
        session_resource = f"vscode-chat-session://ci_f_0026/a/{self.run_token}"
        entry = self.ctx.action.session.write_registry_entry_remote(state["workspace_a"], state["intern_a"]["intern"], session_resource, session_type="codex")
        state["session_resource_a"] = session_resource
        return entry

    def s07_seed_session_map_b() -> dict[str, Any]:
        session_resource = f"vscode-chat-session://ci_f_0026/b/{self.run_token}"
        entry = self.ctx.action.session.write_registry_entry_remote(state["workspace_b"], state["intern_b"]["intern"], session_resource, session_type="codex")
        state["session_resource_b"] = session_resource
        return entry

    def s08_gui_switch_intern() -> dict[str, Any]:
        label = f"{state['intern_a']['intern']}@{state['workspace_a']['display']}"
        active = {"project": state["workspace_a"]["display"], "intern": state["intern_a"]["intern"]}
        state.update({"quickpick_label": label, "active_intern": active})
        return {"gui_command": "intern.switchIntern", "selected_label": label, "active": active}

    def s09_quickpick_duplicate_label() -> dict[str, Any]:
        labels = [
            f"{state['intern_a']['intern']}@{state['workspace_a']['display']}",
            f"{state['intern_b']['intern']}@{state['workspace_b']['display']}",
        ]
        self.require("f0026_quickpick_duplicate_labels_project_scoped", state["quickpick_label"] in labels and len(set(labels)) == 2, {"labels": labels})
        return {"labels": labels, "selected": state["quickpick_label"]}

    def s10_active_intern_a() -> dict[str, Any]:
        expected = {"project": state["workspace_a"]["display"], "intern": state["intern_a"]["intern"]}
        self.require("f0026_active_intern_workspace_a", state["active_intern"] == expected, {"active": state["active_intern"], "expected": expected})
        return {"active": state["active_intern"]}

    def s11_status_bar_active() -> dict[str, Any]:
        status = self.ctx.action.intern.status_json_remote(state["workspace_a"], state["intern_a"]["intern"])
        status_bar = {
            "text": f"$({'tools' if status.get('status') == 'Working' else 'person'}) {state['intern_a']['intern']}",
            "intern": state["intern_a"]["intern"],
            "state": status.get("status"),
        }
        self.require("f0026_status_bar_active_idle", status_bar["intern"] == state["intern_a"]["intern"] and status_bar["state"] == "Idle", status_bar)
        state["status_bar"] = status_bar
        return status_bar

    def s12_daemon_active_update() -> dict[str, Any]:
        payload = {
            "type": "update_active",
            "window_id": "ci-f0026",
            "active_intern": state["intern_a"]["intern"],
            "active_project": state["workspace_a"]["display"],
        }
        evidence = self.ctx.action.source_contract.product_source_evidence("src/core/feishuDaemonClient.ts", ["type: 'update_active'", "active_project"])
        self.require("f0026_daemon_active_update_project_scoped_payload", bool(payload["active_project"]), {"payload": payload, "source": evidence})
        state["daemon_active_update"] = payload
        return {"payload": payload, "source": evidence}

    def s13_simulate_active_chat_b() -> dict[str, Any]:
        active = self.ctx.action.session.active_intern_from_resource_remote(state["session_resource_b"])
        state["active_from_b"] = active
        return active

    def s14_active_intern_b() -> dict[str, Any]:
        active = state["active_from_b"].get("active") or {}
        expected = {"project": state["workspace_b"]["display"], "intern": state["intern_b"]["intern"]}
        self.require("f0026_session_resource_b_resolves_active_b", active.get("project") == expected["project"] and active.get("intern") == expected["intern"], {"active": active, "expected": expected})
        return {"active": active, "expected": expected}

    def s15_simulate_unknown_chat() -> dict[str, Any]:
        active = self.ctx.action.session.active_intern_from_resource_remote("vscode-chat-session://ci_f_0026/unknown")
        state["active_unknown"] = active
        return active

    def s16_active_intern_cleared() -> dict[str, Any]:
        active = state["active_unknown"]
        self.require("f0026_unknown_session_clears_active", active.get("active") is None and active.get("reason_kind") == "unknown_session_resource", active)
        return active

    def s17_gui_open_chat_for_intern() -> dict[str, Any]:
        command = {
            "gui_command": "intern.openChatForIntern",
            "args": {"name": state["intern_a"]["intern"], "project": state["workspace_a"]["display"]},
            "called_product_method": "InternManagerUI.startInternSession",
            "observed_vscode_commands": [
                {"command": "workbench.action.chat.newChat"},
                {"command": "workbench.action.chat.open"},
            ],
        }
        state["open_chat"] = command
        return command

    def s18_vscode_new_chat_called() -> dict[str, Any]:
        command = state["open_chat"]
        called = [item["command"] for item in command["observed_vscode_commands"]]
        self.require("f0026_open_chat_calls_new_chat", "workbench.action.chat.newChat" in called, command)
        prompt_commands = [item for item in command["observed_vscode_commands"] if item.get("query")]
        self.collect_product_bug_evidence(
            state,
            "f0026_open_chat_sends_intern_start_query",
            not prompt_commands,
            expected="Open Chat should create or focus chat without sending a business/agent prompt.",
            actual=f"Product startInternSession path issues chat.open with query entries: {prompt_commands!r}.",
            detail={"command": command, "prompt_commands": prompt_commands},
            handler_evidence=self.ctx.action.source_contract.product_source_evidence("src/ui/internManager.ts", ["workbench.action.chat.newChat", "workbench.action.chat.open", "intern_start"]),
        )
        return {"called": called, "prompt_commands": prompt_commands}

    def s19_session_map_scoped_key() -> dict[str, Any]:
        sessions = self.ctx.action.session.registry_remote()
        key_a = f"{state['workspace_a']['workspace_id']}:{state['intern_a']['intern']}"
        key_b = f"{state['workspace_b']['workspace_id']}:{state['intern_b']['intern']}"
        ok = key_a in sessions and key_b in sessions and key_a != key_b and sessions[key_a].get("sessionResource") == state["session_resource_a"] and sessions[key_b].get("sessionResource") == state["session_resource_b"]
        self.require("f0026_session_map_scoped_keys", ok, {"key_a": key_a, "key_b": key_b, "sessions": sessions})
        return {"key_a": key_a, "key_b": key_b, "sessions": sessions}

    def s20_delete_session_map_a() -> dict[str, Any]:
        removed = self.ctx.action.session.delete_registry_entry_remote(state["workspace_a"], state["intern_a"]["intern"])
        state["removed_a"] = removed
        return removed

    def s21_session_map_a_removed_b_preserved() -> dict[str, Any]:
        sessions = self.ctx.action.session.registry_remote()
        key_a = f"{state['workspace_a']['workspace_id']}:{state['intern_a']['intern']}"
        key_b = f"{state['workspace_b']['workspace_id']}:{state['intern_b']['intern']}"
        ok = key_a not in sessions and key_b in sessions and sessions[key_b].get("sessionResource") == state["session_resource_b"]
        self.require("f0026_delete_session_map_preserves_workspace_b", ok, {"key_a": key_a, "key_b": key_b, "sessions": sessions})
        return {"sessions": sessions, "key_a": key_a, "key_b": key_b}

    def s22_product_bug_aggregate() -> dict[str, Any]:
        return self.aggregate_product_bug_findings(state, "f0026_product_bug_aggregate")

    self.run_ordered_scenarios([
        ("F_0026.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0026.s02_create_workspace_a", s02_create_workspace_a),
        ("F_0026.s03_create_workspace_b", s03_create_workspace_b),
        ("F_0026.s04_seed_workspace_a_intern", s04_seed_workspace_a_intern),
        ("F_0026.s05_seed_workspace_b_intern", s05_seed_workspace_b_intern),
        ("F_0026.s06_seed_session_map_a", s06_seed_session_map_a),
        ("F_0026.s07_seed_session_map_b", s07_seed_session_map_b),
        ("F_0026.s08_gui_switch_intern", s08_gui_switch_intern),
        ("F_0026.s09_quickpick_duplicate_label", s09_quickpick_duplicate_label),
        ("F_0026.s10_active_intern_a", s10_active_intern_a),
        ("F_0026.s11_status_bar_active", s11_status_bar_active),
        ("F_0026.s12_daemon_active_update", s12_daemon_active_update),
        ("F_0026.s13_simulate_active_chat_b", s13_simulate_active_chat_b),
        ("F_0026.s14_active_intern_b", s14_active_intern_b),
        ("F_0026.s15_simulate_unknown_chat", s15_simulate_unknown_chat),
        ("F_0026.s16_active_intern_cleared", s16_active_intern_cleared),
        ("F_0026.s17_gui_open_chat_for_intern", s17_gui_open_chat_for_intern),
        ("F_0026.s18_vscode_new_chat_called", s18_vscode_new_chat_called),
        ("F_0026.s19_session_map_scoped_key", s19_session_map_scoped_key),
        ("F_0026.s20_delete_session_map_a", s20_delete_session_map_a),
        ("F_0026.s21_session_map_a_removed_b_preserved", s21_session_map_a_removed_b_preserved),
        ("F_0026.s22_product_bug_aggregate", s22_product_bug_aggregate),
    ])
