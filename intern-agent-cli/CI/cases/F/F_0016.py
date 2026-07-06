from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0016.s01_reset_case_namespace",
    "F_0016.s02_seed_group",
    "F_0016.s03_send_config_slash",
    "F_0016.s04_wait_for_config_card",
    "F_0016.s05_config_card_fields",
    "F_0016.s05_1_config_card_actions",
    "F_0016.s05_2_cancel_config_card",
    "F_0016.s05_3_config_unchanged_after_cancel",
    "F_0016.s06_submit_full_config",
    "F_0016.s07_config_saved_full",
    "F_0016.s08_reopen_config",
    "F_0016.s09_wait_for_second_config_card",
    "F_0016.s10_config_card_current_values",
    "F_0016.s11_submit_updated_config",
    "F_0016.s12_config_saved_updated",
    "F_0016.s13_daemon_group_mode_synced",
    "F_0016.s14_relay_group_mode_synced",
    "F_0016.s15_product_bug_aggregate",
)


CASE = CaseDefinition(
    id="F_0016_slash_config_mode_persistence",
    name="Slash config mode persistence",
    description=(
        "Uses the deployed relay source-driver to exercise /config message and card "
        "submit handlers for trigger, detail, and no-collapse mode persistence."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_daemon_relay_api",
    tags=("F", "slash", "config", "relay"),
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
            {"resource": "feishu_chat:ci_f_0016", "mode": "exclusive"},
            {"resource": "fixture:ci_f_0016:config:memory-fixture", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0016", "mode": "exclusive"},
            {"resource": "source_driver:deployed-feishu-relay", "mode": "read"},
        ),
        "resources": (
            "namespace:ci_f_0016",
            "source-driver:deployed-feishu-relay",
            "relay-chat:case-scoped",
            "config:memory-fixture",
        ),
        "run_mode": "existing_debug_native_source_driver",
        "notes": (
            "Import deployed bundled-cli/scripts/relay/feishu_relay.py on debug machine.",
            "Use fake Feishu API/registry/relay websocket; do not use synthetic HTTP CI endpoints.",
            "Strictly require /config Save and Cancel; missing Cancel or Cancel mutation is product_bug evidence.",
        ),
    },
)


