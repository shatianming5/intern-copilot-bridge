from typing import Any
from CI.helpers.native_error import NativeCaseError
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0041.s01_reset_case_namespace",
    "F_0041.s02_seed_mapped_intern_group",
    "F_0041.s03_real_config_message",
    "F_0041.s04_wait_for_config_card",
    "F_0041.s05_owner_save_config_callback",
    "F_0041.s06_config_saved_card_updated_owner_identity",
    "F_0041.s07_real_config_cancel_callback",
    "F_0041.s08_cancel_no_mutation",
    "F_0041.s09_non_owner_save_config_callback",
    "F_0041.s10_card_callback_rbac_rejected",
    "F_0041.s11_real_helper_message",
    "F_0041.s12_helper_usage_reply",
    "F_0041.s13_real_helper_start_message",
    "F_0041.s14_wait_for_helper_card",
    "F_0041.s15_helper_start_card_callback",
    "F_0041.s16_helper_action_routed_to_machine",
    "F_0041.s17_main_bot_p2p_status_message",
    "F_0041.s18_main_bot_status_reply",
    "F_0041.s19_unmapped_group_status_message",
    "F_0041.s20_unmapped_group_non_spam_policy",
    "F_0041.s21_unknown_slash_message",
    "F_0041.s22_unknown_slash_help_not_injected",
    "F_0041.s23_driver_is_real_relay_handler",
)


CASE = CaseDefinition(
    id="F_0041_real_feishu_ingress_slash_card_callback_contract",
    name="Real Feishu ingress slash and card callback contract",
    description=(
        "Uses the deployed relay source-driver to exercise real relay-local "
        "message and card callback handler branches for /config, /helper, "
        "main-bot P2P, unmapped group, RBAC, and unknown slash behavior."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_daemon_relay_api",
    tags=("F", "feishu", "ingress", "slash", "card", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "feishu_mock.send_text",
            "feishu_mock.submit_card_form",
            "feishu_mock.click_card",
            "relay.group_config_card_submit",
            "feishu_main_bot.status_debug",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "native.helper_service_lifecycle",
            "f.real_feishu_ingress_handler_consistent",
        ),
        "resource_locks": (
            {"resource": "feishu_chat:ci_f_0041", "mode": "exclusive"},
            {"resource": "fixture:ci_f_0041:config:memory-fixture", "mode": "exclusive"},
            {"resource": "fixture:ci_f_0041:helper:memory-fixture", "mode": "exclusive"},
            {"resource": "fixture:ci_f_0041:main-bot:p2p-memory-fixture", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0041_codex_*", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0041", "mode": "exclusive"},
            {"resource": "project:axis_intern_agents_backup:ci_f_0041_project_*", "mode": "exclusive"},
            {"resource": "source_driver:deployed-feishu-relay", "mode": "read"},
        ),
        "resources": (
            "namespace:ci_f_0041",
            "project:ci_f_0041_project_{run_id}",
            "intern:intern_ci_f_0041_codex_{run_id}",
            "source-driver:deployed-feishu-relay",
            "relay-chat:case-scoped",
            "main-bot:p2p-memory-fixture",
            "helper:memory-fixture",
            "config:memory-fixture",
        ),
        "run_mode": "existing_debug_native_source_driver",
        "notes": (
            "Run with --use-existing-deployment; CI imports deployed bundled-cli/scripts/relay/feishu_relay.py.",
            "Message and card actions call create_message_handler/create_card_callback_handler through source-driver test doubles.",
            "Do not use /api/ci/feishu_message, /api/ci/card_callback, daemon-only injection, package, reset, deploy, bootstrap, VSIX install, or relay restart.",
            "Strict product contract gaps are reported as product_bug evidence; driver gaps are ci_capability_gap_real_feishu_ingress_driver.",
        ),
    },
)


