"""internctl session - headless intern runtime lifecycle commands."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

from commands import workspace as workspace_cmd
from commands.metadata import bind_repo_dotdir_metadata_to_code_repo, resolve_metadata_for_workspace_id
from lib.git_ops import add_commit_push, clone, ensure_git_identity
from lib.enterprise_paths import daemon_runtime_dir, daemon_user_env_path
from lib.session_launch_spec import provider_launch_env_values, session_runtime_launch_env_values, sync_tmux_launch_env
from lib.enterprise_state_v1 import intern_runtime_dir
from lib.tmux_session import scoped_tmux_session_name, tmux_ready_channel


def setup_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("session", help="Manage headless intern sessions")
    sub = p.add_subparsers(dest="session_command")

    start = sub.add_parser("start", help="Start a headless intern session")
    start.add_argument("name")
    start.add_argument("--project", required=True)
    start.add_argument("--type", choices=["claude", "codex"], default="codex")
    start.add_argument("--resume-last", action="store_true")
    start.add_argument("--no-attach", action="store_true", help="Leave tmux detached")
    start.set_defaults(func=run)

    status = sub.add_parser("status", help="Show tmux session status")
    status.add_argument("name")
    status.add_argument("--project")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=run)

    restart = sub.add_parser("restart", help="Restart a managed intern session")
    restart.add_argument("name")
    restart.add_argument("--project", required=True)
    restart.add_argument("--type", choices=["claude", "codex"], default="codex")
    restart.add_argument("--no-attach", action="store_true", help="Leave tmux detached")
    restart.set_defaults(func=run)

    resume = sub.add_parser("resume", help="Resume a managed intern session in its existing tmux pane")
    resume.add_argument("name")
    resume.add_argument("--project", required=True)
    resume.add_argument("--type", choices=["claude", "codex"], default="codex")
    resume.add_argument("--json", action="store_true")
    resume.set_defaults(func=run)

    capture_claude = sub.add_parser("capture-claude-session", help=argparse.SUPPRESS)
    capture_claude.add_argument("name")
    capture_claude.add_argument("--project", required=True)
    capture_claude.add_argument("--timeout", type=float, default=90.0)
    capture_claude.add_argument("--since", type=float, default=0.0)
    capture_claude.add_argument("--json", action="store_true")
    capture_claude.set_defaults(func=run)

    clear_claude = sub.add_parser("clear-claude-session", help=argparse.SUPPRESS)
    clear_claude.add_argument("name")
    clear_claude.add_argument("--project", required=True)
    clear_claude.add_argument("--json", action="store_true")
    clear_claude.set_defaults(func=run)

    stop = sub.add_parser("stop", help="Stop a tmux session")
    stop.add_argument("name")
    stop.add_argument("--project")
    stop.set_defaults(func=run)

    p.set_defaults(func=run)


def _root() -> str:
    return os.environ.get("WORK_AGENTS_ROOT") or os.getcwd()


def _cli_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _session_entry(name: str, project: str) -> dict:
    sessions_path = Path(_root()) / ".intern_sessions.json"
    try:
        data = json.loads(sessions_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"session registry unavailable: {sessions_path}: {exc}") from exc

    def entry_name(_key: str, value: dict) -> str:
        return str(value.get("intern_name") or "")

    def entry_scopes(key: str, value: dict) -> set[str]:
        scopes = {
            str(value.get("project") or ""),
            str(value.get("workspace_id") or ""),
        }
        if ":" in key:
            scopes.add(str(key).split(":", 1)[0])
        return {scope for scope in scopes if scope}

    matches = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        if entry_name(str(key), value) != name:
            continue
        if project not in entry_scopes(str(key), value):
            continue
        intern_dir = str(value.get("intern_dir") or "")
        if not intern_dir or not Path(intern_dir).is_dir():
            continue
        matches.append(value)
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one session registry entry for {project}:{name}, found {len(matches)}")
    return dict(matches[0])


def _workspace_for_project(project: str) -> dict:
    status, body = workspace_cmd._request("GET", "/api/workspaces")
    if status >= 400:
        raise RuntimeError(body.get("error") or f"workspace list failed: HTTP {status}")
    matches = []
    for item in body.get("workspaces") or []:
        if not isinstance(item, dict):
            continue
        candidates = {
            str(item.get("workspace_id") or ""),
            str(item.get("project_id") or ""),
            str(item.get("display_name") or ""),
            str(item.get("name") or ""),
        }
        if project in candidates:
            matches.append(item)
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one workspace for project {project!r}, found {len(matches)}")
    workspace = dict(matches[0])
    if not workspace.get("local_enabled"):
        raise RuntimeError(f"workspace {project!r} is not enabled on this machine")
    if not workspace.get("workspace_id"):
        raise RuntimeError(f"workspace {project!r} missing workspace_id")
    return workspace


def _session_registry_key(name: str, project: str, workspace_id: str) -> str:
    return f"{workspace_id or project}:{name}"


def _write_session_entry(key: str, entry: dict) -> None:
    sessions_file = Path(_root()) / ".intern_sessions.json"
    lock_file = Path(_root()) / ".intern_sessions.lock"
    sessions_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("w", encoding="utf-8") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            try:
                data = json.loads(sessions_file.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            if not isinstance(data, dict):
                data = {}
            current = data.get(key) if isinstance(data.get(key), dict) else {}
            merged = dict(current)
            merged.update(entry)
            data[key] = merged
            tmp = sessions_file.with_suffix(sessions_file.suffix + ".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(sessions_file)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _bootstrap_enterprise_session_entry(name: str, project: str, session_type: str) -> dict:
    workspace = _workspace_for_project(project)
    workspace_id = str(workspace.get("workspace_id") or "")
    intern_dir = os.fspath(intern_runtime_dir(_root(), workspace_id, name))
    code_repo = os.path.join(intern_dir, project)
    resolver = resolve_metadata_for_workspace_id(workspace_id, name, "")
    status_path = str(resolver.get("status_path") or "")
    if not status_path or not os.path.isfile(status_path):
        raise RuntimeError(f"intern metadata not found for {project}:{name}: {status_path or '<missing status_path>'}")
    task_id = _status_task_id(status_path)
    if task_id:
        resolver = resolve_metadata_for_workspace_id(workspace_id, name, task_id)
        status_path = str(resolver.get("status_path") or status_path)
    repo_url = str(workspace.get("repo_url") or workspace.get("local_path") or "")
    if not repo_url:
        raise RuntimeError(f"workspace {workspace_id} missing repo_url/local_path")
    Path(intern_dir).mkdir(parents=True, exist_ok=True)
    if os.path.exists(code_repo):
        if not os.path.isdir(os.path.join(code_repo, ".git")):
            raise RuntimeError(f"intern code repo path exists but is not a git repo: {code_repo}")
    else:
        clone(repo_url, code_repo)
    try:
        ensure_git_identity(code_repo)
    except Exception:
        pass
    resolver["code_repo_path"] = code_repo
    resolver["code_worktree_path"] = code_repo
    resolver = bind_repo_dotdir_metadata_to_code_repo(resolver, code_repo, name, task_id)
    _write_hook_state_resolver(intern_dir, resolver, project, workspace_id)
    entry = {
        "type": session_type,
        "intern_name": name,
        "project": project,
        "workspace_id": workspace_id,
        "intern_dir": intern_dir,
    }
    entry["tmux_session"] = _entry_tmux_session_name(name, project, entry)
    _write_session_entry(_session_registry_key(name, project, workspace_id), entry)
    return entry


def _metadata_resolver(intern_dir: str) -> dict:
    state_path = Path(intern_dir) / ".hook_state.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"hook state unavailable: {state_path}: {exc}") from exc
    resolver = state.get("metadata_resolver")
    if not isinstance(resolver, dict):
        raise RuntimeError(f"hook state missing metadata_resolver: {state_path}")
    return resolver


def _status_task_id(status_path: str) -> str:
    try:
        text = Path(status_path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    match = re.search(r"<!--\s*METADATA:[^>]*\bTASK=([^,>\s]*)", text)
    return match.group(1).strip() if match else ""


def _copy_if_missing(src: str, dst: str) -> bool:
    if not src or not dst or os.path.abspath(src) == os.path.abspath(dst):
        return False
    if not os.path.isfile(src) or os.path.exists(dst):
        return False
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    return True


def _commit_metadata_refresh(resolver: dict, copied_paths: list[str], intern_name: str) -> None:
    if not copied_paths or resolver.get("metadata_mode") == "local_only":
        return
    checkout = str(resolver.get("metadata_checkout_path") or "")
    if not checkout or not os.path.isdir(os.path.join(checkout, ".git")):
        return
    rels = []
    checkout_abs = os.path.abspath(checkout)
    for path in copied_paths:
        path_abs = os.path.abspath(path)
        try:
            if os.path.commonpath([checkout_abs, path_abs]) != checkout_abs:
                continue
        except ValueError:
            continue
        rels.append(os.path.relpath(path_abs, checkout_abs))
    if not rels:
        return
    add_commit_push(
        repo_path=checkout,
        paths=sorted(set(rels)),
        message=f"[{intern_name}] metadata: refresh resolver paths",
        branch=resolver.get("metadata_branch") or None,
    )


def _write_hook_state_resolver(intern_dir: str, resolver: dict, project: str, workspace_id: str) -> None:
    state_path = Path(intern_dir) / ".hook_state.json"
    state = {}
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state["project"] = project
    if workspace_id:
        state["workspace_id"] = workspace_id
    state["metadata_resolver"] = resolver
    tmp = state_path.with_suffix(state_path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(state_path)


def _refresh_enterprise_resolver(entry: dict, intern_dir: str, project: str, old_resolver: dict) -> dict:
    workspace_id = str(entry.get("workspace_id") or "")
    if not workspace_id:
        raise RuntimeError("session registry entry missing workspace_id")
    task_id = _status_task_id(str(old_resolver.get("status_path") or ""))
    resolver = resolve_metadata_for_workspace_id(workspace_id, entry.get("intern_name") or "", task_id)

    code_repo = str(old_resolver.get("code_worktree_path") or old_resolver.get("code_repo_path") or "")
    if not code_repo:
        raise RuntimeError("metadata_resolver missing code_worktree_path/code_repo_path")
    resolver["code_repo_path"] = code_repo
    resolver["code_worktree_path"] = code_repo
    resolver = bind_repo_dotdir_metadata_to_code_repo(
        resolver,
        code_repo,
        str(entry.get("intern_name") or ""),
        task_id,
    )

    copied = []
    for key in ("status_path", "knowledge_path"):
        old_path = str(old_resolver.get(key) or "")
        new_path = str(resolver.get(key) or "")
        if _copy_if_missing(old_path, new_path):
            copied.append(new_path)
    _commit_metadata_refresh(resolver, copied, str(entry.get("intern_name") or ""))
    _write_hook_state_resolver(intern_dir, resolver, project, workspace_id)
    return resolver


def _tmux_running(session_name: str) -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", f"={session_name}"], capture_output=True)
    return result.returncode == 0


def _entry_tmux_session_name(name: str, project: str, entry: dict) -> str:
    explicit = str(entry.get("tmux_session") or "")
    if explicit:
        return explicit
    return scoped_tmux_session_name(
        name,
        project=str(entry.get("project") or project or ""),
        workspace_id=str(entry.get("workspace_id") or ""),
        intern_dir=str(entry.get("intern_dir") or ""),
    )


def _session_entry_optional_project(name: str, project: str = "") -> dict:
    if project:
        return _session_entry(name, project)
    sessions_path = Path(_root()) / ".intern_sessions.json"
    try:
        data = json.loads(sessions_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"session registry unavailable: {sessions_path}: {exc}") from exc
    matches = []
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        entry_name = str(value.get("intern_name") or str(key).split(":", 1)[-1])
        if entry_name != name:
            continue
        intern_dir = str(value.get("intern_dir") or "")
        if not intern_dir or not Path(intern_dir).is_dir():
            continue
        matches.append(value)
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one session registry entry for {name}, found {len(matches)}; pass --project")
    return dict(matches[0])


def _resolve_tmux_session_name(
    name: str,
    project: str = "",
    *,
    entry: dict | None = None,
) -> str:
    resolved_entry = dict(entry or {})
    if not resolved_entry:
        resolved_entry = _session_entry_optional_project(name, project)
    return _entry_tmux_session_name(name, project, resolved_entry)


SHELL_COMMANDS = {"", "bash", "sh", "zsh", "fish", "tmux"}
POLICY_HASH_ENV = {
    "codex": "CODEX_POLICY_ENV_HASH",
    "claude": "CLAUDE_POLICY_ENV_HASH",
}
PROVIDER_POLICY_ARGS_ENV = {
    "codex": "INTERN_CODEX_POLICY_ARGS",
    "claude": "INTERN_CLAUDE_POLICY_ARGS",
}
PROVIDER_DEFAULT_ARGS_ENV = {
    "codex": "INTERN_CODEX_DEFAULT_ARGS",
    "claude": "INTERN_CLAUDE_DEFAULT_ARGS",
}
PROVIDER_REAL_BIN_ENV = {
    "codex": "INTERN_REAL_CODEX",
    "claude": "INTERN_REAL_CLAUDE",
}
PROVIDER_RUNTIME_ENV_KEYS = {
    "claude": {"INTERN_CLAUDE_DISABLE_EXPERIMENTAL_BETAS"},
}
COMMON_RUNTIME_ENV_KEYS = {
    "IS_SANDBOX",
    "FEISHU_DAEMON_ADDR_FILE",
    "INTERN_REAL_CODEX",
    "INTERN_REAL_CLAUDE",
}
COMMON_RUNTIME_ENV_PREFIXES: tuple[str, ...] = ()
ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _capture(args: list[str]) -> str:
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"{args[0]} failed"
        raise RuntimeError(detail)
    return result.stdout


def _run_quiet(args: list[str]) -> None:
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])} failed")


def _first_line(text: str) -> str:
    return (text.strip().splitlines() or [""])[0].strip()


def _tmux_env_value(session_name: str, key: str) -> str:
    try:
        output = _capture(["tmux", "show-environment", "-t", f"={session_name}", key]).strip()
    except Exception:
        return ""
    prefix = f"{key}="
    return output[len(prefix):] if output.startswith(prefix) else ""


def _provider_policy_env_path(provider: str) -> Path:
    return daemon_runtime_dir(_root()) / f"{provider}.env"


def _enterprise_env_source(provider: str) -> str:
    user_env = daemon_user_env_path(_root())
    provider_env = _provider_policy_env_path(provider)
    return (
        "set -a; "
        f"[ -f {shlex.quote(str(user_env))} ] && . {shlex.quote(str(user_env))}; "
        f"[ -f {shlex.quote(str(provider_env))} ] && . {shlex.quote(str(provider_env))}; "
        "set +a"
    )


def _read_exported_env_value(path: Path, key: str) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    prefix = f"export {key}="
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith(prefix):
            continue
        try:
            parsed = shlex.split(stripped[len("export "):], comments=False, posix=True)
        except ValueError:
            return ""
        if len(parsed) != 1 or "=" not in parsed[0]:
            return ""
        return parsed[0].split("=", 1)[1]
    return ""


def _policy_env_managed_keys(provider: str) -> list[str]:
    try:
        for line in _provider_policy_env_path(provider).read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.startswith("# managed_env_keys:"):
                continue
            return line.split()[2:]
    except Exception:
        return []
    return []


def _policy_env_hash(provider: str) -> str:
    key = POLICY_HASH_ENV.get(provider)
    if not key or not _policy_env_managed_keys(provider):
        return ""
    return _read_exported_env_value(_provider_policy_env_path(provider), key)


def _policy_env_manages_key(provider: str, key: str) -> bool:
    return key in _policy_env_managed_keys(provider)


def _resolve_tmux_pane_target(session_name: str) -> str:
    raw = _capture(["tmux", "list-panes", "-s", "-t", f"={session_name}", "-F", "#{window_index}.#{pane_index}"])
    pane = _first_line(raw)
    if not re.match(r"^\d+\.\d+$", pane):
        raise RuntimeError(f"resolveTmuxPaneTarget({session_name}): no pane found (raw=\"{raw.strip()}\")")
    return f"={session_name}:{pane}"


def _is_claude_live(session_name: str) -> bool:
    try:
        current = _first_line(_capture(["tmux", "list-panes", "-t", f"={session_name}", "-F", "#{pane_current_command}"])).lower()
    except Exception:
        return False
    if current not in SHELL_COMMANDS:
        return True
    try:
        pane_pid = _first_line(_capture(["tmux", "list-panes", "-t", f"={session_name}", "-F", "#{pane_pid}"]))
    except Exception:
        return False
    return bool(_provider_child_pid(pane_pid, "claude"))


def _is_uuid(value: object) -> bool:
    return isinstance(value, str) and bool(UUID_RE.match(value.strip()))


def _path_matches_runtime(path: object, intern_dir: str) -> bool:
    if not path or not intern_dir:
        return False
    try:
        return os.path.realpath(os.path.abspath(str(path))) == os.path.realpath(os.path.abspath(intern_dir))
    except Exception:
        return False


def _claude_project_slug(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", os.path.realpath(os.path.abspath(path))).strip("-")


def _claude_project_dir_matches(path: Path, intern_dir: str) -> bool:
    return path.name.strip("-") == _claude_project_slug(intern_dir)


def _json_session_id(data: object) -> str:
    if not isinstance(data, dict):
        return ""
    for key in ("sessionId", "session_id", "id"):
        value = data.get(key)
        if _is_uuid(value):
            return str(value).strip()
    return ""


def _json_cwd_matches(data: object, intern_dir: str) -> bool:
    if not isinstance(data, dict):
        return False
    for key in ("cwd", "currentWorkingDirectory", "workingDirectory", "working_dir", "projectPath", "path"):
        if _path_matches_runtime(data.get(key), intern_dir):
            return True
    return False


def _claude_timestamp_epoch(value: object) -> float:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        return timestamp / 1000.0 if timestamp > 10_000_000_000 else timestamp
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            timestamp = float(text)
            return timestamp / 1000.0 if timestamp > 10_000_000_000 else timestamp
        except ValueError:
            pass
        try:
            normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return 0.0
    return 0.0


def _latest_claude_live_session_id(intern_dir: str, min_mtime: float = 0.0) -> str:
    sessions_dir = Path.home() / ".claude" / "sessions"
    if not sessions_dir.is_dir():
        return ""
    files = []
    try:
        for path in sessions_dir.glob("*.json"):
            try:
                mtime = path.stat().st_mtime
                if min_mtime and mtime < min_mtime:
                    continue
                files.append((mtime, path))
            except OSError:
                pass
    except Exception:
        return ""
    for _, path in sorted(files, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        if not _json_cwd_matches(data, intern_dir):
            continue
        session_id = _json_session_id(data)
        if session_id:
            return session_id
    return ""


def _latest_claude_history_session_id(intern_dir: str, min_mtime: float = 0.0) -> str:
    history_path = Path.home() / ".claude" / "history.jsonl"
    if not history_path.is_file():
        return ""
    try:
        if min_mtime and history_path.stat().st_mtime < min_mtime:
            return ""
    except OSError:
        return ""
    try:
        lines = history_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    for raw_line in reversed(lines[-500:]):
        try:
            data = json.loads(raw_line)
        except Exception:
            continue
        if not _path_matches_runtime(data.get("project"), intern_dir):
            continue
        timestamp = _claude_timestamp_epoch(data.get("timestamp"))
        if min_mtime and (not timestamp or timestamp < min_mtime):
            continue
        session_id = _json_session_id(data)
        if session_id:
            return session_id
    return ""


def _claude_session_id_from_transcript(path: Path, intern_dir: str) -> str:
    filename_session_id = path.stem if _is_uuid(path.stem) else ""
    parent_matches = _claude_project_dir_matches(path.parent, intern_dir)
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for index, raw_line in enumerate(handle):
                if index >= 200:
                    break
                try:
                    data = json.loads(raw_line)
                except Exception:
                    continue
                if _json_cwd_matches(data, intern_dir):
                    return _json_session_id(data) or filename_session_id
                if parent_matches:
                    session_id = _json_session_id(data)
                    if session_id:
                        return session_id
    except Exception:
        return filename_session_id if parent_matches else ""
    return filename_session_id if parent_matches else ""


def _latest_claude_project_session_id(intern_dir: str, min_mtime: float = 0.0) -> str:
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.is_dir():
        return ""
    files = []
    try:
        for path in projects_dir.rglob("*.jsonl"):
            try:
                mtime = path.stat().st_mtime
                if min_mtime and mtime < min_mtime:
                    continue
                files.append((mtime, path))
            except OSError:
                pass
    except Exception:
        return ""
    for _, path in sorted(files, reverse=True):
        session_id = _claude_session_id_from_transcript(path, intern_dir)
        if session_id:
            return session_id
    return ""


def _claude_project_transcript_has_session_id(intern_dir: str, session_id: str) -> bool:
    if not _is_uuid(session_id):
        return False
    projects_dir = Path.home() / ".claude" / "projects"
    if not projects_dir.is_dir():
        return False
    expected = session_id.strip()
    project_dir = projects_dir / ("-" + _claude_project_slug(intern_dir))
    candidates = [project_dir / f"{expected}.jsonl"]
    try:
        candidates.extend(projects_dir.rglob(f"{expected}.jsonl"))
    except Exception:
        pass
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not path.is_file():
            continue
        if _claude_session_id_from_transcript(path, intern_dir) == expected:
            return True
    return False


def _latest_claude_session_id(intern_dir: str) -> str:
    session_id, source = _discover_latest_claude_session_id(intern_dir)
    if session_id:
        _persist_claude_session_id(
            intern_dir,
            session_id,
            source=source,
            resume_mode=_claude_resume_mode_for_source(source),
            intern_name=Path(intern_dir).name,
            project=str(_hook_scope(Path(intern_dir)).get("project") or ""),
            workspace_id=str(_hook_scope(Path(intern_dir)).get("workspace_id") or ""),
        )
    return session_id


def _discover_latest_claude_session_id(intern_dir: str, min_mtime: float = 0.0) -> tuple[str, str]:
    session_id = _latest_claude_live_session_id(intern_dir, min_mtime=min_mtime)
    if session_id:
        if _claude_project_transcript_has_session_id(intern_dir, session_id):
            return session_id, "claude_project_transcript"
        return session_id, "claude_live_session"
    session_id = _latest_claude_history_session_id(intern_dir, min_mtime=min_mtime)
    if session_id:
        if _claude_project_transcript_has_session_id(intern_dir, session_id):
            return session_id, "claude_project_transcript"
        return session_id, "claude_history"
    session_id = _latest_claude_project_session_id(intern_dir, min_mtime=min_mtime)
    if session_id:
        return session_id, "claude_project_transcript"
    return "", ""


def _claude_resume_mode_for_source(source: str) -> str:
    if source in {"claude_live_session", "claude_history"}:
        return "session_id"
    return "resume"


def _normalize_claude_resume_mode(value: object, source: str) -> str:
    mode = str(value or "").strip()
    if mode in {"resume", "session_id"}:
        return mode
    return _claude_resume_mode_for_source(source)


def _claude_session_state_path(intern_dir: str) -> Path:
    return Path(intern_dir) / ".claude_session_state.json"


def _claude_session_pending_path(intern_dir: str) -> Path:
    return Path(intern_dir) / ".claude_session_pending.json"


def _persist_claude_session_id(
    intern_dir: str,
    session_id: str,
    *,
    source: str,
    resume_mode: str = "",
    intern_name: str = "",
    project: str = "",
    workspace_id: str = "",
) -> str:
    if not _is_uuid(session_id):
        return ""
    runtime_path = Path(intern_dir)
    runtime_path.mkdir(parents=True, exist_ok=True)
    state_path = _claude_session_state_path(intern_dir)
    pending_path = _claude_session_pending_path(intern_dir)
    lock_path = runtime_path / ".claude_session_state.lock"
    payload = {
        "schema": "intern-agents.claude-session-state.v1",
        "session_id": session_id,
        "source": source,
        "resume_mode": _normalize_claude_resume_mode(resume_mode, source),
        "updated_at": time.time(),
    }
    if intern_name:
        payload["intern_name"] = intern_name
    if project:
        payload["project"] = project
    if workspace_id:
        payload["workspace_id"] = workspace_id
    with lock_path.open("w", encoding="utf-8") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            tmp = state_path.with_name(state_path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp.replace(state_path)
            if pending_path.exists():
                pending_path.unlink()
            try:
                os.chmod(state_path, 0o600)
            except OSError:
                pass
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    if intern_name and project:
        try:
            entry = _session_entry(intern_name, project)
            entry_workspace = str(entry.get("workspace_id") or workspace_id or "")
            if entry_workspace:
                _write_session_entry(
                    _session_registry_key(intern_name, project, entry_workspace),
                    {
                        "claude_session_id": session_id,
                        "claude_session_source": source,
                        "claude_session_resume_mode": payload["resume_mode"],
                        "claude_session_updated_at": payload["updated_at"],
                    },
                )
        except Exception:
            pass
    return session_id


def _clear_claude_session_id(
    intern_dir: str,
    *,
    intern_name: str = "",
    project: str = "",
    workspace_id: str = "",
) -> dict:
    runtime_path = Path(intern_dir)
    runtime_path.mkdir(parents=True, exist_ok=True)
    state_path = _claude_session_state_path(intern_dir)
    pending_path = _claude_session_pending_path(intern_dir)
    lock_path = runtime_path / ".claude_session_state.lock"
    removed_state = False
    pending_since = time.time()
    with lock_path.open("w", encoding="utf-8") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            if state_path.exists():
                state_path.unlink()
                removed_state = True
            tmp_path = state_path.with_name(state_path.name + ".tmp")
            if tmp_path.exists():
                tmp_path.unlink()
            pending_payload = {
                "schema": "intern-agents.claude-session-pending.v1",
                "pending_since": pending_since,
                "intern_name": intern_name,
                "project": project,
                "workspace_id": workspace_id,
            }
            tmp_pending = pending_path.with_name(pending_path.name + ".tmp")
            tmp_pending.write_text(json.dumps(pending_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            tmp_pending.replace(pending_path)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)

    removed_registry = False
    if intern_name and project:
        sessions_file = Path(_root()) / ".intern_sessions.json"
        registry_lock = Path(_root()) / ".intern_sessions.lock"
        with registry_lock.open("w", encoding="utf-8") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                try:
                    data = json.loads(sessions_file.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
                key = _session_registry_key(intern_name, project, workspace_id)
                entry = data.get(key) if isinstance(data, dict) else None
                if isinstance(entry, dict):
                    for field in (
                        "claude_session_id",
                        "claude_session_source",
                        "claude_session_resume_mode",
                        "claude_session_updated_at",
                    ):
                        if field in entry:
                            entry.pop(field, None)
                            removed_registry = True
                    if removed_registry:
                        tmp = sessions_file.with_suffix(sessions_file.suffix + ".tmp")
                        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                        tmp.replace(sessions_file)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    return {"state": removed_state, "registry": removed_registry, "pending_since": pending_since}


def _pending_claude_session_since(
    intern_dir: str,
    *,
    intern_name: str = "",
    project: str = "",
    workspace_id: str = "",
) -> float:
    pending_path = _claude_session_pending_path(intern_dir)
    if not pending_path.exists():
        return 0.0
    try:
        data = json.loads(pending_path.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    if not isinstance(data, dict) or data.get("schema") != "intern-agents.claude-session-pending.v1":
        return 0.0
    for key, expected in (("intern_name", intern_name), ("project", project), ("workspace_id", workspace_id)):
        actual = str(data.get(key) or "")
        if expected and actual and actual != expected:
            return 0.0
    try:
        return max(0.0, float(data.get("pending_since") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _persisted_claude_session_id(
    intern_dir: str,
    *,
    intern_name: str = "",
    project: str = "",
    workspace_id: str = "",
) -> tuple[str, str, str]:
    state_path = _claude_session_state_path(intern_dir)
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            return "", f"persisted state corrupted: {state_path}: {exc}", ""
        if not isinstance(data, dict):
            return "", f"persisted state corrupted: {state_path}: expected object", ""
        if data.get("schema") != "intern-agents.claude-session-state.v1":
            return "", f"persisted state corrupted: {state_path}: unknown schema", ""
        for key, expected in (("intern_name", intern_name), ("project", project), ("workspace_id", workspace_id)):
            actual = str(data.get(key) or "")
            if expected and actual and actual != expected:
                return "", f"persisted state corrupted: {state_path}: {key} mismatch", ""
        session_id = str(data.get("session_id") or "").strip()
        if _is_uuid(session_id):
            source = str(data.get("source") or "persisted_state")
            mode = _normalize_claude_resume_mode(data.get("resume_mode"), source)
            if mode == "session_id" and _claude_project_transcript_has_session_id(intern_dir, session_id):
                _persist_claude_session_id(
                    intern_dir,
                    session_id,
                    source="claude_project_transcript",
                    resume_mode="resume",
                    intern_name=intern_name,
                    project=project,
                    workspace_id=workspace_id,
                )
                return session_id, "", "resume"
            return session_id, "", mode
        return "", f"persisted state corrupted: {state_path}: missing valid session_id", ""
    if intern_name and project:
        try:
            entry = _session_entry(intern_name, project)
        except Exception:
            entry = {}
        session_id = str(entry.get("claude_session_id") or "").strip() if isinstance(entry, dict) else ""
        if session_id:
            if _is_uuid(session_id):
                source = str(entry.get("claude_session_source") or "session_registry")
                mode = _normalize_claude_resume_mode(entry.get("claude_session_resume_mode"), source)
                if mode == "session_id" and _claude_project_transcript_has_session_id(intern_dir, session_id):
                    source = "claude_project_transcript"
                    mode = "resume"
                _persist_claude_session_id(
                    intern_dir,
                    session_id,
                    source=source,
                    resume_mode=mode,
                    intern_name=intern_name,
                    project=project,
                    workspace_id=workspace_id or str(entry.get("workspace_id") or ""),
                )
                return session_id, "", mode
            return "", "persisted registry corrupted: invalid claude_session_id", ""
    return "", "never discovered", ""


def _resolve_claude_session_id_for_resume(args: argparse.Namespace, runtime: dict, pane: str) -> tuple[str, str, str]:
    intern_dir = str(runtime["intern_dir"])
    project = str(runtime.get("project") or args.project or "")
    workspace_id = str(runtime.get("workspace_id") or "")
    pending_since = _pending_claude_session_since(
        intern_dir,
        intern_name=args.name,
        project=project,
        workspace_id=workspace_id,
    )
    if pending_since:
        session_id, source = _discover_latest_claude_session_id(intern_dir, min_mtime=pending_since)
        if session_id:
            mode = _claude_resume_mode_for_source(source)
            _persist_claude_session_id(
                intern_dir,
                session_id,
                source=source,
                resume_mode=mode,
                intern_name=args.name,
                project=project,
                workspace_id=workspace_id,
            )
            return session_id, "", mode
        return "", "claude session id unavailable: pending launch session not discovered", ""
    persisted, reason, persisted_mode = _persisted_claude_session_id(
        intern_dir,
        intern_name=args.name,
        project=project,
        workspace_id=workspace_id,
    )
    if persisted:
        return persisted, "", persisted_mode
    session_id, source = _discover_latest_claude_session_id(intern_dir)
    if session_id:
        mode = _claude_resume_mode_for_source(source)
        _persist_claude_session_id(
            intern_dir,
            session_id,
            source=source,
            resume_mode=mode,
            intern_name=args.name,
            project=project,
            workspace_id=workspace_id,
        )
        return session_id, "", mode
    session_id = _capture_claude_resume_session_id_from_pane(pane)
    if session_id:
        _persist_claude_session_id(
            intern_dir,
            session_id,
            source="tmux_pane_resume_hint",
            resume_mode="resume",
            intern_name=args.name,
            project=project,
            workspace_id=workspace_id,
        )
        return session_id, "", "resume"
    return "", f"claude session id unavailable: {reason}", ""


def _capture_claude_resume_session_id_from_pane(pane: str) -> str:
    try:
        pane_text = _capture(["tmux", "capture-pane", "-t", pane, "-p", "-J", "-S", "-200"])
    except Exception:
        return ""
    match = re.search(r"claude --resume ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", pane_text)
    return match.group(1) if match else ""


def _daemon_port() -> str:
    try:
        data = json.loads(Path("/tmp/feishu_daemon.json").read_text(encoding="utf-8"))
        port = str(data.get("http_port") or "")
        return port if port.isdigit() else ""
    except Exception:
        return ""


def _offline_notify(name: str, project: str = "", workspace_id: str = "") -> str:
    port = _daemon_port()
    if not port:
        return "true"
    payload = {"intern_name": name}
    if project:
        payload["project"] = project
    if workspace_id:
        payload["workspace_id"] = workspace_id
    return (
        f"curl -s -X POST http://localhost:{port}/api/intern/offline "
        f"-H 'Content-Type: application/json' -d {shlex.quote(json.dumps(payload, separators=(',', ':')))} "
        "> /dev/null 2>&1"
    )


def _request_refresh_later(name: str, project: str = "", workspace_id: str = "") -> None:
    port = _daemon_port()
    if not port:
        return
    command = _request_refresh_shell(port, name, project=project, workspace_id=workspace_id, delay_seconds=3)
    if not command:
        return
    try:
        subprocess.Popen(["bash", "-lc", command], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _request_refresh_shell(
    port: str,
    name: str,
    project: str = "",
    workspace_id: str = "",
    delay_seconds: int = 3,
    background: bool = False,
) -> str:
    if not port:
        return ""
    payload = {"intern_name": name}
    if project:
        payload["project"] = project
    if workspace_id:
        payload["workspace_id"] = workspace_id
    command = (
        f"sleep {int(delay_seconds)}; "
        f"curl -s -X POST http://localhost:{port}/api/intern/request_refresh "
        f"-H 'Content-Type: application/json' -d {shlex.quote(json.dumps(payload, separators=(',', ':')))} "
        "> /dev/null 2>&1"
    )
    if background:
        return f"({command}) &"
    return command


def _shell_command_prefix(command: str) -> str:
    stripped = command.rstrip()
    if not stripped:
        return ""
    separator = " " if stripped.endswith("&") else "; "
    return f"{stripped}{separator}"


def _parent_pid(pid: int) -> int:
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    close = raw.rfind(")")
    if close < 0:
        return 0
    fields = raw[close + 2:].split()
    if len(fields) < 2 or not fields[1].isdigit():
        return 0
    return int(fields[1])


def _current_process_descends_from(ancestor_pid: str) -> bool:
    if not str(ancestor_pid or "").isdigit():
        return False
    ancestor = int(ancestor_pid)
    pid = os.getpid()
    seen: set[int] = set()
    while pid > 0 and pid not in seen:
        if pid == ancestor:
            return True
        seen.add(pid)
        pid = _parent_pid(pid)
    return False


def _resume_hint_command(name: str, project: str, session_type: str) -> str:
    return " ".join(shlex.quote(str(item)) for item in [
        sys.executable or "python3",
        str(_cli_root() / "internctl.py"),
        "session",
        "resume",
        name,
        "--project",
        project,
        "--type",
        session_type,
    ])


def _hook_scope(intern_dir: Path) -> dict:
    try:
        state = json.loads((intern_dir / ".hook_state.json").read_text(encoding="utf-8"))
    except Exception:
        return {}
    resolver = state.get("metadata_resolver") if isinstance(state.get("metadata_resolver"), dict) else {}
    return {
        "project": state.get("project") or resolver.get("project") or "",
        "workspace_id": state.get("workspace_id") or resolver.get("workspace_id") or "",
    }


def _runtime_dir_matches_project(intern_dir: str, project: str, entry: dict | None = None) -> bool:
    if not project:
        return True
    scope = _hook_scope(Path(intern_dir))
    scopes = {
        str(scope.get("project") or ""),
        str(scope.get("workspace_id") or ""),
    }
    if entry:
        scopes.add(str(entry.get("project") or ""))
        scopes.add(str(entry.get("workspace_id") or ""))
    return project in {item for item in scopes if item}


def _resume_runtime(args: argparse.Namespace, session_name: str = "") -> dict:
    entry = {}
    try:
        entry = _session_entry(args.name, args.project)
    except Exception:
        entry = {}
    tmux_dir = _tmux_env_value(session_name or args.name, "INTERN_DIR")
    entry_dir = str(entry.get("intern_dir") or "")
    intern_dir = next((
        candidate for candidate in (tmux_dir, entry_dir)
        if candidate
        and Path(candidate).is_dir()
        and _runtime_dir_matches_project(candidate, args.project, entry if candidate == entry_dir else None)
    ), "")
    if not intern_dir:
        raise RuntimeError(f"enterprise runtime dir missing for {args.project}:{args.name}")
    scope = _hook_scope(Path(intern_dir))
    return {
        "intern_dir": intern_dir,
        "project": args.project or scope.get("project") or str(entry.get("project") or ""),
        "workspace_id": scope.get("workspace_id") or str(entry.get("workspace_id") or ""),
    }


def _resume_result(
    args: argparse.Namespace,
    success: bool,
    reason: str = "",
    session_id: str = "",
    resume_mode: str = "",
) -> dict:
    payload = {
        "schema": "intern-agents.session-resume.v1",
        "name": args.name,
        "type": getattr(args, "type", "codex"),
        "tmux_session": getattr(args, "tmux_session", "") or "",
        "success": success,
        "reason": reason,
        "session_id": session_id,
    }
    if resume_mode:
        payload["resume_mode"] = resume_mode
    return payload


def _emit_resume_result(args: argparse.Namespace, payload: dict) -> int:
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload.get("success"):
        suffix = f" {payload.get('session_id')}" if payload.get("session_id") else ""
        print(f"{args.name}: resumed{suffix}")
    else:
        print(f"{args.name}: resume failed: {payload.get('reason')}", file=sys.stderr)
    return 0 if payload.get("success") else 1


def _claude_resume_shell_command(args: argparse.Namespace, runtime: dict, session_id: str, resume_mode: str) -> str:
    beta_env = "" if _policy_env_manages_key("claude", "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS") else "export CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS=1; "
    resume_hint = _resume_hint_command(args.name, str(runtime.get("project") or ""), "claude")
    claude_session_args = (
        f"--session-id {session_id}"
        if resume_mode == "session_id"
        else f"--resume {session_id}"
    )
    request_refresh = _request_refresh_shell(
        _daemon_port(),
        args.name,
        str(runtime.get("project") or ""),
        str(runtime.get("workspace_id") or ""),
        background=True,
    )
    return (
        f"source ~/.bashrc 2>/dev/null; {_enterprise_env_source('claude')}; "
        f"{beta_env}"
        f"{_shell_command_prefix(request_refresh)}"
        "claude_bin=\"${INTERN_REAL_CLAUDE:-}\"; "
        "if [ -z \"$claude_bin\" ] || [ ! -x \"$claude_bin\" ]; then echo \"[internctl] claude binary unavailable\" >&2; exit 127; fi; "
        "if [ -n \"${INTERN_CLAUDE_POLICY_ARGS:-}\" ]; then export INTERN_CLAUDE_DEFAULT_ARGS=\"$INTERN_CLAUDE_POLICY_ARGS\"; fi; "
        "\"$claude_bin\" ${INTERN_CLAUDE_DEFAULT_ARGS:-} "
        f"{claude_session_args} ; "
        "status=$?; "
        f"{_offline_notify(args.name, str(runtime.get('project') or ''), str(runtime.get('workspace_id') or ''))}; "
        "echo; "
        "echo \"[internctl] Claude exited with status $status.\"; "
        "echo \"[internctl] Resume this intern:\"; "
        f"echo {shlex.quote('  ' + resume_hint)}; "
        "exec bash -l"
    )


def _set_claude_resume_state_env(session_name: str, runtime: dict) -> None:
    settings_path = Path(runtime["intern_dir"]) / ".claude" / "settings.json"
    try:
        mtime = _first_line(_capture(["stat", "-c", "%Y", str(settings_path)]))
        _run_quiet(["tmux", "set-environment", "-t", f"={session_name}", "CLAUDE_SETTINGS_MTIME", mtime])
        _run_quiet(["tmux", "set-environment", "-t", f"={session_name}", "CLAUDE_POLICY_ENV_HASH", _policy_env_hash("claude")])
    except Exception:
        pass


def _exec_current_pane_resume_shell(resume_cmd: str) -> None:
    os.execvp("bash", ["bash", "-lc", resume_cmd])


def _set_current_env_values(values: dict[str, str], provider: str = "") -> None:
    for key, value in values.items():
        if not value:
            continue
        if not ENV_NAME_RE.match(str(key)):
            continue
        if provider and not _is_preservable_runtime_env_key(provider, str(key)):
            continue
        os.environ[str(key)] = str(value)


def _unset_current_env_values(keys: list[str]) -> None:
    for key in keys:
        if ENV_NAME_RE.match(str(key)):
            os.environ.pop(str(key), None)


def _is_container_environment() -> bool:
    if os.environ.get("IS_SANDBOX") == "1":
        return True
    if Path("/.dockerenv").exists() or Path("/run/.containerenv").exists():
        return True
    if os.environ.get("container") or os.environ.get("KUBERNETES_SERVICE_HOST"):
        return True
    try:
        if re.search(r"(docker|containerd|kubepods|podman|lxc)", Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")):
            return True
    except Exception:
        pass
    try:
        result = subprocess.run(["systemd-detect-virt", "-c"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return result.returncode == 0
    except Exception:
        return False


def _should_enable_root_bypass() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0 and _is_container_environment()


def _claude_args_use_root_bypass(args: str) -> bool:
    try:
        tokens = shlex.split(args or "")
    except ValueError:
        tokens = str(args or "").split()
    for index, token in enumerate(tokens):
        if token == "--dangerously-skip-permissions":
            return True
        if token == "--permission-mode=bypassPermissions":
            return True
        if token == "--permission-mode" and index + 1 < len(tokens) and tokens[index + 1] == "bypassPermissions":
            return True
    return False


def _resolve_claude_default_args(claude_bin: str) -> str:
    try:
        result = subprocess.run([claude_bin or "claude", "--help"], capture_output=True, text=True)
        help_output = (result.stdout or "") + (result.stderr or "")
    except Exception:
        help_output = ""
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if "--permission-mode" in help_output:
        if is_root and not _should_enable_root_bypass():
            return "--permission-mode acceptEdits"
        return "--permission-mode bypassPermissions"
    if not is_root and "--dangerously-skip-permissions" in help_output:
        return "--dangerously-skip-permissions"
    return ""


def _sanitize_claude_resume_env(values: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    sanitized = dict(values)
    policy_args = str(sanitized.get("INTERN_CLAUDE_POLICY_ARGS") or "")
    if policy_args:
        sanitized["INTERN_CLAUDE_DEFAULT_ARGS"] = policy_args
        return sanitized, []
    default_args = str(sanitized.get("INTERN_CLAUDE_DEFAULT_ARGS") or "")
    is_root = hasattr(os, "geteuid") and os.geteuid() == 0
    if default_args and not (is_root and not _should_enable_root_bypass() and _claude_args_use_root_bypass(default_args)):
        return sanitized, []
    resolved = _resolve_claude_default_args(str(sanitized.get("INTERN_REAL_CLAUDE") or shutil.which("claude") or "claude"))
    if resolved:
        sanitized["INTERN_CLAUDE_DEFAULT_ARGS"] = resolved
        return sanitized, []
    sanitized.pop("INTERN_CLAUDE_DEFAULT_ARGS", None)
    return sanitized, ["INTERN_CLAUDE_DEFAULT_ARGS"]


def _run_claude_resume(args: argparse.Namespace) -> dict:
    session_name = getattr(args, "tmux_session", "") or _resolve_tmux_session_name(args.name, args.project)
    setattr(args, "tmux_session", session_name)
    try:
        pane = _resolve_tmux_pane_target(session_name)
    except Exception as exc:
        return _resume_result(args, False, f"resolve pane target failed: {exc}")
    pane_pid = _pane_pid(pane)
    same_pane_invocation = _current_process_descends_from(pane_pid)
    preserved_env = _tmux_runtime_env(session_name, "claude")
    launch_env = provider_launch_env_values(_root(), "claude")
    is_live = False if same_pane_invocation else _is_claude_live(session_name)
    if is_live:
        preserved_env.update(_provider_process_env(_provider_child_pid(pane_pid, "claude"), "claude"))
        try:
            _run_quiet(["tmux", "send-keys", "-t", pane, "/exit", "Enter"])
        except Exception as exc:
            return _resume_result(args, False, f"send /exit failed: {exc}")

        exited = False
        bg_prompt_handled = False
        for _ in range(30):
            time.sleep(0.5)
            try:
                if not _is_claude_live(session_name):
                    exited = True
                    break
            except Exception:
                exited = True
                break
            if not bg_prompt_handled:
                try:
                    pane_text = _capture(["tmux", "capture-pane", "-t", pane, "-p"])
                    if "Background work is running" in pane_text:
                        _run_quiet(["tmux", "send-keys", "-t", pane, "Enter"])
                        bg_prompt_handled = True
                except Exception:
                    pass
        if not exited:
            try:
                _run_quiet(["tmux", "send-keys", "-t", pane, "C-c"])
            except Exception:
                pass
            time.sleep(2)

    try:
        runtime = _resume_runtime(args, session_name)
    except Exception as exc:
        return _resume_result(args, False, f"resolve runtime failed: {exc}")

    session_id, unavailable_reason, resume_mode = _resolve_claude_session_id_for_resume(args, runtime, pane)
    if not session_id:
        return _resume_result(args, False, unavailable_reason)

    try:
        current = _first_line(_capture(["tmux", "list-panes", "-t", f"={session_name}", "-F", "#{pane_current_command}"])).lower()
    except Exception:
        current = ""
    if current not in SHELL_COMMANDS and not same_pane_invocation:
        try:
            _run_quiet(["tmux", "send-keys", "-t", pane, "C-c"])
        except Exception:
            pass
        time.sleep(2)

    resume_cmd = _claude_resume_shell_command(args, runtime, session_id, resume_mode)
    preserved_env, unset_env = _sanitize_claude_resume_env(preserved_env)
    launch_env, launch_unset_env = _sanitize_claude_resume_env(launch_env)
    unset_env = list(dict.fromkeys(unset_env + launch_unset_env))
    try:
        _set_tmux_env_values(session_name, preserved_env, "claude")
        _unset_tmux_env_values(session_name, unset_env)
        _set_tmux_launch_env_values(session_name, launch_env)
        _set_tmux_runtime_env(session_name, runtime, "claude", intern_name=args.name)
        if same_pane_invocation:
            if getattr(args, "json", False):
                return _resume_result(args, False, "same-pane claude resume requires interactive output; omit --json or run from another shell", session_id, resume_mode)
            _set_current_env_values(preserved_env, "claude")
            _unset_current_env_values(unset_env)
            _set_current_launch_env_values(launch_env)
            _set_current_env_values(_runtime_env_values(session_name, runtime, intern_name=args.name))
            _set_claude_resume_state_env(session_name, runtime)
            _exec_current_pane_resume_shell(resume_cmd)
            return _resume_result(args, True, session_id=session_id, resume_mode=resume_mode)
        _run_quiet(["tmux", "respawn-pane", "-k", "-t", pane, "-c", str(runtime["intern_dir"]), resume_cmd])
    except Exception as exc:
        return _resume_result(args, False, f"respawn resume failed: {exc}", session_id, resume_mode)
    if not _wait_for_claude_child(pane):
        return _resume_result(args, False, "respawned Claude did not become live", session_id, resume_mode)
    _set_claude_resume_state_env(session_name, runtime)
    _request_refresh_later(args.name, str(runtime.get("project") or ""), str(runtime.get("workspace_id") or ""))
    return _resume_result(args, True, session_id=session_id, resume_mode=resume_mode)


def _provider_process_matches(args: str, provider: str) -> bool:
    try:
        tokens = shlex.split(args or "")
    except ValueError:
        tokens = str(args or "").split()
    if not tokens:
        return False
    first = os.path.basename(tokens[0]).lower()
    provider = provider.lower()
    if first in SHELL_COMMANDS:
        for token in tokens[1:]:
            if token.startswith("-") and "c" in token:
                return False
            if os.path.basename(token).lower() == provider:
                return True
        return False
    if first == provider:
        return True
    for token in tokens[1:]:
        if os.path.basename(token).lower() == provider:
            return True
    return False


def _provider_child_pid(pane_pid: str, provider: str) -> str:
    if not pane_pid or not str(pane_pid).isdigit():
        return ""
    # Build a PPID -> [(pid, args)] index from a full ps table. BSD/macOS ps
    # has no `--ppid` option, so the previous per-parent `ps --ppid` call failed
    # on macOS and made provider-liveness checks always return "" (offline).
    children_by_ppid: dict[str, list[tuple[str, str]]] = {}
    try:
        table = _capture(["ps", "-Ao", "pid=,ppid=,args="])
    except Exception:
        return ""
    for line in table.splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        pid, ppid = parts[0], parts[1]
        args = parts[2] if len(parts) > 2 else ""
        if pid.isdigit() and ppid.isdigit():
            children_by_ppid.setdefault(ppid, []).append((pid, args))
    seen: set[str] = set()
    queue = [str(pane_pid)]
    while queue:
        parent = queue.pop(0)
        if parent in seen:
            continue
        seen.add(parent)
        for pid, args in children_by_ppid.get(parent, []):
            if pid in seen:
                continue
            if _provider_process_matches(args, provider):
                return pid
            queue.append(pid)
    return ""


def _pane_has_codex_child(pane_pid: str) -> bool:
    return bool(_provider_child_pid(pane_pid, "codex"))


def _pane_has_claude_child(pane_pid: str) -> bool:
    return bool(_provider_child_pid(pane_pid, "claude"))


def _provider_process_env(pid: str, provider: str) -> dict[str, str]:
    if not pid or not pid.isdigit():
        return {}
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except Exception:
        return {}
    env: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key_bytes, value_bytes = item.split(b"=", 1)
        try:
            key = key_bytes.decode("utf-8", errors="strict")
            value = value_bytes.decode("utf-8", errors="surrogateescape")
        except Exception:
            continue
        if not ENV_NAME_RE.match(key):
            continue
        if _is_preservable_runtime_env_key(provider, key):
            env[key] = value
    return env


def _tmux_runtime_env(session_name: str, provider: str) -> dict[str, str]:
    try:
        raw = _capture(["tmux", "show-environment", "-t", f"={session_name}"])
    except Exception:
        return {}
    env: dict[str, str] = {}
    for line in raw.splitlines():
        if not line or line.startswith("-") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if not ENV_NAME_RE.match(key):
            continue
        if _is_preservable_runtime_env_key(provider, key):
            env[key] = value
    return env


def _is_preservable_runtime_env_key(provider: str, key: str) -> bool:
    return (
        key in COMMON_RUNTIME_ENV_KEYS
        or key in PROVIDER_RUNTIME_ENV_KEYS.get(provider, set())
        or key == POLICY_HASH_ENV.get(provider)
        or key == PROVIDER_POLICY_ARGS_ENV.get(provider)
        or key == PROVIDER_DEFAULT_ARGS_ENV.get(provider)
        or key == PROVIDER_REAL_BIN_ENV.get(provider)
        or key in _policy_env_managed_keys(provider)
        or any(key.startswith(prefix) for prefix in COMMON_RUNTIME_ENV_PREFIXES)
    )


def _set_tmux_env_values(session_name: str, values: dict[str, str], provider: str = "") -> None:
    for key, value in values.items():
        if not ENV_NAME_RE.match(str(key)):
            continue
        if provider and not _is_preservable_runtime_env_key(provider, str(key)):
            continue
        try:
            _run_quiet(["tmux", "set-environment", "-t", f"={session_name}", str(key), str(value)])
        except Exception:
            pass


def _unset_tmux_env_values(session_name: str, keys: list[str]) -> None:
    for key in keys:
        if not ENV_NAME_RE.match(str(key)):
            continue
        try:
            _run_quiet(["tmux", "set-environment", "-u", "-t", f"={session_name}", str(key)])
        except Exception:
            pass


def _set_tmux_launch_env_values(session_name: str, values: dict[str, str]) -> None:
    sync_tmux_launch_env(session_name, values)


def _set_current_launch_env_values(values: dict[str, str]) -> None:
    for key, value in values.items():
        if not ENV_NAME_RE.match(str(key)):
            continue
        if value:
            os.environ[str(key)] = str(value)
        else:
            os.environ.pop(str(key), None)


def _pane_pid(pane_target: str) -> str:
    try:
        return _first_line(_capture(["tmux", "list-panes", "-t", pane_target, "-F", "#{pane_pid}"]))
    except Exception:
        return ""


def _resolve_live_codex_pane(session_name: str) -> dict:
    raw = _capture([
        "tmux", "list-panes", "-s", "-t", f"={session_name}",
        "-F", "#{window_active}\t#{pane_active}\t#{window_index}.#{pane_index}\t#{pane_pid}\t#{pane_current_path}",
    ])
    candidates = []
    for index, line in enumerate(raw.strip().splitlines()):
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        window_active, pane_active, pane, pane_pid = parts[:4]
        cwd = "\t".join(parts[4:])
        child_pid = _provider_child_pid(pane_pid, "codex") if re.match(r"^\d+\.\d+$", pane) else ""
        if child_pid:
            candidates.append((int(window_active or 0), int(pane_active or 0), -index, pane, cwd, child_pid))
    if not candidates:
        raise RuntimeError(f"resolveLiveCodexPane({session_name}): no live Codex pane found")
    candidates.sort(reverse=True)
    _, _, _, pane, cwd, child_pid = candidates[0]
    return {"target": f"={session_name}:{pane}", "cwd": cwd, "child_pid": child_pid}


def _latest_codex_session_id(intern_dir: str) -> str:
    sessions_dir = Path.home() / ".codex" / "sessions"
    if not sessions_dir.is_dir():
        return ""
    files = []
    try:
        for path in sessions_dir.rglob("rollout-*.jsonl"):
            try:
                files.append((path.stat().st_mtime, path))
            except OSError:
                pass
    except Exception:
        return ""
    for _, path in sorted(files, reverse=True):
        try:
            head = path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
        except Exception:
            continue
        if intern_dir not in head:
            continue
        match = re.search(r"rollout-[0-9TZ:-]+-([0-9a-f-]{36})\.jsonl$", str(path))
        if match:
            return match.group(1)
    return ""


def _wait_for_codex_child(pane_target: str) -> bool:
    for _ in range(20):
        time.sleep(0.5)
        try:
            pane_pid = _first_line(_capture(["tmux", "list-panes", "-t", pane_target, "-F", "#{pane_pid}"]))
        except Exception:
            return False
        if _pane_has_codex_child(pane_pid):
            return True
    return False


def _wait_for_claude_child(pane_target: str) -> bool:
    for _ in range(20):
        time.sleep(0.5)
        try:
            pane_pid = _first_line(_capture(["tmux", "list-panes", "-t", pane_target, "-F", "#{pane_pid}"]))
        except Exception:
            return False
        if _pane_has_claude_child(pane_pid):
            return True
    return False


def _runtime_env_values(session_name: str, runtime: dict, intern_name: str = "") -> dict[str, str]:
    return session_runtime_launch_env_values(
        work_root=_root(),
        session_name=session_name,
        intern_name=intern_name or session_name,
        intern_dir=str(runtime.get("intern_dir") or ""),
        project=str(runtime.get("project") or ""),
        workspace_id=str(runtime.get("workspace_id") or ""),
        daemon_addr_file=os.environ.get("FEISHU_DAEMON_ADDR_FILE", "/tmp/feishu_daemon.json"),
        ctl_python=sys.executable or shutil.which("python3") or "python3",
        ctl_path=str(_cli_root() / "internctl.py"),
        ready_channel=tmux_ready_channel(session_name),
    )


def _set_tmux_runtime_env(session_name: str, runtime: dict, provider: str = "", intern_name: str = "") -> None:
    for key, value in _runtime_env_values(session_name, runtime, intern_name=intern_name).items():
        if not value:
            continue
        try:
            _run_quiet(["tmux", "set-environment", "-t", f"={session_name}", key, str(value)])
        except Exception:
            pass
    hash_key = POLICY_HASH_ENV.get(provider)
    if hash_key:
        try:
            _run_quiet(["tmux", "set-environment", "-t", f"={session_name}", hash_key, _policy_env_hash(provider)])
        except Exception:
            pass


def _run_codex_resume(args: argparse.Namespace) -> dict:
    session_name = getattr(args, "tmux_session", "") or _resolve_tmux_session_name(args.name, args.project)
    setattr(args, "tmux_session", session_name)
    preserved_env = _tmux_runtime_env(session_name, "codex")
    launch_env = provider_launch_env_values(_root(), "codex")
    try:
        pane = _resolve_live_codex_pane(session_name)
    except Exception:
        try:
            pane = {"target": _resolve_tmux_pane_target(session_name), "cwd": "", "child_pid": ""}
        except Exception as exc:
            return _resume_result(args, False, f"codex pane unavailable: {exc}")
    try:
        runtime = _resume_runtime(args, session_name)
    except Exception as exc:
        return _resume_result(args, False, f"resolve runtime failed: {exc}")
    intern_dir = str(runtime["intern_dir"])
    session_id = _latest_codex_session_id(intern_dir)
    if not session_id:
        return _resume_result(args, False, "codex session id unavailable")
    resume_arg = f"resume {session_id}"
    preserved_env.update(_provider_process_env(str(pane.get("child_pid") or ""), "codex"))
    resume_hint = _resume_hint_command(args.name, str(runtime.get("project") or ""), "codex")
    request_refresh = _request_refresh_shell(
        _daemon_port(),
        args.name,
        str(runtime.get("project") or ""),
        str(runtime.get("workspace_id") or ""),
        background=True,
    )
    resume_cmd = (
        "source ~/.bashrc 2>/dev/null; "
        f"{_enterprise_env_source('codex')}; "
        f"{_shell_command_prefix(request_refresh)}"
        "codex_bin=\"${INTERN_REAL_CODEX:-}\"; "
        "if [ -z \"$codex_bin\" ] || [ ! -x \"$codex_bin\" ]; then echo \"[internctl] codex binary unavailable\" >&2; exit 127; fi; "
        "if [ -n \"${INTERN_CODEX_POLICY_ARGS:-}\" ]; then export INTERN_CODEX_DEFAULT_ARGS=\"$INTERN_CODEX_POLICY_ARGS\"; fi; "
        "\"$codex_bin\" ${INTERN_CODEX_DEFAULT_ARGS:---dangerously-bypass-approvals-and-sandbox} "
        f"-C {shlex.quote(intern_dir)} {resume_arg} ; "
        "status=$?; "
        f"{_offline_notify(args.name, str(runtime.get('project') or ''), str(runtime.get('workspace_id') or ''))}; "
        "echo; "
        "echo \"[internctl] Codex exited with status $status.\"; "
        "echo \"[internctl] Resume this intern:\"; "
        f"echo {shlex.quote('  ' + resume_hint)}; "
        "exec bash -l"
    )
    try:
        _set_tmux_env_values(session_name, preserved_env, "codex")
        _set_tmux_launch_env_values(session_name, launch_env)
        _set_tmux_runtime_env(session_name, runtime, "codex", intern_name=args.name)
        _run_quiet(["tmux", "respawn-pane", "-k", "-t", pane["target"], "-c", intern_dir, resume_cmd])
    except Exception as exc:
        return _resume_result(args, False, f"respawn resume failed: {exc}", session_id)
    if not _wait_for_codex_child(pane["target"]):
        return _resume_result(args, False, "respawned Codex did not become live", session_id)
    try:
        mtime = _first_line(_capture(["stat", "-c", "%Y", str(Path(intern_dir) / ".codex" / "config.toml")]))
        _run_quiet(["tmux", "set-environment", "-t", f"={session_name}", "CODEX_SETTINGS_MTIME", mtime])
        _run_quiet(["tmux", "set-environment", "-t", f"={session_name}", "CODEX_POLICY_ENV_HASH", _policy_env_hash("codex")])
    except Exception:
        pass
    _request_refresh_later(args.name, str(runtime.get("project") or ""), str(runtime.get("workspace_id") or ""))
    return _resume_result(args, True, session_id=session_id)


def _session_script(session_type: str) -> Path:
    script_name = "intern_start.sh" if session_type == "claude" else "intern_start_codex.sh"
    return _cli_root() / "scripts" / script_name


def _run_enterprise_start(args: argparse.Namespace, entry: dict) -> int:
    try:
        intern_dir = str(entry.get("intern_dir") or "")
        if not intern_dir:
            raise RuntimeError("session registry entry missing intern_dir")
        tmux_session = _entry_tmux_session_name(args.name, args.project, entry)
        entry["tmux_session"] = tmux_session
        _write_session_entry(
            _session_registry_key(args.name, args.project, str(entry.get("workspace_id") or "")),
            {
                "type": getattr(args, "type", "codex"),
                "intern_name": args.name,
                "project": args.project,
                "workspace_id": str(entry.get("workspace_id") or ""),
                "intern_dir": intern_dir,
                "tmux_session": tmux_session,
            },
        )
        resolver = _refresh_enterprise_resolver(
            entry,
            str(intern_dir),
            args.project,
            _metadata_resolver(str(intern_dir)),
        )
        code_repo = str(resolver.get("code_worktree_path") or resolver.get("code_repo_path") or "")
        if not code_repo:
            raise RuntimeError("metadata_resolver missing code_worktree_path/code_repo_path")
        metadata_intern_dir = os.path.dirname(str(resolver.get("status_path") or ""))
        if not metadata_intern_dir:
            raise RuntimeError("metadata_resolver missing status_path")
        script = _session_script(getattr(args, "type", "codex"))
        env = os.environ.copy()
        env.update({
            "WORK_AGENTS_ROOT": _root(),
            "INTERN_DIR": str(intern_dir),
            "INTERN_CODE_REPO_PATH": code_repo,
            "INTERN_METADATA_INTERN_DIR": metadata_intern_dir,
            "INTERN_SESSION_REGISTRY_KEY": f"{entry.get('workspace_id')}:{args.name}",
            "INTERN_WORKSPACE_ID": str(entry.get("workspace_id") or ""),
            "INTERN_TMUX_SESSION": tmux_session,
            "INTERN_TMUX_READY_CHANNEL": tmux_ready_channel(tmux_session),
            "INTERN_START_NO_ATTACH": "1" if args.no_attach else "0",
            "INTERN_SESSION_FORCE_RESTART": "1" if getattr(args, "force_restart", False) else "0",
            "CODEX_RESUME_ON_START": "1" if getattr(args, "resume_last", False) else os.environ.get("CODEX_RESUME_ON_START", "0"),
        })
        result = subprocess.run(
            ["bash", str(script), args.name, args.project],
            env=env,
            text=True,
        )
        return int(result.returncode)
    except Exception as exc:
        print(f"session start failed: {exc}", file=sys.stderr)
        return 1


def run_start(args: argparse.Namespace) -> int:
    try:
        try:
            entry = _session_entry(args.name, args.project)
        except Exception:
            entry = _bootstrap_enterprise_session_entry(
                args.name,
                args.project,
                getattr(args, "type", "codex"),
            )
        if not entry.get("workspace_id"):
            raise RuntimeError("session registry entry missing workspace_id")
        if not entry.get("intern_dir"):
            raise RuntimeError("session registry entry missing intern_dir")
        return _run_enterprise_start(args, entry)
    except Exception as exc:
        print(f"session start failed: {exc}", file=sys.stderr)
        return 1


def run_status(args: argparse.Namespace) -> int:
    try:
        tmux_session = _resolve_tmux_session_name(args.name, getattr(args, "project", "") or "")
    except Exception as exc:
        if args.json:
            print(json.dumps({
                "schema": "intern-agents.session-status.v1",
                "name": args.name,
                "project": getattr(args, "project", "") or "",
                "tmux_session": "",
                "running": False,
                "error": str(exc),
            }, indent=2))
            return 1
        print(f"{args.name}: not running ({exc})")
        return 1
    running = _tmux_running(tmux_session)
    if args.json:
        print(json.dumps({
            "schema": "intern-agents.session-status.v1",
            "name": args.name,
            "project": getattr(args, "project", "") or "",
            "tmux_session": tmux_session,
            "running": running,
        }, indent=2))
    else:
        print(f"{args.name}: {'running' if running else 'not running'} ({tmux_session})")
    return 0 if running else 1


def run_stop(args: argparse.Namespace) -> int:
    try:
        tmux_session = _resolve_tmux_session_name(args.name, getattr(args, "project", "") or "")
    except Exception as exc:
        print(f"{args.name}: not running ({exc})")
        return 0
    result = subprocess.run(["tmux", "kill-session", "-t", f"={tmux_session}"], capture_output=True, text=True)
    if result.returncode != 0 and not _tmux_running(tmux_session):
        print(f"{args.name}: not running")
        return 0
    if result.returncode != 0:
        print(result.stderr.strip() or result.stdout.strip() or "tmux kill-session failed", file=sys.stderr)
        return result.returncode
    print(f"{args.name}: stopped")
    return 0


def run_resume(args: argparse.Namespace) -> int:
    payload = _run_provider_resume(args)
    return _emit_resume_result(args, payload)


def run_capture_claude_session(args: argparse.Namespace) -> int:
    try:
        entry = _session_entry(args.name, args.project)
        runtime = {
            "intern_dir": str(entry.get("intern_dir") or ""),
            "project": str(entry.get("project") or args.project or ""),
            "workspace_id": str(entry.get("workspace_id") or ""),
        }
        if not runtime["intern_dir"] or not Path(runtime["intern_dir"]).is_dir():
            raise RuntimeError(f"enterprise runtime dir missing for {args.project}:{args.name}")
        deadline = time.time() + max(0.0, float(getattr(args, "timeout", 0.0) or 0.0))
        min_mtime = max(0.0, float(getattr(args, "since", 0.0) or 0.0))
        session_id = ""
        source = ""
        resume_mode = ""
        while True:
            session_id, source = _discover_latest_claude_session_id(runtime["intern_dir"], min_mtime=min_mtime)
            if session_id:
                resume_mode = _claude_resume_mode_for_source(source)
                _persist_claude_session_id(
                    runtime["intern_dir"],
                    session_id,
                    source=source,
                    resume_mode=resume_mode,
                    intern_name=args.name,
                    project=runtime["project"],
                    workspace_id=runtime["workspace_id"],
                )
                break
            if time.time() >= deadline:
                break
            time.sleep(1)
        payload = {
            "schema": "intern-agents.claude-session-capture.v1",
            "name": args.name,
            "project": args.project,
            "success": bool(session_id),
            "session_id": session_id,
            "source": source,
            "resume_mode": resume_mode,
            "reason": "" if session_id else "claude session id unavailable: never discovered",
        }
    except Exception as exc:
        payload = {
            "schema": "intern-agents.claude-session-capture.v1",
            "name": args.name,
            "project": args.project,
            "success": False,
            "session_id": "",
            "source": "",
            "resume_mode": "",
            "reason": str(exc),
        }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload.get("success"):
        print(f"{args.name}: captured claude session {payload.get('session_id')}")
    else:
        print(f"{args.name}: claude session capture failed: {payload.get('reason')}", file=sys.stderr)
    return 0 if payload.get("success") else 1


def run_clear_claude_session(args: argparse.Namespace) -> int:
    try:
        entry = _session_entry(args.name, args.project)
        runtime = {
            "intern_dir": str(entry.get("intern_dir") or ""),
            "project": str(entry.get("project") or args.project or ""),
            "workspace_id": str(entry.get("workspace_id") or ""),
        }
        if not runtime["intern_dir"] or not Path(runtime["intern_dir"]).is_dir():
            raise RuntimeError(f"enterprise runtime dir missing for {args.project}:{args.name}")
        removed = _clear_claude_session_id(
            runtime["intern_dir"],
            intern_name=args.name,
            project=runtime["project"],
            workspace_id=runtime["workspace_id"],
        )
        payload = {
            "schema": "intern-agents.claude-session-clear.v1",
            "name": args.name,
            "project": args.project,
            "success": True,
            "removed": removed,
            "reason": "",
        }
    except Exception as exc:
        payload = {
            "schema": "intern-agents.claude-session-clear.v1",
            "name": args.name,
            "project": args.project,
            "success": False,
            "removed": {"state": False, "registry": False},
            "reason": str(exc),
        }
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload.get("success"):
        print(f"{args.name}: cleared claude session state")
    else:
        print(f"{args.name}: claude session clear failed: {payload.get('reason')}", file=sys.stderr)
    return 0 if payload.get("success") else 1


def _run_provider_resume(args: argparse.Namespace) -> dict:
    try:
        if getattr(args, "type", "codex") == "claude":
            return _run_claude_resume(args)
        return _run_codex_resume(args)
    except Exception as exc:
        return _resume_result(args, False, str(exc))


def run_restart(args: argparse.Namespace) -> int:
    try:
        tmux_session = _resolve_tmux_session_name(args.name, args.project)
    except Exception:
        tmux_session = ""
    if tmux_session and _tmux_running(tmux_session):
        setattr(args, "tmux_session", tmux_session)
        payload = _run_provider_resume(args)
        if payload.get("success"):
            suffix = f" {payload.get('session_id')}" if payload.get("session_id") else ""
            print(f"{args.name}: restarted via resume{suffix}")
            return 0
        if getattr(args, "type", "codex") == "codex" and payload.get("reason") == "codex session id unavailable":
            setattr(args, "force_restart", True)
            setattr(args, "resume_last", False)
            code = run_start(args)
            if code == 0:
                print(f"{args.name}: restarted fresh (no codex session id)")
            else:
                print(f"{args.name}: restart failed", file=sys.stderr)
            return code
        print(f"{args.name}: restart resume failed: {payload.get('reason')}", file=sys.stderr)
        return 1

    setattr(args, "force_restart", True)
    setattr(args, "resume_last", False)
    code = run_start(args)
    if code == 0:
        print(f"{args.name}: restarted")
    else:
        print(f"{args.name}: restart failed", file=sys.stderr)
    return code


def run(args: argparse.Namespace) -> int:
    cmd = getattr(args, "session_command", None)
    if cmd == "start":
        return run_start(args)
    if cmd == "status":
        return run_status(args)
    if cmd == "restart":
        return run_restart(args)
    if cmd == "resume":
        return run_resume(args)
    if cmd == "capture-claude-session":
        return run_capture_claude_session(args)
    if cmd == "clear-claude-session":
        return run_clear_claude_session(args)
    if cmd == "stop":
        return run_stop(args)
    print("Usage: internctl session {start|resume|restart|status|stop}", file=sys.stderr)
    return 1
