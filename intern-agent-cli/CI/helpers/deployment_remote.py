from __future__ import annotations

import base64
from pathlib import Path
import shlex
from typing import Any

from CI.helpers.remote_machine_helper import scp_to_machine, ssh_base, wait_machine_ssh_ready
from CI.runner.reporting import run_command


def reset_remote_ci_state(
    machine: dict[str, Any],
    *,
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    identity_file: Path | None = None,
) -> dict[str, Any]:
    steps = []
    ready = wait_machine_ssh_ready(
        machine,
        cwd=cwd,
        timeout=min(timeout, 180),
        interval=5,
        dry_run=dry_run,
        identity_file=identity_file,
    )
    steps.append({"name": "ssh_ready", **ready})
    if not ready.get("ok") and not dry_run:
        return {
            "ok": False,
            "status": "failed",
            "machine": machine,
            "steps": steps,
            "failure_reason": "remote ssh not ready for state reset",
        }
    reset_script = f"""#!/usr/bin/env bash
set +e
WORK_ROOT={shlex.quote(work_root)}
if [ -x "$WORK_ROOT/extension/bundled-cli/intern-adminctl.py" ]; then
  WORK_AGENTS_ROOT="$WORK_ROOT" python3 "$WORK_ROOT/extension/bundled-cli/intern-adminctl.py" relay stop --json >/tmp/intern-agent-ci-relay-stop.log 2>&1 || true
fi
if [ -x "$WORK_ROOT/extension/bundled-cli/internctl.py" ]; then
  WORK_AGENTS_ROOT="$WORK_ROOT" python3 "$WORK_ROOT/extension/bundled-cli/internctl.py" daemon stop --json >/tmp/intern-agent-ci-daemon-stop.log 2>&1 || true
fi
pkill -f '[f]eishu_relay.py.*axis_enterprise_ci' >/tmp/intern-agent-ci-relay-pkill.log 2>&1 || true
pkill -f 'axis_enterprise_ci.*[f]eishu_relay.py' >>/tmp/intern-agent-ci-relay-pkill.log 2>&1 || true
pkill -f '[f]eishu_daemon.py.*axis_enterprise_ci' >/tmp/intern-agent-ci-daemon-pkill.log 2>&1 || true
pkill -f 'axis_enterprise_ci.*[f]eishu_daemon.py' >>/tmp/intern-agent-ci-daemon-pkill.log 2>&1 || true
pkill -f 'axis_enterprise_ci.*/CI/[n]ative_remote.py' >/tmp/intern-agent-ci-native-remote-kill.log 2>&1 || true
tmux kill-server >/tmp/intern-agent-ci-tmux-kill.log 2>&1 || true
pkill -f '[c]odex --enable hooks' >/tmp/intern-agent-ci-codex-kill.log 2>&1 || true
pkill -f 'axis_enterprise_ci.*[c]laude' >/tmp/intern-agent-ci-claude-kill.log 2>&1 || true
pkill -f '[c]laude.*axis_enterprise_ci' >>/tmp/intern-agent-ci-claude-kill.log 2>&1 || true
rm -f /tmp/feishu_daemon.json "$WORK_ROOT/.feishu_relay.pid" "$WORK_ROOT/.feishu_daemon.pid" "$WORK_ROOT/.intern_sessions.json"
rm -rf "$WORK_ROOT/state" "$WORK_ROOT/enterprise_policy" "$WORK_ROOT/enterprise" "$WORK_ROOT/.feishu_registry" "$WORK_ROOT/llm_intern_logs" "$WORK_ROOT/ci-artifacts" "$WORK_ROOT"/session*_local_repo "$WORK_ROOT"/*_local_repo
mkdir -p "$WORK_ROOT/llm_intern_logs"
"""
    encoded_script = base64.b64encode(reset_script.encode("utf-8")).decode("ascii")
    command = (
        "python3 - <<'PY'\n"
        "import base64\n"
        "from pathlib import Path\n"
        "Path('/tmp/intern-agent-ci-reset.sh').write_bytes(base64.b64decode("
        + repr(encoded_script)
        + "))\n"
        "PY\n"
        "chmod +x /tmp/intern-agent-ci-reset.sh && bash /tmp/intern-agent-ci-reset.sh"
    )
    reset = run_command(ssh_base(machine, identity_file=identity_file) + [command], cwd=cwd, timeout=timeout, dry_run=dry_run)
    reset["machine"] = machine
    reset["steps"] = steps + [{"name": "reset_state", **reset}]
    return reset

