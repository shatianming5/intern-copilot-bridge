"""internctl config - user-facing local configuration commands."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

from lib.cli_contract import ensure_cli_report_contract


CODEX_LB_ENV_KEY = "LB_API_KEY"
CODEX_LB_PROVIDER_LINE = 'model_provider = "lb"'


def _codex_lb_base_url() -> str:
    return os.environ.get("CODEX_LB_BASE_URL") or os.environ.get("CODEX_POLICY_LB_BASE_URL") or ""


def _codex_lb_env_key() -> str:
    return os.environ.get("CODEX_LB_ENV_KEY") or CODEX_LB_ENV_KEY


def _codex_lb_provider_table(base_url: str | None = None, env_key: str | None = None) -> str:
    return "\n".join([
        "[model_providers.lb]",
        'name = "codex-lb"',
        f'base_url = "{base_url if base_url is not None else _codex_lb_base_url()}"',
        'wire_api = "responses"',
        f'env_key = "{env_key if env_key is not None else _codex_lb_env_key()}"',
    ])


def setup_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser("config", help="Manage local intern-agent configuration")
    config_sub = parser.add_subparsers(dest="config_command")

    format_check = config_sub.add_parser("format-check", help="Manage format check enforcement")
    format_sub = format_check.add_subparsers(dest="config_action")
    for action in ("enable", "disable", "toggle", "status"):
        cmd = format_sub.add_parser(action)
        cmd.add_argument("--json", action="store_true")
        cmd.set_defaults(func=run_format_check)

    codex_lb = config_sub.add_parser("codex-load-balance", help="Manage Codex load balance provider")
    codex_sub = codex_lb.add_subparsers(dest="config_action")
    for action in ("enable", "disable", "toggle", "status"):
        cmd = codex_sub.add_parser(action)
        cmd.add_argument("--json", action="store_true")
        cmd.set_defaults(func=run_codex_load_balance)

    parser.set_defaults(func=run_usage)


def _work_root() -> Path:
    return Path(os.environ.get("WORK_AGENTS_ROOT") or os.getcwd())


def _format_check_flag_path() -> Path:
    return _work_root() / ".format_check_disabled"


def _codex_config_path() -> Path:
    configured = os.environ.get("CODEX_CONFIG_PATH")
    if configured:
        return Path(configured)
    return Path.home() / ".codex" / "config.toml"


def _is_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("[") and stripped.endswith("]")


def _is_top_level_lb_provider(content: str) -> bool:
    for line in content.replace("\r\n", "\n").split("\n"):
        if _is_table_line(line):
            return False
        if line.strip() == CODEX_LB_PROVIDER_LINE:
            return True
    return False


def _read_toml_string_value(line: str, key: str) -> str | None:
    match = re.match(rf"^{re.escape(key)}\s*=\s*\"([^\"]*)\"\s*$", line.strip())
    return match.group(1) if match else None


def _has_lb_provider_table(content: str) -> bool:
    values: dict[str, str] = {}
    in_lb_table = False
    for line in content.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        if _is_table_line(line):
            if in_lb_table:
                break
            in_lb_table = stripped == "[model_providers.lb]"
            continue
        if not in_lb_table or not stripped or stripped.startswith("#"):
            continue
        for key in ("base_url", "wire_api", "env_key"):
            value = _read_toml_string_value(line, key)
            if value is not None:
                values[key] = value
    configured_base_url = _codex_lb_base_url()
    base_url = values.get("base_url") or ""
    return (
        bool(base_url)
        and (not configured_base_url or base_url == configured_base_url)
        and values.get("wire_api") == "responses"
        and bool(values.get("env_key"))
    )


def _is_codex_lb_enabled(config_path: Path) -> bool:
    if not config_path.exists():
        return False
    content = config_path.read_text(encoding="utf-8")
    return _is_top_level_lb_provider(content) and _has_lb_provider_table(content)


def _remove_existing_lb_config(content: str) -> str:
    kept: list[str] = []
    skipping_lb_table = False
    in_top_level = True
    for line in content.replace("\r\n", "\n").split("\n"):
        stripped = line.strip()
        is_table = _is_table_line(line)
        if is_table:
            if stripped == "[model_providers.lb]":
                skipping_lb_table = True
                in_top_level = False
                continue
            skipping_lb_table = False
            in_top_level = False
        if skipping_lb_table:
            continue
        if in_top_level and re.match(r"^model_provider\s*=", stripped):
            continue
        kept.append(line)
    return "\n".join(kept).rstrip()


def _insert_top_level_provider(content: str) -> str:
    if not content:
        return CODEX_LB_PROVIDER_LINE
    lines = content.split("\n")
    first_table_index = next((i for i, line in enumerate(lines) if _is_table_line(line)), -1)
    if first_table_index == -1:
        return f"{content.rstrip()}\n{CODEX_LB_PROVIDER_LINE}"
    before = "\n".join(lines[:first_table_index]).rstrip()
    after = "\n".join(lines[first_table_index:]).lstrip()
    prefix = f"{before}\n" if before else ""
    return f"{prefix}{CODEX_LB_PROVIDER_LINE}\n\n{after}".rstrip()


def _ping_codex_load_balance(timeout: float = 5.0) -> tuple[bool, str]:
    base_url = _codex_lb_base_url()
    if not base_url:
        return False, "CODEX_LB_BASE_URL is not set"
    try:
        req = urllib.request.Request(base_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if int(resp.status) < 500:
                return True, ""
            return False, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        if int(exc.code) < 500:
            return True, ""
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


def _enable_codex_lb(config_path: Path) -> dict:
    if not _codex_lb_base_url():
        return {"ok": False, "enabled": False, "reason": "CODEX_LB_BASE_URL is not set", "config_path": str(config_path)}
    ok, reason = _ping_codex_load_balance()
    if not ok:
        return {"ok": False, "enabled": False, "reason": reason, "config_path": str(config_path)}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    current = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    cleaned = _remove_existing_lb_config(current)
    with_provider = _insert_top_level_provider(cleaned)
    next_content = f"{with_provider}\n\n{_codex_lb_provider_table()}\n"
    config_path.write_text(next_content, encoding="utf-8")
    return {
        "ok": True,
        "enabled": True,
        "config_path": str(config_path),
        "written": next_content != current,
        "env_key": _codex_lb_env_key(),
    }


def _disable_codex_lb(config_path: Path) -> dict:
    if not config_path.exists():
        return {"ok": True, "enabled": False, "config_path": str(config_path), "written": False}
    current = config_path.read_text(encoding="utf-8")
    next_content = f"{_remove_existing_lb_config(current)}\n"
    if next_content == "\n":
        next_content = ""
    config_path.write_text(next_content, encoding="utf-8")
    return {
        "ok": True,
        "enabled": False,
        "config_path": str(config_path),
        "written": next_content != current,
    }


def _print_report(report: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if report.get("ok", False):
        enabled = "enabled" if report.get("enabled") else "disabled"
        print(f"{report.get('command')}: {enabled}")
        return
    print(str(report.get("reason") or report.get("message") or report.get("error") or report), file=sys.stderr)


def _with_contract(report: dict, *, ok: bool, command: str) -> dict:
    report["command"] = command
    return ensure_cli_report_contract(
        report,
        ok=ok,
        command=command,
        default_next_action="Review the local config path and rerun the command after fixing file permissions or network access.",
    )


def run_usage(_args: argparse.Namespace) -> int:
    print("Usage: internctl config {format-check|codex-load-balance} {enable|disable|toggle|status}", file=sys.stderr)
    return 1


def run_format_check(args: argparse.Namespace) -> int:
    action = getattr(args, "config_action", "")
    flag_path = _format_check_flag_path()
    if action == "status":
        enabled = not flag_path.exists()
    elif action == "enable":
        flag_path.unlink(missing_ok=True)
        enabled = True
    elif action == "disable":
        flag_path.write_text("", encoding="utf-8")
        enabled = False
    elif action == "toggle":
        if flag_path.exists():
            flag_path.unlink()
            enabled = True
        else:
            flag_path.write_text("", encoding="utf-8")
            enabled = False
    else:
        return run_usage(args)
    report = _with_contract(
        {"ok": True, "enabled": enabled, "flag_path": str(flag_path)},
        ok=True,
        command=f"config format-check {action}",
    )
    _print_report(report, getattr(args, "json", False))
    return 0


def run_codex_load_balance(args: argparse.Namespace) -> int:
    action = getattr(args, "config_action", "")
    config_path = _codex_config_path()
    if action == "status":
        report = {"ok": True, "enabled": _is_codex_lb_enabled(config_path), "config_path": str(config_path)}
    elif action == "enable":
        report = _enable_codex_lb(config_path)
    elif action == "disable":
        report = _disable_codex_lb(config_path)
    elif action == "toggle":
        report = _disable_codex_lb(config_path) if _is_codex_lb_enabled(config_path) else _enable_codex_lb(config_path)
    else:
        return run_usage(args)
    ok = bool(report.get("ok"))
    report = _with_contract(report, ok=ok, command=f"config codex-load-balance {action}")
    _print_report(report, getattr(args, "json", False))
    return 0 if ok else 1