def run_f_real_feishu_ingress_slash_card_callback_contract(case: Any) -> None:
    self = case
    ctx: dict[str, Any] = {}
    state: dict[str, Any] = {"product_bug_findings": []}

    def config_snapshot() -> dict[str, Any]:
        chat_id = ctx["chat_id"]
        return {
            "trigger_mode": ctx["module"].chat_config.get_trigger_mode(chat_id),
            "detail_mode": ctx["relay_ws"].detail_modes.get(chat_id, "full"),
            "no_collapse_mode": ctx["relay_ws"].no_collapse_modes.get(chat_id, "on"),
            "chat_description": (ctx["api"].chat_info.get(chat_id) or {}).get("description", ""),
        }

    def reply_text(result: dict[str, Any]) -> str:
        return "\n".join(item.get("text", "") for item in result.get("new_replies") or [])

    def product_bug_detail(handler_evidence: dict[str, Any]) -> dict[str, Any]:
        findings = list(state.get("product_bug_findings") or [])
        finding_summaries = [
            {
                "name": item.get("name"),
                "expected_behavior": item.get("expected_behavior"),
                "actual_behavior": item.get("actual_behavior"),
                "failure_classification": item.get("failure_classification"),
                "handler_evidence": item.get("handler_evidence"),
            }
            for item in findings
            if isinstance(item, dict)
        ]
        detail = {
            "findings": findings,
            "finding_summaries": finding_summaries,
            "count": len(findings),
            "driver_handler_evidence": handler_evidence,
        }
        self.require_product_bug_evidence("f0041_product_bug_aggregate", not findings, detail)
        return detail

    def s01_reset_case_namespace() -> dict[str, Any]:
        suffix = self.run_token
        ctx.update(self.mock_feishu.relay_driver_context_for_remote(self,
            case_label="real_feishu_ingress",
            mapped=True,
            project=f"{self.resource_namespace}_project_{suffix}",
            intern=f"intern_{self.resource_namespace}_codex_{suffix}",
            chat_id=f"oc_{self.resource_namespace}_group_{suffix}",
            owner_open_id=f"ou_{self.resource_namespace}_owner_{suffix}",
            non_owner_open_id=f"ou_{self.resource_namespace}_non_owner_{suffix}",
        ))
        missing = self.mock_feishu.missing_handler_entrypoints(ctx["module"])
        if missing:
            raise NativeCaseError(
                "ci_capability_gap_real_feishu_ingress_driver: deployed relay source lacks handler entrypoints",
                details={
                    "missing": missing,
                    "deployed_source": self.artifacts.get("relay_source_driver", {}).get("source_path", ""),
                },
            )
        state["driver"] = self.mock_feishu.source_driver_metadata(
            namespace=self.resource_namespace,
            deployed_source=self.artifacts.get("relay_source_driver", {}).get("source_path", ""),
        )
        return state["driver"]

    def s02_seed_mapped_intern_group() -> dict[str, Any]:
        entry = ctx["registry"].find_entry_by_chat(ctx["chat_id"])
        seeded = {
            "project": ctx["project"],
            "intern": ctx["intern"],
            "chat": ctx["chat_id"],
            "owner_open_id": ctx["owner_open_id"],
            "mapped": bool(entry),
            "entry": entry,
        }
        self.require("f0041_mapped_group_seeded", bool(entry), seeded)
        state["seed"] = seeded
        return seeded

    def s03_real_config_message() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/config", sender_open_id=ctx["owner_open_id"])
        state["config_message"] = result
        return result

    def s04_wait_for_config_card() -> dict[str, Any]:
        cards = state["config_message"].get("new_cards") or []
        self.require("f0041_config_card_sent", bool(cards), state["config_message"])
        state["config_card"] = cards[-1]["card"]
        return {
            "card_message_id": cards[-1]["message_id"],
            "fields": self.mock_feishu.form_current_values(state["config_card"]),
            "card_text": self.mock_feishu.card_text(state["config_card"])[:2000],
        }

    def s05_owner_save_config_callback() -> dict[str, Any]:
        value = self.mock_feishu.config_value(state["config_card"])
        state["before_owner_save"] = config_snapshot()
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value=value,
            form_value={"trigger_mode": "all", "detail_mode": "summary", "no_collapse_mode": "off"},
            operator_open_id=ctx["owner_open_id"],
        )
        state["owner_save_value"] = value
        state["owner_save_response"] = response
        return {"value": value, "response": response}

    def s06_assert_config_saved_card_updated_owner_identity() -> dict[str, Any]:
        snapshot = config_snapshot()
        response = state["owner_save_response"]
        card_text = self.mock_feishu.card_text(response.get("card_data") or {})
        detail = {
            "before": state.get("before_owner_save"),
            "after": snapshot,
            "response": response,
            "owner_value": state.get("owner_save_value"),
            "result_card_text": card_text[:2000],
        }
        self.require(
            "f0041_owner_config_saved",
            snapshot["trigger_mode"] == "all" and snapshot["detail_mode"] == "summary" and snapshot["no_collapse_mode"] == "off",
            detail,
        )
        self.require(
            "f0041_config_result_card_updated",
            response.get("toast_type") == "success" and response.get("card_type") == "raw" and "配置已保存" in card_text,
            detail,
        )
        self.require(
            "f0041_config_card_bound_owner_identity",
            (state.get("owner_save_value") or {}).get("operator_open_id") == ctx["owner_open_id"],
            detail,
        )
        state["after_owner_save"] = snapshot
        return detail

    def s07_real_config_cancel_callback() -> dict[str, Any]:
        values = self.mock_feishu.card_values(state["config_card"])
        cancel_values = [
            value for value in values
            if str(value.get("config_action") or value.get("action") or "").lower() in {"cancel", "config_cancel"}
        ]
        state["before_cancel"] = config_snapshot()
        if not cancel_values:
            evidence = self.collect_product_bug_evidence(
                state,
                "product_bug_real_config_callback_contract",
                False,
                expected="/config card exposes a real cancel callback value that can be submitted through create_card_callback_handler.",
                actual="Deployed config card exposes only the save form submit value; no cancel callback value was found.",
                detail={"values": values, "card_text": self.mock_feishu.card_text(state["config_card"])[:2000]},
                handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                    {"function": "_build_config_card", "marker": '"action_type": "form_submit"', "label": "config card only has form submit"},
                    {"function": "create_card_callback_handler", "marker": 'value.get("config_action") == _CONFIG_CARD_ACTION', "label": "config callback handler save branch"},
                ]),
            )
            state["cancel_response"] = {"submitted": False, "reason": "cancel_value_missing", "product_bug_evidence": evidence}
            return state["cancel_response"]
        response = self.mock_feishu.relay_driver_card_action_for_remote(self, ctx, value=cancel_values[0], operator_open_id=ctx["owner_open_id"])
        state["cancel_response"] = {"submitted": True, "value": cancel_values[0], "response": response}
        return state["cancel_response"]

    def s08_assert_cancel_no_mutation() -> dict[str, Any]:
        after = config_snapshot()
        detail = {"before": state.get("before_cancel"), "after": after, "cancel_response": state.get("cancel_response")}
        self.require("f0041_cancel_no_mutation", after == state.get("before_cancel"), detail)
        return detail

    def s09_non_owner_save_config_callback() -> dict[str, Any]:
        owner_card = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/config", sender_open_id=ctx["owner_open_id"])
        card = owner_card["new_cards"][-1]["card"]
        value = self.mock_feishu.config_value(card)
        state["before_non_owner_save"] = config_snapshot()
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value=value,
            form_value={"trigger_mode": "at_only", "detail_mode": "full", "no_collapse_mode": "on"},
            operator_open_id=ctx["non_owner_open_id"],
        )
        state["non_owner_save"] = {"owner_card": owner_card, "value": value, "response": response}
        return state["non_owner_save"]

    def s10_assert_non_owner_rbac_rejected() -> dict[str, Any]:
        after = config_snapshot()
        detail = {"before": state.get("before_non_owner_save"), "after": after, "non_owner_save": state.get("non_owner_save")}
        self.require(
            "f0041_non_owner_config_callback_rejected",
            (state["non_owner_save"]["response"] or {}).get("toast_type") == "error",
            detail,
        )
        self.require("f0041_non_owner_config_unchanged", after == state.get("before_non_owner_save"), detail)
        return detail

    def s11_real_helper_message() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/helper", sender_open_id=ctx["owner_open_id"])
        state["bare_helper"] = result
        return result

    def s12_assert_helper_usage_reply() -> dict[str, Any]:
        text = reply_text(state["bare_helper"])
        detail = {"bare_helper": state["bare_helper"], "reply_text": text}
        self.require(
            "f0041_bare_helper_usage_no_card",
            bool(text) and "helper" in text.lower() and not state["bare_helper"].get("new_cards") and not state["bare_helper"].get("new_payloads"),
            detail,
        )
        return detail

    def s13_real_helper_start_message() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/helper start", sender_open_id=ctx["owner_open_id"])
        state["helper_start_message"] = result
        return result

    def s14_wait_for_helper_card() -> dict[str, Any]:
        cards = state["helper_start_message"].get("new_cards") or []
        self.require("f0041_helper_start_card_sent", bool(cards), state["helper_start_message"])
        state["helper_card"] = cards[-1]["card"]
        return {"card_message_id": cards[-1]["message_id"], "card_text": self.mock_feishu.card_text(state["helper_card"])[:2000]}

    def s15_helper_start_card_callback() -> dict[str, Any]:
        values = [
            value for value in self.mock_feishu.card_values(state["helper_card"])
            if value.get("helper_action") == "start"
        ]
        self.require("f0041_helper_start_action_value_present", bool(values), {"values": self.mock_feishu.card_values(state["helper_card"])})
        before_payloads = len(ctx["relay_ws"].sent_payloads)
        response = self.mock_feishu.relay_driver_card_action_for_remote(self, ctx, value=values[0], operator_open_id=ctx["owner_open_id"])
        state["helper_start_callback"] = {
            "value": values[0],
            "response": response,
            "new_payloads": ctx["relay_ws"].sent_payloads[before_payloads:],
        }
        return state["helper_start_callback"]

    def s16_assert_helper_action_routed_to_machine() -> dict[str, Any]:
        payloads = state["helper_start_callback"].get("new_payloads") or []
        helper = ctx["registry"].get_machine_helper("debug-a")
        detail = {"payloads": payloads, "helper": helper, "response": state["helper_start_callback"].get("response")}
        self.require(
            "f0041_helper_card_callback_routed",
            any((item.get("payload") or {}).get("type") == "helper_action" and (item.get("payload") or {}).get("helper_action") == "start" for item in payloads),
            detail,
        )
        self.require("f0041_helper_registry_updated", helper.get("status") in {"starting", "running"}, detail)
        return detail

    def s17_main_bot_p2p_status_message() -> dict[str, Any]:
        before_created = len(ctx["api"].created_chats)
        result = self.mock_feishu.relay_driver_message_for_remote(self,
            ctx,
            text="/status",
            chat_id=f"ou_{self.resource_namespace}_main_bot_{self.run_token}",
            sender_open_id=ctx["owner_open_id"],
            chat_type="p2p",
        )
        state["main_bot_status"] = result
        state["main_bot_created_delta"] = len(ctx["api"].created_chats) - before_created
        return result

    def s18_assert_main_bot_status_reply() -> dict[str, Any]:
        text = reply_text(state["main_bot_status"])
        detail = {
            "reply_text": text,
            "main_bot_status": state["main_bot_status"],
            "created_chat_delta": state.get("main_bot_created_delta"),
        }
        self.require(
            "f0041_main_bot_status_readonly_reply",
            "Machines:" in text and "connected" in text and not state["main_bot_status"].get("new_payloads") and state.get("main_bot_created_delta") == 0,
            detail,
        )
        return detail

    def s19_unmapped_group_status_message() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self,
            ctx,
            text="/status",
            chat_id=f"oc_{self.resource_namespace}_unmapped_{self.run_token}",
            sender_open_id=ctx["owner_open_id"],
            chat_type="group",
        )
        state["unmapped_group_status"] = result
        return result

    def s20_assert_unmapped_group_policy() -> dict[str, Any]:
        result = state["unmapped_group_status"]
        replies = result.get("new_replies") or []
        detail = {"unmapped_group_status": result, "reply_count": len(replies), "reply_text": reply_text(result)}
        self.require(
            "f0041_unmapped_group_non_spam_no_intern_injection",
            len(replies) <= 1 and not result.get("new_payloads") and not result.get("new_cards"),
            detail,
        )
        return detail

    def s21_unknown_slash_message() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/unknown_f0041", sender_open_id=ctx["owner_open_id"])
        state["unknown_slash"] = result
        return result

    def s22_assert_unknown_slash_help_not_injected() -> dict[str, Any]:
        result = state["unknown_slash"]
        text = reply_text(result)
        no_injection = not result.get("new_payloads")
        evidence = self.collect_product_bug_evidence(
            state,
            "product_bug_unknown_slash_injected",
            bool(text) and no_injection,
            expected="Mapped unknown slash returns a help/fallback reply and is not injected into the intern/daemon prompt path.",
            actual="Mapped unknown slash was injected into the daemon as a feishu_message payload." if result.get("new_payloads") else f"Reply text was {text!r}.",
            detail={"unknown_slash": result, "reply_text": text},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "create_message_handler", "marker": "# Route: chat_id", "label": "mapped chat route branch"},
                {"function": "create_message_handler", "marker": "_send_to_machine_with_reason", "label": "daemon feishu_message send"},
            ]),
        )
        return {"product_bug_evidence": evidence}

    def s23_assert_driver_is_real_relay_handler() -> dict[str, Any]:
        evidence = self.mock_feishu.relay_driver_handler_evidence(ctx, [
            {"function": "create_message_handler", "marker": "def handle_message", "label": "real im.message handler entrypoint"},
            {"function": "create_message_handler", "marker": 'text.strip() == "/config"', "label": "relay-local /config branch"},
            {"function": "create_card_callback_handler", "marker": "def handle_card_action", "label": "real card callback handler entrypoint"},
            {"function": "create_card_callback_handler", "marker": 'value.get("config_action") == _CONFIG_CARD_ACTION', "label": "relay-local config callback branch"},
            {"function": "_handle_helper_command", "label": "relay-local /helper branch"},
            {"function": "_handle_helper_card_action", "marker": '"type": "helper_action"', "label": "relay-local helper card callback branch"},
            {"function": "_handle_unmapped_main_bot_message", "label": "main bot readonly branch"},
        ])
        source_path = evidence.get("deployed_source_path", "")
        detail = {
            "driver": state.get("driver"),
            "handler_evidence": evidence,
            "message_events": [state.get("config_message"), state.get("bare_helper"), state.get("main_bot_status"), state.get("unknown_slash")],
            "card_callbacks": [state.get("owner_save_response"), state.get("cancel_response"), state.get("non_owner_save"), state.get("helper_start_callback")],
        }
        self.require(
            "f0041_driver_is_deployed_relay_source_handler",
            source_path.endswith("scripts/relay/feishu_relay.py") and all(
                ref.get("line_start") for ref in evidence.get("references", [])[:3]
            ),
            detail,
        )
        state["driver_handler_evidence"] = evidence
        product_bug_detail(evidence)
        return detail

    self.run_ordered_scenarios([
        ("F_0041.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0041.s02_seed_mapped_intern_group", s02_seed_mapped_intern_group),
        ("F_0041.s03_real_config_message", s03_real_config_message),
        ("F_0041.s04_wait_for_config_card", s04_wait_for_config_card),
        ("F_0041.s05_owner_save_config_callback", s05_owner_save_config_callback),
        ("F_0041.s06_config_saved_card_updated_owner_identity", s06_assert_config_saved_card_updated_owner_identity),
        ("F_0041.s07_real_config_cancel_callback", s07_real_config_cancel_callback),
        ("F_0041.s08_cancel_no_mutation", s08_assert_cancel_no_mutation),
        ("F_0041.s09_non_owner_save_config_callback", s09_non_owner_save_config_callback),
        ("F_0041.s10_card_callback_rbac_rejected", s10_assert_non_owner_rbac_rejected),
        ("F_0041.s11_real_helper_message", s11_real_helper_message),
        ("F_0041.s12_helper_usage_reply", s12_assert_helper_usage_reply),
        ("F_0041.s13_real_helper_start_message", s13_real_helper_start_message),
        ("F_0041.s14_wait_for_helper_card", s14_wait_for_helper_card),
        ("F_0041.s15_helper_start_card_callback", s15_helper_start_card_callback),
        ("F_0041.s16_helper_action_routed_to_machine", s16_assert_helper_action_routed_to_machine),
        ("F_0041.s17_main_bot_p2p_status_message", s17_main_bot_p2p_status_message),
        ("F_0041.s18_main_bot_status_reply", s18_assert_main_bot_status_reply),
        ("F_0041.s19_unmapped_group_status_message", s19_unmapped_group_status_message),
        ("F_0041.s20_unmapped_group_non_spam_policy", s20_assert_unmapped_group_policy),
        ("F_0041.s21_unknown_slash_message", s21_unknown_slash_message),
        ("F_0041.s22_unknown_slash_help_not_injected", s22_assert_unknown_slash_help_not_injected),
        ("F_0041.s23_driver_is_real_relay_handler", s23_assert_driver_is_real_relay_handler),
    ])
    self.artifacts["real_feishu_ingress_slash_card_callback_contract"] = state
