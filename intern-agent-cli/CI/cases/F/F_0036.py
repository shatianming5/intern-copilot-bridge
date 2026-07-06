from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0036.s01_reset_case_namespace",
    "F_0036.s02_seed_machine_config_schema",
    "F_0036.s03_open_machine_config_from_intern_group",
    "F_0036.s04_machine_config_target_resolved_intern_group",
    "F_0036.s05_open_machine_config_from_helper_group",
    "F_0036.s06_machine_config_target_resolved_helper_group",
    "F_0036.s07_open_machine_config_single_connected_machine",
    "F_0036.s08_machine_config_target_resolved_single_connected",
    "F_0036.s09_open_machine_config_ambiguous_or_none",
    "F_0036.s10_machine_config_structured_error",
    "F_0036.s11_machine_config_card_actions",
    "F_0036.s12_click_machine_config_cancel",
    "F_0036.s13_machine_config_unchanged_and_no_policy_sync",
    "F_0036.s14_foreign_operator_save",
    "F_0036.s15_foreign_operator_save_rejected",
    "F_0036.s16_owner_save_machine_config",
    "F_0036.s17_machine_config_saved_and_policy_sync_requested",
    "F_0036.s18_owner_save_when_machine_offline",
    "F_0036.s19_machine_config_offline_warning_not_fake_success",
    "F_0036.s20_product_bug_aggregate",
)


