#!/usr/bin/env python3
"""Verify a packaged VSIX contains runtime files and excludes local test debris."""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
import sys
import zipfile


SCHEMA = "intern-agents.vsix-package-verification.v1"

REQUIRED_MEMBERS = [
    "extension/package.json",
    "extension/dist/extension.js",
    "extension/bundled-cli/internctl.py",
    "extension/bundled-cli/CI/run_ci.py",
    "extension/bundled-cli/CI/run_light_ci.py",
    "extension/bundled-cli/CI/runner/stage_3_F.py",
]

REQUIRED_EXTENSION_STRINGS = [
    "internSetupView",
    "intern.setupRefresh",
    "intern.setupAutoFix",
    "EnterpriseSetupWebviewProvider",
    "intern-agents.enterprise-setup-report.v1",
    "acquireVsCodeApi",
]

FORBIDDEN_PATTERNS = [
    "extension/*.vsix",
    "extension/.pytest_cache/*",
    "extension/out/*",
    "extension/coverage/*",
    "extension/node_modules/*",
    "extension/src/*",
    "extension/.vscode/*",
    "extension/hooks/tests/*",
    "*/__pycache__/*",
    "*.pyc",
]


def verify(path: Path) -> dict:
    report = {
        "schema": SCHEMA,
        "path": str(path),
        "ok": False,
        "size_bytes": 0,
        "member_count": 0,
        "required_missing": [],
        "forbidden_matches": [],
        "gui_contract": {},
        "errors": [],
    }
    if not path.is_file():
        report["errors"].append(f"VSIX not found: {path}")
        return report
    report["size_bytes"] = path.stat().st_size
    try:
        with zipfile.ZipFile(path) as zf:
            names = sorted(zf.namelist())
            package_json = _read_json_member(zf, "extension/package.json")
            extension_js = _read_text_member(zf, "extension/dist/extension.js")
    except Exception as exc:
        report["errors"].append(f"failed to read VSIX zip: {exc}")
        return report
    report["member_count"] = len(names)
    name_set = set(names)
    report["required_missing"] = [name for name in REQUIRED_MEMBERS if name not in name_set]
    forbidden = []
    for name in names:
        if any(fnmatch.fnmatch(name, pattern) for pattern in FORBIDDEN_PATTERNS):
            forbidden.append(name)
    report["forbidden_matches"] = forbidden
    report["gui_contract"] = _verify_gui_contract(package_json, extension_js)
    if not report["gui_contract"]["ok"]:
        report["errors"].extend(report["gui_contract"]["errors"])
    report["ok"] = not report["errors"] and not report["required_missing"] and not report["forbidden_matches"]
    return report


def _read_json_member(zf: zipfile.ZipFile, name: str) -> dict:
    try:
        return json.loads(zf.read(name).decode("utf-8"))
    except Exception:
        return {}


def _read_text_member(zf: zipfile.ZipFile, name: str) -> str:
    try:
        return zf.read(name).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _verify_gui_contract(package_json: dict, extension_js: str) -> dict:
    errors: list[str] = []
    contributes = package_json.get("contributes") if isinstance(package_json, dict) else {}
    contributes = contributes if isinstance(contributes, dict) else {}
    commands = {
        item.get("command")
        for item in contributes.get("commands", [])
        if isinstance(item, dict) and item.get("command")
    }
    views = contributes.get("views", {})
    intern_views = views.get("internAgents", []) if isinstance(views, dict) else []
    setup_view = next(
        (item for item in intern_views if isinstance(item, dict) and item.get("id") == "internSetupView"),
        {},
    )
    agents_view = next(
        (item for item in intern_views if isinstance(item, dict) and item.get("id") == "internAgentsView"),
        {},
    )
    for command in ("intern.setupRefresh", "intern.setupAutoFix", "intern.openSetup"):
        if command not in commands:
            errors.append(f"GUI command missing from package.json: {command}")
    if setup_view.get("when") != "!intern.setupReady":
        errors.append("internSetupView must be contributed under !intern.setupReady")
    if agents_view.get("when") != "intern.setupReady":
        errors.append("internAgentsView must be contributed under intern.setupReady")
    if package_json.get("main") != "./dist/extension.js":
        errors.append("package main must point to ./dist/extension.js")
    activation_events = package_json.get("activationEvents") or []
    if "onStartupFinished" not in activation_events:
        errors.append("activationEvents must include onStartupFinished")
    missing_strings = [needle for needle in REQUIRED_EXTENSION_STRINGS if needle not in extension_js]
    for needle in missing_strings:
        errors.append(f"dist/extension.js missing GUI runtime marker: {needle}")
    return {
        "ok": not errors,
        "setup_view_when": setup_view.get("when", ""),
        "agents_view_when": agents_view.get("when", ""),
        "commands_present": sorted(command for command in commands if str(command).startswith("intern.setup") or command == "intern.openSetup"),
        "runtime_markers": {needle: needle in extension_js for needle in REQUIRED_EXTENSION_STRINGS},
        "errors": errors,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vsix", help="Path to VSIX file.")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    report = verify(Path(args.vsix).expanduser())
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif report["ok"]:
        print(f"VSIX package verification passed: {report['path']}")
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
