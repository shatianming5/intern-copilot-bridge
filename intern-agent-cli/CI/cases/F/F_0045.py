from pathlib import Path
from typing import Any

from CI.assertions import source_contract as source_contract_assertions
from CI.cases.base import CaseDefinition
from CI.helpers.product_cli_helper import parse_json_output, tail


SCENARIO_IDS = (
    "F_0045.s01_reset_case_namespace",
    "F_0045.s02_create_claude_intern_with_workspace",
    "F_0045.s03_add_case_skill_source",
    "F_0045.s04_enable_repo_skill_for_claude",
    "F_0045.s05_skill_farm_path_for_claude",
    "F_0045.s06_tree_skill_groups_for_claude",
    "F_0045.s07_enable_personal_skill_for_claude",
    "F_0045.s08_skill_config_sources_consistent",
    "F_0045.s09_disable_repo_skill_for_claude",
    "F_0045.s10_claude_skill_disable_removes_repo_scope",
    "F_0045.s11_set_group_trigger_mode",
    "F_0045.s12_set_group_detail_mode",
    "F_0045.s13_group_mode_project_scoped",
    "F_0045.s14_group_mode_same_name_isolation",
    "F_0045.s15_no_agent_prompt_required",
)


CASE = CaseDefinition(
    id="F_0045_claude_skill_farm_group_parity_contract",
    name="Claude skill farm and group parity contract",
    description=(
        "Existing-deployment debug validation covering Claude .claude/skills repo/personal enable-disable sync, "
        "TreeView skill projection counts, and Claude group trigger/detail mode project-scope parity."
    ),
    stage="remote",
    timeout_seconds=1200,
    kind="f_claude_skill_farm_group_parity_contract",
    tags=("F", "claude", "skill", "treeview", "group-mode", "gui", "debug", "existing_deployment"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "actions": (
            "cli.internctl",
            "gui.tree.refresh",
            "gui.skill.tree_enable_repo",
            "gui.skill.tree_enable_personal",
            "gui.skill.tree_disable_repo",
            "daemon.group_trigger_mode_proxy",
            "daemon.group_detail_mode_proxy",
            "collect_artifacts",
            "export_report",
        ),
        "resource_locks": (
            {"resource": "artifact:ci_f_0045", "mode": "exclusive"},
            {"resource": "extension_bundle:claude_skill_group_contract", "mode": "read"},
            {"resource": "feishu_chat:ci_f_0045", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0045_claude", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "skill_source:axis_intern_agents_backup:ci_f_0045_skill", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0045_workspace_a", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0045_workspace_b", "mode": "exclusive"},
        ),
        "resources": (
            "debug_machine:existing_deployment",
            "deployed_extension_bundle:claude_skill_group_contract",
            "workspace_metadata:ci_f_0045_workspace_a",
            "workspace_metadata:ci_f_0045_workspace_b",
            "intern:intern_ci_f_0045_claude",
            "skill_source:ci_f_0045_skill",
            "case_scoped_feishu_group",
            "artifact:ci_f_0045",
        ),
        "run_mode": "existing_deployment_remote_window_no_deploy",
        "assertions": ("f.claude_skill_farm_group_parity_consistent",),
        "scenario_ids": SCENARIO_IDS,
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/install, reset/deploy, bootstrap, restart relay, or execute setup.",
            "Uses file/config/registry/CLI/source evidence only; no natural-language prompt is sent to Claude.",
            "No Team is created. A case-scoped ordinary intern Feishu group is used only for trigger/detail mode parity.",
        ),
    },
)


