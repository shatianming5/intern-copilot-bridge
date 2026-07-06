import json
from typing import Any

from CI.assertions import core as core_assertions
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0012.s01_create_group_through_daemon",
    "F_0012.s02_sync_disabled_preserves_registry",
    "F_0012.s03_delete_group_through_daemon",
    "F_0012.s04_missing_project_rejected",
)


CASE = CaseDefinition(
    id="F_0012_daemon_group_proxy_registry_mutation",
    name="Daemon group proxy and registry mutation",
    description=(
        "Validates daemon /api/group/create, sync-disabled, delete, and missing-project "
        "contracts while preserving project scope in daemon and relay registries."
    ),
    stage="remote",
    timeout_seconds=1200,
    kind="f_daemon_relay_api",
    tags=("F", "daemon", "group", "relay", "registry"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "cli.internctl",
            "create_intern",
            "daemon.group_create_proxy",
            "daemon.group_sync_disabled",
            "daemon.group_delete_proxy",
            "relay.read_chat_presence",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "f.daemon_group_proxy_api_consistent",
        ),
        "resource_locks": (
            {"resource": "feishu_chat:ci_f_0012", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0012_worker", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0012", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0012_group_proxy", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0012",
            "workspace:ci_f_0012_group_proxy",
            "intern:intern_ci_f_0012_worker",
            "relay-chat:case-scoped",
        ),
        "run_mode": "remote_deployed_api",
        "notes": (
            "Case reset may clean only ci_f_0012 namespace resources.",
            "End state preserves report evidence; next reset removes retained case resources.",
        ),
    },
)


def run_f_daemon_group_proxy_registry_mutation(case: Any) -> None:
    self = case
    repo = self.ctx.action.workspace.local_repo_fixture_remote("f0012_group_proxy")
    workspace = self.ctx.action.workspace.create_case_remote(
        suffix="group_proxy",
        provider="local",
        repo_url=str(repo),
        mode="local_only",
        local_path=str(repo),
    )
    intern = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "worker", repo_url=str(repo)))["intern"]
    project = str(workspace["display"])
    state: dict[str, Any] = {"workspace": workspace, "intern": intern, "project": project}

    def s01_create_group_through_daemon() -> dict[str, Any]:
        created = self.http_json("F_0012 daemon group create", "POST", "/api/group/create", {"project": project, "intern_name": intern}, timeout=120)
        chat_id = str(created.get("chat_id") or "")
        self.require("f0012_group_create_chat_id", bool(chat_id), created)
        local_entry = self.ctx.action.relay_daemon.daemon_group_list_entry_remote(project, intern)
        relay_lookup = self.ctx.action.relay_daemon.relay_chat_lookup_remote(intern, project)
        self.require("f0012_daemon_registry_chat_id", local_entry.get("chat_id") == chat_id, {"created": created, "local_entry": local_entry})
        self.require("f0012_relay_registry_chat_id", relay_lookup.get("chat_id") == chat_id, {"created": created, "relay_lookup": relay_lookup})
        state.update({"created": created, "chat_id": chat_id, "local_entry": local_entry, "relay_lookup": relay_lookup})
        return {"created": created, "local_entry": local_entry, "relay_lookup": relay_lookup}

    def s02_sync_disabled_preserves_registry() -> dict[str, Any]:
        sync = self.daemon_request_json("F_0012 daemon group sync disabled", "POST", "/api/group/sync", {"project": project, "intern_name": intern}, timeout=60, check=False)
        sync_detail = core_assertions.require_http_status(self.require, "f0012_group_sync_http_410", sync, 410, error_contains="disabled")
        local_entry = self.ctx.action.relay_daemon.daemon_group_list_entry_remote(project, intern)
        relay_lookup = self.ctx.action.relay_daemon.relay_chat_lookup_remote(intern, project)
        chat_id = str(state.get("chat_id") or "")
        self.require("f0012_sync_preserves_daemon_registry", local_entry.get("chat_id") == chat_id, {"local_entry": local_entry, "chat_id": chat_id})
        self.require("f0012_sync_preserves_relay_registry", relay_lookup.get("chat_id") == chat_id, {"relay_lookup": relay_lookup, "chat_id": chat_id})
        state["sync"] = sync_detail
        return {"sync": sync_detail, "local_entry": local_entry, "relay_lookup": relay_lookup}

    def s03_delete_group_through_daemon() -> dict[str, Any]:
        deleted = self.http_json("F_0012 daemon group delete", "POST", "/api/group/delete", {"project": project, "intern_name": intern}, timeout=120)
        local_entry = self.ctx.action.relay_daemon.daemon_group_list_entry_remote(project, intern)
        relay_lookup = self.ctx.action.relay_daemon.relay_chat_lookup_remote(intern, project)
        self.require("f0012_group_delete_ok", deleted.get("ok") is True or bool(deleted.get("deleted_chat_ids")) or bool(deleted.get("removed")), deleted)
        self.require("f0012_daemon_registry_removed", not local_entry, {"local_entry": local_entry})
        self.require("f0012_relay_registry_removed", not relay_lookup.get("chat_id"), relay_lookup)
        state["deleted"] = deleted
        return {"deleted": deleted, "local_entry": local_entry, "relay_lookup": relay_lookup}

    def s04_missing_project_rejected() -> dict[str, Any]:
        missing = self.daemon_request_json("F_0012 daemon group create missing project", "POST", "/api/group/create", {"intern_name": intern}, timeout=90, check=False)
        body = missing.get("body") if isinstance(missing.get("body"), dict) else {}
        self.require(
            "f0012_missing_project_rejected",
            int(missing.get("status_code") or 0) in {400, 409} and "project" in json.dumps(body, ensure_ascii=False).lower(),
            {"result": missing},
        )
        local_entry = self.ctx.action.relay_daemon.daemon_group_list_entry_remote(project, intern)
        self.require("f0012_missing_project_no_registry_mutation", not local_entry, {"result": missing, "local_entry": local_entry})
        state["missing_project"] = missing
        return {"missing_project": missing, "local_entry": local_entry}

    self.run_ordered_scenarios([
        ("F_0012.s01_create_group_through_daemon", s01_create_group_through_daemon),
        ("F_0012.s02_sync_disabled_preserves_registry", s02_sync_disabled_preserves_registry),
        ("F_0012.s03_delete_group_through_daemon", s03_delete_group_through_daemon),
        ("F_0012.s04_missing_project_rejected", s04_missing_project_rejected),
    ])
    self.artifacts["daemon_group_proxy_registry_mutation"] = state
