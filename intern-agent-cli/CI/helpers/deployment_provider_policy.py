from __future__ import annotations

import json
from pathlib import Path
import shlex
from typing import Any

from CI.helpers.deployment_config import (
    DEFAULT_CLAUDE_BASE_URL,
    DEFAULT_CODEX_LB_BASE_URL,
    DEFAULT_CODEX_LB_ENV_KEY,
)
from CI.helpers.remote_machine_helper import ssh_base
from CI.runner.reporting import run_command, tail


def verify_remote_codex_lb(
    machine: dict[str, Any],
    *,
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    codex_lb_base_url: str = DEFAULT_CODEX_LB_BASE_URL,
    codex_lb_env_key: str = DEFAULT_CODEX_LB_ENV_KEY,
    identity_file: Path | None = None,
) -> dict[str, Any]:
    if dry_run:
        return {"ok": False, "status": "skipped", "failure_reason": "dry run", "machine": machine}
    python = r'''
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys


def apply_env_file(path, env):
    if not path.is_file():
        raise SystemExit(f"missing env file: {path}")
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("unset "):
            for key in shlex.split(line[len("unset "):]):
                env.pop(key, None)
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        try:
            parsed = shlex.split(value, comments=False, posix=True)
        except ValueError:
            parsed = [value.strip().strip('"').strip("'")]
        env[key] = parsed[0] if parsed else ""


root = Path(os.environ["WORK_AGENTS_ROOT"])
expected_base_url = sys.argv[1]
expected_env_key = sys.argv[2]
runtime_dir = root / "enterprise_policy" / "daemon" / "runtime"
user_env = root / "enterprise_policy" / "daemon" / "user.env"
codex_env = runtime_dir / "codex.env"
report_path = runtime_dir / "session_env_report.json"
if not report_path.is_file():
    raise SystemExit(f"missing session env report: {report_path}")

env = os.environ.copy()
apply_env_file(user_env, env)
apply_env_file(codex_env, env)

report = json.loads(report_path.read_text(encoding="utf-8"))
provider = ((report.get("providers") or {}).get("codex") or {})
args = list(provider.get("args") or shlex.split(env.get("INTERN_CODEX_POLICY_ARGS", "")))
required_args = {
    'model_provider="lb"',
    f'model_providers.lb.base_url="{expected_base_url}"',
    'model_providers.lb.wire_api="responses"',
    f'model_providers.lb.env_key="{expected_env_key}"',
}
missing_args = sorted(required_args - set(args))
checks = {
    "base_url_env": env.get("CODEX_POLICY_LB_BASE_URL") == expected_base_url,
    "env_key_env": env.get("CODEX_LB_ENV_KEY") == expected_env_key,
    "secret_present": bool(env.get(expected_env_key)),
    "provider_enabled": provider.get("enabled") is True,
    "provider_env_key_reported": expected_env_key in set(provider.get("env_keys") or []),
    "provider_args": not missing_args,
}
if not all(checks.values()):
    print(json.dumps({
        "ok": False,
        "stage": "policy",
        "checks": checks,
        "missing_args": missing_args,
        "provider_env_keys": provider.get("env_keys") or [],
        "provider_args": args,
    }, ensure_ascii=False, indent=2))
    raise SystemExit(1)

out_path = root / "ci-artifacts" / "deploy" / "codex-lb-smoke-last-message.txt"
out_path.parent.mkdir(parents=True, exist_ok=True)
cmd = [
    "codex",
    "exec",
    "--skip-git-repo-check",
    "--ignore-rules",
    "--ignore-user-config",
    "--ephemeral",
    "--output-last-message",
    str(out_path),
    *args,
    "Reply exactly: CI_CODEX_LB_OK",
]
result = subprocess.run(
    cmd,
    cwd=str(root),
    env=env,
    capture_output=True,
    text=True,
    timeout=min(180, max(30, int(os.environ.get("CI_CODEX_LB_SMOKE_TIMEOUT", "120")))),
)
last_message = out_path.read_text(encoding="utf-8", errors="replace") if out_path.is_file() else ""
ok = result.returncode == 0 and "CI_CODEX_LB_OK" in last_message
print(json.dumps({
    "ok": ok,
    "stage": "connectivity",
    "returncode": result.returncode,
    "stdout_tail": result.stdout[-1200:],
    "stderr_tail": result.stderr[-1200:],
    "last_message_tail": last_message[-1200:],
    "policy_checks": checks,
    "provider_args": args,
    "env_keys": sorted(key for key in ("CODEX_POLICY_LB_BASE_URL", "CODEX_LB_ENV_KEY", expected_env_key) if env.get(key)),
}, ensure_ascii=False, indent=2))
raise SystemExit(0 if ok else 1)
'''
    command = (
        "set -euo pipefail; "
        f"export WORK_AGENTS_ROOT={shlex.quote(work_root)}; "
        f"export PATH={shlex.quote(work_root)}/extension/bundled-cli:/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH; "
        f"python3 - {shlex.quote(codex_lb_base_url)} {shlex.quote(codex_lb_env_key)} <<'PY'\n"
        f"{python}\n"
        "PY"
    )
    run = run_command(
        ssh_base(machine, identity_file=identity_file) + [command],
        cwd=cwd,
        timeout=timeout,
        dry_run=False,
    )
    payload: dict[str, Any] = {}
    if run.get("stdout"):
        try:
            parsed = json.loads(str(run["stdout"]))
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {"parse_error": tail(str(run["stdout"]), 800)}
    return {
        "ok": bool(run.get("ok") and payload.get("ok") is True),
        "status": "passed" if run.get("ok") and payload.get("ok") is True else "failed",
        "machine": machine,
        "payload": payload,
        "run": run,
        "failure_reason": "" if run.get("ok") and payload.get("ok") is True else (
            payload.get("failure_reason")
            or payload.get("stage")
            or run.get("failure_reason")
            or "Codex LB deployment smoke failed"
        ),
    }