CASE = CaseDefinition(
    id="F_0036_machine_config_card_policy_sync_safety_contract",
    name="Machine config card policy sync safety contract",
    description=(
        "Uses the deployed relay source-driver to validate /machine_config target "
        "resolution, Save+Cancel controls, Cancel no-mutation, operator boundary, "
        "policy sync request, and offline warning behavior."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_daemon_relay_api",
    tags=("F", "slash", "machine_config", "relay", "card", "policy"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "feishu_mock.send_text",
            "feishu_mock.click_card",
            "feishu_mock.submit_card_form",
            "callback.health_probe",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": ("ctx.require", "ctx.equals"),
        "resource_locks": (
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0036_codex", "mode": "exclusive"},
            {"resource": "machine_config:ci_f_0036", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0036", "mode": "exclusive"},
            {"resource": "project:axis_intern_agents_backup:ci_f_0036_project", "mode": "exclusive"},
            {"resource": "relay:machine_config", "mode": "write"},
            {"resource": "source_driver:deployed-feishu-relay", "mode": "read"},
            {"resource": "task:axis_intern_agents_backup:task_ci_f_0036", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0036",
            "project:ci_f_0036_project",
            "intern:intern_ci_f_0036_codex",
            "task:task_ci_f_0036",
            "source-driver:deployed-feishu-relay",
            "machine:debug-pool",
            "machine_config:case-scoped-policy",
        ),
        "run_mode": "existing_debug_native_source_driver",
        "notes": (
            "Run with --use-existing-deployment; CI syncs harness only and does not package/reset/deploy/bootstrap/restart relay.",
            "Missing /machine_config Cancel, Cancel mutation, bad owner boundary, missing policy sync, or fake offline success is product_bug evidence.",
        ),
    },
)


def run_f_machine_config_card_policy_sync_safety_contract(case: Any) -> None:
    self = case
    ctx: dict[str, Any] = {}
    state: dict[str, Any] = {"product_bug_findings": []}

    def latest_card(result: dict[str, Any]) -> dict[str, Any]:
        cards = result.get("new_cards") or []
        self.require("f0036_machine_config_card_sent", bool(cards), result)
        message_id = cards[-1]["message_id"]
        for update in reversed(ctx["api"].card_updates):
            if update.get("message_id") == message_id:
                return update["card"]
        return cards[-1]["card"]

    def card_machine_id(card: dict[str, Any]) -> str:
        return str(self.mock_feishu.machine_config_value(card).get("machine_id") or "")

    policy_group_key = "ci_f_0036_policy"
    policy_field_key = "codex_lb_mode"

    def machine_config_form_value(mode: str) -> dict[str, Any]:
        return {
            f"env_switch__{policy_group_key}": "enabled",
            f"env_switch_field__{policy_group_key}__{policy_field_key}": mode,
        }

    def open_fresh_machine_config_card(label: str) -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/machine_config", chat_id=ctx["chat_id"])
        state[f"{label}_open"] = result
        card = latest_card(result)
        state[f"{label}_card"] = card
        return card

    def s01_reset_case_namespace() -> dict[str, Any]:
        schema = self.mock_feishu.machine_config_schema()
        ctx.update(self.mock_feishu.relay_driver_context_for_remote(self, case_label="machine_config", mapped=True, machine_config_schema=schema))
        state["reset"] = {
            "source_driver_only": True,
            "namespace": self.resource_namespace,
            "deployed_source": self.artifacts.get("relay_source_driver", {}).get("source_path", ""),
        }
        return state["reset"]

    def s02_seed_machine_config_schema() -> dict[str, Any]:
        helper_chat_id = f"oc_{self._runtime_namespace()}_helper_debug_b_{self.run_token}"
        ctx["helper_chat_id"] = helper_chat_id
        ctx["registry"].register_machine_helper(
            "debug-b",
            helper_id="machine_helper_debug-b",
            runtime="codex",
            chat_id=helper_chat_id,
            status="running",
            last_operator_open_id=ctx["owner_open_id"],
        )
        state["schema"] = {
            "schema": ctx["machine_config_schema"],
            "policy_path": self.artifacts.get("machine_config_policy", {}).get("policy_path"),
            "helper_chat_id": helper_chat_id,
        }
        return state["schema"]

    def s03_open_machine_config_from_intern_group() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/machine_config", chat_id=ctx["chat_id"])
        state["intern_group_open"] = result
        state["intern_card"] = latest_card(result)
        return {"result": result, "card_text": self.mock_feishu.card_text(state["intern_card"])[:2000]}

    def s04_assert_intern_group_target() -> dict[str, Any]:
        detail = {"machine_id": card_machine_id(state["intern_card"]), "chat_id": ctx["chat_id"]}
        self.require("f0036_intern_group_target_debug_a", detail["machine_id"] == "debug-a", detail)
        return detail

    def s05_open_machine_config_from_helper_group() -> dict[str, Any]:
        result = self.mock_feishu.relay_driver_message_for_remote(self, ctx, text="/machine_config", chat_id=ctx["helper_chat_id"])
        state["helper_group_open"] = result
        state["helper_card"] = latest_card(result)
        return {"result": result, "card_text": self.mock_feishu.card_text(state["helper_card"])[:2000]}

    def s06_assert_helper_group_target() -> dict[str, Any]:
        detail = {"machine_id": card_machine_id(state["helper_card"]), "chat_id": ctx["helper_chat_id"]}
        self.require("f0036_helper_group_target_debug_b", detail["machine_id"] == "debug-b", detail)
        return detail

    def s07_open_machine_config_single_connected() -> dict[str, Any]:
        machines = ctx["registry"].machines
        machines["debug-a"]["ws_connected"] = False
        machines["debug-b"]["ws_connected"] = True
        result = self.mock_feishu.relay_driver_message_for_remote(self,
            ctx,
            text="/machine_config",
            chat_id=f"oc_{self._runtime_namespace()}_single_connected_{self.run_token}",
        )
        machines["debug-a"]["ws_connected"] = True
        state["single_connected_open"] = result
        state["single_connected_card"] = latest_card(result)
        return {"result": result, "card_text": self.mock_feishu.card_text(state["single_connected_card"])[:2000]}

    def s08_assert_single_connected_target() -> dict[str, Any]:
        detail = {"machine_id": card_machine_id(state["single_connected_card"])}
        self.require("f0036_single_connected_target_debug_b", detail["machine_id"] == "debug-b", detail)
        return detail

    def s09_open_machine_config_ambiguous_or_none() -> dict[str, Any]:
        machines = ctx["registry"].machines
        machines["debug-a"]["ws_connected"] = True
        machines["debug-b"]["ws_connected"] = True
        ambiguous = self.mock_feishu.relay_driver_message_for_remote(self,
            ctx,
            text="/machine_config",
            chat_id=f"oc_{self._runtime_namespace()}_ambiguous_{self.run_token}",
        )
        machines["debug-a"]["ws_connected"] = False
        machines["debug-b"]["ws_connected"] = False
        none = self.mock_feishu.relay_driver_message_for_remote(self,
            ctx,
            text="/machine_config",
            chat_id=f"oc_{self._runtime_namespace()}_none_{self.run_token}",
        )
        machines["debug-a"]["ws_connected"] = True
        machines["debug-b"]["ws_connected"] = True
        state["ambiguous_or_none"] = {"ambiguous": ambiguous, "none": none}
        return state["ambiguous_or_none"]

    def s10_assert_machine_config_structured_error() -> dict[str, Any]:
        ambiguous_text = "\n".join(item.get("text", "") for item in state["ambiguous_or_none"]["ambiguous"].get("new_replies") or [])
        none_text = "\n".join(item.get("text", "") for item in state["ambiguous_or_none"]["none"].get("new_replies") or [])
        detail = {"ambiguous_text": ambiguous_text, "none_text": none_text, "result": state["ambiguous_or_none"]}
        self.require(
            "f0036_machine_config_structured_error_reply",
            "无法确定机器" in ambiguous_text and "没有已连接机器" in none_text,
            detail,
        )
        return detail

    def s11_assert_machine_config_card_actions() -> dict[str, Any]:
        summary = self.mock_feishu.card_action_summary(state["intern_card"])
        expected = {"save", "cancel"}
        evidence = self.collect_product_bug_evidence(
            state,
            "product_bug_machine_config_missing_cancel",
            expected.issubset(set(summary["actions"])),
            expected="/machine_config card exposes Save and Cancel actions.",
            actual=f"/machine_config semantic card actions are {summary['actions']!r}.",
            detail=summary,
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_build_machine_config_card", "marker": '"machine_config_action"', "label": "/machine_config save button"},
                {"function": "_build_machine_config_card", "marker": "取消", "label": "/machine_config cancel button"},
                {"function": "_handle_machine_config_card_action", "label": "/machine_config callback mutation path"},
            ]),
        )
        return {"expected": sorted(expected), **summary, "product_bug_evidence": evidence}

    def s12_click_machine_config_cancel() -> dict[str, Any]:
        state["machine_config_before_cancel"] = {
            "state": self.mock_feishu.machine_config_state(ctx),
            "sent_payload_count": len(ctx["relay_ws"].sent_payloads),
        }
        cancel_value = self.mock_feishu.find_card_action_value(state["intern_card"], "cancel")
        state["machine_config_cancel_value"] = cancel_value
        if not cancel_value:
            state["machine_config_cancel_response"] = {"skipped": True, "reason": "machine_config card has no cancel action"}
            return {"cancel_value_present": False, "before": state["machine_config_before_cancel"], "response": state["machine_config_cancel_response"]}
        response = self.mock_feishu.relay_driver_card_action_for_remote(self, ctx, value=cancel_value)
        state["machine_config_cancel_response"] = response
        return {"cancel_value_present": True, "before": state["machine_config_before_cancel"], "response": response}

    def s13_assert_machine_config_unchanged_and_no_policy_sync() -> dict[str, Any]:
        before = state["machine_config_before_cancel"]
        after = {
            "state": self.mock_feishu.machine_config_state(ctx),
            "sent_payload_count": len(ctx["relay_ws"].sent_payloads),
        }
        state["machine_config_after_cancel"] = after
        unchanged = before["state"].get("machines") == after["state"].get("machines") and before["sent_payload_count"] == after["sent_payload_count"]
        evidence = self.collect_product_bug_evidence(
            state,
            "product_bug_machine_config_cancel_mutates_state",
            unchanged,
            expected="/machine_config Cancel does not save state and does not send daemon_policy_sync.",
            actual=f"Before Cancel {before!r}, after Cancel {after!r}.",
            detail={"before": before, "after": after, "cancel_response": state.get("machine_config_cancel_response")},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_handle_machine_config_card_action", "label": "/machine_config save mutation path"},
                {"function": "create_card_callback_handler", "marker": 'value.get("machine_config_action")', "label": "/machine_config callback dispatch"},
            ]),
        )
        return {"before": before, "after": after, "product_bug_evidence": evidence}

    def s14_foreign_operator_save() -> dict[str, Any]:
        fresh_card = open_fresh_machine_config_card("foreign_save")
        value = self.mock_feishu.machine_config_value(fresh_card)
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value=value,
            form_value=machine_config_form_value("disabled"),
            operator_open_id=ctx["non_owner_open_id"],
        )
        state["foreign_save"] = {
            "response": response,
            "state": self.mock_feishu.machine_config_state(ctx),
            "sent_payload_count": len(ctx["relay_ws"].sent_payloads),
        }
        return state["foreign_save"]

    def s15_assert_foreign_operator_save_rejected() -> dict[str, Any]:
        before = state["machine_config_after_cancel"]
        after = state["foreign_save"]
        rejected = (
            after["response"].get("toast_type") == "error"
            and before["state"].get("machines") == after["state"].get("machines")
            and before["sent_payload_count"] == after["sent_payload_count"]
        )
        evidence = self.collect_product_bug_evidence(
            state,
            "product_bug_machine_config_operator_boundary",
            rejected,
            expected="Foreign operator save is rejected and does not mutate machine config or send daemon_policy_sync.",
            actual=f"Foreign save response/state was {after!r}.",
            detail={"before": before, "after": after},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_handle_machine_config_card_action", "marker": "Only", "label": "operator boundary toast"},
                {"function": "_handle_machine_config_card_action", "marker": "actual_open_id != expected_open_id", "label": "operator open_id guard"},
            ]),
        )
        return {"product_bug_evidence": evidence, **after}

    def s16_owner_save_machine_config() -> dict[str, Any]:
        fresh_card = open_fresh_machine_config_card("owner_save")
        value = self.mock_feishu.machine_config_value(fresh_card)
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value=value,
            form_value=machine_config_form_value("disabled"),
            operator_open_id=ctx["owner_open_id"],
        )
        state["owner_save"] = {
            "response": response,
            "state": self.mock_feishu.machine_config_state(ctx),
            "sent_payloads": list(ctx["relay_ws"].sent_payloads),
        }
        return state["owner_save"]

    def s17_assert_machine_config_saved_and_policy_sync_requested() -> dict[str, Any]:
        machines = state["owner_save"]["state"].get("machines") or {}
        record = machines.get("debug-a") if isinstance(machines.get("debug-a"), dict) else {}
        group_values = record.get("group_values") if isinstance(record.get("group_values"), dict) else {}
        policy_values = group_values.get(policy_group_key) if isinstance(group_values.get(policy_group_key), dict) else {}
        legacy_fields = record.get("fields") if isinstance(record.get("fields"), dict) else {}
        payloads = [
            item for item in state["owner_save"]["sent_payloads"]
            if item.get("machine_id") == "debug-a" and (item.get("payload") or {}).get("type") == "daemon_policy_sync"
        ]
        saved_and_synced = (
            (
                policy_values.get(policy_field_key) == "disabled"
                or legacy_fields.get(policy_field_key) == "disabled"
            )
            and bool(payloads)
            and state["owner_save"]["response"].get("toast_type") == "success"
        )
        evidence = self.collect_product_bug_evidence(
            state,
            "product_bug_machine_config_policy_sync_not_requested",
            saved_and_synced,
            expected="Owner Save writes machine_config state and sends daemon_policy_sync to the target machine.",
            actual=f"Owner save evidence was {state['owner_save']!r}.",
            detail={"record": record, "payloads": payloads, "owner_save": state["owner_save"]},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_handle_machine_config_card_action", "marker": 'save_env_switch_state', "label": "machine_config state save"},
                {"function": "_handle_machine_config_card_action", "marker": '"type": "daemon_policy_sync"', "label": "daemon_policy_sync payload"},
                {"function": "_send_to_machine_with_reason", "label": "machine send helper"},
            ]),
        )
        return {"record": record, "payloads": payloads, "product_bug_evidence": evidence}

    def s18_owner_save_when_machine_offline() -> dict[str, Any]:
        ctx["registry"].machines["debug-a"]["ws_connected"] = False
        fresh_card = open_fresh_machine_config_card("offline_save")
        value = self.mock_feishu.machine_config_value(fresh_card)
        response = self.mock_feishu.relay_driver_card_action_for_remote(self,
            ctx,
            value=value,
            form_value=machine_config_form_value("enabled"),
            operator_open_id=ctx["owner_open_id"],
        )
        ctx["registry"].machines["debug-a"]["ws_connected"] = True
        state["offline_save"] = {
            "response": response,
            "state": self.mock_feishu.machine_config_state(ctx),
            "sent_payloads": list(ctx["relay_ws"].sent_payloads),
        }
        return state["offline_save"]

    def s19_assert_machine_config_offline_warning_not_fake_success() -> dict[str, Any]:
        response = state["offline_save"]["response"]
        offline_payloads = [
            item for item in state["offline_save"]["sent_payloads"]
            if item.get("machine_id") == "debug-a" and item.get("connected") is False and (item.get("payload") or {}).get("type") == "daemon_policy_sync"
        ]
        warned = response.get("toast_type") == "warning" and bool(offline_payloads)
        evidence = self.collect_product_bug_evidence(
            state,
            "product_bug_machine_config_offline_warning_not_fake_success",
            warned,
            expected="Saving while target machine is offline returns warning and does not fake immediate success.",
            actual=f"Offline save response/payloads were {state['offline_save']!r}.",
            detail={"response": response, "offline_payloads": offline_payloads, "offline_save": state["offline_save"]},
            handler_evidence=self.mock_feishu.relay_driver_handler_evidence(ctx, [
                {"function": "_handle_machine_config_card_action", "marker": "target machine is offline", "label": "offline warning toast"},
                {"function": "_handle_machine_config_card_action", "marker": "resp.toast.type = \"success\" if sent else \"warning\"", "label": "sent/warning branch"},
            ]),
        )
        return {"product_bug_evidence": evidence, "response": response, "offline_payloads": offline_payloads}

    def s20_assert_product_bug_findings() -> dict[str, Any]:
        return self.aggregate_product_bug_findings(state, "f0036_product_bug_aggregate")

    self.run_ordered_scenarios([
        ("F_0036.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0036.s02_seed_machine_config_schema", s02_seed_machine_config_schema),
        ("F_0036.s03_open_machine_config_from_intern_group", s03_open_machine_config_from_intern_group),
        ("F_0036.s04_machine_config_target_resolved_intern_group", s04_assert_intern_group_target),
        ("F_0036.s05_open_machine_config_from_helper_group", s05_open_machine_config_from_helper_group),
        ("F_0036.s06_machine_config_target_resolved_helper_group", s06_assert_helper_group_target),
        ("F_0036.s07_open_machine_config_single_connected_machine", s07_open_machine_config_single_connected),
        ("F_0036.s08_machine_config_target_resolved_single_connected", s08_assert_single_connected_target),
        ("F_0036.s09_open_machine_config_ambiguous_or_none", s09_open_machine_config_ambiguous_or_none),
        ("F_0036.s10_machine_config_structured_error", s10_assert_machine_config_structured_error),
        ("F_0036.s11_machine_config_card_actions", s11_assert_machine_config_card_actions),
        ("F_0036.s12_click_machine_config_cancel", s12_click_machine_config_cancel),
        ("F_0036.s13_machine_config_unchanged_and_no_policy_sync", s13_assert_machine_config_unchanged_and_no_policy_sync),
        ("F_0036.s14_foreign_operator_save", s14_foreign_operator_save),
        ("F_0036.s15_foreign_operator_save_rejected", s15_assert_foreign_operator_save_rejected),
        ("F_0036.s16_owner_save_machine_config", s16_owner_save_machine_config),
        ("F_0036.s17_machine_config_saved_and_policy_sync_requested", s17_assert_machine_config_saved_and_policy_sync_requested),
        ("F_0036.s18_owner_save_when_machine_offline", s18_owner_save_when_machine_offline),
        ("F_0036.s19_machine_config_offline_warning_not_fake_success", s19_assert_machine_config_offline_warning_not_fake_success),
        ("F_0036.s20_product_bug_aggregate", s20_assert_product_bug_findings),
    ])
    self.artifacts["machine_config_card_policy_sync_safety_contract"] = state
