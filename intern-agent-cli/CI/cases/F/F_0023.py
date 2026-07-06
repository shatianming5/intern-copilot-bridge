from pathlib import Path
from typing import Any

from CI.assertions import source_contract as source_contract_assertions
from CI.cases.base import CaseDefinition
from CI.helpers.native_error import NativeCaseError


CASE = CaseDefinition(
    id="F_0023_task_treeview_projection_contract",
    name="Task TreeView projection contract",
    description=(
        "Existing-deployment debug validation covering task TreeView grouping, README open behavior, "
        "tooltip/raw PR, InProgress PR description formatting, and line-3 metadata parser boundary evidence."
    ),
    stage="remote",
    timeout_seconds=600,
    kind="f_task_treeview_projection_contract",
    tags=("F", "task", "treeview", "gui", "debug", "existing_deployment"),
    parallel_safe=True,
    extra={
        "ci_stage": "F",
        "actions": (
            "create_task",
            "assign_task",
            "gui.tree.refresh",
            "collect_artifacts",
            "export_report",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0023", "mode": "exclusive"},
            {"resource": "extension_bundle:task_treeview_contract", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0023_task_treeview", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:task_treeview_contract",
            "workspace_metadata:ci_f_0023_task_treeview",
            "artifact:ci_f_0023",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.task_treeview_projection_consistent",),
        "scenario_ids": (
            "F_0023.s01_reset_case_namespace",
            "F_0023.s02_create_workspace_for_case",
            "F_0023.s03_seed_inprogress_task_readme",
            "F_0023.s04_seed_codex_intern_status",
            "F_0023.s05_seed_open_unassigned_task",
            "F_0023.s06_seed_completed_task",
            "F_0023.s07_seed_task_without_readme",
            "F_0023.s08_seed_malformed_task_readme",
            "F_0023.s09_gui_refresh_tree",
            "F_0023.s10_task_group_order",
            "F_0023.s11_inprogress_group_count",
            "F_0023.s12_open_group_count_malformed_excluded",
            "F_0023.s13_completed_group_count_collapsed",
            "F_0023.s14_inprogress_task_description_pr",
            "F_0023.s15_inprogress_task_tooltip_raw_pr",
            "F_0023.s16_open_task_tooltip_unassigned_no_pr",
            "F_0023.s17_completed_task_tooltip_no_pr",
            "F_0023.s18_gui_open_existing_task_readme",
            "F_0023.s19_vscode_open_called_for_readme",
            "F_0023.s20_gui_open_missing_task_readme",
            "F_0023.s21_no_vscode_open_for_missing_readme",
            "F_0023.s22_no_gui_exception_for_missing_readme",
            "F_0023.s23_metadata_line3_contract",
            "F_0023.s24_internal_task_list_consistent",
        ),
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, restart relay, allocate Feishu groups, or run an LLM session.",
            "GUI modal/TreeView semantics are verified from the deployed extension bundle; task projection side effects are verified with deployed internctl on debug machines.",
            "If line-4 README METADATA appears in deployed internal task-list, the case reports Product/parser contract bug evidence.",
        ),
    },
)


def run_f_task_treeview_projection_contract(case: Any) -> None:
    self = case
    repo = self.ctx.action.workspace.local_repo_fixture_remote("f0023_task_treeview")
    workspace = self.ctx.action.workspace.create_case_remote(suffix="f0023", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
    root = self.ctx.action.workspace.metadata_root_remote(workspace)
    tasks_dir = root / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"task_ci_f_0023_{self.resource_namespace}_"
    names = {
        "inprogress": f"{prefix}inprogress",
        "open": f"{prefix}open",
        "completed": f"{prefix}completed",
        "missing": f"{prefix}missing_readme",
        "malformed": f"{prefix}malformed",
    }
    pr_url = "https://codeup.aliyun.com/org/repo/change/123"

    def task_groups(tasks: list[dict[str, Any]]) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for task in tasks:
            groups.setdefault(str(task.get("status") or "Open"), []).append(str(task.get("name") or ""))
        for names_for_status in groups.values():
            names_for_status.sort()
        return groups

    def format_pr_description(pr: str) -> str:
        import re

        match = re.search(r"/(?:pull|change|merge_requests)/(\d+)", pr)
        if match:
            return f"PR#{match.group(1)}"
        match = re.fullmatch(r"#?(\d+)", pr)
        if match:
            return f"PR#{match.group(1)}"
        return pr

    def task_tooltip(task: dict[str, Any]) -> str:
        assignee = str(task.get("assignee") or "unassigned")
        tooltip = f"{task.get('name')}\nStatus: {task.get('status')}\nAssignee: {assignee}"
        if task.get("pr"):
            tooltip += f"\nPR: {task.get('pr')}"
        return tooltip

    self._record_contract_scenario("F_0023.s01_reset_case_namespace", True, details={"case_initial_reset": self.artifacts.get("case_initial_reset", {})})
    self._record_contract_scenario("F_0023.s02_create_workspace_for_case", True, details={"workspace": workspace, "metadata_root": str(root), "mode": "local_only"})
    self.ctx.action.task.write_readme_fixture_remote(tasks_dir, names["inprogress"], status="Open")
    self._record_contract_scenario("F_0023.s03_seed_inprogress_task_readme", True, details={"task": names["inprogress"], "status": "Open", "metadata_line": 3})
    intern = self.ctx.action.intern.create_case_remote(workspace, "f0023_worker", repo_url=str(repo))
    intern_status = self.ctx.action.task.seed_treeview_intern_status_remote(workspace, intern=intern, task=names["inprogress"], pr=pr_url)
    self._record_contract_scenario("F_0023.s04_seed_codex_intern_status", True, details={"intern": intern, "task": names["inprogress"], "status": "Working", "pr": pr_url, **intern_status})
    self.ctx.action.task.write_readme_fixture_remote(tasks_dir, names["open"], status="Open")
    self._record_contract_scenario("F_0023.s05_seed_open_unassigned_task", True, details={"task": names["open"], "status": "Open", "assignee": ""})
    self.ctx.action.task.write_readme_fixture_remote(tasks_dir, names["completed"], status="Completed", assignee=f"{intern}_done")
    self._record_contract_scenario("F_0023.s06_seed_completed_task", True, details={"task": names["completed"], "status": "Completed", "assignee": f"{intern}_done"})
    (tasks_dir / names["missing"]).mkdir(parents=True, exist_ok=True)
    self._record_contract_scenario("F_0023.s07_seed_task_without_readme", True, details={"task": names["missing"], "readme_exists": False})
    self.ctx.action.task.write_readme_fixture_remote(tasks_dir, names["malformed"], status="Open", assignee="bad", metadata_line=4)
    self._record_contract_scenario("F_0023.s08_seed_malformed_task_readme", True, details={"task": names["malformed"], "metadata_line": 4, "expected_visible": False})

    source_contract = source_contract_assertions.require_deployed_contract(self, "f0023_task_treeview_projection", "f0023_deployed_gui_source_contract")
    task_list = self.json_cmd("F_0023 deployed internal task-list", [*self.internctl, "internal", "task-list", str(workspace["display"]), "--json"], timeout=120)
    tasks = [item for item in task_list.get("tasks", []) if isinstance(item, dict)]
    tasks_by_name = {str(item.get("name")): item for item in tasks}
    groups = task_groups(tasks)
    group_order = [status for status in ("InProgress", "Open", "Completed") if groups.get(status)]
    malformed_visible = names["malformed"] in tasks_by_name
    expected_visible = {names["inprogress"], names["open"], names["completed"], names["missing"]}
    actual_visible = set(tasks_by_name)
    inprogress = tasks_by_name.get(names["inprogress"], {})
    open_task = tasks_by_name.get(names["open"], {})
    completed = tasks_by_name.get(names["completed"], {})
    missing = tasks_by_name.get(names["missing"], {})
    inprogress_readme = Path(str(inprogress.get("readme_path") or ""))
    missing_readme = Path(str(missing.get("readme_path") or ""))
    inprogress_description = format_pr_description(str(inprogress.get("pr") or ""))
    inprogress_tooltip = task_tooltip(inprogress)
    open_tooltip = task_tooltip(open_task)
    completed_tooltip = task_tooltip(completed)
    parser_bug = "Product/parser contract bug"

    self._record_contract_scenario("F_0023.s09_gui_refresh_tree", True, details={"task_list": task_list, "source_contract": source_contract})
    self._record_contract_scenario("F_0023.s10_task_group_order", group_order == ["InProgress", "Open", "Completed"], details={"expected": ["InProgress", "Open", "Completed"], "actual": group_order, "groups": groups}, failure_reason="Task group order is not stable")
    self._record_contract_scenario("F_0023.s11_inprogress_group_count", len(groups.get("InProgress", [])) == 1, details={"expected": 1, "actual": len(groups.get("InProgress", [])), "tasks": groups.get("InProgress", [])}, failure_reason="InProgress group count mismatch")
    self._record_contract_scenario("F_0023.s12_open_group_count_malformed_excluded", len(groups.get("Open", [])) == 2 and not malformed_visible, details={"expected": 2, "actual": len(groups.get("Open", [])), "tasks": groups.get("Open", []), "malformed_visible": malformed_visible}, failure_reason="Malformed line-4 METADATA task was counted as normal Open task", classification=parser_bug if malformed_visible else "")
    self._record_contract_scenario("F_0023.s13_completed_group_count_collapsed", len(groups.get("Completed", [])) == 1, details={"expected": 1, "actual": len(groups.get("Completed", [])), "collapsed_by_default": True}, failure_reason="Completed group count or collapse contract mismatch")
    self._record_contract_scenario("F_0023.s14_inprogress_task_description_pr", inprogress_description == "PR#123", details={"expected": "PR#123", "actual": inprogress_description, "raw_pr": inprogress.get("pr"), "source_contract": source_contract}, failure_reason="InProgress task description did not format PR as PR#N")
    self._record_contract_scenario("F_0023.s15_inprogress_task_tooltip_raw_pr", "InProgress" in inprogress_tooltip and intern in inprogress_tooltip and pr_url in inprogress_tooltip, details={"tooltip": inprogress_tooltip, "expected_raw_pr": pr_url, "source_contract": source_contract}, failure_reason="InProgress tooltip did not keep status/assignee/raw PR evidence")
    self._record_contract_scenario("F_0023.s16_open_task_tooltip_unassigned_no_pr", "Open" in open_tooltip and "unassigned" in open_tooltip and "PR:" not in open_tooltip, details={"tooltip": open_tooltip, "task": open_task}, failure_reason="Open task tooltip should be unassigned and PR-free")
    self._record_contract_scenario("F_0023.s17_completed_task_tooltip_no_pr", "Completed" in completed_tooltip and f"{intern}_done" in completed_tooltip and "PR:" not in completed_tooltip, details={"tooltip": completed_tooltip, "task": completed}, failure_reason="Completed task tooltip should not invent PR")
    self._record_contract_scenario("F_0023.s18_gui_open_existing_task_readme", inprogress_readme.exists(), details={"task": names["inprogress"], "readme_path": str(inprogress_readme), "exists": inprogress_readme.exists()}, failure_reason="Existing task README path is not available for vscode.open")
    self._record_contract_scenario("F_0023.s19_vscode_open_called_for_readme", str(inprogress_readme).endswith(f"tasks/{names['inprogress']}/README.md"), details={"command": "vscode.open", "path": str(inprogress_readme), "source_contract": source_contract}, failure_reason="Task node did not map to vscode.open README path")
    self._record_contract_scenario("F_0023.s20_gui_open_missing_task_readme", names["missing"] in tasks_by_name, details={"task": names["missing"], "readme_path": str(missing_readme), "exists": missing_readme.exists()}, failure_reason="Missing README fixture was not visible as an Open task")
    self._record_contract_scenario("F_0023.s21_no_vscode_open_for_missing_readme", not missing_readme.exists(), details={"task": names["missing"], "command": None, "source_contract": source_contract}, failure_reason="Missing README task should not map to vscode.open")
    self._record_contract_scenario("F_0023.s22_no_gui_exception_for_missing_readme", names["missing"] in tasks_by_name, details={"task": names["missing"], "task_list_ok": True, "source_contract": source_contract}, failure_reason="Missing README task caused task-list or source-contract failure")
    self._record_contract_scenario("F_0023.s23_metadata_line3_contract", not malformed_visible, details={"task_name": names["malformed"], "expected_visible": False, "actual_visible": malformed_visible, "task": tasks_by_name.get(names["malformed"], {})}, failure_reason="README METADATA on line 4 was accepted as valid task metadata", classification=parser_bug if malformed_visible else "")
    extra_visible = sorted(actual_visible - expected_visible)
    self._record_contract_scenario("F_0023.s24_internal_task_list_consistent", not extra_visible, details={"expected_visible_tasks": sorted(expected_visible), "actual_visible_tasks": sorted(actual_visible), "extra_visible_tasks": extra_visible}, failure_reason="internal task-list exposed tasks outside the well-formed visible task set", classification=parser_bug if extra_visible else "")

    failed = [item for item in self.scenarios if item.get("status") == "failed"]
    if failed:
        if all((item.get("details") or {}).get("failure_classification") == parser_bug for item in failed):
            self.failure_classification = parser_bug
            self.artifacts["failure_classification"] = parser_bug
        raise NativeCaseError("F_0023 task TreeView projection contract failed")
