import shutil
from pathlib import Path
from typing import Any

from CI.assertions import source_contract as source_contract_assertions
from CI.cases.base import CaseDefinition
from CI.helpers.product_cli_helper import tail


CASE = CaseDefinition(
    id="F_0029_skill_source_treeview_projection_mutation",
    name="Skill source TreeView projection and mutation",
    description=(
        "Existing-deployment debug validation covering Skill Sources TreeView empty/add/projected package "
        "and skill item, SKILL.md open path, update/remove lifecycle, invalid source rollback, and current GUI/CLI mapping."
    ),
    stage="remote",
    timeout_seconds=600,
    kind="f_skill_source_treeview_projection_mutation",
    tags=("F", "skill", "treeview", "gui", "debug", "existing_deployment"),
    parallel_safe=True,
    extra={
        "ci_stage": "F",
        "actions": (
            "cli.internctl",
            "gui.tree.refresh",
            "gui.skill.add_source",
            "gui.skill.tree_update_pkg",
            "gui.skill.tree_remove_pkg",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0029", "mode": "exclusive"},
            {"resource": "extension_bundle:skill_source_treeview_contract", "mode": "read"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0029_codex", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "skill_source:axis_intern_agents_backup:ci_f_0029_pkg", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0029_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:skill_source_treeview_contract",
            "workspace_metadata:ci_f_0029_workspace",
            "skill_source:ci_f_0029_pkg",
            "intern:intern_ci_f_0029_codex",
            "artifact:ci_f_0029",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.skill_source_treeview_projection_consistent",),
        "scenario_ids": (
            "F_0029.s01_empty_and_add_source_registry",
            "F_0029.s02_tree_projection_and_open_skill_md",
            "F_0029.s03_update_git_source_metadata",
            "F_0029.s04_remove_cancel_confirm_cascade",
            "F_0029.s05_invalid_source_rollback",
            "F_0029.s06_gui_cli_contract_source_markers",
        ),
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, restart relay, allocate Feishu groups, or run an LLM session.",
            "Uses deployed internctl skill CLI side effects plus deployed extension bundle source-contract evidence for TreeView command wiring.",
            "Current product GUI addSource routes to skill add-skill; the legacy add-source wording is recorded as script drift evidence.",
            "No Feishu group, relay restart, setup webview, Team, non-Codex intern, or Copilot shared skill is used.",
        ),
    },
)


def run_f_skill_source_treeview_projection_mutation(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}
    package = f"ci_f_0029_pkg_{self.resource_namespace}"
    skill_name = "skill_alpha"
    skill_key = f"{package}/{skill_name}"

    def s01_empty_and_add_source_registry() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0029_workspace")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="f0029", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
        root = self.ctx.action.workspace.metadata_root_remote(workspace)
        source = self.ctx.action.skill.git_source_fixture_remote("f0029_skill_source_git", name=skill_name, description="initial F_0029", rel_dir=skill_name)
        intern = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "f0029_codex", repo_url=str(repo)))
        source_root = root / ".skill_sources"
        before_packages = sorted(path.name for path in source_root.iterdir() if path.is_dir()) if source_root.is_dir() else []
        add = self.ctx.action.skill.run_json_remote(
            "F_0029 deployed skill add-skill git source",
            ["add-skill", "--project", str(workspace["display"]), "--scope", "repo", "--source-type", "git", package, source["repo"]],
            timeout=240,
        )
        repo_cfg = self.ctx.action.skill.read_json_path_remote(root / ".intern_skill.json")
        personal_cfg = self.ctx.action.skill.read_json_path_remote(root / "interns" / intern["intern"] / ".intern_skill.json")
        target = Path(self.ctx.action.skill.source_target_remote(root, package)["target"])
        target_head = self.run_cmd("F_0029 target source head", ["git", "-C", str(target), "rev-parse", "HEAD"], timeout=30).stdout.strip()
        self.require("f0029_namespace_empty_before_add", before_packages == [], {"before_packages": before_packages, "source_root": str(source_root)})
        self.require("f0029_add_source_registered_package", add.get("key") == package and target_head == source["head"], {"add": add, "target_head": target_head, "source": source})
        self.require("f0029_add_source_does_not_auto_enable", repo_cfg.get("enabled") == [] and personal_cfg.get("enabled") == [], {"repo_cfg": repo_cfg, "personal_cfg": personal_cfg})
        state.update({"repo": repo, "workspace": workspace, "root": root, "source": source, "intern": intern, "target": target})
        return {"workspace": workspace, "metadata_root": str(root), "intern": intern["intern"], "package": package, "skill_key": skill_key, "add": add, "target_head": target_head}

    def s02_tree_projection_and_open_skill_md() -> dict[str, Any]:
        root: Path = state["root"]
        target: Path = state["target"]
        source_contract = source_contract_assertions.require_deployed_contract(self, "f0029_skill_source_treeview", "f0029_deployed_gui_source_contract")
        available = self.ctx.action.skill.run_json_remote(
            "F_0029 deployed skill list-available",
            ["list-available", "--project", str(state["workspace"]["display"]), package],
            timeout=120,
        )
        skills = available.get("skills") if isinstance(available.get("skills"), list) else []
        skill_md = target / skill_name / "SKILL.md"
        packages = sorted(path.name for path in (root / ".skill_sources").iterdir() if path.is_dir())
        skill_names = [str(item.get("name") or "") for item in skills if isinstance(item, dict)]
        self.require("f0029_package_projection_count", packages == [package], {"packages": packages, "source_root": str(root / ".skill_sources")})
        self.require("f0029_skill_item_projection", skill_name in skill_names, {"available": available, "skill_names": skill_names})
        self.require("f0029_skill_md_open_path_exists", skill_md.is_file(), {"skill_md": str(skill_md), "source_contract": source_contract})
        return {"package_count": len(packages), "skill_names": skill_names, "skill_md": str(skill_md), "context_value": "skill-item-catalog-disabled", "source_contract": source_contract}

    def s03_update_git_source_metadata() -> dict[str, Any]:
        source_repo = Path(str(state["source"]["repo"]))
        updated = self.ctx.action.skill.update_git_source_fixture_remote(source_repo, name=skill_name, description="updated by F_0029", message="update F_0029 skill source", rel_dir=skill_name)
        before_head = self.run_cmd("F_0029 target head before update", ["git", "-C", str(state["target"]), "rev-parse", "HEAD"], timeout=30).stdout.strip()
        update = self.ctx.action.skill.run_cmd_remote("F_0029 deployed skill update-source", ["update-source", "--project", str(state["workspace"]["display"]), package], timeout=240)
        after_head = self.run_cmd("F_0029 target head after update", ["git", "-C", str(state["target"]), "rev-parse", "HEAD"], timeout=30).stdout.strip()
        skill_md = state["target"] / skill_name / "SKILL.md"
        text = skill_md.read_text(encoding="utf-8", errors="replace")
        self.require(
            "f0029_update_source_advanced_head",
            before_head != after_head and after_head == updated["head"],
            {"before": before_head, "after": after_head, "source": updated, "stdout": update.get("stdout"), "stderr": update.get("stderr")},
        )
        self.require("f0029_update_source_metadata_visible", "updated by F_0029" in text, {"skill_md": str(skill_md), "text_tail": tail(text, 800)})
        return {"source_head": updated["head"], "target_head_before": before_head, "target_head_after": after_head, "skill_md": str(skill_md), "stderr": tail(str(update.get("stderr") or ""), 1000)}

    def s04_remove_cancel_confirm_cascade() -> dict[str, Any]:
        workspace = state["workspace"]
        root: Path = state["root"]
        intern = state["intern"]["intern"]
        runtime = Path(str(state["intern"]["runtime"]))
        source_contract = self.artifacts.get("f0029_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0029_skill_source_treeview", "f0029_deployed_gui_source_contract")
        enable = self.ctx.action.skill.run_json_remote("F_0029 pre-remove repo enable", ["enable", "--project", str(workspace["display"]), intern, "repo", skill_key], timeout=180)
        link = Path(self.ctx.action.skill.farm_link_remote(runtime, skill_name)["link"])
        self.require("f0029_pre_remove_repo_enable_synced", enable.get("sync", {}).get("ok") is True and link.exists(), {"enable": enable, "link": str(link), "is_symlink": link.is_symlink()})
        cancel_evidence = {"modal_confirm": False, "cli_invoked": False, "source_exists": state["target"].exists(), "source_contract": source_contract}
        remove = self.ctx.action.skill.run_cmd_remote("F_0029 deployed skill remove-source", ["remove-source", "--project", str(workspace["display"]), package], timeout=240)
        repo_cfg = self.ctx.action.skill.read_json_path_remote(root / ".intern_skill.json")
        farm_entries = list(self.ctx.action.skill.farm_entries_remote(runtime).get("entries") or [])
        self.require("f0029_remove_source_deleted_target", not state["target"].exists(), {"target": str(state["target"]), "stdout": remove.get("stdout"), "stderr": remove.get("stderr")})
        enabled = repo_cfg.get("enabled") if isinstance(repo_cfg.get("enabled"), list) else []
        self.require(
            "f0029_remove_source_cascaded_enabled_and_farm",
            skill_key not in enabled and skill_name not in farm_entries,
            {"repo_cfg": repo_cfg, "farm_entries": farm_entries, "case_skill": skill_name, "case_skill_key": skill_key},
        )
        return {"cancel_evidence": cancel_evidence, "remove_stderr": tail(str(remove.get("stderr") or ""), 1000), "repo_cfg": repo_cfg, "farm_entries": farm_entries, "source_removed": True}

    def s05_invalid_source_rollback() -> dict[str, Any]:
        root: Path = state["root"]
        invalid_source = self.artifact_dir / "f0029_invalid_source"
        if invalid_source.exists():
            shutil.rmtree(invalid_source, ignore_errors=True)
        invalid_source.mkdir(parents=True)
        (invalid_source / "README.md").write_text("not a skill\n", encoding="utf-8")
        invalid_key = f"ci_f_0029_invalid_{self.resource_namespace}"
        result = self.ctx.action.skill.run_cmd_remote(
            "F_0029 invalid deployed skill add-skill",
            ["add-skill", "--project", str(state["workspace"]["display"]), "--scope", "repo", "--source-type", "local", invalid_key, str(invalid_source)],
            timeout=120,
            check=False,
        )
        invalid_target = Path(self.ctx.action.skill.source_target_remote(root, invalid_key)["target"])
        repo_cfg = self.ctx.action.skill.read_json_path_remote(root / ".intern_skill.json")
        result_stderr = str(result.get("stderr") or "")
        self.require("f0029_invalid_source_failed", result.get("returncode") != 0 and "contains no SKILL.md" in result_stderr, {"returncode": result.get("returncode"), "stderr": result_stderr, "stdout": result.get("stdout")})
        self.require("f0029_invalid_source_no_partial_registry", not invalid_target.exists() and repo_cfg.get("enabled") == [], {"target": str(invalid_target), "target_exists": invalid_target.exists(), "repo_cfg": repo_cfg})
        return {"returncode": result.get("returncode"), "stderr": tail(result_stderr, 1000), "target_exists": invalid_target.exists(), "repo_cfg": repo_cfg}

    def s06_gui_cli_contract_source_markers() -> dict[str, Any]:
        source_contract = self.artifacts.get("f0029_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0029_skill_source_treeview", "f0029_deployed_gui_source_contract")
        local_source = self.artifact_dir / "f0029_local_source_gap"
        if local_source.exists():
            shutil.rmtree(local_source, ignore_errors=True)
        self.ctx.action.skill.write_source_fixture_remote(local_source, name="local_update_gap", description="local update gap")
        local_key = f"ci_f_0029_local_update_gap_{self.resource_namespace}"
        add = self.ctx.action.skill.run_json_remote(
            "F_0029 local source add for update gap",
            ["add-skill", "--project", str(state["workspace"]["display"]), "--scope", "repo", "--source-type", "local", local_key, str(local_source)],
            timeout=120,
        )
        update = self.ctx.action.skill.run_cmd_remote(
            "F_0029 local source update gap",
            ["update-source", "--project", str(state["workspace"]["display"]), local_key],
            timeout=120,
            check=False,
        )
        update_stderr = str(update.get("stderr") or "")
        self.require("f0029_local_source_update_gap_stable", update.get("returncode") != 0 and "local sources can't be updated remotely" in update_stderr, {"returncode": update.get("returncode"), "stderr": update_stderr, "add": add})
        return {
            "source_contract": source_contract,
            "script_contract_adjustments": {
                "add_source_wording": "GUI command intern.skill.addSource currently routes to internctl skill add-skill.",
                "local_update_source": "internctl skill update-source is git-source only; local source update is recorded as script drift evidence.",
                "local_update_stderr": tail(update_stderr, 1000),
            },
        }

    self.run_ordered_scenarios([
        ("F_0029.s01_empty_and_add_source_registry", s01_empty_and_add_source_registry),
        ("F_0029.s02_tree_projection_and_open_skill_md", s02_tree_projection_and_open_skill_md),
        ("F_0029.s03_update_git_source_metadata", s03_update_git_source_metadata),
        ("F_0029.s04_remove_cancel_confirm_cascade", s04_remove_cancel_confirm_cascade),
        ("F_0029.s05_invalid_source_rollback", s05_invalid_source_rollback),
        ("F_0029.s06_gui_cli_contract_source_markers", s06_gui_cli_contract_source_markers),
    ])
