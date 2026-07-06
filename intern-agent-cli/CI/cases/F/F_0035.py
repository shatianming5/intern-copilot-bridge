from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0035.s01_reset_case_namespace",
    "F_0035.s02_seed_group",
    "F_0035.s03_send_config_slash",
    "F_0035.s04_wait_for_config_card",
    "F_0035.s05_config_card_actions",
    "F_0035.s06_capture_group_config",
    "F_0035.s07_click_config_cancel",
    "F_0035.s08_config_unchanged_after_cancel",
    "F_0035.s09_config_card_cancel_state",
    "F_0035.s10_submit_config_save",
    "F_0035.s11_config_saved",
    "F_0035.s12_product_bug_aggregate",
)


CASE = CaseDefinition(
    id="F_0035_config_card_cancel_no_mutation_contract",
    name="Config card cancel no-mutation contract",
    description=(
        "Uses the deployed relay source-driver to validate /config card Save+Cancel "
        "controls, Cancel no-mutation behavior, and the still-working Save path."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_daemon_relay_api",
    tags=("F", "slash", "config", "relay", "card"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "feishu_mock.send_text",
            "feishu_mock.click_card",
            "feishu_mock.submit_card_form",
            "relay.group_config_card_submit",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": ("ctx.require", "ctx.equals"),
        "resource_locks": (
            {"resource": "feishu_chat:ci_f_0035", "mode": "exclusive"},
            {"resource": "fixture:ci_f_0035:config:memory-fixture", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0035_codex", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0035", "mode": "exclusive"},
            {"resource": "project:axis_intern_agents_backup:ci_f_0035_project", "mode": "exclusive"},
            {"resource": "source_driver:deployed-feishu-relay", "mode": "read"},
            {"resource": "task:axis_intern_agents_backup:task_ci_f_0035", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0035",
            "project:ci_f_0035_project",
            "intern:intern_ci_f_0035_codex",
            "task:task_ci_f_0035",
            "source-driver:deployed-feishu-relay",
            "relay-chat:case-scoped",
            "config:memory-fixture",
        ),
        "run_mode": "existing_debug_native_source_driver",
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/reset/deploy/bootstrap/restart relay.",
            "Missing /config Cancel, Cancel mutation, or a live stale submit after Cancel is product_bug evidence.",
        ),
    },
)


def run_f_config_card_cancel_no_mutation_contract(case: Any) -> None:
    self = case
    ctx: dict[str, Any] = {}
    state: dict[str, Any] = {"product_bug_findings": []}

    def s01_reset_case_namespace() -> dict[str, Any]:
        ctx.update(self.mock_feishu.relay_driver_context_for_remote(self, case_label="config_cancel", mapped=True))
        state["reset"] = {
            "source_driver_only": True,
            "namespace": self.resource_namespace,
            "deployed_source": self.artifacts.get("relay_source_driver", {}).get("source_path", ""),
        }
        return state["reset"]

    def s02_seed_group() -> dict[str, Any]:
        state["seed"] = {"project": ctx["project"], "intern": ctx["intern"], "chat": ctx["chat_id"], "mapped": True}
        return state["seed"]

    def s03_send_config_slash() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/config")
        state["config_slash"] = result
        return result

    def s04_wait_for_config_card() -> dict[str, Any]:
        cards = state.get("config_slash", {}).get("new_cards") or []
        self.require("f0035_config_card_sent", bool(cards), state.get("config_slash"))
        state["config_card"] = cards[-1]["card"]
        return {"card_message_id": cards[-1]["message_id"], "card_text": self.mock_feishu.card_text(state["config_card"])[:2000]}

    def s05_assert_config_card_actions() -> dict[str, Any]:
        summary = self.mock_feishu.card_action_summary(state["config_card"])
        expected = {"save", "cancel"}
        evidence = self.collect_product_bug_evidence(
            state,
            "product_bug_config_card_missing_cancel",
            expected.issubset(set(summary["actions"])),
            expected="/config card exposes Save and Cancel actions.",
            actual=f"/config semantic card actions are {summary['actions']!r}.",
            detail=summary,
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_build_config_card", "marker": '"name": "submit"', "label": "/config save button"},
                {"function": "_build_config_card", "marker": "取消", "label": "/config cancel button"},
                {"function": "create_card_callback_handler", "marker": 'value.get("config_action")', "label": "/config callback dispatch"},
            ]),
        )
        return {"expected": sorted(expected), **summary, "product_bug_evidence": evidence}

    def s06_capture_group_config() -> dict[str, Any]:
        state["before_cancel"] = self.mock_feishu.config_snapshot(ctx)
        return state["before_cancel"]

    def s07_click_config_cancel() -> dict[str, Any]:
        cancel_value = self.mock_feishu.find_card_action_value(state["config_card"], "cancel")
        state["cancel_value"] = cancel_value
        state["cancel_card_update_count_before"] = len(ctx["api"].card_updates)
        if not cancel_value:
            state["cancel_response"] = {"skipped": True, "reason": "config card has no cancel action"}
            return {"cancel_value_present": False, "response": state["cancel_response"]}
        response = self.mock_feishu.relay_driver_card_action_for_remote(self, ctx, value=cancel_value)
        state["cancel_response"] = response
        return {"cancel_value_present": True, "response": response}

    def s08_assert_config_unchanged_after_cancel() -> dict[str, Any]:
        before = state["before_cancel"]
        after = self.mock_feishu.config_snapshot(ctx)
        state["after_cancel"] = after
        comparable_keys = ("trigger_mode", "detail_mode", "no_collapse_mode", "sent_payload_count")
        unchanged = all(before.get(key) == after.get(key) for key in comparable_keys)
        evidence = self.collect_product_bug_evidence(
            state,
            "product_bug_config_cancel_mutates_state",
            unchanged,
            expected="/config Cancel does not mutate trigger/detail/no-collapse state and does not send daemon payloads.",
            actual=f"Before Cancel {before!r}, after Cancel {after!r}.",
            detail={"before": before, "after": after, "cancel_response": state.get("cancel_response")},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "create_card_callback_handler", "marker": 'value.get("config_action")', "label": "/config callback dispatch"},
                {"function": "_handle_config_card_submit", "label": "/config save mutation path"},
            ]),
        )
        return {"before": before, "after": after, "product_bug_evidence": evidence}

    def s09_assert_config_card_cancel_state() -> dict[str, Any]:
        stale_submit: dict[str, Any] = {}
        after_stale = self.mock_feishu.config_snapshot(ctx)
        cancel_value = state.get("cancel_value") or {}
        if cancel_value:
            save_value = self.mock_feishu.config_value(state["config_card"])
            stale_submit = self.mock_feishu.relay_driver_card_action_for_remote(self,
                ctx,
                value=save_value,
                form_value={"trigger_mode": "at_only", "detail_mode": "summary", "no_collapse_mode": "off"},
            )
            after_stale = self.mock_feishu.config_snapshot(ctx)
        before = state["after_cancel"]
        stale_blocked = bool(cancel_value) and all(
            before.get(key) == after_stale.get(key)
            for key in ("trigger_mode", "detail_mode", "no_collapse_mode", "sent_payload_count")
        )
        evidence = self.collect_product_bug_evidence(
            state,
            "product_bug_config_cancel_leaves_submit_live",
            stale_blocked,
            expected="/config Cancel closes or invalidates the old form so a stale Save cannot mutate config.",
            actual=(
                "No Cancel action exists, so no canceled-card state can be asserted."
                if not cancel_value else f"Stale submit response {stale_submit!r}, state after stale submit {after_stale!r}."
            ),
            detail={
                "cancel_value_present": bool(cancel_value),
                "cancel_response": state.get("cancel_response"),
                "stale_submit": stale_submit,
                "before_stale": before,
                "after_stale": after_stale,
                "card_updates": ctx["api"].card_updates[state.get("cancel_card_update_count_before", 0):],
            },
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_build_config_card", "marker": "取消", "label": "/config cancel button"},
                {"function": "create_card_callback_handler", "marker": 'value.get("config_action")', "label": "/config callback dispatch"},
                {"function": "_build_config_result_card", "label": "/config post-save card update"},
            ]),
        )
        return {"product_bug_evidence": evidence}

    def s10_submit_config_save() -> dict[str, Any]:
        card = state["config_card"]
        if state.get("cancel_value"):
            reopened = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/config")
            self.require("f0035_config_reopen_after_cancel_for_save", bool(reopened.get("new_cards")), reopened)
            card = reopened["new_cards"][-1]["card"]
            state["save_card"] = card
        value = self.mock_feishu.config_value(card)
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value=value,
            form_value={"trigger_mode": "at_only", "detail_mode": "summary", "no_collapse_mode": "off"},
        )
        state["save_response"] = response
        return response

    def s11_assert_config_saved() -> dict[str, Any]:
        snapshot = self.mock_feishu.config_snapshot(ctx)
        state["after_save"] = snapshot
        self.require(
            "f0035_config_save_still_persists",
            snapshot["trigger_mode"] == "at_only" and snapshot["detail_mode"] == "summary" and snapshot["no_collapse_mode"] == "off",
            {"snapshot": snapshot, "save_response": state.get("save_response")},
        )
        return {"snapshot": snapshot, "save_response": state.get("save_response")}

    def s12_assert_product_bug_findings() -> dict[str, Any]:
        return self.aggregate_product_bug_findings(state, "f0035_product_bug_aggregate")

    self.run_ordered_scenarios([
        ("F_0035.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0035.s02_seed_group", s02_seed_group),
        ("F_0035.s03_send_config_slash", s03_send_config_slash),
        ("F_0035.s04_wait_for_config_card", s04_wait_for_config_card),
        ("F_0035.s05_config_card_actions", s05_assert_config_card_actions),
        ("F_0035.s06_capture_group_config", s06_capture_group_config),
        ("F_0035.s07_click_config_cancel", s07_click_config_cancel),
        ("F_0035.s08_config_unchanged_after_cancel", s08_assert_config_unchanged_after_cancel),
        ("F_0035.s09_config_card_cancel_state", s09_assert_config_card_cancel_state),
        ("F_0035.s10_submit_config_save", s10_submit_config_save),
        ("F_0035.s11_config_saved", s11_assert_config_saved),
        ("F_0035.s12_product_bug_aggregate", s12_assert_product_bug_findings),
    ])
    self.artifacts["config_card_cancel_no_mutation_contract"] = state