def enable_remote_codex_lb_config(
    machine: dict[str, Any],
    *,
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    identity_file: Path | None = None,
) -> dict[str, Any]:
    if dry_run:
        return {
            "ok": False,
            "status": "skipped",
            "machine": machine,
            "failure_reason": "dry run",
        }
    command = (
        "set -euo pipefail; "
        f"export WORK_AGENTS_ROOT={shlex.quote(work_root)}; "
        f"export PATH={shlex.quote(work_root)}/extension/bundled-cli:/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH; "
        "set -a; "
        f"[ -f {shlex.quote(work_root)}/enterprise_policy/daemon/user.env ] && . {shlex.quote(work_root)}/enterprise_policy/daemon/user.env; "
        f"[ -f {shlex.quote(work_root)}/enterprise_policy/daemon/runtime/codex.env ] && . {shlex.quote(work_root)}/enterprise_policy/daemon/runtime/codex.env; "
        "set +a; "
        f"python3 {shlex.quote(work_root)}/extension/bundled-cli/internctl.py config codex-load-balance enable --json"
    )
    run = run_command(
        ssh_base(machine, identity_file=identity_file) + [command],
        cwd=cwd,
        timeout=min(timeout, 180),
        dry_run=False,
    )
    payload: dict[str, Any] = {}
    if run.get("stdout"):
        try:
            parsed = json.loads(str(run["stdout"]))
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {"parse_error": tail(str(run["stdout"]), 800)}
    ok = bool(run.get("ok") and payload.get("ok") is True and payload.get("enabled") is True)
    return {
        "ok": ok,
        "status": "passed" if ok else "failed",
        "machine": machine,
        "payload": payload,
        "run": run,
        "failure_reason": "" if ok else (
            payload.get("reason")
            or payload.get("error")
            or payload.get("parse_error")
            or run.get("failure_reason")
            or "Codex LB config enable failed"
        ),
    }


