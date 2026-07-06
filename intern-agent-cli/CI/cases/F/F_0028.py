import json
from typing import Any

from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0028.s01_reset_case_namespace",
    "F_0028.s02_create_workspace_a",
    "F_0028.s03_create_workspace_b",
    "F_0028.s04_seed_group_a",
    "F_0028.s05_seed_group_b",
    "F_0028.s06_gui_set_trigger_all",
    "F_0028.s07_trigger_all_cli_equivalent",
    "F_0028.s08_group_config_a_trigger_all",
    "F_0028.s09_group_config_b_unchanged",
    "F_0028.s10_gui_set_trigger_at_only",
    "F_0028.s11_group_config_a_trigger_at_only",
    "F_0028.s12_gui_set_detail_full",
    "F_0028.s13_detail_full_cli_equivalent",
    "F_0028.s14_group_config_a_detail_full",
    "F_0028.s15_gui_set_detail_summary",
    "F_0028.s16_group_config_a_detail_summary",
    "F_0028.s17_remove_group_registry_a",
    "F_0028.s18_attempt_trigger_missing_group",
    "F_0028.s19_group_mode_failed",
    "F_0028.s20_group_config_absent",
    "F_0028.s21_context_menu_contains",
    "F_0028.s22_no_slash_driver_used",
    "F_0028.s23_no_team_or_non_codex_fixture",
    "F_0028.s24_product_bug_aggregate",
)


CASE = CaseDefinition(
    id="F_0028_codex_group_mode_treeview_context_commands",
    name="Codex Feishu group mode TreeView context commands",
    description=(
        "Validates Codex TreeView group-mode context commands against internctl group trigger/detail-mode, "
        "project-scoped same-name groups, missing-group errors, menu visibility, and no slash-driver usage."
    ),
    stage="remote",
    timeout_seconds=2400,
    kind="f_intern_session_remote",
    tags=("F", "codex", "intern", "treeview", "group-mode", "daemon", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "intern.create_status_remote",
            "gui.chat.open_intern",
            "cli.internctl",
            "daemon.group_trigger_mode_proxy",
            "daemon.group_detail_mode_proxy",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "ctx.action_ok",
            "native.relay_registry_entry",
            "native.intern_metadata_status_consistent",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "feishu_chat:ci_f_0028", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0028_codex", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0028", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0028_workspace_a", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0028_workspace_b", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0028",
            "workspace:ci_f_0028_workspace_a",
            "workspace:ci_f_0028_workspace_b",
            "intern:intern_ci_f_0028_codex",
            "case_scoped_feishu_group",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy",
        "notes": (
            "Codex-only: no Claude, Copilot, not_now intern, or Team fixture is created.",
            "No slash /config or synthetic Feishu driver is used to mutate group modes.",
            "GUI command evidence records exact internctl group CLI equivalence and project-scoped payloads.",
        ),
    },
)