def run_f_slash_config_mode_persistence(case: Any) -> None:
    self = case
    ctx: dict[str, Any] = {}
    state: dict[str, Any] = {"product_bug_findings": []}

    def s01_reset_case_namespace() -> dict[str, Any]:
        ctx.update(self.mock_feishu.relay_driver_context_for_remote(self, case_label="config", mapped=True))
        state["reset"] = {
            "source_driver_only": True,
            "namespace": self.resource_namespace,
            "deployed_source": self.artifacts.get("relay_source_driver", {}).get("source_path", ""),
        }
        return state["reset"]

    def s02_seed_group() -> dict[str, Any]:
        seeded = {"project": ctx["project"], "intern": ctx["intern"], "chat": ctx["chat_id"], "mapped": True}
        state["seed"] = seeded
        return seeded

    def s03_send_config_slash() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/config")
        state["first_config_slash"] = result
        return result

    def s04_wait_for_config_card() -> dict[str, Any]:
        cards = ctx["api"].cards
        self.require("f0016_config_card_sent", bool(cards), {"slash": state.get("first_config_slash")})
        state["first_config_card"] = cards[-1]["card"]
        return {"card_message_id": cards[-1]["message_id"], "card_text": self.mock_feishu.card_text(cards[-1]["card"])[:2000]}

    def s05_assert_config_card_fields() -> dict[str, Any]:
        values = self.mock_feishu.form_current_values(state["first_config_card"])
        expected = {"trigger_mode", "detail_mode", "no_collapse_mode"}
        self.require("f0016_config_card_fields", expected.issubset(values.keys()), {"fields": values, "expected": sorted(expected)})
        return {"fields": values}

    def s05_1_assert_config_card_actions() -> dict[str, Any]:
        summary = self.mock_feishu.card_action_summary(state["first_config_card"])
        expected = {"save", "cancel"}
        evidence = self.collect_product_bug_evidence(
            state,
            "f0016_product_bug_config_card_save_cancel_actions",
            expected.issubset(set(summary["actions"])),
            expected="/config card exposes both Save and Cancel actions.",
            actual=f"/config semantic card actions are {summary['actions']!r}.",
            detail=summary,
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_build_config_card", "marker": '"name": "submit"', "label": "/config card save button"},
                {"function": "_build_config_card", "marker": "取消", "label": "/config card cancel button"},
                {"function": "create_card_callback_handler", "marker": 'value.get("config_action")', "label": "/config callback dispatch"},
            ]),
        )
        return {"expected": sorted(expected), **summary, "product_bug_evidence": evidence}

    def s05_2_cancel_config_card() -> dict[str, Any]:
        state["config_before_cancel"] = self.mock_feishu.config_snapshot(ctx)
        cancel_value = self.mock_feishu.find_card_action_value(state["first_config_card"], "cancel")
        state["config_cancel_value"] = cancel_value
        if not cancel_value:
            state["cancel_response"] = {"skipped": True, "reason": "config card has no cancel action"}
            return {"cancel_value_present": False, "before": state["config_before_cancel"], "response": state["cancel_response"]}
        response = self.mock_feishu.relay_driver_card_action_for_remote(self, ctx, value=cancel_value)
        state["cancel_response"] = response
        return {"cancel_value_present": True, "before": state["config_before_cancel"], "response": response}

    def s05_3_assert_config_unchanged_after_cancel() -> dict[str, Any]:
        before = state["config_before_cancel"]
        after = self.mock_feishu.config_snapshot(ctx)
        state["config_after_cancel"] = after
        comparable_keys = ("trigger_mode", "detail_mode", "no_collapse_mode", "sent_payload_count")
        unchanged = all(before.get(key) == after.get(key) for key in comparable_keys)
        evidence = self.collect_product_bug_evidence(
            state,
            "f0016_product_bug_config_cancel_mutates_state",
            unchanged,
            expected="/config Cancel leaves trigger/detail/no-collapse state and daemon payload count unchanged.",
            actual=f"Before Cancel {before!r}, after Cancel {after!r}.",
            detail={"before": before, "after": after, "cancel_response": state.get("cancel_response")},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "create_card_callback_handler", "marker": 'value.get("config_action")', "label": "/config callback dispatch"},
                {"function": "_handle_config_card_submit", "label": "/config save mutation path"},
            ]),
        )
        return {"before": before, "after": after, "product_bug_evidence": evidence}

    def s06_submit_full_config() -> dict[str, Any]:
        card = state["first_config_card"]
        if state.get("config_cancel_value"):
            reopen = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/config")
            self.require("f0016_config_reopen_after_cancel_for_save", bool(reopen.get("new_cards")), reopen)
            state["post_cancel_save_card"] = reopen["new_cards"][-1]["card"]
            card = state["post_cancel_save_card"]
        value = self.mock_feishu.config_value(card)
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value=value,
            form_value={"trigger_mode": "all", "detail_mode": "full", "no_collapse_mode": "on"},
        )
        state["first_submit"] = response
        return response

    def s07_assert_full_config_saved() -> dict[str, Any]:
        chat_id = ctx["chat_id"]
        detail = {
            "trigger_mode": ctx["module"].chat_config.get_trigger_mode(chat_id),
            "detail_mode": ctx["relay_ws"].detail_modes.get(chat_id, "full"),
            "no_collapse_mode": ctx["relay_ws"].no_collapse_modes.get(chat_id, "on"),
            "submit": state.get("first_submit"),
        }
        self.require(
            "f0016_full_config_saved",
            detail["trigger_mode"] == "all" and detail["detail_mode"] == "full" and detail["no_collapse_mode"] == "on",
            detail,
        )
        return detail

    def s08_reopen_config() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/config")
        state["second_config_slash"] = result
        return result

    def s09_wait_for_second_config_card() -> dict[str, Any]:
        cards = ctx["api"].cards
        self.require("f0016_second_config_card_sent", len(cards) >= 2, {"cards": cards})
        state["second_config_card"] = cards[-1]["card"]
        return {"card_message_id": cards[-1]["message_id"], "card_text": self.mock_feishu.card_text(cards[-1]["card"])[:2000]}

    def s10_assert_current_values() -> dict[str, Any]:
        values = self.mock_feishu.form_current_values(state["second_config_card"])
        expected = {"trigger_mode": "all", "detail_mode": "full", "no_collapse_mode": "on"}
        self.require("f0016_config_card_current_values", values == expected, {"values": values, "expected": expected})
        return {"values": values}

    def s11_submit_updated_config() -> dict[str, Any]:
        value = self.mock_feishu.config_value(state["second_config_card"])
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value=value,
            form_value={"trigger_mode": "at_only", "detail_mode": "summary", "no_collapse_mode": "off"},
        )
        state["second_submit"] = response
        return response

    def s12_assert_updated_config_saved() -> dict[str, Any]:
        chat_id = ctx["chat_id"]
        detail = {
            "trigger_mode": ctx["module"].chat_config.get_trigger_mode(chat_id),
            "detail_mode": ctx["relay_ws"].detail_modes.get(chat_id, "full"),
            "no_collapse_mode": ctx["relay_ws"].no_collapse_modes.get(chat_id, "on"),
            "submit": state.get("second_submit"),
        }
        self.require(
            "f0016_updated_config_saved",
            detail["trigger_mode"] == "at_only" and detail["detail_mode"] == "summary" and detail["no_collapse_mode"] == "off",
            detail,
        )
        return detail

    def s13_assert_daemon_modes_synced() -> dict[str, Any]:
        chat_id = ctx["chat_id"]
        detail = {"detail_mode": ctx["relay_ws"].detail_modes.get(chat_id), "no_collapse_mode": ctx["relay_ws"].no_collapse_modes.get(chat_id)}
        self.require("f0016_daemon_modes_synced", detail == {"detail_mode": "summary", "no_collapse_mode": "off"}, detail)
        return detail

    def s14_assert_relay_trigger_synced() -> dict[str, Any]:
        detail = {"trigger_mode": ctx["module"].chat_config.get_trigger_mode(ctx["chat_id"])}
        self.require("f0016_relay_trigger_synced", detail["trigger_mode"] == "at_only", detail)
        return detail

    def s15_assert_product_bug_findings() -> dict[str, Any]:
        return self.aggregate_product_bug_findings(state, "f0016_product_bug_aggregate")

    self.run_ordered_scenarios([
        ("F_0016.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0016.s02_seed_group", s02_seed_group),
        ("F_0016.s03_send_config_slash", s03_send_config_slash),
        ("F_0016.s04_wait_for_config_card", s04_wait_for_config_card),
        ("F_0016.s05_config_card_fields", s05_assert_config_card_fields),
        ("F_0016.s05_1_config_card_actions", s05_1_assert_config_card_actions),
        ("F_0016.s05_2_cancel_config_card", s05_2_cancel_config_card),
        ("F_0016.s05_3_config_unchanged_after_cancel", s05_3_assert_config_unchanged_after_cancel),
        ("F_0016.s06_submit_full_config", s06_submit_full_config),
        ("F_0016.s07_config_saved_full", s07_assert_full_config_saved),
        ("F_0016.s08_reopen_config", s08_reopen_config),
        ("F_0016.s09_wait_for_second_config_card", s09_wait_for_second_config_card),
        ("F_0016.s10_config_card_current_values", s10_assert_current_values),
        ("F_0016.s11_submit_updated_config", s11_submit_updated_config),
        ("F_0016.s12_config_saved_updated", s12_assert_updated_config_saved),
        ("F_0016.s13_daemon_group_mode_synced", s13_assert_daemon_modes_synced),
        ("F_0016.s14_relay_group_mode_synced", s14_assert_relay_trigger_synced),
        ("F_0016.s15_product_bug_aggregate", s15_assert_product_bug_findings),
    ])
    self.artifacts["slash_config_mode_persistence"] = state
