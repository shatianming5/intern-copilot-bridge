from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0018.s01_reset_case_namespace",
    "F_0018.s02_seed_group",
    "F_0018.s03_send_helper_slash",
    "F_0018.s04_wait_for_helper_card",
    "F_0018.s05_click_select_machine",
    "F_0018.s06_helper_machine_selected",
    "F_0018.s07_click_set_detail_mode",
    "F_0018.s08_helper_detail_mode_saved",
    "F_0018.s09_click_start",
    "F_0018.s10_wait_helper_running",
    "F_0018.s11_helper_running_on_machine",
    "F_0018.s12_click_stop",
    "F_0018.s13_wait_helper_stopped",
    "F_0018.s14_helper_stopped",
    "F_0018.s15_product_bug_aggregate",
)


CASE = CaseDefinition(
    id="F_0018_helper_machine_detail_stop",
    name="Helper machine detail mode and stop",
    description=(
        "Strictly validates helper machine selection, helper detail mode persistence, "
        "start-on-selected-machine, and stop behavior through deployed helper handlers."
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
            "helper.service_start",
            "helper.service_stop",
            "helper.service_unrouteable_after_stop",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": ("ctx.require", "native.helper_service_lifecycle", "native.helper_stop_blocks_routing"),
        "resource_locks": (
            {"resource": "feishu_chat:ci_f_0018", "mode": "exclusive"},
            {"resource": "fixture:ci_f_0018:helper:memory-fixture", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0018", "mode": "exclusive"},
            {"resource": "source_driver:deployed-feishu-relay", "mode": "read"},
        ),
        "resources": (
            "namespace:ci_f_0018",
            "source-driver:deployed-feishu-relay",
            "relay-chat:case-scoped",
            "helper:memory-fixture",
        ),
        "run_mode": "existing_debug_native_source_driver",
        "notes": (
            "Missing select_machine or helper set_detail_mode action is product_bug evidence.",
            "Do not implement a shadow action layer in CI.",
        ),
    },
)


def run_f_helper_machine_detail_stop(case: Any) -> None:
    self = case
    ctx: dict[str, Any] = {}
    state: dict[str, Any] = {}

    def s01_reset_case_namespace() -> dict[str, Any]:
        ctx.update(self.mock_feishu.relay_driver_context_for_remote(self, case_label="helper_detail", mapped=True))
        return {"source_driver_only": True, "namespace": self.resource_namespace}

    def s02_seed_group() -> dict[str, Any]:
        state["seed"] = {"project": ctx["project"], "intern": ctx["intern"], "chat": ctx["chat_id"]}
        return state["seed"]

    def s03_send_helper_slash() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/helper")
        state["bare_helper"] = result
        return result

    def s04_wait_for_helper_card() -> dict[str, Any]:
        card_result = state.get("bare_helper")
        if not (card_result or {}).get("new_cards"):
            card_result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/helper start")
            state["start_card_probe"] = card_result
        cards = (card_result or {}).get("new_cards") or []
        self.require("f0018_helper_action_card_available", bool(cards), {"bare_helper": state.get("bare_helper"), "start_card_probe": state.get("start_card_probe")})
        state["helper_card"] = cards[-1]["card"]
        return {"card": self.mock_feishu.card_text(state["helper_card"])[:2000], "bare_helper_card_missing": not bool(state.get("bare_helper", {}).get("new_cards"))}

    def s05_click_select_machine() -> dict[str, Any]:
        values = self.mock_feishu.card_values(state["helper_card"])
        actions = {str(value.get("helper_action") or value.get("action") or "") for value in values}
        debug_b_start = [
            value
            for value in values
            if value.get("helper_action") == "start" and value.get("machine_id") == "debug-b"
        ]
        detail = {
            "actions": sorted(actions),
            "values": values,
            "selection_contract": "machine can be selected either by an explicit select_machine action or by a machine-scoped start action",
            "bare_helper": state.get("bare_helper"),
            "start_card_probe": state.get("start_card_probe"),
        }
        self.require("f0018_helper_machine_selection_surface", "select_machine" in actions or bool(debug_b_start), detail)
        return detail

    def s06_assert_machine_selected() -> dict[str, Any]:
        helper = ctx["registry"].get_machine_helper("debug-b")
        self.require("f0018_helper_machine_selected", helper.get("machine_id") == "debug-b", helper)
        return helper

    def s07_click_set_detail_mode() -> dict[str, Any]:
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value={"helper_action": "set_detail_mode", "machine_id": "debug-b", "operator_open_id": ctx["owner_open_id"]},
            form_value={"detail_mode": "detailed"},
        )
        state["detail_response"] = response
        return response

    def s08_assert_detail_mode_saved() -> dict[str, Any]:
        response = state.get("detail_response", {})
        evidence = self.collect_product_bug_evidence(
            state,
            "f0018_product_bug_set_detail_mode_action",
            response.get("toast_type") == "success",
            expected="Helper set_detail_mode card action persists helper detail mode and returns success.",
            actual=f"set_detail_mode response toast_type={response.get('toast_type')!r}, toast_i18n={response.get('toast_i18n')!r}.",
            detail={"set_detail_mode_response": response},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_handle_helper_card_action", "marker": 'policy_action = _helper_policy_action_for_card_action(action_name)', "label": "helper card action dispatch"},
                {"function": "_helper_policy_action_for_card_action", "marker": 'raise ValueError(f"unknown helper_action', "label": "unknown helper_action rejection"},
            ]),
        )
        return {"product_bug_evidence": evidence}

    def s09_click_start() -> dict[str, Any]:
        values = [value for value in self.mock_feishu.card_values(state["helper_card"]) if value.get("helper_action") == "start" and value.get("machine_id") == "debug-b"]
        self.require("f0018_start_debug_b_action_present", bool(values), {"values": self.mock_feishu.card_values(state["helper_card"])})
        response = self.mock_feishu.relay_driver_card_action_for_remote(self, ctx, value=values[0])
        state["start_response"] = response
        return response

    def s10_wait_helper_running() -> dict[str, Any]:
        helper = ctx["registry"].get_machine_helper("debug-b")
        self.require("f0018_helper_running_debug_b", helper.get("status") in {"starting", "running"}, helper)
        return helper

    def s11_assert_running_on_machine() -> dict[str, Any]:
        helper = ctx["registry"].get_machine_helper("debug-b")
        self.require("f0018_helper_running_on_selected_machine", helper.get("machine_id") == "debug-b", helper)
        return helper

    def s12_click_stop() -> dict[str, Any]:
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value={"helper_action": "stop", "machine_id": "debug-b", "operator_open_id": ctx["owner_open_id"], "helper_id": "machine_helper_debug-b"},
        )
        state["stop_response"] = response
        return response

    def s13_wait_stopped() -> dict[str, Any]:
        helper = ctx["registry"].get_machine_helper("debug-b")
        self.require("f0018_helper_stopped", helper.get("status") == "stopped", helper)
        return helper

    def s14_assert_stopped_unrouted() -> dict[str, Any]:
        helper = ctx["registry"].get_machine_helper("debug-b")
        self.require("f0018_helper_no_route_after_stop", helper.get("status") == "stopped", helper)
        return helper

    def s15_assert_product_bug_findings() -> dict[str, Any]:
        return self.aggregate_product_bug_findings(state, "f0018_product_bug_aggregate")

    self.run_ordered_scenarios([
        ("F_0018.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0018.s02_seed_group", s02_seed_group),
        ("F_0018.s03_send_helper_slash", s03_send_helper_slash),
        ("F_0018.s04_wait_for_helper_card", s04_wait_for_helper_card),
        ("F_0018.s05_click_select_machine", s05_click_select_machine),
        ("F_0018.s06_helper_machine_selected", s06_assert_machine_selected),
        ("F_0018.s07_click_set_detail_mode", s07_click_set_detail_mode),
        ("F_0018.s08_helper_detail_mode_saved", s08_assert_detail_mode_saved),
        ("F_0018.s09_click_start", s09_click_start),
        ("F_0018.s10_wait_helper_running", s10_wait_helper_running),
        ("F_0018.s11_helper_running_on_machine", s11_assert_running_on_machine),
        ("F_0018.s12_click_stop", s12_click_stop),
        ("F_0018.s13_wait_helper_stopped", s13_wait_stopped),
        ("F_0018.s14_helper_stopped", s14_assert_stopped_unrouted),
        ("F_0018.s15_product_bug_aggregate", s15_assert_product_bug_findings),
    ])
    self.artifacts["helper_machine_detail_stop"] = state
