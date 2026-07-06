import re
from typing import Any

from CI.assertions import source_contract as source_contract_assertions
from CI.assertions import treeview as treeview_assertions
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0044.s01_reset_case_namespace",
    "F_0044.s02_create_workspace_pair_for_scope",
    "F_0044.s03_create_claude_intern",
    "F_0044.s04_create_same_name_codex_or_claude_intern",
    "F_0044.s05_tree_item_projection_claude_context",
    "F_0044.s06_tree_item_project_scope",
    "F_0044.s07_start_claude_session_no_prompt",
    "F_0044.s08_tree_online_projection_claude_backend",
    "F_0044.s09_package_json_claude_menu_contract",
    "F_0044.s10_create_claude_session_cli_equivalent",
    "F_0044.s11_restart_claude_session_cli_equivalent",
    "F_0044.s12_no_codex_env_for_claude_commands",
    "F_0044.s13_treeview_no_team_requirement_for_claude",
)


CASE = CaseDefinition(
    id="F_0044_claude_treeview_projection_command_parity_contract",
    name="Claude TreeView projection and command parity contract",
    description=(
        "Existing-deployment debug validation covering Claude intern TreeView context/icon/type/project scope, "
        "no-prompt online projection fixture, and create/restart Claude command CLI wrapper source contracts."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_claude_treeview_projection_command_parity_contract",
    tags=("F", "claude", "treeview", "session", "gui", "debug", "existing_deployment"),
    parallel_safe=True,
    extra={
        "ci_stage": "F",
        "actions": (
            "cli.internctl",
            "gui.tree.refresh",
            "gui.session.create_claude",
            "gui.session.restart_claude",
            "collect_artifacts",
            "export_report",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0044", "mode": "exclusive"},
            {"resource": "extension_bundle:claude_treeview_command_contract", "mode": "read"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0044_claude", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0044_same", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0044_workspace_a", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0044_workspace_b", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:claude_treeview_command_contract",
            "workspace_metadata:ci_f_0044_workspace_a",
            "workspace_metadata:ci_f_0044_workspace_b",
            "intern:intern_ci_f_0044_claude",
            "intern:intern_ci_f_0044_same",
            "artifact:ci_f_0044",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.claude_treeview_command_parity_consistent",),
        "scenario_ids": SCENARIO_IDS,
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, bootstrap, restart relay, or execute setup.",
            "The online Claude state uses a case-scoped no-prompt tmux/session-registry fixture instead of asking Claude or consuming a provider token.",
            "Team commands are not executed; Team coordinator Claude menu markers are recorded only as source context and are not required for pass.",
        ),
    },
)


def run_f_claude_treeview_projection_command_parity_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {"product_bug_findings": []}

    def handler_evidence() -> dict[str, Any]:
        contract = self.artifacts.get("f0044_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0044_claude_treeview_command", "f0044_deployed_gui_source_contract")
        return {
            "source_contract_summary": {
                "bundle": contract.get("bundle"),
                "package": contract.get("package"),
                "failed": contract.get("failed") or [],
                "menu_missing": contract.get("menu_missing") or [],
                "codex_env_markers": contract.get("codex_env_markers") or [],
            },
                "tree_fixture": "remote_case.tree_item_projection",
        }

    def record_product(name: str, ok: bool, *, expected: str, actual: str, detail: dict[str, Any]) -> dict[str, Any]:
        return self.collect_product_bug_evidence(
            state,
            name,
            ok,
            expected=expected,
            actual=actual,
            detail=detail,
            handler_evidence=handler_evidence(),
        )

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace_pair_for_scope() -> dict[str, Any]:
        repo_a = self.ctx.action.workspace.local_repo_fixture_remote("f0044_workspace_a")
        repo_b = self.ctx.action.workspace.local_repo_fixture_remote("f0044_workspace_b")
        workspace_a = self.ctx.action.workspace.create_case_remote(suffix="workspace_a", provider="local", repo_url=str(repo_a), mode="local_only", local_path=str(repo_a))
        workspace_b = self.ctx.action.workspace.create_case_remote(suffix="workspace_b", provider="local", repo_url=str(repo_b), mode="local_only", local_path=str(repo_b))
        state.update({"repo_a": repo_a, "repo_b": repo_b, "workspace_a": workspace_a, "workspace_b": workspace_b})
        return {"workspace_a": workspace_a, "workspace_b": workspace_b, "repo_a": str(repo_a), "repo_b": str(repo_b)}

    def s03_create_claude_intern() -> dict[str, Any]:
        claude = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(state["workspace_a"], "claude", intern_type="claude", repo_url=str(state["repo_a"])))
        state["claude"] = claude
        return {"claude": claude, "feishu_group_created": False, "session_started": False}

    def s04_create_same_name_codex_or_claude_intern() -> dict[str, Any]:
        same_a = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(state["workspace_a"], "same", intern_type="claude", repo_url=str(state["repo_a"])))
        same_b = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(state["workspace_b"], "same", intern_type="claude", repo_url=str(state["repo_b"])))
        self.require("f0044_same_name_pair_created", same_a["intern"] == same_b["intern"], {"same_a": same_a, "same_b": same_b})
        state.update({"same_a": same_a, "same_b": same_b})
        return {"same_a": same_a, "same_b": same_b, "backend": "claude"}

    def s05_tree_item_projection_claude_context() -> dict[str, Any]:
        item = treeview_assertions.checked_item_projection(self, state["workspace_a"], state["claude"]["intern"])
        ok = (
            item.get("context_value") == "intern-claude"
            and item.get("icon", {}).get("id") == "terminal"
            and item.get("tooltip", {}).get("type") == "Claude"
            and item.get("type") == "claude"
        )
        record_product(
            "product_bug_claude_tree_context_drift",
            ok,
            expected="Claude intern TreeItem has contextValue=intern-claude, terminal icon, and tooltip type Claude.",
            actual=f"Observed projection {item!r}.",
            detail={"projection": item},
        )
        return {"projection": item, "ok": ok}

    def s06_tree_item_project_scope() -> dict[str, Any]:
        main = treeview_assertions.checked_item_projection(self, state["workspace_a"], state["claude"]["intern"])
        same_a = treeview_assertions.checked_item_projection(self, state["workspace_a"], state["same_a"]["intern"])
        same_b = treeview_assertions.checked_item_projection(self, state["workspace_b"], state["same_b"]["intern"])
        ok = (
            main["command_args"] == {"name": state["claude"]["intern"], "project": state["workspace_a"]["display"]}
            and same_a["command_args"]["project"] == state["workspace_a"]["display"]
            and same_b["command_args"]["project"] == state["workspace_b"]["display"]
            and same_a["command_args"]["project"] != same_b["command_args"]["project"]
        )
        record_product(
            "product_bug_claude_tree_project_scope_drift",
            ok,
            expected="Same-name Claude TreeItems carry project-scoped command args and tooltips.",
            actual=f"Observed main={main!r}, same_a={same_a!r}, same_b={same_b!r}.",
            detail={"main": main, "same_a": same_a, "same_b": same_b},
        )
        return {"main": main, "same_a": same_a, "same_b": same_b, "ok": ok}

    def s07_start_claude_session_no_prompt() -> dict[str, Any]:
        workspace = state["workspace_a"]
        intern = state["claude"]["intern"]
        tmux_session = re.sub(r"[^A-Za-z0-9_-]+", "_", f"ia_{self.resource_namespace}_{self.run_token}_claude")[:90]
        self.run_cmd("F_0044 cleanup stale no-prompt tmux", ["tmux", "kill-session", "-t", f"={tmux_session}"], timeout=30, check=False)
        started = self.run_cmd("F_0044 start no-prompt claude tmux fixture", ["tmux", "new-session", "-d", "-s", tmux_session, "sleep", "3600"], timeout=30, check=False)
        self.require("f0044_no_prompt_tmux_fixture_started", started.returncode == 0, {"tmux_session": tmux_session, "stdout": started.stdout, "stderr": started.stderr})
        entry = self.ctx.action.session.write_registry_entry_remote(
            workspace,
            intern,
            f"ci-native://{self.case_id}/{tmux_session}",
            session_type="claude",
            tmux_session=tmux_session,
        )
        state["tmux_session"] = tmux_session
        state["session_entry"] = entry
        return {
            "session_registry_entry": entry,
            "tmux_session": tmux_session,
            "cli_equivalent": "internctl session start <intern> --project <project> --type claude --no-attach",
            "business_prompt_sent": False,
            "provider_token_used": False,
        }

    def s08_tree_online_projection_claude_backend() -> dict[str, Any]:
        item = treeview_assertions.checked_item_projection(self, state["workspace_a"], state["claude"]["intern"])
        ok = item.get("online") is True and item.get("icon", {}).get("id") == "terminal" and item.get("icon", {}).get("color") == "charts.green"
        record_product(
            "product_bug_claude_online_projection_wrong",
            ok,
            expected="Online Claude item is projected as a green terminal, not Codex rocket or Copilot account/star logic.",
            actual=f"Observed online projection {item!r}.",
            detail={"projection": item, "session_entry": state.get("session_entry")},
        )
        return {"projection": item, "ok": ok}

    def s09_package_json_claude_menu_contract() -> dict[str, Any]:
        contract = self.artifacts.get("f0044_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0044_claude_treeview_command", "f0044_deployed_gui_source_contract")
        failed = set(contract.get("failed") or [])
        ok = not ({"f0044_package_menu_has_claude_commands", "f0044_claude_menu_not_team_only"} & failed)
        record_product(
            "product_bug_claude_command_not_cli_equivalent",
            ok,
            expected="package.json exposes ordinary intern-claude menu commands without requiring Team state.",
            actual=f"Observed menu contract failures {sorted(failed)!r}.",
            detail={"contract": contract},
        )
        return {"contract": contract, "ok": ok}

    def s10_create_claude_session_cli_equivalent() -> dict[str, Any]:
        contract = self.artifacts.get("f0044_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0044_claude_treeview_command", "f0044_deployed_gui_source_contract")
        failed = set(contract.get("failed") or [])
        ok = not ({"f0044_create_claude_registered", "f0044_create_claude_uses_session_start"} & failed)
        record_product(
            "product_bug_claude_command_not_cli_equivalent",
            ok,
            expected="intern.createClaudeSession delegates to internctl session start --type claude --no-attach.",
            actual=f"Observed source contract failures {sorted(failed)!r}.",
            detail={"contract": contract},
        )
        return {"contract": contract, "ok": ok}

    def s11_restart_claude_session_cli_equivalent() -> dict[str, Any]:
        contract = self.artifacts.get("f0044_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0044_claude_treeview_command", "f0044_deployed_gui_source_contract")
        failed = set(contract.get("failed") or [])
        ok = not ({"f0044_restart_claude_registered", "f0044_restart_claude_uses_session_restart"} & failed)
        record_product(
            "product_bug_claude_command_not_cli_equivalent",
            ok,
            expected="intern.restartClaudeSession delegates to internctl session restart --type claude --no-attach and refreshes Claude type state.",
            actual=f"Observed source contract failures {sorted(failed)!r}.",
            detail={"contract": contract},
        )
        return {"contract": contract, "ok": ok}

    def s12_no_codex_env_for_claude_commands() -> dict[str, Any]:
        contract = self.artifacts.get("f0044_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0044_claude_treeview_command", "f0044_deployed_gui_source_contract")
        ok = "f0044_claude_command_has_no_codex_env_or_resume" not in set(contract.get("failed") or [])
        record_product(
            "product_bug_claude_command_uses_codex_env",
            ok,
            expected="Claude command path has no Codex-only resume/env marker.",
            actual=f"Observed Codex markers {contract.get('codex_env_markers')!r}.",
            detail={"contract": contract},
        )
        return {"contract": contract, "ok": ok}

    def s13_treeview_no_team_requirement_for_claude() -> dict[str, Any]:
        contract = self.artifacts.get("f0044_deployed_gui_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0044_claude_treeview_command", "f0044_deployed_gui_source_contract")
        visible = set(contract.get("claude_visible_commands") or [])
        forbidden = sorted(command for command in visible if command.startswith("intern.enableTeam") or command.startswith("intern.createTeam") or command.startswith("intern.assignTeam"))
        record_product(
            "product_bug_claude_command_not_cli_equivalent",
            not forbidden,
            expected="Ordinary Claude TreeView menu does not require or execute Team commands.",
            actual=f"Observed Team-only commands {forbidden!r}.",
            detail={"contract": contract, "forbidden": forbidden, "team_context_rows": contract.get("team_context_rows") or []},
        )
        aggregate = self.aggregate_product_bug_findings(state, "f0044_product_bug_aggregate")
        return {"contract": contract, "forbidden": forbidden, "aggregate": aggregate, "team_commands_executed": False}

    try:
        self.run_ordered_scenarios([
            ("F_0044.s01_reset_case_namespace", s01_reset_case_namespace),
            ("F_0044.s02_create_workspace_pair_for_scope", s02_create_workspace_pair_for_scope),
            ("F_0044.s03_create_claude_intern", s03_create_claude_intern),
            ("F_0044.s04_create_same_name_codex_or_claude_intern", s04_create_same_name_codex_or_claude_intern),
            ("F_0044.s05_tree_item_projection_claude_context", s05_tree_item_projection_claude_context),
            ("F_0044.s06_tree_item_project_scope", s06_tree_item_project_scope),
            ("F_0044.s07_start_claude_session_no_prompt", s07_start_claude_session_no_prompt),
            ("F_0044.s08_tree_online_projection_claude_backend", s08_tree_online_projection_claude_backend),
            ("F_0044.s09_package_json_claude_menu_contract", s09_package_json_claude_menu_contract),
            ("F_0044.s10_create_claude_session_cli_equivalent", s10_create_claude_session_cli_equivalent),
            ("F_0044.s11_restart_claude_session_cli_equivalent", s11_restart_claude_session_cli_equivalent),
            ("F_0044.s12_no_codex_env_for_claude_commands", s12_no_codex_env_for_claude_commands),
            ("F_0044.s13_treeview_no_team_requirement_for_claude", s13_treeview_no_team_requirement_for_claude),
        ])
    finally:
        tmux_session = str(state.get("tmux_session") or "")
        if tmux_session:
            self.run_cmd("F_0044 cleanup no-prompt tmux fixture", ["tmux", "kill-session", "-t", f"={tmux_session}"], timeout=30, check=False)
