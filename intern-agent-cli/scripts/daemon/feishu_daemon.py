#!/usr/bin/env python3
"""
Feishu Daemon — 全功能飞书服务端

功能：
1. HTTP API (localhost:<ephemeral>) — 供插件/hooks/CLI 调用
2. WebSocket server (localhost:<ephemeral>) — 向插件推送消息
3. 飞书 WebSocket — 接收飞书群消息
4. 群生命周期管理 — 创建/删除/同步
5. 红绿灯管理 — 更新群名 🟢/🔴
6. 消息发送/更新/回复

启动：插件 activate 时自动后台启动
停止：POST /api/shutdown 或 SIGTERM

PID file: /tmp/feishu_daemon.json (JSON: pid/instance_id/work_agents_root/http_port/ws_port/started_at/script_hash/version/bundle_dir)
"""

__version__ = "1.0.0"

import json
import os
import sys
import subprocess
import shutil
import signal
import logging
import time
import threading
import queue
import asyncio
import hashlib
import base64
import fcntl
import urllib.request
import urllib.error
import urllib.parse
import socket
import uuid
import faulthandler
import tempfile
import sqlite3
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
import re
from collections import deque

# task283: daemon-local per-chat detail_mode store; lives next to this script.
_DAEMON_DIR = os.path.dirname(os.path.abspath(__file__))
_INTERN_CLI_ROOT = os.path.abspath(os.path.join(_DAEMON_DIR, "..", ".."))
sys.path.insert(0, _DAEMON_DIR)
sys.path.insert(0, _INTERN_CLI_ROOT)
import daemon_chat_config
from scripts.common.terminal_screenshot_renderer import render_tmux_screenshot_png
from lib import team_mailbox
from lib.enterprise_state_v1 import (
    LOCAL_REGISTRY_SCHEMA,
    WORKSPACE_SCHEMA,
    daemon_workspace_cache_path,
    state_registry_path,
    validate_workspace_id,
    workspace_metadata_cache_path,
    workspace_record_path,
    workspace_state_dir,
    workspace_source_path,
)
from lib.git_ops import add_commit_push, ensure_git_identity
from lib.enterprise_paths import daemon_owner_path, daemon_policy_path, daemon_runtime_dir, daemon_user_env_path
from lib.log_paths import log_root, system_log_dir
from lib.machine_config_policy import policy_with_env_switch_state, save_env_switch_state
from lib.metadata_checkout import ensure_metadata_branch_checkout
from lib.session_launch_spec import (
    DYNAMIC_LAUNCH_ENV_KEYS,
    provider_launch_env_values,
    session_runtime_launch_env_values,
)
from lib.session_policy_env import has_session_env_policy, load_owner_config, materialize_session_env
from lib.slash_commands import NATIVE_SLASH_COMMANDS_BY_INTERN_TYPE, format_available_slash_commands
from lib.tmux_session import scoped_tmux_session_name, tmux_ready_channel
from lib.user_env import load_enterprise_user_env, parse_env_file

# ── 配置 ──────────────────────────────────

WORK_AGENTS_ROOT = os.environ.get("WORK_AGENTS_ROOT") or os.getcwd()
# HTTP_PORT/WS_PORT default to 0 → OS assigns ephemeral port at bind time.
# Actual ports are written to PID_FILE after bind, and read by hooks/CLI/extension.
HTTP_PORT = int(os.environ.get("FEISHU_HTTP_PORT", "0"))
WS_PORT = int(os.environ.get("FEISHU_WS_PORT", "0"))
PID_FILE = "/tmp/feishu_daemon.json"
PID_FILE_REFRESH_INTERVAL_SECONDS = 30
OLD_PID_FILE = os.path.join(WORK_AGENTS_ROOT, ".feishu_daemon.pid")  # legacy, unlinked on startup
LOG_DIR = str(system_log_dir(
    WORK_AGENTS_ROOT,
    "daemon",
    bundle_dir=_INTERN_CLI_ROOT,
    script_path=__file__,
    component_version=__version__,
))
LOG_FILE = os.path.join(LOG_DIR, "feishu_daemon.log")
BASE_URL = "https://open.feishu.cn/open-apis"
OWNER_JSON_PATH = os.fspath(daemon_owner_path(WORK_AGENTS_ROOT))
RELAY_WS_MAX_SIZE_BYTES = 16 * 1024 * 1024
FEISHU_BUFFER_FLUSH_POLL_SECONDS = 1
FEISHU_BUFFER_MAX_UPDATES_PER_MESSAGE = 17
FEISHU_BUFFER_MAX_POST_BODY_BYTES = 28000
FEISHU_BUFFER_SPINNER = "\n\n⏳ 处理中..."
CODEX_GOAL_SNAPSHOT_FILE = ".codex_goal_snapshot.json"
CODEX_GOAL_SNAPSHOT_INTERVAL_SECONDS = 10
CODEX_GOAL_SQLITE_BUSY_TIMEOUT_MS = 250
_FEISHU_STRUCTURAL_PREFIXES = (
    "🧑 ",
    "✅",
    "❗",
    "⛔",
    "🎯",
    "📌",
    "📊",
    "⚠️",
    "（接上条消息）",
    "(续下条...)",
)

_MACHINE_HELPER_SLUG_RE = re.compile(r"[^a-z0-9]+")
_MACHINE_HELPER_FORBIDDEN_ENDPOINT_CHARS = set(" \t\r\n;&|$`<>\\")
_MACHINE_HELPER_SECRET_HINT_RE = re.compile(
    r"(token|secret|password|passwd|api[_-]?key|access[_-]?key)",
    re.IGNORECASE,
)


def _safe_machine_helper_slug(machine_id):
    slug = _MACHINE_HELPER_SLUG_RE.sub("_", (machine_id or "").strip().lower()).strip("_")
    if not slug:
        raise ValueError("machine_id required")
    return slug


def _runtime_now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_workspace_id(value):
    raw = (value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._").lower()
    return safe or uuid.uuid4().hex[:12]


def _workspace_id_from_body(body):
    if "workspace_id" in body:
        return validate_workspace_id(str(body.get("workspace_id") or ""))
    display = (body.get("display_name") or body.get("name") or "").strip()
    if display:
        return validate_workspace_id("ws_" + _safe_workspace_id(display))
    repo_url = (body.get("repo_url") or "").rstrip("/").removesuffix(".git")
    tail = repo_url.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if repo_url else ""
    return validate_workspace_id("ws_" + _safe_workspace_id(tail or "workspace"))


class RuntimeMetrics:
    def __init__(self, component, latency_limit=256):
        self._component = component
        self._latency_limit = latency_limit
        self._started_at = _runtime_now_iso()
        self._lock = threading.RLock()
        self._interfaces = {}

    def record(self, key, elapsed_ms=0, status_code=None, error=False):
        elapsed_ms = max(0, int(elapsed_ms))
        with self._lock:
            item = self._interfaces.get(key)
            if item is None:
                item = {
                    "key": key,
                    "count": 0,
                    "error_count": 0,
                    "total_ms": 0,
                    "max_ms": 0,
                    "last_ms": 0,
                    "last_status_code": None,
                    "last_at": "",
                    "latencies": deque(maxlen=self._latency_limit),
                }
                self._interfaces[key] = item
            item["count"] += 1
            if error:
                item["error_count"] += 1
            item["total_ms"] += elapsed_ms
            item["max_ms"] = max(item["max_ms"], elapsed_ms)
            item["last_ms"] = elapsed_ms
            item["last_status_code"] = status_code
            item["last_at"] = _runtime_now_iso()
            item["latencies"].append(elapsed_ms)

    def snapshot(self):
        with self._lock:
            interfaces = []
            for item in self._interfaces.values():
                latencies = sorted(item["latencies"])
                if latencies:
                    p95_idx = min(len(latencies) - 1, int((len(latencies) * 95 + 99) / 100) - 1)
                    p95_ms = latencies[p95_idx]
                else:
                    p95_ms = 0
                count = item["count"]
                interfaces.append({
                    "key": item["key"],
                    "count": count,
                    "error_count": item["error_count"],
                    "avg_ms": round(item["total_ms"] / count, 2) if count else 0,
                    "p95_ms": p95_ms,
                    "max_ms": item["max_ms"],
                    "last_ms": item["last_ms"],
                    "last_status_code": item["last_status_code"],
                    "last_at": item["last_at"],
                })
            interfaces.sort(key=lambda row: (-row["count"], row["key"]))
            return {
                "schema": "intern-agents.runtime-metrics.v1",
                "component": self._component,
                "started_at": self._started_at,
                "updated_at": _runtime_now_iso(),
                "interfaces": interfaces,
            }


_daemon_metrics = RuntimeMetrics("daemon")


def _machine_helper_id_for_machine(machine_id):
    return f"machine_helper_{_safe_machine_helper_slug(machine_id)}"


def _machine_helper_task_id(machine_id):
    return f"task_machine_helper_{_safe_machine_helper_slug(machine_id)}"


def _machine_helper_workspace_key(machine_id):
    return f"machine_helper_{_safe_machine_helper_slug(machine_id)}"


def _machine_helper_workspace_display(machine_id):
    return f"machine-helper-{_safe_machine_helper_slug(machine_id)}"


def _machine_helper_state_root(machine_id, work_root=None):
    return os.path.join(work_root or WORK_AGENTS_ROOT, "state", "v1", _machine_helper_workspace_key(machine_id))


def _machine_helper_dir(machine_id, work_root=None):
    return os.path.join(
        _machine_helper_state_root(machine_id, work_root),
        "interns",
        _machine_helper_id_for_machine(machine_id),
    )


def _machine_helper_source_dir(machine_id, work_root=None):
    return os.path.join(_machine_helper_state_root(machine_id, work_root), "source")


def _machine_helper_metadata_root(machine_id, work_root=None):
    return os.path.join(
        _machine_helper_state_root(machine_id, work_root),
        "metadata",
        "local",
        ".intern_workspace",
    )


def _machine_helper_workspace_id(machine_id):
    return f"local-machine-helper:{_safe_machine_helper_slug(machine_id)}"


def parse_machine_helper_endpoint(endpoint):
    """Parse and validate a helper migration target endpoint.

    Accepts IPv4/DNS as ``host:port`` and IPv6 as ``[addr]:port``. Returns a
    normalized dict so migration helpers do not need to parse shell-like text.
    """
    raw = (endpoint or "").strip()
    if not raw:
        raise ValueError("endpoint required")
    if any(ch in _MACHINE_HELPER_FORBIDDEN_ENDPOINT_CHARS for ch in raw):
        raise ValueError("endpoint contains forbidden characters")
    if _MACHINE_HELPER_SECRET_HINT_RE.search(raw):
        raise ValueError("endpoint must not contain credential material")

    bracketed_ipv6 = raw.startswith("[")
    if bracketed_ipv6:
        end = raw.find("]")
        if end < 0 or len(raw) <= end + 2 or raw[end + 1] != ":":
            raise ValueError("IPv6 endpoint must be [host]:port")
        host = raw[1:end]
        port_text = raw[end + 2:]
    else:
        if raw.count(":") != 1:
            raise ValueError("endpoint must be host:port")
        host, port_text = raw.rsplit(":", 1)

    if not host:
        raise ValueError("host required")
    if not port_text.isdigit():
        raise ValueError("port must be numeric")
    port = int(port_text)
    if port < 1 or port > 65535:
        raise ValueError("port out of range")
    normalized = f"[{host}]:{port}" if bracketed_ipv6 else f"{host}:{port}"
    return {"host": host, "port": port, "endpoint": normalized}


def _write_json_file_atomic(path, data, *, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    if mode is not None:
        os.chmod(tmp_path, mode)
    os.replace(tmp_path, path)
    if mode is not None:
        os.chmod(path, mode)


def _pid_is_running(pid):
    try:
        if int(pid) <= 0:
            return False
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def _pid_looks_like_daemon(pid):
    proc_cmdline = f"/proc/{pid}/cmdline"
    if not os.path.exists(proc_cmdline):
        return True
    try:
        with open(proc_cmdline, "rb") as f:
            cmdline = f.read().replace(b"\0", b" ").decode("utf-8", errors="replace")
        return "feishu_daemon.py" in cmdline
    except OSError:
        return True


def _read_pid_file_for_repair():
    try:
        with open(PID_FILE, encoding="utf-8") as f:
            payload = json.load(f)
        if not isinstance(payload, dict):
            return None, "invalid_json_type"
        return payload, ""
    except FileNotFoundError:
        return None, "missing"
    except json.JSONDecodeError:
        return None, "invalid_json"
    except OSError as exc:
        return None, f"unreadable:{exc}"


def _write_pid_file_atomic(payload):
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    tmp_path = f"{PID_FILE}.{os.getpid()}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, PID_FILE)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def _pid_file_payload_matches_self(current, expected):
    keys = ("pid", "work_agents_root", "http_port", "ws_port", "script_hash", "bundle_dir")
    return all(current.get(key) == expected.get(key) for key in keys)


def _ensure_pid_file_points_to_self(pid_payload, reason):
    current, read_error = _read_pid_file_for_repair()
    should_write = False
    repair_reason = read_error
    if current is None:
        should_write = True
    else:
        try:
            current_pid = int(current.get("pid") or 0)
        except (TypeError, ValueError):
            current_pid = 0

        if current_pid == os.getpid():
            if _pid_file_payload_matches_self(current, pid_payload):
                return False
            should_write = True
            repair_reason = "stale_self_payload"
        elif current_pid and _pid_is_running(current_pid) and _pid_looks_like_daemon(current_pid):
            log.warning(
                f"PID file points to another live daemon pid={current_pid}; "
                f"current pid={os.getpid()} leaves it untouched"
            )
            return False
        else:
            should_write = True
            repair_reason = f"stale_pid:{current_pid or 'none'}"

    if should_write:
        _write_pid_file_atomic(pid_payload)
        if reason == "startup":
            log.info(
                f"PID file written: {PID_FILE} "
                f"(pid={pid_payload['pid']}, http={pid_payload['http_port']}, ws={pid_payload['ws_port']})"
            )
        else:
            log.warning(
                f"PID file repaired by {reason}: {PID_FILE} "
                f"(reason={repair_reason}, pid={pid_payload['pid']}, "
                f"http={pid_payload['http_port']}, ws={pid_payload['ws_port']})"
            )
    return should_write


def _pid_file_watchdog(pid_payload, stop_event):
    while not stop_event.wait(PID_FILE_REFRESH_INTERVAL_SECONDS):
        try:
            _ensure_pid_file_points_to_self(pid_payload, "watchdog")
        except Exception as exc:
            log.warning(f"PID file watchdog failed: {exc}")


def _register_machine_helper_session(helper_id, runtime, work_root=None, helper_dir="", project="",
                                     workspace_id=""):
    sessions_file = os.path.join(work_root or WORK_AGENTS_ROOT, ".intern_sessions.json")
    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    tmux_session = scoped_tmux_session_name(
        helper_id,
        project=project,
        workspace_id=workspace_id,
        intern_dir=helper_dir,
    )
    data[helper_id] = {
        "type": runtime,
        "role": "machine_helper",
        "intern_name": helper_id,
        "project": project,
        "workspace_id": workspace_id,
        "intern_dir": helper_dir,
        "tmux_session": tmux_session,
    }
    _write_json_file_atomic(sessions_file, data)


def _unregister_machine_helper_session(helper_id, work_root=None):
    sessions_file = os.path.join(work_root or WORK_AGENTS_ROOT, ".intern_sessions.json")
    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    if not isinstance(data, dict):
        return
    changed = False
    for key, value in list(data.items()):
        if key == helper_id or (isinstance(value, dict) and value.get("intern_name") == helper_id and value.get("role") == "helper"):
            data.pop(key, None)
            changed = True
    if changed:
        _write_json_file_atomic(sessions_file, data)


def _delete_machine_helper_relay_chat(helper_id, project):
    if _registry:
        _registry.unregister(helper_id)
    if not (_relay_client and _relay_client.connected and _relay_client._relay_http_base):
        return
    try:
        payload = {"intern_name": helper_id, "project": project or helper_id}
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{_relay_client._relay_http_base}/api/chat/delete",
            data=data,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as exc:
        log.warning(f"failed to delete helper relay chat for {helper_id}: {exc}")


def _create_machine_helper_relay_chat(machine_id, helper_id, runtime, operator_open_id=""):
    if not (_relay_client and _relay_client.connected and _relay_client._relay_http_base):
        return ""
    payload = {
        "machine_id": machine_id,
        "helper_id": helper_id,
        "runtime": runtime,
        "operator_open_id": operator_open_id,
    }
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{_relay_client._relay_http_base}/api/helper/chat/create",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    resp = urllib.request.urlopen(req, timeout=60)
    result = json.loads(resp.read() or b"{}")
    chat_id = result.get("chat_id") or ""
    if not chat_id:
        raise RuntimeError(f"relay /api/helper/chat/create returned no chat_id: {result}")
    return chat_id


def _metadata_root_for_workspace(workspace):
    mode = workspace.get("metadata_mode") or ""
    local_path = workspace.get("local_path") or ""
    metadata_cache = workspace.get("metadata_cache_path") or ""
    if mode == "repo_dotdir":
        return os.path.join(local_path, ".intern_workspace"), local_path
    if mode == "metadata_branch":
        return os.path.join(metadata_cache, ".intern_workspace"), metadata_cache
    if mode == "local_only":
        return os.path.join(metadata_cache, "local", ".intern_workspace"), ""
    raise ValueError(f"invalid metadata mode for workspace {workspace.get('workspace_id', '')}: {mode!r}")


def _run_machine_helper_git(repo, args, *, check=False):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=check, timeout=30)


def _ensure_machine_helper_source_repo(machine_id, work_root):
    repo = Path(_machine_helper_source_dir(machine_id, work_root))
    repo.mkdir(parents=True, exist_ok=True)
    if not (repo / ".git").exists():
        subprocess.run(["git", "init", "-b", "master"], cwd=repo, capture_output=True, text=True, check=True)
    ensure_git_identity(str(repo))
    readme = repo / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {_machine_helper_workspace_display(machine_id)}\n\n"
            "This local repository is the machine helper workspace for enterprise Intern Agents.\n",
            encoding="utf-8",
        )
    guide = repo / "MACHINE_HELPER.md"
    guide.write_text(
        f"""# Machine Helper Knowledge

Machine: `{machine_id}`
Workspace: `{_machine_helper_workspace_display(machine_id)}`

Use this local repository for machine-level diagnosis and repair. The helper is an enterprise machine helper intern:
- metadata mode is `local_only` for machine-scoped helper metadata;
- task, status, history, and knowledge files live under the local `.intern_workspace`;
- enterprise permissions, owner invite, and machine migration are handled by the relay/daemon helper surface;
- do not require any business workspace to be enabled before starting.
""",
        encoding="utf-8",
    )
    if _run_machine_helper_git(repo, ["status", "--short"]).stdout.strip():
        _run_machine_helper_git(repo, ["add", "README.md", "MACHINE_HELPER.md"], check=True)
        diff = _run_machine_helper_git(repo, ["diff", "--cached", "--quiet"])
        if diff.returncode != 0:
            _run_machine_helper_git(repo, ["commit", "-m", "Initialize machine helper workspace"], check=True)
    return str(repo)


def _write_machine_helper_state_workspace(machine_id, work_root):
    root = Path(_machine_helper_state_root(machine_id, work_root))
    repo = _ensure_machine_helper_source_repo(machine_id, work_root)
    workspace = {
        "schema": "intern-agents.workspace.v1",
        "workspace_id": _machine_helper_workspace_id(machine_id),
        "workspace_key": _machine_helper_workspace_key(machine_id),
        "display_name": _machine_helper_workspace_display(machine_id),
        "repo_url": repo,
        "local_path": repo,
        "default_branch": "master",
        "metadata": {
            "mode": "local_only",
            "repo_relative_path": ".intern_workspace",
            "branch": "intern_workspace",
            "local_path": _machine_helper_metadata_root(machine_id, work_root),
        },
    }
    _write_json_file_atomic(str(root / "workspace.json"), workspace)
    registry_path = state_registry_path(work_root)
    try:
        with open(registry_path, "r", encoding="utf-8") as f:
            registry = json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        registry = {}
    if not isinstance(registry, dict) or registry.get("schema") != LOCAL_REGISTRY_SCHEMA:
        registry = {"schema": LOCAL_REGISTRY_SCHEMA, "workspaces": {}}
    workspaces = registry.get("workspaces")
    if not isinstance(workspaces, dict):
        workspaces = {}
    registry["schema"] = LOCAL_REGISTRY_SCHEMA
    registry["workspaces"] = workspaces
    workspaces[_machine_helper_workspace_id(machine_id)] = (
        f"{_machine_helper_workspace_key(machine_id)}/workspace.json"
    )
    _write_json_file_atomic(str(registry_path), registry)
    return workspace


def _resolve_machine_helper_metadata(work_root, helper_id, task_id, machine_id="", workspace_id="", metadata_resolver=None):
    if metadata_resolver:
        resolver = dict(metadata_resolver)
        resolver.setdefault("intern_name", helper_id)
        resolver.setdefault("task_id", task_id)
        return resolver
    machine_id = machine_id or helper_id.removeprefix("machine_helper_")
    workspace = _write_machine_helper_state_workspace(machine_id, work_root)
    metadata_root = workspace["metadata"]["local_path"]
    metadata_checkout = os.path.join(_machine_helper_state_root(machine_id, work_root), "metadata", "local")
    workspace_key = workspace["workspace_key"]
    workspace_id = workspace["workspace_id"]
    tasks_dir = os.path.join(metadata_root, "tasks")
    task_dir = os.path.join(tasks_dir, task_id)
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "workspace_key": workspace_key,
        "project": workspace["display_name"],
        "projectless": True,
        "intern_name": helper_id,
        "task_id": task_id,
        "metadata_mode": "local_only",
        "metadata_branch": None,
        "repo_provider": "local",
        "runtime_provider": "local",
        "default_branch": "master",
        "code_repo_path": workspace["local_path"],
        "code_worktree_path": workspace["local_path"],
        "metadata_checkout_path": metadata_checkout,
        "metadata_root": metadata_root,
        "workspace_source_path": workspace["local_path"],
        "project_rule_path": os.path.join(metadata_root, "project_rule.txt"),
        "error_book_path": os.path.join(metadata_root, "ERROR_BOOK.md"),
        "tasks_dir": tasks_dir,
        "task_readme_path": os.path.join(task_dir, "README.md"),
        "history_log_path": os.path.join(task_dir, "history_log.md"),
        "task_knowledge_path": os.path.join(task_dir, "task_knowledge.md"),
        "status_path": os.path.join(metadata_root, "interns", helper_id, "status.md"),
        "knowledge_path": os.path.join(metadata_root, "interns", helper_id, "knowledge.md"),
    }


_MACHINE_HELPER_RUNTIMES = ("codex", "claude")


class MachineHelperRuntimeUnavailable(RuntimeError):
    def __init__(self, machine_id, helper_id, failures):
        self.machine_id = machine_id
        self.helper_id = helper_id
        self.failures = list(failures or [])
        detail = "; ".join(
            f"{item.get('runtime')}: {item.get('error') or 'unknown error'}"
            for item in self.failures
        )
        super().__init__(
            "machine_helper_runtime_unavailable"
            f" machine_id={machine_id} helper_id={helper_id}"
            f" failures=[{detail}]"
        )


def ensure_machine_helper_profile(machine_id, runtime="codex", chat_id="", work_root=None,
                                  workspace_id="", metadata_resolver=None):
    """Create a helper profile that can be launched by a normal intern script."""
    runtime = (runtime or "codex").strip().lower()
    if runtime not in _MACHINE_HELPER_RUNTIMES:
        raise ValueError(f"machine helper runtime must be one of: {', '.join(_MACHINE_HELPER_RUNTIMES)}")
    work_root = work_root or WORK_AGENTS_ROOT
    helper_id = _machine_helper_id_for_machine(machine_id)
    helper_dir = _machine_helper_dir(machine_id, work_root)
    task_id = _machine_helper_task_id(machine_id)
    resolver = _resolve_machine_helper_metadata(
        work_root, helper_id, task_id,
        machine_id=machine_id,
        workspace_id=workspace_id,
        metadata_resolver=metadata_resolver,
    )
    project_name = resolver["project"]
    os.makedirs(helper_dir, exist_ok=True)
    os.makedirs(os.path.join(helper_dir, "debug"), exist_ok=True)
    os.makedirs(os.path.join(helper_dir, "outputs"), exist_ok=True)
    os.makedirs(os.path.join(helper_dir, ".feishu_inbox"), exist_ok=True)

    state = {
        "role": "machine_helper",
        "projectless": True,
        "intern_name": helper_id,
        "project": project_name,
        "workspace_id": resolver["workspace_id"],
        "workspace_key": resolver["workspace_key"],
        "metadata_mode": "local_only",
        "code_worktree_path": resolver["code_worktree_path"],
        "intern_dir": helper_dir,
        "metadata_resolver": resolver,
        "current_task": task_id,
        "feishu": {"chat_id": chat_id},
        "helper": {
            "machine_id": machine_id,
            "helper_id": helper_id,
            "runtime": runtime,
            "chat_id": chat_id,
        },
    }
    _write_json_file_atomic(os.path.join(helper_dir, ".hook_state.json"), state)
    _write_json_file_atomic(os.path.join(helper_dir, "helper_profile.json"), {
        "machine_id": machine_id,
        "helper_id": helper_id,
        "runtime": runtime,
        "task_id": task_id,
        "chat_id": chat_id,
        "workspace_id": resolver["workspace_id"],
        "workspace_key": resolver["workspace_key"],
        "projectless": True,
        "metadata_resolver": resolver,
    })
    helper_metadata_paths = _write_machine_helper_workspace_files(resolver, helper_id, task_id, machine_id)
    _commit_machine_helper_metadata_if_needed(resolver, helper_metadata_paths)
    with open(os.path.join(helper_dir, "prompt.md"), "w", encoding="utf-8") as f:
        f.write(
            f"# Machine Helper\n\n"
            f"You are `{helper_id}`, a machine-level helper for `{machine_id}`.\n"
            "You have the same file, attachment, AskUser, hook, and checklist capabilities as a normal intern.\n"
        )
    _register_machine_helper_session(
        helper_id,
        runtime,
        work_root,
        helper_dir=helper_dir,
        project=resolver.get("project") or "",
        workspace_id=resolver.get("workspace_id") or "",
    )
    return {"helper_id": helper_id, "helper_dir": helper_dir, "task_id": task_id, "hook_state": state}


def _write_machine_helper_workspace_files(resolver, helper_id, task_id, machine_id):
    status_path = resolver["status_path"]
    knowledge_path = resolver["knowledge_path"]
    readme_path = resolver["task_readme_path"]
    history_path = resolver["history_log_path"]
    task_knowledge_path = resolver["task_knowledge_path"]
    touched = []
    for path in (status_path, knowledge_path, readme_path, history_path, task_knowledge_path,
                 resolver["project_rule_path"], resolver["error_book_path"]):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(status_path):
        with open(status_path, "w", encoding="utf-8") as f:
            f.write(
                f"# {helper_id} - 状态\n\n"
                f"<!-- METADATA:STATUS=Working,TASK={task_id},ROLE=machine_helper,TEAM_ID= -->\n\n"
                "| 字段 | 值 |\n|------|-----|\n"
                f"| Name | {helper_id} |\n"
                "| Status | Working |\n"
                "| Role | machine_helper |\n"
                "| Team | N/A |\n"
                f"| Current Task | {task_id} |\n"
                "| PR |  |\n"
            )
        touched.append(status_path)
    if not os.path.exists(knowledge_path):
        with open(knowledge_path, "w", encoding="utf-8") as f:
            f.write(f"# {helper_id} - 个人知识库\n\n<!-- METADATA:SESSION=0 -->\n\n---\n\n## 知识条目\n")
        touched.append(knowledge_path)
    if not os.path.exists(readme_path):
        with open(readme_path, "w", encoding="utf-8") as f:
            f.write(
                f"# {task_id} - Machine helper for {machine_id}\n\n"
                f"<!-- METADATA:STATUS=InProgress,ASSIGNEE={helper_id} -->\n\n"
                "## 目标\n\n协助用户排查机器问题、执行新机器迁移诊断，并保持普通 intern 的文件、附件、AskUser、hook 和 Checklist 能力。\n"
            )
        touched.append(readme_path)
    if not os.path.exists(history_path):
        with open(history_path, "w", encoding="utf-8") as f:
            f.write(
                f"# {task_id} - 历史日志\n\n<!-- METADATA:SESSION=0 -->\n\n---\n\n"
                "## Session 0 - 初始化\n\n**执行人**: machine helper\n\nhelper runtime profile created.\n\n---\n"
            )
        touched.append(history_path)
    if not os.path.exists(task_knowledge_path):
        with open(task_knowledge_path, "w", encoding="utf-8") as f:
            f.write(f"# {task_id} - 任务知识\n\n<!-- METADATA:SESSION=0 -->\n\n---\n\n## 知识条目\n")
        touched.append(task_knowledge_path)
    if not os.path.exists(resolver["project_rule_path"]):
        with open(resolver["project_rule_path"], "w", encoding="utf-8") as f:
            f.write("# Project Rule\n")
        touched.append(resolver["project_rule_path"])
    if not os.path.exists(resolver["error_book_path"]):
        with open(resolver["error_book_path"], "w", encoding="utf-8") as f:
            f.write("# ERROR_BOOK\n")
        touched.append(resolver["error_book_path"])
    return touched


def _commit_machine_helper_metadata_if_needed(resolver, paths):
    if not paths or resolver.get("metadata_mode") != "metadata_branch":
        return
    checkout = resolver.get("metadata_checkout_path") or ""
    if not checkout or not os.path.isdir(os.path.join(checkout, ".git")):
        return
    rels = []
    for path in paths:
        try:
            rel = os.path.relpath(path, checkout)
        except ValueError:
            continue
        if not rel.startswith(".."):
            rels.append(rel)
    if not rels:
        return
    add_commit_push(
        checkout,
        rels,
        f"Update machine helper metadata for {resolver.get('intern_name') or 'helper'}",
        branch=resolver.get("metadata_branch") or None,
        push=True,
    )


def _machine_helper_launcher_env(work_root, chat_id, resolver, helper_dir="", runtime="codex"):
    env = os.environ.copy()
    env["WORK_AGENTS_ROOT"] = work_root
    if helper_dir:
        env["INTERN_DIR"] = helper_dir
    env["INTERN_START_NO_ATTACH"] = "1"
    env["INTERN_START_SKIP_GROUP_CREATE"] = "1"
    env["INTERN_SESSION_FORCE_RESTART"] = "0"
    env["CODEX_RESUME_ON_START"] = "0"
    env["INTERN_METADATA_ROOT"] = resolver["metadata_root"]
    env["INTERN_METADATA_INTERN_DIR"] = os.path.dirname(resolver["status_path"])
    env["INTERN_CODE_REPO_PATH"] = resolver["code_repo_path"]
    env["INTERN_WORKSPACE_ID"] = resolver["workspace_id"]
    helper_key = resolver.get("intern_name") or resolver.get("workspace_key") or os.path.basename(helper_dir)
    if helper_key:
        env["INTERN_SESSION_REGISTRY_KEY"] = helper_key
        tmux_session = scoped_tmux_session_name(
            helper_key,
            project=resolver.get("project") or "",
            workspace_id=resolver.get("workspace_id") or "",
            intern_dir=helper_dir,
        )
        env["INTERN_TMUX_SESSION"] = tmux_session
        env["INTERN_TMUX_READY_CHANNEL"] = tmux_ready_channel(tmux_session)
        env.update(session_runtime_launch_env_values(
            work_root=work_root,
            session_name=tmux_session,
            intern_name=helper_key,
            intern_dir=helper_dir,
            project=resolver.get("project") or "",
            workspace_id=resolver.get("workspace_id") or "",
            daemon_addr_file=os.environ.get("FEISHU_DAEMON_ADDR_FILE", "/tmp/feishu_daemon.json"),
            ctl_python=sys.executable or shutil.which("python3") or "python3",
            ctl_path=os.path.join(_INTERN_CLI_ROOT, "internctl.py"),
            ready_channel=tmux_ready_channel(tmux_session),
        ))
    if chat_id:
        env["FEISHU_CHAT_ID"] = chat_id
    env.update(provider_launch_env_values(
        work_root,
        runtime,
        base_env=env,
        python_executable=sys.executable or shutil.which("python3") or "python3",
    ))
    return env


def _machine_helper_start_script(runtime):
    script = "intern_start_codex.sh" if runtime == "codex" else "intern_start.sh"
    return os.path.join(_INTERN_CLI_ROOT, "scripts", script)


def _machine_helper_runtime_failure(runtime, completed=None, error=None):
    code = getattr(completed, "returncode", None) if completed is not None else None
    output = ""
    if completed is not None:
        output = (getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "").strip()
    if error is not None:
        output = str(error).strip()
    if not output:
        output = f"runtime start failed with code {code}"
    return {"runtime": runtime, "returncode": code, "error": output}


def _machine_helper_session_runtime_and_project(helper_id):
    entry = _get_intern_session_entry(helper_id)
    runtime = entry.get("type") if isinstance(entry, dict) else ""
    if runtime not in _MACHINE_HELPER_RUNTIMES:
        runtime = "codex"
    project = entry.get("project") if isinstance(entry, dict) else ""
    return runtime, project or ""


def _machine_helper_runtime_process_status(helper_id):
    runtime, project = _machine_helper_session_runtime_and_project(helper_id)
    if runtime == "claude":
        running = _is_claude_process_running(helper_id, project=project)
    else:
        running = _is_codex_process_running(helper_id, project=project)
    return running, runtime, project


def _machine_helper_runtime_error_payload(exc):
    if not isinstance(exc, MachineHelperRuntimeUnavailable):
        return {}
    return {
        "error_code": "machine_helper_runtime_unavailable",
        "helper_id": exc.helper_id,
        "runtime_failures": [dict(item) for item in exc.failures],
    }


def start_machine_helper_runtime(machine_id, chat_id="", issue_summary="", operator_open_id="",
                                 work_root=None, launcher=None, workspace_id="", metadata_resolver=None):
    work_root = work_root or WORK_AGENTS_ROOT
    helper_id = _machine_helper_id_for_machine(machine_id)
    run = launcher or subprocess.run
    failures = []
    last_profile = None

    for runtime in _MACHINE_HELPER_RUNTIMES:
        profile = ensure_machine_helper_profile(
            machine_id, runtime=runtime, chat_id=chat_id, work_root=work_root,
            workspace_id=workspace_id, metadata_resolver=metadata_resolver)
        last_profile = profile
        helper_id = profile["helper_id"]
        resolver = profile["hook_state"]["metadata_resolver"]
        project_name = resolver["project"]
        if not chat_id:
            chat_id = _create_machine_helper_relay_chat(machine_id, helper_id, runtime, operator_open_id)
            if chat_id:
                profile = ensure_machine_helper_profile(
                    machine_id, runtime=runtime, chat_id=chat_id, work_root=work_root,
                    workspace_id=workspace_id, metadata_resolver=metadata_resolver)
                last_profile = profile
                resolver = profile["hook_state"]["metadata_resolver"]
                project_name = resolver["project"]
        if _registry and chat_id:
            _registry.register(helper_id, chat_id, project=project_name)
        try:
            completed = run(
                [_machine_helper_start_script(runtime), helper_id, project_name],
                cwd=work_root,
                env=_machine_helper_launcher_env(
                    work_root,
                    chat_id,
                    resolver,
                    helper_dir=profile["helper_dir"],
                    runtime=runtime,
                ),
                capture_output=True,
                text=True,
                timeout=120,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            failures.append(_machine_helper_runtime_failure(runtime, error=exc))
            continue
        if completed.returncode == 0:
            _send_machine_helper_context(
                helper_id, machine_id, issue_summary, operator_open_id,
                runtime=runtime, project=project_name)
            return {**profile, "status": "running", "runtime": runtime, "project": project_name}
        failures.append(_machine_helper_runtime_failure(runtime, completed=completed))

    raise MachineHelperRuntimeUnavailable(machine_id, helper_id, failures or [{
        "runtime": (last_profile or {}).get("runtime") or "unknown",
        "returncode": None,
        "error": "no helper runtime attempted",
    }])


def stop_machine_helper_runtime(machine_id, work_root=None, workspace_id=""):
    helper_id = _machine_helper_id_for_machine(machine_id)
    entry = _get_intern_session_entry(helper_id)
    project = entry.get("project") if isinstance(entry, dict) else ""
    project = project or workspace_id
    if _check_tmux_session(helper_id, project=project):
        session_name = _resolve_tmux_session_name(helper_id, project=project)
        subprocess.run(["tmux", "kill-session", "-t", f"={session_name}"], check=True, capture_output=True)
    _unregister_machine_helper_session(helper_id, work_root=work_root)
    _delete_machine_helper_relay_chat(helper_id, project or "")
    return {"helper_id": helper_id, "status": "stopped"}


def machine_helper_runtime_status(machine_id):
    helper_id = _machine_helper_id_for_machine(machine_id)
    running, runtime, project = _machine_helper_runtime_process_status(helper_id)
    return {"helper_id": helper_id, "status": "running" if running else "stopped", "runtime": runtime, "project": project}


def _ensure_machine_helper_runtime_running(machine_id, chat_id="", issue_summary="", operator_open_id="",
                                           work_root=None, workspace_id="", metadata_resolver=None):
    helper_id = _machine_helper_id_for_machine(machine_id)
    running, runtime, project = _machine_helper_runtime_process_status(helper_id)
    if running:
        ensure_machine_helper_profile(
            machine_id,
            runtime=runtime,
            chat_id=chat_id,
            work_root=work_root,
            workspace_id=workspace_id,
            metadata_resolver=metadata_resolver,
        )
        return {"helper_id": helper_id, "status": "running", "runtime": runtime, "project": project}
    return start_machine_helper_runtime(
        machine_id,
        chat_id=chat_id,
        issue_summary=issue_summary,
        operator_open_id=operator_open_id,
        work_root=work_root,
        workspace_id=workspace_id,
        metadata_resolver=metadata_resolver,
    )


def _send_machine_helper_prompt(helper_id, text, runtime="codex", delivery_id="", project=""):
    runtime = (runtime or "codex").strip().lower()
    if runtime == "claude":
        return _send_to_claude_tmux(helper_id, text, delivery_id=delivery_id, project=project)
    return _send_to_codex_tmux(helper_id, text, delivery_id=delivery_id, require_ack=False, project=project)


def _send_machine_helper_context(helper_id, machine_id, issue_summary, operator_open_id="", runtime="codex", project=""):
    text = (
        f"你是机器 `{machine_id}` 的 machine helper。\n"
        f"触发用户 open_id: `{operator_open_id or 'unknown'}`。\n"
        f"当前诉求：{issue_summary or '请先询问用户需要排查的问题。'}\n"
        "请先复述机器、用户诉求和你将执行的排查/迁移步骤；需要用户提供凭据或确认时必须使用 AskUser/request_user_input。"
    )
    return _send_machine_helper_prompt(
        helper_id, text, runtime=runtime,
        delivery_id=f"helper-context-{uuid.uuid4().hex}", project=project)


def build_machine_migration_prompt(endpoint, source_machine_id="", operator_open_id=""):
    parsed = parse_machine_helper_endpoint(endpoint)
    return (
        f"请协助迁移到新机器 `{parsed['endpoint']}`。\n"
        f"源机器：`{source_machine_id or 'unknown'}`；触发用户 open_id：`{operator_open_id or 'unknown'}`。\n"
        "先检查目标机器连通性、安装方式、daemon 接入、workspace enable、intern 启动和回归验证步骤。"
        "不要自动复制 token、ssh key、cookie 或其他敏感凭据；需要用户动作时使用 AskUser/request_user_input 明确说明风险和操作。"
    )


def _machine_config_cli_env(provider=""):
    env = os.environ.copy()
    env["WORK_AGENTS_ROOT"] = WORK_AGENTS_ROOT
    load_enterprise_user_env(WORK_AGENTS_ROOT, env=env)
    if provider:
        runtime_env = daemon_runtime_dir(WORK_AGENTS_ROOT) / f"{provider}.env"
        if runtime_env.is_file():
            env.update(parse_env_file(runtime_env))
    return env


def _run_machine_config_cli(args, provider="", timeout=120):
    cmd = [sys.executable, os.path.join(_INTERN_CLI_ROOT, "internctl.py"), *args, "--json"]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_machine_config_cli_env(provider),
        timeout=timeout,
    )
    try:
        report = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError:
        report = {
            "ok": False,
            "error": "invalid internctl json output",
            "stdout": (completed.stdout or "")[-500:],
            "stderr": (completed.stderr or "")[-500:],
        }
    if completed.returncode != 0:
        report.setdefault("ok", False)
        report.setdefault("error", (completed.stderr or completed.stdout or "internctl config failed").strip())
    return report


def _run_client_upgrade_cli(check_only=False, timeout=360):
    args = ["upgrade"]
    if check_only:
        args.append("--check-only")
    return _run_machine_config_cli(args, timeout=timeout)


def _session_is_working_for_restart(entry):
    name = str(entry.get("intern_name") or entry.get("name") or "")
    project = str(entry.get("project") or entry.get("workspace_id") or "")
    if not name or not project:
        return False
    try:
        if not _is_intern_online(name, project=project):
            return False
        return _is_turn_active(name, {_online_key(name, project), name}, project=project)
    except Exception:
        return False


_PENDING_RESTART_SCHEMA = "intern-agents.pending-restarts.v1"
_PENDING_RESTART_LOCK = threading.Lock()
_PENDING_RESTART_PROCESS_LOCK = threading.Lock()
_PENDING_RESTART_PROCESSING = False


def _pending_restart_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _pending_restart_path():
    return os.fspath(daemon_runtime_dir(WORK_AGENTS_ROOT) / "pending_restarts.json")


def _empty_pending_restart_state():
    return {"schema": _PENDING_RESTART_SCHEMA, "items": []}


def _load_pending_restart_state_locked():
    path = _pending_restart_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return _empty_pending_restart_state()
    if not isinstance(state, dict) or not isinstance(state.get("items"), list):
        raise ValueError(f"invalid pending restart state: {path}")
    state.setdefault("schema", _PENDING_RESTART_SCHEMA)
    return state


def _write_pending_restart_state_locked(state):
    state = dict(state)
    state["schema"] = _PENDING_RESTART_SCHEMA
    state["updated_at"] = _pending_restart_now()
    _write_json_file_atomic(_pending_restart_path(), state)


def _pending_restart_id(provider, project, name, operation_id):
    return f"{provider}:{project}:{name}:{operation_id}"


def _enqueue_pending_restart(session, operation_id, card_message_id="", machine_id="", reason="working"):
    now = _pending_restart_now()
    queue_id = _pending_restart_id(
        session.get("provider") or "",
        session.get("project") or "",
        session.get("name") or "",
        operation_id,
    )
    with _PENDING_RESTART_LOCK:
        state = _load_pending_restart_state_locked()
        items = state.get("items") or []
        existing = next((item for item in items if item.get("id") == queue_id), None)
        if existing:
            existing.update({
                "status": "pending_working",
                "reason": reason,
                "card_message_id": card_message_id or existing.get("card_message_id", ""),
                "machine_id": machine_id or existing.get("machine_id", ""),
                "updated_at": now,
            })
            item = dict(existing)
        else:
            item = {
                "id": queue_id,
                "status": "pending_working",
                "provider": session.get("provider") or "",
                "name": session.get("name") or "",
                "project": session.get("project") or "",
                "operation_id": operation_id,
                "card_message_id": card_message_id,
                "machine_id": machine_id,
                "reason": reason,
                "attempts": 0,
                "created_at": now,
                "updated_at": now,
            }
            items.append(item)
            state["items"] = items
        _write_pending_restart_state_locked(state)
        return dict(item)


def _patch_pending_restart_item(queue_id, patch):
    with _PENDING_RESTART_LOCK:
        state = _load_pending_restart_state_locked()
        for item in state.get("items") or []:
            if item.get("id") != queue_id:
                continue
            item.update(dict(patch))
            item["updated_at"] = _pending_restart_now()
            _write_pending_restart_state_locked(state)
            return dict(item)
    return {}


def _pending_restart_items_for_operation(operation_id):
    with _PENDING_RESTART_LOCK:
        state = _load_pending_restart_state_locked()
        return [
            dict(item) for item in state.get("items") or []
            if item.get("operation_id") == operation_id
        ]


def _update_pending_restart_operation_card(item):
    operation_id = item.get("operation_id") or ""
    card_message_id = item.get("card_message_id") or ""
    if not operation_id or not card_message_id:
        return
    items = _pending_restart_items_for_operation(operation_id)
    pending = [entry.get("name", "") for entry in items if entry.get("status") == "pending_working"]
    restarted = [entry.get("name", "") for entry in items if entry.get("status") == "restarted"]
    failed = [entry.get("name", "") for entry in items if entry.get("status") == "failed"]
    if pending:
        title = "机器配置等待重启"
        template = "orange"
        state = "pending_working"
    elif failed:
        title = "机器配置待重启失败"
        template = "red"
        state = "failed"
    else:
        title = "机器配置待重启已完成"
        template = "green"
        state = "completed"
    lines = [
        f"operation_id: `{operation_id}`",
        f"machine_id: `{item.get('machine_id') or ''}`",
        f"状态：`{state}`",
        f"restarted: `{', '.join(restarted) if restarted else '-'}`",
        f"pending_working: `{', '.join(pending) if pending else '-'}`",
        f"failed: `{', '.join(failed) if failed else '-'}`",
    ]
    _update_machine_config_card(card_message_id, title, template, lines)


def _run_provider_session_restart(provider, name, project):
    if not project:
        return {"name": name, "ok": False, "error": "missing project/workspace_id"}
    cmd = [
        sys.executable,
        os.path.join(_INTERN_CLI_ROOT, "internctl.py"),
        "session",
        "restart",
        name,
        "--project",
        project,
        "--type",
        provider,
        "--no-attach",
    ]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env=_machine_config_cli_env(provider),
        timeout=240,
    )
    return {
        "name": name,
        "project": project,
        "ok": completed.returncode == 0,
        "stdout": (completed.stdout or "")[-500:],
        "stderr": (completed.stderr or "")[-500:],
    }


def _emit_restart_progress(event, provider, operation_id="", name="", project="", status="", done=0, total=0, **extra):
    server = globals().get("_ws_server")
    if not server:
        return
    payload = {
        "type": "intern_restart_progress",
        "event": event,
        "provider": provider,
        "operation_id": operation_id,
        "intern_name": name,
        "project": project,
        "status": status,
        "done": done,
        "total": total,
    }
    for key, value in extra.items():
        if value is not None:
            payload[key] = value
    try:
        server.push(payload)
    except Exception as exc:
        log.debug(f"[RESTART_PROGRESS] websocket push failed: {exc}")


def _pending_restart_session_entry(item):
    name = item.get("name") or ""
    project = item.get("project") or ""
    provider = item.get("provider") or ""
    if not name or not project or not provider:
        return {}
    entry = _get_intern_session_entry(name, project=project)
    if not isinstance(entry, dict) or not entry or entry.get("_unusable_enterprise"):
        return {}
    if entry.get("type") and entry.get("type") != provider:
        return {}
    return entry


def _process_pending_restarts_once():
    try:
        with _PENDING_RESTART_LOCK:
            state = _load_pending_restart_state_locked()
            pending_items = [
                dict(item) for item in state.get("items") or []
                if item.get("status") == "pending_working"
            ]
    except FileNotFoundError:
        return {"processed": 0, "restarted": 0, "failed": 0}

    processed = 0
    restarted = 0
    failed = 0
    for item in pending_items:
        queue_id = item.get("id") or ""
        if not queue_id:
            continue
        entry = _pending_restart_session_entry(item)
        if not entry:
            updated = _patch_pending_restart_item(queue_id, {
                "status": "failed",
                "error": "session entry unavailable",
                "completed_at": _pending_restart_now(),
            })
            _update_pending_restart_operation_card(updated or item)
            processed += 1
            failed += 1
            continue
        if _session_is_working_for_restart(entry):
            continue
        attempts = int(item.get("attempts") or 0) + 1
        provider = item.get("provider") or ""
        name = item.get("name") or ""
        project = item.get("project") or ""
        operation_id = item.get("operation_id") or queue_id
        _emit_restart_progress(
            "started",
            provider,
            operation_id=operation_id,
            status="started",
            total=1,
            source="pending_restart",
        )
        _emit_restart_progress(
            "session",
            provider,
            operation_id=operation_id,
            name=name,
            project=project,
            status="restarting",
            total=1,
            source="pending_restart",
        )
        result = _run_provider_session_restart(
            provider,
            name,
            project,
        )
        patch = {
            "attempts": attempts,
            "last_result": result,
            "completed_at": _pending_restart_now(),
        }
        if result.get("ok"):
            patch["status"] = "restarted"
            restarted += 1
        else:
            patch["status"] = "failed"
            patch["error"] = result.get("stderr") or result.get("error") or "restart failed"
            failed += 1
        updated = _patch_pending_restart_item(queue_id, patch)
        _update_pending_restart_operation_card(updated or item)
        processed += 1
        _emit_restart_progress(
            "session",
            provider,
            operation_id=operation_id,
            name=name,
            project=project,
            status=patch["status"],
            done=1,
            total=1,
            source="pending_restart",
            error=patch.get("error"),
        )
        _emit_restart_progress(
            "finished",
            provider,
            operation_id=operation_id,
            status="completed" if result.get("ok") else "failed",
            done=1,
            total=1,
            source="pending_restart",
            restarted=1 if result.get("ok") else 0,
            pending=0,
            failed=0 if result.get("ok") else 1,
        )
    return {"processed": processed, "restarted": restarted, "failed": failed}


def _start_pending_restart_processor():
    global _PENDING_RESTART_PROCESSING
    with _PENDING_RESTART_PROCESS_LOCK:
        if _PENDING_RESTART_PROCESSING:
            return
        _PENDING_RESTART_PROCESSING = True

    def _worker():
        global _PENDING_RESTART_PROCESSING
        try:
            result = _process_pending_restarts_once()
            if result.get("processed"):
                log.info(f"[MACHINE_CONFIG] pending restart processor: {result}")
        except Exception as exc:
            log.warning(f"[MACHINE_CONFIG] pending restart processor failed: {exc}", exc_info=True)
        finally:
            with _PENDING_RESTART_PROCESS_LOCK:
                _PENDING_RESTART_PROCESSING = False

    threading.Thread(target=_worker, daemon=True, name="pending_restart_processor").start()


def _iter_local_provider_sessions_for_restart(provider):
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, dict):
        return []
    sessions = []
    seen = set()
    for key, entry in data.items():
        if not isinstance(entry, dict) or entry.get("type") != provider:
            continue
        if entry.get("external_managed"):
            # Externally-managed panes (e.g. Copilot-CLI bridge) must never be
            # respawned by provider policy sync; the bridge owns their lifecycle.
            continue
        name = str(entry.get("intern_name") or str(key).split(":", 1)[-1])
        if not name:
            continue
        project = str(entry.get("project") or entry.get("workspace_id") or "")
        dedupe_key = (project, name)
        if dedupe_key in seen or not _check_tmux_session(name, project=project):
            continue
        seen.add(dedupe_key)
        sessions.append({
            "name": name,
            "project": project,
            "provider": provider,
            "workspace_id": str(entry.get("workspace_id") or ""),
            "intern_dir": str(entry.get("intern_dir") or ""),
            "tmux_session": str(entry.get("tmux_session") or ""),
            "role": str(entry.get("role") or ""),
            "working": _session_is_working_for_restart(entry),
        })
    return sessions


def _restart_provider_sessions_for_config(provider, operation_id="", card_message_id="", machine_id="", session_filter=None):
    results = []
    sessions = []
    for session in _iter_local_provider_sessions_for_restart(provider):
        if session_filter is not None:
            try:
                if not session_filter(session):
                    continue
            except Exception as exc:
                log.warning(
                    "[POLICY] runtime drift filter failed for %s/%s:%s; restarting conservatively: %s",
                    provider,
                    session.get("project") or "",
                    session.get("name") or "",
                    exc,
                    exc_info=True,
                )
        sessions.append(session)
    total = len(sessions)
    done = 0
    pending_count = 0
    restarted_count = 0
    failed_count = 0
    if total:
        _emit_restart_progress(
            "started",
            provider,
            operation_id=operation_id,
            status="started",
            done=0,
            total=total,
        )
    for session in sessions:
        name = session["name"]
        project = session["project"]
        if not project:
            done += 1
            failed_count += 1
            result = {"name": name, "ok": False, "error": "missing project/workspace_id"}
            results.append(result)
            _emit_restart_progress(
                "session",
                provider,
                operation_id=operation_id,
                name=name,
                project=project,
                status="failed",
                done=done,
                total=total,
                error=result["error"],
            )
            continue
        if session.get("working"):
            pending = _enqueue_pending_restart(
                session,
                operation_id=operation_id or uuid.uuid4().hex,
                card_message_id=card_message_id,
                machine_id=machine_id,
            )
            done += 1
            pending_count += 1
            result = {
                "name": name,
                "project": project,
                "ok": None,
                "status": "pending_working",
                "queue_id": pending.get("id"),
            }
            results.append(result)
            _emit_restart_progress(
                "session",
                provider,
                operation_id=operation_id,
                name=name,
                project=project,
                status="pending_working",
                done=done,
                total=total,
                queue_id=pending.get("id"),
            )
            continue
        _emit_restart_progress(
            "session",
            provider,
            operation_id=operation_id,
            name=name,
            project=project,
            status="restarting",
            done=done,
            total=total,
        )
        result = _run_provider_session_restart(provider, name, project)
        done += 1
        if result.get("ok"):
            restarted_count += 1
            status = "restarted"
        else:
            failed_count += 1
            status = "failed"
        results.append(result)
        _emit_restart_progress(
            "session",
            provider,
            operation_id=operation_id,
            name=name,
            project=project,
            status=status,
            done=done,
            total=total,
            error=result.get("error") or result.get("stderr"),
        )
    if total:
        final_status = "failed" if failed_count and not (restarted_count or pending_count) else "completed"
        _emit_restart_progress(
            "finished",
            provider,
            operation_id=operation_id,
            status=final_status,
            done=done,
            total=total,
            restarted=restarted_count,
            pending=pending_count,
            failed=failed_count,
        )
    return results


def _local_machine_id_for_restart():
    if _relay_client and getattr(_relay_client, "machine_id", ""):
        return _relay_client.machine_id
    if not os.path.exists(OWNER_JSON_PATH):
        return ""
    try:
        return str(load_relay_config().get("machine_id") or "")
    except (Exception, SystemExit):
        return ""


def _summarize_restart_result(result):
    summary = {
        "name": result.get("name") or "",
        "project": result.get("project") or "",
        "ok": result.get("ok"),
    }
    for key in ("status", "queue_id", "error"):
        if result.get(key):
            summary[key] = result.get(key)
    if result.get("ok") is False and "error" not in summary:
        detail = result.get("stderr") or result.get("stdout")
        if detail:
            summary["error"] = str(detail)[-200:]
    return summary


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
PROVIDER_EXTRA_PREFIXES = {
    "codex": ("INTERN_CODEX_", "LB_", "CODEX_POLICY_", "CODEX_LB_", "OPENAI_"),
    "claude": ("INTERN_CLAUDE_", "CLAUDE_POLICY_"),
}
PROVIDER_ALLOWED_EXTRA_ENV = {
    "codex": {"INTERN_REAL_CODEX", "INTERN_CODEX_AUTH_MODE", "INTERN_CODEX_OPENAI_API_KEY_POLICY_MANAGED"},
    "claude": {"INTERN_REAL_CLAUDE", "INTERN_CLAUDE_DISABLE_EXPERIMENTAL_BETAS"},
}


def _tmux_session_env(session_name):
    if not session_name:
        return {}
    completed = subprocess.run(
        ["tmux", "show-environment", "-t", f"={session_name}"],
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return {}
    env = {}
    for line in (completed.stdout or "").splitlines():
        if not line or line.startswith("-") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            env[key] = value
    return env


def _list_child_processes(parent_pid):
    """Return [(pid, args), ...] for direct children of parent_pid.

    Uses a full `ps` table filtered by PPID so it works on both Linux and
    macOS/BSD (BSD `ps` has no `--ppid` option, which previously made all
    provider-liveness checks fail on macOS and interns show as offline).
    """
    parent_pid = str(parent_pid).strip()
    if not parent_pid.isdigit():
        return []
    table = subprocess.run(
        ["ps", "-Ao", "pid=,ppid=,args="],
        capture_output=True,
        text=True,
    )
    if table.returncode != 0:
        return []
    children = []
    for line in (table.stdout or "").splitlines():
        parts = line.split(None, 2)
        if len(parts) < 2:
            continue
        pid, ppid = parts[0], parts[1]
        args = parts[2] if len(parts) > 2 else ""
        if pid.isdigit() and ppid == parent_pid:
            children.append((pid, args))
    return children


def _provider_child_pid_from_tmux(session_name, provider):
    if not session_name:
        return ""
    panes = subprocess.run(
        ["tmux", "list-panes", "-s", "-t", f"={session_name}", "-F", "#{pane_pid}"],
        capture_output=True,
        text=True,
    )
    if panes.returncode != 0:
        return ""
    needle = "codex" if provider == "codex" else "claude"
    for pane_pid in (panes.stdout or "").splitlines():
        pane_pid = pane_pid.strip()
        if not pane_pid.isdigit():
            continue
        for pid, args in _list_child_processes(pane_pid):
            if needle in args.lower():
                return pid
    return ""


def _process_env(pid):
    if not pid or not str(pid).isdigit():
        return {}
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except Exception:
        return {}
    env = {}
    for item in raw.split(b"\0"):
        if not item or b"=" not in item:
            continue
        key_raw, value_raw = item.split(b"=", 1)
        key = key_raw.decode("utf-8", errors="ignore")
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        env[key] = value_raw.decode("utf-8", errors="surrogateescape")
    return env


def _provider_runtime_env(provider):
    runtime_env = daemon_runtime_dir(WORK_AGENTS_ROOT) / f"{provider}.env"
    return parse_env_file(runtime_env) if runtime_env.is_file() else {}


def _expected_provider_launch_env(provider, session, provider_report):
    expected = provider_launch_env_values(
        WORK_AGENTS_ROOT,
        provider,
        base_env=os.environ.copy(),
        python_executable=sys.executable or shutil.which("python3") or "python3",
    )
    expected.pop(PROVIDER_HASH_ENV.get(provider, ""), None)
    session_name = str(session.get("tmux_session") or "") or _resolve_tmux_session_name(
        session.get("name") or "",
        project=session.get("project") or "",
    )
    intern_dir = str(session.get("intern_dir") or "")
    project = str(session.get("project") or "")
    workspace_id = str(session.get("workspace_id") or "")
    expected.update(session_runtime_launch_env_values(
        work_root=WORK_AGENTS_ROOT,
        session_name=session_name,
        intern_name=str(session.get("name") or ""),
        intern_dir=intern_dir,
        project=project,
        workspace_id=workspace_id,
        daemon_addr_file=os.environ.get("FEISHU_DAEMON_ADDR_FILE", "/tmp/feishu_daemon.json"),
        ctl_python=sys.executable or shutil.which("python3") or "python3",
        ctl_path=os.path.join(_INTERN_CLI_ROOT, "internctl.py"),
        ready_channel=tmux_ready_channel(session_name) if session_name else "",
    ))
    return {key: str(value) for key, value in expected.items() if key and value is not None}


def _provider_report_contract_keys(provider, provider_report, expected):
    keys = set(expected)
    for key in provider_report.get("managed_env_keys") or []:
        if isinstance(key, str) and key and key not in DYNAMIC_LAUNCH_ENV_KEYS:
            keys.add(key)
    for key in provider_report.get("unset_env_keys") or []:
        if isinstance(key, str) and key and key not in DYNAMIC_LAUNCH_ENV_KEYS:
            keys.add(key)
    for key in provider_report.get("env_keys") or []:
        if isinstance(key, str) and key and key not in DYNAMIC_LAUNCH_ENV_KEYS:
            keys.add(key)
    has_policy_args = bool(provider_report.get("args") or expected.get(PROVIDER_POLICY_ARGS_ENV.get(provider, "")))
    for key in (PROVIDER_POLICY_ARGS_ENV.get(provider), PROVIDER_DEFAULT_ARGS_ENV.get(provider)):
        if key and (has_policy_args or key in expected):
            keys.add(key)
    return keys


def _provider_extra_key_is_stale(provider, key, expected_keys):
    if key == PROVIDER_HASH_ENV.get(provider):
        return False
    if key in expected_keys or key in PROVIDER_ALLOWED_EXTRA_ENV.get(provider, set()):
        return False
    if key == PROVIDER_DEFAULT_ARGS_ENV.get(provider):
        return False
    return any(key.startswith(prefix) for prefix in PROVIDER_EXTRA_PREFIXES.get(provider, ()))


def _env_matches_launch_spec(provider, observed, expected, contract_keys):
    for key in contract_keys:
        expected_value = expected.get(key, "")
        actual_value = observed.get(key, "")
        if actual_value != expected_value:
            return False, f"{key}:expected={bool(expected_value)} actual={bool(actual_value)}"
    for key in observed:
        if _provider_extra_key_is_stale(provider, key, contract_keys):
            return False, f"{key}:extra"
    return True, ""


def _provider_session_policy_mismatch(provider, provider_report, session):
    expected = _expected_provider_launch_env(provider, session, provider_report)
    contract_keys = _provider_report_contract_keys(provider, provider_report, expected)
    session_name = str(session.get("tmux_session") or "") or _resolve_tmux_session_name(
        session.get("name") or "",
        project=session.get("project") or "",
    )
    tmux_env = _tmux_session_env(session_name)
    tmux_ok, tmux_reason = _env_matches_launch_spec(provider, tmux_env, expected, contract_keys)
    if not tmux_ok:
        log.info(
            "[POLICY] %s session %s/%s stale tmux env: %s",
            provider,
            session.get("project") or "",
            session.get("name") or "",
            tmux_reason,
        )
        return True
    child_pid = _provider_child_pid_from_tmux(session_name, provider)
    process_env = _process_env(child_pid)
    if process_env:
        process_ok, process_reason = _env_matches_launch_spec(provider, process_env, expected, contract_keys)
        if not process_ok:
            log.info(
                "[POLICY] %s session %s/%s stale process env: %s",
                provider,
                session.get("project") or "",
                session.get("name") or "",
                process_reason,
            )
            return True
    return False


def _provider_report_has_launch_contract(provider_report):
    if not isinstance(provider_report, dict):
        return False
    return bool(
        provider_report.get("needs_restart")
        or provider_report.get("managed_env_keys")
        or provider_report.get("unset_env_keys")
        or provider_report.get("env_keys")
        or provider_report.get("args")
    )


def _restart_providers_for_policy_env(session_env_report):
    if not isinstance(session_env_report, dict):
        return {}
    providers = session_env_report.get("providers")
    if not isinstance(providers, dict):
        return {}
    machine_id = _local_machine_id_for_restart()
    restarted = {}
    for provider in ("claude", "codex"):
        provider_report = providers.get(provider)
        if not _provider_report_has_launch_contract(provider_report):
            continue
        digest = str(provider_report.get("hash") or "unknown")
        operation_id = f"policy_env:{provider}:{digest}"
        results = _restart_provider_sessions_for_config(
            provider,
            operation_id=operation_id,
            machine_id=machine_id,
            session_filter=lambda session, provider=provider, provider_report=provider_report: _provider_session_policy_mismatch(provider, provider_report, session),
        )
        if results:
            restarted[provider] = [_summarize_restart_result(item) for item in results]
    return restarted


def _relay_ready_for_session_restarts():
    client = globals().get("_relay_client")
    return bool(client and getattr(client, "connected", False))


def _machine_config_card(title, template, lines):
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines) or "-"}},
        ],
    }


def _update_machine_config_card(message_id, title, template, lines):
    if not message_id or not _api:
        return
    err = _api.update_interactive_card(message_id, _machine_config_card(title, template, lines))
    if err:
        log.warning(f"[MACHINE_CONFIG] update card failed message_id={message_id}: {err}")


def _flatten_session_env_restarts(applied):
    restarts = applied.get("session_env_restarts") if isinstance(applied, dict) else {}
    if not isinstance(restarts, dict):
        return []
    flattened = []
    for provider, items in restarts.items():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict):
                merged = dict(item)
                merged.setdefault("provider", provider)
                flattened.append(merged)
    return flattened


def apply_daemon_policy_sync(msg):
    machine_id = msg.get("machine_id") or ""
    local_machine_id = _relay_client.machine_id if _relay_client else ""
    if local_machine_id and machine_id and machine_id != local_machine_id:
        raise ValueError("machine_id must match this daemon")
    operation_id = msg.get("operation_id") or uuid.uuid4().hex
    card_message_id = msg.get("card_message_id") or ""
    enabled_groups = msg.get("enabled_groups") if isinstance(msg.get("enabled_groups"), list) else []
    changed_groups = msg.get("changed_groups") if isinstance(msg.get("changed_groups"), list) else []

    lines = [
        f"operation_id: `{operation_id}`",
        f"machine_id: `{machine_id or local_machine_id}`",
        "状态：`planning`",
    ]
    _update_machine_config_card(card_message_id, "机器配置处理中", "blue", lines)

    config_reports = {}
    relay_cfg = load_relay_config()
    applied_policy = sync_daemon_policy_from_relay(relay_cfg, {
        "enabled_groups": enabled_groups,
        "group_values": msg.get("group_values") if isinstance(msg.get("group_values"), dict) else {},
    })
    config_reports["policy_apply"] = applied_policy
    restarted_sessions = _flatten_session_env_restarts(applied_policy)

    failed_restarts = [item for item in restarted_sessions if item.get("ok") is False]
    pending_restarts = [item for item in restarted_sessions if item.get("status") == "pending_working"]
    status = "completed"
    template = "green"
    if any(("ok" in report and not report.get("ok")) for report in config_reports.values() if isinstance(report, dict)):
        status = "failed"
        template = "red"
    elif failed_restarts:
        status = "completed_restart_failed"
        template = "orange"
    elif pending_restarts:
        status = "completed_with_pending_restart"
        template = "orange"

    result_lines = [
        f"operation_id: `{operation_id}`",
        f"machine_id: `{machine_id or local_machine_id}`",
        f"状态：`{status}`",
    ]
    result_lines.append(f"enabled_groups: `{', '.join(str(item) for item in enabled_groups) if enabled_groups else '-'}`")
    result_lines.append(f"changed_groups: `{', '.join(str(item) for item in changed_groups) if changed_groups else '-'}`")
    if restarted_sessions:
        restarted = [item.get("name", "") for item in restarted_sessions if item.get("ok") is True]
        pending = [item.get("name", "") for item in pending_restarts]
        failed = [item.get("name", "") for item in failed_restarts]
        result_lines.append(f"restarted: `{', '.join(restarted) if restarted else '-'}`")
        result_lines.append(f"pending_working: `{', '.join(pending) if pending else '-'}`")
        result_lines.append(f"failed: `{', '.join(failed) if failed else '-'}`")
    _update_machine_config_card(card_message_id, "机器配置已处理", template, result_lines)
    return {
        "schema": "intern-agents.machine-config-apply-result.v1",
        "operation_id": operation_id,
        "machine_id": machine_id or local_machine_id,
        "status": status,
        "changed_groups": changed_groups,
        "config_reports": config_reports,
        "restarted_sessions": restarted_sessions,
    }


def handle_machine_helper_action(msg):
    action = msg.get("helper_action") or ""
    machine_id = msg.get("machine_id") or ""
    request_id = msg.get("request_id") or ""
    chat_id = msg.get("chat_id") or ""
    if not action or not machine_id:
        raise ValueError("helper_action and machine_id required")
    workspace_id = msg.get("workspace_id") or ""
    metadata_resolver = msg.get("metadata_resolver") if isinstance(msg.get("metadata_resolver"), dict) else None
    if action == "start":
        result = start_machine_helper_runtime(
            machine_id,
            chat_id=chat_id,
            issue_summary=msg.get("issue_summary") or "",
            operator_open_id=msg.get("operator_open_id") or "",
            workspace_id=workspace_id,
            metadata_resolver=metadata_resolver,
        )
    elif action == "stop":
        result = stop_machine_helper_runtime(machine_id, workspace_id=workspace_id)
    elif action == "status":
        result = machine_helper_runtime_status(machine_id)
    elif action == "upgrade_check":
        report = _run_client_upgrade_cli(check_only=True, timeout=120)
        if not report.get("ok"):
            raise RuntimeError(report.get("message") or report.get("error") or "upgrade check failed")
        result = {
            "helper_id": msg.get("helper_id") or _machine_helper_id_for_machine(machine_id),
            "status": report.get("action") or ("failed" if not report.get("ok") else "checked"),
            "upgrade": report,
            "current_version": report.get("current_version") or "",
            "latest_version": report.get("latest_version") or "",
            "update_available": bool(report.get("update_available")),
        }
    elif action == "upgrade_client":
        report = _run_client_upgrade_cli(check_only=False, timeout=420)
        if not report.get("ok"):
            raise RuntimeError(report.get("message") or report.get("error") or "upgrade failed")
        result = {
            "helper_id": msg.get("helper_id") or _machine_helper_id_for_machine(machine_id),
            "status": report.get("action") or ("failed" if not report.get("ok") else "upgraded"),
            "upgrade": report,
            "current_version": report.get("current_version") or "",
            "latest_version": report.get("latest_version") or "",
            "update_available": bool(report.get("update_available")),
        }
    elif action == "invite_owner":
        runtime = _ensure_machine_helper_runtime_running(
            machine_id,
            chat_id=chat_id,
            issue_summary=msg.get("issue_summary") or "app owner 已被邀请进群，请向 owner 说明当前问题上下文、已做排查和需要确认的事项。",
            operator_open_id=msg.get("operator_open_id") or "",
            workspace_id=workspace_id,
            metadata_resolver=metadata_resolver,
        )
        helper_id = runtime.get("helper_id") or msg.get("helper_id") or _machine_helper_id_for_machine(machine_id)
        ok, err = _send_machine_helper_context(
            helper_id,
            machine_id,
            msg.get("issue_summary") or "app owner 已被邀请进群，请向 owner 说明当前问题上下文、已做排查和需要确认的事项。",
            msg.get("operator_open_id") or "",
            runtime=runtime.get("runtime") or "codex",
            project=runtime.get("project") or "",
        )
        if not ok:
            raise RuntimeError(err or "failed to send owner context to helper runtime")
        result = {
            "helper_id": helper_id,
            "status": "owner_invited",
            "runtime": runtime.get("runtime") or "codex",
            "context_sent": ok,
            "context_error": err,
        }
    elif action == "migrate":
        runtime = _ensure_machine_helper_runtime_running(
            machine_id,
            chat_id=chat_id,
            issue_summary=msg.get("issue_summary") or "准备发送新机器迁移诊断 prompt。",
            operator_open_id=msg.get("operator_open_id") or "",
            workspace_id=workspace_id,
            metadata_resolver=metadata_resolver,
        )
        helper_id = runtime.get("helper_id") or msg.get("helper_id") or _machine_helper_id_for_machine(machine_id)
        prompt = build_machine_migration_prompt(
            msg.get("endpoint") or "",
            source_machine_id=machine_id,
            operator_open_id=msg.get("operator_open_id") or "",
        )
        runtime_name = runtime.get("runtime") or "codex"
        ok, err = _send_machine_helper_prompt(
            helper_id,
            prompt,
            runtime=runtime_name,
            delivery_id=f"helper-migrate-{request_id}",
            project=runtime.get("project") or "",
        )
        if not ok:
            raise RuntimeError(err or "failed to send migration prompt to helper runtime")
        result = {
            "helper_id": helper_id,
            "status": "migration_prompt_sent",
            "runtime": runtime_name,
            "context_sent": ok,
            "context_error": err,
        }
    else:
        raise ValueError(f"unknown helper_action: {action!r}")
    result["request_id"] = request_id
    result["machine_id"] = machine_id
    result["helper_action"] = action
    if msg.get("silent_result"):
        result["silent_result"] = True
    if chat_id:
        result["chat_id"] = chat_id
    if msg.get("reply_chat_id"):
        result["reply_chat_id"] = msg.get("reply_chat_id")
    return result


def _is_machine_helper_intern(intern_name):
    if not intern_name:
        return False
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            entry = (json.load(f).get(intern_name) or {})
    except (FileNotFoundError, json.JSONDecodeError):
        entry = {}
    return intern_name.startswith("machine_helper_") or entry.get("role") == "helper"

# Script content hash at startup — used for auto-update detection
def _compute_script_hash():
    """Compute deterministic hash of all files in this script's directory (daemon folder)."""
    try:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        h = hashlib.sha256()
        for root, dirs, files in sorted(os.walk(script_dir)):
            dirs[:] = [d for d in sorted(dirs) if d != '__pycache__']
            for fname in sorted(files):
                if fname.endswith('.pyc'):
                    continue
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, script_dir)
                h.update(rel.encode())
                with open(fpath, "rb") as f:
                    h.update(f.read())
        return h.hexdigest()[:16]
    except Exception:
        return "unknown"

_script_hash = _compute_script_hash()


# ── Meta reported to relay ──
#
# 版本信息由 VSCode 插件随 WS register 窗口帧携带。
# 这样仍覆盖 daemon 重启/插件 Reload/VSCode 重启，同时不再绕 HTTP POST。

_extension_meta = {
    "extension_version": "",
    "hooks_version": "",
    "updated_at": None,
}
_extension_meta_lock = threading.Lock()


def _update_extension_meta(extension_version, hooks_version, source):
    ext_ver = (extension_version or "").strip()
    hooks_ver = (hooks_version or "").strip()
    now_iso = datetime.now().isoformat()
    with _extension_meta_lock:
        _extension_meta["extension_version"] = ext_ver
        _extension_meta["hooks_version"] = hooks_ver
        _extension_meta["updated_at"] = now_iso
    log.info(f"[META] {source}: ext={ext_ver}, hooks={hooks_ver}")
    if _relay_client and _relay_client.connected:
        _relay_client.send({
            "type": "meta_update",
            "extension_version": ext_ver,
            "hooks_version": hooks_ver,
        })
    return now_iso


def _collect_static_meta():
    """Auth-time static meta: CLI versions only. Extension/hooks versions flow in
    later via plugin WS register → stored separately in _extension_meta."""
    import shutil as _shutil
    import subprocess as _sp

    def _probe(cmd):
        exe = _shutil.which(cmd)
        if not exe:
            return ""
        try:
            out = _sp.run([exe, "--version"], capture_output=True, timeout=3, text=True)
            return (out.stdout or out.stderr or "").strip().splitlines()[0] if (out.stdout or out.stderr) else ""
        except (_sp.TimeoutExpired, OSError):
            return ""

    return {
        "cli_versions": {
            "python": sys.version.split()[0],
            "claude": _probe("claude"),
            "codex": _probe("codex"),
        },
    }


_static_meta = _collect_static_meta()


# ── Daemon self-check warnings reported to relay/admin ──
_daemon_warnings = {}
_daemon_warnings_lock = threading.Lock()
_daemon_warning_last_log = {}
_DAEMON_WARNING_LOG_INTERVAL = 300


def _set_daemon_warning(code, detail):
    now_ts = time.time()
    now_iso = datetime.now().isoformat()
    should_log = False
    changed = False
    with _daemon_warnings_lock:
        existing = _daemon_warnings.get(code)
        if existing is None:
            _daemon_warnings[code] = {"code": code, "detail": detail, "since": now_iso}
            changed = True
            should_log = True
        elif existing.get("detail") != detail:
            existing["detail"] = detail
            changed = True
            should_log = True
        elif now_ts - _daemon_warning_last_log.get(code, 0) >= _DAEMON_WARNING_LOG_INTERVAL:
            should_log = True
        if should_log:
            _daemon_warning_last_log[code] = now_ts
    if should_log:
        log.warning(f"[WARN] {code}: {detail}")
    return changed


def _clear_daemon_warning(code):
    with _daemon_warnings_lock:
        existed = code in _daemon_warnings
        if existed:
            del _daemon_warnings[code]
            _daemon_warning_last_log.pop(code, None)
    if existed:
        log.info(f"[WARN] cleared {code}")
    return existed


def _collect_daemon_warnings():
    with _daemon_warnings_lock:
        return [dict(_daemon_warnings[code]) for code in sorted(_daemon_warnings)]


def _count_feishu_daemon_processes():
    script_name = os.path.basename(__file__)
    count = 0
    pids = []
    proc_dir = "/proc"
    try:
        pid_names = os.listdir(proc_dir)
    except OSError:
        return 1, [str(os.getpid())]
    for pid_text in pid_names:
        if not pid_text.isdigit():
            continue
        cmdline_path = os.path.join(proc_dir, pid_text, "cmdline")
        try:
            with open(cmdline_path, "rb") as cmdline_file:
                raw_parts = [part for part in cmdline_file.read().split(b"\0") if part]
        except OSError:
            continue
        args = [part.decode(errors="ignore") for part in raw_parts]
        if "py_compile" in args:
            continue
        if any(os.path.basename(arg) == script_name for arg in args):
            count += 1
            pids.append(pid_text)
    return count, pids


def _check_multi_daemon_warning():
    count, pids = _count_feishu_daemon_processes()
    if count > 1:
        shown_pids = ",".join(pids[:8])
        suffix = "" if len(pids) <= 8 else ",..."
        return _set_daemon_warning(
            "multi_daemon",
            f"发现 {count} 个 feishu_daemon.py 进程 (pids={shown_pids}{suffix})",
        )
    return _clear_daemon_warning("multi_daemon")


def _sync_warnings_if_changed(changed):
    if changed and _relay_client and _relay_client.connected:
        threading.Thread(target=_refresh_lights, daemon=True).start()


def _collect_resources():
    """Dynamic machine resources reported on each sync_online."""
    import shutil as _shutil
    res = {}
    try:
        res["loadavg"] = list(os.getloadavg())
    except OSError:
        pass
    try:
        du = _shutil.disk_usage(WORK_AGENTS_ROOT)
        res["disk_free_gb"] = round(du.free / (1024 ** 3), 1)
    except OSError:
        pass
    return res


_STATUS_MD_METADATA_RE = re.compile(r"<!--\s*METADATA:(.+?)\s*-->")


def _parse_status_metadata(status_md_path):
    """Read line 3 (METADATA) of status.md. Returns {STATUS, TASK, ROLE} or {}."""
    try:
        with open(status_md_path) as f:
            lines = f.read().splitlines()
    except (FileNotFoundError, OSError):
        return {}
    if len(lines) < 3:
        return {}
    m = _STATUS_MD_METADATA_RE.search(lines[2])
    if not m:
        return {}
    result = {}
    for pair in m.group(1).split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            result[k.strip()] = v.strip()
    result.setdefault("ROLE", "independent")
    return result


def _load_local_enterprise_sessions():
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return {key: value for key, value in data.items() if isinstance(value, dict)}


def _session_status_path(entry):
    intern_dir = entry.get("intern_dir") or ""
    if intern_dir:
        state_file = os.path.join(intern_dir, ".hook_state.json")
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            state = {}
        resolver = state.get("metadata_resolver") if isinstance(state.get("metadata_resolver"), dict) else {}
        if resolver.get("status_path"):
            return resolver["status_path"]
    return _get_status_md_path(entry.get("intern_name") or "", entry.get("project") or "")


def _online_key(intern_name, project=""):
    return f"{project}:{intern_name}" if project else intern_name


def _is_turn_active(intern_name, online_names, project=""):
    """True when the intern is online AND its feishu turn is not finalized.

    Used to drive the dashboard blue-light (mid-turn) vs green-light (online but idle).
    A turn is "active" only when the feishu module has an outstanding message for
    the current turn (``message_id`` set) and Stop APPROVE has not yet flipped
    ``finalized``. Dormant tmux sessions that have never run a turn have neither
    field set — they must stay green (idle), not blue.
    """
    if _online_key(intern_name, project) not in online_names and intern_name not in online_names:
        return False
    intern_dir = _get_intern_dir(intern_name, project=project)
    if not intern_dir:
        return False
    state_file = os.path.join(intern_dir, ".hook_state.json")
    try:
        with open(state_file, "r") as f:
            state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    fs = state.get("feishu", {})
    if not fs.get("message_id"):
        return False
    return not bool(fs.get("finalized", False))


def _collect_interns_dynamic(online_names=None):
    """Per-intern dynamic state: status, current_task, last_active, turn_active.
    Enterprise interns read status.md from their hook_state metadata resolver.
    Silently skips interns without a status.md."""
    if not _registry:
        return []
    if online_names is None:
        online_names = set()
    result = []
    all_interns = _iter_registry_entries(_registry)
    for item in all_interns:
        name = item["name"]
        project = _get_intern_project_scoped(name, project=item.get("project") or "")
        status_md = _get_status_md_path(name, project)
        if not os.path.isfile(status_md):
            continue
        meta = _parse_status_metadata(status_md)
        try:
            mtime = os.path.getmtime(status_md)
            last_active = datetime.fromtimestamp(mtime).isoformat()
        except OSError:
            last_active = ""
        result.append({
            "name": name,
            "project": project,
            "status": meta.get("STATUS", ""),
            "current_task": meta.get("TASK", ""),
            "role": meta.get("ROLE", "independent"),
            "team_id": meta.get("TEAM_ID", "") or meta.get("TEAM", ""),
            "last_active": last_active,
            "turn_active": _is_turn_active(name, online_names, project=project),
        })
    return result

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("feishu_daemon")


# ══════════════════════════════════════════
# 凭据 & Registry
# ══════════════════════════════════════════

def fetch_daemon_policy_from_relay(relay_cfg):
    relay_http_url = relay_cfg.get("relay_http_url") or _relay_http_url_from_ws(relay_cfg["relay_url"])
    query = urllib.parse.urlencode({
        key: value for key, value in {
            "machine_id": relay_cfg.get("machine_id") or "",
            "owner_mobile": relay_cfg.get("owner_mobile") or "",
            "owner_open_id": relay_cfg.get("owner_open_id") or "",
        }.items() if value
    })
    path = "/api/enterprise/daemon-policy" + (f"?{query}" if query else "")
    req = urllib.request.Request(
        relay_http_url.rstrip("/") + path,
        headers={"Authorization": f"Bearer {relay_cfg['relay_token']}"},
    )
    try:
        resp = urllib.request.urlopen(req, timeout=90)
        result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        raise RuntimeError(f"relay daemon policy fetch failed: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"relay daemon policy fetch failed: {exc}") from exc
    policy = result.get("policy")
    if not isinstance(policy, dict):
        raise RuntimeError("relay daemon policy response missing policy object")
    policy = _apply_local_policy_override(policy)
    return policy


def _deep_merge_dict(base, override):
    if not isinstance(base, dict) or not isinstance(override, dict):
        return override
    merged = dict(base)
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_local_policy_override(policy):
    """Deep-merge a user-managed local override onto the relay policy.

    Lets a machine that cannot reach the intranet Claude gateway rewrite
    ANTHROPIC_BASE_URL (and inject ANTHROPIC_AUTH_TOKEN via secret_env) to a
    reachable public relay. The override file is never overwritten by relay
    sync, so the fix survives daemon restarts and relay pushes.
    """
    try:
        override_path = daemon_policy_path(WORK_AGENTS_ROOT).parent / "local_policy_override.json"
        if not override_path.is_file():
            return policy
        override = json.loads(override_path.read_text(encoding="utf-8"))
        if not isinstance(override, dict):
            return policy
        override = {k: v for k, v in override.items() if not str(k).startswith("_")}
        merged = _deep_merge_dict(policy, override)
        log.info(f"[POLICY] Applied local policy override from {override_path}")
        return merged
    except Exception as exc:
        log.warning(f"[POLICY] local policy override skipped: {exc}")
        return policy


def feishu_credentials_from_policy(policy):
    feishu = policy.get("feishu") if isinstance(policy, dict) and isinstance(policy.get("feishu"), dict) else {}
    app_id = str(feishu.get("app_id") or "").strip()
    app_secret = str(feishu.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("daemon policy missing feishu.app_id/app_secret")
    return app_id, app_secret


def _write_daemon_policy(policy):
    _write_json_file_atomic(os.fspath(daemon_policy_path(WORK_AGENTS_ROOT)), policy, mode=0o600)


def _refresh_api_credentials_from_policy(policy):
    api = globals().get("_api")
    if not api:
        return
    app_id, app_secret = feishu_credentials_from_policy(policy)
    if api.app_id == app_id and api.app_secret == app_secret:
        return
    api.app_id = app_id
    api.app_secret = app_secret
    api._token = None
    api._token_expires = 0
    log.info(f"[POLICY] Feishu credentials refreshed from daemon policy: app_id={app_id[:8]}...")


def apply_daemon_policy(policy):
    """Apply relay-hosted daemon policy to daemon-local config.

    Supported enterprise policy path:
    `feishu.tool_buffer_flush_interval_seconds`.
    `session_env` / `{codex,claude}.session_env`.
    """
    if not isinstance(policy, dict):
        raise ValueError("daemon policy must be an object")
    applied = {}
    feishu = policy.get("feishu") if isinstance(policy.get("feishu"), dict) else {}
    if "tool_buffer_flush_interval_seconds" in feishu:
        seconds = feishu.get("tool_buffer_flush_interval_seconds")
        changed = daemon_chat_config.set_tool_buffer_flush_interval_seconds(seconds)
        applied["tool_buffer_flush_interval_seconds"] = {"value": seconds, "changed": changed}
    if has_session_env_policy(policy):
        if not _relay_ready_for_session_restarts():
            applied["session_env"] = {
                "skipped": True,
                "reason": "relay_not_connected",
            }
            log.warning("[POLICY] session_env apply deferred until relay is connected")
            return applied
        session_env_report = materialize_session_env(
            work_root=WORK_AGENTS_ROOT,
            policy=policy,
            owner=load_owner_config(WORK_AGENTS_ROOT),
            environ=os.environ,
        )
        applied["session_env"] = session_env_report
        session_env_restarts = _restart_providers_for_policy_env(session_env_report)
        if session_env_restarts:
            applied["session_env_restarts"] = session_env_restarts
    return applied


def sync_daemon_policy_from_relay(relay_cfg, env_switch_state=None):
    try:
        policy = fetch_daemon_policy_from_relay(relay_cfg)
        feishu_credentials_from_policy(policy)
        _write_daemon_policy(policy)
        _refresh_api_credentials_from_policy(policy)
        machine_id = str((relay_cfg or {}).get("machine_id") or load_owner_config(WORK_AGENTS_ROOT).get("machine_id") or "")
        if isinstance(env_switch_state, dict):
            save_env_switch_state(
                work_root=WORK_AGENTS_ROOT,
                policy=policy,
                machine_id=machine_id,
                enabled_groups=env_switch_state.get("enabled_groups") or [],
                group_values=env_switch_state.get("group_values") if isinstance(env_switch_state.get("group_values"), dict) else {},
            )
        effective_policy = policy_with_env_switch_state(
            work_root=WORK_AGENTS_ROOT,
            policy=policy,
            machine_id=machine_id,
        )
        applied = apply_daemon_policy(effective_policy)
    except Exception as exc:
        log.warning(f"[POLICY] daemon policy sync skipped: {exc}")
        return {"ok": False, "error": str(exc)}
    if applied:
        log.info(f"[POLICY] daemon policy applied: {json.dumps(applied, ensure_ascii=False)}")
    else:
        log.info("[POLICY] daemon policy loaded; no local feishu buffer settings present")
    return applied


def load_credentials(relay_cfg):
    policy = fetch_daemon_policy_from_relay(relay_cfg)
    _write_daemon_policy(policy)
    return feishu_credentials_from_policy(policy)


def _relay_http_url_from_ws(relay_url):
    parsed = urllib.parse.urlparse(relay_url)
    scheme = "https" if parsed.scheme == "wss" else "http"
    port = parsed.port
    if port is not None and port > 1:
        port -= 1
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = f"{host}:{port}" if port is not None else host
    return urllib.parse.urlunparse((scheme, netloc, "", "", "", ""))


# ══════════════════════════════════════════
# 飞书 API
# ══════════════════════════════════════════

def _build_post_content(text):
    lines = str(text or "").split("\n")
    content_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            if i < len(lines):
                i += 1
            content_lines.append([{
                "tag": "code_block",
                "language": "PLAINTEXT",
                "text": "\n".join(code_lines),
            }])
        elif line.strip() == "---":
            content_lines.append([{"tag": "hr"}])
            i += 1
        else:
            content_lines.append([{"tag": "text", "text": line}])
            i += 1
    return json.dumps({"zh_cn": {"title": "", "content": content_lines}})


def _estimate_post_body_size(text):
    content_json = _build_post_content(text)
    return len(json.dumps({"msg_type": "post", "content": content_json}).encode("utf-8"))


class FeishuAPI:
    def __init__(self, app_id, app_secret, credential_loader=None):
        self.app_id = app_id
        self.app_secret = app_secret
        self._credential_loader = credential_loader
        self._token = None
        self._token_expires = 0

    def _ensure_credentials(self):
        if self.app_id and self.app_secret:
            return True
        if not self._credential_loader:
            return False
        try:
            self.app_id, self.app_secret = self._credential_loader()
            log.info("Credentials refreshed from relay into daemon memory")
            return bool(self.app_id and self.app_secret)
        except Exception as exc:
            log.error(f"refresh credentials from relay failed: {exc}")
            return False

    def _get_token(self):
        now = time.time()
        if self._token and now < self._token_expires - 300:
            return self._token
        if not self._ensure_credentials():
            return None
        payload = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode()
        req = urllib.request.Request(f"{BASE_URL}/auth/v3/tenant_access_token/internal",
                                     data=payload, headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=90)
            result = json.loads(resp.read())
            if result.get("code") == 0:
                self._token = result["tenant_access_token"]
                self._token_expires = now + result.get("expire", 7200)
                return self._token
        except Exception as e:
            log.error(f"get_token failed: {e}")
        return None

    def _request(self, method, path, body=None):
        token = self._get_token()
        if not token:
            return None, "no token"
        url = f"{BASE_URL}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read())
            if result.get("code") == 0:
                return result.get("data"), None
            return None, f"code={result.get('code')}, msg={result.get('msg')}"
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as e:
            return None, str(e)

    def send_message(self, chat_id, text):
        data, err = self._request("POST", "/im/v1/messages?receive_id_type=chat_id", {
            "receive_id": chat_id, "msg_type": "post", "content": _build_post_content(text)})
        if err:
            return None, err
        return data.get("message_id") if data else None, None

    def update_message(self, message_id, text):
        _, err = self._request("PUT", f"/im/v1/messages/{message_id}", {
            "msg_type": "post", "content": _build_post_content(text)})
        return err

    def reply_message(self, message_id, text):
        content = json.dumps({"text": text})
        _, err = self._request("POST", f"/im/v1/messages/{message_id}/reply", {
            "msg_type": "text", "content": content})
        return err

    def reply_image(self, message_id, image_key):
        content = json.dumps({"image_key": image_key})
        _, err = self._request("POST", f"/im/v1/messages/{message_id}/reply", {
            "msg_type": "image", "content": content})
        return err

    def send_to_user(self, open_id, text):
        """通过 open_id 直接给用户发消息"""
        data, err = self._request("POST", "/im/v1/messages?receive_id_type=open_id", {
            "receive_id": open_id, "msg_type": "post", "content": _build_post_content(text)})
        if err:
            return None, err
        return data.get("message_id") if data else None, None

    def mobile_to_open_id(self, mobile):
        """通过手机号反查飞书 open_id，返回 (open_id, error)"""
        data, err = self._request(
            "POST", "/contact/v3/users/batch_get_id?user_id_type=open_id",
            {"mobiles": [mobile]}
        )
        if err:
            return None, err
        user_list = (data or {}).get("user_list", [])
        if user_list and user_list[0].get("user_id"):
            return user_list[0]["user_id"], None
        return None, f"mobile '{mobile}' not found in this tenant"

    def get_user_info(self, open_id):
        """Resolve open_id to basic Feishu user info."""
        if not open_id:
            return None, "empty open_id"
        data, err = self._request("GET", f"/contact/v3/users/{open_id}?user_id_type=open_id")
        if err or not data:
            return None, err or "empty response"
        user = data.get("user") or {}
        return {
            "name": user.get("name", ""),
            "mobile": user.get("mobile", ""),
            "avatar_url": (user.get("avatar") or {}).get("avatar_72", ""),
        }, None

    def create_chat(self, name, description="", owner_open_id=""):
        body = {"name": name, "description": description or f"Intern agent: {name}",
                "chat_type": "private"}
        if owner_open_id:
            body["user_id_list"] = [owner_open_id]
        data, err = self._request("POST", "/im/v1/chats?user_id_type=open_id", body)
        if err:
            return None, err
        return data.get("chat_id") if data else None, None

    def add_chat_managers(self, chat_id, open_ids):
        """把成员设置为群管理员。open_ids 必须已经是群成员。"""
        _, err = self._request(
            "POST", f"/im/v1/chats/{chat_id}/managers/add_managers?member_id_type=open_id",
            {"manager_ids": open_ids})
        return err

    def delete_chat(self, chat_id):
        _, err = self._request("DELETE", f"/im/v1/chats/{chat_id}")
        return err

    def list_chats(self):
        chats = []
        page_token = ""
        while True:
            path = f"/im/v1/chats?page_size=100"
            if page_token:
                path += f"&page_token={page_token}"
            data, err = self._request("GET", path)
            if err:
                log.error(f"list_chats: {err}")
                break
            items = data.get("items", []) if data else []
            for item in items:
                chats.append({"chat_id": item.get("chat_id", ""), "name": item.get("name", "")})
            if not data or not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
        return chats

    def update_chat(self, chat_id, name=None, avatar=None):
        # avatar 字段保留：task204 回滚后当前不主动设置头像（类型由群名 emoji 区分），
        # avatar="" 可用于重置为飞书默认头像。
        body = {}
        if name is not None:
            body["name"] = name
        if avatar is not None:
            body["avatar"] = avatar
        if not body:
            return None
        _, err = self._request("PUT", f"/im/v1/chats/{chat_id}", body)
        return err

    def send_interactive_card(self, chat_id, card_json):
        """Send an interactive card message. Returns (message_id, error)."""
        content = json.dumps(card_json)
        data, err = self._request("POST", "/im/v1/messages?receive_id_type=chat_id", {
            "receive_id": chat_id, "msg_type": "interactive", "content": content})
        if err:
            return None, err
        return data.get("message_id") if data else None, None

    def update_interactive_card(self, message_id, card_json):
        """Update an existing interactive card message via PATCH."""
        content = json.dumps(card_json)
        _, err = self._request("PATCH", f"/im/v1/messages/{message_id}", {
            "msg_type": "interactive", "content": content})
        return err

    def upload_file(self, file_path, file_type="stream"):
        """POST /im/v1/files multipart upload. Returns (file_key, err)。

        飞书文件上限 30MB；超过返回 err。file_type 默认 stream（通用二进制），
        .md 等文本走 stream 即可，飞书 IM 客户端会渲染 markdown 预览。
        """
        import os as _os
        import uuid as _uuid
        if not _os.path.isfile(file_path):
            return None, f"file not found: {file_path}"
        size = _os.path.getsize(file_path)
        if size > 30 * 1024 * 1024:
            return None, f"file {file_path} is {size / 1024 / 1024:.1f} MB, exceeds 30 MB"
        token = self._get_token()
        if not token:
            return None, "no token"
        filename = _os.path.basename(file_path)
        with open(file_path, "rb") as f:
            data = f.read()
        boundary = "----file" + _uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file_type"\r\n\r\n'
            f"{file_type}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file_name"\r\n\r\n'
            f"{filename}\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n"
        ).encode() + data + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{BASE_URL}/im/v1/files", data=body, method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=120)
            result = json.loads(resp.read())
            if result.get("code") == 0:
                return (result.get("data") or {}).get("file_key"), None
            return None, f"code={result.get('code')}, msg={result.get('msg')}"
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as e:
            return None, str(e)

    def upload_image_bytes(self, image_bytes, filename="screenshot.png"):
        """POST /im/v1/images multipart upload with image_type=message.

        Returns (image_key, err). Feishu message images have a 10 MB limit.
        """
        if not image_bytes:
            return None, "empty image"
        size = len(image_bytes)
        if size > 10 * 1024 * 1024:
            return None, f"image is {size / 1024 / 1024:.1f} MB, exceeds 10 MB"
        token = self._get_token()
        if not token:
            return None, "no token"
        safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", filename or "screenshot.png")
        if not safe_name.lower().endswith(".png"):
            safe_name += ".png"
        boundary = "----img" + uuid.uuid4().hex
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image_type"\r\n\r\n'
            f"message\r\n"
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="image"; filename="{safe_name}"\r\n'
            f"Content-Type: image/png\r\n\r\n"
        ).encode() + image_bytes + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{BASE_URL}/im/v1/images",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            resp = urllib.request.urlopen(req, timeout=60)
            result = json.loads(resp.read())
            if result.get("code") == 0:
                return (result.get("data") or {}).get("image_key"), None
            return None, f"code={result.get('code')}, msg={result.get('msg')}"
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as e:
            return None, str(e)

    def send_file(self, chat_id, file_key):
        """Send a previously uploaded file as msg_type=file. Returns (message_id, err)."""
        content = json.dumps({"file_key": file_key})
        data, err = self._request("POST", "/im/v1/messages?receive_id_type=chat_id", {
            "receive_id": chat_id, "msg_type": "file", "content": content})
        if err:
            return None, err
        return data.get("message_id") if data else None, None


# ══════════════════════════════════════════
# Registry
# ══════════════════════════════════════════

class RegistryManager:
    def __init__(self, registry_dir):
        self.registry_dir = registry_dir
        self._cache = {}
        self._intern_to_chat = {}
        self.reload()

    def reload(self):
        self._cache = {}
        self._intern_to_chat = {}
        if not os.path.isdir(self.registry_dir):
            return
        for fname in os.listdir(self.registry_dir):
            if not fname.endswith(".json"):
                continue
            try:
                data = json.loads(Path(os.path.join(self.registry_dir, fname)).read_text())
                chat_id = data.get("chatId", "")
                intern_name = data.get("internName") or fname.replace(".json", "")
                project = data.get("project") or ""
                if chat_id and intern_name:
                    key = self._key(intern_name, project)
                    self._cache[chat_id] = {
                        "intern_name": intern_name,
                        "project": project,
                        "chat_id": chat_id,
                    }
                    self._intern_to_chat[key] = chat_id
            except Exception:
                continue

    @staticmethod
    def _key(intern_name, project=""):
        return f"{project}:{intern_name}" if project else intern_name

    @staticmethod
    def _safe_file_part(value):
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("._") or "default"

    def _registry_path(self, intern_name, project=""):
        if project:
            fname = f"{self._safe_file_part(project)}__{self._safe_file_part(intern_name)}.json"
        else:
            fname = f"{self._safe_file_part(intern_name)}.json"
        return os.path.join(self.registry_dir, fname)

    def find_intern(self, chat_id):
        entry = self._cache.get(chat_id)
        if isinstance(entry, dict):
            return entry.get("intern_name")
        return entry

    def find_intern_info(self, chat_id):
        entry = self._cache.get(chat_id)
        if isinstance(entry, dict):
            return dict(entry)
        if entry:
            return {"intern_name": entry, "project": "", "chat_id": chat_id}
        return {}

    def find_chat_id(self, intern_name, project=""):
        if project:
            chat_id = self._intern_to_chat.get(self._key(intern_name, project))
            if chat_id:
                return chat_id
            if intern_name.startswith("machine_helper_"):
                legacy_chat_id = self._intern_to_chat.get(intern_name)
                if legacy_chat_id:
                    log.warning(
                        f"[REGISTRY] machine helper {_online_key(intern_name, project)} "
                        "using legacy projectless chat registry entry"
                    )
                    return legacy_chat_id
            return None
        exact = self._intern_to_chat.get(intern_name)
        if exact:
            return exact
        matches = [
            chat_id for key, chat_id in self._intern_to_chat.items()
            if key.endswith(f":{intern_name}")
        ]
        if len(set(matches)) == 1:
            return matches[0]
        if len(set(matches)) > 1:
            log.warning(f"[REGISTRY] ambiguous chat lookup for intern={intern_name}; project required")
        return None

    def get_all(self):
        return dict(self._intern_to_chat)

    def get_all_entries(self):
        return [dict(entry) for entry in self._cache.values() if isinstance(entry, dict)]

    def register(self, intern_name, chat_id, project=""):
        os.makedirs(self.registry_dir, exist_ok=True)
        reg_path = self._registry_path(intern_name, project)
        with open(reg_path, "w") as f:
            data = {"internName": intern_name, "chatId": chat_id}
            if project:
                data["project"] = project
            json.dump(data, f, indent=2)
        self.reload()

    def unregister(self, intern_name, project=""):
        paths = [self._registry_path(intern_name, project)]
        if not project:
            for fname in os.listdir(self.registry_dir) if os.path.isdir(self.registry_dir) else []:
                if not fname.endswith(".json"):
                    continue
                try:
                    data = json.loads(Path(os.path.join(self.registry_dir, fname)).read_text())
                except Exception:
                    continue
                if data.get("internName") == intern_name and not data.get("project"):
                    paths.append(os.path.join(self.registry_dir, fname))
        for reg_path in set(paths):
            if os.path.exists(reg_path):
                os.remove(reg_path)
        self.reload()

    # ── owner mobile 持久化 ──

    def load_owner_mobile(self):
        try:
            path_ = OWNER_JSON_PATH
            if os.path.exists(path_):
                return json.loads(Path(path_).read_text()).get("mobile")
        except Exception:
            pass
        return None


def _load_owner_open_id():
    try:
        path_ = Path(OWNER_JSON_PATH)
        if path_.exists():
            owner = json.loads(path_.read_text(encoding="utf-8"))
            return owner.get("owner_open_id") or owner.get("open_id") or ""
    except Exception:
        pass
    return ""


class WorkspaceCache:
    """Daemon-local workspace cache and enable state.

    Relay is authoritative for workspace existence. This cache stores the last
    relay snapshot plus per-machine enable/local path state.
    """

    SCHEMA = "intern-agents.daemon-workspaces.v1"
    SCHEMA_VERSION = 1

    def __init__(self, work_root, cache_path=None):
        self.work_root = work_root
        self.cache_path = cache_path or os.fspath(daemon_workspace_cache_path(work_root))
        self._lock = threading.RLock()
        self._data = {
            "schema": self.SCHEMA,
            "schema_version": self.SCHEMA_VERSION,
            "relay_synced_at": "",
            "workspaces": {},
            "enabled": {},
        }
        self._load()

    def _load(self):
        candidates = [self.cache_path]
        if not os.path.exists(self.cache_path):
            candidates.extend([
                os.fspath(state_registry_path(self.work_root)),
                os.path.join(self.work_root, ".enterprise_state", "workspaces.json"),
            ])
        for load_path in candidates:
            if not os.path.exists(load_path):
                continue
            try:
                with open(load_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("schema") != self.SCHEMA:
                    continue
                data.setdefault("schema_version", self.SCHEMA_VERSION)
                data.setdefault("workspaces", {})
                data.setdefault("enabled", {})
                data.setdefault("relay_synced_at", "")
                self._data = data
                self._load_local_workspace_records()
                return
            except Exception as e:
                log.error(f"[WORKSPACE] failed to load daemon cache {load_path}: {e}")
        self._load_local_workspace_records()

    def _save(self):
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, self.cache_path)

    def _save_workspace_records(self, stale_workspace_ids=()):
        for workspace_id in stale_workspace_ids or ():
            try:
                os.remove(os.fspath(workspace_record_path(self.work_root, workspace_id)))
            except FileNotFoundError:
                pass
        for workspace_id, item in self._data.get("workspaces", {}).items():
            record_path = workspace_record_path(self.work_root, workspace_id)
            record = self._workspace_record_from_item(workspace_id, item)
            os.makedirs(os.path.dirname(os.fspath(record_path)), exist_ok=True)
            tmp = os.fspath(record_path) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(record, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, record_path)

    def _save_state_registry_mapping(self, stale_workspace_ids=()):
        registry_path = state_registry_path(self.work_root)
        data = {}
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            data = {}
        if data.get("schema") != LOCAL_REGISTRY_SCHEMA:
            data = {}
        data.setdefault("schema", LOCAL_REGISTRY_SCHEMA)
        data.setdefault("work_agents_root", os.fspath(Path(self.work_root)))
        data.setdefault("default_metadata_mode", "repo_dotdir")
        workspaces = data.get("workspaces")
        if not isinstance(workspaces, dict):
            workspaces = {}
        data["workspaces"] = workspaces
        for workspace_id in stale_workspace_ids or ():
            workspaces.pop(workspace_id, None)
        for workspace_id in self._data.get("workspaces", {}):
            workspaces[workspace_id] = f"{workspace_id}/workspace.json"
        os.makedirs(os.path.dirname(os.fspath(registry_path)), exist_ok=True)
        tmp = os.fspath(registry_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, registry_path)

    def _workspace_record_from_item(self, workspace_id, item):
        record = dict(item)
        mode = record.get("metadata_mode") or ""
        local_path = record.get("local_path") or self.default_local_path(record)
        metadata_cache_path = record.get("metadata_cache_path") or self.default_metadata_cache_path(workspace_id)
        record.setdefault("schema", WORKSPACE_SCHEMA)
        record.setdefault("workspace_key", workspace_id)
        record.setdefault("local_path", local_path)
        record.setdefault("metadata_cache_path", metadata_cache_path)
        if not isinstance(record.get("metadata"), dict):
            metadata = {
                "mode": mode,
                "repo_relative_path": ".intern_workspace",
                "branch": record.get("metadata_branch") or "intern_workspace",
            }
            if mode == "local_only":
                metadata["local_path"] = os.path.join(metadata_cache_path, "local", ".intern_workspace")
            elif mode == "metadata_branch":
                metadata["local_path"] = os.path.join(metadata_cache_path, ".intern_workspace")
            else:
                metadata["local_path"] = os.path.join(local_path, ".intern_workspace")
            record["metadata"] = metadata
        return record

    def _workspace_item_from_record(self, workspace_id, record):
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        mode = record.get("metadata_mode") or metadata.get("mode") or ""
        if mode != "local_only":
            return None
        item = dict(record)
        item["workspace_id"] = workspace_id
        item.setdefault("display_name", record.get("display_name") or record.get("name") or workspace_id)
        item.setdefault("provider", "local")
        item.setdefault("repo_url", record.get("repo_url") or record.get("local_path") or "")
        item.setdefault("metadata_mode", "local_only")
        item.setdefault("metadata_branch", "")
        item.setdefault("workspace_authority", "local")
        item.setdefault("enabled_by_default", True)
        item.setdefault("deleted", False)
        item.setdefault("local_path", record.get("local_path") or item.get("repo_url") or self.default_local_path(item))
        item.setdefault("metadata_cache_path", record.get("metadata_cache_path") or self.default_metadata_cache_path(workspace_id))
        return item

    def _load_local_workspace_records(self):
        registry_path = state_registry_path(self.work_root)
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                registry = json.load(f)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return
        if not isinstance(registry, dict) or registry.get("schema") != LOCAL_REGISTRY_SCHEMA:
            return
        workspaces = registry.get("workspaces")
        if not isinstance(workspaces, dict):
            return
        loaded = {}
        for workspace_id, rel_path in workspaces.items():
            try:
                workspace_id = self.validate_workspace_id(workspace_id)
            except ValueError:
                continue
            record_path = Path(self.work_root) / "state" / "v1" / str(rel_path or f"{workspace_id}/workspace.json")
            try:
                with open(record_path, "r", encoding="utf-8") as f:
                    record = json.load(f)
            except (FileNotFoundError, OSError, json.JSONDecodeError):
                continue
            item = self._workspace_item_from_record(workspace_id, record)
            if item:
                loaded[workspace_id] = item
        if not loaded:
            return
        self._data.setdefault("workspaces", {})
        for workspace_id, item in loaded.items():
            self._data["workspaces"].setdefault(workspace_id, item)

    def validate_workspace_id(self, workspace_id):
        return validate_workspace_id(workspace_id)

    def sync_from_relay_payload(self, payload):
        workspaces = payload.get("workspaces")
        if not isinstance(workspaces, list):
            raise ValueError("relay workspace response missing workspaces list")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self._lock:
            previous_items = dict(self._data.get("workspaces", {}))
            local_items = {
                workspace_id: dict(item)
                for workspace_id, item in previous_items.items()
                if item.get("workspace_authority") == "local" or (
                    item.get("provider") == "local" and item.get("metadata_mode") == "local_only"
                )
            }
            previous_remote_ids = set(previous_items) - set(local_items)
            self._data["relay_synced_at"] = now
            synced = {}
            for item in workspaces:
                if not isinstance(item, dict) or not item.get("workspace_id"):
                    continue
                workspace_id = self.validate_workspace_id(item["workspace_id"])
                remote_item = dict(item)
                remote_item["workspace_authority"] = "relay"
                synced[workspace_id] = remote_item
            stale_workspace_ids = sorted(previous_remote_ids - set(synced))
            self._data["workspaces"] = {**synced, **local_items}
            for workspace_id in stale_workspace_ids:
                self._data.get("enabled", {}).pop(workspace_id, None)
            self._save()
            self._save_workspace_records(stale_workspace_ids)
            self._save_state_registry_mapping(stale_workspace_ids)
            return self.list()

    def list(self):
        with self._lock:
            result = []
            for workspace_id, item in self._data.get("workspaces", {}).items():
                local = dict(self._data.get("enabled", {}).get(workspace_id, {}))
                merged = dict(item)
                merged["local_enabled"] = bool(local.get("enabled", False))
                merged["local_path"] = local.get("local_path", self.default_local_path(item))
                merged["metadata_cache_path"] = local.get("metadata_cache_path", self.default_metadata_cache_path(workspace_id))
                merged["state_path"] = os.fspath(workspace_state_dir(self.work_root, workspace_id))
                merged["last_checked_at"] = local.get("last_checked_at", "")
                result.append(merged)
            result.sort(key=lambda x: (x.get("display_name") or "", x.get("workspace_id") or ""))
            return {
                "schema": self.SCHEMA,
                "schema_version": self.SCHEMA_VERSION,
                "relay_synced_at": self._data.get("relay_synced_at", ""),
                "workspaces": result,
            }

    def get_workspace(self, workspace_id):
        workspace_id = self.validate_workspace_id(workspace_id)
        with self._lock:
            item = self._data.get("workspaces", {}).get(workspace_id)
            return dict(item) if item else None

    def default_local_path(self, workspace):
        workspace_id = self.validate_workspace_id(workspace.get("workspace_id") or "")
        if workspace.get("provider") == "local" and workspace.get("repo_url"):
            return os.fspath(Path(str(workspace.get("repo_url"))).expanduser())
        return os.fspath(workspace_source_path(self.work_root, workspace_id))

    def default_metadata_cache_path(self, workspace_id):
        return os.fspath(workspace_metadata_cache_path(self.work_root, self.validate_workspace_id(workspace_id)))

    def _ensure_code_checkout(self, workspace, local_path):
        provider = workspace.get("provider") or ""
        repo_url = workspace.get("repo_url") or ""
        if provider == "local":
            if not os.path.isdir(os.path.join(local_path, ".git")):
                raise RuntimeError(f"local workspace path is not a git repo: {local_path}")
            return
        if not repo_url:
            raise RuntimeError("repo_url is required to enable remote workspace")
        if os.path.isdir(os.path.join(local_path, ".git")):
            return
        target = Path(local_path)
        if target.exists() and any(target.iterdir()):
            raise RuntimeError(f"local workspace path is not a git repo and is not empty: {local_path}")
        if target.exists():
            target.rmdir()
        target.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        ssh_command = env.get("GIT_SSH_COMMAND", "ssh")
        if "BatchMode" not in ssh_command:
            ssh_command = f"{ssh_command} -o BatchMode=yes"
        if "ConnectTimeout" not in ssh_command:
            ssh_command = f"{ssh_command} -o ConnectTimeout=10"
        env["GIT_SSH_COMMAND"] = ssh_command
        result = subprocess.run(
            ["git", "clone", repo_url, local_path],
            capture_output=True,
            text=True,
            env=env,
            timeout=int(os.environ.get("INTERN_CODE_GIT_TIMEOUT", "120")),
        )
        if result.returncode != 0:
            shutil.rmtree(local_path, ignore_errors=True)
            detail = (result.stderr or result.stdout or "").strip()
            raise RuntimeError(f"code repo clone failed: {detail}")

    def enable(self, workspace_id, local_path=None):
        workspace_id = self.validate_workspace_id(workspace_id)
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            raise KeyError(f"workspace not found: {workspace_id}")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if not local_path and workspace.get("provider") == "local" and workspace.get("repo_url"):
            local_path = workspace.get("repo_url")
        local_path = local_path or self.default_local_path(workspace)
        self._ensure_code_checkout(workspace, local_path)
        metadata_cache_path = self.default_metadata_cache_path(workspace_id)
        if workspace.get("metadata_mode") == "metadata_branch":
            os.makedirs(metadata_cache_path, exist_ok=True)
            checkout_workspace = dict(workspace)
            checkout_workspace["local_path"] = local_path
            checkout_workspace["metadata_cache_path"] = metadata_cache_path
            ensure_metadata_branch_checkout(checkout_workspace, workspace_id=workspace_id)
        elif workspace.get("metadata_mode") == "local_only":
            os.makedirs(os.path.join(metadata_cache_path, "local", ".intern_workspace"), exist_ok=True)
        with self._lock:
            self._data.setdefault("enabled", {})[workspace_id] = {
                "enabled": True,
                "local_path": local_path,
                "metadata_cache_path": metadata_cache_path,
                "last_checked_at": now,
            }
            self._save()
        return {"ok": True, "workspace_id": workspace_id, "enabled": True, "local_path": local_path}

    def disable(self, workspace_id):
        workspace_id = self.validate_workspace_id(workspace_id)
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            raise KeyError(f"workspace not found: {workspace_id}")
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        with self._lock:
            current = self._data.setdefault("enabled", {}).get(workspace_id, {})
            current["enabled"] = False
            current["last_checked_at"] = now
            current.setdefault("local_path", self.default_local_path(workspace))
            current.setdefault("metadata_cache_path", self.default_metadata_cache_path(workspace_id))
            self._data["enabled"][workspace_id] = current
            self._save()
        return {"ok": True, "workspace_id": workspace_id, "enabled": False, "local_path": current["local_path"]}

    def create_local(self, body):
        display_name = (body.get("display_name") or body.get("name") or "").strip()
        repo_url = os.fspath(Path(str(body.get("repo_url") or "")).expanduser())
        provider = (body.get("provider") or "").strip().lower()
        mode = (body.get("metadata_mode") or body.get("mode") or "").strip()
        if provider != "local":
            raise ValueError("local workspace create requires provider=local")
        if mode != "local_only":
            raise ValueError("local workspaces only support local_only metadata mode")
        if not display_name:
            raise ValueError("display_name required")
        if not repo_url or not os.path.isabs(repo_url):
            raise ValueError("local workspace repo_url must be an absolute path")
        if not os.path.isdir(os.path.join(repo_url, ".git")):
            raise RuntimeError(f"local workspace path is not a git repo: {repo_url}")
        workspace_id = _workspace_id_from_body(body)
        now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        metadata_cache_path = self.default_metadata_cache_path(workspace_id)
        item = {
            "workspace_id": workspace_id,
            "display_name": display_name,
            "provider": "local",
            "repo_url": repo_url,
            "provider_config": body.get("provider_config") or body.get("codeup_config") or {},
            "metadata_mode": "local_only",
            "metadata_branch": "",
            "enabled_by_default": bool(body.get("enabled_by_default", True)),
            "created_by": body.get("created_by") or "",
            "created_at": now,
            "updated_at": now,
            "deleted": False,
            "workspace_authority": "local",
            "local_path": repo_url,
            "metadata_cache_path": metadata_cache_path,
        }
        with self._lock:
            existing = self._data.setdefault("workspaces", {}).get(workspace_id)
            if existing and not existing.get("deleted"):
                raise ValueError(f"workspace already exists: {workspace_id}")
            self._data["workspaces"][workspace_id] = item
            self._data.setdefault("enabled", {}).setdefault(workspace_id, {
                "enabled": False,
                "local_path": repo_url,
                "metadata_cache_path": metadata_cache_path,
                "last_checked_at": now,
            })
            os.makedirs(os.path.join(metadata_cache_path, "local", ".intern_workspace"), exist_ok=True)
            self._save()
            self._save_workspace_records()
            self._save_state_registry_mapping()
        return dict(item)

    def doctor(self, workspace_id):
        workspace_id = self.validate_workspace_id(workspace_id)
        workspace = self.get_workspace(workspace_id)
        if not workspace:
            raise KeyError(f"workspace not found: {workspace_id}")
        with self._lock:
            local = dict(self._data.get("enabled", {}).get(workspace_id, {}))
        local_path = local.get("local_path", self.default_local_path(workspace))
        checks = {
            "registered_in_relay_cache": True,
            "local_enabled": bool(local.get("enabled", False)),
            "local_clone_exists": os.path.isdir(os.path.join(local_path, ".git")),
            "metadata_mode": workspace.get("metadata_mode", ""),
            "metadata_cache_path": local.get("metadata_cache_path", self.default_metadata_cache_path(workspace_id)),
        }
        return {"ok": True, "workspace_id": workspace_id, "checks": checks, "workspace": workspace}


# ══════════════════════════════════════════
# WebSocket server (push to plugin)
# ══════════════════════════════════════════

class WSServer:
    """WebSocket server for pushing messages to VS Code plugin.
    
    Maintains a window_registry for targeted routing:
    - Each plugin sends a 'register' frame on connect with {window_id, active_intern, active_project}
    - 'update_active' frames update the active intern/project for a window
    - feishu_message is routed only to the window whose active project/intern matches
    - status_changed is broadcast to all (every window may care)
    """

    def __init__(self, port):
        self.port = port  # requested port (0 = ephemeral)
        self.actual_port = None  # filled after bind
        self.clients = set()
        # window_id → { "ws": websocket, "active": intern_name|None, "active_project": project|"" }
        self.windows = {}
        self._loop = None
        self._server = None
        self._bound = threading.Event()
        self._restart_progress_lock = threading.Lock()
        self._restart_progress_backlog = []
        self._restart_progress_backlog_ttl_seconds = 10 * 60

    def start(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        # Wait for the server to actually bind so caller can read actual_port
        if not self._bound.wait(timeout=10):
            raise RuntimeError("WSServer failed to bind within 10s")
        log.info(f"WebSocket server starting on ws://localhost:{self.actual_port}")

    def _run(self):
        try:
            import websockets
        except ImportError:
            log.error("[WS_SERVER] FATAL: 'websockets' package not installed! Run: pip3 install websockets lark-oapi")
            raise
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        def _parse_window_started_at(window_id, raw_started_at):
            try:
                if raw_started_at is not None:
                    return int(raw_started_at)
            except (TypeError, ValueError):
                pass

            if isinstance(window_id, str):
                match = re.search(r"(\d{10,})$", window_id)
                if match:
                    try:
                        return int(match.group(1))
                    except ValueError:
                        pass

            return 0

        async def handler(websocket):
            self.clients.add(websocket)
            window_id = None
            log.info(f"WS client connected ({len(self.clients)} total)")
            try:
                async for raw in websocket:
                    try:
                        msg = json.loads(raw)
                        msg_type = msg.get("type")
                        if msg_type == "register":
                            window_id = msg.get("window_id")
                            active = msg.get("active_intern")
                            active_project = msg.get("active_project") or ""
                            if active and not active_project:
                                raise ValueError("active_project required when active_intern is set")
                            started_at = _parse_window_started_at(window_id, msg.get("window_started_at"))

                            stale_ws = self._register_window(window_id, websocket, active, started_at, active_project)
                            if stale_ws:
                                log.info(f"[WS_REG] Replacing previous socket for window {window_id}")
                                self.clients.discard(stale_ws)
                                asyncio.ensure_future(stale_ws.close(1000, "replaced by same window"))
                            log.info(f"[WS_REG] Window {window_id} registered, active={_online_key(active, active_project) if active else None}, started_at={started_at}")
                            if "extension_version" in msg or "hooks_version" in msg:
                                _update_extension_meta(
                                    msg.get("extension_version", ""),
                                    msg.get("hooks_version", ""),
                                    "ws_register",
                                )
                            else:
                                log.warning("[META] WS register missing extension/hooks version")
                            await self._replay_restart_progress(websocket)
                            threading.Thread(target=_refresh_lights, daemon=True).start()
                        elif msg_type == "update_active":
                            if window_id and window_id in self.windows:
                                old = self.windows[window_id].get("active")
                                old_project = self.windows[window_id].get("active_project") or ""
                                active = msg.get("active_intern")
                                active_project = msg.get("active_project") or ""
                                if active and not active_project:
                                    raise ValueError("active_project required when active_intern is set")
                                self.windows[window_id]["active"] = active
                                self.windows[window_id]["active_project"] = active_project
                                new_active = self.windows[window_id].get("active")
                                new_project = self.windows[window_id].get("active_project") or ""
                                log.info(f"[WS_REG] Window {window_id} active: "
                                         f"{_online_key(old, old_project) if old else None} → "
                                         f"{_online_key(new_active, new_project) if new_active else None}")
                                threading.Thread(target=_refresh_lights, daemon=True).start()
                    except json.JSONDecodeError:
                        pass
            finally:
                self.clients.discard(websocket)
                if window_id and window_id in self.windows and self.windows[window_id].get("ws") is websocket:
                    del self.windows[window_id]
                    log.info(f"[WS_REG] Window {window_id} unregistered")
                    threading.Thread(target=_refresh_lights, daemon=True).start()
                log.info(f"WS client disconnected ({len(self.clients)} total)")

        async def serve():
            self._server = await websockets.serve(handler, "localhost", self.port)
            # Capture actual bound port (matters when self.port == 0 → ephemeral)
            sock = list(self._server.sockets)[0]
            self.actual_port = sock.getsockname()[1]
            self._bound.set()
            log.info(f"WebSocket server listening on ws://localhost:{self.actual_port}")
            await self._server.wait_closed()

        self._loop.run_until_complete(serve())

    def _register_window(self, window_id, websocket, active, started_at, active_project):
        """Register one VS Code plugin connection without evicting other windows.

        Multiple users may open the extension from the same machine and same
        WORK_AGENTS_ROOT. They intentionally share this daemon, so distinct
        window_id values must coexist. Only a reconnect/reload of the same
        window_id replaces its previous socket.
        """
        if active and not active_project:
            raise ValueError("active_project required when active is set")
        existing_entry = self.windows.get(window_id)
        stale_ws = None
        if existing_entry and existing_entry.get("ws") is not websocket:
            stale_ws = existing_entry["ws"]

        self.windows[window_id] = {
            "ws": websocket,
            "active": active,
            "active_project": active_project,
            "started_at": started_at,
        }
        return stale_ws

    def _recent_restart_progress_payloads(self):
        now = time.time()
        with self._restart_progress_lock:
            self._restart_progress_backlog = [
                item for item in self._restart_progress_backlog
                if now - item["recorded_at"] <= self._restart_progress_backlog_ttl_seconds
            ]
            return [dict(item["payload"]) for item in self._restart_progress_backlog]

    def _record_restart_progress(self, data):
        now = time.time()
        with self._restart_progress_lock:
            self._restart_progress_backlog.append({
                "recorded_at": now,
                "payload": dict(data),
            })
            self._restart_progress_backlog = [
                item for item in self._restart_progress_backlog[-200:]
                if now - item["recorded_at"] <= self._restart_progress_backlog_ttl_seconds
            ]

    async def _replay_restart_progress(self, websocket):
        payloads = self._recent_restart_progress_payloads()
        if not payloads:
            return
        for payload in payloads:
            try:
                await websocket.send(json.dumps(payload))
            except Exception as exc:
                log.debug(f"[RESTART_PROGRESS] replay failed: {exc}")
                return
        log.info(f"[RESTART_PROGRESS] replayed {len(payloads)} event(s) to registered VS Code window")

    def push(self, data):
        """Broadcast message to all connected clients (used for status_changed)."""
        if isinstance(data, dict) and data.get("type") == "intern_restart_progress":
            self._record_restart_progress(data)
        if not self.clients or not self._loop:
            return
        msg = json.dumps(data)

        async def _send():
            disconnected = set()
            for ws in self.clients.copy():
                try:
                    await ws.send(msg)
                except Exception:
                    disconnected.add(ws)
            self.clients -= disconnected

        asyncio.run_coroutine_threadsafe(_send(), self._loop)

    def route_to_active(self, intern_name, data, project):
        """Send message only to the window whose active_intern matches. Returns True if delivered."""
        if not self._loop:
            return False
        if not project:
            raise ValueError("project required for active window routing")
        target_ws = None
        target_key = _online_key(intern_name, project)
        for wid, info in self.windows.items():
            active = info.get("active")
            active_project = info.get("active_project") or ""
            if active and _online_key(active, active_project) == target_key:
                target_ws = info["ws"]
                break
        if not target_ws:
            return False
        msg = json.dumps(data)
        asyncio.run_coroutine_threadsafe(target_ws.send(msg), self._loop)
        return True

    def get_active_intern_keys(self):
        """Return set of active scoped keys."""
        keys = set()
        for info in self.windows.values():
            active = info.get("active")
            if not active:
                continue
            active_project = info.get("active_project") or ""
            if not active_project:
                raise ValueError("active_project required for active window")
            keys.add(_online_key(active, active_project))
        return keys

    def is_active(self, intern_name, project):
        if not project:
            raise ValueError("project required for active window lookup")
        return _online_key(intern_name, project) in self.get_active_intern_keys()


# ══════════════════════════════════════════
# Local Config & Relay Client
# ══════════════════════════════════════════

def load_relay_config():
    """Load relay config from _owner.json. Returns dict with relay_url, relay_token.
    
    Raises SystemExit if _owner.json is missing or lacks relay fields.
    """
    if not os.path.exists(OWNER_JSON_PATH):
        log.error(f"_owner.json not found: {OWNER_JSON_PATH}")
        sys.exit(1)
    try:
        with open(OWNER_JSON_PATH, "r") as f:
            owner = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        log.error(f"Failed to load {OWNER_JSON_PATH}: {e}")
        sys.exit(1)
    relay_url = owner.get("relay_url", "")
    relay_token = owner.get("relay_token", "")
    relay_http_url = owner.get("relay_http_url") or _relay_http_url_from_ws(relay_url)
    if not relay_url or not relay_token:
        log.error(f"_owner.json missing relay_url or relay_token")
        sys.exit(1)
    import socket
    # Get local IP
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = socket.gethostbyname(socket.gethostname())
    # Get SSH port from SSH_CONNECTION env var (format: client_ip client_port server_ip server_port)
    ssh_port = 22
    ssh_conn = os.environ.get("SSH_CONNECTION", "")
    if ssh_conn:
        parts = ssh_conn.split()
        if len(parts) >= 4:
            ssh_port = int(parts[3])
    return {
        "relay_url": relay_url,
        "relay_http_url": relay_http_url,
        "relay_token": relay_token,
        "machine_id": _build_instance_id(),
        "owner_mobile": owner.get("mobile", ""),
        "owner_open_id": owner.get("owner_open_id") or owner.get("open_id") or "",
        "ip": local_ip,
        "ssh_port": ssh_port,
    }


def enrich_owner_identity_at_startup(api):
    """Resolve owner mobile once at daemon startup and persist display fields.

    `_owner.json` is the local source VS Code can read without making Feishu API
    calls. Some deployed files only contain `mobile`; resolving the name here lets
    UI defaults use the supervisor's name instead of falling back to `user`.
    """
    try:
        with open(OWNER_JSON_PATH, "r", encoding="utf-8") as f:
            owner = json.load(f)
    except FileNotFoundError:
        log.warning(f"[OWNER] _owner.json not found: {OWNER_JSON_PATH}")
        return False
    except Exception as exc:
        log.warning(f"[OWNER] failed to read _owner.json: {exc}")
        return False

    mobile = str(owner.get("mobile") or "").strip()
    if not mobile:
        log.warning("[OWNER] _owner.json missing mobile; skip owner identity enrichment")
        return False

    open_id = str(owner.get("owner_open_id") or owner.get("open_id") or "").strip()
    if not open_id:
        open_id, err = api.mobile_to_open_id(mobile)
        if err or not open_id:
            log.warning(f"[OWNER] mobile_to_open_id failed for owner mobile: {err}")
            return False

    user_info, err = api.get_user_info(open_id)
    if err or not user_info:
        log.warning(f"[OWNER] get_user_info failed for owner open_id: {err}")
        return False

    owner_name = str(user_info.get("name") or "").strip()
    updated = False
    updates = {
        "owner_open_id": open_id,
        "open_id": open_id,
        "owner_name": owner_name,
        "name": owner_name,
        "display_name": owner_name,
        "avatar_url": str(user_info.get("avatar_url") or "").strip(),
    }
    if user_info.get("mobile"):
        updates["mobile"] = str(user_info.get("mobile") or "").strip()

    for key, value in updates.items():
        if value and owner.get(key) != value:
            owner[key] = value
            updated = True

    if not updated:
        log.info(f"[OWNER] owner identity already present: {owner_name or open_id}")
        return True

    owner["owner_identity_updated_at"] = datetime.now().isoformat()
    owner_dir = os.path.dirname(OWNER_JSON_PATH)
    fd, tmp_path = tempfile.mkstemp(prefix="_owner.", suffix=".json.tmp", dir=owner_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(owner, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, OWNER_JSON_PATH)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    log.info(f"[OWNER] enriched owner identity: name={owner_name or '-'}, open_id={open_id}")
    return True


def _build_instance_id():
    """Globally unique identifier for THIS daemon = hostname:ssh_port.

    Multiple dockers on the same physical host that share host network namespace
    will all return the same hostname (e.g. 'dev4infer'), so we MUST disambiguate
    by SSH port — each docker is reachable on its own SSH port (the user's entry).

    SSH port resolution order:
      1) FEISHU_INSTANCE_SSH_PORT env (extension-injected at spawn)
      2) parse from SSH_CONNECTION env (if daemon was started inside an SSH session)
      3) fallback '22'
    """
    hostname = socket.gethostname()
    ssh_port = os.environ.get("FEISHU_INSTANCE_SSH_PORT")
    if not ssh_port:
        ssh_conn = os.environ.get("SSH_CONNECTION", "")
        if ssh_conn:
            parts = ssh_conn.split()
            if len(parts) >= 4:
                ssh_port = parts[3]
    if not ssh_port:
        ssh_port = "22"
    return f"{hostname}:{ssh_port}"


class RelayClient:
    """WebSocket client connecting to the Relay Server in relay mode.

    Handles: auth, register_interns, heartbeat, receiving feishu_message.
    """

    def __init__(self, relay_url, relay_token, machine_id, registry, ws_server,
                 owner_mobile="", owner_open_id="", ip="", ssh_port=22):
        self.relay_url = relay_url
        self.relay_token = relay_token
        self.machine_id = machine_id
        self.registry = registry
        self.ws_server = ws_server
        self.owner_mobile = owner_mobile
        self.owner_open_id = owner_open_id
        self.ip = ip
        self.ssh_port = ssh_port
        self._loop = None
        self._ws = None
        self._connected = False
        self._conn_lock = threading.Lock()
        self._stop = False
        self._check_online_handler = None
        # task213: peer-send request/response correlation via request_id.
        self._peer_pending = {}            # request_id → {"event": Event, "result": dict}
        self._peer_pending_lock = threading.Lock()
        # Derive relay HTTP base URL from WS URL (ws://host:28081 → http://host:28080)
        import re as _re
        m = _re.match(r'wss?://([^:]+):(\d+)', relay_url)
        if m:
            self._relay_http_base = f"http://{m.group(1)}:{int(m.group(2)) - 1}"
        else:
            self._relay_http_base = None

    @property
    def connected(self):
        with self._conn_lock:
            return self._connected

    def start(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()

    def _run(self):
        import websockets.sync.client as ws_sync
        while not self._stop:
            try:
                self._connect_and_listen(ws_sync)
            except Exception as e:
                self._clear_connection()
                if self._stop:
                    break
                log.warning(f"[RELAY_CLIENT] Connection lost: {e}, reconnecting in 5s...")
                time.sleep(5)

    def _set_connection(self, ws):
        with self._conn_lock:
            self._ws = ws
            self._connected = True

    def _clear_connection(self, ws=None):
        with self._conn_lock:
            if ws is not None and self._ws is not ws:
                return
            self._ws = None
            self._connected = False

    def _current_connection(self):
        with self._conn_lock:
            return self._ws

    def _mark_connection_broken(self, ws, reason):
        if ws is None:
            self._clear_connection()
            return
        with self._conn_lock:
            if self._ws is not ws:
                return
            self._ws = None
            self._connected = False
        log.warning(f"[RELAY_CLIENT] {reason}; closing relay websocket to trigger reconnect")
        try:
            ws.close()
        except Exception as close_err:
            log.warning(f"[RELAY_CLIENT] failed to close broken relay websocket: {close_err}")

    def _connect_and_listen(self, ws_sync):
        log.info(f"[RELAY_CLIENT] Connecting to {self.relay_url}...")
        with ws_sync.connect(self.relay_url, max_size=RELAY_WS_MAX_SIZE_BYTES) as ws:
            # Auth
            with _extension_meta_lock:
                ext_ver = _extension_meta.get("extension_version", "")
                hooks_ver = _extension_meta.get("hooks_version", "")
            workspaces = []
            if _workspace_cache is not None:
                try:
                    workspaces = _workspace_cache.list().get("workspaces", [])
                except Exception as e:
                    log.warning(f"[WORKSPACE] failed to include workspace state in auth: {e}")
            ws.send(json.dumps({
                "type": "auth",
                "token": self.relay_token,
                "machine_id": self.machine_id,
                "owner_mobile": self.owner_mobile,
                "owner_open_id": self.owner_open_id,
                "ip": self.ip,
                "ssh_port": self.ssh_port,
                "script_hash": _script_hash,
                "extension_version": ext_ver,
                "hooks_version": hooks_ver,
                "cli_versions": _static_meta.get("cli_versions", {}),
                "workspaces": workspaces,
                # task228: this daemon supports inbound attachment persistence
                # and pending_attachments state. Relay gates attachment forward
                # on this capability and asks for upgrade when it is absent.
                # task261: "peer" 表示本 daemon 识别 intern_peer_message WS msg_type；
                # capability absence returns target_outdated before relay
                # forward to avoid timeout/misreported relay_unreachable.
                # task283: "detail_mode" 表示本 daemon 支持 detail_mode_get/set RPC；
                # capability absence returns a daemon upgrade error.
                # task373: "no_collapse_mode" 表示本 daemon 支持
                # no_collapse_mode_get/set RPC.
                "capabilities": [
                    "attachments", "peer", "peer_modes", "detail_mode", "no_collapse_mode",
                    "goal_api", "team_contract", "mailbox",
                    "machine_helper"
                ],
            }))
            resp = json.loads(ws.recv(timeout=10))
            if resp.get("type") != "auth_result" or not resp.get("ok"):
                log.error(f"[RELAY_CLIENT] Auth failed: {resp.get('error', 'unknown')}")
                time.sleep(10)
                return

            self._set_connection(ws)
            log.info(f"[RELAY_CLIENT] Authenticated as '{self.machine_id}'")
            sync_daemon_policy_from_relay({
                "relay_url": self.relay_url,
                "relay_http_url": self._relay_http_base,
                "relay_token": self.relay_token,
                "machine_id": self.machine_id,
                "owner_mobile": self.owner_mobile,
                "owner_open_id": self.owner_open_id,
            })

            # Register all local interns
            self._registered_interns = set()  # Reset on reconnect
            self._register_local_interns(ws)

            # Sync current online states to relay server after connect
            _check_multi_daemon_warning()
            threading.Thread(target=_refresh_lights, daemon=True).start()

            # Start heartbeat thread
            hb_stop = threading.Event()
            hb_thread = threading.Thread(target=self._heartbeat_loop, args=(ws, hb_stop), daemon=True)
            hb_thread.start()

            try:
                # Listen for messages from relay server
                while not self._stop:
                    try:
                        raw = ws.recv(timeout=30)
                    except TimeoutError:
                        continue
                    msg = json.loads(raw)
                    self._handle_relay_message(msg)
            finally:
                hb_stop.set()
                self._clear_connection(ws)

    def _register_local_interns(self, ws):
        """Register locally-owned interns with the relay.

        task216 clarification: register == "this machine owns this intern"
        (local dir exists). It is intentionally broader than "online":
        relay routes incoming feishu messages to the machine that owns the
        intern even when the CLI is not running at that instant — the daemon
        will start/resume it on demand. "online" status (sync_online, below)
        is the live CLI check.

          - Claude/Codex interns and machine helpers: registered iff local intern dir exists.
          - Copilot interns: only registered when the VS Code window owns the
            active scoped key (via _ws_server.get_active_intern_keys). Non-active
            Copilot interns are NOT registered because they are not running
            on this machine at all.
        """
        all_interns = _iter_registry_entries(self.registry)
        if not all_interns:
            log.info("[RELAY_CLIENT] No local interns to register")
            return
        active_copilot = _ws_server.get_active_intern_keys() if _ws_server else set()
        interns = []
        skipped = []
        for item in all_interns:
            name = item["name"]
            chat_id = item["chat_id"]
            project = item.get("project") or ""
            intern_dir = _get_intern_dir(name, project=project)
            if not os.path.isdir(intern_dir):
                skipped.append(name)
                continue
            intern_type = _get_intern_type_scoped(name, project=project)
            if _is_tmux_intern_type(intern_type):
                # Register all tmux-based interns (Claude/Codex) that have local dirs
                interns.append({"name": name, "type": intern_type, "chat_id": chat_id, "project": _get_intern_project_scoped(name, project=project)})
            elif _online_key(name, project) in active_copilot:
                # Only register active Copilot interns
                interns.append({"name": name, "type": intern_type, "chat_id": chat_id, "project": _get_intern_project_scoped(name, project=project)})
            else:
                skipped.append(name)
        if skipped:
            log.info(f"[RELAY_CLIENT] Skipped {len(skipped)} non-active interns: {skipped}")
        if interns:
            ws.send(json.dumps({"type": "register_interns", "interns": interns}))
            self._registered_interns.update(_online_key(i["name"], i.get("project") or "") for i in interns)
            log.info(f"[RELAY_CLIENT] Registered {len(interns)} local targets: {[i['name'] for i in interns]}")
        else:
            log.info("[RELAY_CLIENT] No local targets to register")

    def send(self, data):
        """Send a message to the relay server. Thread-safe."""
        msg_type = data.get("type", "unknown") if isinstance(data, dict) else "unknown"
        started = time.time()
        with self._conn_lock:
            ws = self._ws
        if not ws:
            _daemon_metrics.record(f"ws:out:{msg_type}", error=True)
            return
        try:
            ws.send(json.dumps(data))
            _daemon_metrics.record(f"ws:out:{msg_type}", elapsed_ms=int((time.time() - started) * 1000))
        except Exception as e:
            _daemon_metrics.record(
                f"ws:out:{msg_type}",
                elapsed_ms=int((time.time() - started) * 1000),
                error=True,
            )
            self._mark_connection_broken(ws, f"send failed: {e}")

    # Track which interns have been registered with relay in this connection
    _registered_interns = set()

    def _ensure_registered(self, intern_name, project):
        """Ensure an intern is registered with relay. Used when Copilot becomes active after startup."""
        if not project:
            raise ValueError("project required for relay registration")
        key = _online_key(intern_name, project)
        if key in self._registered_interns:
            return
        chat_id = self.registry.find_chat_id(intern_name, project=project)
        if not chat_id:
            return
        intern_type = _get_intern_type_scoped(intern_name, project=project)
        self.send({"type": "register_interns", "interns": [
            {"name": intern_name, "type": intern_type, "chat_id": chat_id, "project": project}
        ]})
        self._registered_interns.add(key)
        log.info(f"[RELAY_CLIENT] Late-registered Copilot '{intern_name}' project={project} with relay")

    def send_intern_online(self, intern_name, project):
        """Notify relay that an intern went online on this machine.
        Includes chat_id and type so relay can auto-register if needed."""
        if not project:
            raise ValueError("project required for intern_online")
        self._ensure_registered(intern_name, project=project)
        chat_id = self.registry.find_chat_id(intern_name, project=project) if self.registry else None
        intern_type = _get_intern_type_scoped(intern_name, project=project)
        self.send({"type": "intern_online", "intern_name": intern_name,
                   "chat_id": chat_id, "intern_type": intern_type,
                   "project": project})

    def send_intern_offline(self, intern_name, project):
        """Notify relay that an intern went offline on this machine."""
        if not project:
            raise ValueError("project required for intern_offline")
        self.send({"type": "intern_offline", "intern_name": intern_name,
                   "project": project})

    def check_online(self, intern_name, project, timeout=5):
        """Ask relay server if intern is online. Uses HTTP API for reliability."""
        if not self._relay_http_base:
            return None
        if not project:
            raise ValueError("project required for check_online")
        try:
            import urllib.request
            from urllib.parse import quote
            if not project:
                raise ValueError("project required for check_online")
            url = (f"{self._relay_http_base}/api/intern/check_online"
                   f"?intern_name={quote(intern_name)}&project={quote(project)}")
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except Exception as e:
            log.warning(f"[RELAY_CLIENT] check_online HTTP failed: {e}")
            return None

    def resolve_peer_target(self, to_intern_name, timeout=5):
        """task213: ask relay for all (project, name) candidates. Returns list or None on timeout/disconnect."""
        if not self._connected:
            return None
        request_id = uuid.uuid4().hex
        event = threading.Event()
        holder = {}
        with self._peer_pending_lock:
            self._peer_pending[request_id] = {"event": event, "result": holder}
        try:
            self.send({
                "type": "peer_resolve_target",
                "request_id": request_id,
                "to_intern_name": to_intern_name,
            })
            if not event.wait(timeout=timeout):
                return None
            return holder.get("candidates", [])
        finally:
            with self._peer_pending_lock:
                self._peer_pending.pop(request_id, None)

    def forward_peer_message(self, payload, timeout=10):
        """task213: send peer message via relay to target machine. Returns {status, reason?, ...}."""
        if not self._connected:
            return {"status": "undeliverable", "reason": "relay_unreachable"}
        request_id = uuid.uuid4().hex
        event = threading.Event()
        holder = {}
        with self._peer_pending_lock:
            self._peer_pending[request_id] = {"event": event, "result": holder}
        try:
            msg = dict(payload)
            msg["type"] = "intern_peer_message"
            msg["request_id"] = request_id
            self.send(msg)
            if not event.wait(timeout=timeout):
                return {"status": "undeliverable", "reason": "relay_unreachable"}
            return {k: v for k, v in holder.items() if k not in ("type", "request_id")}
        finally:
            with self._peer_pending_lock:
                self._peer_pending.pop(request_id, None)

    def forward_goal_command(self, payload, timeout=10):
        """task320: send goal command via relay to target machine. Returns {status, reason?, ...}."""
        if not self._connected:
            return {"status": "undeliverable", "reason": "relay_unreachable"}
        request_id = uuid.uuid4().hex
        event = threading.Event()
        holder = {}
        with self._peer_pending_lock:
            self._peer_pending[request_id] = {"event": event, "result": holder}
        try:
            msg = dict(payload)
            msg["type"] = "intern_goal_command"
            msg["request_id"] = request_id
            self.send(msg)
            if not event.wait(timeout=timeout):
                return {"status": "undeliverable", "reason": "relay_unreachable"}
            return {k: v for k, v in holder.items() if k not in ("type", "request_id")}
        finally:
            with self._peer_pending_lock:
                self._peer_pending.pop(request_id, None)

    def forward_mail_message(self, payload, timeout=10):
        """task309: send mail-to message via relay to target daemon. Returns {status, reason?, ...}."""
        if not self._connected:
            return {"status": "undeliverable", "reason": "relay_unreachable"}
        request_id = uuid.uuid4().hex
        event = threading.Event()
        holder = {}
        with self._peer_pending_lock:
            self._peer_pending[request_id] = {"event": event, "result": holder}
        try:
            msg = dict(payload)
            msg["type"] = "intern_mail_message"
            msg["request_id"] = request_id
            self.send(msg)
            if not event.wait(timeout=timeout):
                return {"status": "undeliverable", "reason": "relay_unreachable"}
            return {k: v for k, v in holder.items() if k not in ("type", "request_id")}
        finally:
            with self._peer_pending_lock:
                self._peer_pending.pop(request_id, None)

    def _heartbeat_loop(self, ws, stop_event):
        while not stop_event.is_set():
            stop_event.wait(timeout=30)
            if stop_event.is_set():
                break
            try:
                _sync_warnings_if_changed(_check_multi_daemon_warning())
                ws.send(json.dumps({"type": "heartbeat"}))
            except Exception as e:
                self._mark_connection_broken(ws, f"heartbeat failed: {e}")
                break

    def _handle_relay_message(self, msg):
        """Handle a message received from relay server."""
        msg_type = msg.get("type")
        _daemon_metrics.record(f"ws:in:{msg_type or 'unknown'}")

        if msg_type == "feishu_message":
            # Route to local intern (same logic as direct feishu message handler)
            intern_name = msg.get("intern_name", "")
            text = msg.get("text", "")
            message_id = msg.get("message_id", "")
            chat_id = msg.get("chat_id", "")
            project = msg.get("project", "")
            attachments = msg.get("attachments") or []

            if not intern_name or (not text and not attachments):
                return

            log.info(f"[RELAY_CLIENT] Feishu msg for '{intern_name}': "
                     f"text={text[:80]!r} atts={len(attachments)}")

            # task228: 附件 → 落盘 + 写 intern state.pending_attachments。
            # 失败（解码/写盘/字段缺失）→ reply_message 明确告诉主管，不再走 text
            # 路径（避免"AI 看到图"的假象，项目规则 6）。
            if attachments:
                try:
                    _persist_inbound_attachments(intern_name, message_id, attachments, project=project)
                except Exception as e:
                    log.error(f"[RELAY_CLIENT] persist attachments failed for {intern_name}: {e}",
                              exc_info=True)
                    if _api:
                        _api.reply_message(message_id, f"⚠️ 附件落盘失败：{e}")
                    return

            # 纯附件无 text：pending_attachments 已累积，等下一条 text 的 UPS hook 消费。
            # 不把空 text 发到 tmux —— 不唤醒 AI，此条消息不产生 AI 轮次。
            # relay 侧已经 reply_message 提示主管"请再发 text 触发 intern 查看"。
            if not text:
                return

            # ── 检查是否有 pending question 等待回答 ──
            if _try_answer_pending_question(intern_name, text, project=project):
                if _api:
                    _api.reply_message(message_id, f"✅ 已收到回复")
                return

            # Check if this is a command (starts with /)
            if text.strip().startswith("/"):
                _handle_feishu_command(intern_name, text.strip(), message_id, project=project)
                return

            # ── Route to intern (Claude/Codex via tmux, Copilot via WS) ──
            intern_type = _get_intern_type_scoped(intern_name, project=project)

            if _is_tmux_intern_type(intern_type):
                _set_pending_supervisor_origin(intern_name, message_id, chat_id, project=project)
                if intern_type == "codex":
                    success, err = _send_to_codex_tmux(intern_name, text, delivery_id=message_id, project=project)
                else:
                    success, err = _send_to_claude_tmux(intern_name, text, delivery_id=message_id, project=project)
                if success:
                    log.info(f"[RELAY_CLIENT] Sent to {intern_type.capitalize()} '{intern_name}' via tmux")
                else:
                    if err in _TMUX_SUBMIT_UNCONFIRMED_ERRORS:
                        if _api and _should_reply_tmux_unconfirmed(err):
                            _api.reply_message(message_id, _format_tmux_unconfirmed_message(intern_name, err))
                        log.warning(f"[RELAY_CLIENT] Codex submit unconfirmed for '{intern_name}': {err}")
                        return
                    _clear_pending_supervisor_origin(intern_name, project=project)
                    if _api:
                        _api.reply_message(message_id, f"⚠️ {intern_name} 当前离线")
                    self.send_intern_offline(intern_name, project=project)
                    _notify_intern_status_changed(intern_name, project=project)
            else:
                _set_pending_supervisor_origin(intern_name, message_id, chat_id, project=project)
                payload = {
                    "type": "feishu_message",
                    "intern_name": intern_name,
                    "text": text,
                    "message_id": message_id,
                    "chat_id": chat_id,
                }
                payload["project"] = project
                delivered = self.ws_server.route_to_active(intern_name, payload, project=project)
                if not delivered:
                    _clear_pending_supervisor_origin(intern_name, project=project)
                    if _api:
                        _api.reply_message(message_id, f"⚠️ {intern_name} 当前不在线")
                    self.send_intern_offline(intern_name, project=project)
                    _notify_intern_status_changed(intern_name, project=project)
                    log.info(f"[RELAY_CLIENT] '{intern_name}' not active in any window, sent offline")

        elif msg_type == "helper_action":
            request_id = msg.get("request_id", "")
            try:
                result = handle_machine_helper_action(msg)
                result.update({"type": "helper_action_result", "ok": True})
            except Exception as e:
                log.error(f"[HELPER] action failed: {e}", exc_info=True)
                result = {
                    "type": "helper_action_result",
                    "ok": False,
                    "request_id": request_id,
                    "machine_id": msg.get("machine_id", ""),
                    "helper_action": msg.get("helper_action", ""),
                    "error": str(e),
                }
                result.update(_machine_helper_runtime_error_payload(e))
            self.send(result)

        elif msg_type == "daemon_policy_sync":
            try:
                apply_daemon_policy_sync(msg)
            except Exception as e:
                log.error(f"[POLICY] daemon policy sync failed: {e}", exc_info=True)
                _update_machine_config_card(
                    msg.get("card_message_id") or "",
                    "机器配置失败",
                    "red",
                    [
                        f"operation_id: `{msg.get('operation_id') or ''}`",
                        f"machine_id: `{msg.get('machine_id') or ''}`",
                        f"error: `{str(e)}`",
                    ],
                )

        elif msg_type == "heartbeat_ack":
            machine_known = msg.get("machine_known", True)
            if machine_known is False:
                changed = _set_daemon_warning(
                    "machine_unknown_on_relay",
                    f"relay 未识别当前 machine_id={self.machine_id} 的 ws 连接",
                )
                _sync_warnings_if_changed(changed)
                log.warning("[WARN] machine_unknown_on_relay: relay 未识别当前 ws，关闭连接触发重连")
                self._mark_connection_broken(self._current_connection(), "relay no longer knows this machine")
            else:
                _sync_warnings_if_changed(_clear_daemon_warning("machine_unknown_on_relay"))

        elif msg_type == "card_callback":
            # Card interaction from relay → answer pending question
            intern_name = msg.get("intern_name", "")
            project = msg.get("project", "")
            if not intern_name or not project:
                log.warning(
                    f"[RELAY_CLIENT] Dropping card callback with missing scope "
                    f"intern={intern_name or '-'} project={project or '-'} "
                    f"question_id={msg.get('question_id', '') or '-'}"
                )
                return
            question_id = msg.get("question_id", "")

            with _pq_lock:
                entry = _get_pending_question_locked(intern_name, project, question_id)
            if entry and entry["answer"] is not None:
                log.info(
                    f"[RELAY_CLIENT] Duplicate card callback ignored for answered question "
                    f"'{_online_key(intern_name, project)}' question_id={question_id or '-'}"
                )
                return
            if not entry:
                invalid_reason = "stale_or_unknown_question_id"
                with _pq_lock:
                    stored_record = _question_entry_from_store_locked(project, intern_name, question_id)
                    if stored_record:
                        invalid_reason = stored_record.get("invalid_reason") or stored_record.get("status") or invalid_reason
                log.warning(
                    f"[RELAY_CLIENT] No pending question for '{_online_key(intern_name, project)}' "
                    f"(card callback question_id={question_id or '-'}, reason={invalid_reason})"
                )
                if stored_record:
                    _update_question_card_to_invalid(
                        intern_name,
                        invalid_reason,
                        project=project,
                        question_id=question_id,
                    )
                else:
                    chat_id = _registry.find_chat_id(intern_name, project=project) if _registry else None
                    if chat_id and _api:
                        _api.send_message(
                            chat_id,
                            f"⚠️ 收到旧问题卡片回答，但 daemon 找不到 question_id={question_id or '-'} 的状态；"
                            f"{_question_invalid_message(invalid_reason)}，请让 intern 重新发起问题。",
                        )
                return

            questions = entry["questions"]

            if msg.get("is_form"):
                # Form submission (free text or multi-question form)
                form_value = msg.get("form_value", {})
                question_keys = msg.get("question_keys", [])
                answers = {}
                for i, qk in enumerate(question_keys):
                    # multiSelect 优先：有勾选则直接用 list（保留 list 类型给 Claude）
                    multi = form_value.get(f"q_{i}_multiselect")
                    if isinstance(multi, list) and multi:
                        answers[qk] = multi
                        continue
                    # 自由文本 > 单选下拉
                    custom = form_value.get(f"q_{i}_input", "")
                    selected = form_value.get(f"q_{i}_select", "")
                    val = (custom.strip() if custom else "") or selected or ""
                    if val:
                        answers[qk] = val

                if not answers:
                    log.warning(f"[RELAY_CLIENT] Empty form answers for '{intern_name}'")
                    return

                log.info(
                    f"[RELAY_CLIENT] Card form answers for '{intern_name}' "
                    f"question_id={entry.get('question_id', '-')}: {answers}"
                )
            else:
                # Button click (single answer)
                answer = msg.get("answer", "")
                if not answer:
                    return

                log.info(
                    f"[RELAY_CLIENT] Card callback for '{intern_name}' "
                    f"question_id={entry.get('question_id', '-')}: {answer[:80]}"
                )

                if len(questions) == 1:
                    question_key = questions[0].get("question", questions[0].get("header", "Q1"))
                    answers = {question_key: answer}
                else:
                    # Shouldn't happen for multi-q (uses form), but handle gracefully
                    answers = {}
                    for q in questions:
                        qk = q.get("question", q.get("header", "Q"))
                        answers[qk] = answer

            with _pq_lock:
                entry = _get_pending_question_locked(intern_name, project, question_id)
                if entry:
                    entry["answer"] = answers
                    entry["updated_at"] = time.time()
                    _upsert_question_entry_locked(entry)
                    question_id = entry.get("question_id", question_id)
                else:
                    log.warning(
                        f"[RELAY_CLIENT] Pending question disappeared before answer set "
                        f"for '{intern_name}' question_id={question_id or '-'}"
                    )
                    return
            log.info(
                f"[RELAY_CLIENT] Card answer set for '{_online_key(intern_name, project)}' "
                f"question_id={question_id or '-'}: {answers}"
            )
            _update_question_card(intern_name, answers, "飞书卡片", project=project, question_id=question_id)
            with _pq_lock:
                entry = _get_pending_question_locked(intern_name, project, question_id)
                if entry:
                    entry["event"].set()
            return

        elif msg_type == "heartbeat_ack":
            pass  # Expected response to heartbeat

        elif msg_type == "check_online_result":
            if self._check_online_handler:
                self._check_online_handler(msg)

        elif msg_type == "intern_online_rejected":
            intern_name = msg.get("intern_name", "")
            existing_machine = msg.get("machine_id", "")
            log.warning(f"[RELAY_CLIENT] Online rejected for '{intern_name}': already on '{existing_machine}'")

        elif msg_type == "peer_resolve_target_result":
            # task213: relay replied with candidates for to_intern_name
            request_id = msg.get("request_id", "")
            with self._peer_pending_lock:
                entry = self._peer_pending.get(request_id)
            if entry:
                entry["result"]["candidates"] = msg.get("candidates", [])
                entry["event"].set()

        elif msg_type == "intern_peer_message":
            # task213: relay forwarded a peer message destined for one of our local interns.
            request_id = msg.get("request_id", "")
            sender_mid = msg.get("sender_machine_id", "")
            if (msg.get("mode") or "default") not in _PEER_DELIVERY_MODES:
                result = {"status": "undeliverable", "reason": "unsupported_mode"}
            else:
                result = _deliver_peer_locally(msg)
            reply = {
                "type": "intern_peer_message_result",
                "request_id": request_id,
                "sender_machine_id": sender_mid,
            }
            reply.update(result)
            self.send(reply)

        elif msg_type == "intern_goal_command":
            # task320: relay forwarded a goal set/cancel command to one local tmux intern.
            request_id = msg.get("request_id", "")
            sender_mid = msg.get("sender_machine_id", "")
            msg["via_relay"] = True
            result = _deliver_goal_locally(msg)
            reply = {
                "type": "intern_goal_command_result",
                "request_id": request_id,
                "sender_machine_id": sender_mid,
            }
            reply.update(result)
            self.send(reply)

        elif msg_type == "intern_peer_message_result":
            # task213: B daemon's reply came back via relay
            request_id = msg.get("request_id", "")
            with self._peer_pending_lock:
                entry = self._peer_pending.get(request_id)
            if entry:
                entry["result"].update({k: v for k, v in msg.items() if k != "type"})
                entry["event"].set()

        elif msg_type == "intern_goal_command_result":
            # task320: B daemon's goal delivery receipt came back via relay.
            request_id = msg.get("request_id", "")
            with self._peer_pending_lock:
                entry = self._peer_pending.get(request_id)
            if entry:
                entry["result"].update({k: v for k, v in msg.items() if k != "type"})
                entry["event"].set()

        elif msg_type == "intern_mail_message":
            # task309: relay forwarded a mail-to message for one local intern mailbox.
            request_id = msg.get("request_id", "")
            sender_mid = msg.get("sender_machine_id", "")
            result = _deliver_mail_locally(msg)
            reply = {
                "type": "intern_mail_message_result",
                "request_id": request_id,
                "sender_machine_id": sender_mid,
            }
            reply.update(result)
            self.send(reply)

        elif msg_type == "intern_mail_message_result":
            # task309: target daemon's mailbox write receipt came back via relay.
            request_id = msg.get("request_id", "")
            with self._peer_pending_lock:
                entry = self._peer_pending.get(request_id)
            if entry:
                entry["result"].update({k: v for k, v in msg.items() if k != "type"})
                entry["event"].set()

        elif msg_type == "detail_mode_get":
            # task283: relay asks this daemon for the current per-chat
            # detail_mode value. Truth source is daemon-local since the hook
            # filtering also runs on this machine — see daemon_chat_config.
            request_id = msg.get("request_id", "")
            chat_id = msg.get("chat_id", "")
            reply = {"type": "detail_mode_get_result", "request_id": request_id,
                     "chat_id": chat_id}
            try:
                reply["mode"] = daemon_chat_config.get_detail_mode(chat_id)
            except Exception as e:
                log.error(f"[DETAIL] detail_mode_get for chat={chat_id} failed: {e}", exc_info=True)
                reply["error"] = f"daemon_local_read_failed: {e}"
            self.send(reply)

        elif msg_type == "detail_mode_set":
            # task283: relay asks this daemon to write the per-chat detail_mode
            # value. ValueError (bad mode / empty chat_id) is reported as a
            # structured error string — relay surfaces it to the supervisor.
            request_id = msg.get("request_id", "")
            chat_id = msg.get("chat_id", "")
            mode = msg.get("mode", "")
            reply = {"type": "detail_mode_set_result", "request_id": request_id,
                     "chat_id": chat_id, "mode": mode}
            try:
                reply["changed"] = daemon_chat_config.set_detail_mode(chat_id, mode)
            except ValueError as e:
                # Bad input — caller-side error, log at INFO not ERROR.
                log.info(f"[DETAIL] detail_mode_set rejected: {e}")
                reply["error"] = f"invalid_argument: {e}"
            except Exception as e:
                log.error(f"[DETAIL] detail_mode_set for chat={chat_id} failed: {e}", exc_info=True)
                reply["error"] = f"daemon_local_write_failed: {e}"
            self.send(reply)

        elif msg_type == "no_collapse_mode_get":
            request_id = msg.get("request_id", "")
            chat_id = msg.get("chat_id", "")
            reply = {"type": "no_collapse_mode_get_result", "request_id": request_id,
                     "chat_id": chat_id}
            try:
                reply["mode"] = daemon_chat_config.get_no_collapse_mode(chat_id)
            except Exception as e:
                log.error(f"[NO_COLLAPSE] get for chat={chat_id} failed: {e}", exc_info=True)
                reply["error"] = f"daemon_local_read_failed: {e}"
            self.send(reply)

        elif msg_type == "no_collapse_mode_set":
            request_id = msg.get("request_id", "")
            chat_id = msg.get("chat_id", "")
            mode = msg.get("mode", "")
            reply = {"type": "no_collapse_mode_set_result", "request_id": request_id,
                     "chat_id": chat_id, "mode": mode}
            try:
                reply["changed"] = daemon_chat_config.set_no_collapse_mode(chat_id, mode)
            except ValueError as e:
                log.info(f"[NO_COLLAPSE] set rejected: {e}")
                reply["error"] = f"invalid_argument: {e}"
            except Exception as e:
                log.error(f"[NO_COLLAPSE] set for chat={chat_id} failed: {e}", exc_info=True)
                reply["error"] = f"daemon_local_write_failed: {e}"
            self.send(reply)

        elif msg_type == "request_logs":
            request_id = msg.get("request_id", "")
            intern_name = msg.get("intern_name")
            relay_upload_url = msg.get("relay_upload_url", "")
            if request_id and relay_upload_url:
                threading.Thread(
                    target=self._upload_logs,
                    args=(request_id, relay_upload_url, intern_name),
                    daemon=True,
                ).start()

    def _upload_logs(self, request_id, relay_upload_url, intern_name=None):
        """Tar logs and upload to relay via HTTP POST."""
        import tarfile
        import tempfile

        log_dir = str(log_root(WORK_AGENTS_ROOT))
        if not os.path.isdir(log_dir):
            log.warning(f"[LOG_UPLOAD] Log directory not found: {log_dir}")
            return

        # If intern_name specified, only tar that intern's subdirectory
        if intern_name:
            matches = sorted(Path(log_dir).glob(f"versions/*/projects/*/interns/{intern_name}"))
            if not matches:
                log.warning(f"[LOG_UPLOAD] Intern log dir not found for {intern_name} under {log_dir}")
                return
            target = str(matches[-1])
            arcname_base = intern_name
        else:
            target = log_dir
            arcname_base = "llm_intern_logs"

        try:
            with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp:
                tmp_path = tmp.name
            with tarfile.open(tmp_path, "w:gz") as tar:
                tar.add(target, arcname=arcname_base)

            # Upload via HTTP POST
            file_size = os.path.getsize(tmp_path)
            log.info(f"[LOG_UPLOAD] Uploading {file_size} bytes to relay (request_id={request_id})")
            with open(tmp_path, "rb") as f:
                req = urllib.request.Request(
                    relay_upload_url,
                    data=f.read(),
                    method="POST",
                    headers={"Content-Type": "application/gzip", "Content-Length": str(file_size)},
                )
                resp = urllib.request.urlopen(req, timeout=120)
                result = json.loads(resp.read())
                log.info(f"[LOG_UPLOAD] Upload complete: {result}")
        except Exception as e:
            log.error(f"[LOG_UPLOAD] Failed (request_id={request_id}): {e}")
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def stop(self):
        self._stop = True


# Global relay client reference (set in main if relay mode)
_relay_client = None


# ══════════════════════════════════════════
# Feishu Command Handling
# ══════════════════════════════════════════

# Commands supported per intern type. Shared with relay so mapped native slash
# commands allowed through Feishu also execute in the daemon instead of being
# rejected after routing.
_COMMANDS = NATIVE_SLASH_COMMANDS_BY_INTERN_TYPE
_TMUX_NATIVE_SLASH_COMMANDS = frozenset(("/clear", "/compact", "/help", "/cost", "/model"))

_TMUX_SCREENSHOT_MAX_REPLY_CHARS = 12000


def _handle_feishu_command(intern_name, command, message_id, project=None):
    """Handle a /command from Feishu. Returns True if handled."""
    started = time.time()
    intern_type = _get_intern_type_scoped(intern_name, project=project)
    # Extract first word as command (e.g. "/clear foo" → "/clear")
    cmd = command.strip().split()[0].lower()
    try:
        supported = _COMMANDS.get(intern_type, {})
        if cmd not in supported:
            cmd_list = format_available_slash_commands(intern_type, supported.items())
            reply = f"❌ 不支持的指令: {cmd}\n\n可用指令:\n{cmd_list}"
            if _api:
                _api.reply_message(message_id, reply)
            return True

        if cmd == "/screenshot":
            return _exec_screenshot_command(intern_name, message_id, project=project)

        if intern_type == "claude":
            if project:
                return _exec_claude_command(intern_name, cmd, message_id, command, project=project)
            return _exec_claude_command(intern_name, cmd, message_id, command)
        if intern_type == "codex":
            if project:
                return _exec_codex_command(intern_name, cmd, message_id, command, project=project)
            return _exec_codex_command(intern_name, cmd, message_id, command)
        return True
    finally:
        _daemon_metrics.record(
            f"slash:{intern_type}:{cmd}",
            elapsed_ms=int((time.time() - started) * 1000),
        )


def _capture_tmux_visible_text(intern_name, project=None):
    """Capture the current visible pane text for an intern tmux session."""
    result = subprocess.run(
        ["tmux", "capture-pane", "-p", "-J", "-t", _tmux_target(intern_name, project=project)],
        check=True,
        capture_output=True,
        text=True,
    )
    snapshot = (result.stdout or "").rstrip()
    return snapshot if snapshot.strip() else "(tmux pane is empty)"


def _format_tmux_screenshot_reply(intern_name, project, snapshot):
    scope = f"{intern_name} / {project}" if project else intern_name
    line_count = len(snapshot.splitlines())
    body = snapshot
    truncated = False
    omitted = 0
    if len(body) > _TMUX_SCREENSHOT_MAX_REPLY_CHARS:
        omitted = len(body) - _TMUX_SCREENSHOT_MAX_REPLY_CHARS
        body = body[-_TMUX_SCREENSHOT_MAX_REPLY_CHARS:]
        truncated = True

    header = f"tmux 当前屏幕文本快照：{scope}\nlines={line_count}"
    if truncated:
        header += f"，已保留末尾 {_TMUX_SCREENSHOT_MAX_REPLY_CHARS} 字符，省略前部 {omitted} 字符"
    return f"{header}\n```\n{body}\n```"


def _format_tmux_screenshot_text_fallback(intern_name, project, snapshot, reason):
    return (
        f"⚠️ 图片快照发送失败：{reason}\n\n"
        + _format_tmux_screenshot_reply(intern_name, project, snapshot)
    )


def _send_native_slash_to_tmux(target, command_text):
    subprocess.run(
        ["tmux", "send-keys", "-t", target, "-l", "--", command_text],
        check=True, capture_output=True
    )
    time.sleep(0.5)
    subprocess.run(
        ["tmux", "send-keys", "-t", target, "Enter"],
        check=True, capture_output=True
    )


def _short_screenshot_error(error):
    return str(error or "").replace("`", "'")[:220]


def _screenshot_filename(intern_name, project):
    scope = f"{project}_{intern_name}" if project else intern_name
    safe_scope = re.sub(r"[^A-Za-z0-9_.-]+", "_", scope).strip("._") or "intern"
    return f"tmux_screenshot_{safe_scope}.png"


def _exec_screenshot_command(intern_name, message_id, project=None):
    """Reply with the current tmux pane screenshot image for debugging."""
    if not _check_tmux_session(intern_name, project=project):
        if _api:
            _api.reply_message(message_id, f"⚠️ {intern_name} tmux 会话不存在")
        return True

    try:
        snapshot = _capture_tmux_visible_text(intern_name, project=project)
        try:
            png_bytes = render_tmux_screenshot_png(
                intern_name=intern_name,
                project=project,
                snapshot=snapshot,
            )
        except Exception as e:
            reason = f"渲染失败: {_short_screenshot_error(e)}"
            log.error(f"[CMD] Failed to render /screenshot image for '{intern_name}': {e}", exc_info=True)
            if _api:
                err = _api.reply_message(
                    message_id,
                    _format_tmux_screenshot_text_fallback(
                        intern_name, project, snapshot, reason))
                if err:
                    log.error(f"[CMD] Failed to reply /screenshot text fallback for '{intern_name}': {err}")
            return True

        if _api:
            filename = _screenshot_filename(intern_name, project)
            image_key, err = _api.upload_image_bytes(png_bytes, filename=filename)
            if err:
                reason = f"上传失败: {_short_screenshot_error(err)}"
                log.error(f"[CMD] Failed to upload /screenshot image for '{intern_name}': {err}")
                fallback_err = _api.reply_message(
                    message_id,
                    _format_tmux_screenshot_text_fallback(
                        intern_name, project, snapshot, reason))
                if fallback_err:
                    log.error(f"[CMD] Failed to reply /screenshot upload fallback for '{intern_name}': {fallback_err}")
                return True
            err = _api.reply_image(message_id, image_key)
            if err:
                reason = f"图片回复失败: {_short_screenshot_error(err)}"
                log.error(f"[CMD] Failed to reply /screenshot image for '{intern_name}': {err}")
                fallback_err = _api.reply_message(
                    message_id,
                    _format_tmux_screenshot_text_fallback(
                        intern_name, project, snapshot, reason))
                if fallback_err:
                    log.error(f"[CMD] Failed to reply /screenshot image fallback for '{intern_name}': {fallback_err}")
                return True
        log.info(f"[CMD] Captured tmux screenshot image for '{intern_name}' "
                 f"({len(snapshot)} chars, {len(png_bytes)} bytes)")
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        log.error(f"[CMD] Failed to capture /screenshot for '{intern_name}': {e}")
        if _api:
            _api.reply_message(message_id, f"❌ 获取 {intern_name} tmux 快照失败: {e}")
    return True


def _exec_codex_command(intern_name, cmd, message_id, raw_command=None, project=None):
    """Execute a command for Codex intern via tmux."""
    command_text = (raw_command or cmd).strip()
    goal_content = command_text[len("/goal"):].strip() if cmd == "/goal" and command_text.lower().startswith("/goal") else ""
    goal_action = goal_content.lower()
    if cmd == "/goal" and goal_action == "status":
        if _api:
            _api.reply_message(message_id, _format_codex_goal_status_reply(intern_name, project=project))
        return True

    if cmd != "/goal":
        if not _check_tmux_session(intern_name, project=project):
            if _api:
                _api.reply_message(message_id, f"⚠️ {intern_name} tmux 会话不存在")
            return True
        if not _is_codex_process_running(intern_name, project=project):
            if _api:
                _api.reply_message(message_id, f"⚠️ {intern_name} Codex 进程未运行")
            return True

    try:
        if cmd in _TMUX_NATIVE_SLASH_COMMANDS:
            target = _tmux_target(intern_name, project=project)
            _send_native_slash_to_tmux(target, command_text)
            log.info(f"[CMD] Sent {command_text} to Codex '{intern_name}'")
            if _api:
                _api.reply_message(message_id, f"✅ 已向 {intern_name} 发送 {cmd}")

        elif cmd == "/goal":
            content = goal_content
            if not content:
                if _api:
                    _api.reply_message(
                        message_id,
                        "❌ /goal 需要目标内容，或使用 `/goal clear`、`/goal status`、`/goal resume`",
                    )
                return True

            reply_action = "/goal"
            replacing_existing_goal = False
            if goal_action == "clear":
                success, err = _send_goal_cancel_to_codex_tmux(intern_name, message_id, project=project)
                reply_action = "/goal clear"
            elif goal_action == "resume":
                _, current_goal = _latest_codex_goal_state(intern_name, project=project)
                current_status = str((current_goal or {}).get("status") or "").lower()
                if current_status not in {"blocked", "paused"}:
                    if _api:
                        label = current_status or "unknown"
                        _api.reply_message(message_id, f"ℹ️ 当前 goal 状态为 {label}，无需 /goal resume")
                    return True
                success, err = _send_goal_resume_to_codex_tmux(intern_name, message_id, project=project)
                reply_action = "/goal resume"
            else:
                current_goal = _current_codex_goal_state_for_replacement(intern_name, project=project)
                replacing_existing_goal = _goal_state_exists_for_replacement(current_goal)
                success, err = _send_peer_goal_to_codex_tmux(
                    intern_name,
                    content,
                    message_id,
                    project=project or "",
                    ask_feishu=bool(replacing_existing_goal and project),
                )

            if success:
                log.info(f"[CMD] Sent {cmd} to Codex '{intern_name}'")
                if _api:
                    if err == _CODEX_GOAL_REPLACE_PENDING:
                        _api.reply_message(
                            message_id,
                            (
                                f"⏳ 已捕捉到 {intern_name} 的 Replace goal 确认，"
                                "已发送飞书卡片等待选择。"
                            ),
                        )
                    else:
                        suffix = "，并替换旧 goal" if replacing_existing_goal else ""
                        _api.reply_message(message_id, f"✅ 已向 {intern_name} 发送 {reply_action}{suffix}")
            else:
                log.warning(f"[CMD] Failed to send {cmd} to '{intern_name}': {err}")
                if _api:
                    _api.reply_message(message_id, f"❌ 发送 {reply_action} 失败: {err}")

        elif cmd == "/stop":
            target = _tmux_target(intern_name, project=project)
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Escape"],
                check=True, capture_output=True
            )
            log.info(f"[CMD] Sent Escape (stop) to Codex '{intern_name}'")
            ok, reason = _finalize_active_feishu_message_for_stop(intern_name, project=project)
            if ok:
                log.info(f"[CMD] Finalized active Feishu turn for Codex '{intern_name}' after stop ({reason})")
                _notify_intern_status_changed(intern_name, project=project)
                _push_interns_state_once()
            else:
                log.info(f"[CMD] No Codex Feishu turn finalized for '{intern_name}' after stop: {reason}")
            if _api:
                _api.reply_message(message_id, f"✅ 已向 {intern_name} 发送停止信号")

    except subprocess.CalledProcessError as e:
        log.error(f"[CMD] Failed to send {cmd} to '{intern_name}': {e}")
        if _api:
            _api.reply_message(message_id, f"❌ 发送 {cmd} 失败: {e}")
    return True


def _exec_claude_command(intern_name, cmd, message_id, raw_command=None, project=None):
    """Execute a command for Claude intern via tmux."""
    command_text = (raw_command or cmd).strip()
    if not _check_tmux_session(intern_name, project=project):
        if _api:
            _api.reply_message(message_id, f"⚠️ {intern_name} tmux 会话不存在")
        return True
    if not _is_claude_process_running(intern_name, project=project):
        if _api:
            _api.reply_message(message_id, f"⚠️ {intern_name} Claude 进程未运行")
        return True
    target = _tmux_target(intern_name, project=project)

    try:
        if cmd in _TMUX_NATIVE_SLASH_COMMANDS:
            _send_native_slash_to_tmux(target, command_text)
            log.info(f"[CMD] Sent {command_text} to Claude '{intern_name}'")
            if _api:
                _api.reply_message(message_id, f"✅ 已向 {intern_name} 发送 {cmd}")

        elif cmd == "/stop":
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Escape"],
                check=True, capture_output=True
            )
            log.info(f"[CMD] Sent Escape (stop) to Claude '{intern_name}'")
            if _api:
                _api.reply_message(message_id, f"✅ 已向 {intern_name} 发送停止信号")

        elif cmd == "/btw":
            # 提取 /btw 之后的全文作为问题（保留中英文/换行/多空格）
            raw = (raw_command or "").strip()
            question = raw[len("/btw"):].strip() if raw.lower().startswith("/btw") else ""
            if not question:
                if _api:
                    _api.reply_message(message_id, "❌ /btw 需要问题文本，用法：/btw <问题>")
                return True
            line = f"/btw {question}"
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "-l", "--", line],
                check=True, capture_output=True
            )
            time.sleep(0.5)
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Enter"],
                check=True, capture_output=True
            )
            log.info(f"[CMD] Sent /btw to Claude '{intern_name}': {question[:80]}")
            if _api:
                _api.reply_message(message_id, f"✅ 已发送 /btw 到 {intern_name}，答案稍后回传")

    except subprocess.CalledProcessError as e:
        log.error(f"[CMD] Failed to send {cmd} to '{intern_name}': {e}")
        if _api:
            _api.reply_message(message_id, f"❌ 发送 {cmd} 失败: {e}")
    return True


def _get_intern_session_entry(intern_name, project=None):
    """Return a .intern_sessions.json entry for intern/project.

    Project-less lookup may resolve only a unique scoped enterprise entry.
    Machine helpers keep their explicit machine-global key.
    """
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}

    def entry_name(key, candidate):
        return str(candidate.get("intern_name") or str(key).split(":", 1)[-1])

    def entry_scopes(key, candidate):
        scopes = {
            str(candidate.get("project") or ""),
            str(candidate.get("workspace_id") or ""),
        }
        if ":" in str(key):
            scopes.add(str(key).split(":", 1)[0])
        return {scope for scope in scopes if scope}

    def entry_usable(candidate):
        intern_dir = str(candidate.get("intern_dir") or "")
        return bool(intern_dir and os.path.isdir(intern_dir))

    def mark_unusable_enterprise(candidate):
        result = dict(candidate)
        result["_unusable_enterprise"] = True
        return result

    entry = data.get(f"{project}:{intern_name}") if project else None
    if not project:
        machine_entry = data.get(intern_name)
        if isinstance(machine_entry, dict) and machine_entry.get("role") in ("machine_helper", "helper"):
            entry = machine_entry
    if isinstance(entry, dict) and entry:
        if entry_usable(entry):
            return entry
        if entry.get("workspace_id"):
            return mark_unusable_enterprise(entry)
    matches = []
    unusable_enterprise_matches = []
    for key, candidate in data.items():
        if not isinstance(candidate, dict):
            continue
        if not candidate.get("workspace_id") and candidate.get("role") not in ("machine_helper", "helper"):
            continue
        if entry_name(key, candidate) != intern_name and key != intern_name and not str(key).endswith(f":{intern_name}"):
            continue
        if project and project not in entry_scopes(key, candidate):
            continue
        if not entry_usable(candidate):
            if candidate.get("workspace_id"):
                unusable_enterprise_matches.append(candidate)
            continue
        matches.append(candidate)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        log.warning(f"[SESSION] ambiguous session lookup for intern={intern_name}; project required")
        return {"_ambiguous": True}
    if len(unusable_enterprise_matches) == 1:
        return mark_unusable_enterprise(unusable_enterprise_matches[0])
    if len(unusable_enterprise_matches) > 1:
        log.warning(f"[SESSION] ambiguous unusable enterprise session lookup for intern={intern_name}; project required")
        return {"_ambiguous": True}
    return {}


def _canonical_intern_scope(intern_name, project="", workspace_id=""):
    lookup_scope = project or workspace_id or ""
    entry = _get_intern_session_entry(intern_name, project=lookup_scope) if lookup_scope else _get_intern_session_entry(intern_name)
    if isinstance(entry, dict) and not entry.get("_ambiguous"):
        return (
            str(entry.get("project") or project or ""),
            str(entry.get("workspace_id") or workspace_id or ""),
        )
    return project or "", workspace_id or ""


def _get_intern_dir(intern_name, project=None):
    """Resolve intern runtime dir from the session registry.

    Enterprise entries must carry an explicit intern_dir.
    """
    entry = _get_intern_session_entry(intern_name, project=project)
    if isinstance(entry, dict) and entry.get("_ambiguous"):
        return ""
    if isinstance(entry, dict) and entry.get("_unusable_enterprise"):
        return ""
    intern_dir = entry.get("intern_dir") if isinstance(entry, dict) else ""
    if intern_dir:
        return intern_dir
    return ""


def _get_status_md_path(intern_name, project=None):
    project = project or _get_intern_project(intern_name) or ""
    entry = _get_intern_session_entry(intern_name, project=project)
    if isinstance(entry, dict) and entry.get("_ambiguous"):
        return ""
    enterprise_entry = isinstance(entry, dict) and bool(entry.get("workspace_id"))
    intern_dir = entry.get("intern_dir") if isinstance(entry, dict) else ""
    if intern_dir:
        state_file = os.path.join(intern_dir, ".hook_state.json")
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            state = {}
        resolver = state.get("metadata_resolver") if isinstance(state.get("metadata_resolver"), dict) else {}
        status_path = resolver.get("status_path") or ""
        if status_path:
            return status_path
        if enterprise_entry:
            return ""
    if enterprise_entry:
        return ""
    try:
        status_md = os.path.join(team_mailbox.team_registry.interns_dir(project), intern_name, "status.md")
        if os.path.isfile(status_md):
            return status_md
    except Exception:
        pass
    return ""


def _get_intern_type(intern_name, project=None):
    """Read intern type from .intern_sessions.json. Returns 'copilot' / 'claude' / 'codex'."""
    entry = _get_intern_session_entry(intern_name, project=project)
    intern_type = entry.get("type") if isinstance(entry, dict) else ""
    if intern_type in ("copilot", "claude", "codex"):
        return intern_type
    return "copilot"


def _get_intern_type_scoped(intern_name, project=None):
    return _get_intern_type(intern_name, project=project)


def _resolve_tmux_session_name(intern_name, project=None):
    entry = _get_intern_session_entry(intern_name, project=project)
    if isinstance(entry, dict) and not entry.get("_ambiguous"):
        explicit = str(entry.get("tmux_session") or "")
        if explicit:
            return explicit
        workspace_id = str(entry.get("workspace_id") or "")
        intern_dir = str(entry.get("intern_dir") or "")
        if workspace_id and intern_dir:
            return scoped_tmux_session_name(
                intern_name,
                project=str(entry.get("project") or project or ""),
                workspace_id=workspace_id,
                intern_dir=intern_dir,
            )
    return ""


def _tmux_target(intern_name, project=None):
    session_name = _resolve_tmux_session_name(intern_name, project=project)
    if not session_name:
        raise RuntimeError(f"tmux session unresolved for {project or '-'}:{intern_name}")
    return f"={session_name}:"


def _check_tmux_session(intern_name, project=None):
    """Check if a tmux session exists for the given intern. Returns True/False."""
    session_name = _resolve_tmux_session_name(intern_name, project=project)
    if not session_name:
        return False
    try:
        subprocess.run(
            ["tmux", "has-session", "-t", f"={session_name}"],
            check=True, capture_output=True
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def _is_claude_process_running(intern_name, project=None):
    """Check if Claude CLI is actually running in the tmux pane (not just bash).

    Returns True if the pane runs 'claude'. Also accepts 'copilot' so that
    Copilot-CLI interns registered under the claude tmux path are detected as
    live (their pane runs `node .../copilot`, matched via child-arg scan).
    """
    return (_is_tmux_cli_process_running(intern_name, "claude", project=project)
            or _is_tmux_cli_process_running(intern_name, "copilot", project=project))


def _is_codex_process_running(intern_name, project=None):
    """Check if Codex CLI is actually running in the tmux pane.

    Codex CLI is a node script (#!/usr/bin/env node). Tmux's pane_current_command
    reports `node` (the foreground process), and pane_pid is the bash shell —
    its direct child is the node process whose cmdline contains "codex".
    Strategy: enumerate children of pane_pid via `ps --ppid` and grep for "codex".
    """
    session_name = _resolve_tmux_session_name(intern_name, project=project)
    if not _check_tmux_session(intern_name, project=project):
        return False
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-t", f"={session_name}", "-F", "#{pane_pid}"],
            capture_output=True, text=True
        )
        pane_pid = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
        if not pane_pid:
            return False
        for _pid, args in _list_child_processes(pane_pid):
            if "codex" in args.lower():
                return True
        return False
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        return False


def _is_tmux_cli_process_running(intern_name, expected_cmd, project=None):
    """Generic helper: check if tmux pane is running the expected CLI command."""
    session_name = _resolve_tmux_session_name(intern_name, project=project)
    if not _check_tmux_session(intern_name, project=project):
        return False
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-t", f"={session_name}", "-F", "#{pane_current_command}"],
            capture_output=True, text=True
        )
        cmd = result.stdout.strip().splitlines()[0].lower() if result.stdout.strip() else ""
        if not cmd:
            return False
        # Match "claude", "claude.exe" (node wrapper basename on some installs),
        # etc. Exact equality broke liveness when pane_current_command was
        # "claude.exe", making the intern show offline while Claude was running.
        expected = expected_cmd.lower()
        if cmd == expected or cmd.startswith(expected + "."):
            return True
        # Fall back to scanning pane child processes (e.g. when the foreground
        # command reports as "node" but a child cmdline contains the CLI name).
        pane = subprocess.run(
            ["tmux", "list-panes", "-t", f"={session_name}", "-F", "#{pane_pid}"],
            capture_output=True, text=True
        )
        pane_pid = pane.stdout.strip().splitlines()[0] if pane.stdout.strip() else ""
        if pane_pid:
            for _pid, args in _list_child_processes(pane_pid):
                if expected in args.lower():
                    return True
        return False
    except (subprocess.CalledProcessError, FileNotFoundError, IndexError):
        return False


def _is_tmux_intern_type(intern_type):
    """tmux-based intern types (claude, codex) — opposed to Copilot which runs in VS Code Chat."""
    return intern_type in ("claude", "codex")


def _iter_registry_entries(registry):
    """Return normalized registry entries with canonical intern identity.

    Project-scoped registry entries are internally keyed as ``project:intern``.
    Online detection and tmux lookup must carry project through to the scoped
    tmux session resolver; ``intern_name`` alone is not globally unique.
    """
    if not registry:
        return []
    if hasattr(registry, "get_all_entries"):
        entries = []
        for entry in registry.get_all_entries():
            if not isinstance(entry, dict):
                continue
            name = entry.get("intern_name") or entry.get("name")
            chat_id = entry.get("chat_id") or entry.get("chatId")
            if name and chat_id:
                entries.append({
                    "name": name,
                    "chat_id": chat_id,
                    "project": entry.get("project") or "",
                })
        return entries
    return [
        {"name": name, "chat_id": chat_id, "project": ""}
        for name, chat_id in registry.get_all().items()
    ]


def _is_plain_feishu_prose_entry(entry):
    text = str(entry or "").strip()
    if not text:
        return False
    if text == "---" or "\n---" in text:
        return False
    if text.startswith("```"):
        return False
    return not text.startswith(_FEISHU_STRUCTURAL_PREFIXES)


def _is_feishu_tool_block_entry(entry):
    text = str(entry or "")
    return text.startswith("```text\n") and text.endswith("\n```")


def _join_feishu_buffer_lines(buffer_lines):
    rendered = []
    previous = None
    for entry in buffer_lines or []:
        if (
            rendered
            and rendered[-1] != ""
            and (
                _is_plain_feishu_prose_entry(previous)
                or _is_feishu_tool_block_entry(previous)
            )
            and _is_plain_feishu_prose_entry(entry)
        ):
            rendered.append("")
        rendered.append(str(entry))
        previous = entry
    return "\n".join(rendered)


def _compose_pending_feishu_buffer(fs, spinner=True):
    text = _join_feishu_buffer_lines(fs.get("buffer_lines") or [])
    footer = fs.get("last_footer") or ""
    if footer:
        text += "\n" + footer
    if spinner:
        text += FEISHU_BUFFER_SPINNER
    return text


def _read_hook_state_file(state_path):
    with open(state_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


def _write_hook_state_file(state_path, state):
    tmp_path = state_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, state_path)


_FEISHU_RICH_CONTENT_KINDS = {"card", "file", "image"}


def _normalize_feishu_rich_content_kind(kind):
    kind = str(kind or "").strip()
    if kind not in _FEISHU_RICH_CONTENT_KINDS:
        raise ValueError(f"unsupported Feishu rich content kind: {kind!r}")
    return kind


def prepare_feishu_rich_content_boundary_for_intern(
        intern_name, kind, api=None, now=None, project=None):
    """Flush the current Feishu text message before sending rich content."""
    kind = _normalize_feishu_rich_content_kind(kind)
    if now is None:
        now = time.time()
    api = api or _api
    if not api:
        return {"status": "no_api", "kind": kind}
    intern_dir = _get_intern_dir(intern_name, project=project)
    if not intern_dir or not os.path.isdir(intern_dir):
        return {"status": "missing_intern_dir", "kind": kind}

    state_path = os.path.join(intern_dir, ".hook_state.json")
    if not os.path.exists(state_path):
        return {"status": "missing_state", "kind": kind}
    lock_path = os.path.join(intern_dir, ".hook_state.lock")
    with open(lock_path, "w", encoding="utf-8") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            state = _read_hook_state_file(state_path)
            fs = state.get("feishu") if isinstance(state.get("feishu"), dict) else {}
            if fs.get("finalized"):
                return {"status": "finalized", "kind": kind}
            if isinstance(fs.get("rich_content_boundary"), dict):
                return {"status": "already_pending", "kind": kind}
            msg_id = fs.get("message_id")
            buffer_lines = fs.get("buffer_lines") or []
            if not msg_id or not buffer_lines:
                return {"status": "no_active_message", "kind": kind}
            count = int(fs.get("update_count") or 0)
            if count >= FEISHU_BUFFER_MAX_UPDATES_PER_MESSAGE:
                return {"status": "edit_limit_deferred", "kind": kind, "update_count": count}
            text = _compose_pending_feishu_buffer(fs, spinner=False)
            body_size = _estimate_post_body_size(text)
            if body_size > FEISHU_BUFFER_MAX_POST_BODY_BYTES:
                return {"status": "content_overflow_deferred", "kind": kind, "body_size": body_size}
            err = api.update_message(msg_id, text)
            if err:
                return {"status": "update_failed", "kind": kind, "error": err}
            fs["update_count"] = count + 1
            fs["pending_tool_flush"] = False
            fs["last_feishu_update_at"] = now
            state["feishu"] = fs
            _write_hook_state_file(state_path, state)
            return {
                "status": "flushed",
                "kind": kind,
                "message_id": msg_id,
                "buffer_len": len(buffer_lines),
                "update_count": count + 1,
            }
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def mark_feishu_rich_content_boundary_sent(
        intern_name, kind, rich_message_id, now=None, project=None):
    """Mark that future Feishu text should continue after a sent rich item."""
    kind = _normalize_feishu_rich_content_kind(kind)
    if not rich_message_id:
        return {"status": "missing_rich_message_id", "kind": kind}
    if now is None:
        now = time.time()
    intern_dir = _get_intern_dir(intern_name, project=project)
    if not intern_dir or not os.path.isdir(intern_dir):
        return {"status": "missing_intern_dir", "kind": kind}

    state_path = os.path.join(intern_dir, ".hook_state.json")
    if not os.path.exists(state_path):
        return {"status": "missing_state", "kind": kind}
    lock_path = os.path.join(intern_dir, ".hook_state.lock")
    with open(lock_path, "w", encoding="utf-8") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            state = _read_hook_state_file(state_path)
            fs = state.get("feishu") if isinstance(state.get("feishu"), dict) else {}
            if fs.get("finalized"):
                return {"status": "finalized", "kind": kind}
            current = fs.get("rich_content_boundary") if isinstance(fs.get("rich_content_boundary"), dict) else {}
            try:
                buffer_len = int(current.get("buffer_len"))
            except (TypeError, ValueError):
                buffer_len = len(fs.get("buffer_lines") or [])
            items = current.get("items") if isinstance(current.get("items"), list) else []
            items.append({"kind": kind, "message_id": rich_message_id})
            fs["rich_content_boundary"] = {
                "buffer_len": max(0, buffer_len),
                "created_at": current.get("created_at") or now,
                "items": items,
            }
            state["feishu"] = fs
            _write_hook_state_file(state_path, state)
            return {
                "status": "marked",
                "kind": kind,
                "message_id": rich_message_id,
                "buffer_len": fs["rich_content_boundary"]["buffer_len"],
            }
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def flush_pending_feishu_buffer_for_intern(intern_dir, api=None, now=None, interval_seconds=None):
    """Flush one intern's pending Feishu tool buffer if its interval elapsed."""
    if now is None:
        now = time.time()
    if interval_seconds is None:
        interval_seconds = daemon_chat_config.get_tool_buffer_flush_interval_seconds()
    api = api or _api
    if not api:
        return {"status": "no_api"}
    if not intern_dir or not os.path.isdir(intern_dir):
        return {"status": "missing_intern_dir"}

    state_path = os.path.join(intern_dir, ".hook_state.json")
    if not os.path.exists(state_path):
        return {"status": "missing_state"}
    lock_path = os.path.join(intern_dir, ".hook_state.lock")
    with open(lock_path, "w", encoding="utf-8") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            state = _read_hook_state_file(state_path)
            fs = state.get("feishu") if isinstance(state.get("feishu"), dict) else {}
            if not fs.get("pending_tool_flush"):
                return {"status": "no_pending"}
            if fs.get("finalized"):
                fs["pending_tool_flush"] = False
                state["feishu"] = fs
                _write_hook_state_file(state_path, state)
                return {"status": "finalized_cleared"}
            msg_id = fs.get("message_id")
            if not msg_id:
                return {"status": "missing_message_id"}
            try:
                last_update_at = float(fs.get("last_feishu_update_at") or 0)
            except (TypeError, ValueError):
                last_update_at = 0
            if last_update_at > 0 and now - last_update_at < interval_seconds:
                return {"status": "not_due", "remaining": interval_seconds - (now - last_update_at)}
            count = int(fs.get("update_count") or 0)
            if count >= FEISHU_BUFFER_MAX_UPDATES_PER_MESSAGE:
                return {"status": "edit_limit_deferred", "update_count": count}
            text = _compose_pending_feishu_buffer(fs)
            body_size = _estimate_post_body_size(text)
            if body_size > FEISHU_BUFFER_MAX_POST_BODY_BYTES:
                return {"status": "content_overflow_deferred", "body_size": body_size}
            err = api.update_message(msg_id, text)
            if err:
                return {"status": "update_failed", "error": err}
            fs["update_count"] = count + 1
            fs["pending_tool_flush"] = False
            fs["last_feishu_update_at"] = now
            state["feishu"] = fs
            _write_hook_state_file(state_path, state)
            return {"status": "flushed", "message_id": msg_id, "update_count": count + 1}
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _feishu_buffer_flush_loop(stop_event):
    while not stop_event.wait(FEISHU_BUFFER_FLUSH_POLL_SECONDS):
        if not _registry or not _api:
            continue
        try:
            interval_seconds = daemon_chat_config.get_tool_buffer_flush_interval_seconds()
        except Exception as exc:
            log.warning(f"[FEISHU_BUFFER] interval read failed: {exc}")
            interval_seconds = 10
        for entry in _iter_registry_entries(_registry):
            intern_name = entry.get("name") or ""
            project = entry.get("project") or ""
            try:
                intern_dir = _get_intern_dir(intern_name, project=project)
                result = flush_pending_feishu_buffer_for_intern(
                    intern_dir,
                    api=_api,
                    now=time.time(),
                    interval_seconds=interval_seconds,
                )
                status = result.get("status")
                if status == "flushed":
                    log.info(
                        "[FEISHU_BUFFER] flushed "
                        f"intern={intern_name} project={project or '-'} "
                        f"msg={result.get('message_id')} update_count={result.get('update_count')}")
                elif status in {"update_failed", "edit_limit_deferred", "content_overflow_deferred"}:
                    log.warning(
                        "[FEISHU_BUFFER] deferred "
                        f"intern={intern_name} project={project or '-'} result={result}")
            except Exception as exc:
                log.warning(
                    f"[FEISHU_BUFFER] flush scan failed for intern={intern_name} "
                    f"project={project or '-'}: {exc}", exc_info=True)


def _owns_local_peer_target(intern_name, project):
    """True only when this daemon really owns the peer target locally.

    ``RegistryManager`` is a Feishu chat mapping. It can contain stale or
    imported chat entries for interns owned by other machines, so peer routing
    must not treat registry membership alone as local ownership.
    """
    if not intern_name or not project:
        return False

    intern_type = _get_intern_type_scoped(intern_name, project=project)
    intern_dir = _get_intern_dir(intern_name, project=project)
    if _is_tmux_intern_type(intern_type):
        if not os.path.isdir(intern_dir):
            return False
    elif intern_type == "copilot":
        if not (_ws_server and _ws_server.is_active(intern_name, project=project)):
            return False
    else:
        return False

    return (_get_intern_project_scoped(intern_name, project=project) or "") == project


def _owns_local_mail_target(intern_name, project):
    if not intern_name or not project:
        return False
    intern_workspace = os.path.join(team_mailbox.team_registry.interns_dir(project), intern_name)
    return os.path.isdir(intern_workspace)


def _is_intern_online(intern_name, project=None):
    """Claude/Codex online depends on CLI process running in tmux; Copilot online depends on any active VS Code window."""
    intern_type = _get_intern_type_scoped(intern_name, project=project)
    if intern_type == "claude":
        return _is_claude_process_running(intern_name, project=project)
    if intern_type == "codex":
        return _is_codex_process_running(intern_name, project=project)
    return bool(_ws_server and _ws_server.is_active(intern_name, project=project))


def _get_intern_project(intern_name, project=None):
    if project:
        entry = _get_intern_session_entry(intern_name, project=project)
        if isinstance(entry, dict) and not entry.get("_ambiguous") and entry.get("project"):
            return entry.get("project")
        return project
    session_entry = _get_intern_session_entry(intern_name)
    if isinstance(session_entry, dict) and session_entry.get("_ambiguous"):
        return ""
    """Read intern project from .hook_state.json. Returns project name string.
    Falls back to auto-detection from directory structure if not set."""
    intern_dir = _get_intern_dir(intern_name, project=project)
    if intern_dir:
        state_file = os.path.join(intern_dir, ".hook_state.json")
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
            project = state.get("project")
            if project:
                return project
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        # Auto-detect: find subdirectory that is a git repo (exclude standard dirs)
        _skip = {"debug", "outputs", "llm_intern_logs", ".claude"}
        try:
            for entry in os.listdir(intern_dir):
                if entry.startswith(".") or entry in _skip:
                    continue
                candidate = os.path.join(intern_dir, entry)
                if os.path.isdir(candidate) and os.path.isdir(os.path.join(candidate, ".git")):
                    return entry
        except OSError:
            pass
    entry = session_entry if isinstance(session_entry, dict) else _get_intern_session_entry(intern_name)
    if isinstance(entry, dict) and entry.get("project"):
        return entry["project"]
    return ""


def _get_intern_project_scoped(intern_name, project=None):
    return _get_intern_project(intern_name, project=project)


# task228: 入站附件 inbox 目录名（repo 外，enterprise runtime intern_dir/.feishu_inbox/<mid>/）。
# 与 hook 侧 `common/utils.STATE_FILE`/`LOCK_FILE` 对齐复用同一把 fcntl 锁。
_FEISHU_INBOX_DIR = ".feishu_inbox"
_HOOK_STATE_FILE = ".hook_state.json"
_HOOK_STATE_LOCK = ".hook_state.lock"


def _set_pending_supervisor_origin(intern_name, message_id, chat_id, project=None):
    """Mark the next real UserPromptSubmit as supervisor-originated from Feishu."""
    intern_dir = _get_intern_dir(intern_name, project=project)
    if not os.path.isdir(intern_dir):
        log.warning(f"[FEISHU_ORIGIN] no intern_dir for {project or ''}/{intern_name}")
        return False
    state_path = os.path.join(intern_dir, _HOOK_STATE_FILE)
    lock_path = os.path.join(intern_dir, _HOOK_STATE_LOCK)
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                state = {}
            state["pending_supervisor_origin"] = {
                "source": "feishu",
                "message_id": message_id,
                "chat_id": chat_id,
                "project": project or "",
                "recorded_at": time.time(),
            }
            _write_hook_state_atomic(state_path, state)
            return True
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _clear_pending_supervisor_origin(intern_name, project=None):
    intern_dir = _get_intern_dir(intern_name, project=project)
    if not os.path.isdir(intern_dir):
        return False
    state_path = os.path.join(intern_dir, _HOOK_STATE_FILE)
    lock_path = os.path.join(intern_dir, _HOOK_STATE_LOCK)
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            try:
                with open(state_path, "r", encoding="utf-8") as f:
                    state = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                state = {}
            if "pending_supervisor_origin" not in state:
                return False
            state.pop("pending_supervisor_origin", None)
            _write_hook_state_atomic(state_path, state)
            return True
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _persist_inbound_attachments(intern_name, message_id, attachments, project=None):
    """task228: 把 relay 下发的 base64 附件落盘并 append 到 intern state.pending_attachments。

    - 落盘目录：`<enterprise intern_dir>/.feishu_inbox/<message_id>/<filename>`
    - 文件名 basename 二次保护（relay 已做过一次，双防穿越）。
    - state.pending_attachments: list of `{"kind":..., "path": abs_path, "filename":...}`
      原子追加（fcntl 互斥 `.hook_state.lock`，与 hook `state_lock` 共用）。

    失败（缺字段、base64 解码错、写盘错）统一 raise；caller 负责向主管 reply_message。
    不允许"写了一半又回退"——附件原子性一次 try；中途 IO 错 raise 时前面已落盘的文件
    不清理（下一次看到仍可处理 / 后续清理任务负责，项目规则 6 重点是不隐藏错误）。
    """
    if not intern_name or not message_id:
        raise ValueError(f"intern_name/message_id 必须非空: {intern_name!r} {message_id!r}")
    if not isinstance(attachments, list) or not attachments:
        raise ValueError("attachments 必须是非空 list")

    intern_dir = _get_intern_dir(intern_name, project=project)
    if not os.path.isdir(intern_dir):
        raise FileNotFoundError(f"intern_dir 不存在: {intern_dir}")

    # 每条消息一个子目录；os.makedirs 幂等。basename 防 message_id 被构造成 '..'.
    safe_mid = os.path.basename(str(message_id)) or "_unknown"
    inbox_dir = os.path.join(intern_dir, _FEISHU_INBOX_DIR, safe_mid)
    os.makedirs(inbox_dir, exist_ok=True)

    new_items = []
    for idx, a in enumerate(attachments):
        if not isinstance(a, dict):
            raise ValueError(f"attachments[{idx}] 不是 dict: {type(a)}")
        kind = a.get("kind")
        filename = os.path.basename(str(a.get("filename") or "")) or f"att_{idx}.bin"
        b64 = a.get("bytes_b64") or ""
        if kind not in ("image", "file") or not b64:
            raise ValueError(f"attachments[{idx}] 字段非法: kind={kind!r} bytes_b64_len={len(b64)}")
        data = base64.b64decode(b64, validate=True)
        dest = os.path.join(inbox_dir, filename)
        with open(dest, "wb") as f:
            f.write(data)
        new_items.append({"kind": kind, "path": dest, "filename": filename})
        log.info(f"[INBOX] {intern_name} {safe_mid} {kind} → {dest} ({len(data)} bytes)")

    # append pending_attachments 到 intern state（与 hook 共享 fcntl LOCK_EX）。
    lock_path = os.path.join(intern_dir, _HOOK_STATE_LOCK)
    state_path = os.path.join(intern_dir, _HOOK_STATE_FILE)
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            try:
                with open(state_path, "r") as f:
                    state = json.load(f)
            except (FileNotFoundError, json.JSONDecodeError):
                state = {}
            pending = state.get("pending_attachments") or []
            if not isinstance(pending, list):
                pending = []
            pending.extend(new_items)
            state["pending_attachments"] = pending
            tmp_path = state_path + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            os.rename(tmp_path, state_path)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)


def _build_group_name(intern_name, is_online=None, project=None):
    """Name format: `🟢 🤖 rule_bob/axis_intern_agents`.

    task204: 类型由群名 emoji 表达（claude=🤖 / codex=🚀 / copilot=空），
    相关头像 API 保留但当前不主动设置。
    """
    project = _get_intern_project_scoped(intern_name, project=project)
    online = _is_intern_online(intern_name, project=project) if is_online is None else is_online
    prefix = "🟢" if online else "🔴"
    stripped = intern_name[len("intern_"):] if intern_name.startswith("intern_") else intern_name
    intern_type = _get_intern_type_scoped(intern_name, project=project) or "copilot"
    badge = {"claude": "🤖 ", "codex": "🚀 ", "copilot": ""}.get(intern_type, "")
    return f"{prefix} {badge}{stripped}/{project}"


def _is_transient_feishu_error(err):
    detail = str(err or "").lower()
    return any(token in detail for token in (
        "http 500",
        "internal error",
        "1663",
        "name or service not known",
        "temporary failure in name resolution",
        "could not resolve",
        "timed out",
        "connection reset",
        "connection refused",
        "network is unreachable",
    ))


def _mobile_to_open_id_with_retry(owner_mobile, *, attempts=3, delay=1.0):
    last_err = None
    for attempt in range(attempts):
        owner_open_id, err = _api.mobile_to_open_id(owner_mobile)
        if owner_open_id and not err:
            return owner_open_id, None
        last_err = err
        if not _is_transient_feishu_error(err) or attempt == attempts - 1:
            break
        time.sleep(delay)
    return None, last_err


def _add_chat_managers_with_retry(chat_id, open_ids, *, attempts=3, delay=1.0):
    last_err = None
    for attempt in range(attempts):
        err = _api.add_chat_managers(chat_id, open_ids)
        if not err:
            return None
        last_err = err
        if not _is_transient_feishu_error(err) or attempt == attempts - 1:
            break
        time.sleep(delay)
    return last_err


def _ensure_group_creator_manager(chat_id, owner_mobile, intern_name, owner_open_id=""):
    owner_open_id = str(owner_open_id or "").strip()
    if not owner_open_id:
        if not owner_mobile:
            return "owner_mobile or owner_open_id required"
        owner_open_id, err = _mobile_to_open_id_with_retry(owner_mobile)
    else:
        err = None
    if err or not owner_open_id:
        return f"mobile lookup failed: {err}"
    err = _add_chat_managers_with_retry(chat_id, [owner_open_id])
    if err:
        return err
    log.info(f"Ensured group creator is manager for {intern_name}: {chat_id}")
    return None


def _relay_lookup_chat(intern_name, project, timeout=5):
    if not (_relay_client and _relay_client.connected and _relay_client._relay_http_base):
        return {}
    query = urllib.parse.urlencode({"intern": intern_name, "project": project or ""})
    url = f"{_relay_client._relay_http_base}/api/chat/lookup?{query}"
    resp = urllib.request.urlopen(url, timeout=timeout)
    return json.loads(resp.read())


def _finalize_group_create(intern_name, chat_id, owner_mobile, result, recovered=False, project="", owner_open_id=""):
    manager_err = _ensure_group_creator_manager(chat_id, owner_mobile, intern_name, owner_open_id=owner_open_id)
    if manager_err:
        return None, {"error": f"ensure_chat_manager failed: {manager_err}"}
    _registry.register(intern_name, chat_id, project=project)
    response = dict(result or {})
    response["chat_id"] = chat_id
    if project:
        response["project"] = project
    if recovered:
        response["existing"] = True
        response["recovered"] = True
    log.info(f"Registered group for {intern_name}: {chat_id} recovered={recovered}")
    threading.Thread(target=_refresh_lights, daemon=True).start()
    return response, None


def _recover_group_create_after_proxy_error(intern_name, project, owner_mobile, original_error, owner_open_id=""):
    log.warning(f"Recovering /api/group/create after proxy error for {intern_name}: {original_error}")
    last_error = None
    for attempt in range(3):
        try:
            result = _relay_lookup_chat(intern_name, project, timeout=5)
            chat_id = result.get("chat_id", "")
            if chat_id:
                return _finalize_group_create(
                    intern_name,
                    chat_id,
                    owner_mobile,
                    result,
                    recovered=True,
                    project=project,
                    owner_open_id=owner_open_id,
                )
        except Exception as exc:
            last_error = exc
            log.warning(f"Relay lookup recovery attempt {attempt + 1} failed for {intern_name}: {exc}")
        time.sleep(1)
    detail = f"{original_error}; recovery lookup failed"
    if last_error:
        detail += f": {last_error}"
    return None, {"error": f"relay proxy failed: {detail}"}


def _notify_intern_status_changed(intern_name, project):
    if not project:
        raise ValueError("project required for intern_status_changed notification")
    if _ws_server:
        _ws_server.push({
            "type": "intern_status_changed",
            "intern_name": intern_name,
            "project": project,
        })


def _compose_feishu_timeline(buffer_lines, spinner=True, footer=""):
    text = _join_feishu_buffer_lines(buffer_lines)
    if footer:
        text += "\n" + footer
    if spinner:
        text += "\n\n⏳ 处理中..."
    return text


def _write_hook_state_atomic(state_path, state):
    tmp_path = state_path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, state_path)


def _finalize_active_feishu_message_for_stop(intern_name, stop_note="\n⛔ 已停止", project=None):
    """Finalize Codex's active Feishu timeline when ESC does not fire Stop hook.

    Codex interrupt currently shows "Conversation interrupted" in the TUI but
    does not reliably emit our Stop hook. The hook state remains the relay
    dashboard's turn_active source, so daemon-side /stop must close the active
    Feishu message and flip feishu.finalized itself.
    """
    if not _api:
        return False, "no_api"

    intern_dir = _get_intern_dir(intern_name, project=project)
    if not intern_dir:
        return False, "no_intern_dir"
    state_path = os.path.join(intern_dir, _HOOK_STATE_FILE)
    lock_path = os.path.join(intern_dir, _HOOK_STATE_LOCK)

    try:
        if not os.path.isdir(intern_dir):
            return False, "no_intern_dir"
        with open(lock_path, "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            try:
                try:
                    with open(state_path, "r", encoding="utf-8") as f:
                        state = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError):
                    return False, "no_state"

                fs = state.get("feishu") or {}
                msg_id = fs.get("message_id")
                if not msg_id:
                    return False, "no_active_message"
                if fs.get("finalized"):
                    return True, "already_finalized"

                buffer_lines = fs.get("buffer_lines") or []
                if not isinstance(buffer_lines, list):
                    buffer_lines = []
                if stop_note and (not buffer_lines or buffer_lines[-1] != stop_note):
                    buffer_lines.append(stop_note)
                if not any(str(line).strip().startswith("✅") for line in buffer_lines[-2:]):
                    buffer_lines.append("\n✅ 完成")

                final_text = _compose_feishu_timeline(buffer_lines, spinner=False)
                err = _api.update_message(msg_id, final_text)
                if err:
                    return False, f"update_failed: {err}"

                fs["buffer_lines"] = buffer_lines
                fs["finalized"] = True
                fs["update_count"] = fs.get("update_count", 0) + 1

                transcript_path = state.get("log", {}).get("transcript_path", "")
                if transcript_path and os.path.exists(transcript_path):
                    try:
                        fs["transcript_offset"] = os.path.getsize(transcript_path)
                    except OSError:
                        pass

                state["feishu"] = fs
                _write_hook_state_atomic(state_path, state)
                return True, "finalized"
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
    except OSError as e:
        return False, f"state_io_error: {e}"


def _push_interns_state_once():
    if _registry and _relay_client and _relay_client.connected:
        try:
            _relay_client.send(_build_state_payload("interns_state"))
        except Exception as e:
            log.warning(f"[STATE] interns_state immediate push failed: {e}")


# Claude Code TUI 在 prompt 为空时将这些字符解释为快捷键（打开 help / bash 模式 / memorize 等），
# 字符不会进对话流。短消息或以这些字符开头的消息需要占位包装。'/' 不在此集合——
# daemon 已在 _handle_feishu_command 截走 slash command。
_CLAUDE_SHORTCUT_FIRST_CHARS = {'?', '!', '@', '&', '#'}
_TMUX_PASTE_MIN_CHARS = 512
_TMUX_ENTER_DELAY_SECONDS = 1.0
_TMUX_ACK_TIMEOUT_SECONDS = 5.0
_CODEX_GOAL_ACK_TIMEOUT_SECONDS = 3.0
_TMUX_ACK_POLL_SECONDS = 0.2
_CODEX_GOAL_REPLACE_PENDING = "goal replace waiting for Feishu confirmation"
_CODEX_GOAL_REPLACE_TOOL = "codex_goal_replace"
_CODEX_GOAL_REPLACE_QUESTION_KEY = "Replace goal?"
_TMUX_SUBMIT_UNCONFIRMED_ERROR = "prompt submit unconfirmed"
_TMUX_SUBMIT_RETRY_UNCONFIRMED_ERROR = "prompt submit unconfirmed after enter retry"
_TMUX_SUBMIT_UNCONFIRMED_ERRORS = {
    _TMUX_SUBMIT_UNCONFIRMED_ERROR,
    _TMUX_SUBMIT_RETRY_UNCONFIRMED_ERROR,
}


def _format_tmux_unconfirmed_message(intern_name, err):
    if err == _TMUX_SUBMIT_RETRY_UNCONFIRMED_ERROR:
        return (
            f"⚠️ 已写入 {intern_name} 的 tmux，并补发过一次 Enter，"
            "但仍未确认 Codex 提交。请查看 tmux。"
        )
    return (
        f"⚠️ 已写入 {intern_name} 的 tmux，但未确认 Codex 提交；"
        "未补发 Enter（未确认输入框可提交）。请查看 tmux。"
    )


def _should_reply_tmux_unconfirmed(err):
    return err == _TMUX_SUBMIT_RETRY_UNCONFIRMED_ERROR


def _send_to_claude_tmux(intern_name, text, delivery_id="", project=""):
    """Send message to Claude CLI via tmux send-keys. Returns (success, error).

    Two-step send: literal text first, then Enter after a short delay.
    Uses -l (literal) flag to prevent escape sequence injection.
    """
    return _send_to_tmux_cli(intern_name, text, _is_claude_process_running, "Claude", delivery_id, project=project)


def _send_to_codex_tmux(intern_name, text, delivery_id="", require_ack=True, project=""):
    """Send message to Codex CLI via tmux send-keys. Returns (success, error)."""
    return _send_to_tmux_cli(
        intern_name, text, _is_codex_process_running, "Codex", delivery_id,
        require_codex_ack=require_ack, project=project)


def _tmux_send_enter(target):
    subprocess.run(
        ["tmux", "send-keys", "-t", target, "Enter"],
        check=True, capture_output=True
    )


def _tmux_send_escape(target):
    subprocess.run(
        ["tmux", "send-keys", "-t", target, "Escape"],
        check=True, capture_output=True
    )


def _tmux_send_literal(target, text):
    subprocess.run(
        ["tmux", "send-keys", "-t", target, "-l", "--", text],
        check=True, capture_output=True
    )


def _tmux_clear_input_line(target):
    subprocess.run(
        ["tmux", "send-keys", "-t", target, "C-u"],
        check=True, capture_output=True
    )


def _tmux_paste_text(target, text):
    buffer_name = f"feishu-prompt-{os.getpid()}-{int(time.time() * 1000)}"
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(text)
            tmp_path = f.name
        subprocess.run(
            ["tmux", "load-buffer", "-b", buffer_name, tmp_path],
            check=True, capture_output=True
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-d", "-p", "-b", buffer_name, "-t", target],
            check=True, capture_output=True
        )
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _load_hook_state_for_intern(intern_name, project=None):
    intern_dir = _get_intern_dir(intern_name, project=project)
    if not intern_dir:
        return {}
    state_path = os.path.join(intern_dir, ".hook_state.json")
    try:
        with open(state_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _codex_transcript_matches_intern(path, intern_name, project=None):
    intern_dir_raw = _get_intern_dir(intern_name, project=project)
    if not intern_dir_raw:
        return False
    intern_dir = os.path.abspath(intern_dir_raw)
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
        obj = json.loads(first) if first else {}
    except (OSError, json.JSONDecodeError):
        return False
    if obj.get("type") != "session_meta":
        return False
    cwd = obj.get("payload", {}).get("cwd", "")
    if not cwd:
        return False
    cwd_abs = os.path.abspath(cwd)
    return cwd_abs == intern_dir or cwd_abs.startswith(intern_dir + os.sep)


def _discover_codex_transcript_path(intern_name, project=None, allow_session_scan=True):
    state = _load_hook_state_for_intern(intern_name, project=project)
    candidates = []
    state_path = state.get("log", {}).get("transcript_path", "")
    if state_path and os.path.exists(state_path):
        candidates.append(state_path)

    sessions_dir = Path(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex"))) / "sessions"
    if allow_session_scan and sessions_dir.exists():
        try:
            recent = sorted(
                sessions_dir.rglob("*.jsonl"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:80]
        except OSError:
            recent = []
        candidates.extend(str(p) for p in recent)

    seen = set()
    matched = []
    for path in candidates:
        if path in seen or not os.path.exists(path):
            continue
        seen.add(path)
        if _codex_transcript_matches_intern(path, intern_name, project=project):
            try:
                matched.append((os.path.getmtime(path), path))
            except OSError:
                pass
    if not matched:
        return ""
    return max(matched)[1]


def _codex_thread_id_from_transcript(transcript_path):
    if not transcript_path or not os.path.exists(transcript_path):
        return ""
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            first = f.readline()
        obj = json.loads(first) if first else {}
    except (OSError, json.JSONDecodeError):
        obj = {}
    if obj.get("type") == "session_meta":
        thread_id = str((obj.get("payload") or {}).get("id") or "")
        if thread_id:
            return thread_id
    match = re.search(r"rollout-[0-9TZ:.-]+-([0-9a-fA-F-]{36})\.jsonl$", str(transcript_path))
    return match.group(1) if match else ""


def _codex_goal_snapshot_path(intern_dir):
    return os.path.join(intern_dir, CODEX_GOAL_SNAPSHOT_FILE)


def _write_codex_goal_snapshot(intern_dir, snapshot):
    os.makedirs(intern_dir, exist_ok=True)
    path = _codex_goal_snapshot_path(intern_dir)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp_path, path)


def _codex_goal_db_candidates():
    codex_home = Path(os.environ.get("CODEX_HOME", os.path.expanduser("~/.codex")))
    candidates = []
    for path in codex_home.glob("goals_*.sqlite"):
        match = re.match(r"goals_(\d+)\.sqlite$", path.name)
        if not match:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        candidates.append((int(match.group(1)), mtime, path))
    return [path for _, _, path in sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)]


def _codex_goal_db_has_expected_schema(db_path):
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.25)
        try:
            conn.execute(f"PRAGMA busy_timeout={CODEX_GOAL_SQLITE_BUSY_TIMEOUT_MS}")
            rows = conn.execute("PRAGMA table_info(thread_goals)").fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return False
    columns = {row[1] for row in rows}
    required = {
        "thread_id",
        "goal_id",
        "objective",
        "status",
        "token_budget",
        "tokens_used",
        "time_used_seconds",
        "created_at_ms",
        "updated_at_ms",
    }
    return required.issubset(columns)


def _discover_codex_goal_db_path():
    for db_path in _codex_goal_db_candidates():
        if _codex_goal_db_has_expected_schema(db_path):
            return str(db_path)
    return ""


def _codex_goal_from_sqlite_row(row, thread_id):
    if row is None:
        return None
    return {
        "status": str(row["status"] or ""),
        "objective": str(row["objective"] or ""),
        "thread_id": str(row["thread_id"] or thread_id or ""),
        "goal_id": str(row["goal_id"] or ""),
        "created_at": int(row["created_at_ms"] or 0),
        "updated_at": int(row["updated_at_ms"] or 0),
        "token_budget": row["token_budget"],
        "tokens_used": int(row["tokens_used"] or 0),
        "time_used_seconds": int(row["time_used_seconds"] or 0),
    }


def _read_codex_goal_from_sqlite(db_path, thread_id):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=0.25)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={CODEX_GOAL_SQLITE_BUSY_TIMEOUT_MS}")
        row = conn.execute(
            """
            SELECT thread_id, goal_id, objective, status, token_budget,
                   tokens_used, time_used_seconds, created_at_ms, updated_at_ms
            FROM thread_goals
            WHERE thread_id = ?
            """,
            (thread_id,),
        ).fetchone()
    finally:
        conn.close()
    return _codex_goal_from_sqlite_row(row, thread_id)


def _codex_goal_snapshot_base(intern_name, project, intern_dir, transcript_path, thread_id, reason):
    return {
        "schema": "intern-agents.codex-goal-snapshot.v1",
        "schema_version": 1,
        "source": "codex_goal_sqlite",
        "source_path": "",
        "reason": reason,
        "intern_name": intern_name,
        "project": project or "",
        "intern_dir": intern_dir or "",
        "transcript_path": transcript_path or "",
        "thread_id": thread_id or "",
        "refreshed_at_ms": int(time.time() * 1000),
        "status": "unknown",
        "goal": None,
        "error": "",
    }


def _refresh_codex_goal_snapshot_for_intern(intern_name, project=None, reason="manual", allow_session_scan=True):
    intern_dir = _get_intern_dir(intern_name, project=project)
    transcript_path = ""
    thread_id = ""
    if intern_dir:
        transcript_path = _discover_codex_transcript_path(
            intern_name,
            project=project,
            allow_session_scan=allow_session_scan,
        )
        thread_id = _codex_thread_id_from_transcript(transcript_path)
    snapshot = _codex_goal_snapshot_base(
        intern_name,
        project,
        intern_dir,
        transcript_path,
        thread_id,
        reason,
    )
    if not intern_dir:
        snapshot["error"] = "intern_dir_not_found"
        return snapshot
    if not thread_id:
        snapshot["error"] = "thread_id_not_found"
        _write_codex_goal_snapshot(intern_dir, snapshot)
        return snapshot

    db_path = _discover_codex_goal_db_path()
    snapshot["source_path"] = db_path
    if not db_path:
        snapshot["error"] = "goal_db_not_found"
        _write_codex_goal_snapshot(intern_dir, snapshot)
        return snapshot

    try:
        goal = _read_codex_goal_from_sqlite(db_path, thread_id)
    except sqlite3.Error as exc:
        snapshot["error"] = f"goal_db_read_failed: {exc}"
        _write_codex_goal_snapshot(intern_dir, snapshot)
        return snapshot

    if goal is None:
        snapshot["status"] = "none"
        snapshot["goal"] = None
    else:
        snapshot["status"] = str(goal.get("status") or "unknown").lower()
        snapshot["goal"] = goal
    _write_codex_goal_snapshot(intern_dir, snapshot)
    return snapshot


def _read_codex_goal_snapshot_for_intern(intern_name, project=None):
    intern_dir = _get_intern_dir(intern_name, project=project)
    if not intern_dir:
        return {}
    path = _codex_goal_snapshot_path(intern_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _refresh_codex_goal_snapshots_once(reason="poll", allow_session_scan=False):
    if not _registry:
        return
    for item in _iter_registry_entries(_registry):
        name = item.get("name") or ""
        project = _get_intern_project_scoped(name, project=item.get("project") or "")
        if not name or _get_intern_type_scoped(name, project=project) != "codex":
            continue
        try:
            _refresh_codex_goal_snapshot_for_intern(
                name,
                project=project,
                reason=reason,
                allow_session_scan=allow_session_scan,
            )
        except Exception as exc:
            log.debug(f"[CODEX_GOAL] snapshot refresh failed for {_online_key(name, project)}: {exc}")


def _codex_goal_snapshot_loop(stop_event, interval=CODEX_GOAL_SNAPSHOT_INTERVAL_SECONDS):
    while not stop_event.is_set():
        _refresh_codex_goal_snapshots_once(reason="poll", allow_session_scan=False)
        stop_event.wait(interval)


def _normalise_prompt_for_ack(text):
    return (text or "").replace("\r\n", "\n").replace("\r", "\n")


def _codex_user_text_from_entry(obj):
    payload = obj.get("payload", {})
    if obj.get("type") == "event_msg" and payload.get("type") == "user_message":
        return payload.get("message", "")
    if obj.get("type") == "response_item":
        if payload.get("type") == "message" and payload.get("role") == "user":
            parts = []
            for item in payload.get("content", []) or []:
                if isinstance(item, dict) and item.get("type") in ("input_text", "text"):
                    parts.append(item.get("text", ""))
            return "\n".join(parts)
    return ""


def _codex_goal_objective_from_entry(obj):
    payload = obj.get("payload", {})
    if obj.get("type") != "event_msg" or payload.get("type") != "thread_goal_updated":
        return ""
    goal = payload.get("goal") or {}
    return goal.get("objective", "")


def _latest_codex_goal_state(intern_name, project=None):
    snapshot = _refresh_codex_goal_snapshot_for_intern(
        intern_name,
        project=project,
        reason="on_demand",
        allow_session_scan=True,
    )
    source = snapshot.get("source_path") or snapshot.get("transcript_path") or "codex_goal_snapshot"
    if snapshot.get("error") and snapshot.get("status") == "unknown":
        return "", None
    goal = snapshot.get("goal") if isinstance(snapshot.get("goal"), dict) else None
    return source, goal


def _current_codex_goal_state_for_replacement(intern_name, project=None):
    _, goal = _latest_codex_goal_state(intern_name, project=project)
    return goal


def _format_goal_elapsed(value):
    try:
        seconds = int(value or 0)
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h{minutes}m" if minutes else f"{hours}h"


def _goal_state_exists_for_replacement(goal_state):
    if not isinstance(goal_state, dict):
        return False
    status = str(goal_state.get("status") or "").lower()
    return bool(status and status not in {"cleared", "canceled", "cancelled", "complete", "completed"})


def _format_codex_goal_status_reply(intern_name, project=None):
    source, goal = _latest_codex_goal_state(intern_name, project=project)
    if not source:
        snapshot = _read_codex_goal_snapshot_for_intern(intern_name, project=project)
        error = snapshot.get("error") if isinstance(snapshot, dict) else ""
        detail = f"\n原因：{error}" if error else ""
        return f"🎯 {intern_name} goal status: unknown\n无法确认当前 goal。{detail}"
    if not isinstance(goal, dict):
        return f"🎯 {intern_name} goal status: none\n当前没有 active goal。"

    status = str(goal.get("status") or "unknown").lower()
    if status in {"cleared", "canceled", "cancelled", "complete", "completed"}:
        return f"🎯 {intern_name} goal status: {status}\n当前没有 active goal。"

    lines = [f"🎯 {intern_name} goal status: {status}"]
    objective = str(goal.get("objective") or "").strip()
    if objective:
        lines.append("Goal:")
        lines.append(objective)
    elapsed = _format_goal_elapsed(goal.get("time_used_seconds"))
    if elapsed:
        lines.append(f"time_used: {elapsed}")
    tokens = int(goal.get("tokens_used") or 0)
    if tokens:
        lines.append(f"tokens_used: {tokens}")
    thread_id = str(goal.get("thread_id") or "")
    if thread_id:
        lines.append(f"thread_id: {thread_id}")
    return "\n".join(lines)


def _codex_transcript_has_user_prompt(transcript_path, start_offset, text):
    expected = _normalise_prompt_for_ack(text)
    try:
        file_size = os.path.getsize(transcript_path)
    except OSError:
        return False
    offset = start_offset if 0 <= start_offset <= file_size else 0
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                actual = _normalise_prompt_for_ack(_codex_user_text_from_entry(obj))
                if actual == expected:
                    return True
    except OSError:
        return False
    return False


def _codex_transcript_has_goal_update(transcript_path, start_offset, objective):
    expected = _normalise_prompt_for_ack(objective)
    try:
        file_size = os.path.getsize(transcript_path)
    except OSError:
        return False
    offset = start_offset if 0 <= start_offset <= file_size else 0
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(offset)
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                actual = _normalise_prompt_for_ack(_codex_goal_objective_from_entry(obj))
                if actual == expected:
                    return True
    except OSError:
        return False
    return False


def _get_codex_ack_start(intern_name, project=None):
    transcript_path = _discover_codex_transcript_path(intern_name, project=project)
    if not transcript_path:
        return "", 0
    try:
        return transcript_path, os.path.getsize(transcript_path)
    except OSError:
        return "", 0


def _wait_for_codex_prompt_ack(transcript_path, start_offset, text, timeout=_TMUX_ACK_TIMEOUT_SECONDS):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _codex_transcript_has_user_prompt(transcript_path, start_offset, text):
            return True
        time.sleep(_TMUX_ACK_POLL_SECONDS)
    return _codex_transcript_has_user_prompt(transcript_path, start_offset, text)


def _wait_for_codex_goal_ack(transcript_path, start_offset, objective, timeout=_TMUX_ACK_TIMEOUT_SECONDS):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _codex_transcript_has_goal_update(transcript_path, start_offset, objective):
            return True
        time.sleep(_TMUX_ACK_POLL_SECONDS)
    return _codex_transcript_has_goal_update(transcript_path, start_offset, objective)


_CODEX_NON_IDLE_MARKERS = (
    "action required",
    "question ",
    "enter to confirm",
    "esc to cancel",
    "ctrl+c to cancel",
    "ctrl+c to interrupt",
    "esc to interrupt",
    "working (",
)


def _visible_prompt_fragment(text):
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) < 12:
        return ""
    return compact[-80:]


def _visible_goal_objective_fragment(text):
    compact = re.sub(r"\s+", "", text or "")
    if len(compact) >= 12:
        return compact[-80:]
    if len(compact) >= 4:
        return compact
    return ""


def _visible_prompt_fragments(text):
    compact = re.sub(r"\s+", "", text or "").lower()
    if len(compact) < 12:
        return ()
    fragments = {compact[-80:]}
    if len(compact) > 48:
        fragments.add(compact[:48])
    return tuple(fragment for fragment in fragments if len(fragment) >= 12)


def _codex_prompt_pending_submit(target, text):
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-J", "-t", target, "-S", "-80"],
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        log.warning(f"[TMUX_SEND] capture-pane failed for {target}: {e}")
        return False

    capture = result.stdout or ""
    lines = capture.splitlines()
    bottom = "\n".join(lines[-12:])
    marker_scope = "\n".join(lines[-20:]).lower()
    if any(marker in marker_scope for marker in _CODEX_NON_IDLE_MARKERS):
        return False
    fragment = _visible_prompt_fragment(text)
    if not fragment:
        return False
    return fragment in re.sub(r"\s+", "", bottom)


def _codex_prompt_visible_in_pane(target, text):
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-J", "-t", target, "-S", "-80"],
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        log.warning(f"[TMUX_SEND] capture-pane failed for visible prompt check {target}: {e}")
        return False

    fragment = _visible_prompt_fragment(text)
    if not fragment:
        return False
    return fragment in re.sub(r"\s+", "", result.stdout or "")


def _codex_goal_replace_confirmation_pending(target, objective):
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-J", "-t", target, "-S", "-400"],
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        log.warning(f"[PEER] capture-pane failed for goal replace confirmation {target}: {e}")
        return False

    compact = re.sub(r"\s+", "", result.stdout or "").lower()
    fragments = _visible_prompt_fragments(objective)
    if not fragments:
        return False
    required_markers = (
        "replacegoal?",
        "replacecurrentgoal",
        "cancel",
    )
    optional_markers = (
        "newobjective:",
        "pressentertoconfirm",
    )
    return (
        any(fragment in compact for fragment in fragments)
        and all(marker in compact for marker in required_markers)
        and any(marker in compact for marker in optional_markers)
    )


def _codex_goal_replace_questions(objective):
    return [{
        "header": "Goal",
        "question": (
            "Codex 正在确认是否替换当前 goal。\n\n"
            f"New objective:\n{objective}\n\n"
            "请选择处理方式。"
        ),
        "options": [
            {
                "label": "Replace current goal",
                "description": "确认替换当前 goal，并立即开始执行新 objective。",
                "recommended": True,
            },
            {
                "label": "Cancel",
                "description": "保留当前 goal，取消这次 /goal 设置。",
            },
        ],
    }]


def _codex_goal_replace_choice(answers):
    if not isinstance(answers, dict):
        return "replace"
    value = answers.get(_CODEX_GOAL_REPLACE_QUESTION_KEY)
    if value is None and answers:
        value = next(iter(answers.values()))
    if isinstance(value, list):
        value = value[0] if value else ""
    text = str(value or "").strip().lower()
    if text in {"cancel", "2"} or "取消" in text:
        return "cancel"
    return "replace"


def _reply_codex_goal_replace_result(message_id, text):
    if message_id and _api:
        _api.reply_message(message_id, text)


def _await_codex_goal_replace_confirmation(
    intern_name,
    project,
    target,
    content,
    ack_path,
    ack_offset,
    question_id,
    message_id,
    log_ctx,
):
    key = _pending_question_key(intern_name, project)
    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
        event = entry.get("event") if entry else None
    if not event:
        _reply_codex_goal_replace_result(
            message_id,
            f"❌ 未找到 {intern_name} 的 goal 替换确认状态，请重新发送 /goal",
        )
        log.warning(f"[PEER] codex goal replace missing pending event {log_ctx}")
        return

    event.wait()
    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
        answers = dict(entry.get("answer") or {}) if entry else {}
        if entry:
            _remove_pending_question_locked(intern_name, project, entry)
    if not answers:
        _reply_codex_goal_replace_result(
            message_id,
            f"❌ 未找到 {intern_name} 的 goal 替换确认答案，请重新发送 /goal",
        )
        log.warning(f"[PEER] codex goal replace missing answer {log_ctx}")
        return
    source = "飞书卡片"

    choice = _codex_goal_replace_choice(answers)
    try:
        if choice == "cancel":
            _tmux_send_escape(target)
            _reply_codex_goal_replace_result(
                message_id,
                f"✅ 已取消替换 {intern_name} 的 goal，保留当前 goal",
            )
            log.info(f"[PEER] codex goal replace cancelled {log_ctx}, source={source}")
            return

        _tmux_send_enter(target)
    except subprocess.CalledProcessError as e:
        _reply_codex_goal_replace_result(message_id, f"❌ 处理 goal 替换确认失败: {e}")
        log.warning(f"[PEER] codex goal replace confirmation send failed {log_ctx}: {e}")
        return

    if ack_path and _wait_for_codex_goal_ack(
        ack_path, ack_offset, content, timeout=_CODEX_GOAL_ACK_TIMEOUT_SECONDS
    ):
        _reply_codex_goal_replace_result(
            message_id,
            f"✅ 已确认替换并向 {intern_name} 设置新 goal",
        )
        log.info(f"[PEER] codex goal replace confirmed {log_ctx}, source={source}, ack=ok")
        return
    if _codex_goal_visible_in_panel(target, content):
        _reply_codex_goal_replace_result(
            message_id,
            f"✅ 已确认替换并向 {intern_name} 设置新 goal",
        )
        log.info(f"[PEER] codex goal replace confirmed {log_ctx}, source={source}, ack=panel")
        return

    _reply_codex_goal_replace_result(
        message_id,
        f"⚠️ 已确认替换，但未确认 {intern_name} 的新 goal 是否生效；请查看 tmux",
    )
    log.warning(f"[PEER] codex goal replace ack failed {log_ctx}, transcript={ack_path or '-'}")
    with _pq_lock:
        current = _pending_questions.get(key)
        if current and current.get("question_id") == question_id:
            _remove_pending_question_locked(intern_name, project, current)


def _start_codex_goal_replace_feishu_confirmation(
    intern_name,
    project,
    target,
    content,
    ack_path,
    ack_offset,
    message_id,
    log_ctx,
):
    if not project or not _api or not _registry:
        return False
    question_id = uuid.uuid4().hex
    status, resp = _register_pending_question(
        intern_name,
        _CODEX_GOAL_REPLACE_TOOL,
        _codex_goal_replace_questions(content),
        metadata={
            "question_id": question_id,
            "source": "codex_goal_replace",
        },
        project=project,
    )
    if status != 200 or not isinstance(resp, dict) or not resp.get("message_id"):
        with _pq_lock:
            entry = _get_pending_question_locked(intern_name, project, question_id)
            if entry:
                _remove_pending_question_locked(intern_name, project, entry)
        log.warning(
            f"[PEER] codex goal replace card unavailable {log_ctx}, "
            f"status={status}, resp={resp}"
        )
        return False

    threading.Thread(
        target=_await_codex_goal_replace_confirmation,
        args=(intern_name, project, target, content, ack_path, ack_offset, question_id, message_id, log_ctx),
        daemon=True,
    ).start()
    log.info(f"[PEER] codex goal replace card sent {log_ctx}, question_id={question_id}")
    return True


def _codex_goal_visible_in_panel_result(target, objective):
    """Best-effort confirmation that Codex visibly accepted the goal.

    Transcript ``thread_goal_updated`` is the authoritative ack, but Codex can
    accept the slash command while the transcript watcher misses the event. The
    panel check is intentionally narrow: the objective fragment must be visible
    together with goal/objective UI wording, and the visible text must not simply
    be the still-pending ``/goal ...`` input line.
    """
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-J", "-t", target, "-S", "-120"],
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        log.warning(f"[PEER] capture-pane failed for goal panel check {target}: {e}")
        return False, "capture_failed"

    capture = result.stdout or ""
    compact = re.sub(r"\s+", "", capture)
    lower_compact = compact.lower()
    fragment = _visible_goal_objective_fragment(objective)
    if not fragment:
        return False, "fragment_too_short"
    lower_fragment = fragment.lower()

    goal_markers = (
        "goalactive",
        "goal:",
        "currentgoal",
        "pressinggoal",
        "objective:",
        "newobjective:",
        "目标:",
        "当前目标",
    )
    line_compacts = [
        re.sub(r"\s+", "", line).lower()
        for line in capture.splitlines()
    ]
    def _is_pending_goal_input_line(line):
        return f"/goal{lower_fragment}" in line

    for line in line_compacts:
        if (
            lower_fragment in line
            and any(marker in line for marker in goal_markers)
            and not _is_pending_goal_input_line(line)
        ):
            return True, "ok"

    # Some Codex panels wrap labels and objective text across adjacent lines.
    # Do not let a pending input line provide the objective fragment; otherwise
    # an old goal marker plus a new unsent `/goal ...` input looks active.
    for idx in range(len(line_compacts)):
        window_lines = line_compacts[idx:idx + 3]
        window = "".join(window_lines)
        fragment_source = "".join(
            line for line in window_lines
            if not _is_pending_goal_input_line(line)
        )
        if lower_fragment in fragment_source and any(marker in window for marker in goal_markers):
            return True, "ok"

    nonempty_lines = [line for line in line_compacts if line]
    bottom_input_scope = nonempty_lines[-3:]
    if any(_is_pending_goal_input_line(line) for line in bottom_input_scope):
        return False, "pending_input_line"

    if not any(marker in lower_compact for marker in goal_markers):
        return False, "marker_missing"
    if lower_fragment not in lower_compact:
        return False, "fragment_missing"
    return True, "ok"


def _codex_goal_visible_in_panel(target, objective):
    visible, _reason = _codex_goal_visible_in_panel_result(target, objective)
    return visible


def _codex_goal_clear_visible_in_panel(target):
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-p", "-J", "-t", target, "-S", "-80"],
            check=True, capture_output=True, text=True
        )
    except subprocess.CalledProcessError as e:
        log.warning(f"[GOAL_API] capture-pane failed for goal clear panel check {target}: {e}")
        return False

    compact = re.sub(r"\s+", "", result.stdout or "").lower()
    return "goalcleared" in compact or "currentgoal:none" in compact or "goal:none" in compact


def _delivery_hash(text):
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def _send_to_tmux_cli(
    intern_name, text, process_check, label, delivery_id="", require_codex_ack=True, project=""
):
    """Generic tmux send-keys for CLI-type interns (claude/codex).

    Claude 分支：短消息（≤2 字符）或首字符在快捷键集合里的消息走占位包装路径，
    避免被 Claude TUI 当作快捷键吞掉。长消息与 Codex 走原路径。
    """
    if not _check_tmux_session(intern_name, project=project):
        return False, "tmux session not found"
    if not process_check(intern_name, project=project):
        return False, f"{label} has exited (tmux session exists but {label} is not running)"

    target = _tmux_target(intern_name, project=project)
    delivery_hash = _delivery_hash(text)
    ack_path, ack_offset = ("", 0)
    if label == "Codex" and require_codex_ack:
        ack_path, ack_offset = _get_codex_ack_start(intern_name, project=project)
    needs_wrap = (
        label == "Claude"
        and text
        and (len(text) <= 2 or text[0] in _CLAUDE_SHORTCUT_FIRST_CHARS)
    )
    use_paste_buffer = (
        label == "Codex"
        and bool(text)
        and not needs_wrap
        and ("\n" in text or len(text) >= _TMUX_PASTE_MIN_CHARS)
    )
    try:
        if needs_wrap:
            reason = "short_msg" if len(text) <= 2 else "shortcut_first_char"
            # 先发一个空格占住 prompt，避免首字符触发 TUI 快捷键
            _tmux_send_literal(target, " ")
            _tmux_send_literal(target, text)
            time.sleep(0.05)
            # 抹掉占位空格：Home 回行首 + Delete 删一字符
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Home"],
                check=True, capture_output=True
            )
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Delete"],
                check=True, capture_output=True
            )
            time.sleep(_TMUX_ENTER_DELAY_SECONDS)
            _tmux_send_enter(target)
            method = "wrapped"
            log.info(f"[TMUX_SEND] wrapped (reason={reason}) intern={intern_name}, text={text[:30]!r}")
        else:
            if use_paste_buffer:
                _tmux_paste_text(target, text)
                method = "paste-buffer"
            else:
                _tmux_send_literal(target, text)
                method = "send-keys"
            time.sleep(_TMUX_ENTER_DELAY_SECONDS)
            _tmux_send_enter(target)

        if label == "Codex":
            log_ctx = f"intern={intern_name}, delivery={delivery_id or '-'}, hash={delivery_hash}"
            if not require_codex_ack:
                log.info(f"[TMUX_SEND] success {log_ctx}, len={len(text)}, method={method}, ack=skipped")
                return True, None
            if not ack_path:
                if _codex_prompt_visible_in_pane(target, text):
                    log.info(f"[TMUX_SEND] success {log_ctx}, len={len(text)}, method={method}, ack=pane")
                    return True, None
                log.warning(f"[TMUX_SEND] codex ack unavailable {log_ctx}, len={len(text)}, method={method}")
                return False, _TMUX_SUBMIT_UNCONFIRMED_ERROR
            if _wait_for_codex_prompt_ack(ack_path, ack_offset, text):
                log.info(f"[TMUX_SEND] success {log_ctx}, len={len(text)}, method={method}, ack=ok")
                return True, None
            if not _codex_prompt_pending_submit(target, text):
                log.warning(f"[TMUX_SEND] ack timeout without enter retry {log_ctx}, len={len(text)}, method={method}")
                return False, _TMUX_SUBMIT_UNCONFIRMED_ERROR
            log.warning(f"[TMUX_SEND] ack timeout {log_ctx}, len={len(text)}; retrying Enter once")
            _tmux_send_enter(target)
            if _wait_for_codex_prompt_ack(ack_path, ack_offset, text):
                log.info(f"[TMUX_SEND] success {log_ctx}, len={len(text)}, method={method}, ack=ok_after_retry")
                return True, None
            log.warning(f"[TMUX_SEND] ack failed after enter retry {log_ctx}, transcript={ack_path}")
            return False, _TMUX_SUBMIT_RETRY_UNCONFIRMED_ERROR

        log.info(f"[TMUX_SEND] success intern={intern_name}, len={len(text)}, method={method}")
        return True, None
    except subprocess.CalledProcessError as e:
        return False, str(e)


def _notify_peer_target_outdated(from_name, to_name, to_project):
    """task261: A 端拿到 reason=target_outdated 后给 A 飞书群发 systemMessage 提示
    主管升级 B 所在机器的插件。target_outdated 是版本兼容问题，LLM 自己看不懂也
    不会主动汇报，必须主动飞书可见；其他 undeliverable reason（busy/offline/
    unknown_target/400 类）由 LLM 自行处理，不发飞书避免噪音。

    任何失败仅 warn-only 日志 anchor [PEER_VISIBILITY]，不嵌套提示。
    """
    if _api is None:
        log.warning(f"[PEER_VISIBILITY] _api not initialized, skip target_outdated alert for {from_name}")
        return
    chat_id = _registry.find_chat_id(from_name)
    if not chat_id:
        log.warning(f"[PEER_VISIBILITY] no chat_id for {from_name}, skip target_outdated alert")
        return
    text = (
        f"⚠️ peer 投递失败：{to_project}/{to_name} 所在机器的插件版本太旧，"
        f"daemon 未声明本次投递需要的 peer capability。请升级该机器的 "
        f"intern-agent-helper 插件后重试。（reason=target_outdated）"
    )
    _, err = _api.send_message(chat_id, text)
    if err:
        log.warning(f"[PEER_VISIBILITY] send target_outdated alert failed for {from_name}: {err}")


_PEER_DELIVERY_MODES = {"default", "next", "stop"}
_GOAL_API_ACTIONS = {"set", "replace", "cancel"}
_TEAM_CONTRACT_ROLES = {"coordinator", "team_lead", "worker"}
_INDEPENDENT_ROLE = "independent"
_TEAM_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_TEAM_SUPERVISOR_ONLY_REASON = "team_only_accepts_supervisor_tasks_via_coordinator"
_TEAM_SUPERVISOR_ONLY_MESSAGE = "team只允许coordinator从主管接受任务"
_PEER_TARGET_QUEUE_LIMIT = 100
_PEER_DELIVERY_WORKER_COUNT = 32
_PEER_BATCH_MAX_CHARS = 64 * 1024
_PEER_BATCH_ACK_TIMEOUT_SECONDS = 300
_PEER_BATCH_ACK_POLL_SECONDS = 5


def _format_peer_text(payload, content):
    from_intern = payload.get("from_intern_name", "")
    from_project = payload.get("from_project", "")
    mode = payload.get("original_mode") or payload.get("mode") or "default"
    delivery = payload.get("delivery_kind") or "direct"
    msg_id = payload.get("msg_id") or payload.get("request_id") or "-"
    source = f"{from_project}/{from_intern}" if from_project else from_intern
    prefix = f"【peer mode={mode} delivery={delivery} from {source} msg_id={msg_id}】"
    return prefix + "\n" + content


def _format_peer_batch_text(batch_id, jobs):
    lines = [
        f"【peer batch batch_id={batch_id} count={len(jobs)}】",
        f"你同时收到 {len(jobs)} 条 peer 消息，请按顺序处理：",
        "",
    ]
    for idx, job in enumerate(jobs, start=1):
        payload = job["payload"]
        from_intern = payload.get("from_intern_name", "")
        from_project = payload.get("from_project", "")
        source = f"{from_project}/{from_intern}" if from_project else from_intern
        lines.extend([
            f"{idx}. 来自 {source}，msg_id={job['msg_id']}，mode={job['mode']}",
            "内容：",
            job["content"],
            "",
        ])
    return "\n".join(lines).rstrip() + "\n"


def _send_peer_text_to_tmux(intern_name, intern_type, text, msg_id, project=""):
    if intern_type == "codex":
        return _send_to_codex_tmux(intern_name, text, delivery_id=msg_id, require_ack=True, project=project)
    return _send_to_claude_tmux(intern_name, text, delivery_id=msg_id, project=project)


def _normalize_contract_role(role):
    role = (role or _INDEPENDENT_ROLE).strip()
    if not role or role == "plain_intern":
        return _INDEPENDENT_ROLE
    if role in _TEAM_CONTRACT_ROLES or role == _INDEPENDENT_ROLE:
        return role
    return _INDEPENDENT_ROLE


def _get_local_contract_meta(intern_name, project):
    if not intern_name:
        return {"role": _INDEPENDENT_ROLE, "team_id": ""}
    project = project or _get_intern_project(intern_name) or ""
    status_md = _get_status_md_path(intern_name, project)
    meta = _parse_status_metadata(status_md)
    return {
        "role": _normalize_contract_role(meta.get("ROLE", _INDEPENDENT_ROLE)),
        "team_id": meta.get("TEAM_ID", "") or meta.get("TEAM", ""),
    }


def _contract_roles_from_payload(payload):
    from_name = payload.get("from_intern_name", "")
    from_project = payload.get("from_project", "")
    to_name = payload.get("to_intern_name", "")
    to_project = payload.get("to_project", "")
    from_role = _normalize_contract_role(payload.get("from_role"))
    to_role = _normalize_contract_role(payload.get("to_role"))
    from_team_id = payload.get("from_team_id", "")
    to_team_id = payload.get("to_team_id", "")

    if "from_role" not in payload or "from_team_id" not in payload:
        meta = _get_local_contract_meta(from_name, from_project)
        if "from_role" not in payload:
            from_role = meta["role"]
        if "from_team_id" not in payload:
            from_team_id = meta["team_id"]
    if "to_role" not in payload or "to_team_id" not in payload:
        meta = _get_local_contract_meta(to_name, to_project)
        if "to_role" not in payload:
            to_role = meta["role"]
        if "to_team_id" not in payload:
            to_team_id = meta["team_id"]

    return {
        "from_role": from_role,
        "to_role": to_role,
        "from_team_id": from_team_id,
        "to_team_id": to_team_id,
    }


def _same_contract_team(payload, roles):
    request_team_id = payload.get("team_id", "")
    from_team_id = roles.get("from_team_id", "")
    to_team_id = roles.get("to_team_id", "")
    if request_team_id and from_team_id and request_team_id != from_team_id:
        return False
    if request_team_id and to_team_id and request_team_id != to_team_id:
        return False
    if from_team_id and to_team_id and from_team_id != to_team_id:
        return False
    return True


def _contract_reject(reason, message=None):
    result = {"status": "undeliverable", "reason": reason}
    if message:
        result["message"] = message
    return result


def _team_supervisor_only_reject():
    return _contract_reject(_TEAM_SUPERVISOR_ONLY_REASON, _TEAM_SUPERVISOR_ONLY_MESSAGE)


def _validate_independent_team_boundary(roles):
    from_role = roles.get("from_role")
    to_role = roles.get("to_role")
    if from_role == _INDEPENDENT_ROLE and to_role in _TEAM_CONTRACT_ROLES:
        return _team_supervisor_only_reject()
    if to_role == _INDEPENDENT_ROLE and from_role in _TEAM_CONTRACT_ROLES:
        return _team_supervisor_only_reject()
    return None


def _is_safe_team_id(team_id):
    return (
        isinstance(team_id, str)
        and bool(_TEAM_ID_PATTERN.fullmatch(team_id))
        and os.path.basename(team_id) == team_id
        and team_id not in (".", "..")
    )


def _load_team_contract(project, team_id):
    if not project or not _is_safe_team_id(team_id):
        return None
    try:
        path = team_mailbox.team_registry.team_json_path(project, team_id)
    except Exception:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fp:
            data = json.load(fp)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _active_team_member_names(team_data):
    names = set()
    lead = team_data.get("team_lead")
    if isinstance(lead, dict) and lead.get("status", "active") != "deleted":
        lead_name = str(lead.get("intern_name") or "")
        if lead_name:
            names.add(lead_name)
    workers = team_data.get("workers")
    if isinstance(workers, list):
        for worker in workers:
            if not isinstance(worker, dict) or worker.get("status", "active") == "deleted":
                continue
            worker_name = str(worker.get("intern_name") or "")
            if worker_name:
                names.add(worker_name)
    return names


def _validate_coordinator_team_scope(payload, roles):
    """Limit coordinator control to teams explicitly bound to that coordinator.

    The target daemon performs this check for both local and relayed peer/goal
    deliveries, so cross-machine sends cannot bypass the team.json owner.
    """
    if roles.get("from_role") != "coordinator":
        return None

    to_role = roles.get("to_role")
    if to_role == _INDEPENDENT_ROLE:
        return _contract_reject("coordinator_target_not_in_assigned_team")
    if to_role not in {"team_lead", "worker"}:
        return _contract_reject("coordinator_target_not_in_assigned_team")

    team_id = roles.get("to_team_id") or payload.get("team_id") or ""
    if not team_id:
        return _contract_reject("coordinator_target_not_in_assigned_team")

    team_data = _load_team_contract(payload.get("to_project", ""), team_id)
    if not team_data:
        return _contract_reject("coordinator_target_not_in_assigned_team")

    coordinator = team_data.get("coordinator")
    if not isinstance(coordinator, dict):
        return _contract_reject("coordinator_target_not_in_assigned_team")
    if str(coordinator.get("intern_name") or "") != payload.get("from_intern_name", ""):
        return _contract_reject("coordinator_target_not_in_assigned_team")
    if payload.get("to_intern_name", "") not in _active_team_member_names(team_data):
        return _contract_reject("coordinator_target_not_in_assigned_team")
    return None


_DELIVERY_HEALTH_REASONS = {
    "offline": "Target intern is offline or not active on this daemon.",
    "tmux_session_missing": "Target tmux session is missing.",
    "session_not_running": "Target tmux session exists but the CLI process is not running.",
    "tmux_send_failed": "Target tmux session rejected the delivery command.",
}


def _augment_delivery_health_response(result, to_name, same_machine):
    if not isinstance(result, dict) or result.get("status") != "undeliverable":
        return result
    reason = result.get("reason", "")
    if reason not in _DELIVERY_HEALTH_REASONS:
        return result

    response = dict(result)
    response.setdefault("message", _DELIVERY_HEALTH_REASONS[reason])
    if same_machine:
        response["remediation"] = {
            "same_machine": True,
            "action": "restart_session_via_daemon",
            "message": (
                f"{to_name} is on the same machine as the sender. "
                "Call the local daemon/session restart entry point to try restarting the session, then retry."
            ),
        }
    else:
        response["remediation"] = {
            "same_machine": False,
            "action": "notify_supervisor",
            "message": (
                f"{to_name} is not on the sender's machine. "
                "Notify the supervisor to restart or repair the target intern session on its host."
            ),
        }
    return response


def _augment_goal_unconfirmed_response(result):
    if not isinstance(result, dict) or result.get("reason") != "unconfirmed":
        return result

    response = dict(result)
    response.setdefault(
        "message",
        (
            "Codex did not confirm this content as an active goal. "
            "The goal content may be too complex or multi-line for `/goal [content]` handling."
        ),
    )
    response.setdefault("detail", _TMUX_SUBMIT_UNCONFIRMED_ERROR)
    response["remediation"] = {
        "action": "rewrite_goal_content_single_line_and_retry",
        "message": (
            "Rewrite the goal content to fit `/goal [content]`, preferably as one concise line, "
            "then call the goal API again. For long instructions, put details in a file or task "
            "document and set a short one-line goal that points to it."
        ),
    }
    return response


def _validate_peer_contract(payload):
    mode = payload.get("mode") or "default"
    roles = _contract_roles_from_payload(payload)
    from_role = roles["from_role"]
    to_role = roles["to_role"]

    boundary_result = _validate_independent_team_boundary(roles)
    if boundary_result:
        return boundary_result

    scope_result = _validate_coordinator_team_scope(payload, roles)
    if scope_result:
        return scope_result

    # independent-to-independent keeps the broad peer-send compatibility surface.
    if from_role == _INDEPENDENT_ROLE or to_role == _INDEPENDENT_ROLE:
        return None

    if from_role == to_role:
        return _contract_reject("same_role_team_channel_not_supported")
    if from_role == "coordinator" and to_role == "team_lead":
        if mode in {"default", "next", "stop"}:
            return None
        return _contract_reject("unsupported_mode_for_team")
    if from_role == "team_lead" and to_role == "coordinator":
        if mode == "default":
            return None
        return _contract_reject("unsupported_mode_for_team")
    if from_role == "team_lead" and to_role == "worker":
        if mode not in {"default", "next", "stop"}:
            return _contract_reject("unsupported_mode_for_team")
        if not _same_contract_team(payload, roles):
            return _contract_reject("not_same_team")
        return None
    if from_role == "worker" and to_role == "team_lead":
        return _contract_reject("worker_to_team_lead_use_mailbox")
    if from_role == "coordinator" and to_role == "worker":
        return _contract_reject("coordinator_to_worker_use_team_lead")
    if from_role == "worker" and to_role == "coordinator":
        return _contract_reject("worker_to_coordinator_use_team_lead")
    return _contract_reject("role_not_allowed")


def _validate_goal_contract(payload, same_daemon):
    roles = _contract_roles_from_payload(payload)
    from_role = roles["from_role"]
    to_role = roles["to_role"]
    same_project = (payload.get("from_project", "") == payload.get("to_project", ""))

    boundary_result = _validate_independent_team_boundary(roles)
    if boundary_result:
        return boundary_result

    scope_result = _validate_coordinator_team_scope(payload, roles)
    if scope_result:
        return scope_result

    if from_role == _INDEPENDENT_ROLE or to_role == _INDEPENDENT_ROLE:
        if from_role == _INDEPENDENT_ROLE and to_role == _INDEPENDENT_ROLE and same_daemon and same_project:
            return None
        return _contract_reject("goal_independent_same_daemon_required")
    if from_role == "coordinator" and to_role == "team_lead":
        return None
    if from_role == "coordinator" and to_role == "worker":
        return _contract_reject("coordinator_to_worker_use_team_lead")
    if from_role == "worker" and to_role == "coordinator":
        return _contract_reject("worker_to_coordinator_use_team_lead")
    if from_role == "worker" and to_role == "team_lead":
        return _contract_reject("worker_to_team_lead_use_mailbox")
    if from_role == to_role:
        return _contract_reject("same_role_team_channel_not_supported")
    return _contract_reject("unsupported_goal_target")


def _attach_local_sender_contract(payload):
    meta = _get_local_contract_meta(payload.get("from_intern_name", ""), payload.get("from_project", ""))
    payload["from_role"] = meta["role"]
    payload["from_team_id"] = meta["team_id"]


_CODEX_GOAL_TMUX_SESSION_MISSING = "tmux session not found"
_CODEX_GOAL_PROCESS_NOT_RUNNING = "Codex has exited (tmux session exists but Codex is not running)"


def _codex_goal_tmux_target(intern_name, project=""):
    if not _check_tmux_session(intern_name, project=project):
        return "", _CODEX_GOAL_TMUX_SESSION_MISSING
    if not _is_codex_process_running(intern_name, project=project):
        return "", _CODEX_GOAL_PROCESS_NOT_RUNNING
    return _tmux_target(intern_name, project=project), None


def _send_codex_goal_command_to_tmux(target, text, clear_existing_goal=False):
    try:
        _tmux_clear_input_line(target)
        time.sleep(0.05)
        if clear_existing_goal:
            _tmux_paste_text(target, "/goal clear")
            time.sleep(_TMUX_ENTER_DELAY_SECONDS)
            _tmux_send_enter(target)
            time.sleep(0.2)
            _tmux_clear_input_line(target)
            time.sleep(0.05)
        _tmux_paste_text(target, text)
        time.sleep(_TMUX_ENTER_DELAY_SECONDS)
        _tmux_send_enter(target)
    except subprocess.CalledProcessError as e:
        return False, str(e)
    return True, None


def _send_codex_goal_set_attempt(
    intern_name,
    target,
    content,
    log_ctx,
    project="",
    message_id="",
    ask_feishu=False,
):
    text = "/goal " + content
    ack_path, ack_offset = _get_codex_ack_start(intern_name, project=project)
    sent, err = _send_codex_goal_command_to_tmux(
        target,
        text,
        clear_existing_goal=not ask_feishu,
    )
    if not sent:
        return False, err

    if ack_path and _wait_for_codex_goal_ack(ack_path, ack_offset, content, timeout=1.0):
        log.info(f"[PEER] codex goal delivered {log_ctx}, ack=ok")
        return True, None
    if _codex_goal_replace_confirmation_pending(target, content):
        log.info(f"[PEER] codex goal replace confirmation pending {log_ctx}; confirming")
        if ask_feishu and _start_codex_goal_replace_feishu_confirmation(
            intern_name,
            project,
            target,
            content,
            ack_path,
            ack_offset,
            message_id,
            log_ctx,
        ):
            return True, _CODEX_GOAL_REPLACE_PENDING
        try:
            _tmux_send_enter(target)
        except subprocess.CalledProcessError as e:
            return False, str(e)
    if ack_path and _wait_for_codex_goal_ack(
        ack_path, ack_offset, content, timeout=_CODEX_GOAL_ACK_TIMEOUT_SECONDS
    ):
        log.info(f"[PEER] codex goal delivered {log_ctx}, ack=ok")
        return True, None
    panel_visible, panel_reason = _codex_goal_visible_in_panel_result(target, content)
    if panel_visible:
        log.info(f"[PEER] codex goal delivered {log_ctx}, ack=panel")
        return True, None
    log.warning(
        f"[PEER] codex goal ack failed {log_ctx}, transcript={ack_path or '-'}, "
        f"panel={panel_reason}"
    )
    return False, _TMUX_SUBMIT_UNCONFIRMED_ERROR


def _send_peer_goal_to_codex_tmux(intern_name, content, msg_id, project="", ask_feishu=False):
    target, err = _codex_goal_tmux_target(intern_name, project=project)
    if err:
        return False, err
    text = "/goal " + content
    log_ctx = f"intern={intern_name}, delivery={msg_id or '-'}, hash={_delivery_hash(text)}"
    return _send_codex_goal_set_attempt(
        intern_name,
        target,
        content,
        log_ctx,
        project=project,
        message_id=msg_id,
        ask_feishu=ask_feishu,
    )


def _send_goal_cancel_to_codex_tmux(intern_name, msg_id, project=""):
    target, err = _codex_goal_tmux_target(intern_name, project=project)
    if err:
        return False, err
    text = "/goal clear"
    ack_path, ack_offset = _get_codex_ack_start(intern_name, project=project)
    sent, err = _send_codex_goal_command_to_tmux(target, text)
    if not sent:
        return False, err

    log_ctx = f"intern={intern_name}, delivery={msg_id or '-'}, hash={_delivery_hash(text)}"
    if not ack_path:
        if _codex_goal_clear_visible_in_panel(target):
            log.info(f"[GOAL_API] codex goal canceled {log_ctx}, ack=panel")
            return True, None
        log.warning(f"[GOAL_API] codex goal cancel ack unavailable {log_ctx}")
        return False, _TMUX_SUBMIT_UNCONFIRMED_ERROR
    if _wait_for_codex_goal_ack(ack_path, ack_offset, "", timeout=_CODEX_GOAL_ACK_TIMEOUT_SECONDS):
        log.info(f"[GOAL_API] codex goal canceled {log_ctx}, ack=ok")
        return True, None
    if _codex_goal_clear_visible_in_panel(target):
        log.info(f"[GOAL_API] codex goal canceled {log_ctx}, ack=panel")
        return True, None
    log.warning(f"[GOAL_API] codex goal cancel ack failed {log_ctx}, transcript={ack_path}")
    return False, _TMUX_SUBMIT_UNCONFIRMED_ERROR


def _send_goal_resume_to_codex_tmux(intern_name, msg_id, project=""):
    target, err = _codex_goal_tmux_target(intern_name, project=project)
    if err:
        return False, err
    text = "/goal resume"
    sent, err = _send_codex_goal_command_to_tmux(target, text)
    if not sent:
        return False, err

    log.info(
        f"[GOAL_API] codex goal resume sent intern={intern_name}, "
        f"delivery={msg_id or '-'}, hash={_delivery_hash(text)}"
    )
    return True, None


def _notify_goal_api_visible(payload, action, content):
    """Send a target-group Feishu marker for direct goal API delivery."""
    if _api is None:
        log.warning("[GOAL_API_VISIBILITY] _api not initialized, skip goal marker")
        return
    to_name = payload.get("to_intern_name", "")
    chat_id = _registry.find_chat_id(to_name) if _registry else ""
    if not chat_id:
        log.warning(f"[GOAL_API_VISIBILITY] no chat_id for {to_name}, skip goal marker")
        return
    from_intern = payload.get("from_intern_name", "")
    from_project = payload.get("from_project", "")
    source = f"{from_project}/{from_intern}" if from_project else from_intern
    msg_id = payload.get("goal_id") or payload.get("msg_id") or payload.get("request_id") or "-"
    title = "🛑 goal canceled" if action == "cancel" else "🎯 goal set"
    text = (
        f"{title}\n"
        f"【goal api action={action} delivery=goal from {source} msg_id={msg_id}】"
    )
    if action != "cancel":
        text += "\nGoal:\n" + content
    _, err = _api.send_message(chat_id, text)
    if err:
        log.warning(f"[GOAL_API_VISIBILITY] send goal marker failed for {to_name}: {err}")


def _deliver_goal_locally(payload):
    """Deliver a same-daemon/same-project goal API command to a tmux intern."""
    to_name = payload.get("to_intern_name", "")
    to_project = payload.get("to_project", "")
    from_project = payload.get("from_project", "")
    action = payload.get("action") or "set"
    content = payload.get("content", "")
    goal_id = payload.get("goal_id") or payload.get("msg_id") or payload.get("request_id") or uuid.uuid4().hex

    if action not in _GOAL_API_ACTIONS:
        return {"status": "undeliverable", "reason": "unsupported_action"}
    contract_result = _validate_goal_contract(payload, same_daemon=not bool(payload.get("via_relay")))
    if contract_result:
        return contract_result
    intern_type = _get_intern_type_scoped(to_name, project=to_project)
    if not _is_tmux_intern_type(intern_type):
        return {"status": "undeliverable", "reason": "unsupported_target"}
    if not _owns_local_peer_target(to_name, to_project):
        return {"status": "undeliverable", "reason": "offline"}
    if intern_type != "codex":
        if not _check_tmux_session(to_name, project=to_project):
            return {"status": "undeliverable", "reason": "tmux_session_missing"}
        if not _is_claude_process_running(to_name, project=to_project):
            return {"status": "undeliverable", "reason": "session_not_running"}

    if action == "cancel":
        if intern_type == "codex":
            success, err = _send_goal_cancel_to_codex_tmux(to_name, goal_id, project=to_project)
        else:
            success, err = _send_peer_text_to_tmux(to_name, intern_type, "/goal clear", goal_id, project=to_project)
    elif intern_type == "codex":
        success, err = _send_peer_goal_to_codex_tmux(to_name, content, goal_id, project=to_project)
    else:
        success, err = _send_peer_text_to_tmux(to_name, intern_type, "/goal " + content, goal_id, project=to_project)

    if not success:
        log.warning(f"[GOAL_API] tmux goal send failed for {to_name}: {err}")
        if err == _CODEX_GOAL_TMUX_SESSION_MISSING:
            return {"status": "undeliverable", "reason": "tmux_session_missing"}
        if err == _CODEX_GOAL_PROCESS_NOT_RUNNING:
            return {"status": "undeliverable", "reason": "session_not_running"}
        if err in _TMUX_SUBMIT_UNCONFIRMED_ERRORS:
            return {"status": "undeliverable", "reason": "unconfirmed", "detail": err}
        return {"status": "undeliverable", "reason": "tmux_send_failed", "detail": err}

    _notify_goal_api_visible(payload, action, content)
    if action == "cancel":
        return {"status": "delivered", "kind": "goal_cancel", "goal_id": goal_id}
    return {"status": "delivered", "kind": "goal", "goal_id": goal_id}


def _goal_api_http_status(result):
    """Map goal delivery result to HTTP status.

    Goal API callers must be able to rely on transport status for the common
    success/failure split. Detailed handling still uses the JSON ``reason``.
    """
    if result.get("status") == "delivered":
        return 200
    reason = result.get("reason")
    if reason == "relay_unreachable":
        return 503
    if reason == "unknown_target":
        return 404
    return 409


def _deliver_mail_locally(payload):
    to_name = payload.get("to_intern_name", "")
    to_project = payload.get("to_project", "")
    from_name = payload.get("from_intern_name", "")
    from_project = payload.get("from_project", "")
    content = payload.get("content", "")
    if not _owns_local_mail_target(to_name, to_project):
        return {"status": "undeliverable", "reason": "offline"}
    try:
        message = team_mailbox.append_message(
            target_project=to_project,
            from_intern_name=from_name,
            from_project=from_project,
            to_intern_name=to_name,
            content=content,
            team_id=payload.get("team_id", ""),
            kind=payload.get("kind", "progress"),
            related_task=payload.get("related_task", ""),
            related_pr=payload.get("related_pr", ""),
            client_message_id=payload.get("client_message_id", ""),
        )
    except PermissionError as exc:
        return {"status": "undeliverable", "reason": str(exc)}
    except ValueError as exc:
        return {"status": "undeliverable", "reason": str(exc)}
    return {
        "status": "stored",
        "kind": "mail",
        "message_id": message["message_id"],
        "team_id": message["team_id"],
        "read_state": "unread",
    }


def _send_peer_stop_to_tmux(intern_name, intern_type, project=None):
    target = _tmux_target(intern_name, project=project)
    try:
        subprocess.run(
            ["tmux", "send-keys", "-t", target, "Escape"],
            check=True, capture_output=True
        )
        if intern_type == "codex":
            ok, reason = _finalize_active_feishu_message_for_stop(intern_name, project=project)
            if ok:
                log.info(f"[PEER] finalized active Feishu turn for Codex '{intern_name}' after peer stop ({reason})")
                _notify_intern_status_changed(intern_name, project=project)
                _push_interns_state_once()
        return {"status": "delivered", "kind": "stop"}
    except subprocess.CalledProcessError as e:
        log.warning(f"[PEER] stop tmux failed for {intern_name}: {e}")
        return {"status": "undeliverable", "reason": "tmux_send_failed", "detail": str(e)}


def _wait_for_peer_batch_codex_ack(intern_name, project, transcript_path, start_offset, text, batch_id):
    target = _tmux_target(intern_name, project=project)
    deadline = time.time() + _PEER_BATCH_ACK_TIMEOUT_SECONDS
    while True:
        if transcript_path and _codex_transcript_has_user_prompt(transcript_path, start_offset, text):
            log.info(f"[PEER_QUEUE] batch ack accepted target={project}/{intern_name} batch={batch_id}")
            return "accepted"
        if _codex_prompt_visible_in_pane(target, text):
            log.info(f"[PEER_QUEUE] batch ack visible target={project}/{intern_name} batch={batch_id}")
            return "visible"
        if time.time() >= deadline:
            log.warning(
                f"[PEER_QUEUE] batch ack missing target={project}/{intern_name} "
                f"batch={batch_id} timeout={_PEER_BATCH_ACK_TIMEOUT_SECONDS}s"
            )
            return "missing"
        time.sleep(_PEER_BATCH_ACK_POLL_SECONDS)


def _send_peer_batch_to_tmux(intern_name, intern_type, project, text, batch_id):
    if intern_type == "codex":
        ack_path, ack_offset = _get_codex_ack_start(intern_name, project=project)
        success, err = _send_to_codex_tmux(
            intern_name,
            text,
            delivery_id=batch_id,
            require_ack=False,
            project=project,
        )
        if success:
            _wait_for_peer_batch_codex_ack(
                intern_name,
                project,
                ack_path,
                ack_offset,
                text,
                batch_id,
            )
        return success, err
    return _send_to_claude_tmux(intern_name, text, delivery_id=batch_id, project=project)


class PeerDeliveryManager:
    def __init__(
        self,
        *,
        target_queue_limit=_PEER_TARGET_QUEUE_LIMIT,
        worker_count=_PEER_DELIVERY_WORKER_COUNT,
        batch_max_chars=_PEER_BATCH_MAX_CHARS,
    ):
        self._target_queue_limit = target_queue_limit
        self._worker_count = worker_count
        self._batch_max_chars = batch_max_chars
        self._lock = threading.RLock()
        self._work_queue = queue.Queue()
        self._targets = {}
        self._active_targets = set()
        self._scheduled_targets = set()
        self._seq = 0
        self._started = False
        self._stop_event = None

    def start(self, stop_event):
        with self._lock:
            if self._started:
                return
            self._started = True
            self._stop_event = stop_event
        threading.Thread(
            target=self._scheduler_loop,
            name="peer_delivery_scheduler",
            daemon=True,
        ).start()
        for idx in range(self._worker_count):
            threading.Thread(
                target=self._worker_loop,
                name=f"peer_delivery_worker_{idx}",
                daemon=True,
            ).start()

    def submit(self, payload, intern_type):
        to_name = payload.get("to_intern_name", "")
        to_project = payload.get("to_project", "")
        content = payload.get("content", "")
        mode = payload.get("mode") or "default"
        if content == "/esc":
            mode = "stop"
        msg_id = payload.get("msg_id") or payload.get("request_id") or uuid.uuid4().hex
        target_key = (to_project, to_name)

        with self._lock:
            state = self._targets.setdefault(
                target_key,
                {"jobs": [], "dedupe": {}, "dedupe_order": []},
            )
            dedupe_key = msg_id
            existing = state["dedupe"].get(dedupe_key)
            if existing:
                return dict(existing)
            if len(state["jobs"]) >= self._target_queue_limit:
                return {"status": "undeliverable", "reason": "queue_full"}

            self._seq += 1
            receipt_kind = "esc" if payload.get("content") == "/esc" else ("stop" if mode == "stop" else "queued")
            job = {
                "msg_id": msg_id,
                "seq": self._seq,
                "mode": mode,
                "receipt_kind": receipt_kind,
                "content": content,
                "payload": dict(payload, msg_id=msg_id, mode=mode),
                "intern_type": intern_type,
                "created_at": time.time(),
            }
            state["jobs"].append(job)
            result = {
                "status": "delivered",
                "kind": receipt_kind,
                "msg_id": msg_id,
                "queue_depth": len(state["jobs"]),
            }
            state["dedupe"][dedupe_key] = dict(result)
            state["dedupe_order"].append(dedupe_key)
            while len(state["dedupe_order"]) > self._target_queue_limit * 4:
                old = state["dedupe_order"].pop(0)
                state["dedupe"].pop(old, None)
            self._schedule_target_locked(target_key, state)
            return result

    def schedule_all(self):
        with self._lock:
            for target_key, state in self._targets.items():
                self._schedule_target_locked(target_key, state)

    def drain_target_for_test(self, target_key):
        with self._lock:
            state = self._targets.get(target_key)
            if not state:
                return
            if target_key in self._active_targets:
                return
            self._scheduled_targets.discard(target_key)
            self._active_targets.add(target_key)
        try:
            self._drain_target(target_key)
        finally:
            with self._lock:
                self._active_targets.discard(target_key)

    def snapshot_depth(self, target_key):
        with self._lock:
            state = self._targets.get(target_key)
            return len(state["jobs"]) if state else 0

    def metrics_snapshot(self):
        with self._lock:
            targets = []
            total_jobs = 0
            max_depth = 0
            for (project, intern_name), state in self._targets.items():
                jobs = list(state["jobs"])
                depth = len(jobs)
                total_jobs += depth
                max_depth = max(max_depth, depth)
                default_count = len([job for job in jobs if job["mode"] == "default"])
                next_count = len([job for job in jobs if job["mode"] == "next"])
                stop_count = len([job for job in jobs if job["mode"] == "stop"])
                targets.append({
                    "project": project,
                    "intern_name": intern_name,
                    "queue_depth": depth,
                    "default_count": default_count,
                    "next_count": next_count,
                    "stop_count": stop_count,
                    "active": (project, intern_name) in self._active_targets,
                    "scheduled": (project, intern_name) in self._scheduled_targets,
                })
            targets.sort(key=lambda row: (-row["queue_depth"], row["project"], row["intern_name"]))
            return {
                "target_queue_limit": self._target_queue_limit,
                "worker_count": self._worker_count,
                "active_targets": len(self._active_targets),
                "scheduled_targets": len(self._scheduled_targets),
                "total_jobs": total_jobs,
                "max_target_depth": max_depth,
                "targets": targets[:20],
            }

    def _scheduler_loop(self):
        while self._stop_event and not self._stop_event.wait(1):
            self.schedule_all()

    def _worker_loop(self):
        while self._stop_event and not self._stop_event.is_set():
            try:
                target_key = self._work_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                with self._lock:
                    self._scheduled_targets.discard(target_key)
                    self._active_targets.add(target_key)
                self._drain_target(target_key)
            finally:
                with self._lock:
                    self._active_targets.discard(target_key)
                self._work_queue.task_done()

    def _schedule_target_locked(self, target_key, state):
        if target_key in self._active_targets or target_key in self._scheduled_targets:
            return
        if not self._target_has_runnable_jobs_locked(target_key, state):
            return
        self._scheduled_targets.add(target_key)
        self._work_queue.put(target_key)

    def _target_has_runnable_jobs_locked(self, target_key, state):
        jobs = state["jobs"]
        if not jobs:
            return False
        if any(job["mode"] == "stop" for job in jobs):
            return True
        if any(job["mode"] == "default" for job in jobs):
            return True
        return not self._is_target_active(target_key)

    def _is_target_active(self, target_key):
        project, name = target_key
        return _is_turn_active(name, {_online_key(name, project), name}, project=project)

    def _drain_target(self, target_key):
        while True:
            with self._lock:
                state = self._targets.get(target_key)
                if not state:
                    return
                batch = self._take_batch_locked(target_key, state)
                if not batch:
                    return
            self._deliver_batch(target_key, batch)

    def _take_batch_locked(self, target_key, state):
        jobs = state["jobs"]
        if not jobs:
            return []

        stop_jobs = [job for job in jobs if job["mode"] == "stop"]
        if stop_jobs:
            stop_job = min(stop_jobs, key=lambda job: job["seq"])
            jobs.remove(stop_job)
            return [stop_job]

        active = self._is_target_active(target_key)
        if active:
            selected = [job for job in jobs if job["mode"] == "default"]
        else:
            defaults = [job for job in jobs if job["mode"] == "default"]
            nexts = [job for job in jobs if job["mode"] == "next"]
            selected = sorted(defaults, key=lambda job: job["seq"]) + sorted(nexts, key=lambda job: job["seq"])
        if not selected:
            return []
        for job in selected:
            jobs.remove(job)
        return selected

    def _deliver_batch(self, target_key, jobs):
        project, intern_name = target_key
        stop_jobs = [job for job in jobs if job["mode"] == "stop"]
        if stop_jobs:
            for job in sorted(stop_jobs, key=lambda item: item["seq"]):
                result = _send_peer_stop_to_tmux(intern_name, job["intern_type"], project=project)
                log.info(
                    f"[PEER_QUEUE] stop delivered target={project}/{intern_name} "
                    f"msg_id={job['msg_id']} result={result}"
                )
            return

        ordered = sorted(jobs, key=lambda job: (0 if job["mode"] == "default" else 1, job["seq"]))
        self._deliver_text_jobs(target_key, ordered)

    def _deliver_text_jobs(self, target_key, jobs):
        project, intern_name = target_key
        batch_id = f"peer-batch-{uuid.uuid4().hex}"
        text = _format_peer_batch_text(batch_id, jobs)
        if len(text.encode("utf-8")) > self._batch_max_chars and len(jobs) > 1:
            midpoint = max(1, len(jobs) // 2)
            self._deliver_text_jobs(target_key, jobs[:midpoint])
            self._deliver_text_jobs(target_key, jobs[midpoint:])
            return

        intern_type = jobs[0]["intern_type"]
        started = time.time()
        success, err = _send_peer_batch_to_tmux(intern_name, intern_type, project, text, batch_id)
        elapsed_ms = int((time.time() - started) * 1000)
        msg_ids = [job["msg_id"] for job in jobs]
        if not success:
            log.warning(
                f"[PEER_QUEUE] batch injection failed target={project}/{intern_name} "
                f"batch={batch_id} msg_ids={msg_ids} elapsed_ms={elapsed_ms} err={err}"
            )
            return
        log.info(
            f"[PEER_QUEUE] batch injected target={project}/{intern_name} "
            f"batch={batch_id} msg_ids={msg_ids} chars={len(text)} elapsed_ms={elapsed_ms}"
        )


_peer_delivery_manager = PeerDeliveryManager()


def _deliver_peer_locally(payload):
    """task213: deliver a peer message to a target intern owned by this daemon.

    Returns ``{"status": "delivered"|"undeliverable", "reason"?: str, "kind"?: str}``.
    Caller (HTTP handler for same-machine, RelayClient WS handler for cross-machine)
    is responsible for relay communication and HTTP response shaping. This function
    only encapsulates the local delivery rules (tmux/copilot/esc/busy/attachments).

    mode=next queues while the target turn is active; default intentionally
    sends to the CLI so its pending input behavior can take over.
    """
    to_name = payload.get("to_intern_name", "")
    content = payload.get("content", "")
    attachments = payload.get("attachments") or []
    to_project = payload.get("to_project", "")
    msg_id = payload.get("msg_id") or payload.get("request_id") or uuid.uuid4().hex
    mode = payload.get("mode") or "default"
    if mode not in _PEER_DELIVERY_MODES:
        return {"status": "undeliverable", "reason": "unsupported_mode"}
    contract_result = _validate_peer_contract(payload)
    if contract_result:
        return contract_result

    intern_type = _get_intern_type_scoped(to_name, project=to_project)

    if not _is_tmux_intern_type(intern_type) and intern_type != "copilot":
        return {"status": "undeliverable", "reason": "unsupported_target"}
    if not _owns_local_peer_target(to_name, to_project):
        return {"status": "undeliverable", "reason": "offline"}

    if intern_type == "copilot":
        if mode != "default":
            return {"status": "undeliverable", "reason": "unsupported_mode"}
        if attachments:
            return {"status": "undeliverable", "reason": "unsupported_attachment_target"}
        push_payload = {
            "type": "peer_message",
            "intern_name": to_name,
            "text": _format_peer_text(payload, content),
            "message_id": msg_id,
            "project": to_project,
            "to_project": to_project,
            "from_intern_name": payload.get("from_intern_name", ""),
            "from_project": payload.get("from_project", ""),
        }
        delivered = _ws_server.route_to_active(to_name, push_payload, project=to_project) if _ws_server else False
        if not delivered:
            return {"status": "undeliverable", "reason": "offline"}
        return {"status": "delivered"}

    if not _check_tmux_session(to_name, project=to_project):
        return {"status": "undeliverable", "reason": "tmux_session_missing"}
    process_check = _is_claude_process_running if intern_type == "claude" else _is_codex_process_running
    if not process_check(to_name, project=to_project):
        return {"status": "undeliverable", "reason": "session_not_running"}

    if attachments and mode != "stop" and content != "/esc":
        try:
            _persist_inbound_attachments(to_name, msg_id, attachments, project=to_project)
        except Exception as e:
            log.error(f"[PEER] persist attachments failed for {to_name}: {e}", exc_info=True)
            return {"status": "undeliverable", "reason": "attachment_persist_failed"}

    result = _peer_delivery_manager.submit(payload, intern_type)
    if result.get("status") == "delivered":
        log.info(
            f"[PEER] queued message for '{to_project}/{to_name}', "
            f"mode={mode}, kind={result.get('kind')}, depth={result.get('queue_depth')}"
        )
    return result


def _build_state_payload(msg_type):
    """Build the full state payload sent to the relay.

    Same shape for ``sync_online`` (event-driven, triggers light control) and
    ``interns_state`` (5s periodic, memory-only). The only difference is ``type``.
    """
    all_interns = _iter_registry_entries(_registry)
    active_copilot = _ws_server.get_active_intern_keys() if _ws_server else set()
    current_online = []
    online_names = set()
    for item in all_interns:
        name = item["name"]
        chat_id = item["chat_id"]
        project = item.get("project") or ""
        intern_type = _get_intern_type_scoped(name, project=project)
        project = _get_intern_project_scoped(name, project=project)
        if _is_tmux_intern_type(intern_type):
            if _is_intern_online(name, project=project):
                current_online.append({"name": name, "chat_id": chat_id, "type": intern_type, "project": project})
                online_names.add(_online_key(name, project))
                online_names.add(name)
        elif _online_key(name, project) in active_copilot:
            current_online.append({"name": name, "chat_id": chat_id, "type": intern_type, "project": project})
            online_names.add(_online_key(name, project))
            online_names.add(name)
    return {
        "type": msg_type,
        "online_interns": current_online,
        "resources": _collect_resources(),
        "interns_dynamic": _collect_interns_dynamic(online_names),
        "warnings": _collect_daemon_warnings(),
        "metrics": {
            "runtime": _daemon_metrics.snapshot(),
            "peer_delivery": _peer_delivery_manager.metrics_snapshot(),
        },
    }


def _refresh_lights():
    """Send full online set to relay (stateless, no local diff).

    Scans tmux for Claude + WS active set for Copilot, sends complete list to relay.
    Sends extended format with chat_id+type so relay can auto-register if needed.
    Relay computes diff and updates feishu group lights.
    """
    if not _registry:
        return

    if _relay_client and _relay_client.connected:
        msg = _build_state_payload("sync_online")
        _relay_client.send(msg)
        log.info(f"[LIGHT] sync_online sent: {[i['name'] for i in msg['online_interns']]}")
    else:
        log.warning("[LIGHT] relay client not connected, skipping light sync")


def _refresh_lights_for_intern(intern_name, project):
    """Refresh global online set, then repair one confirmed-online intern route."""
    _refresh_lights()
    if not intern_name:
        return
    if not project:
        raise ValueError("project required for intern light refresh")
    if not _relay_client or not _relay_client.connected:
        return
    online = _is_intern_online(intern_name, project=project)
    if not online:
        log.info(f"[LIGHT] request_refresh skip online repair for '{intern_name}': not live")
        return
    _relay_client.send_intern_online(intern_name, project=project)
    _notify_intern_status_changed(intern_name, project=project)
    log.info(f"[LIGHT] request_refresh sent intern_online repair for '{intern_name}'")


OFFLINE_NOTIFICATION_GRACE_SECONDS = 1.0


def _handle_intern_offline_notification(
        intern_name, project, grace_seconds=OFFLINE_NOTIFICATION_GRACE_SECONDS):
    """Apply a launcher offline hint only if the intern is still not live."""
    if grace_seconds > 0:
        time.sleep(grace_seconds)
    try:
        if _is_intern_online(intern_name, project=project):
            log.info(
                "[HTTP] Intern offline notification ignored: "
                f"{_online_key(intern_name, project)} still live")
            return False
    except Exception as exc:
        log.debug(
            "[HTTP] offline notification liveness check failed for "
            f"{_online_key(intern_name, project)}: {exc}")
    if _relay_client and _relay_client.connected:
        _relay_client.send_intern_offline(intern_name, project=project)
    _notify_intern_status_changed(intern_name, project=project)
    return True


def _report_interns_state(stop_event, interval=5):
    """Periodic (default 5s) interns_state push to relay.

    Same payload as ``_refresh_lights`` but ``type=interns_state`` — relay updates
    its in-memory registry only and does not touch the feishu API, so the
    dashboard stays fresh without amplifying light-control calls.
    """
    while not stop_event.is_set():
        try:
            _start_pending_restart_processor()
            if _registry and _relay_client and _relay_client.connected:
                msg = _build_state_payload("interns_state")
                _relay_client.send(msg)
        except Exception as e:
            log.debug(f"[STATE] interns_state push failed: {e}")
        stop_event.wait(interval)


# ══════════════════════════════════════════
# HTTP API server
# ══════════════════════════════════════════

# Global references set in main()
_api = None
_registry = None
_workspace_cache = None
_ws_server = None
_shutdown_event = None

# ── 交互式问答队列（AskUserQuestion / ExitPlanMode） ──
import threading as _q_threading
_pending_questions = {}   # project:intern_name → {"questions": [...], "tool_name": str, "answer": None|dict, "event": Event}
_pq_lock = _q_threading.Lock()
_codex_rui_watchers = {}  # (project, intern_name, transcript_path) → Thread
_codex_rui_seen_calls = set()
_codex_rui_lock = _q_threading.Lock()
_CODEX_RUI_WATCH_TIMEOUT = 6 * 3600
_CODEX_RUI_PRE_TOOL_BACKFILL_TIMEOUT = 60
_CODEX_RUI_PRE_TOOL_LOOKBACK_BYTES = 256 * 1024
_CODEX_RUI_PRE_TOOL_LOOKBACK_SECONDS = 300
_CODEX_RUI_PRE_TOOL_ADOPT_GRACE = 60
_QUESTION_STORE_SCHEMA = "intern-agents.pending-questions.v1"
_QUESTION_TOMBSTONE_TTL_SECONDS = 24 * 3600
_QUESTION_DEFAULT_TIMEOUT_SECONDS = _CODEX_RUI_WATCH_TIMEOUT
_question_store = None
_question_store_loaded = False


def _empty_question_store():
    return {
        "schema": _QUESTION_STORE_SCHEMA,
        "updated_at": _runtime_now_iso(),
        "entries": {},
        "active": {},
    }


def _question_store_path():
    return os.fspath(daemon_runtime_dir(WORK_AGENTS_ROOT) / "pending_questions.json")


def _question_entry_key(project, intern_name, question_id):
    return f"{project}:{intern_name}:{question_id}"


def _active_question_key(project, intern_name):
    return _online_key(intern_name, project)


def _question_entry_from_store_locked(project, intern_name, question_id):
    if not question_id:
        return None
    _ensure_question_store_loaded_locked()
    record = (_question_store or {}).get("entries", {}).get(
        _question_entry_key(project, intern_name, question_id)
    )
    return record if isinstance(record, dict) else None


def _serialize_question_entry(entry):
    data = {}
    for key, value in (entry or {}).items():
        if key == "event":
            continue
        data[key] = value
    data.setdefault("status", "pending")
    data.setdefault("invalid_reason", "")
    data.setdefault("invalid_detail", "")
    data.setdefault("created_at", time.time())
    data["updated_at"] = time.time()
    return data


def _hydrate_question_entry(record):
    entry = dict(record or {})
    entry["event"] = _q_threading.Event()
    if entry.get("answer") is not None:
        entry["event"].set()
    return entry


def _persist_question_store_locked():
    global _question_store
    if _question_store is None:
        _question_store = _empty_question_store()
    _question_store["schema"] = _QUESTION_STORE_SCHEMA
    _question_store["updated_at"] = _runtime_now_iso()
    entries = _question_store.setdefault("entries", {})
    now = time.time()
    stale_keys = []
    for key, record in list(entries.items()):
        if not isinstance(record, dict):
            stale_keys.append(key)
            continue
        status = record.get("status", "pending")
        settled_at = float(record.get("settled_at") or 0)
        if status != "pending" and settled_at and now - settled_at > _QUESTION_TOMBSTONE_TTL_SECONDS:
            stale_keys.append(key)
    for key in stale_keys:
        entries.pop(key, None)
    active = _question_store.setdefault("active", {})
    for active_key, question_id in list(active.items()):
        record = None
        for candidate in entries.values():
            if not isinstance(candidate, dict):
                continue
            if candidate.get("question_id") != question_id:
                continue
            if _active_question_key(candidate.get("project", ""), candidate.get("intern_name", "")) == active_key:
                record = candidate
                break
        if not record or record.get("status") != "pending":
            active.pop(active_key, None)
    _write_json_file_atomic(_question_store_path(), _question_store, mode=0o600)


def _ensure_question_store_loaded_locked():
    global _question_store, _question_store_loaded
    if _question_store_loaded:
        return
    path = _question_store_path()
    try:
        with open(path, "r", encoding="utf-8") as fp:
            store = json.load(fp)
        if not isinstance(store, dict) or store.get("schema") != _QUESTION_STORE_SCHEMA:
            store = _empty_question_store()
    except FileNotFoundError:
        store = _empty_question_store()
    except Exception as exc:
        log.warning(f"[QUESTION] Failed to load pending question store {path}: {exc}")
        store = _empty_question_store()
    store.setdefault("entries", {})
    store.setdefault("active", {})
    _question_store = store
    _question_store_loaded = True
    now = time.time()
    changed = False
    for entry_key, record in list(store.get("entries", {}).items()):
        if not isinstance(record, dict):
            store["entries"].pop(entry_key, None)
            changed = True
            continue
        status = record.get("status", "pending")
        if status != "pending":
            continue
        deadline_at = float(record.get("deadline_at") or 0)
        if deadline_at and deadline_at <= now:
            record["status"] = "invalidated"
            record["invalid_reason"] = "daemon_unavailable_until_timeout"
            record["invalid_detail"] = "pending question expired while daemon was unavailable or restarting"
            record["settled_at"] = now
            record["updated_at"] = now
            changed = True
            continue
        if record.get("delivery_state") == "sending" and not record.get("message_id"):
            record["status"] = "invalidated"
            record["invalid_reason"] = "daemon_restarted_during_card_delivery"
            record["invalid_detail"] = "daemon restarted before card delivery produced a message_id"
            record["settled_at"] = now
            record["updated_at"] = now
            changed = True
            continue
        project = record.get("project", "")
        intern_name = record.get("intern_name", "")
        if project and intern_name:
            _pending_questions[_pending_question_key(intern_name, project)] = _hydrate_question_entry(record)
    if changed:
        _persist_question_store_locked()


def _upsert_question_entry_locked(entry):
    global _question_store
    _ensure_question_store_loaded_locked()
    if not entry:
        return
    project = entry.get("project", "")
    intern_name = entry.get("intern_name", "")
    question_id = entry.get("question_id", "")
    if not project or not intern_name or not question_id:
        return
    record = _serialize_question_entry(entry)
    entry_key = _question_entry_key(project, intern_name, question_id)
    _question_store.setdefault("entries", {})[entry_key] = record
    active_key = _active_question_key(project, intern_name)
    if record.get("status", "pending") == "pending":
        _question_store.setdefault("active", {})[active_key] = question_id
    elif _question_store.setdefault("active", {}).get(active_key) == question_id:
        _question_store["active"].pop(active_key, None)
    _persist_question_store_locked()


def _mark_question_status_locked(entry, status, reason="", detail=""):
    if not entry:
        return
    entry["status"] = status
    entry["updated_at"] = time.time()
    if status != "pending" and not entry.get("settled_at"):
        entry["settled_at"] = time.time()
    if reason:
        entry["invalid_reason"] = reason
    if detail:
        entry["invalid_detail"] = detail
    _upsert_question_entry_locked(entry)


def _missing_question_poll_response(project, intern_name, question_id, reason="question_state_missing_after_restart", current_question_id=""):
    resp = {
        "status": "missing",
        "question_id": question_id,
        "reason": reason,
        "message": "daemon 重启或状态丢失后未找到该问题卡片状态",
        "guidance": "请重新发起问题",
    }
    if current_question_id:
        resp["current_question_id"] = current_question_id
    return resp


def _question_signature(questions):
    """Stable identity for equivalent question payloads from hook and transcript."""
    payload = json.dumps(questions or [], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _question_channel(tool_name, metadata):
    if isinstance(metadata, dict) and metadata.get("codex_tui"):
        return "codex_tui"
    if tool_name == "request_user_input":
        source = (metadata or {}).get("source") if isinstance(metadata, dict) else ""
        if source == "pre_tool_hook":
            return "pre_tool"
    return "hook"


def _pending_question_key(intern_name, project):
    if not project:
        raise ValueError("project required for pending question")
    return _online_key(intern_name, project)


def _get_pending_question_locked(intern_name, project, question_id=""):
    _ensure_question_store_loaded_locked()
    entry = _pending_questions.get(_pending_question_key(intern_name, project))
    if question_id and entry and entry.get("question_id") != question_id:
        return None
    return entry


def _remove_pending_question_locked(intern_name, project, entry=None):
    key = _pending_question_key(intern_name, project)
    current = _pending_questions.get(key)
    if entry is None or current is entry:
        _pending_questions.pop(key, None)
        removed = entry or current
        if removed:
            _ensure_question_store_loaded_locked()
            active_key = _active_question_key(project, intern_name)
            if _question_store.setdefault("active", {}).get(active_key) == removed.get("question_id"):
                _question_store["active"].pop(active_key, None)
                _persist_question_store_locked()


def _poll_pending_question(intern_name, project, question_id=""):
    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
        active_entry = _pending_questions.get(_pending_question_key(intern_name, project))
        stored_record = _question_entry_from_store_locked(project, intern_name, question_id)
    if not entry:
        if stored_record:
            status = stored_record.get("status", "pending")
            if status == "answered":
                return {
                    "status": "answered",
                    "answers": stored_record.get("answer") or {},
                    "question_id": stored_record.get("question_id", question_id),
                    "owner": stored_record.get("owner", "hook"),
                }
            if status in ("invalidated", "timed_out", "cancelled", "superseded", "delivery_failed"):
                reason = stored_record.get("invalid_reason") or status
                _update_question_card_to_invalid(
                    intern_name,
                    reason,
                    detail=stored_record.get("invalid_detail") or "",
                    project=project,
                    question_id=stored_record.get("question_id", question_id),
                )
                return {
                    "status": "invalidated",
                    "question_id": stored_record.get("question_id", question_id),
                    "reason": reason,
                    "message": _question_invalid_message(reason),
                    "guidance": "请重新发起问题",
                }
        if question_id:
            current_question_id = (active_entry or {}).get("question_id", "")
            reason = "stale_or_unknown_question_id" if current_question_id else "question_state_missing_after_restart"
            return _missing_question_poll_response(project, intern_name, question_id, reason, current_question_id)
        return {"status": "none"}
    if entry["answer"] is not None:
        answer_data = entry["answer"]
        owner = entry.get("owner", "hook")
        response = {
            "status": "answered",
            "answers": answer_data,
            "question_id": entry.get("question_id", ""),
            "owner": owner,
        }
        if owner != "codex_tui":
            if entry.get("tool_name") == _CODEX_GOAL_REPLACE_TOOL:
                response["cleanup_deferred"] = "codex_goal_replace_waiter"
                return response
            defer_cleanup = (
                entry.get("tool_name") == "request_user_input"
                and owner == "pre_tool"
                and (entry.get("metadata") or {}).get("transcript_path")
            )
            with _pq_lock:
                current = _get_pending_question_locked(intern_name, project, entry.get("question_id", ""))
                if current is entry:
                    if defer_cleanup:
                        if not current.get("pre_tool_answered_poll_at"):
                            current["pre_tool_answered_poll_at"] = time.time()
                            current["updated_at"] = time.time()
                            response["cleanup_deferred"] = "codex_tui_adopt_grace"
                            _upsert_question_entry_locked(current)
                            _schedule_pre_tool_codex_cleanup(
                                intern_name,
                                project,
                                current.get("question_id", ""),
                            )
                    else:
                        _mark_question_status_locked(current, "answered")
                        _remove_pending_question_locked(intern_name, project, entry)
        return response
    return {
        "status": "pending",
        "question_id": entry.get("question_id", ""),
        "owner": entry.get("owner", "hook"),
        "message_id": entry.get("message_id", ""),
        "delivery_state": entry.get("delivery_state", ""),
        "delivery": entry.get("delivery_kind", ""),
    }


def _options_description_md(options):
    """把 options[].description 拼成 markdown bullet list。
    返回 None 表示没有任何 option 带 description（调用方可省略渲染）。
    """
    lines = []
    for opt in options:
        if not isinstance(opt, dict):
            continue
        desc = opt.get("description", "")
        if not desc:
            continue
        label = opt.get("label", "")
        star = " ⭐" if opt.get("recommended", False) else ""
        lines.append(f"- **{label}**{star} — {desc}")
    return "\n".join(lines) if lines else None


def _format_question_card(intern_name, tool_name, questions, question_id=""):
    """Build interactive card JSON for AskUserQuestion / ExitPlanMode.

    Single question with options → buttons for quick select + form for free text.
    Single question without options → form with input.
    Multi question → form with select/input per question + submit.
    """
    if tool_name == "ExitPlanMode":
        title = f"📋 {intern_name} 的方案已完成规划"
        template = "blue"
    elif tool_name == "request_user_input":
        # Codex CLI 的等价工具，标题区分一下来源便于主管识别
        title = f"❓ {intern_name}（Codex）有问题需要确认"
        template = "purple"
    elif tool_name == _CODEX_GOAL_REPLACE_TOOL:
        title = f"🎯 {intern_name} goal 替换确认"
        template = "orange"
    else:
        title = f"❓ {intern_name} 有问题需要确认"
        template = "purple"

    # Build question_keys for form submission metadata
    question_keys = []
    for q in questions:
        qk = q.get("question", q.get("header", f"Q{len(question_keys)+1}"))
        question_keys.append(qk)

    elements = []

    if len(questions) == 1:
        # ── Single question ──
        q = questions[0]
        header = q.get("header", "")
        question = q.get("question", "")
        options = q.get("options", [])
        multi_select = q.get("multiSelect", False)
        q_text = f"【{header}】{question}" if header else question

        # Question text
        elements.append({
            "tag": "div",
            "text": {"tag": "lark_md", "content": f"**{q_text}**"}
        })

        # Options descriptions — 独立可见 text block，不被 button/下拉宽度截断
        desc_md = _options_description_md(options)
        if desc_md:
            elements.append({
                "tag": "div",
                "text": {"tag": "lark_md", "content": desc_md}
            })

        if options and not multi_select:
            # Quick-select buttons (outside form) — 按钮上只放 label，description 在上方 text block 里已展示
            actions = []
            for opt in options:
                label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
                recommended = opt.get("recommended", False) if isinstance(opt, dict) else False
                desc = opt.get("description", "") if isinstance(opt, dict) else ""
                url = opt.get("url", "") if isinstance(opt, dict) else ""
                btn_text = f"{label} — {desc}" if desc else label
                btn_type = "primary" if recommended else "default"

                btn = {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": label},
                    "type": btn_type,
                }
                if url:
                    btn["multi_url"] = {
                        "url": url,
                        "pc_url": "",
                        "android_url": "",
                        "ios_url": "",
                    }
                else:
                    btn["value"] = {
                        "intern_name": intern_name,
                        "question_id": question_id,
                        "question_key": question_keys[0],
                        "answer": label,
                        "question_title": q_text,
                    }
                actions.append(btn)
            elements.append({"tag": "action", "actions": actions})

        # Free text input form (multiSelect path puts multi_select_static inside form)
        elements.append({"tag": "hr"})
        form_elements = []

        if options and multi_select:
            # multi_select_static 放 form 里，提交时返回 list of labels
            select_options = []
            for opt in options:
                label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
                recommended = opt.get("recommended", False) if isinstance(opt, dict) else False
                disp = label + (" ⭐" if recommended else "")
                select_options.append({
                    "text": {"tag": "plain_text", "content": disp},
                    "value": label,
                })
            form_elements.append({
                "tag": "multi_select_static",
                "name": "q_0_multiselect",
                "placeholder": {"tag": "plain_text", "content": "可勾选多项..."},
                "options": select_options,
            })

        form_elements.extend([
            {
                "tag": "input",
                "name": "q_0_input",
                "placeholder": {"tag": "plain_text", "content": "输入你的回答..."},
                "label": {
                    "tag": "plain_text",
                    "content": "✍️ 或输入自定义回答：" if options else "✍️ 输入回答：",
                },
                "label_position": "top",
            },
            {
                "tag": "button",
                "text": {"tag": "lark_md", "content": "提交"},
                "type": "primary",
                "action_type": "form_submit",
                "name": "submit",
                "value": {
                    "intern_name": intern_name,
                    "question_id": question_id,
                    "question_keys": question_keys,
                    "question_title": q_text,
                }
            }
        ])
        elements.append({
            "tag": "form",
            "name": "free_text_form",
            "elements": form_elements,
        })

    else:
        # ── Multi question → all in form ──
        form_elements = []
        for i, q in enumerate(questions):
            header = q.get("header", "")
            question = q.get("question", "")
            options = q.get("options", [])
            multi_select = q.get("multiSelect", False)
            q_text = f"【{header}】{question}" if header else question

            # Question text (div not allowed directly in form, use column_set>column>markdown)
            form_elements.append({
                "tag": "column_set",
                "flex_mode": "none",
                "background_style": "default",
                "columns": [{
                    "tag": "column",
                    "width": "weighted",
                    "weight": 1,
                    "vertical_align": "top",
                    "elements": [{
                        "tag": "markdown",
                        "content": f"**{q_text}**"
                    }]
                }]
            })

            # Options descriptions — 独立可见，不被 select 下拉宽度截断
            desc_md = _options_description_md(options)
            if desc_md:
                form_elements.append({
                    "tag": "column_set",
                    "flex_mode": "none",
                    "background_style": "default",
                    "columns": [{
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "vertical_align": "top",
                        "elements": [{
                            "tag": "markdown",
                            "content": desc_md
                        }]
                    }]
                })

            if options:
                select_options = []
                for opt in options:
                    label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
                    recommended = opt.get("recommended", False) if isinstance(opt, dict) else False
                    disp = label + (" ⭐" if recommended else "")
                    select_options.append({
                        "text": {"tag": "plain_text", "content": disp},
                        "value": label,
                    })
                if multi_select:
                    form_elements.append({
                        "tag": "multi_select_static",
                        "name": f"q_{i}_multiselect",
                        "placeholder": {"tag": "plain_text", "content": "可勾选多项..."},
                        "options": select_options,
                    })
                else:
                    form_elements.append({
                        "tag": "select_static",
                        "name": f"q_{i}_select",
                        "placeholder": {"tag": "plain_text", "content": "请选择..."},
                        "options": select_options,
                    })

            # Text input for custom answer (always present; multi-select 场景仅作 fallback)
            form_elements.append({
                "tag": "input",
                "name": f"q_{i}_input",
                "placeholder": {
                    "tag": "plain_text",
                    "content": "或输入自定义回答（优先于下拉/多选）" if options else "输入回答...",
                },
            })

        # Submit button
        form_elements.append({
            "tag": "button",
            "text": {"tag": "lark_md", "content": "提交所有回答"},
            "type": "primary",
            "action_type": "form_submit",
            "name": "submit",
            "value": {
                "intern_name": intern_name,
                "question_id": question_id,
                "question_keys": question_keys,
            }
        })

        elements.append({
            "tag": "form",
            "name": "multi_question_form",
            "elements": form_elements,
        })

    # Hint
    elements.append({"tag": "hr"})
    if len(questions) == 1 and questions[0].get("options"):
        hint = "💡 点击按钮/表单提交；卡片不可用时再用 /answer 1 或 /answer <自定义内容>"
    elif len(questions) > 1:
        hint = "💡 选择或输入回答后提交；卡片不可用时再用 /answer 1:答案 2:答案"
    else:
        hint = "💡 输入回答后提交；卡片不可用时再用 /answer <回复内容>"

    elements.append({
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": hint}]
    })

    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": template,
            "title": {"tag": "plain_text", "content": title}
        },
        "elements": elements
    }


def _build_answered_card(intern_name, tool_name, answers, source="飞书"):
    """Build a read-only card showing the question has been answered."""
    if tool_name == "ExitPlanMode":
        title = f"📋 {intern_name} 的方案 — ✅ 已回答"
    elif tool_name == "request_user_input":
        title = f"❓ {intern_name}（Codex）的问题 — ✅ 已回答"
    elif tool_name == _CODEX_GOAL_REPLACE_TOOL:
        title = f"🎯 {intern_name} goal 替换确认 — ✅ 已处理"
    else:
        title = f"❓ {intern_name} 的问题 — ✅ 已回答"

    # Format answers for display
    if "_local" in answers and len(answers) == 1:
        answer_text = answers["_local"]
    else:
        def _display(v):
            if isinstance(v, list):
                return "、".join(str(x) for x in v)
            return v
        answer_lines = [f"**{k}**: {_display(v)}" for k, v in answers.items()]
        answer_text = "\n".join(answer_lines) if answer_lines else str(answers)

    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"💬 **回答内容：**\n{answer_text}"}},
        {"tag": "hr"},
        {"tag": "note", "elements": [{"tag": "plain_text", "content": f"✅ 已通过{source}回答"}]},
    ]
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": "green", "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }


def _update_question_card(intern_name, answers, source="飞书", project="", question_id=""):
    """Update the Feishu card to show answered state (does NOT pop pending entry)."""
    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
    if not entry:
        log.warning(f"[QUESTION] No pending entry to update answered card for {intern_name} question_id={question_id or '-'}")
        return
    message_id = entry.get("message_id")
    tool_name = entry.get("tool_name", "AskUserQuestion")
    if message_id and _api:
        card_json = _build_answered_card(intern_name, tool_name, answers, source)
        err = _api.update_interactive_card(message_id, card_json)
        if err:
            log.warning(f"[QUESTION] Failed to update card for {intern_name}: {err}")
        else:
            log.info(
                f"[QUESTION] Updated card for {intern_name} to answered state "
                f"question_id={entry.get('question_id', '-')} owner={entry.get('owner', '-')}"
            )


def _build_timeout_card(intern_name, tool_name, hours):
    """Build a read-only card showing the question has timed out."""
    if tool_name == "ExitPlanMode":
        title = f"📋 {intern_name} 的方案 — ⏰ 已超时"
    elif tool_name == "request_user_input":
        title = f"❓ {intern_name}（Codex）的问题 — ⏰ 已超时"
    elif tool_name == _CODEX_GOAL_REPLACE_TOOL:
        title = f"🎯 {intern_name} goal 替换确认 — ⏰ 已超时"
    else:
        title = f"❓ {intern_name} 的问题 — ⏰ 已超时"

    elements = [
        {"tag": "div", "text": {"tag": "lark_md",
                                "content": f"⏰ **{hours} 小时内未收到回复，问题已超时**\n\n请到 tmux 终端查看 intern 当前状态，或重新发起问题。"}},
        {"tag": "hr"},
        {"tag": "note", "elements": [{"tag": "plain_text", "content": "该卡片已失效，点击不会再提交回答"}]},
    ]
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": "red", "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }


def _question_invalid_message(reason):
    messages = {
        "timed_out": "问题已超时，卡片已失效",
        "daemon_unavailable_until_timeout": "daemon 在等待期间不可用，问题已失效",
        "expired_while_daemon_down": "daemon 重启/不可用期间问题已过期",
        "daemon_restarted_during_card_delivery": "daemon 在卡片发送期间重启，无法确认原卡片状态",
        "question_state_missing_after_restart": "daemon 重启后未找到该问题状态",
        "superseded_by_new_question": "该问题已被新的问题取代",
        "stale_or_unknown_question_id": "这是旧卡片或未知问题，无法继续提交",
        "cancelled": "问题已取消",
        "delivery_failed": "问题卡片发送失败，无法继续提交",
        "codex_tui_answer_verification_failed": "回答未能安全提交到 Codex TUI",
    }
    return messages.get(reason or "", "问题卡片已失效")


def _build_invalid_question_card(intern_name, tool_name, reason, detail="", guidance="请重新发起问题"):
    if tool_name == "ExitPlanMode":
        prefix = f"📋 {intern_name} 的方案"
    elif tool_name == "request_user_input":
        prefix = f"❓ {intern_name}（Codex）的问题"
    elif tool_name == _CODEX_GOAL_REPLACE_TOOL:
        prefix = f"🎯 {intern_name} goal 替换确认"
    else:
        prefix = f"❓ {intern_name} 的问题"
    message = _question_invalid_message(reason)
    content = f"⚠️ **{message}**\n\n处理建议：{guidance}"
    if detail:
        content += f"\n\n原因细节：`{detail}`"
    return {
        "header": {
            "template": "orange" if reason == "superseded_by_new_question" else "red",
            "title": {"tag": "plain_text", "content": f"{prefix} — 已失效"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": content}},
            {"tag": "hr"},
            {"tag": "note", "elements": [{"tag": "plain_text", "content": "该卡片已失效，点击不会再提交回答"}]},
        ],
    }


def _update_question_card_to_invalid(intern_name, reason, detail="", project="", question_id="", guidance="请重新发起问题"):
    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
        if not entry and question_id:
            record = _question_entry_from_store_locked(project, intern_name, question_id)
            entry = _hydrate_question_entry(record) if record else None
    if not entry:
        log.warning(f"[QUESTION] No pending entry to update invalid card for {intern_name} question_id={question_id or '-'}")
        return
    message_id = entry.get("message_id")
    tool_name = entry.get("tool_name", "AskUserQuestion")
    if message_id and _api:
        card_json = _build_invalid_question_card(
            intern_name,
            tool_name,
            reason,
            detail=detail,
            guidance=guidance,
        )
        err = _api.update_interactive_card(message_id, card_json)
        if err:
            log.warning(f"[QUESTION] Failed to update card to invalid for {intern_name}: {err}")
        else:
            log.info(
                f"[QUESTION] Updated card for {intern_name} to invalid state "
                f"question_id={entry.get('question_id', '-')} reason={reason}"
            )


def _update_question_card_to_timeout(intern_name, hours, project="", question_id=""):
    """Update the Feishu card to show timeout state (does NOT pop pending entry)."""
    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
    if not entry:
        log.warning(f"[QUESTION] No pending entry to update timeout card for {intern_name} question_id={question_id or '-'}")
        return
    message_id = entry.get("message_id")
    tool_name = entry.get("tool_name", "AskUserQuestion")
    if message_id and _api:
        card_json = _build_timeout_card(intern_name, tool_name, hours)
        err = _api.update_interactive_card(message_id, card_json)
        if err:
            log.warning(f"[QUESTION] Failed to update card to timeout for {intern_name}: {err}")
        else:
            log.info(f"[QUESTION] Updated card for {intern_name} to timeout state ({hours}h)")


def _format_question_feishu(intern_name, tool_name, questions):
    """将 AskUserQuestion / ExitPlanMode 的问题格式化为飞书消息文本。"""
    lines = []
    if tool_name == "ExitPlanMode":
        lines.append(f"📋 {intern_name} 的方案已完成规划，请选择执行方式：")
    elif tool_name == "request_user_input":
        lines.append(f"❓ {intern_name}（Codex）有问题需要确认：")
    else:
        lines.append(f"❓ {intern_name} 有问题需要确认：")
    lines.append("")

    for i, q in enumerate(questions, 1):
        header = q.get("header", "")
        question = q.get("question", "")
        options = q.get("options", [])

        if header:
            lines.append(f"【{header}】{question}")
        else:
            lines.append(f"{i}. {question}")

        if options:
            for j, opt in enumerate(options, 1):
                label = opt.get("label", "") if isinstance(opt, dict) else str(opt)
                desc = opt.get("description", "") if isinstance(opt, dict) else ""
                recommended = opt.get("recommended", False) if isinstance(opt, dict) else False
                mark = " ⭐推荐" if recommended else ""
                if desc:
                    lines.append(f"  {j}. {label} — {desc}{mark}")
                else:
                    lines.append(f"  {j}. {label}{mark}")
            # 自由输入选项
            lines.append(f"  {len(options) + 1}. 自由输入（请用 /answer 写内容）")
        lines.append("")

    lines.append("💡 回复方式：")
    if len(questions) == 1 and questions[0].get("options"):
        lines.append("  - 回复 /answer 1 选择选项")
        lines.append("  - 或回复 /answer <自由内容>")
    elif len(questions) > 1:
        lines.append("  - 逐题回复，格式: /answer 1:2 2:是的")
        lines.append("  - 或回复 /answer <自由内容> 作为统一回复")
    else:
        lines.append("  - 回复 /answer <回复内容>")

    return "\n".join(lines)


_ANSWER_COMMAND_RE = re.compile(r"^/answer(?:\s+|\n)(.*)$", re.IGNORECASE | re.DOTALL)


def _extract_explicit_answer_text(text):
    stripped = (text or "").strip()
    if stripped.lower() == "/answer":
        return True, ""
    match = _ANSWER_COMMAND_RE.match(stripped)
    if match:
        return True, match.group(1).strip()
    return False, ""


def _try_answer_pending_question(intern_name, text, project=""):
    """尝试用飞书消息回答 pending question。

    Returns True if consumed, False if no pending question.
    Handles parse failure by sending retry hint via feishu.
    """
    key = _pending_question_key(intern_name, project)
    with _pq_lock:
        entry = _pending_questions.get(key)
    if not entry or entry["answer"] is not None:
        return False

    question_id = entry.get("question_id", "")
    questions = entry["questions"]
    is_answer, answer_text = _extract_explicit_answer_text(text)
    if not is_answer:
        return False
    if not answer_text:
        log.warning(f"[QUESTION] Empty /answer for {intern_name}")
        retry_text = "⚠️ 回复为空：请使用 /answer <内容>，或点击卡片按钮/表单提交。"
        chat_id = _registry.find_chat_id(intern_name, project=project) if _registry else None
        if chat_id and _api:
            _api.send_message(chat_id, retry_text)
        return True

    try:
        answers = _parse_answer(questions, answer_text)
    except ValueError as e:
        # 解析失败 → 发送提示让主管重新回复
        error_msg = str(e)
        log.warning(f"[QUESTION] Parse failed for {intern_name}: {error_msg}")
        retry_text = f"⚠️ 回复格式有误：{error_msg}\n请重新回复。"
        chat_id = _registry.find_chat_id(intern_name, project=project) if _registry else None
        if chat_id and _api:
            _api.send_message(chat_id, retry_text)
        return True  # consumed the message, but not answered yet — wait for retry

    # 成功解析
    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
        if entry:
            entry["answer"] = answers
            entry["updated_at"] = time.time()
            _upsert_question_entry_locked(entry)

    log.info(f"[QUESTION] Answered for {intern_name}: {answers}")
    _update_question_card(intern_name, answers, "飞书消息", project=project, question_id=question_id)
    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
        if entry:
            entry["event"].set()
    return True


def _parse_answer(questions, text):
    """解析主管的回复文本为 answers dict。

    Raises ValueError on parse failure with human-readable reason.
    """
    answers = {}

    if len(questions) == 1:
        q = questions[0]
        question_key = q.get("question", q.get("header", "Q1"))
        options = q.get("options", [])

        if options:
            # 尝试数字选择
            try:
                choice = int(text)
                if 1 <= choice <= len(options):
                    opt = options[choice - 1]
                    label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
                    answers[question_key] = label
                    return answers
                elif choice == len(options) + 1:
                    # 自由输入选项 — 需要更多文本
                    raise ValueError(f"你选了「自由输入」，请直接输入内容（不要只写数字 {choice}）")
                else:
                    raise ValueError(f"请输入 1-{len(options) + 1} 之间的数字")
            except ValueError as ve:
                if "请输入" in str(ve) or "自由输入" in str(ve):
                    raise
                # 不是纯数字 → 当自由文本
                pass

        # 自由文本回复
        answers[question_key] = text
        return answers

    # 多题模式
    # 尝试解析 "题号:答案" 格式
    parts = re.split(r'\s+', text)
    parsed_any = False
    for part in parts:
        if ':' in part or '：' in part:
            sep = ':' if ':' in part else '：'
            idx_str, ans_str = part.split(sep, 1)
            try:
                idx = int(idx_str.strip()) - 1
                if 0 <= idx < len(questions):
                    q = questions[idx]
                    question_key = q.get("question", q.get("header", f"Q{idx+1}"))
                    options = q.get("options", [])
                    if options:
                        try:
                            choice = int(ans_str.strip())
                            if 1 <= choice <= len(options):
                                opt = options[choice - 1]
                                label = opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt)
                                answers[question_key] = label
                                parsed_any = True
                                continue
                        except ValueError:
                            pass
                    answers[question_key] = ans_str.strip()
                    parsed_any = True
            except ValueError:
                pass

    if parsed_any:
        # 填充未回答的题目默认值
        for i, q in enumerate(questions):
            question_key = q.get("question", q.get("header", f"Q{i+1}"))
            if question_key not in answers:
                answers[question_key] = ""  # 空字符串表示未回答
        return answers

    # 无法解析为结构化格式 → 当统一自由回复（所有题共用）
    for q in questions:
        question_key = q.get("question", q.get("header", "Q"))
        answers[question_key] = text

    return answers


def _adopt_pre_tool_codex_question_for_tui(intern_name, project, questions, codex_tui):
    """Let the transcript watcher own an already-sent PreToolUse Codex card."""
    if not codex_tui:
        return None
    signature = _question_signature(questions)
    transcript_path = codex_tui.get("transcript_path", "")
    with _pq_lock:
        entry = _pending_questions.get(_pending_question_key(intern_name, project))
        if not entry:
            return None
        if entry.get("tool_name") != "request_user_input" or entry.get("owner") != "pre_tool":
            return None
        if entry.get("question_signature") != signature:
            return None
        entry_meta = entry.get("metadata") or {}
        entry_transcript = entry_meta.get("transcript_path", "")
        if entry_transcript and transcript_path and entry_transcript != transcript_path:
            return None
        entry["owner"] = "codex_tui"
        entry["codex_tui"] = codex_tui
        entry["metadata"] = {**entry_meta, "codex_tui": codex_tui}
        entry["updated_at"] = time.time()
        _upsert_question_entry_locked(entry)
        question_id = entry.get("question_id", "")
        already_answered = entry.get("answer") is not None

    call_id = codex_tui.get("call_id", "")
    log.info(
        f"[QUESTION] Adopted PreToolUse Codex question for TUI owner "
        f"intern={intern_name} question_id={question_id or '-'} call_id={call_id or '-'} "
        f"already_answered={already_answered}"
    )
    threading.Thread(
        target=_await_codex_tui_question_answer,
        args=(intern_name, project, call_id),
        daemon=True,
    ).start()
    return entry


def _find_existing_codex_tui_question(intern_name, project, questions, metadata):
    """Return an existing TUI-owned Codex pending matching a later PreToolUse hook."""
    signature = _question_signature(questions)
    transcript_path = (metadata or {}).get("transcript_path", "") if isinstance(metadata, dict) else ""
    with _pq_lock:
        entry = _pending_questions.get(_pending_question_key(intern_name, project))
        if not entry:
            return None
        if entry.get("tool_name") != "request_user_input" or entry.get("owner") != "codex_tui":
            return None
        if entry.get("question_signature") != signature:
            return None
        codex_tui = entry.get("codex_tui") or {}
        entry_transcript = codex_tui.get("transcript_path", "")
        if entry_transcript and transcript_path and entry_transcript != transcript_path:
            return None
        return entry


def _question_delivery_error(error, intern_name, project, question_id, owner, **extra):
    data = {
        "ok": False,
        "error": error,
        "intern_name": intern_name,
        "project": project,
        "question_id": question_id,
        "owner": owner,
    }
    data.update({k: v for k, v in extra.items() if v not in (None, "")})
    return data


def _resolve_question_chat_id(intern_name, project):
    if not _registry:
        return "", "registry unavailable"
    try:
        chat_id = _registry.find_chat_id(intern_name, project=project)
    except Exception as exc:
        return "", f"registry lookup failed: {exc}"
    if chat_id:
        return chat_id, ""
    return "", f"chat_id not found for {_online_key(intern_name, project)}"


def _restore_pending_question_after_delivery_failure(key, entry, old_entry, detail=""):
    with _pq_lock:
        current = _pending_questions.get(key)
        if current is not entry:
            return
        _mark_question_status_locked(entry, "delivery_failed", "delivery_failed", detail)
        if old_entry:
            _pending_questions[key] = old_entry
            _upsert_question_entry_locked(old_entry)
        else:
            _pending_questions.pop(key, None)


def _register_pending_question(intern_name, tool_name, questions, prelude_file_path="", metadata=None,
                               project="", workspace_id=""):
    """Send a Feishu question card and register the pending answer state."""
    if not intern_name or not questions:
        return 400, {"error": "intern_name and questions required"}
    metadata = metadata if isinstance(metadata, dict) else {}
    project, workspace_id = _canonical_intern_scope(
        intern_name,
        project=project or metadata.get("project") or "",
        workspace_id=workspace_id or metadata.get("workspace_id") or "",
    )
    if not project:
        return 400, {"error": "project required"}
    key = _pending_question_key(intern_name, project)

    metadata = metadata if isinstance(metadata, dict) else {}
    channel = _question_channel(tool_name, metadata)
    signature = _question_signature(questions)
    # Codex can surface the same request_user_input through both PreToolUse and
    # the transcript watcher. Keep one scoped pending owner so the supervisor
    # sees one card and the answer wakes the correct waiter.
    if channel == "pre_tool" and tool_name == "request_user_input":
        existing_tui = _find_existing_codex_tui_question(intern_name, project, questions, metadata)
        if existing_tui:
            question_id = existing_tui.get("question_id", "")
            log.info(
                f"[QUESTION] PreToolUse Codex question delegated to existing TUI owner "
                f"intern={intern_name} question_id={question_id or '-'} "
                f"call_id={(existing_tui.get('codex_tui') or {}).get('call_id', '-')}"
            )
            return 200, {"ok": True, "question_id": question_id, "delegated": "codex_tui"}

    question_id = metadata.get("question_id") or uuid.uuid4().hex
    codex_tui = metadata.get("codex_tui")
    owner = "codex_tui" if codex_tui else channel
    chat_id, chat_error = _resolve_question_chat_id(intern_name, project)
    if chat_error:
        log.error(
            f"[QUESTION] Cannot deliver question card for {_online_key(intern_name, project)} "
            f"question_id={question_id} owner={owner}: {chat_error}"
        )
        return 409, _question_delivery_error(
            "question_chat_id_unavailable",
            intern_name,
            project,
            question_id,
            owner,
            detail=chat_error,
        )
    if not _api:
        log.error(
            f"[QUESTION] Cannot deliver question card for {_online_key(intern_name, project)} "
            f"question_id={question_id} owner={owner}: Feishu API unavailable"
        )
        return 503, _question_delivery_error(
            "feishu_api_unavailable",
            intern_name,
            project,
            question_id,
            owner,
            chat_id=chat_id,
        )

    # Reserve the scoped pending entry before sending the Feishu card. Codex
    # request_user_input may arrive through the transcript watcher and the
    # PreToolUse hook within the same second; without this reservation both
    # paths can miss each other while the first card send is still in flight.
    now = time.time()
    entry = {
        "intern_name": intern_name,
        "question_id": question_id,
        "questions": questions,
        "tool_name": tool_name,
        "answer": None,
        "event": _q_threading.Event(),
        "message_id": None,
        "codex_tui": codex_tui,
        "owner": owner,
        "channel": channel,
        "metadata": metadata,
        "question_signature": signature,
        "project": project,
        "workspace_id": workspace_id,
        "chat_id": chat_id,
        "delivery_state": "sending",
        "delivery_kind": "",
        "status": "pending",
        "created_at": now,
        "updated_at": now,
        "deadline_at": now + _QUESTION_DEFAULT_TIMEOUT_SECONDS,
        "settled_at": None,
        "invalid_reason": "",
        "invalid_detail": "",
    }
    with _pq_lock:
        _ensure_question_store_loaded_locked()
        old_entry = _pending_questions.get(key)
        _pending_questions[key] = entry
        _upsert_question_entry_locked(entry)

    if channel == "pre_tool" and tool_name == "request_user_input":
        _start_codex_tui_backfill_for_pre_tool(intern_name, project, metadata, workspace_id=workspace_id)

    # 发送到飞书（优先发送交互卡片，失败时 fallback 到文本）
    msg_id = None
    delivery_kind = "card"
    card_err = ""
    text_err = ""
    # 若有 prelude_file_path（例如 ExitPlanMode 把完整 plan 写到 md 临时文件），
    # 先上传 + 发文件到群。利用飞书对 md 的渲染，主管点开即可阅读完整 plan，
    # 不再用普通消息污染聊天窗口。
    if prelude_file_path:
        file_key, up_err = _api.upload_file(prelude_file_path)
        if up_err:
            log.warning(f"[QUESTION] prelude file upload failed for {intern_name}: {up_err}")
        else:
            prep = prepare_feishu_rich_content_boundary_for_intern(
                intern_name, "file", api=_api)
            if prep.get("status") not in ("flushed", "already_pending", "no_active_message", "missing_state"):
                log.warning(f"[QUESTION] prelude file boundary prepare for {intern_name}: {prep}")
            file_msg_id, send_err = _api.send_file(chat_id, file_key)
            if send_err:
                log.warning(f"[QUESTION] prelude file send failed for {intern_name}: {send_err}")
            else:
                mark = mark_feishu_rich_content_boundary_sent(
                    intern_name, "file", file_msg_id)
                if mark.get("status") != "marked":
                    log.warning(f"[QUESTION] prelude file boundary mark for {intern_name}: {mark}")
                log.info(f"[QUESTION] prelude file sent for {intern_name}: {prelude_file_path}")
    # 尝试发送交互卡片
    card_json = _format_question_card(intern_name, tool_name, questions, question_id=question_id)
    prep = prepare_feishu_rich_content_boundary_for_intern(
        intern_name, "card", api=_api)
    if prep.get("status") not in ("flushed", "already_pending", "no_active_message", "missing_state"):
        log.warning(f"[QUESTION] card boundary prepare for {intern_name}: {prep}")
    msg_id, card_err = _api.send_interactive_card(chat_id, card_json)
    if card_err or not msg_id:
        card_err = card_err or "interactive card send returned empty message_id"
        delivery_kind = "text_fallback"
        log.warning(
            f"[QUESTION] Card send failed for {_online_key(intern_name, project)} "
            f"question_id={question_id}: {card_err}; falling back to text"
        )
        feishu_text = _format_question_feishu(intern_name, tool_name, questions)
        msg_id, text_err = _api.send_message(chat_id, feishu_text)
        if text_err or not msg_id:
            text_err = text_err or "text fallback returned empty message_id"
            log.error(
                f"[QUESTION] Cannot deliver question for {_online_key(intern_name, project)} "
                f"question_id={question_id} owner={owner}: card_error={card_err}; text_error={text_err}"
            )
            _restore_pending_question_after_delivery_failure(
                key,
                entry,
                old_entry,
                detail=f"card_error={card_err}; text_error={text_err}",
            )
            return 502, _question_delivery_error(
                "question_card_delivery_failed",
                intern_name,
                project,
                question_id,
                owner,
                chat_id=chat_id,
                card_error=card_err,
                text_error=text_err,
            )
        mark = mark_feishu_rich_content_boundary_sent(
            intern_name, "card", msg_id)
        if mark.get("status") != "marked":
            log.warning(f"[QUESTION] text fallback boundary mark for {intern_name}: {mark}")
    else:
        mark = mark_feishu_rich_content_boundary_sent(
            intern_name, "card", msg_id)
        if mark.get("status") != "marked":
            log.warning(f"[QUESTION] card boundary mark for {intern_name}: {mark}")
        log.info(
            f"[QUESTION] Interactive card sent to {intern_name}, msg_id={msg_id}, "
            f"question_id={question_id}, channel={channel}"
        )

    if old_entry:
        with _pq_lock:
            _mark_question_status_locked(
                old_entry,
                "superseded",
                "superseded_by_new_question",
                f"new_question_id={question_id}",
            )
    if old_entry and old_entry.get("message_id") and _api:
        old_msg_id = old_entry["message_id"]
        old_tool = old_entry.get("tool_name", "AskUserQuestion")
        supersede_card = _build_invalid_question_card(
            intern_name,
            old_tool,
            "superseded_by_new_question",
            detail=f"new_question_id={question_id}",
            guidance="请回复最新的问题卡片",
        )
        err = _api.update_interactive_card(old_msg_id, supersede_card)
        if err:
            log.warning(f"[QUESTION] Failed to supersede old card for {intern_name}: {err}")
        else:
            log.info(
                f"[QUESTION] Superseded old card for {_online_key(intern_name, project)}: "
                f"old_question_id={old_entry.get('question_id', '-')} "
                f"old_owner={old_entry.get('owner', '-')} "
                f"old_call_id={(old_entry.get('codex_tui') or {}).get('call_id', '-')} "
                f"new_question_id={question_id} new_owner={owner} "
                f"new_call_id={(codex_tui or {}).get('call_id', '-')}"
            )
    with _pq_lock:
        current = _pending_questions.get(key)
        if current is entry:
            current["message_id"] = msg_id
            current["delivery_state"] = "sent"
            current["delivery_kind"] = delivery_kind
            current["updated_at"] = time.time()
            _upsert_question_entry_locked(current)

    log.info(
        f"[QUESTION] Registered pending question for {_online_key(intern_name, project)} ({tool_name}) "
        f"question_id={question_id} owner={owner} channel={channel} delivery={delivery_kind} "
        f"chat_id={chat_id} message_id={msg_id} "
        f"call_id={(codex_tui or {}).get('call_id', '-')}"
    )
    if codex_tui:
        call_id = codex_tui.get("call_id", "")
        threading.Thread(
            target=_await_codex_tui_question_answer,
            args=(intern_name, project, call_id),
            daemon=True,
        ).start()
    return 200, {
        "ok": True,
        "message_id": msg_id,
        "question_id": question_id,
        "owner": owner,
        "chat_id": chat_id,
        "delivery": delivery_kind,
    }


def _codex_question_key(question):
    return question.get("question", question.get("header", "Q"))


def _codex_option_labels(question):
    labels = []
    for opt in question.get("options", []) or []:
        labels.append(opt.get("label", str(opt)) if isinstance(opt, dict) else str(opt))
    return labels


_CODEX_TUI_VERIFY_ATTEMPTS = 8
_CODEX_TUI_VERIFY_INTERVAL = 0.15
_CODEX_TUI_QUESTION_VISIBLE_ATTEMPTS = 40
_CODEX_TUI_QUESTION_VISIBLE_INTERVAL = 0.25
_CODEX_TUI_SELECTED_MARKERS = ("›", ">", "▶", "▸", "●", "◉", "◆")
_CODEX_TUI_ANY_CHECKBOX_RE = re.compile(r"^\s*[-*]?\s*\[[ xX]\]")
_CODEX_TUI_CHECKBOX_RE = re.compile(r"^\s*[-*]?\s*\[[xX]\]")
_CODEX_TUI_CUSTOM_ANSWER_LABELS = ("None of the above", "None of above")


def _capture_codex_tui_pane(target):
    result = subprocess.run(
        ["tmux", "capture-pane", "-p", "-J", "-t", target, "-S", "-80"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout or ""


def _codex_tui_compact_text(value):
    return re.sub(r"\s+", "", str(value or ""))


def _codex_tui_text_has_ordered_words(text, fragment):
    words = [_codex_tui_compact_text(part) for part in re.split(r"\s+", str(fragment or "").strip()) if part]
    if len(words) <= 1:
        return False
    compact_text = _codex_tui_compact_text(text)
    pos = 0
    for word in words:
        found = compact_text.find(word, pos)
        if found < 0:
            return False
        pos = found + len(word)
    return True


def _codex_tui_text_has_fragment(text, fragment):
    fragment = str(fragment or "").strip()
    if not fragment:
        return True
    text = str(text or "")
    if fragment in text:
        return True
    compact_fragment = _codex_tui_compact_text(fragment)
    if len(compact_fragment) <= 1:
        return False
    return compact_fragment in _codex_tui_compact_text(text) or _codex_tui_text_has_ordered_words(text, fragment)


def _codex_tui_question_visible(pane_text, question_key, labels):
    if question_key and not _codex_tui_text_has_fragment(pane_text, question_key):
        return False
    for label in labels or []:
        if not _codex_tui_text_has_fragment(pane_text, label):
            return False
    return True


def _wait_codex_tui_question_visible(target, question_key, labels):
    last = ""
    for _ in range(_CODEX_TUI_QUESTION_VISIBLE_ATTEMPTS):
        last = _capture_codex_tui_pane(target)
        if _codex_tui_question_visible(last, question_key, labels):
            return True, "", last
        time.sleep(_CODEX_TUI_QUESTION_VISIBLE_INTERVAL)
    return False, f"Codex TUI question/options not visible before answer submit: {question_key}", last


def _codex_tui_line_has_label(text, label):
    label = str(label or "").strip()
    if not label:
        return False
    text = str(text or "").strip()
    if text == label:
        return True
    if re.search(rf"(?<!\w){re.escape(label)}(?!\w)", text) is not None:
        return True
    return len(_codex_tui_compact_text(label)) > 1 and _codex_tui_text_has_fragment(text, label)


def _codex_tui_strip_checkbox_prefix(text):
    return _CODEX_TUI_ANY_CHECKBOX_RE.sub("", str(text or ""), count=1).strip()


def _codex_tui_selected_line_body(line):
    stripped = (line or "").strip()
    for marker in _CODEX_TUI_SELECTED_MARKERS:
        if stripped.startswith(marker):
            return _codex_tui_strip_checkbox_prefix(stripped[len(marker):].strip())
    return ""


def _codex_tui_line_window(lines, index, first_body):
    parts = [first_body]
    for line in lines[index + 1:index + 3]:
        if _codex_tui_selected_line_body(line):
            break
        parts.append(str(line or "").strip())
    return "\n".join(part for part in parts if part)


def _codex_tui_match_label_in_body(body, window, labels):
    body_compact = _codex_tui_compact_text(body)
    for label in labels:
        label_compact = _codex_tui_compact_text(label)
        if not label_compact:
            continue
        if _codex_tui_line_has_label(body, label) or _codex_tui_line_has_label(window, label):
            return label
        if body_compact and label_compact.startswith(body_compact) and _codex_tui_text_has_fragment(window, label):
            return label
    return ""


def _codex_tui_selected_label(pane_text, labels):
    labels = [str(label) for label in (labels or []) if str(label)]
    lines = (pane_text or "").splitlines()
    for index, line in enumerate(lines):
        body = _codex_tui_selected_line_body(line)
        if not body:
            continue
        label = _codex_tui_match_label_in_body(body, _codex_tui_line_window(lines, index, body), labels)
        if label:
            return label
    return ""


def _codex_tui_checked_line_body(line):
    stripped = (line or "").strip()
    for marker in _CODEX_TUI_SELECTED_MARKERS:
        if stripped.startswith(marker):
            stripped = stripped[len(marker):].strip()
            break
    if _CODEX_TUI_CHECKBOX_RE.match(stripped):
        return _codex_tui_strip_checkbox_prefix(stripped)
    return ""


def _codex_tui_checked_labels(pane_text, labels):
    labels = [str(label) for label in (labels or []) if str(label)]
    checked = []
    lines = (pane_text or "").splitlines()
    for index, line in enumerate(lines):
        body = _codex_tui_checked_line_body(line)
        if not body:
            continue
        label = _codex_tui_match_label_in_body(body, _codex_tui_line_window(lines, index, body), labels)
        if label and label not in checked:
            checked.append(label)
    return checked


def _codex_tui_selected_answer_visible(pane_text, answer):
    return _codex_tui_selected_label(pane_text, [answer]) == str(answer)


def _wait_codex_tui_selection_state(target, labels, expected="", previous=""):
    last_selected = ""
    last = ""
    for _ in range(_CODEX_TUI_VERIFY_ATTEMPTS):
        last = _capture_codex_tui_pane(target)
        selected = _codex_tui_selected_label(last, labels)
        if selected:
            last_selected = selected
            if expected and selected == expected:
                return selected, "", last
            if previous and selected != previous:
                return selected, "", last
            if not previous:
                return selected, "", last
        time.sleep(_CODEX_TUI_VERIFY_INTERVAL)
    detail = f"; last selected: {last_selected}" if last_selected else ""
    if expected:
        return "", f"selected answer not visible before submit: {expected}{detail}", last
    return "", f"selected answer not visible before submit{detail}", last


def _wait_codex_tui_selection(target, answer):
    selected, err, _pane = _wait_codex_tui_selection_state(target, [answer], expected=str(answer))
    return selected == str(answer), err


def _wait_codex_tui_checked_state(target, labels, expected):
    expected = {str(answer) for answer in (expected or []) if str(answer)}
    last_checked = []
    last = ""
    for _ in range(_CODEX_TUI_VERIFY_ATTEMPTS):
        last = _capture_codex_tui_pane(target)
        checked = _codex_tui_checked_labels(last, labels)
        last_checked = checked
        if expected.issubset(set(checked)):
            return True, "", last
        time.sleep(_CODEX_TUI_VERIFY_INTERVAL)
    detail = f"; last checked: {', '.join(last_checked)}" if last_checked else ""
    return False, f"checked answers not visible before submit: {', '.join(sorted(expected))}{detail}", last


def _codex_tui_max_selection_moves(labels):
    return max(len(labels or []) * 2, 3)


def _move_codex_tui_selection_to_any_answer(target, labels, answers, send_key, initial_pane=""):
    labels = [str(label) for label in (labels or []) if str(label)]
    answers = {str(answer) for answer in (answers or []) if str(answer)}
    previous = _codex_tui_selected_label(initial_pane, labels)
    if previous in answers:
        return True, ""

    if not previous:
        previous, _err, _pane = _wait_codex_tui_selection_state(target, labels)
        if previous in answers:
            return True, ""

    observed = [previous] if previous else []

    def scan(direction, previous):
        for _ in range(_codex_tui_max_selection_moves(labels)):
            send_key(direction)
            selected, _err, _pane = _wait_codex_tui_selection_state(
                target,
                labels,
                previous=previous,
            )
            if selected:
                observed.append(selected)
                previous = selected
            if selected in answers:
                return True, previous
        return False, previous

    found, previous = scan("Down", previous)
    if found:
        return True, ""
    found, previous = scan("Up", previous)
    if found:
        return True, ""

    answer_detail = "/".join(sorted(answers)) if answers else "<empty>"
    detail = f"; observed selections: {', '.join(observed[-6:])}" if observed else ""
    return False, f"selected answer not visible before submit: {answer_detail}{detail}"


def _move_codex_tui_selection_to_answer(target, labels, answer, send_key, initial_pane=""):
    return _move_codex_tui_selection_to_any_answer(
        target,
        labels,
        [answer],
        send_key,
        initial_pane=initial_pane,
    )


def _wait_codex_tui_text(target, text):
    if not text:
        return True, ""
    for _ in range(_CODEX_TUI_VERIFY_ATTEMPTS):
        pane = _capture_codex_tui_pane(target)
        if _codex_tui_text_has_fragment(pane, text):
            return True, ""
        time.sleep(_CODEX_TUI_VERIFY_INTERVAL)
    return False, f"custom answer text not visible before submit: {text}"


def _send_codex_tui_answer(intern_name, questions, answers, project=""):
    """Submit Feishu answers into Codex 0.130 native request_user_input TUI."""
    if not _check_tmux_session(intern_name, project=project):
        return False, "tmux session not found"
    if not _is_codex_process_running(intern_name, project=project):
        return False, "Codex has exited"

    target = _tmux_target(intern_name, project=project)

    def send_key(key, delay=0.15):
        subprocess.run(["tmux", "send-keys", "-t", target, key], check=True, capture_output=True)
        time.sleep(delay)

    try:
        for question in questions:
            key = _codex_question_key(question)
            answer = answers.get(key, "")
            labels = _codex_option_labels(question)
            visible, err, pane_text = _wait_codex_tui_question_visible(target, key, labels)
            if not visible:
                return False, err

            if isinstance(answer, list):
                selected_answers = [str(item) for item in answer if str(item)]
                if selected_answers and labels and all(item in labels for item in selected_answers):
                    checked_so_far = set(_codex_tui_checked_labels(pane_text, labels))
                    for selected_answer in selected_answers:
                        if selected_answer not in checked_so_far:
                            moved, err = _move_codex_tui_selection_to_answer(
                                target,
                                labels,
                                selected_answer,
                                send_key,
                                initial_pane=pane_text,
                            )
                            if not moved:
                                return False, err
                            send_key("Space", delay=0.25)
                        checked_so_far.add(selected_answer)
                        checked, err, pane_text = _wait_codex_tui_checked_state(
                            target,
                            labels,
                            checked_so_far,
                        )
                        if not checked:
                            return False, err
                    send_key("Enter", delay=0.25)
                    time.sleep(0.25)
                    continue
                answer = "、".join(selected_answers)
            answer = str(answer)

            if answer in labels:
                moved, err = _move_codex_tui_selection_to_answer(
                    target,
                    labels,
                    answer,
                    send_key,
                    initial_pane=pane_text,
                )
                if not moved:
                    return False, err
                send_key("Enter", delay=0.25)
            else:
                # Codex adds a final "None of the above" option with notes. Use it
                # for custom text answers that do not match an explicit option.
                if labels:
                    all_labels = labels + list(_CODEX_TUI_CUSTOM_ANSWER_LABELS)
                    moved, err = _move_codex_tui_selection_to_any_answer(
                        target,
                        all_labels,
                        _CODEX_TUI_CUSTOM_ANSWER_LABELS,
                        send_key,
                        initial_pane=pane_text,
                    )
                    if not moved:
                        return False, err
                    send_key("Tab", delay=0.5)
                if answer:
                    subprocess.run(
                        ["tmux", "send-keys", "-t", target, "-l", "--", answer],
                        check=True,
                        capture_output=True,
                    )
                    time.sleep(0.25)
                    visible, err = _wait_codex_tui_text(target, answer)
                    if not visible:
                        return False, err
                send_key("Enter", delay=0.25)
            time.sleep(0.25)
        log.info(f"[CODEX_RUI] Submitted TUI answer for {intern_name}")
        return True, None
    except subprocess.CalledProcessError as e:
        return False, str(e)


def _await_codex_tui_question_answer(intern_name, project, call_id):
    key = _pending_question_key(intern_name, project)
    with _pq_lock:
        entry = _pending_questions.get(key)
        question_id = (entry or {}).get("question_id", "")
        if not entry or not entry.get("codex_tui") or entry["codex_tui"].get("call_id") != call_id:
            current = _pending_questions.get(key)
            log.warning(
                f"[CODEX_RUI] Pending entry missing/mismatched before wait "
                f"intern={_online_key(intern_name, project)} question_id={question_id or '-'} call_id={call_id or '-'} "
                f"current_question_id={(current or {}).get('question_id', '-')} "
                f"current_owner={(current or {}).get('owner', '-')}"
            )
            return
        event = entry["event"]

    if not event.wait(timeout=_CODEX_RUI_WATCH_TIMEOUT):
        log.warning(
            f"[CODEX_RUI] Timeout waiting Feishu answer for {_online_key(intern_name, project)} "
            f"question_id={question_id or '-'} call_id={call_id or '-'}"
        )
        _update_question_card_to_timeout(
            intern_name,
            _CODEX_RUI_WATCH_TIMEOUT // 3600,
            project=project,
            question_id=question_id,
        )
        with _pq_lock:
            current = _get_pending_question_locked(intern_name, project, question_id)
            if current and current.get("codex_tui", {}).get("call_id") == call_id:
                _mark_question_status_locked(current, "timed_out", "timed_out")
                _remove_pending_question_locked(intern_name, project, current)
        return

    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
        if not entry or not entry.get("codex_tui") or entry["codex_tui"].get("call_id") != call_id:
            current = _pending_questions.get(key)
            log.warning(
                f"[CODEX_RUI] Pending entry missing/mismatched after answer "
                f"intern={_online_key(intern_name, project)} question_id={question_id or '-'} call_id={call_id or '-'} "
                f"current_question_id={(current or {}).get('question_id', '-')} "
                f"current_owner={(current or {}).get('owner', '-')}"
            )
            return
        questions = entry.get("questions", [])
        answers = entry.get("answer") or {}

    success, err = _send_codex_tui_answer(intern_name, questions, answers, project=project)
    if not success:
        log.warning(
            f"[CODEX_RUI] Failed to submit TUI answer for {_online_key(intern_name, project)} "
            f"question_id={question_id or '-'} call_id={call_id or '-'}: {err}"
        )
        chat_id = _registry.find_chat_id(intern_name, project=project) if _registry else None
        if chat_id and _api:
            _api.send_message(chat_id, f"⚠️ 已收到回答，但回填 Codex TUI 失败：{err}")
        with _pq_lock:
            current = _get_pending_question_locked(intern_name, project, question_id)
            if current and current.get("codex_tui", {}).get("call_id") == call_id:
                current["codex_tui_answer_delivery"] = {
                    "status": "failed",
                    "reason": "codex_tui_answer_verification_failed",
                    "detail": err or "",
                    "updated_at": _runtime_now_iso(),
                }
                _mark_question_status_locked(current, "answered")
                _remove_pending_question_locked(intern_name, project, current)
        return

    with _pq_lock:
        current = _get_pending_question_locked(intern_name, project, question_id)
        if current and current.get("codex_tui", {}).get("call_id") == call_id:
            _mark_question_status_locked(current, "answered")
            _remove_pending_question_locked(intern_name, project, current)


def _handle_codex_request_user_input_call(intern_name, transcript_path, payload, project="", workspace_id=""):
    call_id = payload.get("call_id", "")
    if not call_id:
        return
    project, workspace_id = _canonical_intern_scope(
        intern_name,
        project=project,
        workspace_id=workspace_id,
    )

    seen_key = (_pending_question_key(intern_name, project), transcript_path, call_id)
    with _codex_rui_lock:
        if seen_key in _codex_rui_seen_calls:
            return
        _codex_rui_seen_calls.add(seen_key)

    try:
        args = json.loads(payload.get("arguments") or "{}")
    except json.JSONDecodeError as e:
        log.warning(f"[CODEX_RUI] Invalid request_user_input args for {intern_name}: {e}")
        return

    questions = args.get("questions", [])
    if not isinstance(questions, list) or not questions:
        log.warning(f"[CODEX_RUI] Empty request_user_input questions for {intern_name}")
        return

    codex_tui = {"call_id": call_id, "transcript_path": transcript_path}
    adopted = _adopt_pre_tool_codex_question_for_tui(intern_name, project, questions, codex_tui)
    if adopted:
        log.info(
            f"[CODEX_RUI] Feishu question adopted for {intern_name} "
            f"call_id={call_id} question_id={adopted.get('question_id', '-')}"
        )
        return

    status, resp = _register_pending_question(
        intern_name,
        "request_user_input",
        questions,
        project=project,
        workspace_id=workspace_id,
        metadata={"codex_tui": codex_tui},
    )
    if status != 200:
        log.warning(f"[CODEX_RUI] Failed to register Feishu question for {intern_name}: {resp}")
    else:
        log.info(f"[CODEX_RUI] Feishu question registered for {intern_name} call_id={call_id}")


def _cleanup_pre_tool_codex_question_after_grace(intern_name, project, question_id):
    time.sleep(_CODEX_RUI_PRE_TOOL_ADOPT_GRACE)
    with _pq_lock:
        entry = _get_pending_question_locked(intern_name, project, question_id)
        if (
            entry
            and entry.get("tool_name") == "request_user_input"
            and entry.get("owner") == "pre_tool"
            and entry.get("answer") is not None
        ):
            _remove_pending_question_locked(intern_name, project, entry)
            log.info(
                f"[CODEX_RUI] Cleaned answered PreToolUse Codex pending after adopt grace "
                f"intern={_online_key(intern_name, project)} question_id={question_id or '-'}"
            )


def _schedule_pre_tool_codex_cleanup(intern_name, project, question_id):
    threading.Thread(
        target=_cleanup_pre_tool_codex_question_after_grace,
        args=(intern_name, project, question_id),
        daemon=True,
    ).start()


def _codex_rui_item_epoch(item):
    ts = item.get("timestamp", "")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _codex_rui_pre_tool_backfill_done(intern_name, project):
    with _pq_lock:
        entry = _pending_questions.get(_pending_question_key(intern_name, project))
        return bool(
            entry
            and entry.get("tool_name") == "request_user_input"
            and entry.get("owner") == "codex_tui"
        )


def _start_codex_tui_backfill_for_pre_tool(intern_name, project, metadata, workspace_id=""):
    transcript_path = (metadata or {}).get("transcript_path", "") if isinstance(metadata, dict) else ""
    if not transcript_path or not os.path.exists(transcript_path):
        return
    try:
        size = os.path.getsize(transcript_path)
    except OSError:
        return
    start_offset = max(0, size - _CODEX_RUI_PRE_TOOL_LOOKBACK_BYTES)
    min_event_time = time.time() - _CODEX_RUI_PRE_TOOL_LOOKBACK_SECONDS
    log.info(
        f"[CODEX_RUI] PreToolUse backfill watcher start requested "
        f"intern={_online_key(intern_name, project)} path={transcript_path} "
        f"offset={start_offset} size={size} min_event_time={min_event_time:.3f}"
    )
    threading.Thread(
        target=_codex_request_user_input_watch_loop,
        args=(intern_name, transcript_path, start_offset, project, workspace_id),
        kwargs={
            "watch_timeout": _CODEX_RUI_PRE_TOOL_BACKFILL_TIMEOUT,
            "label": "pre_tool_backfill",
            "min_event_time": min_event_time,
            "prefer_latest_existing": True,
        },
        daemon=True,
    ).start()


def _codex_request_user_input_watch_loop(intern_name, transcript_path, start_offset, project="", workspace_id="",
                                         watch_timeout=None, label="watcher", min_event_time=None,
                                         prefer_latest_existing=False):
    timeout = _CODEX_RUI_WATCH_TIMEOUT if watch_timeout is None else watch_timeout
    log.info(
        f"[CODEX_RUI] Watcher start label={label} intern={_online_key(intern_name, project)} "
        f"path={transcript_path} offset={start_offset} timeout={timeout}"
    )
    offset = max(0, int(start_offset or 0))
    deadline = time.time() + timeout
    first_batch = True

    while time.time() < deadline:
        try:
            if not os.path.exists(transcript_path):
                time.sleep(0.5)
                continue
            size = os.path.getsize(transcript_path)
            if size < offset:
                offset = 0
            if size == offset:
                time.sleep(0.5)
                continue
            with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                lines = f.readlines()
                offset = f.tell()
            if prefer_latest_existing and first_batch:
                iterable_lines = reversed(lines)
            else:
                iterable_lines = lines
            first_batch = False
            for line in iterable_lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if item.get("type") != "response_item":
                    continue
                if min_event_time is not None:
                    item_epoch = _codex_rui_item_epoch(item)
                    if item_epoch is not None and item_epoch < min_event_time:
                        continue
                payload = item.get("payload") or {}
                if payload.get("type") == "function_call" and payload.get("name") == "request_user_input":
                    _handle_codex_request_user_input_call(
                        intern_name,
                        transcript_path,
                        payload,
                        project=project,
                        workspace_id=workspace_id,
                    )
                    if label == "pre_tool_backfill" and _codex_rui_pre_tool_backfill_done(intern_name, project):
                        log.info(
                            f"[CODEX_RUI] PreToolUse backfill adopted TUI owner; stopping "
                            f"intern={_online_key(intern_name, project)} path={transcript_path}"
                        )
                        return
        except Exception as e:
            log.warning(f"[CODEX_RUI] Watcher error for {intern_name}: {e}", exc_info=True)
            time.sleep(1)

    log.info(f"[CODEX_RUI] Watcher stop label={label} intern={_online_key(intern_name, project)} path={transcript_path}")


def _register_codex_request_user_input_watcher(intern_name, transcript_path, start_offset=0,
                                               project="", workspace_id=""):
    if not intern_name or not transcript_path:
        return 400, {"error": "intern_name and transcript_path required"}
    project, workspace_id = _canonical_intern_scope(
        intern_name,
        project=project,
        workspace_id=workspace_id,
    )
    if not project:
        return 400, {"error": "project required"}
    if _get_intern_type(intern_name, project=project) != "codex":
        return 200, {"ok": True, "skipped": "not_codex"}

    key = (_pending_question_key(intern_name, project), transcript_path)
    with _codex_rui_lock:
        existing = _codex_rui_watchers.get(key)
        if existing and existing.is_alive():
            return 200, {"ok": True, "already_running": True}
        t = threading.Thread(
            target=_codex_request_user_input_watch_loop,
            args=(intern_name, transcript_path, start_offset, project, workspace_id),
            daemon=True,
        )
        _codex_rui_watchers[key] = t
        t.start()
    return 200, {"ok": True}


class DaemonHTTPServer(ThreadingHTTPServer):
    # Python's TCPServer defaults request_queue_size=5 → socket.listen(5). On
    # VS Code window reload the extension fires 3–5 concurrent /api/status
    # probes (hash check + reachability poll + plugin status); multi-window
    # reload easily crosses 5 simultaneous SYNs and the kernel starts dropping
    # them, forcing 1s TCP SYN retransmit that blows past the extension's
    # 1.5s/3s timeouts and triggers a spurious daemon kill-and-restart.
    request_queue_size = 128
    daemon_threads = True
    block_on_close = False


def _relay_http_base_required():
    if not (_relay_client and _relay_client.connected and _relay_client._relay_http_base):
        raise RuntimeError("relay not connected")
    return _relay_client._relay_http_base


def _wait_for_relay_http_base(timeout_seconds=0):
    deadline = time.time() + max(0, float(timeout_seconds or 0))
    while True:
        client = globals().get("_relay_client")
        if client and getattr(client, "connected", False) and getattr(client, "_relay_http_base", None):
            return client._relay_http_base
        if time.time() >= deadline:
            return ""
        time.sleep(0.1)


def _relay_workspace_request(method, path, payload=None, timeout=15):
    base = _relay_http_base_required()
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw or "{}")
        except Exception:
            body = {"error": raw}
        return int(exc.code), body


def _workspace_id_from_relay_workspace_payload(payload):
    if not isinstance(payload, dict):
        return ""
    workspace = payload.get("workspace") if isinstance(payload.get("workspace"), dict) else {}
    return str(payload.get("workspace_id") or workspace.get("workspace_id") or "").strip()


class APIHandler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(3)

    def log_message(self, format, *args):
        log.info(f"[HTTP] {format % args}")

    def _start_metric(self, method):
        parsed = urllib.parse.urlparse(self.path)
        self._metric_key = f"http:{method} {parsed.path}"
        self._metric_started_at = time.time()
        self._metric_recorded = False

    def _json_response(self, code, data):
        if getattr(self, "_metric_key", None) and not getattr(self, "_metric_recorded", False):
            elapsed_ms = int((time.time() - self._metric_started_at) * 1000)
            _daemon_metrics.record(
                self._metric_key,
                elapsed_ms=elapsed_ms,
                status_code=code,
                error=code >= 400,
            )
            self._metric_recorded = True
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._json_response(400, {"error": "invalid Content-Length"})
            return None
        if length > 1024 * 1024:
            self._json_response(413, {"error": "request body too large"})
            return None
        if length > 0:
            try:
                raw = self.rfile.read(length)
            except socket.timeout:
                self._json_response(408, {"error": "request body timeout"})
                return None
            if len(raw) != length:
                self._json_response(400, {"error": "incomplete request body"})
                return None
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                self._json_response(400, {"error": "invalid JSON body"})
                return None
        return {}

    def _workspace_path_parts(self):
        parsed = urllib.parse.urlparse(self.path)
        return [part for part in parsed.path.split("/") if part], urllib.parse.parse_qs(parsed.query)

    def _sync_workspaces_from_relay(self):
        if _workspace_cache is None:
            raise RuntimeError("workspace cache unavailable")
        status, payload = _relay_workspace_request("GET", "/api/workspaces")
        if status >= 400:
            raise RuntimeError(payload.get("error") or f"relay workspace sync failed: HTTP {status}")
        return _workspace_cache.sync_from_relay_payload(payload)

    def _handle_workspace_get(self):
        if _workspace_cache is None:
            return self._json_response(503, {"error": "workspace cache unavailable"})
        parts, _ = self._workspace_path_parts()
        if parts == ["api", "workspaces"]:
            try:
                self._sync_workspaces_from_relay()
            except Exception as e:
                log.warning(f"[WORKSPACE] relay sync before list failed: {e}")
            return self._json_response(200, _workspace_cache.list())
        if len(parts) == 4 and parts[:2] == ["api", "workspaces"] and parts[3] == "doctor":
            try:
                return self._json_response(200, _workspace_cache.doctor(parts[2]))
            except KeyError as e:
                return self._json_response(404, {"error": str(e)})
        return self._json_response(404, {"error": "not found"})

    def _handle_workspace_post(self, body):
        if _workspace_cache is None:
            return self._json_response(503, {"error": "workspace cache unavailable"})
        parts, _ = self._workspace_path_parts()
        try:
            if parts == ["api", "workspaces"]:
                provider = (body.get("provider") or "").strip().lower()
                mode = (body.get("metadata_mode") or body.get("mode") or "").strip()
                if provider == "local" or mode == "local_only":
                    if provider != "local" or mode != "local_only":
                        return self._json_response(400, {
                            "ok": False,
                            "error": "local workspaces require provider=local and metadata_mode=local_only",
                        })
                    item = _workspace_cache.create_local(body)
                    return self._json_response(201, {"ok": True, "workspace": item, **item})
                status, payload = _relay_workspace_request("POST", "/api/workspaces", body, timeout=30)
                if status < 400:
                    try:
                        self._sync_workspaces_from_relay()
                    except Exception as sync_error:
                        workspace_id = _workspace_id_from_relay_workspace_payload(payload)
                        rollback = {
                            "attempted": bool(workspace_id) and not bool(payload.get("reused")),
                            "deleted": False,
                            "error": "",
                        }
                        if workspace_id and not payload.get("reused"):
                            try:
                                delete_status, delete_payload = _relay_workspace_request(
                                    "DELETE",
                                    f"/api/workspaces/{urllib.parse.quote(workspace_id)}",
                                )
                                rollback["status"] = delete_status
                                rollback["response"] = delete_payload
                                rollback["deleted"] = delete_status < 400 and delete_payload.get("ok", True) is not False
                                try:
                                    self._sync_workspaces_from_relay()
                                except Exception as refresh_error:
                                    rollback["refresh_error"] = str(refresh_error)
                            except Exception as rollback_error:
                                rollback["error"] = str(rollback_error)
                        return self._json_response(503, {
                            "ok": False,
                            "error": f"workspace create failed after relay mutation: {sync_error}",
                            "workspace_id": workspace_id,
                            "rollback": rollback,
                        })
                    if isinstance(payload, dict) and payload.get("reused"):
                        workspace_id = _workspace_id_from_relay_workspace_payload(payload)
                        try:
                            payload["local_enable"] = _workspace_cache.enable(workspace_id, body.get("local_path") or None)
                        except Exception as enable_error:
                            return self._json_response(503, {
                                "ok": False,
                                "reused": True,
                                "workspace_id": workspace_id,
                                "error": f"workspace reuse succeeded but local enable failed: {enable_error}",
                            })
                return self._json_response(status, payload)
            if parts == ["api", "workspaces", "sync"]:
                return self._json_response(200, self._sync_workspaces_from_relay())
            if len(parts) == 4 and parts[:2] == ["api", "workspaces"] and parts[3] == "enable":
                self._sync_workspaces_from_relay()
                return self._json_response(200, _workspace_cache.enable(parts[2], body.get("local_path") or None))
            if len(parts) == 4 and parts[:2] == ["api", "workspaces"] and parts[3] == "disable":
                return self._json_response(200, _workspace_cache.disable(parts[2]))
            if len(parts) == 4 and parts[:2] == ["api", "workspaces"] and parts[3] == "doctor":
                return self._json_response(200, _workspace_cache.doctor(parts[2]))
        except KeyError as e:
            return self._json_response(404, {"error": str(e)})
        except Exception as e:
            return self._json_response(503, {"error": str(e)})
        return self._json_response(404, {"error": "not found"})

    def _handle_workspace_delete(self):
        if _workspace_cache is None:
            return self._json_response(503, {"error": "workspace cache unavailable"})
        parts, _ = self._workspace_path_parts()
        if len(parts) == 3 and parts[:2] == ["api", "workspaces"]:
            try:
                workspace = _workspace_cache.get_workspace(parts[2])
                if workspace and workspace.get("workspace_authority") == "local":
                    return self._json_response(400, {
                        "ok": False,
                        "error": "local_only workspaces have no relay record to delete globally; use workspace disable to stop maintaining locally",
                    })
                status, payload = _relay_workspace_request("DELETE", f"/api/workspaces/{urllib.parse.quote(parts[2])}")
                if status < 400:
                    self._sync_workspaces_from_relay()
                return self._json_response(status, payload)
            except Exception as e:
                return self._json_response(503, {"error": str(e)})
        return self._json_response(404, {"error": "not found"})

    def _handle_peer_send(self, body):
        """task213: intern A → intern B peer message.

        Synchronous delivery confirmation only (status: delivered|undeliverable);
        B's reply is asynchronous (reverse call to the same endpoint). See
        intern-cli/builtin/peer_send.md for the LLM behavior contract.
        """
        from_name = body.get("from_intern_name", "")
        to_name = body.get("to_intern_name", "")
        to_project = body.get("to_project") or ""
        content = body.get("content", "")
        attachments = body.get("attachments") or []
        mode = body.get("mode") or "default"

        if not from_name or not to_name:
            return self._json_response(400, {"error": "missing_field"})
        if not isinstance(content, str):
            return self._json_response(400, {"error": "missing_field"})
        if not isinstance(mode, str) or mode not in _PEER_DELIVERY_MODES:
            return self._json_response(400, {"error": "invalid_mode"})

        from_project = _get_intern_project(from_name) or ""
        if not (_registry.find_chat_id(from_name, project=from_project) or _registry.find_chat_id(from_name)):
            return self._json_response(400, {"error": "invalid_from"})

        # /esc and mode=stop are control commands that bypass content size/empty
        # checks (their purpose is to interrupt; message text is irrelevant).
        if content != "/esc" and mode != "stop":
            if content == "":
                return self._json_response(400, {"error": "content_empty"})
            if len(content.encode("utf-8")) > 4096:
                return self._json_response(400, {"error": "content_too_long"})

        resolved_project = to_project
        if not resolved_project:
            if not (_relay_client and _relay_client.connected):
                return self._json_response(200, {
                    "status": "undeliverable", "reason": "relay_unreachable"})
            candidates = _relay_client.resolve_peer_target(to_name, timeout=5)
            if candidates is None:
                return self._json_response(200, {
                    "status": "undeliverable", "reason": "relay_unreachable"})
            if len(candidates) == 0:
                return self._json_response(200, {
                    "status": "undeliverable", "reason": "unknown_target"})
            if len(candidates) > 1:
                return self._json_response(200, {
                    "status": "undeliverable",
                    "reason": "ambiguous_target",
                    "candidates": candidates,
                })
            resolved_project = candidates[0]["project"]

        if resolved_project == from_project and to_name == from_name:
            return self._json_response(400, {"error": "self_send"})

        msg_id = uuid.uuid4().hex
        payload = {
            "from_intern_name": from_name,
            "from_project": from_project,
            "to_intern_name": to_name,
            "to_project": resolved_project,
            "content": content,
            "mode": mode,
            "msg_id": msg_id,
        }
        _attach_local_sender_contract(payload)
        if attachments:
            payload["attachments"] = attachments

        # Keep the same-machine fast path, but only when this daemon really
        # owns the target. Local chat registry files are just Feishu chat
        # mappings and may contain stale/imported entries.
        same_machine = _owns_local_peer_target(to_name, resolved_project)

        if same_machine:
            result = _deliver_peer_locally(payload)
        else:
            if not (_relay_client and _relay_client.connected):
                return self._json_response(200, {
                    "status": "undeliverable", "reason": "relay_unreachable"})
            result = _relay_client.forward_peer_message(payload, timeout=10)

        # task261: 仅 target_outdated 触发飞书 systemMessage（主管诉求"明确感知"
        # 边界 align 结果）。其他 reason 由 LLM 自行处理避免噪音。same-machine
        # 路径 target_mid 是 A 自己，永远 has_capability=True 不会触发此分支。
        if result.get("reason") == "target_outdated":
            _notify_peer_target_outdated(from_name, to_name, resolved_project)

        result = _augment_delivery_health_response(result, to_name, same_machine)
        return self._json_response(200, result)

    def _handle_goal_api(self, body, path_action=None):
        """task320: direct same-daemon intern goal API with explicit cancel support."""
        from_name = body.get("from_intern_name", "")
        to_name = body.get("to_intern_name", "")
        to_project = body.get("to_project") or ""
        from_project = body.get("from_project") or ""
        body_action = body.get("action") or ""
        action = path_action or body_action or "set"
        content = body.get("content")
        if content is None:
            content = body.get("objective", "")

        if not from_name or not to_name:
            return self._json_response(400, {"error": "missing_field"})
        if body_action and path_action and body_action != path_action:
            return self._json_response(400, {"error": "invalid_action"})
        if not isinstance(action, str) or action not in _GOAL_API_ACTIONS:
            return self._json_response(400, {"error": "invalid_action"})
        if not isinstance(content, str):
            return self._json_response(400, {"error": "missing_field"})
        from_project = from_project or _get_intern_project(from_name) or ""
        if not (_registry.find_chat_id(from_name, project=from_project) or _registry.find_chat_id(from_name)):
            return self._json_response(400, {"error": "invalid_from"})
        if action != "cancel":
            if content == "":
                return self._json_response(400, {"error": "content_empty"})
            if len(content.encode("utf-8")) > 4096:
                return self._json_response(400, {"error": "content_too_long"})
        resolved_project = to_project
        if not resolved_project:
            if not (_relay_client and _relay_client.connected):
                result = {"status": "undeliverable", "reason": "relay_unreachable"}
                return self._json_response(_goal_api_http_status(result), result)
            candidates = _relay_client.resolve_peer_target(to_name, timeout=5)
            if candidates is None:
                result = {"status": "undeliverable", "reason": "relay_unreachable"}
                return self._json_response(_goal_api_http_status(result), result)
            if len(candidates) == 0:
                result = {"status": "undeliverable", "reason": "unknown_target"}
                return self._json_response(_goal_api_http_status(result), result)
            if len(candidates) > 1:
                result = {
                    "status": "undeliverable",
                    "reason": "ambiguous_target",
                    "candidates": candidates,
                }
                return self._json_response(_goal_api_http_status(result), result)
            resolved_project = candidates[0]["project"]

        if resolved_project == from_project and to_name == from_name:
            return self._json_response(400, {"error": "self_send"})

        goal_id = body.get("goal_id") or body.get("client_goal_id") or uuid.uuid4().hex
        payload = {
            "from_intern_name": from_name,
            "from_project": from_project,
            "to_intern_name": to_name,
            "to_project": resolved_project,
            "content": content,
            "action": action,
            "goal_id": goal_id,
            "msg_id": goal_id,
        }
        _attach_local_sender_contract(payload)
        same_machine = _owns_local_peer_target(to_name, resolved_project)
        if not same_machine and payload.get("from_role") == _INDEPENDENT_ROLE:
            result = {
                "status": "undeliverable",
                "reason": "goal_independent_same_daemon_required",
            }
            return self._json_response(_goal_api_http_status(result), result)
        if same_machine:
            result = _deliver_goal_locally(payload)
        else:
            if not (_relay_client and _relay_client.connected):
                result = {"status": "undeliverable", "reason": "relay_unreachable"}
                return self._json_response(_goal_api_http_status(result), result)
            result = _relay_client.forward_goal_command(payload, timeout=10)
        if result.get("reason") == "target_outdated":
            _notify_peer_target_outdated(from_name, to_name, resolved_project)
        result = _augment_delivery_health_response(result, to_name, same_machine)
        result = _augment_goal_unconfirmed_response(result)
        return self._json_response(_goal_api_http_status(result), result)

    def _mailbox_error_response(self, exc):
        reason = str(exc)
        if reason in {"content_empty", "content_too_long", "message_ids_required"}:
            return self._json_response(400, {"error": reason})
        if reason in {
            "invalid_intern_name",
            "invalid_team_id",
            "unknown_team",
            "not_managed_worker",
            "ambiguous_team",
        }:
            return self._json_response(200, {"status": "undeliverable", "reason": reason})
        return self._json_response(500, {"error": reason})

    def _handle_mailbox_send(self, body):
        from_name = body.get("from_intern_name", "")
        to_name = body.get("to_intern_name", "")
        to_project = body.get("to_project") or ""
        content = body.get("content", "")
        team_id = body.get("team_id") or ""
        if not from_name or not to_name:
            return self._json_response(400, {"error": "missing_field"})
        if not isinstance(content, str):
            return self._json_response(400, {"error": "missing_field"})
        from_project = body.get("from_project") or _get_intern_project(from_name) or ""
        if not (_registry.find_chat_id(from_name, project=from_project) or _registry.find_chat_id(from_name)):
            return self._json_response(400, {"error": "invalid_from"})
        resolved_project = to_project
        if not resolved_project:
            if not (_relay_client and _relay_client.connected):
                return self._json_response(200, {
                    "status": "undeliverable", "reason": "relay_unreachable"})
            candidates = _relay_client.resolve_peer_target(to_name, timeout=5)
            if candidates is None:
                return self._json_response(200, {
                    "status": "undeliverable", "reason": "relay_unreachable"})
            if len(candidates) == 0:
                return self._json_response(200, {
                    "status": "undeliverable", "reason": "unknown_target"})
            if len(candidates) > 1:
                return self._json_response(200, {
                    "status": "undeliverable",
                    "reason": "ambiguous_target",
                    "candidates": candidates,
                })
            resolved_project = candidates[0]["project"]
        payload = {
            "from_intern_name": from_name,
            "from_project": from_project,
            "to_intern_name": to_name,
            "to_project": resolved_project,
            "team_id": team_id,
            "kind": body.get("kind") or "progress",
            "content": content,
            "related_task": body.get("related_task") or "",
            "related_pr": body.get("related_pr") or "",
            "client_message_id": body.get("client_message_id") or "",
        }
        if _owns_local_mail_target(to_name, resolved_project):
            result = _deliver_mail_locally(payload)
        else:
            if not (_relay_client and _relay_client.connected):
                return self._json_response(200, {
                    "status": "undeliverable", "reason": "relay_unreachable"})
            result = _relay_client.forward_mail_message(payload, timeout=10)
        return self._json_response(200, result)

    def _handle_mailbox_list(self, body):
        if self.path.startswith("/api/intern/mailbox/"):
            mailbox_intern_name = body.get("intern_name") or ""
        else:
            mailbox_intern_name = body.get("to_intern_name") or body.get("team_lead_name") or body.get("intern_name") or ""
        project = body.get("to_project") or body.get("project") or ""
        include_read = bool(body.get("include_read", False))
        if not mailbox_intern_name or not project:
            return self._json_response(400, {"error": "missing_field"})
        try:
            messages = team_mailbox.list_messages(
                project=project,
                intern_name=mailbox_intern_name,
                include_read=include_read,
            )
        except (ValueError, OSError) as exc:
            return self._mailbox_error_response(exc)
        return self._json_response(200, {
            "status": "ok",
            "messages": messages,
            "unread_count": len([message for message in messages if not message.get("read")]),
        })

    def _handle_mailbox_mark_read(self, body):
        if self.path.startswith("/api/intern/mailbox/"):
            mailbox_intern_name = body.get("intern_name") or ""
        else:
            mailbox_intern_name = body.get("to_intern_name") or body.get("team_lead_name") or body.get("intern_name") or ""
        project = body.get("to_project") or body.get("project") or ""
        message_ids = body.get("message_ids")
        if message_ids is None and body.get("message_id"):
            message_ids = [body.get("message_id")]
        if not mailbox_intern_name or not project:
            return self._json_response(400, {"error": "missing_field"})
        if not isinstance(message_ids, list) or not all(isinstance(item, str) for item in message_ids):
            return self._json_response(400, {"error": "message_ids_required"})
        try:
            marked = team_mailbox.mark_read(
                project=project,
                intern_name=mailbox_intern_name,
                message_ids=message_ids,
            )
        except (ValueError, OSError) as exc:
            return self._mailbox_error_response(exc)
        return self._json_response(200, {
            "status": "ok",
            "marked_read": marked,
            "marked_count": len(marked),
        })

    def do_GET(self):
        self._start_metric("GET")
        if self.path.startswith("/api/workspaces"):
            self._handle_workspace_get()
            return
        if self.path == "/api/status":
            status = {
                "running": True,
                "version": __version__,
                "script_hash": _script_hash,
                "uptime": time.time(),
                "registry_count": len(_registry.get_all()),
                "ws_clients": len(_ws_server.clients),
                "mode": "relay",
                "relay_connected": _relay_client.connected if _relay_client else False,
                "work_agents_root": WORK_AGENTS_ROOT,
                "instance_id": _relay_client.machine_id if _relay_client else None,
            }
            self._json_response(200, status)
        elif self.path == "/api/group/list":
            self._json_response(200, [
                {
                    "intern_name": entry.get("intern_name"),
                    "project": entry.get("project", ""),
                    "chat_id": entry.get("chat_id"),
                }
                for entry in _registry.get_all_entries()
            ])
        elif self.path.startswith("/api/intern/check_online"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            intern_name = params.get("intern_name", [""])[0]
            project = params.get("project", [""])[0]
            workspace_id = params.get("workspace_id", [""])[0]
            if not intern_name:
                return self._json_response(400, {"error": "intern_name required"})
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=project,
                workspace_id=workspace_id,
            )
            if not project:
                return self._json_response(400, {"error": "project required"})
            if _relay_client and _relay_client.connected:
                # Ask relay server
                result = _relay_client.check_online(intern_name, project=project, timeout=5)
                if result:
                    self._json_response(200, {
                        "intern_name": intern_name,
                        "project": project,
                        "online": result.get("online", False),
                        "machine_id": result.get("machine_id"),
                    })
                else:
                    self._json_response(503, {"error": "relay server timeout"})
            else:
                self._json_response(503, {"error": "relay not connected"})
        elif self.path.startswith("/api/chat/lookup"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            intern_name = params.get("intern", [""])[0]
            project = params.get("project", [""])[0]
            if not intern_name:
                return self._json_response(400, {"error": "intern param required"})
            # First check local registry
            chat_id = _registry.find_chat_id(intern_name, project=project)
            if chat_id:
                return self._json_response(200, {"intern_name": intern_name, "project": project, "chat_id": chat_id})
            # Fallback: ask relay
            if _relay_client and _relay_client.connected and _relay_client._relay_http_base:
                try:
                    resolved_project = project or _get_intern_project(intern_name)
                    query = urllib.parse.urlencode({"intern": intern_name, "project": resolved_project})
                    url = f"{_relay_client._relay_http_base}/api/chat/lookup?{query}"
                    resp = urllib.request.urlopen(url, timeout=5)
                    result = json.loads(resp.read())
                    chat_id = result.get("chat_id", "")
                    if chat_id:
                        _registry.register(intern_name, chat_id, project=resolved_project)
                    self._json_response(200, result)
                except Exception as e:
                    self._json_response(502, {"error": f"relay lookup failed: {e}"})
            else:
                self._json_response(200, {"intern_name": intern_name, "chat_id": ""})
        elif self.path.startswith("/api/question/poll"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            intern_name = params.get("intern_name", [""])[0]
            question_id = params.get("question_id", [""])[0]
            project = params.get("project", [""])[0]
            workspace_id = params.get("workspace_id", [""])[0]
            if not intern_name:
                return self._json_response(400, {"error": "intern_name required"})
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=project,
                workspace_id=workspace_id,
            )
            if not project:
                return self._json_response(400, {"error": "project required"})
            return self._json_response(200, _poll_pending_question(intern_name, project, question_id))

        else:
            self._json_response(404, {"error": "not found"})

    def do_POST(self):
        self._start_metric("POST")
        body = self._read_body()
        if body is None:
            return

        if self.path.startswith("/api/workspaces"):
            self._handle_workspace_post(body)
            return

        if self.path == "/api/group/create":
            intern_name = body.get("intern_name", "")
            if not intern_name:
                return self._json_response(400, {"error": "intern_name required"})
            raw_project = body.get("project")
            if not isinstance(raw_project, str) or not raw_project.strip():
                log.warning(f"[GROUP_CREATE] missing explicit project for intern={intern_name} request_project={raw_project!r}")
                return self._json_response(409, {"error": "project required for group create"})
            request_project = raw_project.strip()
            # Proxy to relay server for centralized chat management
            relay_http_base = _wait_for_relay_http_base(20)
            if not relay_http_base:
                return self._json_response(503, {"error": "relay not connected"})
            project = request_project
            owner_mobile = None
            owner_open_id = ""
            try:
                project, workspace_id = _canonical_intern_scope(
                    intern_name,
                    project=request_project,
                    workspace_id=body.get("workspace_id") or "",
                )
                intern_type = body.get("type") or _get_intern_type(intern_name, project=project or workspace_id)
                # Include owner identity from local _owner.json so relay uses
                # the requesting machine's configured owner.
                owner_mobile = _registry.load_owner_mobile() if _registry else None
                owner_open_id = _load_owner_open_id()
                payload = {"intern_name": intern_name, "type": intern_type, "project": project}
                machine_id = getattr(_relay_client, "machine_id", "") if _relay_client else ""
                if machine_id:
                    payload["machine_id"] = machine_id
                if workspace_id:
                    payload["workspace_id"] = workspace_id
                if owner_mobile:
                    payload["owner_mobile"] = owner_mobile
                if owner_open_id:
                    payload["owner_open_id"] = owner_open_id
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    f"{relay_http_base}/api/chat/create",
                    data=data,
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=60)
                result = json.loads(resp.read())
                chat_id = result.get("chat_id", "")
                if chat_id:
                    response, err_response = _finalize_group_create(
                        intern_name,
                        chat_id,
                        owner_mobile,
                        result,
                        project=project,
                        owner_open_id=owner_open_id,
                    )
                    if err_response:
                        return self._json_response(500, err_response)
                    return self._json_response(200, response)
                return self._json_response(200, result)
            except Exception as e:
                log.error(f"Relay proxy /api/chat/create failed: {e}")
                response, err_response = _recover_group_create_after_proxy_error(
                    intern_name,
                    project,
                    owner_mobile,
                    e,
                    owner_open_id=owner_open_id,
                )
                if response:
                    return self._json_response(200, response)
                return self._json_response(502, err_response)

        elif self.path == "/api/group/delete":
            intern_name = body.get("intern_name", "")
            if not intern_name:
                return self._json_response(400, {"error": "intern_name required"})
            # Proxy to relay server
            if not (_relay_client and _relay_client.connected and _relay_client._relay_http_base):
                return self._json_response(503, {"error": "relay not connected"})
            try:
                project, workspace_id = _canonical_intern_scope(
                    intern_name,
                    project=body.get("project") or "",
                    workspace_id=body.get("workspace_id") or "",
                )
                if not project:
                    return self._json_response(409, {"error": "project required for group delete"})
                payload = {"intern_name": intern_name, "project": project}
                if workspace_id:
                    payload["workspace_id"] = workspace_id
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    f"{_relay_client._relay_http_base}/api/chat/delete",
                    data=data, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=15)
                result = json.loads(resp.read())
                _registry.unregister(intern_name, project=project)
                log.info(f"Deleted group for {intern_name} via relay")
                self._json_response(200, result)
            except Exception as e:
                log.error(f"Relay proxy /api/chat/delete failed: {e}")
                return self._json_response(502, {"error": f"relay proxy failed: {e}"})

        elif self.path == "/api/group/sync":
            return self._json_response(
                410,
                {"error": "group sync from Feishu chat list is disabled in enterprise mode"},
            )

        elif self.path == "/api/group/trigger_mode":
            # task252: VS Code 右键 → 切换该 intern 群的 trigger_mode（all|at_only）。
            # daemon 把 intern_name 配上 project 后代理到 relay /api/chat/trigger_mode。
            intern_name = body.get("intern_name", "")
            mode = body.get("mode", "")
            if not intern_name or not mode:
                return self._json_response(400, {"error": "intern_name and mode required"})
            if not (_relay_client and _relay_client.connected and _relay_client._relay_http_base):
                return self._json_response(503, {"error": "relay not connected"})
            try:
                project, _workspace_id = _canonical_intern_scope(
                    intern_name,
                    project=body.get("project") or "",
                    workspace_id=body.get("workspace_id") or "",
                )
                if not project:
                    project = _get_intern_project(intern_name)
                payload = {"intern_name": intern_name, "project": project, "mode": mode}
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    f"{_relay_client._relay_http_base}/api/chat/trigger_mode",
                    data=data, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=15)
                result = json.loads(resp.read())
                log.info(f"[TRIGGER] proxied /api/chat/trigger_mode for {intern_name}: {result}")
                self._json_response(200, result)
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
                log.error(f"Relay proxy /api/chat/trigger_mode HTTP {e.code}: {err_body}")
                self._json_response(e.code, {"error": err_body})
            except Exception as e:
                log.error(f"Relay proxy /api/chat/trigger_mode failed: {e}")
                return self._json_response(502, {"error": f"relay proxy failed: {e}"})

        elif self.path == "/api/group/detail_mode":
            # task283/task375: daemon owns detail_mode truth source, but the
            # relay must orchestrate the write via WS RPC so it can also sync
            # the full config snapshot into the Feishu group description.
            intern_name = body.get("intern_name", "")
            mode = body.get("mode", "")
            if not intern_name or not mode:
                return self._json_response(400, {"error": "intern_name and mode required"})
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            if not project:
                project = _get_intern_project(intern_name)
            if mode not in daemon_chat_config.valid_detail_modes():
                return self._json_response(400, {"error": (
                    f"invalid mode {mode!r}; must be one of "
                    f"{list(daemon_chat_config.valid_detail_modes())}")})
            chat_id = _registry.find_chat_id(intern_name, project=project) if _registry else None
            if not chat_id:
                return self._json_response(404, {"error": (
                    f"no chat for intern={intern_name!r}")})
            try:
                if not (_relay_client and _relay_client.connected and _relay_client._relay_http_base):
                    return self._json_response(503, {"error": "relay not connected"})
                payload = {"intern_name": intern_name, "project": project, "mode": mode}
                machine_id = getattr(_relay_client, "machine_id", "") if _relay_client else ""
                if machine_id:
                    payload["machine_id"] = machine_id
                data = json.dumps(payload).encode()
                req = urllib.request.Request(
                    f"{_relay_client._relay_http_base}/api/chat/detail_mode",
                    data=data, method="POST",
                    headers={"Content-Type": "application/json"},
                )
                resp = urllib.request.urlopen(req, timeout=15)
                result = json.loads(resp.read())
                log.info(f"[DETAIL] proxied /api/chat/detail_mode for {intern_name}: {result}")
                self._json_response(200, result)
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
                log.error(f"Relay proxy /api/chat/detail_mode HTTP {e.code}: {err_body}")
                self._json_response(e.code, {"error": err_body})
            except Exception as e:
                log.error(f"Relay proxy /api/chat/detail_mode failed: {e}")
                return self._json_response(502, {"error": f"relay proxy failed: {e}"})

        elif self.path == "/api/light/set":
            # 灯控完全由 WS 注册表驱动，HTTP 只是手动触发刷新
            _refresh_lights()
            self._json_response(200, {"ok": True})

        elif self.path == "/api/message/send":
            intern_name = body.get("intern_name", "")
            text = body.get("text", "")
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            chat_id = _registry.find_chat_id(intern_name, project=project)
            if not chat_id:
                return self._json_response(404, {"error": f"no chat for {intern_name}"})
            msg_id, err = _api.send_message(chat_id, text)
            if err:
                return self._json_response(500, {"error": err})
            self._json_response(200, {"message_id": msg_id})

        elif self.path == "/api/message/update":
            msg_id = body.get("message_id", "")
            text = body.get("text", "")
            err = _api.update_message(msg_id, text)
            if err:
                return self._json_response(500, {"error": err})
            self._json_response(200, {"ok": True})

        elif self.path == "/api/message/finalize":
            msg_id = body.get("message_id", "")
            text = body.get("text", "")
            err = _api.update_message(msg_id, text)
            if err:
                return self._json_response(500, {"error": err})
            self._json_response(200, {"ok": True})

        elif self.path == "/api/message/reply":
            msg_id = body.get("message_id", "")
            text = body.get("text", "")
            err = _api.reply_message(msg_id, text)
            if err:
                return self._json_response(500, {"error": err})
            self._json_response(200, {"ok": True})

        elif self.path == "/api/intern/offline":
            intern_name = body.get("intern_name", "")
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            if not intern_name:
                return self._json_response(400, {"error": "intern_name required"})
            if not project:
                return self._json_response(400, {"error": "project required"})
            log.info(f"[HTTP] Intern offline notification: {intern_name}")
            threading.Thread(
                target=_handle_intern_offline_notification,
                args=(intern_name, project),
                daemon=True,
            ).start()
            self._json_response(200, {"ok": True})

        elif self.path == "/api/intern/request_refresh":
            # task223: Claude resume 等场景下，插件侧调这里触发一次 light 重新扫描。
            # daemon 不信任请求者声称的 online 状态，仅通过 _is_claude_process_running 扫 tmux pane 后再上报。
            intern_name = body.get("intern_name", "")
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            if not project:
                return self._json_response(400, {"error": "project required"})
            log.info(f"[HTTP] Request light refresh (intern={intern_name or '-'})")
            threading.Thread(target=_refresh_lights_for_intern, args=(intern_name, project), daemon=True).start()
            self._json_response(200, {"ok": True})

        elif self.path == "/api/intern/status_changed":
            intern_name = body.get("intern_name", "")
            if not intern_name:
                return self._json_response(400, {"error": "intern_name required"})
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            if not project:
                return self._json_response(400, {"error": "project required"})
            log.info(f"Intern status changed: {_online_key(intern_name, project)}")
            _notify_intern_status_changed(intern_name, project=project)
            self._json_response(200, {"ok": True})

        elif self.path in ("/api/intern/goal/set", "/api/intern/goal/cancel"):
            path_action = None
            if self.path.endswith("/set"):
                path_action = "set"
            elif self.path.endswith("/cancel"):
                path_action = "cancel"
            return self._handle_goal_api(body, path_action=path_action)

        elif self.path == "/api/helper/action":
            action = body.get("helper_action") or body.get("action") or ""
            machine_id = body.get("machine_id") or (_relay_client.machine_id if _relay_client else "")
            local_machine_id = _relay_client.machine_id if _relay_client else ""
            if not action:
                return self._json_response(400, {"ok": False, "error": "action required"})
            if not machine_id:
                return self._json_response(503, {"ok": False, "error": "local machine_id unavailable"})
            if local_machine_id and machine_id != local_machine_id:
                return self._json_response(400, {
                    "ok": False,
                    "error": "machine_id must match this daemon",
                    "local_machine_id": local_machine_id,
                })
            msg = dict(body)
            msg["helper_action"] = action
            msg["machine_id"] = machine_id
            try:
                result = handle_machine_helper_action(msg)
                result["ok"] = True
                return self._json_response(200, result)
            except Exception as e:
                log.error(f"[HELPER] local API action failed: {e}", exc_info=True)
                payload = {
                    "ok": False,
                    "error": str(e),
                    "helper_action": action,
                    "machine_id": machine_id,
                }
                payload.update(_machine_helper_runtime_error_payload(e))
                return self._json_response(500, payload)

        elif self.path == "/api/team/mailbox/send":
            return self._handle_mailbox_send(body)

        elif self.path == "/api/intern/mail/to":
            return self._handle_mailbox_send(body)

        elif self.path == "/api/team/mailbox/list":
            return self._handle_mailbox_list(body)

        elif self.path == "/api/intern/mailbox/list":
            return self._handle_mailbox_list(body)

        elif self.path == "/api/team/mailbox/mark-read":
            return self._handle_mailbox_mark_read(body)

        elif self.path == "/api/intern/mailbox/mark-read":
            return self._handle_mailbox_mark_read(body)

        elif self.path == "/api/intern/peer/send":
            return self._handle_peer_send(body)

        elif self.path == "/api/question/ask":
            intern_name = body.get("intern_name", "")
            tool_name = body.get("tool_name", "AskUserQuestion")
            questions = body.get("questions", [])
            prelude_file_path = body.get("prelude_file_path", "")
            metadata = body.get("metadata") or {}
            status, resp = _register_pending_question(
                intern_name,
                tool_name,
                questions,
                prelude_file_path,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
                metadata=metadata,
            )
            self._json_response(status, resp)

        elif self.path == "/api/codex/request_user_input/register":
            intern_name = body.get("intern_name", "")
            transcript_path = body.get("transcript_path", "")
            start_offset = body.get("offset", 0)
            status, resp = _register_codex_request_user_input_watcher(
                intern_name,
                transcript_path,
                start_offset,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            self._json_response(status, resp)

        elif self.path == "/api/question/cancel":
            intern_name = body.get("intern_name", "")
            question_id = body.get("question_id", "")
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            if not project:
                return self._json_response(400, {"error": "project required"})
            with _pq_lock:
                entry = _get_pending_question_locked(intern_name, project, question_id)
                if entry:
                    _mark_question_status_locked(entry, "cancelled", "cancelled")
                    _remove_pending_question_locked(intern_name, project, entry)
            log.info(
                f"[QUESTION] Cancelled pending question for {_online_key(intern_name, project)} "
                f"question_id={question_id or '-'}"
            )
            self._json_response(200, {"ok": True})

        elif self.path == "/api/question/invalidate":
            intern_name = body.get("intern_name", "")
            question_id = body.get("question_id", "")
            reason = body.get("reason", "") or "question_state_missing_after_restart"
            detail = body.get("detail", "") or ""
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            if not project:
                return self._json_response(400, {"error": "project required"})
            found = False
            with _pq_lock:
                entry = _get_pending_question_locked(intern_name, project, question_id)
                if not entry and question_id:
                    record = _question_entry_from_store_locked(project, intern_name, question_id)
                    entry = _hydrate_question_entry(record) if record else None
                if entry:
                    found = True
                    _mark_question_status_locked(entry, "invalidated", reason, detail)
                    active = _get_pending_question_locked(intern_name, project, question_id)
                    if active:
                        _remove_pending_question_locked(intern_name, project, active)
            _update_question_card_to_invalid(
                intern_name,
                reason,
                detail=detail,
                project=project,
                question_id=question_id,
            )
            log.info(
                f"[QUESTION] Invalidated pending question for {_online_key(intern_name, project)} "
                f"question_id={question_id or '-'} reason={reason} found={found}"
            )
            self._json_response(200, {"ok": True, "found": found, "reason": reason})

        elif self.path == "/api/question/timeout":
            intern_name = body.get("intern_name", "")
            question_id = body.get("question_id", "")
            hours = body.get("hours", 6)
            project, _workspace_id = _canonical_intern_scope(
                intern_name,
                project=body.get("project") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            if not project:
                return self._json_response(400, {"error": "project required"})
            with _pq_lock:
                entry = _get_pending_question_locked(intern_name, project, question_id)
                if entry:
                    _mark_question_status_locked(entry, "timed_out", "timed_out")
            _update_question_card_to_timeout(intern_name, hours, project=project, question_id=question_id)
            with _pq_lock:
                entry = _get_pending_question_locked(intern_name, project, question_id)
                if entry:
                    _remove_pending_question_locked(intern_name, project, entry)
            log.info(
                f"[QUESTION] Timeout notified for {_online_key(intern_name, project)} "
                f"({hours}h) question_id={question_id or '-'}"
            )
            self._json_response(200, {"ok": True})

        elif self.path == "/api/message/send_to_owner":
            text = body.get("text", "")
            if not text:
                return self._json_response(400, {"error": "text required"})
            owner_mobile = os.environ.get("INTERN_OWNER_MOBILE", "")
            if not owner_mobile:
                return self._json_response(500, {"error": "INTERN_OWNER_MOBILE not configured"})
            open_id, err = _api.mobile_to_open_id(owner_mobile)
            if err or not open_id:
                return self._json_response(500, {"error": f"mobile_to_open_id failed: {err}"})
            msg_id, err = _api.send_to_user(open_id, text)
            if err:
                return self._json_response(500, {"error": f"send_to_user failed: {err}"})
            log.info(f"[HTTP] Sent message to owner ({owner_mobile})")
            self._json_response(200, {"message_id": msg_id})

        elif self.path == "/api/shutdown":
            log.info("Shutdown requested via API")
            self._json_response(200, {"ok": True})
            threading.Thread(target=lambda: (_shutdown_event.set()), daemon=True).start()

        else:
            self._json_response(404, {"error": "not found"})

    def do_DELETE(self):
        self._start_metric("DELETE")
        if self.path.startswith("/api/workspaces"):
            self._handle_workspace_delete()
            return
        self._json_response(404, {"error": "not found"})


# ══════════════════════════════════════════
# 飞书消息接收 + 路由
# ══════════════════════════════════════════

def parse_text(content, msg_type):
    try:
        data = json.loads(content)
        if msg_type == "text":
            return data.get("text", "").strip()
        elif msg_type == "post":
            texts = []
            # Feishu API v2 flat format: {"title":"...","content":[[{"tag":"text","text":"..."}]]}
            content_lines = data.get("content", [])
            if isinstance(content_lines, list) and content_lines and isinstance(content_lines[0], list):
                for line in content_lines:
                    for elem in line:
                        if isinstance(elem, dict) and elem.get("tag") == "text":
                            texts.append(elem.get("text", ""))
            else:
                # Legacy format: {"zh_cn": {"title":"...","content":[...]}}
                for lang_content in data.values():
                    if isinstance(lang_content, dict):
                        for line in lang_content.get("content", []):
                            for elem in line:
                                if elem.get("tag") == "text":
                                    texts.append(elem.get("text", ""))
            return " ".join(texts).strip()
    except Exception:
        pass
    return content.strip() if isinstance(content, str) else ""


def create_message_handler(api, registry, ws_server):
    start_time_ms = str(int(time.time() * 1000))

    def handle_message(data):
        try:
            msg = data.event.message
            sender = data.event.sender
            chat_id = msg.chat_id
            message_id = msg.message_id
            msg_type = msg.message_type
            content = msg.content

            if sender and sender.sender_type == "app":
                return

            # 忽略 daemon 启动前的旧消息（Lark SDK 可能在 WebSocket 连接时投递积压消息）
            create_time = getattr(msg, 'create_time', '') or ''
            if create_time and create_time < start_time_ms:
                log.info(f"Ignoring old message {message_id} (create_time={create_time} < start={start_time_ms})")
                return

            text = parse_text(content, msg_type)
            if not text:
                return

            intern_info = registry.find_intern_info(chat_id)
            intern_name = intern_info.get("intern_name", "")
            project = intern_info.get("project", "")
            if not intern_name:
                log.warning(f"No intern for chatId={chat_id}")
                return

            log.info(f"Feishu msg for {_online_key(intern_name, project)}: {text[:80]}")

            intern_type = _get_intern_type_scoped(intern_name, project=project)

            # ── 检查是否有 pending question 等待回答 ──
            if _try_answer_pending_question(intern_name, text, project=project):
                api.reply_message(message_id, f"✅ 已收到回复")
                return

            if text.strip().startswith("/"):
                _handle_feishu_command(intern_name, text.strip(), message_id, project=project)
                return

            if _is_tmux_intern_type(intern_type):
                # Claude/Codex intern: route via tmux send-keys
                _set_pending_supervisor_origin(intern_name, message_id, chat_id, project=project)
                if intern_type == "codex":
                    success, err = _send_to_codex_tmux(intern_name, text, delivery_id=message_id, project=project)
                else:
                    success, err = _send_to_claude_tmux(intern_name, text, delivery_id=message_id, project=project)
                if success:
                    log.info(f"[ROUTE] Sent to {intern_type.capitalize()} intern {intern_name} via tmux")
                else:
                    if err in _TMUX_SUBMIT_UNCONFIRMED_ERRORS:
                        if _should_reply_tmux_unconfirmed(err):
                            api.reply_message(message_id, _format_tmux_unconfirmed_message(intern_name, err))
                        log.warning(f"[ROUTE] Codex submit unconfirmed for {intern_name}: {err}")
                        return
                    # tmux session not found / process not running → offline
                    _clear_pending_supervisor_origin(intern_name, project=project)
                    api.reply_message(message_id, f"⚠️ {intern_name} 当前离线")
                    log.info(f"[ROUTE] {intern_type.capitalize()} intern {intern_name} offline: {err}")
                    target_chat = registry.find_chat_id(intern_name, project=project)
                    if target_chat:
                        api.update_chat(target_chat, name=_build_group_name(intern_name, is_online=False, project=project))
                    _notify_intern_status_changed(intern_name, project=project)
            else:
                # Copilot intern: route via WebSocket to VS Code plugin
                _set_pending_supervisor_origin(intern_name, message_id, chat_id, project=project)
                payload = {
                    "type": "feishu_message",
                    "intern_name": intern_name,
                    "project": project,
                    "text": text,
                    "message_id": message_id,
                    "chat_id": chat_id,
                }
                delivered = ws_server.route_to_active(intern_name, payload, project=project)
                if not delivered:
                    _clear_pending_supervisor_origin(intern_name, project=project)
                    api.reply_message(message_id, f"⚠️ {intern_name} 当前不在线")
                    # Notify relay that Copilot went offline (align with Claude failure path)
                    if _relay_client and _relay_client.connected:
                        _relay_client.send_intern_offline(intern_name, project=project)
                    _notify_intern_status_changed(intern_name, project=project)
                    log.info(f"[ROUTE] {intern_name} not active in any window, sent offline")

        except Exception as e:
            log.error(f"Message handler error: {e}", exc_info=True)

    return handle_message


# ══════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════

def main():
    global _api, _registry, _workspace_cache, _ws_server, _shutdown_event, _relay_client

    log.info("=" * 60)
    log.info(f"Feishu Daemon v{__version__} starting...")

    # ── 诊断 hook：SIGUSR1 → 把所有线程 Python 栈打到 daemon log ──
    # 容器无 cap_sys_ptrace 时 py-spy/gdb 都用不了；这是唯一能在不重启进程的
    # 前提下取得运行时栈的办法。卡死时直接 `kill -USR1 <pid>`。
    # 同时启用 faulthandler 让 segfault 也能出栈。
    fault_dir = LOG_DIR
    os.makedirs(fault_dir, exist_ok=True)
    fault_log = open(os.path.join(fault_dir, "feishu_daemon_faults.log"), "a")
    faulthandler.enable(file=fault_log, all_threads=True)
    faulthandler.register(signal.SIGUSR1, file=fault_log, all_threads=True, chain=False)
    log.info(f"faulthandler registered: SIGUSR1 → {fault_log.name}")

    if os.path.exists(OLD_PID_FILE):
        try:
            os.remove(OLD_PID_FILE)
            log.info(f"Removed legacy PID file: {OLD_PID_FILE}")
        except Exception as e:
            log.warning(f"Failed to remove legacy PID file {OLD_PID_FILE}: {e}")

    _shutdown_event = threading.Event()

    # 0. 加载 relay 配置（从 _owner.json）
    relay_cfg = load_relay_config()
    log.info(f"Mode: relay (url={relay_cfg['relay_url']}, instance_id={relay_cfg['machine_id']})")

    # 1. 凭据 + API
    credential_loader = lambda: load_credentials(relay_cfg)
    app_id, app_secret = load_credentials(relay_cfg)
    _api = FeishuAPI(app_id, app_secret, credential_loader=credential_loader)
    log.info(f"Credentials: app_id={app_id[:8]}...")
    enrich_owner_identity_at_startup(_api)

    # 2. Registry
    registry_dir = os.path.join(WORK_AGENTS_ROOT, ".feishu_registry")
    _registry = RegistryManager(registry_dir)
    _workspace_cache = WorkspaceCache(WORK_AGENTS_ROOT)
    log.info(f"Registry: {len(_registry.get_all())} interns")

    # 3. 企业模式下 registry 只接受本机创建/relay 合同写入，不从飞书历史群反向恢复。
    log.info("[REGISTRY] Feishu chat list sync disabled in enterprise mode")

    # 4. WebSocket server (binds to ephemeral port; actual_port populated after start)
    _ws_server = WSServer(WS_PORT)
    _ws_server.start()

    # 5. HTTP server (bind to ephemeral port if HTTP_PORT == 0)
    http_server = DaemonHTTPServer(("localhost", HTTP_PORT), APIHandler)
    actual_http_port = http_server.server_address[1]
    actual_ws_port = _ws_server.actual_port
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()
    log.info(f"HTTP API on http://localhost:{actual_http_port}")

    # 现在两个服务均已 bind 到实际端口 → 写 PID file (JSON)
    # task267: bundle_dir = daemon 自身所在 bundled-cli 根目录（__file__ 上溯 3 层：
    # <install>/bundled-cli/scripts/daemon/feishu_daemon.py → <install>/bundled-cli）；
    # context_loader 等 consumer 用 `<bundle_dir>/builtin/peer_send.md` 拼路径
    # 指向与协议版本绑定的 doc，避免随 intern PR 漂移。
    _bundle_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    pid_payload = {
        "pid": os.getpid(),
        "instance_id": relay_cfg["machine_id"],
        "work_agents_root": WORK_AGENTS_ROOT,
        "http_port": actual_http_port,
        "ws_port": actual_ws_port,
        "started_at": datetime.now().isoformat(),
        "script_hash": _script_hash,
        "version": __version__,
        "bundle_dir": _bundle_dir,
    }
    _ensure_pid_file_points_to_self(pid_payload, "startup")
    threading.Thread(
        target=_pid_file_watchdog,
        args=(pid_payload, _shutdown_event),
        name="pid_file_watchdog",
        daemon=True,
    ).start()

    # 6. 入站消息来源（relay 模式）
    _relay_client = RelayClient(
        relay_url=relay_cfg["relay_url"],
        relay_token=relay_cfg["relay_token"],
        machine_id=relay_cfg["machine_id"],
        registry=_registry,
        ws_server=_ws_server,
        owner_mobile=relay_cfg.get("owner_mobile", ""),
        owner_open_id=relay_cfg.get("owner_open_id", ""),
        ip=relay_cfg.get("ip", ""),
        ssh_port=relay_cfg.get("ssh_port", 22),
    )
    _relay_client.start()
    log.info(f"Relay client connecting to {relay_cfg['relay_url']} as '{relay_cfg['machine_id']}'")

    # 6b. 周期状态上报（interns_state，5s 一次，只更新 registry，不触发飞书灯控）
    threading.Thread(
        target=_report_interns_state,
        args=(_shutdown_event,),
        name="interns_state_reporter",
        daemon=True,
    ).start()
    log.info("interns_state reporter started (5s interval)")

    threading.Thread(
        target=_feishu_buffer_flush_loop,
        args=(_shutdown_event,),
        name="feishu_buffer_flush_loop",
        daemon=True,
    ).start()
    log.info("feishu buffer flush loop started")

    threading.Thread(
        target=_codex_goal_snapshot_loop,
        args=(_shutdown_event,),
        name="codex_goal_snapshot_loop",
        daemon=True,
    ).start()
    log.info(f"codex goal snapshot loop started ({CODEX_GOAL_SNAPSHOT_INTERVAL_SECONDS}s interval)")

    _peer_delivery_manager.start(_shutdown_event)
    log.info("peer delivery manager started")

    # 7. 信号处理
    def signal_handler(sig, frame):
        log.info("Received signal, shutting down...")
        _shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    log.info("Daemon ready. Waiting for shutdown...")
    _shutdown_event.wait()

    # 清理：仅删除指向自己的 pid 文件，避免误删后起 daemon 写入的新文件。
    # Use os._exit at the end: this daemon embeds third-party websocket/client
    # loops and request threads; sys.exit() can leave the process alive after
    # the pidfile has been removed.
    http_server.shutdown()
    http_server.server_close()
    current, read_error = _read_pid_file_for_repair()
    if current and current.get("pid") == os.getpid():
        try:
            os.remove(PID_FILE)
            log.info(f"PID file removed on shutdown: {PID_FILE} (pid={os.getpid()})")
        except OSError as exc:
            log.warning(f"Failed to remove PID file on shutdown: {exc}")
    elif read_error:
        log.info(f"PID file already unavailable on shutdown: {read_error}")
    else:
        log.info("PID file points elsewhere on shutdown; leaving it untouched")
    log.info("Daemon stopped.")
    logging.shutdown()
    os._exit(0)


if __name__ == "__main__":
    main()
