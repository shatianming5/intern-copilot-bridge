from pathlib import Path
from typing import Any

from CI.assertions import source_contract as source_contract_assertions
from CI.cases.base import CaseDefinition


CASE = CaseDefinition(
    id="F_0030_codex_skill_repo_personal_enable_contract",
    name="Codex skill repo and personal enable contract",
    description=(
        "Existing-deployment debug validation covering Codex-only skill repo/personal enable and disable behavior, "
        "repo-wide farm sync, personal scope isolation, duplicate no-op, promote-personal, and out-of-scope guards."
    ),
    stage="remote",
    timeout_seconds=600,
    kind="f_codex_skill_repo_personal_enable_contract",
    tags=("F", "skill", "codex", "treeview", "gui", "debug", "existing_deployment"),
    parallel_safe=True,
    extra={
        "ci_stage": "F",
        "actions": (
            "cli.internctl",
            "gui.tree.refresh",
            "gui.skill.tree_enable_repo",
            "gui.skill.tree_enable_personal",
            "gui.skill.tree_disable_repo",
            "gui.skill.tree_disable_personal",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0030", "mode": "exclusive"},
            {"resource": "extension_bundle:codex_skill_scope_contract", "mode": "read"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0030_a", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0030_b", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "skill_source:axis_intern_agents_backup:ci_f_0030_pkg", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0030_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:codex_skill_scope_contract",
            "workspace_metadata:ci_f_0030_workspace",
            "intern:intern_ci_f_0030_a",
            "intern:intern_ci_f_0030_b",
            "skill_source:ci_f_0030_pkg",
            "artifact:ci_f_0030",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.codex_skill_scope_sync_consistent",),
        "scenario_ids": (
            "F_0030.s01_repo_enable_confirm_syncs_all_codex",
            "F_0030.s02_repo_enabled_personal_reject_contract",
            "F_0030.s03_repo_disable_clears_codex_farms",
            "F_0030.s04_personal_enable_boundary_and_duplicate_noop",
            "F_0030.s05_promote_personal_to_repo",
            "F_0030.s06_no_non_codex_or_copilot_shared_state",
        ),
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, restart relay, allocate Feishu groups, or run an LLM session.",
            "Uses two Codex interns only; no Claude/Copilot intern or Copilot shared state is created.",
            "GUI confirmations/rejections are verified from the deployed extension bundle; state transitions use deployed internctl skill CLI handlers.",
            "No Feishu group, relay restart, setup webview, or Team command is used.",
        ),
    },
)


def run_f_codex_skill_repo_personal_enable_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}
    package = f"ci_f_0030_pkg_{self.resource_namespace}"
    skill_name = "skill_alpha"
    skill_key = f"{package}/{skill_name}"

    def scope_snapshot() -> dict[str, Any]:
        root: Path = state["root"]
        runtime_a = Path(str(state["intern_a"]["runtime"]))
        runtime_b = Path(str(state["intern_b"]["runtime"]))
        intern_a = state["intern_a"]["intern"]
        intern_b = state["intern_b"]["intern"]
        return {
            "repo": self.ctx.action.skill.read_json_path_remote(root / ".intern_skill.json"),
            "personal_a": self.ctx.action.skill.read_json_path_remote(root / "interns" / intern_a / ".intern_skill.json"),
            "personal_b": self.ctx.action.skill.read_json_path_remote(root / "interns" / intern_b / ".intern_skill.json"),
            "farm_a": list(self.ctx.action.skill.farm_entries_remote(runtime_a).get("entries") or []),
            "farm_b": list(self.ctx.action.skill.farm_entries_remote(runtime_b).get("entries") or []),
        }

    def farm_has_case_skill(snapshot: dict[str, Any], key: str) -> bool:
        return skill_name in (snapshot.get(key) or [])

    def farm_lacks_case_skill(snapshot: dict[str, Any], key: str) -> bool:
        return skill_name not in (snapshot.get(key) or [])

    def s01_repo_enable_confirm_syncs_all_codex() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0030_workspace")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="f0030", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
        root = self.ctx.action.workspace.metadata_root_remote(workspace)
        intern_a = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "f0030_a", repo_url=str(repo)))
        intern_b = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "f0030_b", repo_url=str(repo)))
        source = self.ctx.action.skill.git_source_fixture_remote("f0030_skill_source_git", name=skill_name, description="initial F_0030", rel_dir=skill_name)
        add = self.ctx.action.skill.run_json_remote(
            "F_0030 deployed seed skill source",
            ["add-skill", "--project", str(workspace["display"]), "--scope", "repo", "--source-type", "git", package, source["repo"]],
            timeout=240,
        )
        state.update({"repo": repo, "workspace": workspace, "root": root, "intern_a": intern_a, "intern_b": intern_b, "source": source})
        source_contract = source_contract_assertions.require_deployed_contract(self, "f0030_codex_skill_scope", "f0030_deployed_gui_source_contract")
        before = scope_snapshot()
        enable = self.ctx.action.skill.run_json_remote("F_0030 deployed repo enable", ["enable", "--project", str(workspace["display"]), intern_a["intern"], "repo", skill_key], timeout=180)
        after = scope_snapshot()
        self.require("f0030_repo_enable_changed", enable.get("changed") is True and enable.get("sync", {}).get("ok") is True, {"enable": enable, "before": before, "after": after})
        self.require(
            "f0030_repo_enable_scope_and_farm",
            after["repo"].get("enabled") == [skill_key]
            and after["personal_a"].get("enabled") == []
            and after["personal_b"].get("enabled") == []
            and farm_has_case_skill(after, "farm_a")
            and farm_has_case_skill(after, "farm_b"),
            {"after": after, "case_skill": skill_name},
        )
        return {"workspace": workspace, "package": package, "skill_key": skill_key, "add": add, "before_cancel_scope": before, "enable": enable, "after_scope": after, "source_contract": source_contract}

    def s02_repo_enabled_personal_reject_contract() -> dict[str, Any]:
        source_contract = self.artifacts.get("f0030_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0030_codex_skill_scope", "f0030_deployed_gui_source_contract")
        listed = self.ctx.action.skill.run_json_remote("F_0030 deployed skill list repo enabled", ["list", "--project", str(state["workspace"]["display"]), state["intern_a"]["intern"]], timeout=120)
        pdata = listed.get("projects", {}).get(str(state["workspace"]["display"]), {})
        self.require("f0030_repo_enabled_personal_reject_precondition", pdata.get("repo_enabled") == [skill_key] and pdata.get("personal_enabled") == [], {"list": listed, "source_contract": source_contract})
        return {"reject_contract": source_contract, "list": listed, "expected_gui_behavior": "intern.skill.tree.enablePersonal rejects/no-ops when repoHas is true"}

    def s03_repo_disable_clears_codex_farms() -> dict[str, Any]:
        disable = self.ctx.action.skill.run_json_remote("F_0030 deployed repo disable", ["disable", "--project", str(state["workspace"]["display"]), state["intern_a"]["intern"], "repo", skill_key], timeout=180)
        after = scope_snapshot()
        self.require(
            "f0030_repo_disable_cleared_scope_and_farms",
            disable.get("changed") is True
            and after["repo"].get("enabled") == []
            and farm_lacks_case_skill(after, "farm_a")
            and farm_lacks_case_skill(after, "farm_b"),
            {"disable": disable, "after": after, "case_skill": skill_name},
        )
        return {"disable": disable, "after_scope": after}

    def s04_personal_enable_boundary_and_duplicate_noop() -> dict[str, Any]:
        enable = self.ctx.action.skill.run_json_remote("F_0030 deployed personal enable", ["enable", "--project", str(state["workspace"]["display"]), state["intern_a"]["intern"], "personal", skill_key], timeout=180)
        duplicate = self.ctx.action.skill.run_json_remote("F_0030 deployed duplicate personal enable", ["enable", "--project", str(state["workspace"]["display"]), state["intern_a"]["intern"], "personal", skill_key], timeout=180)
        after = scope_snapshot()
        self.require(
            "f0030_personal_enable_boundary",
            enable.get("changed") is True
            and duplicate.get("changed") is False
            and after["repo"].get("enabled") == []
            and after["personal_a"].get("enabled") == [skill_key]
            and after["personal_b"].get("enabled") == []
            and farm_has_case_skill(after, "farm_a")
            and farm_lacks_case_skill(after, "farm_b"),
            {"enable": enable, "duplicate": duplicate, "after": after, "case_skill": skill_name},
        )
        return {"enable": enable, "duplicate": duplicate, "after_scope": after}

    def s05_promote_personal_to_repo() -> dict[str, Any]:
        source_contract = self.artifacts.get("f0030_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0030_codex_skill_scope", "f0030_deployed_gui_source_contract")
        promote = self.ctx.action.skill.run_json_remote("F_0030 deployed promote personal to repo", ["enable", "--project", str(state["workspace"]["display"]), "--promote-personal", state["intern_a"]["intern"], "repo", skill_key], timeout=180)
        after = scope_snapshot()
        self.require(
            "f0030_promote_personal_to_repo",
            promote.get("changed") is True
            and promote.get("promoted_personal") == [state["intern_a"]["intern"]]
            and after["repo"].get("enabled") == [skill_key]
            and after["personal_a"].get("enabled") == []
            and farm_has_case_skill(after, "farm_a")
            and farm_has_case_skill(after, "farm_b"),
            {"promote": promote, "after": after, "case_skill": skill_name},
        )
        return {"promote_contract": source_contract, "promote": promote, "after_scope": after}

    def s06_no_non_codex_or_copilot_shared_state() -> dict[str, Any]:
        disable = self.ctx.action.skill.run_json_remote("F_0030 deployed final repo disable", ["disable", "--project", str(state["workspace"]["display"]), state["intern_a"]["intern"], "repo", skill_key], timeout=180)
        after = scope_snapshot()
        session_entries = [state["intern_a"]["session_entry"], state["intern_b"]["session_entry"]]
        bad_types = [entry for entry in session_entries if entry.get("type") != "codex"]
        copilot_case_paths = sorted(str(path) for path in (self.work_root / ".github" / "skills").glob(f"*{self.resource_namespace}*")) if (self.work_root / ".github" / "skills").is_dir() else []
        self.require(
            "f0030_final_disable_and_no_out_of_scope_state",
            disable.get("changed") is True
            and after["repo"].get("enabled") == []
            and farm_lacks_case_skill(after, "farm_a")
            and farm_lacks_case_skill(after, "farm_b")
            and not bad_types
            and not copilot_case_paths,
            {"disable": disable, "after": after, "session_entries": session_entries, "bad_types": bad_types, "copilot_case_paths": copilot_case_paths, "case_skill": skill_name},
        )
        return {"disable": disable, "after_scope": after, "session_entries": session_entries, "copilot_case_paths": copilot_case_paths}

    self.run_ordered_scenarios([
        ("F_0030.s01_repo_enable_confirm_syncs_all_codex", s01_repo_enable_confirm_syncs_all_codex),
        ("F_0030.s02_repo_enabled_personal_reject_contract", s02_repo_enabled_personal_reject_contract),
        ("F_0030.s03_repo_disable_clears_codex_farms", s03_repo_disable_clears_codex_farms),
        ("F_0030.s04_personal_enable_boundary_and_duplicate_noop", s04_personal_enable_boundary_and_duplicate_noop),
        ("F_0030.s05_promote_personal_to_repo", s05_promote_personal_to_repo),
        ("F_0030.s06_no_non_codex_or_copilot_shared_state", s06_no_non_codex_or_copilot_shared_state),
    ])
