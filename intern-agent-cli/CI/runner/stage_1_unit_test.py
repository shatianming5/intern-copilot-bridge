from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from CI.runner.reporting import run_command, write_json


def run_unit_stage(*, root: Path, artifact_dir: Path, timeout: int, parallel_workers: int, dry_run: bool) -> dict[str, Any]:
    unit_report = artifact_dir / "unit-stage.json"
    commands = [
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "intern-cli/tests",
        ],
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "vscode-extension/hooks/tests",
        ],
    ]
    steps = [
        run_command(command, cwd=root, timeout=timeout, dry_run=dry_run)
        for command in commands
    ]
    ok = all(step.get("ok") for step in steps)
    report = {
        "schema": "intern-agents.ci-unit-stage.v1",
        "parallel_workers": parallel_workers,
        "steps": steps,
        "ok": ok,
        "status": "passed" if ok else ("skipped" if dry_run else "failed"),
    }
    write_json(unit_report, report)
    return report | {"report": str(unit_report)}