def verify_remote_claude_policy(
    machine: dict[str, Any],
    *,
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    claude_base_url: str = DEFAULT_CLAUDE_BASE_URL,
    identity_file: Path | None = None,
) -> dict[str, Any]:
    if dry_run:
        return {
            "ok": False,
            "status": "skipped",
            "machine": machine,
            "failure_reason": "dry run",
        }
    python = r'''
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys


def apply_env_file(path, env):
    if not path.is_file():
        raise SystemExit(f"missing env file: {path}")
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("unset "):
            for key in shlex.split(line[len("unset "):]):
                env.pop(key, None)
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        try:
            parsed = shlex.split(value, comments=False, posix=True)
        except ValueError:
            parsed = [value.strip().strip('"').strip("'")]
        env[key.strip()] = parsed[0] if parsed else ""


root = Path(os.environ["WORK_AGENTS_ROOT"])
expected_base_url = sys.argv[1]
runtime_dir = root / "enterprise_policy" / "daemon" / "runtime"
user_env = root / "enterprise_policy" / "daemon" / "user.env"
claude_env = runtime_dir / "claude.env"
report_path = runtime_dir / "session_env_report.json"
env = os.environ.copy()
apply_env_file(user_env, env)
apply_env_file(claude_env, env)
report = json.loads(report_path.read_text(encoding="utf-8"))
provider = ((report.get("providers") or {}).get("claude") or {})
policy_args = env.get("INTERN_CLAUDE_POLICY_ARGS", "")
expected_args = "--permission-mode bypassPermissions --model claude-opus-4-7"
claude_bin = shutil.which("claude", path=env.get("PATH"))
version = ""
returncode = 127
if claude_bin:
    result = subprocess.run([claude_bin, "--version"], env=env, capture_output=True, text=True, timeout=30)
    returncode = result.returncode
    version = (result.stdout or result.stderr).strip()
checks = {
    "provider_enabled": provider.get("enabled") is True,
    "policy_args": policy_args == expected_args,
    "base_url_env": env.get("ANTHROPIC_BASE_URL") == expected_base_url,
    "provider_base_url_reported": "ANTHROPIC_BASE_URL" in set(provider.get("env_keys") or []),
    "token_present": bool(env.get("ANTHROPIC_AUTH_TOKEN") or env.get("CLAUDE_CODE_OAUTH_TOKEN")),
    "claude_binary": bool(claude_bin),
    "claude_version": returncode == 0 and bool(version),
}
print(json.dumps({
    "ok": all(checks.values()),
    "checks": checks,
    "policy_args": policy_args,
    "provider_args": provider.get("args") or [],
    "env_keys": provider.get("env_keys") or [],
    "expected_base_url": expected_base_url,
    "claude_bin": claude_bin or "",
    "claude_version": version,
}, ensure_ascii=False, indent=2))
raise SystemExit(0 if all(checks.values()) else 1)
'''
    command = (
        "set -euo pipefail; "
        f"export WORK_AGENTS_ROOT={shlex.quote(work_root)}; "
        f"export PATH={shlex.quote(work_root)}/extension/bundled-cli:/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH; "
        f"python3 - {shlex.quote(claude_base_url)} <<'PY'\n{python}\nPY"
    )
    run = run_command(
        ssh_base(machine, identity_file=identity_file) + [command],
        cwd=cwd,
        timeout=timeout,
        dry_run=False,
    )
    payload: dict[str, Any] = {}
    if run.get("stdout"):
        try:
            parsed = json.loads(str(run["stdout"]))
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {"parse_error": tail(str(run["stdout"]), 800)}
    ok = bool(run.get("ok") and payload.get("ok") is True)
    return {
        "ok": ok,
        "status": "passed" if ok else "failed",
        "machine": machine,
        "payload": payload,
        "run": run,
        "failure_reason": "" if ok else (
            payload.get("parse_error")
            or run.get("failure_reason")
            or "Claude policy deployment smoke failed"
        ),
    }
