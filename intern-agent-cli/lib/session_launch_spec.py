"""Shared provider launch environment expansion for session start/resume/reconcile."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Any

from lib.enterprise_paths import daemon_runtime_dir, daemon_user_env_path

PROVIDER_POLICY_ARGS_ENV = {
    "codex": "INTERN_CODEX_POLICY_ARGS",
    "claude": "INTERN_CLAUDE_POLICY_ARGS",
}
PROVIDER_DEFAULT_ARGS_ENV = {
    "codex": "INTERN_CODEX_DEFAULT_ARGS",
    "claude": "INTERN_CLAUDE_DEFAULT_ARGS",
}
PROVIDER_HASH_ENV = {
    "codex": "CODEX_POLICY_ENV_HASH",
    "claude": "CLAUDE_POLICY_ENV_HASH",
}
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
DYNAMIC_LAUNCH_ENV_KEYS = {"PATH"}


def canonical_executable_path(path: str) -> str:
    if not path:
        return ""
    raw = str(path)
    candidate = raw if os.path.isabs(raw) else shutil.which(raw)
    if not candidate:
        return raw
    try:
        return str(Path(candidate).resolve())
    except Exception:
        return candidate or raw


def provider_runtime_env_path(work_root: str | os.PathLike[str], provider: str) -> Path:
    return daemon_runtime_dir(work_root) / f"{provider}.env"


def launch_env_file_keys(path: Path) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()

    def add(key: str) -> None:
        if key in DYNAMIC_LAUNCH_ENV_KEYS:
            return
        if key and ENV_NAME_RE.match(key) and key not in seen:
            keys.append(key)
            seen.add(key)

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return keys
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("# managed_env_keys:"):
            for key in line.split(":", 1)[1].split():
                add(key)
            continue
        if line.startswith("#"):
            continue
        unset_match = re.match(r"^unset\s+([A-Za-z_][A-Za-z0-9_]*)\s*$", line)
        if unset_match:
            add(unset_match.group(1))
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        assign_match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=", line)
        if assign_match:
            add(assign_match.group(1))
    return keys


def provider_launch_env_files(work_root: str | os.PathLike[str], provider: str) -> list[Path]:
    return [daemon_user_env_path(work_root), provider_runtime_env_path(work_root, provider)]


def provider_launch_env_keys(work_root: str | os.PathLike[str], provider: str) -> list[str]:
    keys: list[str] = []
    seen: set[str] = set()
    for path in provider_launch_env_files(work_root, provider):
        for key in launch_env_file_keys(path):
            if key not in seen:
                keys.append(key)
                seen.add(key)
    args_key = PROVIDER_POLICY_ARGS_ENV.get(provider, "")
    default_args_key = PROVIDER_DEFAULT_ARGS_ENV.get(provider, "")
    if args_key and default_args_key and args_key in seen and default_args_key not in seen:
        keys.append(default_args_key)
    return keys


def provider_launch_env_values(
    work_root: str | os.PathLike[str],
    provider: str,
    *,
    base_env: dict[str, str] | None = None,
    python_executable: str | None = None,
) -> dict[str, str]:
    keys = provider_launch_env_keys(work_root, provider)
    if not keys:
        return {}
    files = provider_launch_env_files(work_root, provider)
    args_key = PROVIDER_POLICY_ARGS_ENV.get(provider, "")
    default_args_key = PROVIDER_DEFAULT_ARGS_ENV.get(provider, "")
    script = (
        "import json, os, sys; "
        "keys=json.loads(sys.argv[1]); "
        "values={key: os.environ.get(key, '') for key in keys}; "
        "args_key=sys.argv[2]; default_args_key=sys.argv[3]; "
        "values.update({default_args_key: values[args_key]} if args_key and default_args_key and values.get(args_key) else {}); "
        "print(json.dumps(values, ensure_ascii=False))"
    )
    source_cmd = (
        "set -a; "
        f"[ -f {shlex.quote(str(files[0]))} ] && . {shlex.quote(str(files[0]))}; "
        f"[ -f {shlex.quote(str(files[1]))} ] && . {shlex.quote(str(files[1]))}; "
        "set +a; "
        f"{shlex.quote(python_executable or sys.executable or 'python3')} -c {shlex.quote(script)} "
        f"{shlex.quote(json.dumps(keys))} {shlex.quote(args_key)} {shlex.quote(default_args_key)}"
    )
    try:
        result = subprocess.run(
            ["bash", "-lc", source_cmd],
            capture_output=True,
            text=True,
            env=base_env,
        )
    except Exception:
        return {}
    if result.returncode != 0:
        return {}
    try:
        parsed: Any = json.loads(result.stdout or "{}")
    except Exception:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(parsed.get(key) or "") for key in keys if ENV_NAME_RE.match(str(key))}


def runtime_project_repo_path(intern_dir: str | os.PathLike[str]) -> str:
    """Return the code checkout path used by the shell start scripts."""
    if not intern_dir:
        return ""
    state_path = Path(intern_dir) / ".hook_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    if not isinstance(state, dict):
        return ""
    resolver = state.get("metadata_resolver")
    if not isinstance(resolver, dict):
        resolver = {}
    for source in (resolver, state):
        for key in ("code_worktree_path", "code_repo_path"):
            value = source.get(key) if isinstance(source, dict) else ""
            if value:
                return str(value)
    return ""


def session_runtime_launch_env_values(
    *,
    work_root: str | os.PathLike[str],
    session_name: str,
    intern_name: str,
    intern_dir: str,
    project: str = "",
    workspace_id: str = "",
    daemon_addr_file: str = "/tmp/feishu_daemon.json",
    ctl_python: str = "",
    ctl_path: str = "",
    ready_channel: str = "",
) -> dict[str, str]:
    values = {
        "INTERN_NAME": intern_name or session_name,
        "INTERN_TMUX_SESSION": session_name,
        "INTERN_TMUX_READY_CHANNEL": ready_channel,
        "INTERN_DIR": intern_dir,
        "WORK_AGENTS_ROOT": str(work_root),
        "PROJECT_NAME": project,
        "INTERN_WORKSPACE_ID": workspace_id,
        "FEISHU_DAEMON_ADDR_FILE": daemon_addr_file,
        "INTERN_CTL_PYTHON": canonical_executable_path(ctl_python),
        "INTERN_CTL_PATH": ctl_path,
    }
    project_repo = runtime_project_repo_path(intern_dir)
    if project_repo:
        values["PROJECT_REPO"] = project_repo
    return {key: str(value) for key, value in values.items() if key and value is not None}


def sync_tmux_launch_env(session_name: str, values: dict[str, str]) -> None:
    for key, value in values.items():
        if not ENV_NAME_RE.match(str(key)):
            continue
        if value:
            cmd = ["tmux", "set-environment", "-t", f"={session_name}", str(key), str(value)]
        else:
            cmd = ["tmux", "set-environment", "-u", "-t", f"={session_name}", str(key)]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    dump = sub.add_parser("json")
    dump.add_argument("--work-root", required=True)
    dump.add_argument("--provider", required=True, choices=sorted(PROVIDER_POLICY_ARGS_ENV))
    sync = sub.add_parser("tmux-sync")
    sync.add_argument("--work-root", required=True)
    sync.add_argument("--provider", required=True, choices=sorted(PROVIDER_POLICY_ARGS_ENV))
    sync.add_argument("--session", required=True)
    args = parser.parse_args(argv)
    values = provider_launch_env_values(args.work_root, args.provider)
    if args.command == "json":
        print(json.dumps(values, ensure_ascii=False, sort_keys=True))
        return 0
    sync_tmux_launch_env(args.session, values)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
