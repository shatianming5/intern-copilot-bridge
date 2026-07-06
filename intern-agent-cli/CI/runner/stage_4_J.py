from __future__ import annotations

from pathlib import Path
from typing import Any

from CI.cases.base import CaseDefinition
from CI.runner.stage_3_F import case_stage, run_remote_cases


def is_j_case(case: CaseDefinition) -> bool:
    return case_stage(case) == "J"


def run_j_remote_cases(
    *,
    cases: list[CaseDefinition],
    machines: list[dict[str, Any]],
    work_root: str,
    expected_machines: int,
    protected_repo: str,
    nonprotected_repo: str,
    cwd: Path,
    timeout: int,
    parallel_workers: int,
    dry_run: bool,
    ci_harness_root: str = "",
) -> list[dict[str, Any]]:
    return run_remote_cases(
        cases=[case for case in cases if is_j_case(case)],
        machines=machines,
        work_root=work_root,
        expected_machines=expected_machines,
        protected_repo=protected_repo,
        nonprotected_repo=nonprotected_repo,
        cwd=cwd,
        timeout=timeout,
        parallel_workers=parallel_workers,
        dry_run=dry_run,
        ci_harness_root=ci_harness_root,
    )
