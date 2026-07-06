from pathlib import Path
from typing import Any

from CI.assertions import treeview as treeview_assertions
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0025.s01_reset_case_namespace",
    "F_0025.s02_create_workspace_a",
    "F_0025.s03_create_workspace_b",
    "F_0025.s04_seed_workspace_a_same_offline",
    "F_0025.s05_seed_workspace_b_same_online",
    "F_0025.s06_seed_workspace_a_active_online",
    "F_0025.s07_set_active_intern",
    "F_0025.s08_gui_refresh_tree",
    "F_0025.s09_workspace_a_tree_contains",
    "F_0025.s10_workspace_b_tree_contains",
    "F_0025.s11_no_cross_workspace_leak",
    "F_0025.s12_tree_item_context",
    "F_0025.s13_tree_item_icon",
    "F_0025.s14_intern_order",
    "F_0025.s15_offline_group_collapsed",
    "F_0025.s16_intern_description",
    "F_0025.s17_intern_tooltip",
    "F_0025.s18_open_chat_context_command",
    "F_0025.s19_gui_command_args_project_scoped",
    "F_0025.s20_cli_list_interns",
    "F_0025.s21_tree_matches_cli_interns",
    "F_0025.s22_no_team_or_non_codex_fixture",
    "F_0025.s23_product_bug_aggregate",
)


