from __future__ import annotations

import json
from pathlib import Path
import time
from typing import Any

from CI.helpers.deployment_config import DEFAULT_CLAUDE_BASE_URL, DEFAULT_CODEX_LB_BASE_URL, DEFAULT_CODEX_LB_ENV_KEY
from CI.helpers.deployment_provider_policy import (
    enable_remote_codex_lb_config,
    verify_remote_claude_policy,
    verify_remote_codex_lb,
)
from CI.helpers.remote_machine_helper import remote_cli, wait_http_json
from CI.runner.reporting import run_command, tail


def wait_remote_daemon_connected(
    machine: dict[str, Any],
    *,
    work_root: str,
    cwd: Path,
    timeout: int,
    interval: int,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {"ok": False, "status": "skipped", "failure_reason": "dry run"}
    deadline = time.time() + timeout
    attempts: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {}
    while True:
        probe = run_command(
            remote_cli(machine, work_root, "internctl", ["daemon", "status", "--json"]),
            cwd=cwd,
            timeout=min(30, max(1, timeout)),
            dry_run=False,
        )
        payload: dict[str, Any] = {}
        if probe.get("stdout"):
            try:
                parsed = json.loads(str(probe["stdout"]))
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {"parse_error": tail(str(probe["stdout"]), 500)}
        last_payload = payload
        attempts.append({"ok": probe.get("ok"), "status": probe.get("status"), "payload": payload})
        status = payload.get("status") if isinstance(payload.get("status"), dict) else {}
        if probe.get("ok") and payload.get("running") is True and status.get("relay_connected") is True:
            return {
                "ok": True,
                "status": "passed",
                "attempts": len(attempts),
                "payload": payload,
                "machine": machine,
            }
        if time.time() >= deadline:
            return {
                "ok": False,
                "status": "failed",
                "attempts": len(attempts),
                "last": attempts[-1] if attempts else {},
                "failure_reason": f"daemon did not connect to relay; last={json.dumps(last_payload, ensure_ascii=False)[:800]}",
                "machine": machine,
            }
        time.sleep(interval)

def wait_remote_relay_connections(
    machine: dict[str, Any],
    *,
    work_root: str,
    cwd: Path,
    expected_machines: int,
    timeout: int,
    interval: int,
    dry_run: bool,
) -> dict[str, Any]:
    if dry_run:
        return {"ok": False, "status": "skipped", "failure_reason": "dry run"}
    deadline = time.time() + timeout
    attempts: list[dict[str, Any]] = []
    last_payload: dict[str, Any] = {}
    while True:
        probe = run_command(
            remote_cli(machine, work_root, "intern-adminctl", ["relay", "status", "--json"]),
            cwd=cwd,
            timeout=min(30, max(1, timeout)),
            dry_run=False,
        )
        payload: dict[str, Any] = {}
        if probe.get("stdout"):
            try:
                parsed = json.loads(str(probe["stdout"]))
                if isinstance(parsed, dict):
                    payload = parsed
            except json.JSONDecodeError:
                payload = {"parse_error": tail(str(probe["stdout"]), 500)}
        last_payload = payload
        attempts.append({"ok": probe.get("ok"), "status": probe.get("status"), "payload": payload})
        http = payload.get("http") if isinstance(payload.get("http"), dict) else {}
        connected = int(http.get("machines_connected") or 0)
        if probe.get("ok") and payload.get("running") is True and connected >= expected_machines:
            return {
                "ok": True,
                "status": "passed",
                "attempts": len(attempts),
                "payload": payload,
                "expected_machines": expected_machines,
                "actual_machines": connected,
            }
        if time.time() >= deadline:
            return {
                "ok": False,
                "status": "failed",
                "attempts": len(attempts),
                "last": attempts[-1] if attempts else {},
                "failure_reason": (
                    f"relay did not observe {expected_machines} machine connections; "
                    f"last={json.dumps(last_payload, ensure_ascii=False)[:800]}"
                ),
            }
        time.sleep(interval)

def bootstrap_remote_services(
    machines: list[dict[str, Any]],
    *,
    relay_health_url: str,
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    codex_lb_base_url: str = DEFAULT_CODEX_LB_BASE_URL,
    codex_lb_env_key: str = DEFAULT_CODEX_LB_ENV_KEY,
    claude_base_url: str = DEFAULT_CLAUDE_BASE_URL,
) -> dict[str, Any]:
    if not machines:
        return {"ok": False, "status": "failed", "failure_reason": "no machines"}
    steps = []
    relay = run_command(
        remote_cli(machines[0], work_root, "intern-adminctl", ["relay", "restart"]),
        cwd=cwd,
        timeout=timeout,
        dry_run=dry_run,
    )
    steps.append({"name": "relay_restart", **relay})
    if not relay.get("ok") and not dry_run:
        return {"ok": False, "status": "failed", "steps": steps, "failure_reason": "relay restart failed"}
    if not dry_run:
        relay_ready = wait_http_json(relay_health_url, timeout=min(timeout, 300), interval=5)
        steps.append({"name": "relay_health_wait", "ok": True, "status": "passed", "payload": relay_ready})
    for machine in machines:
        refreshed = run_command(
            remote_cli(machine, work_root, "internctl", ["setup", "refresh-policy", "--json"]),
            cwd=cwd,
            timeout=timeout,
            dry_run=dry_run,
        )
        steps.append({"name": f"setup_refresh_policy_machine_{machine.get('index')}", **refreshed})
        if not refreshed.get("ok") and not dry_run:
            return {"ok": False, "status": "failed", "steps": steps, "failure_reason": "setup refresh-policy failed"}
    for machine in machines:
        final_applied = run_command(
            remote_cli(machine, work_root, "internctl", ["setup", "apply", "--install-runtime", "--json"]),
            cwd=cwd,
            timeout=timeout,
            dry_run=dry_run,
        )
        steps.append({"name": f"setup_apply_final_machine_{machine.get('index')}", **final_applied})
        if not final_applied.get("ok"):
            steps[-1]["nonblocking"] = True
    for machine in machines:
        codex_config = enable_remote_codex_lb_config(
            machine,
            work_root=work_root,
            cwd=cwd,
            timeout=min(timeout, 240),
            dry_run=dry_run,
        )
        steps.append({"name": f"codex_lb_config_enable_machine_{machine.get('index')}", **codex_config})
        if not codex_config.get("ok") and not dry_run:
            return {"ok": False, "status": "failed", "steps": steps, "failure_reason": "Codex LB config enable failed"}
    for machine in machines:
        codex_ready = verify_remote_codex_lb(
            machine,
            work_root=work_root,
            cwd=cwd,
            timeout=min(timeout, 240),
            dry_run=dry_run,
            codex_lb_base_url=codex_lb_base_url,
            codex_lb_env_key=codex_lb_env_key,
        )
        steps.append({"name": f"codex_lb_smoke_machine_{machine.get('index')}", **codex_ready})
        if not codex_ready.get("ok") and not dry_run:
            return {"ok": False, "status": "failed", "steps": steps, "failure_reason": "Codex LB deployment smoke failed"}
    for machine in machines:
        claude_ready = verify_remote_claude_policy(
            machine,
            work_root=work_root,
            cwd=cwd,
            timeout=min(timeout, 120),
            dry_run=dry_run,
            claude_base_url=claude_base_url,
        )
        steps.append({"name": f"claude_policy_smoke_machine_{machine.get('index')}", **claude_ready})
        if not claude_ready.get("ok") and not dry_run:
            return {"ok": False, "status": "failed", "steps": steps, "failure_reason": "Claude policy deployment smoke failed"}
    for machine in machines:
        daemon = run_command(
            remote_cli(machine, work_root, "internctl", ["daemon", "restart"]),
            cwd=cwd,
            timeout=timeout,
            dry_run=dry_run,
        )
        steps.append({"name": f"daemon_restart_machine_{machine.get('index')}", **daemon})
        if not daemon.get("ok") and not dry_run:
            return {"ok": False, "status": "failed", "steps": steps, "failure_reason": "daemon restart failed"}
        ready = wait_remote_daemon_connected(
            machine,
            work_root=work_root,
            cwd=cwd,
            timeout=min(timeout, 300),
            interval=2,
            dry_run=dry_run,
        )
        steps.append({"name": f"daemon_relay_wait_machine_{machine.get('index')}", **ready})
        if not ready.get("ok") and not dry_run:
            return {"ok": False, "status": "failed", "steps": steps, "failure_reason": "daemon relay connection wait failed"}
    relay_connections = wait_remote_relay_connections(
        machines[0],
        work_root=work_root,
        cwd=cwd,
        expected_machines=len(machines),
        timeout=min(timeout, 300),
        interval=2,
        dry_run=dry_run,
    )
    steps.append({"name": "relay_machine_connections_wait", **relay_connections})
    if not relay_connections.get("ok") and not dry_run:
        return {"ok": False, "status": "failed", "steps": steps, "failure_reason": "relay machine connection wait failed"}
    return {"ok": not dry_run, "status": "passed" if not dry_run else "skipped", "steps": steps}