def deploy_machine(
    machine: dict[str, Any],
    *,
    extension_tar: Path,
    codex_auth_tar: Path,
    python_wheels_tar: Path,
    ssh_auth_tar: Path,
    enterprise_config_tar: Path,
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    identity_file: Path | None = None,
) -> dict[str, Any]:
    remote_tmp = "/tmp/intern-agent-ci"
    steps = []
    ready = wait_machine_ssh_ready(machine, cwd=cwd, timeout=180, interval=5, dry_run=dry_run, identity_file=identity_file)
    steps.append({"name": "ssh_ready", **ready})
    if not ready.get("ok") and not dry_run:
        return {"ok": False, "status": "failed", "machine": machine, "steps": steps, "failure_reason": "remote ssh not ready"}
    prep = run_command(
        ssh_base(machine, identity_file=identity_file) + [f"mkdir -p {shlex.quote(remote_tmp)} {shlex.quote(work_root)} ~/.codex"],
        cwd=cwd,
        timeout=timeout,
        dry_run=dry_run,
    )
    steps.append({"name": "prep", **prep})
    if not prep.get("ok") and not dry_run:
        return {"ok": False, "status": "failed", "machine": machine, "steps": steps, "failure_reason": "remote prep failed"}
    for src, name in [
        (extension_tar, "extension.tgz"),
        (codex_auth_tar, "codex-auth.tgz"),
        (python_wheels_tar, "python-wheels.tgz"),
        (ssh_auth_tar, "ssh-auth.tgz"),
        (enterprise_config_tar, "enterprise-config.tgz"),
    ]:
        copied = scp_to_machine(src, machine, f"{remote_tmp}/{name}", cwd=cwd, timeout=timeout, dry_run=dry_run, identity_file=identity_file)
        steps.append({"name": f"copy_{name}", **copied})
        if not copied.get("ok") and not dry_run:
            return {"ok": False, "status": "failed", "machine": machine, "steps": steps, "failure_reason": f"copy {name} failed"}

    tmux_cmd = "(command -v tmux >/dev/null || (apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tmux))"
    tmux_installed = run_command(ssh_base(machine, identity_file=identity_file) + [tmux_cmd], cwd=cwd, timeout=timeout, dry_run=dry_run)
    steps.append({"name": "install_tmux", **tmux_installed})
    if not tmux_installed.get("ok") and not dry_run:
        return {
            "ok": False,
            "status": "failed",
            "machine": machine,
            "steps": steps,
            "failure_reason": tmux_installed.get("failure_reason", "tmux install failed"),
        }

    owner_mode = "server" if machine.get("role") == "relay" or int(machine.get("index") or 0) == 0 else "client"
    owner_mode_script = enterprise_owner_mode_script(work_root, owner_mode)
    payload_cmd = (
        f"rm -rf {shlex.quote(work_root)}/extension && "
        f"mkdir -p {shlex.quote(work_root)} && "
        f"tar -xzf {remote_tmp}/extension.tgz -C {shlex.quote(work_root)} && "
        f"tar -xzf {remote_tmp}/codex-auth.tgz -C ~ && "
        f"tar -xzf {remote_tmp}/python-wheels.tgz -C {shlex.quote(remote_tmp)} && "
        f"tar -xzf {remote_tmp}/ssh-auth.tgz -C ~ && "
        f"tar -xzf {remote_tmp}/enterprise-config.tgz -C {shlex.quote(work_root)} && "
        f"{owner_mode_script}&& "
        f"(python3 -m pip install --no-index --find-links {remote_tmp}/wheels websockets lark-oapi --break-system-packages >/tmp/intern-agent-ci-pip.log 2>&1 || "
        f"python3 -m pip install --no-index --find-links {remote_tmp}/wheels websockets lark-oapi >/tmp/intern-agent-ci-pip.log 2>&1) && "
        "chmod 700 ~/.ssh && chmod 600 ~/.ssh/* 2>/dev/null || true; "
        "chmod 600 ~/.codex/auth.json ~/.codex/config.toml 2>/dev/null || true; "
        f"chmod 600 {shlex.quote(work_root)}/enterprise_policy/relay/secrets.json {shlex.quote(work_root)}/enterprise_policy/daemon/user.env {shlex.quote(work_root)}/enterprise_policy/relay/_owner.json {shlex.quote(work_root)}/enterprise_policy/daemon/_owner.json 2>/dev/null || true; "
        f"chmod +x {shlex.quote(work_root)}/extension/bundled-cli/scripts/intern_start_codex.sh {shlex.quote(work_root)}/extension/bundled-cli/scripts/intern_start.sh 2>/dev/null || true; "
        f"chmod +x {shlex.quote(work_root)}/extension/bundled-cli/internctl.py && "
        f"test -f {shlex.quote(work_root)}/extension/bundled-cli/internctl.py"
    )
    installed = run_command(ssh_base(machine, identity_file=identity_file) + [payload_cmd], cwd=cwd, timeout=timeout, dry_run=dry_run)
    steps.append({"name": "install", **installed})
    return {
        "ok": bool(installed.get("ok")) if not dry_run else False,
        "status": "passed" if installed.get("ok") else ("skipped" if dry_run else "failed"),
        "machine": machine,
        "extension_path": f"{work_root}/extension",
        "work_root": work_root,
        "steps": steps,
        "failure_reason": "" if installed.get("ok") else installed.get("failure_reason", "install failed"),
    }

