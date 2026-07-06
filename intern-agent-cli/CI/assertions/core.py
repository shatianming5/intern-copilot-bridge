from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
from typing import Any

from CI.assertions import evidence
from CI.assertions import surface


def native_require_check(name: str, condition: bool, detail: dict[str, Any] | None = None) -> dict[str, Any]:
    check = {"name": name, "ok": bool(condition), "detail": detail or {}}
    if not check["ok"]:
        check["failure_reason"] = f"assertion failed: {name}: {json.dumps(detail or {}, ensure_ascii=False)[:1200]}"
    return check


def http_status_detail(result: dict[str, Any]) -> dict[str, Any]:
    body = result.get("body") if isinstance(result.get("body"), dict) else {}
    return {"status_code": result.get("status_code"), "body": body}


def http_status_check(
    name: str,
    result: dict[str, Any],
    expected_status: int,
    *,
    error_contains: str = "",
) -> dict[str, Any]:
    detail = http_status_detail(result)
    body = detail["body"]
    ok = result.get("status_code") == expected_status
    if error_contains:
        ok = ok and error_contains in json.dumps(body, ensure_ascii=False)
    return native_require_check(
        name,
        ok,
        {
            "expected_status": expected_status,
            "actual_status": result.get("status_code"),
            "body": body,
            "error_contains": error_contains,
        },
    )


def require_http_status(
    require: Callable[[str, bool, dict[str, Any] | None], Any],
    name: str,
    result: dict[str, Any],
    expected_status: int,
    *,
    error_contains: str = "",
) -> dict[str, Any]:
    check = http_status_check(name, result, expected_status, error_contains=error_contains)
    require(check["name"], check["ok"], check["detail"])
    return http_status_detail(result)


@dataclass
class CaseAssertions:
    ctx: Any

    def require(self, condition: bool, message: str, *, detail: dict[str, Any] | None = None) -> dict[str, Any]:
        result = {
            "ok": bool(condition),
            "message": message,
            "detail": detail or {},
        }
        if not result["ok"]:
            raise AssertionError(f"{message}: {result['detail']}")
        return result

    def equals(self, actual: Any, expected: Any, message: str) -> dict[str, Any]:
        return self.require(
            actual == expected,
            message,
            detail={"actual": actual, "expected": expected},
        )

    def contains(self, container: Any, item: Any, message: str) -> dict[str, Any]:
        return self.require(
            item in container,
            message,
            detail={"container": container, "item": item},
        )

    def action_ok(self, result: dict[str, Any], message: str = "action ok") -> dict[str, Any]:
        return self.require(
            bool(result.get("ok")),
            message,
            detail=result,
        )

    def feishu_visible_operation(
        self,
        result: dict[str, Any],
        message: str = "mock Feishu operation is visible",
        *,
        expected_prefix: str = "[CI模拟]",
        contains: str = "",
    ) -> dict[str, Any]:
        detail = surface.feishu_visible_operation_detail(result, expected_prefix=expected_prefix, contains=contains)
        return self.require(detail["ok"], message, detail=detail)

    def feishu_retained_scene(
        self,
        result: dict[str, Any],
        message: str = "mock Feishu scene is retained",
    ) -> dict[str, Any]:
        detail = surface.feishu_retained_scene_detail(result)
        return self.require(detail["ok"], message, detail=detail)

    def treeview_no_business_prompt(
        self,
        evidence: dict[str, Any],
        message: str = "TreeView F evidence does not trigger agent prompt",
    ) -> dict[str, Any]:
        detail = surface.treeview_no_business_prompt_detail(evidence)
        return self.require(detail["ok"], message, detail=detail)

    def treeview_cli_equivalent(
        self,
        evidence: dict[str, Any],
        message: str = "TreeView GUI action has CLI-equivalent evidence",
    ) -> dict[str, Any]:
        detail = surface.treeview_cli_equivalent_detail(evidence)
        return self.require(detail["ok"], message, detail=detail)

    def source_markers_found(
        self,
        source_evidence: dict[str, Any],
        message: str = "source contract markers are present",
    ) -> dict[str, Any]:
        detail = evidence.source_markers_found_detail(source_evidence)
        return self.require(detail["ok"], message, detail=detail)

    def dist_contract_ok(
        self,
        contract_evidence: dict[str, Any],
        message: str = "deployed extension dist contract checks passed",
    ) -> dict[str, Any]:
        detail = evidence.dist_contract_ok_detail(contract_evidence)
        return self.require(detail["ok"], message, detail=detail)

    def report_redacted(
        self,
        report_value: Any,
        message: str = "CI report evidence is redacted",
    ) -> dict[str, Any]:
        detail = evidence.report_redacted_detail(report_value)
        return self.require(detail["ok"], message, detail=detail)

    def scenario_summary_consistent(
        self,
        scenarios: list[dict[str, Any]],
        summary: dict[str, Any],
        message: str = "scenario summary matches scenario records",
    ) -> dict[str, Any]:
        detail = evidence.scenario_summary_consistent_detail(scenarios, summary)
        return self.require(detail["ok"], message, detail=detail)

