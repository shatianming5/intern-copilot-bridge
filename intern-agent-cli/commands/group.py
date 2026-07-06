"""internctl group - user-facing Feishu group configuration commands."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from lib.cli_contract import ensure_cli_report_contract


PID_FILE = os.environ.get("FEISHU_DAEMON_ADDR_FILE") or "/tmp/feishu_daemon.json"


def setup_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("group", help="Manage intern Feishu group settings")
    sub = p.add_subparsers(dest="group_command")

    trigger = sub.add_parser("trigger-mode", help="Set group trigger mode")
    trigger.add_argument("name", help="intern name")
    trigger.add_argument("--project", default="axis_intern_agents", help="project name")
    trigger.add_argument("--mode", required=True, choices=["all", "at_only"], help="trigger mode")
    trigger.add_argument("--json", action="store_true")
    trigger.set_defaults(func=run)

    detail = sub.add_parser("detail-mode", help="Set group detail mode")
    detail.add_argument("name", help="intern name")
    detail.add_argument("--project", default="axis_intern_agents", help="project name")
    detail.add_argument("--mode", required=True, choices=["full", "summary"], help="detail mode")
    detail.add_argument("--json", action="store_true")
    detail.set_defaults(func=run)

    p.set_defaults(func=run)


def _daemon_base() -> str:
    try:
        data = json.loads(Path(PID_FILE).read_text(encoding="utf-8"))
        port = int(data["http_port"])
    except Exception as exc:
        raise RuntimeError(f"daemon address unavailable: {PID_FILE}: {exc}") from exc
    return f"http://127.0.0.1:{port}"


def _request(path: str, payload: dict, timeout: float = 30.0) -> tuple[int, dict]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        _daemon_base() + path,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return int(resp.status), json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw or "{}")
        except Exception:
            body = {"error": raw}
        return int(exc.code), body


def _print(data: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if data.get("ok", False):
        command = data.get("command") or "group"
        intern_name = data.get("intern_name") or data.get("name") or ""
        mode = data.get("mode") or ""
        suffix = f" {intern_name} -> {mode}" if intern_name and mode else ""
        print(f"{command}: ok{suffix}")
        return
    if data.get("message"):
        print(str(data["message"]), file=sys.stderr)
    elif data.get("error"):
        print(str(data["error"]), file=sys.stderr)
    else:
        print(json.dumps(data, ensure_ascii=False), file=sys.stderr)


def _endpoint(command: str) -> str:
    if command == "trigger-mode":
        return "/api/group/trigger_mode"
    if command == "detail-mode":
        return "/api/group/detail_mode"
    raise RuntimeError("Usage: internctl group {trigger-mode|detail-mode}")


def run(args: argparse.Namespace) -> int:
    command = getattr(args, "group_command", "")
    if not command:
        print("Usage: internctl group {trigger-mode|detail-mode}", file=sys.stderr)
        return 1

    payload = {
        "intern_name": args.name,
        "project": args.project,
        "mode": args.mode,
    }
    try:
        status, body = _request(_endpoint(command), payload)
    except Exception as exc:
        body = ensure_cli_report_contract(
            {
                "error": "GROUP_DAEMON_UNAVAILABLE",
                "message": str(exc),
                "command": f"group {command}",
                "intern_name": args.name,
                "project": args.project,
                "mode": args.mode,
            },
            ok=False,
            command=f"group {command}",
            default_next_action="Start the local daemon with `internctl daemon start`, then rerun the group command.",
        )
        _print(body, getattr(args, "json", False))
        return 1

    ok = status < 400 and body.get("ok", True) is not False
    body.update({
        "command": f"group {command}",
        "intern_name": args.name,
        "project": args.project,
        "mode": args.mode,
    })
    body = ensure_cli_report_contract(
        body,
        ok=ok,
        command=f"group {command}",
        default_next_action="Review the daemon response, fix the group registry or relay connection, then rerun the group command.",
    )
    _print(body, getattr(args, "json", False))
    return 0 if ok else 1
