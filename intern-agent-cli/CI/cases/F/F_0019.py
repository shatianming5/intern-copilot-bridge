from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0019.s01_reset_case_namespace",
    "F_0019.s02_send_status",
    "F_0019.s03_wait_status_reply",
    "F_0019.s04_status_reply_contains",
    "F_0019.s05_send_list_machines",
    "F_0019.s06_list_machines_contains",
    "F_0019.s07_send_list_workspaces",
    "F_0019.s08_list_workspaces_summary",
    "F_0019.s09_send_list_interns",
    "F_0019.s10_list_interns_summary",
    "F_0019.s11_send_debug",
    "F_0019.s12_debug_reply_contains",
    "F_0019.s13_no_resource_created",
)


CASE = CaseDefinition(
    id="F_0019_main_bot_readonly_slash_commands",
    name="Main bot readonly slash commands",
    description=(
        "Uses deployed relay main-bot handler to validate unmapped P2P /status, "
        "/list, and /debug readonly behavior."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_daemon_relay_api",
    tags=("F", "slash", "main-bot", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "feishu_mock.send_text",
            "feishu_main_bot.status_debug",
            "feishu_main_bot.list_topics",
            "feishu_main_bot.reject_unsupported_create",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": ("ctx.require", "ctx.contains"),
        "resource_locks": (
            {"resource": "fixture:ci_f_0019:main-bot:p2p-memory-fixture", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0019", "mode": "exclusive"},
            {"resource": "source_driver:deployed-feishu-relay", "mode": "read"},
        ),
        "resources": (
            "namespace:ci_f_0019",
            "source-driver:deployed-feishu-relay",
            "main-bot:p2p-memory-fixture",
        ),
        "run_mode": "existing_debug_native_source_driver",
        "notes": (
            "Main bot readonly coverage uses unmapped P2P context, not unmapped group.",
            "No workspace, intern, group, or helper resource should be created.",
        ),
    },
)


def run_f_main_bot_readonly_slash_commands(case: Any) -> None:
    self = case
    ctx: dict[str, Any] = {}
    state: dict[str, Any] = {}

    def s01_reset_case_namespace() -> dict[str, Any]:
        ctx.update(self.mock_feishu.relay_driver_context_for_remote(self, case_label="main_bot", mapped=False))
        return {"source_driver_only": True, "namespace": self.resource_namespace}

    def send(command: str) -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text=command, chat_id=f"ou_{self._runtime_namespace()}_main_bot_{self.run_token}", chat_type="p2p")
        state[command] = result
        return result

    def s02_send_status() -> dict[str, Any]:
        return send("/status")

    def s03_wait_status() -> dict[str, Any]:
        self.require("f0019_status_reply_present", bool(state["/status"].get("new_replies")), state["/status"])
        return state["/status"]

    def s04_assert_status() -> dict[str, Any]:
        text = "\n".join(item.get("text", "") for item in state["/status"].get("new_replies") or [])
        self.require("f0019_status_machines_connected", "Machines:" in text and "connected" in text, {"reply_text": text})
        return {"reply_text": text}

    def s05_send_list_machines() -> dict[str, Any]:
        return send("/list machines")

    def s06_assert_list_machines() -> dict[str, Any]:
        text = "\n".join(item.get("text", "") for item in state["/list machines"].get("new_replies") or [])
        self.require("f0019_list_machines_debug_pool", "debug-a" in text and "debug-b" in text, {"reply_text": text})
        return {"reply_text": text}

    def s07_send_list_workspaces() -> dict[str, Any]:
        return send("/list workspaces")

    def s08_assert_list_workspaces() -> dict[str, Any]:
        text = "\n".join(item.get("text", "") for item in state["/list workspaces"].get("new_replies") or [])
        self.require("f0019_workspace_summary_redacted", "Workspaces" in text and "secret" not in text.lower() and "token" not in text.lower(), {"reply_text": text})
        return {"reply_text": text}

    def s09_send_list_interns() -> dict[str, Any]:
        return send("/list interns")

    def s10_assert_list_interns() -> dict[str, Any]:
        text = "\n".join(item.get("text", "") for item in state["/list interns"].get("new_replies") or [])
        self.require("f0019_intern_summary_project_scoped", "Interns" in text, {"reply_text": text})
        return {"reply_text": text}

    def s11_send_debug() -> dict[str, Any]:
        return send("/debug")

    def s12_assert_debug() -> dict[str, Any]:
        text = "\n".join(item.get("text", "") for item in state["/debug"].get("new_replies") or [])
        self.require("f0019_debug_reply_redacted", "Intern Agents" in text and "secret" not in text.lower() and "token" not in text.lower(), {"reply_text": text})
        return {"reply_text": text}

    def s13_assert_no_resources_created() -> dict[str, Any]:
        detail = {
            "sent_payloads": ctx["relay_ws"].sent_payloads,
            "created_chats": ctx["api"].created_chats,
            "cards": ctx["api"].cards,
        }
        self.require("f0019_readonly_no_resource_created", not detail["sent_payloads"] and not detail["created_chats"] and not detail["cards"], detail)
        return detail

    self.run_ordered_scenarios([
        ("F_0019.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0019.s02_send_status", s02_send_status),
        ("F_0019.s03_wait_status_reply", s03_wait_status),
        ("F_0019.s04_status_reply_contains", s04_assert_status),
        ("F_0019.s05_send_list_machines", s05_send_list_machines),
        ("F_0019.s06_list_machines_contains", s06_assert_list_machines),
        ("F_0019.s07_send_list_workspaces", s07_send_list_workspaces),
        ("F_0019.s08_list_workspaces_summary", s08_assert_list_workspaces),
        ("F_0019.s09_send_list_interns", s09_send_list_interns),
        ("F_0019.s10_list_interns_summary", s10_assert_list_interns),
        ("F_0019.s11_send_debug", s11_send_debug),
        ("F_0019.s12_debug_reply_contains", s12_assert_debug),
        ("F_0019.s13_no_resource_created", s13_assert_no_resources_created),
    ])
    self.artifacts["main_bot_readonly_slash_commands"] = state
