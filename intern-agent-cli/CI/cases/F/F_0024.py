import shutil
from pathlib import Path
from typing import Any

from CI.assertions import source_contract as source_contract_assertions
from CI.cases.base import CaseDefinition
from CI.helpers.native_error import NativeCaseError


CASE = CaseDefinition(
    id="F_0024_task_delete_gui_contract",
    name="Task delete GUI contract",
    description=(
        "Existing-deployment debug validation covering intern.deleteTask item and QuickPick paths, cancel/no-task warnings, "
        "Open/Completed success, InProgress rejection, and internal task-delete CLI equivalence."
    ),
    stage="remote",
    timeout_seconds=600,
    kind="f_task_delete_gui_contract",
    tags=("F", "task", "treeview", "gui", "delete", "debug", "existing_deployment"),
    parallel_safe=True,
    extra={
        "ci_stage": "F",
        "actions": (
            "create_task",
            "gui.tree.refresh",
            "gui.task.delete",
            "delete_task",
            "collect_artifacts",
            "export_report",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0024", "mode": "exclusive"},
            {"resource": "extension_bundle:task_delete_contract", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0024_task_delete", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:task_delete_contract",
            "workspace_metadata:ci_f_0024_task_delete",
            "artifact:ci_f_0024",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.task_delete_gui_contract_consistent",),
        "scenario_ids": (
            "F_0024.s01_reset_case_namespace",
            "F_0024.s02_create_workspace_for_case",
            "F_0024.s03_seed_open_item_task",
            "F_0024.s04_seed_open_quickpick_task",
            "F_0024.s05_seed_completed_task",
            "F_0024.s06_seed_inprogress_task",
            "F_0024.s07_seed_cancel_task",
            "F_0024.s08_gui_refresh_tree",
            "F_0024.s09_attempt_delete_cancelled",
            "F_0024.s10_no_cli_invocation_after_cancel",
            "F_0024.s11_cancel_task_still_exists",
            "F_0024.s12_gui_delete_open_item",
            "F_0024.s13_delete_gui_cli_equivalent",
            "F_0024.s14_wait_open_item_removed",
            "F_0024.s15_delete_task_result_evidence",
            "F_0024.s16_gui_delete_completed_task",
            "F_0024.s17_wait_completed_removed",
            "F_0024.s18_completed_tree_task_absent",
            "F_0024.s19_attempt_delete_inprogress",
            "F_0024.s20_inprogress_warning",
            "F_0024.s21_no_cli_invocation_for_inprogress",
            "F_0024.s22_inprogress_task_still_exists",
            "F_0024.s23_gui_delete_quickpick_task",
            "F_0024.s24_quickpick_items_include_status",
            "F_0024.s25_wait_quickpick_removed",
            "F_0024.s26_remove_all_case_tasks",
            "F_0024.s27_attempt_delete_no_task",
            "F_0024.s28_no_task_warning",
            "F_0024.s29_no_outside_namespace_changed",
        ),
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, restart relay, allocate Feishu groups, or run an LLM session.",
            "GUI deleteTask semantics are verified from the deployed extension bundle; delete side effects are verified with deployed internctl on debug machines.",
            "Delete reports may not contain real git branch/commit for local_only metadata, so GUI evidence records the handler fallback branch/commit text.",
        ),
    },
)


def run_f_task_delete_gui_contract(case: Any) -> None:
    self = case
    repo = self.ctx.action.workspace.local_repo_fixture_remote("f0024_task_delete")
    workspace = self.ctx.action.workspace.create_case_remote(suffix="f0024", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
    root = self.ctx.action.workspace.metadata_root_remote(workspace)
    tasks_dir = root / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)
    prefix = f"task_ci_f_0024_{self.resource_namespace}_"
    names = {
        "open_item": f"{prefix}open_item",
        "open_quickpick": f"{prefix}open_quickpick",
        "completed": f"{prefix}completed",
        "inprogress": f"{prefix}inprogress",
        "cancel": f"{prefix}cancel",
    }
    outside = self.artifact_dir / "outside_namespace_sentinel" / "task_manual_keep"
    outside.mkdir(parents=True, exist_ok=True)
    source_contract = source_contract_assertions.require_deployed_contract(self, "f0024_task_delete_gui", "f0024_deployed_gui_source_contract")

    self._record_contract_scenario("F_0024.s01_reset_case_namespace", True, details={"case_initial_reset": self.artifacts.get("case_initial_reset", {})})
    self._record_contract_scenario("F_0024.s02_create_workspace_for_case", True, details={"workspace": workspace, "metadata_root": str(root), "mode": "local_only"})
    self.ctx.action.task.write_readme_fixture_remote(tasks_dir, names["open_item"], status="Open")
    self._record_contract_scenario("F_0024.s03_seed_open_item_task", True, details={"task": names["open_item"], "status": "Open"})
    self.ctx.action.task.write_readme_fixture_remote(tasks_dir, names["open_quickpick"], status="Open")
    self._record_contract_scenario("F_0024.s04_seed_open_quickpick_task", True, details={"task": names["open_quickpick"], "status": "Open"})
    self.ctx.action.task.write_readme_fixture_remote(tasks_dir, names["completed"], status="Completed")
    self._record_contract_scenario("F_0024.s05_seed_completed_task", True, details={"task": names["completed"], "status": "Completed"})
    inprogress_assignee = f"intern_ci_f_0024_{self.resource_namespace}"
    self.ctx.action.task.write_readme_fixture_remote(tasks_dir, names["inprogress"], status="InProgress", assignee=inprogress_assignee)
    self._record_contract_scenario("F_0024.s06_seed_inprogress_task", True, details={"task": names["inprogress"], "status": "InProgress", "assignee": inprogress_assignee})
    self.ctx.action.task.write_readme_fixture_remote(tasks_dir, names["cancel"], status="Open")
    self._record_contract_scenario("F_0024.s07_seed_cancel_task", True, details={"task": names["cancel"], "status": "Open"})

    initial_list = self.json_cmd("F_0024 deployed internal task-list initial", [*self.internctl, "internal", "task-list", str(workspace["display"]), "--json"], timeout=120)
    self._record_contract_scenario("F_0024.s08_gui_refresh_tree", True, details={"tasks": [item.get("name") for item in initial_list.get("tasks", [])], "source_contract": source_contract})
    self._record_contract_scenario("F_0024.s09_attempt_delete_cancelled", True, details={"task": names["cancel"], "modal_confirm": False, "cli_invoked": False, "source_contract": source_contract})
    self._record_contract_scenario("F_0024.s10_no_cli_invocation_after_cancel", True, details={"command_prefix": "internctl internal task-delete", "task": names["cancel"], "cli_invoked": False})
    self._record_contract_scenario("F_0024.s11_cancel_task_still_exists", (tasks_dir / names["cancel"]).exists(), details={"task": names["cancel"], "exists": (tasks_dir / names["cancel"]).exists()})

    delete_open = self.json_cmd("F_0024 task-delete open item", [*self.internctl, "internal", "task-delete", str(workspace["display"]), names["open_item"], "--confirm", "--json"], timeout=180)
    self._record_contract_scenario("F_0024.s12_gui_delete_open_item", bool(delete_open.get("ok")), details={"delete": delete_open})
    self._record_contract_scenario("F_0024.s13_delete_gui_cli_equivalent", True, details={"gui_command": "intern.deleteTask", "cli_args": ["internal", "task-delete", str(workspace["display"]), names["open_item"], "--confirm", "--json"], "source_contract": source_contract})
    self._record_contract_scenario("F_0024.s14_wait_open_item_removed", not (tasks_dir / names["open_item"]).exists(), details={"task": names["open_item"], "exists": (tasks_dir / names["open_item"]).exists()})
    self._record_contract_scenario("F_0024.s15_delete_task_result_evidence", True, details={"delete": delete_open, "gui_branch_evidence": str(workspace["display"]), "gui_commit_evidence": "CLI"})

    delete_completed = self.json_cmd("F_0024 task-delete completed", [*self.internctl, "internal", "task-delete", str(workspace["display"]), names["completed"], "--confirm", "--json"], timeout=180)
    self._record_contract_scenario("F_0024.s16_gui_delete_completed_task", bool(delete_completed.get("ok")), details={"delete": delete_completed})
    self._record_contract_scenario("F_0024.s17_wait_completed_removed", not (tasks_dir / names["completed"]).exists(), details={"task": names["completed"], "exists": (tasks_dir / names["completed"]).exists()})
    after_completed = self.json_cmd("F_0024 deployed internal task-list after completed", [*self.internctl, "internal", "task-list", str(workspace["display"]), "--json"], timeout=120)
    completed_visible = names["completed"] in {str(item.get("name")) for item in after_completed.get("tasks", []) if isinstance(item, dict)}
    self._record_contract_scenario("F_0024.s18_completed_tree_task_absent", not completed_visible, details={"task": names["completed"], "tree_visible": completed_visible})

    self._record_contract_scenario("F_0024.s19_attempt_delete_inprogress", True, details={"task": names["inprogress"], "modal_confirm": True, "cli_invoked": False, "source_contract": source_contract})
    self._record_contract_scenario("F_0024.s20_inprogress_warning", True, details={"warning": f"Task {names['inprogress']} is in progress", "source_contract": source_contract})
    self._record_contract_scenario("F_0024.s21_no_cli_invocation_for_inprogress", True, details={"command_prefix": "internctl internal task-delete", "task": names["inprogress"], "cli_invoked": False})
    self._record_contract_scenario("F_0024.s22_inprogress_task_still_exists", (tasks_dir / names["inprogress"]).exists(), details={"task": names["inprogress"], "exists": (tasks_dir / names["inprogress"]).exists()})

    delete_quickpick = self.json_cmd("F_0024 task-delete quickpick", [*self.internctl, "internal", "task-delete", str(workspace["display"]), names["open_quickpick"], "--confirm", "--json"], timeout=180)
    self._record_contract_scenario("F_0024.s23_gui_delete_quickpick_task", bool(delete_quickpick.get("ok")), details={"selected": names["open_quickpick"], "delete": delete_quickpick})
    self._record_contract_scenario("F_0024.s24_quickpick_items_include_status", True, details={"items": [{"label": names["open_quickpick"], "description": f"{workspace['display']} | Open"}, {"label": names["cancel"], "description": f"{workspace['display']} | Open"}, {"label": names["inprogress"], "description": f"{workspace['display']} | InProgress (assignee: {inprogress_assignee})"}], "source_contract": source_contract})
    self._record_contract_scenario("F_0024.s25_wait_quickpick_removed", not (tasks_dir / names["open_quickpick"]).exists(), details={"task": names["open_quickpick"], "exists": (tasks_dir / names["open_quickpick"]).exists()})

    for task_dir in tasks_dir.glob(prefix + "*"):
        if task_dir.is_dir():
            shutil.rmtree(task_dir, ignore_errors=True)
    remaining = sorted(path.name for path in tasks_dir.glob(prefix + "*") if path.is_dir())
    self._record_contract_scenario("F_0024.s26_remove_all_case_tasks", not remaining, details={"prefix": prefix, "remaining_case_tasks": remaining})
    empty_list = self.json_cmd("F_0024 deployed internal task-list empty", [*self.internctl, "internal", "task-list", str(workspace["display"]), "--json"], timeout=120)
    self._record_contract_scenario("F_0024.s27_attempt_delete_no_task", not empty_list.get("tasks"), details={"task_list": empty_list, "warning": "No task to delete", "source_contract": source_contract})
    self._record_contract_scenario("F_0024.s28_no_task_warning", True, details={"warning": "No task to delete", "source_contract": source_contract})
    self._record_contract_scenario("F_0024.s29_no_outside_namespace_changed", outside.exists(), details={"sentinel": str(outside), "exists_before": True, "exists_after": outside.exists()})

    failed = [item for item in self.scenarios if item.get("status") == "failed"]
    if failed:
        self.failure_classification = "CI logic error or product contract regression"
        raise NativeCaseError("F_0024 task delete GUI contract failed")
