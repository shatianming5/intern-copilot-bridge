#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[1]
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))

from CI.runner.reporting import resolve_report_path
from CI.runner.runner import REPO_ROOT, run_light_ci


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run light CI: unit tests and VSIX package only.")
    parser.add_argument("--repo-root", default="", help="Repository root. Defaults to the current checked-out repo.")
    parser.add_argument("--parallel-workers", type=int, default=3, help="Worker budget for unit-test and package commands.")
    parser.add_argument("--command-timeout", type=int, default=3600, help="Per command timeout in seconds.")
    parser.add_argument("--dry-run", action="store_true", help="Write the report shape without running unit tests or package steps.")
    parser.add_argument("--report", default="", help="Report path; defaults to /tmp/intern_agent_CI/<run_id>/report.json.")
    parser.add_argument("--run-id", default="", help="Run id used in the default report directory.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    run_id = args.run_id or f"ci_{time.strftime('%Y%m%d_%H%M%S', time.gmtime())}"
    report_path = resolve_report_path(args.report, run_id=run_id)
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else REPO_ROOT
    report = run_light_ci(
        report_path=report_path,
        run_id=run_id,
        repo_root=repo_root,
        parallel_workers=args.parallel_workers,
        command_timeout=args.command_timeout,
        dry_run=args.dry_run,
    )
    summary = report.get("summary", {})
    print(
        "light CI "
        f"{'passed' if report.get('ok') else 'failed'}: "
        f"passed={summary.get('passed', 0)} failed={summary.get('failed', 0)} skipped={summary.get('skipped', 0)} "
        f"report={report_path}"
    )
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