def run_f_claude_skill_farm_group_parity_contract(case: Any) -> None:
    self = case
    state: dict[str, Any] = {"product_bug_findings": []}
    package = f"ci_f_0045_skill_{self.resource_namespace}"
    skill_name = "skill_alpha"
    skill_key = f"{package}/{skill_name}"

    def handler_evidence() -> dict[str, Any]:
        contract = self.artifacts.get("f0045_deployed_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0045_claude_skill_group", "f0045_deployed_source_contract")
        return {
            "source_contract_summary": {
                "bundle": contract.get("bundle"),
                "failed": contract.get("failed") or [],
                "group_missing": contract.get("group_missing") or [],
                "skill_cli_source": contract.get("skill_cli_source") or {},
            },
                "skill_farm_fixture": "skill.farm_entries_remote",
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

    def run_skill_json_report(label: str, args: list[str], *, timeout: int = 180) -> dict[str, Any]:
        result = self.ctx.action.skill.run_cmd_remote(label, args, timeout=timeout, check=False)
        stdout = str(result.get("stdout") or "")
        stderr = str(result.get("stderr") or "")
        payload: dict[str, Any] = {}
        if stdout.strip():
            try:
                parsed = parse_json_output(label, stdout)
                payload = parsed if isinstance(parsed, dict) else {"raw": parsed}
            except Exception as exc:  # noqa: BLE001
                payload = {"parse_error": str(exc), "stdout_tail": tail(stdout, 2000)}
        return {
            "returncode": int(result.get("returncode") or 0),
            "ok": int(result.get("returncode") or 0) == 0,
            "payload": payload,
            "stdout": tail(stdout, 2000),
            "stderr": tail(stderr, 2000),
        }

    def scope_snapshot() -> dict[str, Any]:
        root: Path = state["root"]
        intern = state["intern"]["intern"]
        runtime = Path(str(state["intern"]["runtime"]))
        claude_farm = runtime / ".claude" / "skills"
        codex_farm = runtime / ".agents" / "skills"
        return {
            "repo": self.ctx.action.skill.read_json_path_remote(root / ".intern_skill.json"),
            "personal": self.ctx.action.skill.read_json_path_remote(root / "interns" / intern / ".intern_skill.json"),
            "claude_farm": list(self.ctx.action.skill.farm_entries_remote(runtime, "claude").get("entries") or []),
            "codex_farm": list(self.ctx.action.skill.farm_entries_remote(runtime, "codex").get("entries") or []),
            "claude_farm_path": str(claude_farm),
            "codex_farm_path": str(codex_farm),
            "claude_link": self.ctx.action.skill.farm_link_remote(runtime, skill_name, "claude")["link"],
            "codex_link": self.ctx.action.skill.farm_link_remote(runtime, skill_name, "codex")["link"],
        }

    def list_projection() -> dict[str, Any]:
        listed = self.ctx.action.skill.run_json_remote("F_0045 deployed skill list", ["list", "--project", str(state["workspace"]["display"]), state["intern"]["intern"]], timeout=120)
        available = self.ctx.action.skill.run_json_remote("F_0045 deployed skill list available", ["list-available", "--project", str(state["workspace"]["display"]), package], timeout=120)
        pdata = listed.get("projects", {}).get(str(state["workspace"]["display"]), {}) if isinstance(listed.get("projects"), dict) else {}
        repo_enabled = list(pdata.get("repo_enabled") or [])
        personal_enabled = list(pdata.get("personal_enabled") or [])
        return {
            "list": listed,
            "available": available,
            "repo_enabled": repo_enabled,
            "personal_enabled": personal_enabled,
            "repo_count": 1 if skill_key in repo_enabled else 0,
            "personal_count": 1 if skill_key in personal_enabled else 0,
            "context_values": {
                "catalog_disabled": "skill-item-catalog-disabled",
                "catalog_repo_enabled": "skill-item-catalog-enabled-repo",
                "personal_enabled": "skill-item-enabled-personal",
            },
        }

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_claude_intern_with_workspace() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("f0045_workspace")
        repo_b = self.ctx.action.workspace.local_repo_fixture_remote("f0045_workspace_b")
        workspace = self.ctx.action.workspace.create_case_remote(suffix="workspace_a", provider="local", repo_url=str(repo), mode="local_only", local_path=str(repo))
        workspace_b = self.ctx.action.workspace.create_case_remote(suffix="workspace_b", provider="local", repo_url=str(repo_b), mode="local_only", local_path=str(repo_b))
        root = self.ctx.action.workspace.metadata_root_remote(workspace)
        intern = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(
            workspace,
            "claude",
            intern_type="claude",
            repo_url=str(repo),
            skip_feishu_group=False,
            skip_status_notify=True,
        ))
        chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace, intern["intern"], timeout=self.args.timeout)
        relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace, intern["intern"], timeout=self.args.timeout)
        same_b = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(
            workspace_b,
            "claude",
            intern_type="claude",
            repo_url=str(repo_b),
            skip_feishu_group=False,
            skip_status_notify=True,
        ))
        self.require("f0045_same_name_pair_created", same_b["intern"] == intern["intern"], {"same_b": same_b, "intern_a": intern})
        chat_b = self.ctx.action.feishu.wait_chat_lookup_remote(workspace_b, same_b["intern"], timeout=self.args.timeout)
        relay_b = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace_b, same_b["intern"], timeout=self.args.timeout)
        trigger_b = self.ctx.action.feishu.group_mode_cli_remote(workspace_b, same_b["intern"], command="trigger-mode", mode="all", check=False)
        detail_b = self.ctx.action.feishu.group_mode_cli_remote(workspace_b, same_b["intern"], command="detail-mode", mode="summary", check=False)
        state.update({
            "repo": repo,
            "repo_b": repo_b,
            "workspace": workspace,
            "workspace_b": workspace_b,
            "root": root,
            "intern": intern,
            "same_b": same_b,
            "chat": chat,
            "chat_b": chat_b,
            "relay": relay,
            "relay_b": relay_b,
            "trigger_b_baseline": trigger_b,
            "detail_b_baseline": detail_b,
        })
        return {
            "repo": str(repo),
            "repo_b": str(repo_b),
            "workspace": workspace,
            "workspace_b": workspace_b,
            "intern": intern,
            "same_b": same_b,
            "chat": chat,
            "chat_b": chat_b,
            "relay": relay,
            "relay_b": relay_b,
            "trigger_b": trigger_b,
            "detail_b": detail_b,
            "team_created": False,
        }

    def s03_add_case_skill_source() -> dict[str, Any]:
        source = self.ctx.action.skill.git_source_fixture_remote("f0045_skill_source_git", name=skill_name, description="initial F_0045", rel_dir=skill_name)
        add = run_skill_json_report(
            "F_0045 deployed seed skill source",
            ["add-skill", "--project", str(state["workspace"]["display"]), "--scope", "repo", "--source-type", "git", package, source["repo"]],
            timeout=240,
        )
        source_contract = source_contract_assertions.require_deployed_contract(self, "f0045_claude_skill_group", "f0045_deployed_source_contract")
        record_product(
            "product_bug_claude_skill_projection_missing",
            add.get("returncode") == 0 and (add.get("payload") or {}).get("key") == package,
            expected="Case skill source is registered and visible for TreeView projection.",
            actual=f"Observed add-skill report {add!r}.",
            detail={"add": add, "source": source, "source_contract": source_contract},
        )
        state["source"] = source
        return {"source": source, "add": add, "source_contract": source_contract}

    def s04_enable_repo_skill_for_claude() -> dict[str, Any]:
        before = scope_snapshot()
        enable = run_skill_json_report("F_0045 deployed repo enable", ["enable", "--project", str(state["workspace"]["display"]), state["intern"]["intern"], "repo", skill_key], timeout=180)
        after = scope_snapshot()
        sync = (enable.get("payload") or {}).get("sync")
        ok = enable.get("returncode") == 0 and (enable.get("payload") or {}).get("changed") is True and isinstance(sync, dict) and sync.get("ok") is True
        record_product(
            "product_bug_claude_skill_projection_missing",
            ok,
            expected="Repo skill enable for Claude succeeds and syncs the Claude runtime farm.",
            actual=f"Observed enable={enable!r}, after={after!r}.",
            detail={"before": before, "enable": enable, "after": after},
        )
        state["repo_enable"] = enable
        return {"before": before, "enable": enable, "after": after}

    def s05_skill_farm_path_for_claude() -> dict[str, Any]:
        after = scope_snapshot()
        ok = after["claude_farm"] == [skill_name] and skill_name not in after["codex_farm"]
        record_product(
            "product_bug_claude_skill_farm_path_wrong",
            ok,
            expected="Claude skill sync writes the enabled skill to .claude/skills and not .agents/skills.",
            actual=f"Observed farm snapshot {after!r}.",
            detail={"snapshot": after, "expected": ".claude/skills", "forbidden": ".agents/skills"},
        )
        return {"snapshot": after, "ok": ok}

    def s06_tree_skill_groups_for_claude() -> dict[str, Any]:
        projection = list_projection()
        ok = projection["repo_count"] == 1 and projection["personal_count"] == 0
        record_product(
            "product_bug_claude_skill_projection_missing",
            ok,
            expected="Claude TreeView skill projection has repo_count=1 and personal_count=0 after repo enable.",
            actual=f"Observed projection {projection!r}.",
            detail={"projection": projection},
        )
        return {"projection": projection, "ok": ok}

    def s07_enable_personal_skill_for_claude() -> dict[str, Any]:
        enable = run_skill_json_report("F_0045 deployed personal enable", ["enable", "--project", str(state["workspace"]["display"]), state["intern"]["intern"], "personal", skill_key], timeout=180)
        after = scope_snapshot()
        ok = enable.get("returncode") == 0 and (enable.get("payload") or {}).get("changed") is True and skill_key in after["personal"].get("enabled", [])
        record_product(
            "product_bug_claude_skill_projection_missing",
            ok,
            expected="Personal skill enable for Claude succeeds and records personal config without agent prompts.",
            actual=f"Observed personal enable={enable!r}, after={after!r}.",
            detail={"enable": enable, "after": after},
        )
        state["personal_enable"] = enable
        return {"enable": enable, "after": after}

    def s08_skill_config_sources_consistent() -> dict[str, Any]:
        after = scope_snapshot()
        projection = list_projection()
        ok = (
            after["repo"].get("enabled") == [skill_key]
            and after["personal"].get("enabled") == [skill_key]
            and after["claude_farm"] == [skill_name]
            and after["codex_farm"] == []
            and projection["repo_count"] == 1
            and projection["personal_count"] == 1
        )
        record_product(
            "product_bug_claude_skill_projection_missing",
            ok,
            expected="Repo/personal config, Claude farm, and TreeView projection counts are consistent.",
            actual=f"Observed snapshot={after!r}, projection={projection!r}.",
            detail={"snapshot": after, "projection": projection},
        )
        return {"snapshot": after, "projection": projection, "ok": ok}

    def s09_disable_repo_skill_for_claude() -> dict[str, Any]:
        before = scope_snapshot()
        disable = run_skill_json_report("F_0045 deployed repo disable", ["disable", "--project", str(state["workspace"]["display"]), state["intern"]["intern"], "repo", skill_key], timeout=180)
        after = scope_snapshot()
        ok = disable.get("returncode") == 0 and (disable.get("payload") or {}).get("changed") is True and after["repo"].get("enabled") == []
        record_product(
            "product_bug_claude_skill_disable_not_synced",
            ok,
            expected="Repo skill disable removes repo config for Claude.",
            actual=f"Observed disable={disable!r}, after={after!r}.",
            detail={"before": before, "disable": disable, "after": after},
        )
        state["repo_disable"] = disable
        return {"before": before, "disable": disable, "after": after}

    def s10_claude_skill_disable_removes_repo_scope() -> dict[str, Any]:
        after = scope_snapshot()
        projection = list_projection()
        ok = (
            after["repo"].get("enabled") == []
            and after["personal"].get("enabled") == [skill_key]
            and after["claude_farm"] == [skill_name]
            and after["codex_farm"] == []
            and projection["repo_count"] == 0
            and projection["personal_count"] == 1
        )
        record_product(
            "product_bug_claude_skill_disable_not_synced",
            ok,
            expected="Disabling repo scope removes repo projection while preserving the personal Claude farm entry.",
            actual=f"Observed snapshot={after!r}, projection={projection!r}.",
            detail={"snapshot": after, "projection": projection},
        )
        return {"snapshot": after, "projection": projection, "ok": ok}

    def s11_set_group_trigger_mode() -> dict[str, Any]:
        result = self.ctx.action.feishu.group_mode_cli_remote(state["workspace"], state["intern"]["intern"], command="trigger-mode", mode="at_only", check=False)
        ok = result.get("returncode") == 0 and ((result.get("payload") or {}).get("mode") == "at_only" or result.get("mode") == "at_only")
        record_product(
            "product_bug_claude_group_mode_not_supported",
            ok,
            expected="Claude intern group trigger mode can be set through the same CLI/API path as Codex; script manual mode maps to current at_only CLI value.",
            actual=(
                "Observed trigger-mode returncode="
                f"{result.get('returncode')} error={(result.get('payload') or {}).get('error') or result.get('stderr') or ''!r}."
            ),
            detail={"result": result, "script_mode": "manual", "cli_mode": "at_only"},
        )
        state["trigger"] = result
        return {"result": result, "gui_command": "intern.setTriggerModeAtOnly", "script_mode": "manual", "cli_mode": "at_only"}

    def s12_set_group_detail_mode() -> dict[str, Any]:
        result = self.ctx.action.feishu.group_mode_cli_remote(state["workspace"], state["intern"]["intern"], command="detail-mode", mode="full", check=False)
        ok = result.get("returncode") == 0 and ((result.get("payload") or {}).get("mode") == "full" or result.get("mode") == "full")
        record_product(
            "product_bug_claude_group_mode_not_supported",
            ok,
            expected="Claude intern group detail mode can be set through the same CLI/API path as Codex; script detailed mode maps to current full CLI value.",
            actual=(
                "Observed detail-mode returncode="
                f"{result.get('returncode')} error={(result.get('payload') or {}).get('error') or result.get('stderr') or ''!r}."
            ),
            detail={"result": result, "script_mode": "detailed", "cli_mode": "full"},
        )
        state["detail"] = result
        return {"result": result, "gui_command": "intern.setDetailModeFull", "script_mode": "detailed", "cli_mode": "full"}

    def s13_group_mode_project_scoped() -> dict[str, Any]:
        config = self.ctx.action.feishu.group_config_remote(state["workspace"], state["intern"]["intern"], check=False)
        ok = config.get("trigger_mode") == "at_only" and config.get("detail_mode") == "full"
        record_product(
            "product_bug_claude_group_project_scope_drift",
            ok,
            expected="Group trigger/detail config is readable for the exact project+Claude intern scope.",
            actual=(
                "Observed trigger="
                f"{config.get('trigger_status')}/{config.get('trigger_mode')} "
                f"detail={config.get('detail_status')}/{config.get('detail_mode')}."
            ),
            detail={"config": config, "workspace": state["workspace"], "intern": state["intern"]["intern"]},
        )
        return {"config": config, "ok": ok}

    def s14_group_mode_same_name_isolation() -> dict[str, Any]:
        workspace_b = state["workspace_b"]
        same_b = state["same_b"]
        config_a = self.ctx.action.feishu.group_config_remote(state["workspace"], state["intern"]["intern"], check=False)
        config_b = self.ctx.action.feishu.group_config_remote(workspace_b, same_b["intern"], check=False)
        ok = (
            config_a.get("trigger_mode") == "at_only"
            and config_a.get("detail_mode") == "full"
            and config_b.get("trigger_mode") == "all"
            and config_b.get("detail_mode") == "summary"
            and config_a.get("project") != config_b.get("project")
        )
        record_product(
            "product_bug_claude_group_project_scope_drift",
            ok,
            expected="Same-name Claude interns in different projects keep independent group trigger/detail modes.",
            actual=(
                "Observed A trigger/detail="
                f"{config_a.get('trigger_mode')}/{config_a.get('detail_mode')} "
                "and B trigger/detail="
                f"{config_b.get('trigger_mode')}/{config_b.get('detail_mode')}."
            ),
            detail={
                "workspace_b": workspace_b,
                "same_b": same_b,
                "chat_b": state.get("chat_b"),
                "trigger_b": state.get("trigger_b_baseline"),
                "detail_b": state.get("detail_b_baseline"),
                "config_a": config_a,
                "config_b": config_b,
            },
        )
        return {"workspace_b": workspace_b, "same_b": same_b, "chat_b": state.get("chat_b"), "config_a": config_a, "config_b": config_b, "ok": ok}

    def s15_no_agent_prompt_required() -> dict[str, Any]:
        source_contract = self.artifacts.get("f0045_deployed_source_contract") or source_contract_assertions.require_deployed_contract(self, "f0045_claude_skill_group", "f0045_deployed_source_contract")
        aggregate = self.aggregate_product_bug_findings(state, "f0045_product_bug_aggregate")
        return {
            "agent_prompt_required": False,
            "business_prompt_sent": False,
            "source_contract": source_contract,
            "aggregate": aggregate,
            "team_created": False,
        }

    self.run_ordered_scenarios([
        ("F_0045.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0045.s02_create_claude_intern_with_workspace", s02_create_claude_intern_with_workspace),
        ("F_0045.s03_add_case_skill_source", s03_add_case_skill_source),
        ("F_0045.s04_enable_repo_skill_for_claude", s04_enable_repo_skill_for_claude),
        ("F_0045.s05_skill_farm_path_for_claude", s05_skill_farm_path_for_claude),
        ("F_0045.s06_tree_skill_groups_for_claude", s06_tree_skill_groups_for_claude),
        ("F_0045.s07_enable_personal_skill_for_claude", s07_enable_personal_skill_for_claude),
        ("F_0045.s08_skill_config_sources_consistent", s08_skill_config_sources_consistent),
        ("F_0045.s09_disable_repo_skill_for_claude", s09_disable_repo_skill_for_claude),
        ("F_0045.s10_claude_skill_disable_removes_repo_scope", s10_claude_skill_disable_removes_repo_scope),
        ("F_0045.s11_set_group_trigger_mode", s11_set_group_trigger_mode),
        ("F_0045.s12_set_group_detail_mode", s12_set_group_detail_mode),
        ("F_0045.s13_group_mode_project_scoped", s13_group_mode_project_scoped),
        ("F_0045.s14_group_mode_same_name_isolation", s14_group_mode_same_name_isolation),
        ("F_0045.s15_no_agent_prompt_required", s15_no_agent_prompt_required),
    ])
