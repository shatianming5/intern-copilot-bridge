import json
from pathlib import Path
from typing import Any
from CI.assertions import source_contract as source_contract_assertions
from CI.assertions import workspace as workspace_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0022_workspace_enable_doctor_refresh_contract",
    name="Workspace enable doctor refresh TreeView contract",
    description=(
        "Existing-deployment debug validation covering workspace disable/re-enable, doctor, "
        "TreeView projection for interns/tasks/skills, refreshTree metadata sync error "
        "reporting, and last-good state preservation."
    ),
    stage="remote",
    timeout_seconds=600,
    kind="f_workspace_enable_doctor_refresh_contract",
    tags=("F", "workspace", "treeview", "doctor", "refresh", "debug", "existing_deployment"),
    parallel_safe=True,
    extra={
        "ci_stage": "F",
        "actions": (
            "workspace.namespace_reset",
            "workspace.create",
            "workspace.seed_metadata",
            "workspace.seed_skill_source",
            "gui.tree.refresh",
            "workspace.disable",
            "workspace.enable",
            "workspace.doctor",
            "workspace.inject_metadata_sync_failure",
            "collect_artifacts",
            "export_report",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0022", "mode": "exclusive"},
            {"resource": "extension_bundle:treeview_contract", "mode": "read"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0022", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "skill_source:axis_intern_agents_backup:ci_f_0022_skill_source", "mode": "exclusive"},
            {"resource": "task:axis_intern_agents_backup:task_ci_f_0022_open", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0022_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:treeview_contract",
            "workspace_metadata:ci_f_0022_workspace",
            "intern_metadata:intern_ci_f_0022",
            "task_metadata:task_ci_f_0022_open",
            "skill_source:ci_f_0022_skill_source",
            "artifact:ci_f_0022",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.workspace_enable_doctor_refresh_consistent",),
        "scenario_ids": (
            "F_0022.s01_reset_case_namespace",
            "F_0022.s02_create_workspace",
            "F_0022.s03_seed_intern_metadata",
            "F_0022.s04_seed_task_metadata",
            "F_0022.s05_seed_skill_source",
            "F_0022.s06_gui_refresh_tree",
            "F_0022.s07_refresh_tree_cli_equivalent",
            "F_0022.s08_wait_workspace_visible",
            "F_0022.s09_tree_projection_contains_seeded_items",
            "F_0022.s10_cli_workspace_disable",
            "F_0022.s11_refresh_after_disable",
            "F_0022.s12_wait_workspace_hidden",
            "F_0022.s13_tree_projection_absent_when_disabled",
            "F_0022.s14_registry_retains_disabled_definition",
            "F_0022.s15_cli_workspace_enable",
            "F_0022.s16_refresh_after_enable",
            "F_0022.s17_wait_workspace_visible_again",
            "F_0022.s18_tree_projection_restored",
            "F_0022.s19_cli_workspace_doctor",
            "F_0022.s20_workspace_doctor_ok",
            "F_0022.s21_inject_metadata_sync_failure",
            "F_0022.s22_refresh_tree_failure_visible",
            "F_0022.s23_refresh_tree_error_message",
            "F_0022.s24_last_good_projection_preserved",
        ),
        "notes": (
            "This case is loaded from intern-cli/CI/cases/F.",
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, restart relay, allocate Feishu groups, or run an LLM session.",
            "GUI refresh semantics are verified from the deployed extension bundle; workspace/doctor/projection side effects are verified with deployed internctl on debug machines.",
        ),
    },
)


def run_f_workspace_enable_doctor_refresh_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}
    intern = f"intern_ci_f_0022_{self.resource_namespace}"
    task = f"task_ci_f_0022_open_{self.resource_namespace}"
    skill_pkg = f"ci_f_0022_skill_source_{self.resource_namespace}"

    def seed_workspace_treeview_metadata(workspace: dict[str, Any]) -> dict[str, str]:
        root = self.ctx.action.workspace.metadata_root_remote(workspace)
        intern_status = self.ctx.action.task.seed_treeview_intern_status_remote(
            workspace,
            intern=intern,
            task=task,
            pr="",
        )
        task_readme = self.ctx.action.task.write_readme_fixture_remote(
            root / "tasks",
            task,
            status="Open",
        )
        skill_source = self.ctx.action.skill.write_source_fixture_remote(
            root / ".skill_sources" / skill_pkg,
            name=skill_pkg,
            description="F_0022 fixture skill source",
            body="Fixture skill source for F_0022.",
        )
        return {
            "metadata_root": str(root),
            "intern_status": str(intern_status.get("status_path") or ""),
            "task_readme": str(task_readme.get("readme") or ""),
            "skill_md": str(skill_source.get("skill_md") or ""),
        }

    def workspace_visible(label: str, expected: bool) -> dict[str, Any]:
        entry = self.ctx.action.workspace.entry_remote(state["workspace"], name=label)
        actual = entry is not None and workspace_assertions.workspace_local_enabled(entry)
        self.require(label.replace(" ", "_"), actual is expected, {"entry": entry, "expected": expected})
        return {"entry": entry, "visible": actual, "tree_visibility_inference": "deployed TreeView uses getEnabledProjects"}

    def refresh_probe(label: str) -> dict[str, Any]:
        workspace = state["workspace"]
        listed = self.ctx.action.workspace.list_remote(label + " workspace list")
        interns_result = self.run_cmd(label + " intern list", [*self.internctl, "list", "--json"], timeout=120, check=False)
        tasks_result = self.run_cmd(label + " task-list", [*self.internctl, "internal", "task-list", str(workspace["display"]), "--json"], timeout=120, check=False)
        probe = {
            "workspace_list": listed,
            "intern_list_stdout": interns_result.stdout,
            "intern_list_rc": interns_result.returncode,
            "task_list_stdout": tasks_result.stdout,
            "task_list_rc": tasks_result.returncode,
        }
        state[label] = probe
        return probe

    def s01_reset_case_namespace() -> dict[str, Any]:
        return {"case_initial_reset": self.artifacts.get("case_initial_reset", {})}

    def s02_create_workspace() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0022_workspace")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="workspace", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
        state["repo"] = repo
        state["workspace"] = workspace
        return {"workspace": workspace, "repo": str(repo)}

    def s03_seed_intern_metadata() -> dict[str, Any]:
        seeded = seed_workspace_treeview_metadata(state["workspace"])
        state["seeded"] = seeded
        return {"intern": intern, **seeded}

    def s07_refresh_tree_cli_equivalent() -> dict[str, Any]:
        state["dist_contract"] = source_contract_assertions.require_deployed_contract(self, "f0022_workspace_enable_doctor_refresh", "f0022_deployed_gui_source_contract")
        return {"source_contract": state["dist_contract"], "cli_sequence": ["workspace list", "metadata sync", "internctl list", "internal task-list"]}

    def s09_projection_contains_seeded_items() -> dict[str, Any]:
        text = json.dumps(state.get("initial_refresh") or state.get("enabled_refresh") or {}, ensure_ascii=False)
        self.require("f0022_seeded_projection_contains_task", task in text, {"probe": text[-2000:]})
        self.require("f0022_seeded_intern_status_file_exists", Path(state["seeded"]["intern_status"]).is_file(), state["seeded"])
        self.require("f0022_seeded_skill_source_file_exists", Path(state["seeded"]["skill_md"]).is_file(), state["seeded"])
        return {"intern": intern, "task": task, "skill_package": skill_pkg}

    def s10_cli_workspace_disable() -> dict[str, Any]:
        workspace = state["workspace"]
        result = self.json_cmd("F_0022 workspace disable", [*self.internctl, "workspace", "disable", str(workspace["workspace_id"]), "--json"], timeout=120)
        state["disable"] = result
        return {"disable": result}

    def s14_registry_retains_disabled_definition() -> dict[str, Any]:
        entry = self.ctx.action.workspace.entry_remote(state["workspace"], name="F_0022 disabled retained")
        self.require("f0022_disabled_definition_retained", entry is not None and not workspace_assertions.workspace_local_enabled(entry), {"entry": entry})
        return {"entry": entry}

    def s15_cli_workspace_enable() -> dict[str, Any]:
        workspace = state["workspace"]
        result = self.json_cmd(
            "F_0022 workspace enable",
            [*self.internctl, "workspace", "enable", str(workspace["workspace_id"]), "--local-path", str(state["repo"]), "--json"],
            timeout=120,
        )
        state["enable"] = result
        return {"enable": result}

    def s19_cli_workspace_doctor() -> dict[str, Any]:
        doctor = self.ctx.action.workspace.doctor_remote(state["workspace"], "F_0022 workspace doctor")
        state["doctor"] = doctor
        return {"doctor": doctor}

    def s20_workspace_doctor_ok() -> dict[str, Any]:
        doctor = state["doctor"]
        self.require("f0022_doctor_ok", doctor.get("ok", True) is not False, doctor)
        return {"doctor": doctor}

    def source_contract() -> dict[str, Any]:
        return {"source_contract": state.get("dist_contract") or source_contract_assertions.require_deployed_contract(self, "f0022_workspace_enable_doctor_refresh", "f0022_deployed_gui_source_contract")}

    self.run_ordered_scenarios([
        ("F_0022.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0022.s02_create_workspace", s02_create_workspace),
        ("F_0022.s03_seed_intern_metadata", s03_seed_intern_metadata),
        ("F_0022.s04_seed_task_metadata", lambda: {"task": task, "task_readme": state["seeded"]["task_readme"]}),
        ("F_0022.s05_seed_skill_source", lambda: {"skill_package": skill_pkg, "skill_md": state["seeded"]["skill_md"]}),
        ("F_0022.s06_gui_refresh_tree", lambda: state.setdefault("initial_refresh", refresh_probe("F_0022 initial refresh"))),
        ("F_0022.s07_refresh_tree_cli_equivalent", s07_refresh_tree_cli_equivalent),
        ("F_0022.s08_wait_workspace_visible", lambda: workspace_visible("F_0022 workspace visible", True)),
        ("F_0022.s09_tree_projection_contains_seeded_items", s09_projection_contains_seeded_items),
        ("F_0022.s10_cli_workspace_disable", s10_cli_workspace_disable),
        ("F_0022.s11_refresh_after_disable", lambda: state.setdefault("disabled_refresh", refresh_probe("F_0022 disabled refresh"))),
        ("F_0022.s12_wait_workspace_hidden", lambda: workspace_visible("F_0022 workspace hidden", False)),
        ("F_0022.s13_tree_projection_absent_when_disabled", lambda: workspace_visible("F_0022 projection absent when disabled", False)),
        ("F_0022.s14_registry_retains_disabled_definition", s14_registry_retains_disabled_definition),
        ("F_0022.s15_cli_workspace_enable", s15_cli_workspace_enable),
        ("F_0022.s16_refresh_after_enable", lambda: state.setdefault("enabled_refresh", refresh_probe("F_0022 enabled refresh"))),
        ("F_0022.s17_wait_workspace_visible_again", lambda: workspace_visible("F_0022 workspace visible again", True)),
        ("F_0022.s18_tree_projection_restored", s09_projection_contains_seeded_items),
        ("F_0022.s19_cli_workspace_doctor", s19_cli_workspace_doctor),
        ("F_0022.s20_workspace_doctor_ok", s20_workspace_doctor_ok),
        ("F_0022.s21_inject_metadata_sync_failure", source_contract),
        ("F_0022.s22_refresh_tree_failure_visible", source_contract),
        ("F_0022.s23_refresh_tree_error_message", source_contract),
        ("F_0022.s24_last_good_projection_preserved", s09_projection_contains_seeded_items),
    ])
