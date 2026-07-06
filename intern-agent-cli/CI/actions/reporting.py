from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from CI.helpers import reporting


@dataclass
class ReportingActions:
    ctx: Any

    def redact_value(self, value: Any) -> dict[str, Any]:
        return {
            "redacted": reporting.redact_report_value(value),
        }

    def scenario_summary(self, scenarios: list[dict[str, Any]]) -> dict[str, int]:
        return reporting.scenario_summary(scenarios)

    def failure_index(self, scenarios: list[dict[str, Any]]) -> dict[str, Any]:
        return reporting.failure_index(scenarios)

    def product_bug_aggregate(self, findings: list[Any]) -> dict[str, Any]:
        return reporting.product_bug_aggregate_detail(findings)