# --- CI assertion domain registry specs (generated by B4.R4 migration) ---

ASSERTION_SPECS: tuple[dict[str, Any], ...] = ({'id': 'ctx.require',
  'title': 'Require condition',
  'description': '断言 condition 为 true；失败时抛 AssertionError。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.require',
  'parameters': [{'name': 'condition', 'description': '布尔条件。', 'required': True},
                 {'name': 'message', 'description': '失败时的可读信息。', 'required': True},
                 {'name': 'detail', 'description': '附加定位信息。', 'required': False, 'default': 'None'}],
  'returns': '结构化 dict：ok/message/detail。',
  'notes': [],
  'order': 0},
 {'id': 'ctx.equals',
  'title': 'Assert equality',
  'description': '断言 actual == expected。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.equals',
  'parameters': [{'name': 'actual', 'description': '实际值。', 'required': True},
                 {'name': 'expected', 'description': '期望值。', 'required': True},
                 {'name': 'message', 'description': '失败时的可读信息。', 'required': True}],
  'returns': '结构化 dict，detail 包含 actual/expected。',
  'notes': [],
  'order': 1},
 {'id': 'ctx.contains',
  'title': 'Assert contains',
  'description': '断言 container 包含 item。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.contains',
  'parameters': [{'name': 'container', 'description': '容器。', 'required': True},
                 {'name': 'item', 'description': '期望成员。', 'required': True},
                 {'name': 'message', 'description': '失败时的可读信息。', 'required': True}],
  'returns': '结构化 dict，detail 包含 container/item。',
  'notes': [],
  'order': 2},
 {'id': 'ctx.action_ok',
  'title': 'Assert action ok',
  'description': '断言 action 返回 dict 中 ok 为 true。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.action_ok',
  'parameters': [{'name': 'result', 'description': 'action 返回 dict。', 'required': True},
                 {'name': 'message', 'description': '失败时的可读信息。', 'required': False, 'default': 'action ok'}],
  'returns': '结构化 dict，detail 为原始 action result。',
  'notes': [],
  'order': 3},
 {'id': 'ctx.feishu_visible_operation',
  'title': 'Assert mock Feishu operation is visible',
  'description': '断言 mock Feishu action 产生 `[CI模拟]` 可视消息，并可选匹配剧本文案片段。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.feishu_visible_operation',
  'parameters': [{'name': 'result', 'description': 'Feishu mock action 返回 dict。', 'required': True},
                 {'name': 'message',
                  'description': '失败时的可读信息。',
                  'required': False,
                  'default': 'mock Feishu operation is visible'},
                 {'name': 'expected_prefix', 'description': '可视消息前缀。', 'required': False, 'default': '[CI模拟]'},
                 {'name': 'contains', 'description': '可视消息中必须包含的剧本文案片段。', 'required': False, 'default': ''}],
  'returns': '结构化 dict，detail 包含 text/prefix/contains。',
  'notes': [],
  'order': 4},
 {'id': 'ctx.feishu_retained_scene',
  'title': 'Assert mock Feishu retained scene',
  'description': '断言 Feishu mock evidence 标记为 case 开头清理、结束保留现场。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.feishu_retained_scene',
  'parameters': [{'name': 'result', 'description': 'Feishu mock action 或 relay-driver evidence。', 'required': True},
                 {'name': 'message',
                  'description': '失败时的可读信息。',
                  'required': False,
                  'default': 'mock Feishu scene is retained'}],
  'returns': '结构化 dict，detail 包含 retained_scene/cleanup_policy/end_cleanup。',
  'notes': [],
  'order': 5},
 {'id': 'ctx.treeview_no_business_prompt',
  'title': 'Assert TreeView mock does not prompt agent',
  'description': '断言 TreeView mock evidence 未触发业务 prompt，保持 F case 不花钱边界。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.treeview_no_business_prompt',
  'parameters': [{'name': 'evidence', 'description': 'TreeView mock evidence。', 'required': True},
                 {'name': 'message',
                  'description': '失败时的可读信息。',
                  'required': False,
                  'default': 'TreeView F evidence does not trigger agent prompt'}],
  'returns': '结构化 dict，detail 包含 event/gui_command/business_prompt_sent。',
  'notes': [],
  'order': 6},
 {'id': 'ctx.treeview_cli_equivalent',
  'title': 'Assert TreeView CLI-equivalent evidence',
  'description': '断言 TreeView CLI-equivalence evidence 为 equivalent=true。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.treeview_cli_equivalent',
  'parameters': [{'name': 'evidence', 'description': 'TreeView CLI-equivalence evidence。', 'required': True},
                 {'name': 'message',
                  'description': '失败时的可读信息。',
                  'required': False,
                  'default': 'TreeView GUI action has CLI-equivalent evidence'}],
  'returns': '结构化 dict，detail 包含 expected_cli/actual_commands。',
  'notes': [],
  'order': 7},
 {'id': 'ctx.source_markers_found',
  'title': 'Assert source markers found',
  'description': '断言 source-contract evidence 已定位源码路径且所有 marker 都有正 line number。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.source_markers_found',
  'parameters': [{'name': 'source_evidence',
                  'description': 'SourceContractHelper.product_source_evidence 返回 dict。',
                  'required': True},
                 {'name': 'message',
                  'description': '失败时的可读信息。',
                  'required': False,
                  'default': 'source contract markers are present'}],
  'returns': '结构化 dict，detail 包含 source_path/missing_markers/markers。',
  'notes': [],
  'order': 8},
 {'id': 'ctx.dist_contract_ok',
  'title': 'Assert dist contract ok',
  'description': '断言 deployed extension dist contract evidence 中所有 checks 通过且 failed 为空。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.dist_contract_ok',
  'parameters': [{'name': 'contract_evidence',
                  'description': 'SourceContractHelper.dist_contract_results 返回 dict。',
                  'required': True},
                 {'name': 'message',
                  'description': '失败时的可读信息。',
                  'required': False,
                  'default': 'deployed extension dist contract checks passed'}],
  'returns': '结构化 dict，detail 包含 failed/checks/bundle。',
  'notes': [],
  'order': 9},
 {'id': 'ctx.report_redacted',
  'title': 'Assert report evidence redacted',
  'description': '断言 report evidence 中没有未脱敏的敏感 key/term 或 Bearer token。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.report_redacted',
  'parameters': [{'name': 'report_value', 'description': '已准备写入 report 的 dict/list/string value。', 'required': True},
                 {'name': 'message',
                  'description': '失败时的可读信息。',
                  'required': False,
                  'default': 'CI report evidence is redacted'}],
  'returns': '结构化 dict，detail 包含 sensitive_terms/bearer_token_leaks。',
  'notes': [],
  'order': 10},
 {'id': 'ctx.scenario_summary_consistent',
  'title': 'Assert scenario summary consistent',
  'description': '断言 report summary counts 与 scenario records 完全一致。',
  'kind': 'ctx_assertion',
  'callable_path': 'ctx.assertion.scenario_summary_consistent',
  'parameters': [{'name': 'scenarios', 'description': 'scenario record list。', 'required': True},
                 {'name': 'summary', 'description': 'summary dict。', 'required': True},
                 {'name': 'message',
                  'description': '失败时的可读信息。',
                  'required': False,
                  'default': 'scenario summary matches scenario records'}],
  'returns': '结构化 dict，detail 包含 expected/actual summary。',
  'notes': [],
  'order': 11})