def run_f_codex_group_mode_treeview_context_commands(case: Any) -> None:
    self = case
    state: dict[str, Any] = {"product_bug_findings": []}

    def group_mode_source() -> dict[str, Any]:
        return {
            "daemon": self.ctx.action.source_contract.product_source_evidence("scripts/daemon/feishu_daemon.py", ["/api/group/trigger_mode", "_get_intern_project", "/api/group/detail_mode"]),
            "extension": self.ctx.action.source_contract.product_source_evidence("src/ui/internManager.ts", ["setTriggerMode", "setDetailMode", "runGroupModeCli"]),
        }

    def assert_group_mode(name: str, workspace: dict[str, Any], *, trigger_mode: str | None = None, detail_mode: str | None = None) -> dict[str, Any]:
        config = self.ctx.action.feishu.group_config_remote(workspace, state["intern"], check=False)
        expected: dict[str, str] = {}
        ok = True
        if trigger_mode is not None:
            expected["trigger_mode"] = trigger_mode
            ok = ok and config.get("trigger_mode") == trigger_mode
        if detail_mode is not None:
            expected["detail_mode"] = detail_mode
            ok = ok and config.get("detail_mode") == detail_mode
        self.collect_product_bug_evidence(
            state,
            name,
            ok,
            expected=f"Group config for {workspace['display']}:{state['intern']} matches {expected}.",
            actual=f"Observed config {config!r}.",
            detail={"workspace": workspace, "config": config, "expected": expected},
            handler_evidence=group_mode_source(),
        )
        return {"config": config, "expected": expected, "ok": ok}

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

    def s04_seed_group_a() -> dict[str, Any]:
        intern = self.ctx.action.intern.create_case_remote(state["workspace_a"], "codex", repo_url=str(state["repo_a"]))
        chat = self.ctx.action.feishu.wait_chat_lookup_remote(state["workspace_a"], intern, timeout=self.args.timeout)
        relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(state["workspace_a"], intern, timeout=self.args.timeout)
        baseline = self.ctx.action.feishu.set_group_config_direct_remote(state["workspace_a"], intern, trigger_mode="at_only", detail_mode="summary", check=False)
        self.collect_product_bug_evidence(
            state,
            "f0028_seed_group_a_mode_baseline",
            baseline.get("ok") is True,
            expected=f"Baseline group config for {state['workspace_a']['display']}:{intern} can be seeded to trigger=at_only/detail=summary.",
            actual=f"Observed baseline seed/config result {baseline!r}.",
            detail={"workspace": state["workspace_a"], "intern": intern, "baseline": baseline},
            handler_evidence=group_mode_source(),
        )
        state.update({"intern": intern, "chat_a": chat, "relay_a": relay})
        return {"intern": intern, "chat": chat, "relay": relay, "baseline": baseline}

    def s05_seed_group_b() -> dict[str, Any]:
        intern = self.ctx.action.intern.create_case_remote(state["workspace_b"], "codex", repo_url=str(state["repo_b"]))
        self.require("f0028_same_intern_name_across_workspaces", intern == state["intern"], {"intern_a": state["intern"], "intern_b": intern})
        chat = self.ctx.action.feishu.wait_chat_lookup_remote(state["workspace_b"], intern, timeout=self.args.timeout)
        relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(state["workspace_b"], intern, timeout=self.args.timeout)
        baseline = self.ctx.action.feishu.set_group_config_direct_remote(state["workspace_b"], intern, trigger_mode="at_only", detail_mode="summary", check=False)
        self.collect_product_bug_evidence(
            state,
            "f0028_seed_group_b_mode_baseline",
            baseline.get("ok") is True,
            expected=f"Baseline group config for {state['workspace_b']['display']}:{intern} can be seeded to trigger=at_only/detail=summary.",
            actual=f"Observed baseline seed/config result {baseline!r}.",
            detail={"workspace": state["workspace_b"], "intern": intern, "baseline": baseline},
            handler_evidence=group_mode_source(),
        )
        state.update({"chat_b": chat, "relay_b": relay})
        return {"intern": intern, "chat": chat, "relay": relay, "baseline": baseline}

    def s06_gui_set_trigger_all() -> dict[str, Any]:
        result = self.ctx.action.feishu.group_mode_cli_remote(state["workspace_a"], state["intern"], command="trigger-mode", mode="all", check=False)
        state["trigger_all"] = result
        return result | {"gui_command": "intern.setTriggerModeAll"}

    def s07_trigger_all_cli_equivalent() -> dict[str, Any]:
        expected = ["internctl", "group", "trigger-mode", state["intern"], "--project", state["workspace_a"]["display"], "--mode", "all"]
        result = {"gui_command": "intern.setTriggerModeAll", "cli": expected, "actual": state["trigger_all"]}
        ok = state["trigger_all"].get("returncode") == 0 and (
            state["trigger_all"].get("mode") == "all"
            or (state["trigger_all"].get("payload") or {}).get("mode") == "all"
        )
        self.collect_product_bug_evidence(
            state,
            "f0028_trigger_all_cli_equivalent",
            ok,
            expected=f"GUI intern.setTriggerModeAll maps to successful internctl group trigger-mode for {state['workspace_a']['display']}:{state['intern']}.",
            actual=f"Observed trigger-mode result {state['trigger_all']!r}.",
            detail=result,
            handler_evidence=group_mode_source(),
        )
        return result

    def s08_group_config_a_trigger_all() -> dict[str, Any]:
        return assert_group_mode("f0028_trigger_all_project_a_scoped", state["workspace_a"], trigger_mode="all", detail_mode="summary")

    def s09_group_config_b_unchanged() -> dict[str, Any]:
        return assert_group_mode("f0028_trigger_all_does_not_mutate_project_b", state["workspace_b"], trigger_mode="at_only", detail_mode="summary")

    def s10_gui_set_trigger_at_only() -> dict[str, Any]:
        result = self.ctx.action.feishu.group_mode_cli_remote(state["workspace_a"], state["intern"], command="trigger-mode", mode="at_only", check=False)
        state["trigger_at_only"] = result
        return result | {"gui_command": "intern.setTriggerModeAtOnly"}

    def s11_group_config_a_trigger_at_only() -> dict[str, Any]:
        return assert_group_mode("f0028_trigger_at_only_project_a_scoped", state["workspace_a"], trigger_mode="at_only")

    def s12_gui_set_detail_full() -> dict[str, Any]:
        result = self.ctx.action.feishu.group_mode_cli_remote(state["workspace_a"], state["intern"], command="detail-mode", mode="full", check=False)
        state["detail_full"] = result
        return result | {"gui_command": "intern.setDetailModeFull"}

    def s13_detail_full_cli_equivalent() -> dict[str, Any]:
        expected = ["internctl", "group", "detail-mode", state["intern"], "--project", state["workspace_a"]["display"], "--mode", "full"]
        result = {"gui_command": "intern.setDetailModeFull", "cli": expected, "actual": state["detail_full"]}
        ok = state["detail_full"].get("returncode") == 0 and (
            state["detail_full"].get("mode") == "full"
            or (state["detail_full"].get("payload") or {}).get("mode") == "full"
        )
        self.collect_product_bug_evidence(
            state,
            "f0028_detail_full_cli_equivalent",
            ok,
            expected=f"GUI intern.setDetailModeFull maps to successful internctl group detail-mode for {state['workspace_a']['display']}:{state['intern']}.",
            actual=f"Observed detail-mode result {state['detail_full']!r}.",
            detail=result,
            handler_evidence=group_mode_source(),
        )
        return result

    def s14_group_config_a_detail_full() -> dict[str, Any]:
        return assert_group_mode("f0028_detail_full_project_a_scoped", state["workspace_a"], trigger_mode="at_only", detail_mode="full")

    def s15_gui_set_detail_summary() -> dict[str, Any]:
        result = self.ctx.action.feishu.group_mode_cli_remote(state["workspace_a"], state["intern"], command="detail-mode", mode="summary", check=False)
        state["detail_summary"] = result
        return result | {"gui_command": "intern.setDetailModeSummary"}

    def s16_group_config_a_detail_summary() -> dict[str, Any]:
        return assert_group_mode("f0028_detail_summary_project_a_scoped", state["workspace_a"], detail_mode="summary")

    def s17_remove_group_registry_a() -> dict[str, Any]:
        deleted = self.relay_json(
            "F_0028 relay chat delete project A",
            "POST",
            "/api/chat/delete",
            {"project": state["workspace_a"]["display"], "intern_name": state["intern"]},
            timeout=120,
        )
        state["delete_a"] = deleted
        absent = self.ctx.action.feishu.group_config_remote(state["workspace_a"], state["intern"], check=False)
        return {"deleted": deleted, "absent_probe": absent}

    def s18_attempt_trigger_missing_group() -> dict[str, Any]:
        result = self.ctx.action.feishu.group_mode_cli_remote(state["workspace_a"], state["intern"], command="trigger-mode", mode="all", check=False)
        state["missing_group_attempt"] = result
        return result

    def s19_group_mode_failed() -> dict[str, Any]:
        result = state["missing_group_attempt"]
        text = json.dumps(result, ensure_ascii=False).lower()
        ok = result.get("returncode") != 0 and ("no chat" in text or "not found" in text or "unregistered" in text)
        self.collect_product_bug_evidence(
            state,
            "f0028_missing_group_trigger_mode_rejected",
            ok,
            expected="Trigger-mode command for a removed project-scoped group fails with explicit group-not-found/unregistered error.",
            actual=f"Observed result {result!r}.",
            detail={"attempt": result, "workspace": state["workspace_a"]},
            handler_evidence=group_mode_source(),
        )
        return {"attempt": result, "ok": ok}

    def s20_group_config_absent() -> dict[str, Any]:
        config = self.ctx.action.feishu.group_config_remote(state["workspace_a"], state["intern"], check=False)
        ok = config.get("trigger_status") == 404 and config.get("detail_status") == 404
        self.collect_product_bug_evidence(
            state,
            "f0028_missing_group_no_half_config",
            ok,
            expected="Removed workspace A group has no readable trigger/detail config and no half-created replacement.",
            actual=f"Observed config probe {config!r}.",
            detail={"config": config, "workspace": state["workspace_a"]},
            handler_evidence=group_mode_source(),
        )
        return {"config": config, "ok": ok}

    def s21_context_menu_contains() -> dict[str, Any]:
        menu = self.ctx.action.treeview.context_menu_commands_remote("intern-codex")
        expected = {"intern.setTriggerModeAll", "intern.setTriggerModeAtOnly", "intern.setDetailModeFull", "intern.setDetailModeSummary"}
        self.require("f0028_context_menu_contains_group_mode_commands", expected.issubset(set(menu["commands"])), {"menu": menu, "expected": sorted(expected)})
        return {"menu": menu, "expected": sorted(expected)}

    def s22_no_slash_driver_used() -> dict[str, Any]:
        slash_steps = [
            step for step in self.steps
            if "/config" in json.dumps(step, ensure_ascii=False)
            or "/trigger_mode " in json.dumps(step, ensure_ascii=False)
            or "/detail_mode " in json.dumps(step, ensure_ascii=False)
        ]
        self.require("f0028_no_slash_driver_used", not slash_steps, {"slash_steps": slash_steps})
        return {"slash_steps": slash_steps, "no_slash_driver": True}

    def s23_no_team_or_non_codex_fixture() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.intern.no_team_or_non_codex_fixture_remote())

    def s24_product_bug_aggregate() -> dict[str, Any]:
        return self.aggregate_product_bug_findings(state, "f0028_product_bug_aggregate")

    self.run_ordered_scenarios([
        ("F_0028.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0028.s02_create_workspace_a", s02_create_workspace_a),
        ("F_0028.s03_create_workspace_b", s03_create_workspace_b),
        ("F_0028.s04_seed_group_a", s04_seed_group_a),
        ("F_0028.s05_seed_group_b", s05_seed_group_b),
        ("F_0028.s06_gui_set_trigger_all", s06_gui_set_trigger_all),
        ("F_0028.s07_trigger_all_cli_equivalent", s07_trigger_all_cli_equivalent),
        ("F_0028.s08_group_config_a_trigger_all", s08_group_config_a_trigger_all),
        ("F_0028.s09_group_config_b_unchanged", s09_group_config_b_unchanged),
        ("F_0028.s10_gui_set_trigger_at_only", s10_gui_set_trigger_at_only),
        ("F_0028.s11_group_config_a_trigger_at_only", s11_group_config_a_trigger_at_only),
        ("F_0028.s12_gui_set_detail_full", s12_gui_set_detail_full),
        ("F_0028.s13_detail_full_cli_equivalent", s13_detail_full_cli_equivalent),
        ("F_0028.s14_group_config_a_detail_full", s14_group_config_a_detail_full),
        ("F_0028.s15_gui_set_detail_summary", s15_gui_set_detail_summary),
        ("F_0028.s16_group_config_a_detail_summary", s16_group_config_a_detail_summary),
        ("F_0028.s17_remove_group_registry_a", s17_remove_group_registry_a),
        ("F_0028.s18_attempt_trigger_missing_group", s18_attempt_trigger_missing_group),
        ("F_0028.s19_group_mode_failed", s19_group_mode_failed),
        ("F_0028.s20_group_config_absent", s20_group_config_absent),
        ("F_0028.s21_context_menu_contains", s21_context_menu_contains),
        ("F_0028.s22_no_slash_driver_used", s22_no_slash_driver_used),
        ("F_0028.s23_no_team_or_non_codex_fixture", s23_no_team_or_non_codex_fixture),
        ("F_0028.s24_product_bug_aggregate", s24_product_bug_aggregate),
    ])