def enterprise_owner_mode_script(work_root: str, owner_mode: str) -> str:
    return (
        "{ python3 - <<'PY'\n"
        "import json\n"
        "import shutil\n"
        "from pathlib import Path\n"
        f"work_root = Path({work_root!r})\n"
        f"owner_mode = {owner_mode!r}\n"
        "relay_dir = work_root / 'enterprise_policy' / 'relay'\n"
        "relay_owner_path = relay_dir / '_owner.json'\n"
        "daemon_owner_path = work_root / 'enterprise_policy' / 'daemon' / '_owner.json'\n"
        "policy_path = relay_dir / 'policy.json'\n"
        "owner = json.loads(relay_owner_path.read_text(encoding='utf-8'))\n"
        "policy = json.loads(policy_path.read_text(encoding='utf-8'))\n"
        "feishu = policy.get('feishu') or {}\n"
        "relay_http_url = str(feishu.get('relay_http_url') or '').rstrip('/')\n"
        "if not relay_http_url and str(feishu.get('relay_health_url') or '').endswith('/api/status'):\n"
        "    relay_http_url = str(feishu.get('relay_health_url')).rsplit('/api/status', 1)[0]\n"
        "if relay_http_url:\n"
        "    owner['relay_http_url'] = relay_http_url\n"
        "if owner_mode == 'client':\n"
        "    owner.pop('relay_ws_port', None)\n"
        "    owner.pop('relay_http_port', None)\n"
        "daemon_owner = dict(owner)\n"
        "daemon_owner.pop('relay_ws_port', None)\n"
        "daemon_owner.pop('relay_http_port', None)\n"
        "daemon_owner_path.write_text(json.dumps(daemon_owner, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')\n"
        "if owner_mode == 'client':\n"
        "    shutil.rmtree(relay_dir, ignore_errors=True)\n"
        "else:\n"
        "    relay_owner_path.write_text(json.dumps(owner, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')\n"
        "PY\n"
        "} "
    )
