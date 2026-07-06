import json
from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0020.s01_reset_case_namespace",
    "F_0020.s02_seed_group",
    "F_0020.s03_send_unknown_slash",
    "F_0020.s04_unknown_slash_help_reply",
    "F_0020.s05_send_unmapped_group_status",
    "F_0020.s06_unmapped_group_non_spam",
    "F_0020.s07_non_owner_config",
    "F_0020.s08_config_card_or_permission",
    "F_0020.s09_submit_sensitive_callback",
    "F_0020.s10_callback_rejected",
    "F_0020.s11_config_unchanged",
    "F_0020.s12_send_missing_project_helper",
    "F_0020.s13_slash_error_reply",
    "F_0020.s14_product_bug_aggregate",
)


CASE = CaseDefinition(
    id="F_0020_slash_routing_errors_rbac_unknown",
    name="Slash routing errors RBAC and unknown fallback",
    description=(
        "Strictly validates unknown slash fallback, unmapped group non-spam, "
        "non-owner config callback rejection, and structured bad-project errors."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_daemon_relay_api",
    tags=("F", "slash", "rbac", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "feishu_mock.send_text",
            "feishu_mock.submit_card_form",
            "relay.group_config_card_submit",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": ("ctx.require", "ctx.equals"),
        "resource_locks": (
            {"resource": "feishu_chat:ci_f_0020", "mode": "exclusive"},
            {"resource": "fixture:ci_f_0020:rbac:memory-fixture", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0020", "mode": "exclusive"},
            {"resource": "source_driver:deployed-feishu-relay", "mode": "read"},
        ),
        "resources": (
            "namespace:ci_f_0020",
            "source-driver:deployed-feishu-relay",
            "relay-chat:case-scoped",
            "rbac:memory-fixture",
        ),
        "run_mode": "existing_debug_native_source_driver",
        "notes": (
            "Mapped unknown slash passthrough and unmapped group silent drop are product_bug evidence.",
            "Do not relax strict fallback or structured-error expectations.",
        ),
    },
)


def run_f_slash_routing_errors_rbac_unknown(case: Any) -> None:
    self = case
    ctx: dict[str, Any] = {}
    state: dict[str, Any] = {}

    def s01_reset_case_namespace() -> dict[str, Any]:
        ctx.update(self.mock_feishu.relay_driver_context_for_remote(self, case_label="slash_errors", mapped=True))
        return {"source_driver_only": True, "namespace": self.resource_namespace}

    def s02_seed_group() -> dict[str, Any]:
        state["seed"] = {"project": ctx["project"], "intern": ctx["intern"], "chat": ctx["chat_id"], "owner": ctx["owner_open_id"]}
        return state["seed"]

    def s03_send_unknown_slash() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/unknown_f0020", sender_open_id=ctx["owner_open_id"])
        state["unknown_slash"] = result
        return result

    def s04_assert_unknown_help_reply() -> dict[str, Any]:
        reply_text = "\n".join(item.get("text", "") for item in state["unknown_slash"].get("new_replies") or [])
        evidence = self.collect_product_bug_evidence(
            state,
            "f0020_product_bug_unknown_slash_help_reply",
            bool(reply_text) and "可用命令" in reply_text and not state["unknown_slash"].get("new_payloads"),
            expected="Mapped unknown slash returns a help/fallback reply and is not injected into the intern.",
            actual="Mapped unknown slash was routed as a daemon feishu_message payload." if state["unknown_slash"].get("new_payloads") else f"Reply text was {reply_text!r}.",
            detail={"unknown_slash": state["unknown_slash"], "unknown_reply_text": reply_text},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "create_message_handler", "marker": "# Route: chat_id", "label": "mapped chat routes to intern"},
                {"function": "create_message_handler", "marker": 'log.info(f"[ROUTE] Feishu msg', "label": "mapped message route branch"},
                {"function": "create_message_handler", "marker": "_send_to_machine_with_reason", "label": "daemon feishu_message send"},
            ]),
        )
        return {"product_bug_evidence": evidence}

    def s05_send_unmapped_group_status() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self,
            ctx,
            text="/status",
            chat_id=f"oc_{self._runtime_namespace()}_unmapped_explicit_{self.run_token}",
            chat_type="group",
            sender_open_id=ctx["owner_open_id"],
        )
        state["unmapped_group"] = result
        return result

    def s06_assert_unmapped_group_non_spam() -> dict[str, Any]:
        replies = state["unmapped_group"].get("new_replies") or []
        evidence = self.collect_product_bug_evidence(
            state,
            "f0020_product_bug_unmapped_group_structured_error",
            0 < len(replies) <= 1 and "chat_not_registered" in json.dumps(replies, ensure_ascii=False),
            expected="Unmapped group slash returns at most one structured non-spam diagnostic such as chat_not_registered.",
            actual="Unmapped group slash silently dropped with no reply/message/card/payload." if not replies else f"Unmapped group replies were {replies!r}.",
            detail=state["unmapped_group"],
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "create_message_handler", "marker": 'if chat_type == "p2p":', "label": "unmapped P2P main-bot split"},
                {"function": "create_message_handler", "marker": "No intern/helper for chat_id", "label": "unmapped group silent drop"},
            ]),
        )
        return {"product_bug_evidence": evidence}

    def s07_non_owner_config() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/config", sender_open_id=ctx["non_owner_open_id"])
        state["non_owner_config"] = result
        return result

    def s08_wait_config_card_or_permission() -> dict[str, Any]:
        self.require("f0020_config_card_or_permission_reply", bool(state["non_owner_config"].get("new_cards") or state["non_owner_config"].get("new_replies")), state["non_owner_config"])
        return state["non_owner_config"]

    def s09_submit_sensitive_callback() -> dict[str, Any]:
        owner_card = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/config", sender_open_id=ctx["owner_open_id"])
        card = owner_card["new_cards"][-1]["card"]
        value = self.mock_feishu.config_value(card)
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value=value,
            form_value={"trigger_mode": "at_only", "detail_mode": "summary", "no_collapse_mode": "off"},
            operator_open_id=ctx["non_owner_open_id"],
        )
        state["non_owner_submit"] = {"owner_card": owner_card, "response": response}
        return state["non_owner_submit"]

    def s10_assert_callback_rejected() -> dict[str, Any]:
        response = state["non_owner_submit"]["response"]
        self.require("f0020_non_owner_callback_rejected", response.get("toast_type") == "error", response)
        return response

    def s11_assert_config_unchanged() -> dict[str, Any]:
        chat_id = ctx["chat_id"]
        detail = {
            "trigger_mode": ctx["module"].chat_config.get_trigger_mode(chat_id),
            "detail_mode": ctx["relay_ws"].detail_modes.get(chat_id, "full"),
            "no_collapse_mode": ctx["relay_ws"].no_collapse_modes.get(chat_id, "on"),
        }
        self.require("f0020_config_unchanged_after_reject", detail == {"trigger_mode": "all", "detail_mode": "full", "no_collapse_mode": "on"}, detail)
        return detail

    def s12_send_missing_project_helper() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/helper --project missing_project", sender_open_id=ctx["owner_open_id"])
        state["missing_project"] = result
        return result

    def s13_assert_missing_project_structured() -> dict[str, Any]:
        text = "\n".join(item.get("text", "") for item in state["missing_project"].get("new_replies") or [])
        evidence = self.collect_product_bug_evidence(
            state,
            "f0020_product_bug_helper_missing_project_structured_error",
            "project_not_found" in text,
            expected="/helper --project missing_project returns a structured project_not_found error.",
            actual=f"/helper --project missing_project reply text was {text!r}.",
            detail={"result": state["missing_project"], "reply_text": text},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_parse_helper_command", "marker": 'raise ValueError("unknown /helper action")', "label": "helper parser lacks --project option"},
                {"function": "_handle_helper_command", "marker": 'reply(f"⚠️ {e}")', "label": "helper parse error reply path"},
            ]),
        )
        return {"product_bug_evidence": evidence}

    def s14_assert_product_bug_findings() -> dict[str, Any]:
        return self.aggregate_product_bug_findings(state, "f0020_product_bug_aggregate")

    self.run_ordered_scenarios([
        ("F_0020.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0020.s02_seed_group", s02_seed_group),
        ("F_0020.s03_send_unknown_slash", s03_send_unknown_slash),
        ("F_0020.s04_unknown_slash_help_reply", s04_assert_unknown_help_reply),
        ("F_0020.s05_send_unmapped_group_status", s05_send_unmapped_group_status),
        ("F_0020.s06_unmapped_group_non_spam", s06_assert_unmapped_group_non_spam),
        ("F_0020.s07_non_owner_config", s07_non_owner_config),
        ("F_0020.s08_config_card_or_permission", s08_wait_config_card_or_permission),
        ("F_0020.s09_submit_sensitive_callback", s09_submit_sensitive_callback),
        ("F_0020.s10_callback_rejected", s10_assert_callback_rejected),
        ("F_0020.s11_config_unchanged", s11_assert_config_unchanged),
        ("F_0020.s12_send_missing_project_helper", s12_send_missing_project_helper),
        ("F_0020.s13_slash_error_reply", s13_assert_missing_project_structured),
        ("F_0020.s14_product_bug_aggregate", s14_assert_product_bug_findings),
    ])
    self.artifacts["slash_routing_errors_rbac_unknown"] = state