CASE = CaseDefinition(
    id="F_0025_codex_intern_treeview_projection_scope",
    name="Codex intern TreeView projection and project scope",
    description=(
        "Validates Codex intern TreeView projection, project-scoped same-name interns, "
        "active/online/offline ordering, context menu routing args, and CLI/list consistency."
    ),
    stage="remote",
    timeout_seconds=2400,
    kind="f_intern_session_remote",
    tags=("F", "codex", "intern", "treeview", "project-scope", "gui", "cli"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "intern.create_status_remote",
            "session.codex_start_restart_remote",
            "gui.tree.refresh",
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
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0025_active", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0025_same", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0025", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "task:axis_intern_agents_backup:task_ci_f_0025_focus", "mode": "exclusive"},
            {"resource": "tmux:ci_f_0025", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0025_workspace_a", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0025_workspace_b", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0025",
            "workspace:ci_f_0025_workspace_a",
            "workspace:ci_f_0025_workspace_b",
            "intern:intern_ci_f_0025_same",
            "intern:intern_ci_f_0025_active",
            "task:task_ci_f_0025_focus",
            "tmux",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy",
        "notes": (
            "Codex-only: no Claude, Copilot, not_now intern, or Team fixture is created.",
            "Open Chat is verified as a project-scoped GUI command argument contract and does not send a business prompt.",
            "Product TreeView icon/context mismatches are reported as product bug evidence; CI must not patch product code.",
        ),
    },
)


def run_f_codex_intern_treeview_projection_scope(case: Any) -> None:
    self = case
    state: dict[str, Any] = {"product_bug_findings": []}
    pr_url = "https://codeup.aliyun.com/org/repo/change/250"

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace_a() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace_a")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace_a",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        state.update({"repo_a": repo, "workspace_a": workspace})
        return {"repo": str(repo), "workspace": workspace}

    def s03_create_workspace_b() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace_b")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace_b",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        state.update({"repo_b": repo, "workspace_b": workspace})
        return {"repo": str(repo), "workspace": workspace}

    def s04_seed_workspace_a_same_offline() -> dict[str, Any]:
        workspace = state["workspace_a"]
        same = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "same", intern_type="codex", repo_url=str(state["repo_a"])))
        status_path = Path(str(same["status_path"]))
        self.ctx.action.task.write_intern_status_metadata_remote(status_path, status="Idle", task="", role="independent", team_id="", pr="")
        state["same_a"] = same
        session_status = self.ctx.action.session.status_for_workspace_remote(workspace, same["intern"], check=False)
        return {
            "intern": same,
            "status": self.ctx.action.intern.status_json_remote(workspace, same["intern"]),
            "online": session_status.get("running") is True,
        }

    def s05_seed_workspace_b_same_online() -> dict[str, Any]:
        workspace = state["workspace_b"]
        same = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "same", intern_type="codex", repo_url=str(state["repo_b"])))
        metadata = same["metadata"]
        task_id = self.task_id("focus")
        self.ctx.action.task.write_fixture_remote(Path(str(metadata["metadata_root"])), task_id, status="InProgress", assignee=same["intern"])
        self.ctx.action.task.write_intern_status_metadata_remote(Path(str(same["status_path"])), status="Working", task=task_id, role="independent", team_id="", pr=pr_url)
        session = self.ctx.action.session.start_for_workspace_remote(workspace, same["intern"])
        state.update({"same_b": same, "task_id": task_id, "session_b": session})
        return {"intern": same, "task_id": task_id, "session": session, "status": self.ctx.action.intern.status_json_remote(workspace, same["intern"])}

    def s06_seed_workspace_a_active_online() -> dict[str, Any]:
        workspace = state["workspace_a"]
        active = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "active", intern_type="codex", repo_url=str(state["repo_a"])))
        metadata = active["metadata"]
        task_id = state["task_id"]
        self.ctx.action.task.write_fixture_remote(Path(str(metadata["metadata_root"])), task_id, status="InProgress", assignee=active["intern"])
        self.ctx.action.task.write_intern_status_metadata_remote(Path(str(active["status_path"])), status="Working", task=task_id, role="independent", team_id="", pr="")
        session = self.ctx.action.session.start_for_workspace_remote(workspace, active["intern"])
        state.update({"active_a": active, "session_active": session})
        return {"intern": active, "task_id": task_id, "session": session, "status": self.ctx.action.intern.status_json_remote(workspace, active["intern"])}

    def s07_set_active_intern() -> dict[str, Any]:
        active = state["active_a"]["intern"]
        state["active_intern"] = active
        payload = {"active_intern": active, "active_project": state["workspace_a"]["display"], "source": "status_bar_focus_contract"}
        state["daemon_active_payload"] = payload
        return payload

    def s08_gui_refresh_tree() -> dict[str, Any]:
        active = state["active_intern"]
        tree_a = treeview_assertions.checked_workspace_projection(self,
            state["workspace_a"],
            [state["same_a"]["intern"], state["active_a"]["intern"]],
            focus_intern=active,
        )
        tree_b = treeview_assertions.checked_workspace_projection(self, state["workspace_b"], [state["same_b"]["intern"]], focus_intern=active)
        state.update({"tree_a": tree_a, "tree_b": tree_b})
        return {"gui_command": "intern.refreshTree", "tree_a": tree_a, "tree_b": tree_b}

    def s09_workspace_a_tree_contains() -> dict[str, Any]:
        names = [item["name"] for item in state["tree_a"]["items"]]
        expected = {state["same_a"]["intern"], state["active_a"]["intern"]}
        self.require("f0025_workspace_a_tree_contains_only_expected", set(names) == expected, {"names": names, "expected": sorted(expected)})
        return {"names": names}

    def s10_workspace_b_tree_contains() -> dict[str, Any]:
        names = [item["name"] for item in state["tree_b"]["items"]]
        self.require("f0025_workspace_b_tree_contains_same_only", names == [state["same_b"]["intern"]], {"names": names, "tree": state["tree_b"]})
        return {"names": names}

    def s11_no_cross_workspace_leak() -> dict[str, Any]:
        item_a = treeview_assertions.checked_item_projection(self, state["workspace_a"], state["same_a"]["intern"], focus_intern=state["active_intern"])
        item_b = treeview_assertions.checked_item_projection(self, state["workspace_b"], state["same_b"]["intern"], focus_intern=state["active_intern"])
        ok = (
            item_a["project"] == state["workspace_a"]["display"]
            and item_b["project"] == state["workspace_b"]["display"]
            and item_a["command_args"]["project"] != item_b["command_args"]["project"]
            and item_a["state"] == "Idle"
            and item_b["state"] == "Working"
        )
        self.require("f0025_same_name_project_scoped_tree_items", ok, {"item_a": item_a, "item_b": item_b})
        return {"item_a": item_a, "item_b": item_b}

    def s12_tree_item_context() -> dict[str, Any]:
        item = treeview_assertions.checked_item_projection(self, state["workspace_a"], state["active_a"]["intern"], focus_intern=state["active_intern"])
        self.require("f0025_codex_context_value", item["context_value"] == "intern-codex", item)
        return item

    def s13_tree_item_icon() -> dict[str, Any]:
        item = treeview_assertions.checked_item_projection(self, state["workspace_a"], state["active_a"]["intern"], focus_intern=state["active_intern"])
        expected_icon_ok = item["icon"]["id"] == "rocket" and item["icon"].get("color") in {"charts.yellow", "charts.blue"}
        evidence = self.collect_product_bug_evidence(
            state,
            "f0025_active_codex_icon_contract",
            expected_icon_ok,
            expected="Active/online Codex intern TreeItem keeps rocket icon with active/online color.",
            actual=f"Observed product icon model {item['icon']!r} for active Codex intern.",
            detail={"tree_item": item},
            handler_evidence=self.ctx.action.source_contract.product_source_evidence("src/ui/internTree.ts", ["star-full", "rocket", "charts.yellow", "charts.blue"]),
        )
        return {"tree_item": item, "product_bug_evidence": evidence}

    def s14_intern_order() -> dict[str, Any]:
        order = [item["name"] for item in state["tree_a"]["items"]]
        self.require("f0025_active_before_offline", order[:2] == [state["active_a"]["intern"], state["same_a"]["intern"]], {"order": order, "tree": state["tree_a"]})
        return {"order": order}

    def s15_offline_group_collapsed() -> dict[str, Any]:
        group = state["tree_a"].get("inactive_group") or {}
        names = [item["name"] for item in group.get("items", [])]
        self.require(
            "f0025_offline_other_group_collapsed",
            group.get("collapsed") is True and names == [state["same_a"]["intern"]],
            {"group": group, "names": names},
        )
        return {"group": group}

    def s16_intern_description() -> dict[str, Any]:
        item = treeview_assertions.checked_item_projection(self, state["workspace_a"], state["active_a"]["intern"], focus_intern=state["active_intern"])
        ok = "Working" in item["description"] and state["task_id"] in item["description"]
        self.require("f0025_description_state_task", ok, item)
        return item

    def s17_intern_tooltip() -> dict[str, Any]:
        item = treeview_assertions.checked_item_projection(self, state["workspace_b"], state["same_b"]["intern"], focus_intern=state["active_intern"])
        tooltip = item["tooltip"]
        ok = (
            tooltip.get("type") == "Codex"
            and tooltip.get("project") == state["workspace_b"]["display"]
            and tooltip.get("state") == "Working"
            and tooltip.get("current_task") == state["task_id"]
            and tooltip.get("current_pr") == pr_url
        )
        self.require("f0025_tooltip_project_task_pr", ok, {"tooltip": tooltip, "item": item})
        return {"tooltip": tooltip, "item": item}

    def s18_open_chat_context_command() -> dict[str, Any]:
        command = {
            "command": "intern.openChatForIntern",
            "args": treeview_assertions.checked_item_projection(self, state["workspace_b"], state["same_b"]["intern"])["command_args"],
            "dry_run": True,
            "business_prompt_sent": False,
        }
        state["open_chat_command"] = command
        return command

    def s19_gui_command_args_project_scoped() -> dict[str, Any]:
        command = state["open_chat_command"]
        expected = {"name": state["same_b"]["intern"], "project": state["workspace_b"]["display"]}
        self.require("f0025_open_chat_args_project_scoped", command["args"] == expected, {"command": command, "expected": expected})
        return {"command": command, "expected": expected}

    def s20_cli_list_interns() -> dict[str, Any]:
        items = self.ctx.action.intern.list_json_remote()
        state["cli_list"] = items
        return {"project": state["workspace_a"]["display"], "items": [item for item in items if item.get("project") == state["workspace_a"]["display"]]}

    def s21_tree_matches_cli_interns() -> dict[str, Any]:
        tree_by_name = {item["name"]: item for item in state["tree_a"]["items"]}
        cli_by_name = {
            item["name"]: item for item in state["cli_list"]
            if item.get("project") == state["workspace_a"]["display"] and item.get("type") == "codex"
        }
        mismatches = []
        for name, tree_item in tree_by_name.items():
            cli_item = cli_by_name.get(name)
            if not cli_item or cli_item.get("status") != tree_item.get("status") or cli_item.get("workspace_id") != tree_item.get("workspace_id"):
                mismatches.append({"name": name, "tree": tree_item, "cli": cli_item})
        self.require("f0025_tree_matches_cli_codex_projection", not mismatches, {"mismatches": mismatches, "tree": tree_by_name, "cli": cli_by_name})
        return {"tree": tree_by_name, "cli": cli_by_name}

    def s22_no_team_or_non_codex_fixture() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.intern.no_team_or_non_codex_fixture_remote())

    def s23_product_bug_aggregate() -> dict[str, Any]:
        return self.aggregate_product_bug_findings(state, "f0025_product_bug_aggregate")

    self.run_ordered_scenarios([
        ("F_0025.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0025.s02_create_workspace_a", s02_create_workspace_a),
        ("F_0025.s03_create_workspace_b", s03_create_workspace_b),
        ("F_0025.s04_seed_workspace_a_same_offline", s04_seed_workspace_a_same_offline),
        ("F_0025.s05_seed_workspace_b_same_online", s05_seed_workspace_b_same_online),
        ("F_0025.s06_seed_workspace_a_active_online", s06_seed_workspace_a_active_online),
        ("F_0025.s07_set_active_intern", s07_set_active_intern),
        ("F_0025.s08_gui_refresh_tree", s08_gui_refresh_tree),
        ("F_0025.s09_workspace_a_tree_contains", s09_workspace_a_tree_contains),
        ("F_0025.s10_workspace_b_tree_contains", s10_workspace_b_tree_contains),
        ("F_0025.s11_no_cross_workspace_leak", s11_no_cross_workspace_leak),
        ("F_0025.s12_tree_item_context", s12_tree_item_context),
        ("F_0025.s13_tree_item_icon", s13_tree_item_icon),
        ("F_0025.s14_intern_order", s14_intern_order),
        ("F_0025.s15_offline_group_collapsed", s15_offline_group_collapsed),
        ("F_0025.s16_intern_description", s16_intern_description),
        ("F_0025.s17_intern_tooltip", s17_intern_tooltip),
        ("F_0025.s18_open_chat_context_command", s18_open_chat_context_command),
        ("F_0025.s19_gui_command_args_project_scoped", s19_gui_command_args_project_scoped),
        ("F_0025.s20_cli_list_interns", s20_cli_list_interns),
        ("F_0025.s21_tree_matches_cli_interns", s21_tree_matches_cli_interns),
        ("F_0025.s22_no_team_or_non_codex_fixture", s22_no_team_or_non_codex_fixture),
        ("F_0025.s23_product_bug_aggregate", s23_product_bug_aggregate),
    ])
