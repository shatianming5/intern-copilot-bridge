from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0017.s01_reset_case_namespace",
    "F_0017.s02_seed_group",
    "F_0017.s03_send_helper_slash",
    "F_0017.s04_wait_for_helper_usage_reply",
    "F_0017.s05_helper_usage_reply_contains",
    "F_0017.s06_send_helper_start",
    "F_0017.s07_wait_for_helper_card",
    "F_0017.s08_helper_card_actions",
    "F_0017.s09_click_helper_start",
    "F_0017.s10_wait_helper_status",
    "F_0017.s11_helper_started",
    "F_0017.s12_send_helper_status",
    "F_0017.s13_wait_helper_status_message",
    "F_0017.s14_helper_status_message_contains",
    "F_0017.s15_product_bug_aggregate",
)


CASE = CaseDefinition(
    id="F_0017_helper_slash_open_start_status",
    name="Helper slash open start status",
    description=(
        "Strictly validates bare /helper returns usage/help while /helper start "
        "opens the start card through the deployed relay helper handlers."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_daemon_relay_api",
    tags=("F", "slash", "helper", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "feishu_mock.send_text",
            "feishu_mock.click_card",
            "helper.service_status",
            "helper.service_start",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": ("ctx.require", "native.helper_service_lifecycle"),
        "resource_locks": (
            {"resource": "feishu_chat:ci_f_0017", "mode": "exclusive"},
            {"resource": "fixture:ci_f_0017:helper:memory-fixture", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0017", "mode": "exclusive"},
            {"resource": "source_driver:deployed-feishu-relay", "mode": "read"},
        ),
        "resources": (
            "namespace:ci_f_0017",
            "source-driver:deployed-feishu-relay",
            "relay-chat:case-scoped",
            "helper:memory-fixture",
        ),
        "run_mode": "existing_debug_native_source_driver",
        "notes": (
            "Bare /helper usage/help supersedes the old control-card expectation.",
            "Current deployed bare /helper status/no-visible-helper behavior is product_bug evidence, not a case skip.",
        ),
    },
)


def run_f_helper_slash_open_start_status(case: Any) -> None:
    self = case
    ctx: dict[str, Any] = {}
    state: dict[str, Any] = {"product_bug_findings": []}

    def s01_reset_case_namespace() -> dict[str, Any]:
        ctx.update(self.mock_feishu.relay_driver_context_for_remote(self, case_label="helper_open", mapped=True))
        return {"source_driver_only": True, "namespace": self.resource_namespace}

    def s02_seed_group() -> dict[str, Any]:
        state["seed"] = {"project": ctx["project"], "intern": ctx["intern"], "chat": ctx["chat_id"]}
        return state["seed"]

    def s03_send_helper_slash() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/helper")
        state["bare_helper"] = result
        return result

    def s04_wait_for_helper_usage_reply() -> dict[str, Any]:
        replies = state.get("bare_helper", {}).get("new_replies") or []
        cards = state.get("bare_helper", {}).get("new_cards") or []
        text = "\n".join(item.get("text", "") for item in replies)
        state["bare_helper_usage_text"] = text
        evidence = self.collect_product_bug_evidence(
            state,
            "f0017_product_bug_bare_helper_usage_reply",
            bool(replies) and not cards and "/helper start" in text and "/helper status" in text,
            expected="Bare /helper returns usage/help text and does not open a control card.",
            actual=(
                f"Bare /helper opened {len(cards)} card(s) and replied {text!r}."
                if cards else f"Bare /helper reply text was {text!r}."
            ),
            detail={"bare_helper": state.get("bare_helper"), "reply_text": text, "card_count": len(cards)},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_parse_helper_command", "marker": 'return {"action": "status"', "label": "bare /helper currently parses as status"},
                {"function": "_handle_helper_command", "marker": 'reply("当前没有可见 helper")', "label": "status path replies no visible helper"},
                {"function": "_main_bot_help_text", "marker": "`/helper start", "label": "usage/help command list"},
            ]),
        )
        return {"reply_text": text, "card_count": len(cards), "product_bug_evidence": evidence}

    def s05_assert_helper_usage_reply_contains() -> dict[str, Any]:
        text = state.get("bare_helper_usage_text", "")
        expected = ["/helper status", "/helper start", "/helper stop", "/helper invite-owner", "/helper migrate"]
        evidence = self.collect_product_bug_evidence(
            state,
            "f0017_product_bug_bare_helper_usage_tokens",
            all(token in text for token in expected),
            expected=f"Bare /helper usage reply contains {expected!r}.",
            actual=f"Bare /helper usage reply was {text!r}.",
            detail={"reply_text": text, "expected_tokens": expected},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_parse_helper_command", "label": "helper slash parser"},
                {"function": "_handle_helper_command", "label": "helper slash handler"},
            ]),
        )
        return {"reply_text": text, "expected_tokens": expected, "product_bug_evidence": evidence}

    def s06_send_helper_start() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/helper start")
        state["helper_start"] = result
        return result

    def s07_wait_for_helper_card() -> dict[str, Any]:
        cards = state.get("helper_start", {}).get("new_cards") or []
        self.require("f0017_helper_start_card_available", bool(cards), {"helper_start": state.get("helper_start")})
        state["helper_card"] = cards[-1]["card"]
        return {"card": self.mock_feishu.card_text(state["helper_card"])[:2000]}

    def s08_assert_helper_card_actions() -> dict[str, Any]:
        actions = {str(value.get("helper_action") or value.get("action") or "") for value in self.mock_feishu.card_values(state["helper_card"])}
        expected = {"start"}
        evidence = self.collect_product_bug_evidence(
            state,
            "f0017_product_bug_helper_card_actions",
            expected.issubset(actions),
            expected="/helper start card exposes a start action.",
            actual=f"Helper card actions are {sorted(actions)}.",
            detail={"actions": sorted(actions), "expected": sorted(expected), "values": self.mock_feishu.card_values(state["helper_card"])},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_send_helper_action_card", "label": "/helper start sends action card"},
                {"function": "_build_helper_action_card", "label": "helper start card construction"},
                {"function": "_helper_policy_action_for_card_action", "label": "supported helper card actions"},
            ]),
        )
        return {"actions": sorted(actions), "product_bug_evidence": evidence}

    def s09_click_start() -> dict[str, Any]:
        values = [value for value in self.mock_feishu.card_values(state["helper_card"]) if value.get("helper_action") == "start"]
        self.require("f0017_start_action_value_present", bool(values), {"values": self.mock_feishu.card_values(state["helper_card"])})
        response = self.mock_feishu.relay_driver_card_action_for_remote(self, ctx, value=values[0])
        state["start_response"] = response
        return response

    def s10_wait_helper_running() -> dict[str, Any]:
        helper = ctx["registry"].get_machine_helper("debug-a")
        self.require("f0017_helper_starting_or_running", helper.get("status") in {"starting", "running"}, helper)
        return helper

    def s11_assert_helper_started() -> dict[str, Any]:
        helper = ctx["registry"].get_machine_helper("debug-a")
        self.require("f0017_helper_bound_to_context", helper.get("machine_id") == "debug-a", {"helper": helper, "chat": ctx["chat_id"]})
        return helper

    def s12_send_helper_status() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/helper status debug-a")
        state["status_result"] = result
        return result

    def s13_wait_status_message() -> dict[str, Any]:
        replies = state.get("status_result", {}).get("new_replies") or []
        self.require("f0017_helper_status_reply_present", bool(replies), state.get("status_result"))
        return {"replies": replies}

    def s14_assert_status_message_contains() -> dict[str, Any]:
        text = "\n".join(item.get("text", "") for item in state.get("status_result", {}).get("new_replies") or [])
        self.require("f0017_helper_status_running", "running" in text or "starting" in text, {"reply_text": text})
        return {"reply_text": text}

    def s15_assert_product_bug_findings() -> dict[str, Any]:
        return self.aggregate_product_bug_findings(state, "f0017_product_bug_aggregate")

    self.run_ordered_scenarios([
        ("F_0017.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0017.s02_seed_group", s02_seed_group),
        ("F_0017.s03_send_helper_slash", s03_send_helper_slash),
        ("F_0017.s04_wait_for_helper_usage_reply", s04_wait_for_helper_usage_reply),
        ("F_0017.s05_helper_usage_reply_contains", s05_assert_helper_usage_reply_contains),
        ("F_0017.s06_send_helper_start", s06_send_helper_start),
        ("F_0017.s07_wait_for_helper_card", s07_wait_for_helper_card),
        ("F_0017.s08_helper_card_actions", s08_assert_helper_card_actions),
        ("F_0017.s09_click_helper_start", s09_click_start),
        ("F_0017.s10_wait_helper_status", s10_wait_helper_running),
        ("F_0017.s11_helper_started", s11_assert_helper_started),
        ("F_0017.s12_send_helper_status", s12_send_helper_status),
        ("F_0017.s13_wait_helper_status_message", s13_wait_status_message),
        ("F_0017.s14_helper_status_message_contains", s14_assert_status_message_contains),
        ("F_0017.s15_product_bug_aggregate", s15_assert_product_bug_findings),
    ])
    self.artifacts["helper_slash_open_start_status"] = state
