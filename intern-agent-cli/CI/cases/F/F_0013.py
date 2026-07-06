from typing import Any

from CI.assertions import core as core_assertions
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0013.s01_project_a_create_and_reuse",
    "F_0013.s02_project_b_same_intern_is_distinct",
    "F_0013.s03_delete_project_a_only",
    "F_0013.s04_missing_project_delete_rejected",
)


CASE = CaseDefinition(
    id="F_0013_relay_chat_project_scope_lifecycle",
    name="Relay chat create/delete project scope",
    description=(
        "Validates relay /api/chat/create idempotency, same intern name across projects, "
        "project-scoped delete, and missing-project delete rejection."
    ),
    stage="remote",
    timeout_seconds=1200,
    kind="f_daemon_relay_api",
    tags=("F", "relay", "chat", "project-scope"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "relay.chat_create_existing",
            "relay.chat_create_new",
            "relay.chat_delete_project",
            "relay.read_chat_presence",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "f.relay_chat_lifecycle_consistent",
        ),
        "resource_locks": (
            {"resource": "feishu_chat:ci_f_0013", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0013", "mode": "exclusive"},
            {"resource": "project:axis_intern_agents_backup:ci_f_0013_project_a", "mode": "exclusive"},
            {"resource": "project:axis_intern_agents_backup:ci_f_0013_project_b", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0013",
            "project:ci_f_0013_project_a",
            "project:ci_f_0013_project_b",
            "relay-chat:case-scoped",
        ),
        "run_mode": "remote_deployed_api",
        "notes": (
            "Direct relay calls must include daemon owner identity from deployed _owner.json.",
            "Project B chat is intentionally retained as scene evidence.",
        ),
    },
)


def run_f_relay_chat_project_scope_lifecycle(case: Any) -> None:
    self = case
    owner = self.ctx.action.relay_daemon.owner_identity_payload_remote()
    self.require("owner_identity_present", bool(owner.get("owner_open_id") or owner.get("owner_mobile")), {"keys": sorted(owner)})
    intern = self.remote_context.identity("same_name")
    project_a = self.remote_context.workspace_name("project_a")
    project_b = self.remote_context.workspace_name("project_b")
    state: dict[str, Any] = {"intern": intern, "project_a": project_a, "project_b": project_b}

    def create(project: str, name: str) -> dict[str, Any]:
        payload = {"project": project, "intern_name": intern, "type": "codex", **owner}
        return self.relay_json(name, "POST", "/api/chat/create", payload, timeout=120)

    def s01_project_a_create_and_reuse() -> dict[str, Any]:
        first = create(project_a, "F_0013 relay chat create project A")
        second = create(project_a, "F_0013 relay chat create project A repeat")
        chat_id = str(first.get("chat_id") or "")
        self.require("f0013_project_a_chat_created", bool(chat_id), first)
        self.require("f0013_project_a_chat_reused", second.get("chat_id") == chat_id and second.get("existing") is True, {"first": first, "second": second})
        state.update({"project_a_chat": chat_id, "project_a_first": first, "project_a_second": second})
        return {"first": first, "second": second}

    def s02_project_b_same_intern_is_distinct() -> dict[str, Any]:
        created = create(project_b, "F_0013 relay chat create project B")
        chat_b = str(created.get("chat_id") or "")
        chat_a = str(state.get("project_a_chat") or "")
        self.require("f0013_project_b_chat_created", bool(chat_b), created)
        self.require("f0013_project_chat_ids_distinct", chat_a != chat_b, {"project_a_chat": chat_a, "project_b": created})
        state.update({"project_b_chat": chat_b, "project_b_created": created})
        return {"project_b": created, "project_a_chat": chat_a}

    def s03_delete_project_a_only() -> dict[str, Any]:
        deleted = self.relay_json("F_0013 relay chat delete project A", "POST", "/api/chat/delete", {"project": project_a, "intern_name": intern}, timeout=120)
        lookup_a = self.ctx.action.relay_daemon.relay_chat_lookup_remote(intern, project_a)
        lookup_b = self.ctx.action.relay_daemon.relay_chat_lookup_remote(intern, project_b)
        self.require("f0013_project_a_removed", not lookup_a.get("chat_id"), {"deleted": deleted, "lookup_a": lookup_a})
        self.require("f0013_project_b_still_exists", lookup_b.get("chat_id") == state.get("project_b_chat"), {"lookup_b": lookup_b, "state": state})
        state.update({"delete_a": deleted, "lookup_a_after_delete": lookup_a, "lookup_b_after_delete": lookup_b})
        return {"deleted": deleted, "lookup_a": lookup_a, "lookup_b": lookup_b}

    def s04_missing_project_delete_rejected() -> dict[str, Any]:
        missing = self.relay_request_json("F_0013 relay chat delete missing project", "POST", "/api/chat/delete", {"intern_name": intern}, timeout=60, check=False)
        detail = core_assertions.require_http_status(self.require, "f0013_missing_project_delete_http_400", missing, 400, error_contains="project")
        state["missing_project_delete"] = detail
        return detail

    self.run_ordered_scenarios([
        ("F_0013.s01_project_a_create_and_reuse", s01_project_a_create_and_reuse),
        ("F_0013.s02_project_b_same_intern_is_distinct", s02_project_b_same_intern_is_distinct),
        ("F_0013.s03_delete_project_a_only", s03_delete_project_a_only),
        ("F_0013.s04_missing_project_delete_rejected", s04_missing_project_delete_rejected),
    ])
    self.artifacts["relay_chat_project_scope_lifecycle"] = state
