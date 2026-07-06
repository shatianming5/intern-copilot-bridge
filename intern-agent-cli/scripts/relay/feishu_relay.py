#!/usr/bin/env python3
"""
Feishu Relay Server — 入站消息中继（多用户支持）

职责：
1. 唯一持有飞书 app credentials，维护 lark_oapi WebSocket 长连接
2. 接收飞书入站消息，按 chat_id → intern_name → machine_id 路由
3. 转发消息到对应 Local Agent（通过 Relay WebSocket）
4. 目标机器离线时代替回复"机器离线"
5. HTTP 监控 API

启动：python3 feishu_relay.py --root /path/to/work-agents
停止：Ctrl+C / SIGTERM
"""

__version__ = "1.0.0"

import json
import os
import sys
import re
import signal
import logging
import getpass
import time
import threading
import asyncio
import socket
from collections import OrderedDict, deque
import argparse
import hmac
import hashlib
import base64
import urllib.request
import urllib.error
import urllib.parse
import uuid as _uuid
import zipfile
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone
from pathlib import Path

# scripts/ is one level up; lib/ is under intern-cli root.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from lib.enterprise_policy import (
    load_enterprise_policy as load_enterprise_policy_file,
    load_enterprise_secrets,
    redact_secrets,
    resolve_secret_value,
)
from lib.enterprise_paths import relay_owner_path, relay_policy_path, relay_secrets_path, user_config_backups_dir
from lib.log_paths import current_version_key, system_log_dir, transfer_log_dir
from lib.machine_config_policy import (
    MachineConfigPolicyError,
    env_switch_schema as enterprise_env_switch_schema,
    env_switch_state_for_machine,
    normalize_env_switch_state,
    save_env_switch_state,
)
from lib.slash_commands import (
    NATIVE_SLASH_COMMANDS_BY_INTERN_TYPE,
    RELAY_CONFIG_COMMAND,
    RELAY_DETAIL_MODE_COMMAND,
    RELAY_HELPER_COMMAND,
    RELAY_MACHINE_CONFIG_COMMAND,
    RELAY_TRIGGER_MODE_COMMAND,
    RELAY_UPGRADE_COMMAND,
    format_available_slash_commands,
)

import chat_config

# ── 日志 ──────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("feishu_relay")

CLIENT_RELEASE_SCHEMA = "intern-agents.client-release.v1"
CLIENT_RELEASE_FEED_SCHEMA = "intern-agents.client-release-feed.v1"
CLIENT_RELEASE_PATTERN = re.compile(r"^intern-agent-helper-(\d+(?:\.\d+)+(?:[-.][A-Za-z0-9]+)?)\.vsix$")


def _client_release_version_parts(version):
    main = str(version or "").strip().split("-", 1)[0]
    parts = []
    for piece in main.split("."):
        if not piece.isdigit():
            return (0,)
        parts.append(int(piece))
    return tuple(parts or [0])


def _client_release_dirs(root_dir):
    return [path for path in _client_release_dir_candidates(root_dir) if path.is_dir()]


def _client_release_dir_candidates(root_dir):
    root = Path(root_dir or ".").resolve()
    candidates = []
    env_dir = os.environ.get("INTERN_AGENT_RELEASES_DIR", "").strip()
    if env_dir:
        candidates.append(Path(env_dir).expanduser())
    try:
        owner_path = relay_owner_path(root)
        owner = json.loads(Path(owner_path).read_text(encoding="utf-8"))
        owner_dir = str(owner.get("client_releases_dir") or "").strip() if isinstance(owner, dict) else ""
        if owner_dir:
            candidates.append(Path(owner_dir).expanduser())
    except Exception:
        pass
    candidates.append(root / ".feishu_relay" / "releases")
    seen = set()
    result = []
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        result.append(resolved)
    return result


def _read_vsix_json_member(vsix_path, member, *, required):
    try:
        with zipfile.ZipFile(vsix_path) as zf:
            return json.loads(zf.read(member).decode("utf-8"))
    except KeyError:
        if required:
            raise RuntimeError(f"VSIX {vsix_path} missing {member}")
        return {}
    except Exception:
        if required:
            raise
        return {}


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


CLIENT_RELEASE_HASH_SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache", "llm_intern_logs"}


def _client_release_hash_skip(relative):
    parts = Path(relative).parts
    return relative == ".vsixmanifest" or any(part in CLIENT_RELEASE_HASH_SKIP_DIRS for part in parts) or relative.endswith(".pyc")


def _normalize_client_release_hash_data(name, data):
    if name != "extension/package.json":
        return data
    try:
        package_json = json.loads(data.decode("utf-8"))
    except Exception:
        return data
    if isinstance(package_json, dict):
        package_json.pop("__metadata", None)
        return json.dumps(package_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return data


def _client_release_content_sha256(path):
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as zf:
        members = [
            name for name in zf.namelist()
            if name.startswith("extension/") and not name.endswith("/")
            and not _client_release_hash_skip(name[len("extension/"):])
        ]
        for name in sorted(members):
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(_normalize_client_release_hash_data(name, zf.read(name)))
            digest.update(b"\0")
    return digest.hexdigest()


def _client_release_from_vsix(path, *, base_url=""):
    path = Path(path)
    match = CLIENT_RELEASE_PATTERN.match(path.name)
    if not match:
        return None
    package_json = _read_vsix_json_member(path, "extension/package.json", required=True)
    build_meta = _read_vsix_json_member(path, "extension/build-meta.json", required=False)
    version = str(package_json.get("version") or match.group(1)).strip()
    download_path = f"/api/releases/vsix/{urllib.parse.quote(path.name)}"
    stat = path.stat()
    release = {
        "schema": CLIENT_RELEASE_SCHEMA,
        "client_only": True,
        "relay_upgrade": "manual_admin",
        "extension_id": "llm-intern-agents.intern-agent-helper",
        "version": version,
        "filename": path.name,
        "size_bytes": stat.st_size,
        "sha256": _sha256_file(path),
        "content_sha256": _client_release_content_sha256(path),
        "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "release_dir": os.fspath(path.parent),
        "download_path": download_path,
        "download_url": (base_url.rstrip("/") + download_path) if base_url else download_path,
        "metadata": {
            "package": {
                "name": package_json.get("name", ""),
                "publisher": package_json.get("publisher", ""),
                "version": package_json.get("version", ""),
            },
            "build": build_meta if isinstance(build_meta, dict) else {},
        },
    }
    return release


def _discover_latest_client_release(root_dir, *, base_url=""):
    releases = []
    for release_dir in _client_release_dirs(root_dir):
        for path in release_dir.glob("intern-agent-helper-*.vsix"):
            release = _client_release_from_vsix(path, base_url=base_url)
            if release:
                releases.append(release)
    if not releases:
        return None
    releases.sort(
        key=lambda item: (
            _client_release_version_parts(item["version"]),
            item["mtime"],
            item["filename"],
        ),
        reverse=True,
    )
    return releases[0]


def _find_client_release_file(root_dir, filename):
    if "/" in filename or "\\" in filename or not CLIENT_RELEASE_PATTERN.match(filename):
        return None
    for release_dir in _client_release_dirs(root_dir):
        path = release_dir / filename
        if path.is_file():
            return path
    return None

BASE_URL = "https://open.feishu.cn/open-apis"
RECONNECT_GRACE_SECONDS = 15
RELAY_WS_MAX_SIZE_BYTES = 16 * 1024 * 1024
ENTERPRISE_POLICY_REFRESH_SECONDS = 10.0
_enterprise_policy_cache_lock = threading.Lock()
_enterprise_policy_cache = {
    "root": "",
    "loaded_at": 0.0,
    "policy": {},
    "error": "",
}

# task228: 入站附件上限。单文件超过即 reject（reply_message 提示主管压缩或 scp），
# 不走 HTTP URL fallback。relay-daemon websocket 显式设置 16 MiB max_size，
# 留出 payload 其他字段与 base64 ~1.34× 开销后，10 MiB binary 约等于
# ~13.4 MiB base64，安全在 16 MiB 内。
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024

_LOCAL_RELAY_UPLOAD_HOSTS = {"", "localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"}
_STARTUP_ENV_ALLOWLIST = {
    "SSH_CONNECTION",
    "SSH_CLIENT",
    "SSH_TTY",
    "VSCODE_IPC_HOOK_CLI",
}
_STARTUP_ENV_SENSITIVE_RE = re.compile(
    r"(TOKEN|SECRET|KEY|PASSWORD|AUTH|COOKIE)",
    re.IGNORECASE,
)


def _unknown_if_empty(value):
    text = str(value or "").strip()
    return text if text else "unknown"


def _parse_ssh_connection_server_port(value):
    parts = str(value or "").strip().split()
    if len(parts) >= 4 and parts[3].isdigit():
        return parts[3]
    return ""


def _parse_ssh_client_server_port(value):
    parts = str(value or "").strip().split()
    if len(parts) >= 3 and parts[2].isdigit():
        return parts[2]
    return ""


def _startup_ssh_server_port(environ, cfg=None):
    environ = environ or {}
    cfg = cfg or {}
    port = _parse_ssh_connection_server_port(environ.get("SSH_CONNECTION", ""))
    if port:
        return port
    port = _parse_ssh_client_server_port(environ.get("SSH_CLIENT", ""))
    if port:
        return port
    for key in ("ssh_port", "relay_ssh_port", "server_ssh_port"):
        value = str(cfg.get(key) or "").strip()
        if value:
            return value
    return "unknown"


def _format_ssh_connection(value):
    parts = str(value or "").strip().split()
    if len(parts) >= 4:
        return f"{parts[0]}:{parts[1]} -> {parts[2]}:{parts[3]}"
    return "unknown"


def _format_ssh_client(value):
    parts = str(value or "").strip().split()
    if len(parts) >= 3:
        return f"{parts[0]}:{parts[1]} -> server_port:{parts[2]}"
    return "unknown"


def _safe_startup_env_snapshot(environ, allowlist=None):
    environ = environ or {}
    allowlist = set(allowlist or _STARTUP_ENV_ALLOWLIST)
    snapshot = {}
    for key in sorted(allowlist):
        raw = str(environ.get(key) or "").strip()
        if _STARTUP_ENV_SENSITIVE_RE.search(key):
            snapshot[key] = "redacted" if raw else "unknown"
        elif key == "VSCODE_IPC_HOOK_CLI":
            snapshot[key] = "present" if raw else "absent"
        elif key == "SSH_CONNECTION":
            snapshot[key] = _format_ssh_connection(raw)
        elif key == "SSH_CLIENT":
            snapshot[key] = _format_ssh_client(raw)
        else:
            snapshot[key] = raw if raw else "unknown"
    return snapshot


def _safe_getfqdn():
    try:
        return socket.getfqdn()
    except Exception:
        return ""


def _safe_gethostname(environ):
    if environ.get("HOSTNAME"):
        return environ.get("HOSTNAME")
    try:
        return socket.gethostname()
    except Exception:
        return ""


def _safe_detect_primary_ip():
    try:
        return _detect_relay_reachable_host()
    except Exception:
        return ""


def _safe_get_current_user(environ):
    if environ.get("USER") or environ.get("LOGNAME"):
        return environ.get("USER") or environ.get("LOGNAME")
    try:
        return getpass.getuser()
    except Exception:
        return ""


def collect_startup_machine_identity(cfg, root, environ=None):
    environ = dict(os.environ if environ is None else environ)
    cfg = cfg or {}
    work_agents_root = os.fspath(root or "")
    return {
        "hostname": _unknown_if_empty(_safe_gethostname(environ)),
        "fqdn": _unknown_if_empty(_safe_getfqdn()),
        "ip": _unknown_if_empty(_safe_detect_primary_ip()),
        "ssh_port": _startup_ssh_server_port(environ, cfg),
        "user": _unknown_if_empty(_safe_get_current_user(environ)),
        "pid": str(os.getpid()),
        "work_agents_root": _unknown_if_empty(work_agents_root),
        "relay_root": _unknown_if_empty(os.path.join(work_agents_root, ".feishu_relay") if work_agents_root else ""),
        "http_port": _unknown_if_empty(cfg.get("relay_http_port")),
        "ws_port": _unknown_if_empty(cfg.get("relay_ws_port")),
        "env": _safe_startup_env_snapshot(environ),
    }


def render_startup_notification(identity, *, started_at):
    identity = identity or {}
    env = identity.get("env") if isinstance(identity.get("env"), dict) else {}
    return "\n".join([
        "🚀 飞书 Relay 已启动",
        "",
        "Machine:",
        f"- hostname: {_unknown_if_empty(identity.get('hostname'))}",
        f"- fqdn: {_unknown_if_empty(identity.get('fqdn'))}",
        f"- ip: {_unknown_if_empty(identity.get('ip'))}",
        f"- ssh_port: {_unknown_if_empty(identity.get('ssh_port'))}",
        f"- user: {_unknown_if_empty(identity.get('user'))}",
        f"- pid: {_unknown_if_empty(identity.get('pid'))}",
        "",
        "Relay:",
        f"- http_port: {_unknown_if_empty(identity.get('http_port'))}",
        f"- ws_port: {_unknown_if_empty(identity.get('ws_port'))}",
        f"- root: {_unknown_if_empty(identity.get('relay_root'))}",
        f"- work_agents_root: {_unknown_if_empty(identity.get('work_agents_root'))}",
        f"- started_at: {_unknown_if_empty(started_at)}",
        "",
        "SSH env:",
        f"- SSH_CONNECTION: {_unknown_if_empty(env.get('SSH_CONNECTION'))}",
        f"- SSH_CLIENT: {_unknown_if_empty(env.get('SSH_CLIENT'))}",
        f"- SSH_TTY: {_unknown_if_empty(env.get('SSH_TTY'))}",
        f"- VSCODE_IPC_HOOK_CLI: {_unknown_if_empty(env.get('VSCODE_IPC_HOOK_CLI'))}",
    ])


def _detect_relay_reachable_host():
    """Return this relay's routable host/IP for peer daemons.

    UDP connect only asks the kernel which local address would be used for an
    outbound route; it does not send traffic to the remote address.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
        finally:
            sock.close()
        if host:
            return host
    except OSError:
        pass
    return socket.gethostbyname(socket.gethostname())


def _split_host_header(host_header, default_port):
    host_header = (host_header or "").strip()
    if host_header.startswith("["):
        end = host_header.find("]")
        if end != -1:
            host = host_header[:end + 1]
            rest = host_header[end + 1:]
            port = rest[1:] if rest.startswith(":") else str(default_port)
            return host, port
    if ":" in host_header:
        host, port = host_header.rsplit(":", 1)
        return host, port or str(default_port)
    return host_header, str(default_port)


def _format_upload_host(host):
    if ":" in host and not (host.startswith("[") and host.endswith("]")):
        return f"[{host}]"
    return host


def _build_relay_upload_url(host_header, server_port, request_id):
    host, port = _split_host_header(host_header, server_port)
    if host.lower() in _LOCAL_RELAY_UPLOAD_HOSTS:
        host = _detect_relay_reachable_host()
    return f"http://{_format_upload_host(host)}:{port}/api/admin/upload_logs?request_id={request_id}"


# task228: Content-Disposition 文件名解析，仅供 FeishuAPI.download_message_resource 使用。
# 飞书返回 `filename*=UTF-8''xxx`（RFC 5987 编码）或 `filename="xxx"` 两种形式。
_CD_FILENAME_STAR_RE = re.compile(r"filename\*=UTF-8''([^;]+)", re.IGNORECASE)
_CD_FILENAME_RE = re.compile(r'filename="([^"]+)"|filename=([^;]+)', re.IGNORECASE)


def _parse_content_disposition_filename(cd):
    if not cd:
        return ""
    m = _CD_FILENAME_STAR_RE.search(cd)
    if m:
        try:
            return urllib.parse.unquote(m.group(1).strip())
        except Exception:
            return ""
    m = _CD_FILENAME_RE.search(cd)
    if m:
        return (m.group(1) or m.group(2) or "").strip()
    return ""

# Script content hash at startup — used for auto-update detection
def _compute_script_hash():
    """Compute deterministic hash of all files in this script's directory (relay folder)."""
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
_log_version_key = ""


# ══════════════════════════════════════════
# Workspace registry helpers
# ══════════════════════════════════════════

WORKSPACE_REGISTRY_SCHEMA = "intern-agents.relay-workspaces.v1"
WORKSPACE_REGISTRY_VERSION = 1
WORKSPACE_MODES = {"repo_dotdir", "metadata_branch"}
WORKSPACE_PROVIDERS = {"github", "codeup", "gitlab", "local"}
WORKSPACE_ID_RE = re.compile(r"^[a-z0-9_][a-z0-9_.-]*$")


def _now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class RuntimeMetrics:
    def __init__(self, component, latency_limit=256):
        self._component = component
        self._latency_limit = latency_limit
        self._started_at = _now_iso()
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
            item["last_at"] = _now_iso()
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
                "updated_at": _now_iso(),
                "interfaces": interfaces,
            }


_relay_metrics = RuntimeMetrics("relay")


def _safe_workspace_id(value):
    raw = (value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._").lower()
    return safe or _uuid.uuid4().hex[:12]


def _validate_workspace_id(value):
    workspace_id = (value or "").strip()
    if not WORKSPACE_ID_RE.fullmatch(workspace_id):
        raise ValueError("workspace_id must match [a-z0-9_][a-z0-9_.-]*")
    return workspace_id


def _workspace_id_from_body(body):
    if "workspace_id" in body:
        return _validate_workspace_id(body.get("workspace_id"))
    display = (body.get("display_name") or body.get("name") or "").strip()
    if display:
        return _validate_workspace_id("ws_" + _safe_workspace_id(display))
    repo_url = (body.get("repo_url") or "").rstrip("/").removesuffix(".git")
    tail = repo_url.rsplit("/", 1)[-1].rsplit(":", 1)[-1] if repo_url else ""
    return _validate_workspace_id("ws_" + _safe_workspace_id(tail or "workspace"))


def _normalize_workspace_mode(mode):
    normalized = (mode or "").strip().replace("-", "_")
    if normalized not in WORKSPACE_MODES:
        raise ValueError(f"invalid metadata_mode: {mode!r}")
    return normalized


def _normalize_workspace_provider(provider):
    normalized = (provider or "").strip().lower()
    if normalized not in WORKSPACE_PROVIDERS:
        raise ValueError(f"invalid provider: {provider!r}")
    return normalized


def _normalize_repo_url_key(value):
    normalized = (value or "").strip()
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.lower()


def _load_enterprise_policy_with_error(root):
    result = load_enterprise_policy_file(relay_policy_path(root))
    if not result.ok:
        return {}, result.error or f"enterprise relay policy not found: {result.path}"
    policy = dict(result.data)
    policy["_source_path"] = result.path
    return policy, ""


def load_enterprise_policy(root):
    policy, _error = _load_enterprise_policy_with_error(root)
    return policy


def _reset_enterprise_policy_cache():
    with _enterprise_policy_cache_lock:
        _enterprise_policy_cache.update({
            "root": "",
            "loaded_at": 0.0,
            "policy": {},
            "error": "",
        })


def _current_enterprise_policy(root=None, *, force=False):
    root = os.fspath(root or _root_dir or "")
    if not root:
        return {}, "WORK_AGENTS_ROOT is not set"
    now = time.time()
    with _enterprise_policy_cache_lock:
        if (
                not force
                and _enterprise_policy_cache.get("root") == root
                and now - float(_enterprise_policy_cache.get("loaded_at") or 0.0) < ENTERPRISE_POLICY_REFRESH_SECONDS):
            return (
                dict(_enterprise_policy_cache.get("policy") or {}),
                str(_enterprise_policy_cache.get("error") or ""),
            )
    policy, error = _load_enterprise_policy_with_error(root)
    if error:
        log.warning(f"[ENTERPRISE_POLICY] reload failed root={root}: {error}")
    else:
        log.info(f"[ENTERPRISE_POLICY] reloaded {policy.get('_source_path') or relay_policy_path(root)}")
    with _enterprise_policy_cache_lock:
        _enterprise_policy_cache.update({
            "root": root,
            "loaded_at": now,
            "policy": dict(policy or {}),
            "error": error,
        })
    return dict(policy or {}), error


def daemon_policy_from_enterprise_policy(policy, machine_id="", *, feishu_credentials=None):
    """Return the daemon policy served to user machines.

    The endpoint is daemon-authenticated. General secret-shaped values remain
    redacted, but Feishu app credentials are intentionally included from the
    relay-owned secret bundle because daemon hooks must authenticate Feishu
    upstream without reading local key files.
    """
    if not isinstance(policy, dict):
        return {}
    public_policy = {k: v for k, v in policy.items() if not str(k).startswith("_")}
    public_policy["role"] = "daemon"
    public_policy["daemon_policy"] = True
    daemon_policy = redact_secrets(public_policy)
    if feishu_credentials:
        feishu = daemon_policy.get("feishu") if isinstance(daemon_policy.get("feishu"), dict) else {}
        feishu = dict(feishu)
        app_id = str(feishu_credentials.get("app_id") or feishu.get("app_id") or "").strip()
        app_secret = str(feishu_credentials.get("app_secret") or "").strip()
        if app_id:
            feishu["app_id"] = app_id
        if app_secret:
            feishu["app_secret"] = app_secret
        daemon_policy["feishu"] = feishu
    return daemon_policy


def daemon_credentials_from_root(root):
    policy_result = load_enterprise_policy_file(relay_policy_path(root))
    if not policy_result.ok:
        return None, policy_result.error or f"enterprise relay policy not found: {policy_result.path}"
    secrets_result = load_enterprise_secrets(relay_secrets_path(root), required=True)
    if not secrets_result.ok:
        return None, secrets_result.error or f"enterprise relay secrets not found: {secrets_result.path}"
    feishu = policy_result.data.get("feishu") if isinstance(policy_result.data.get("feishu"), dict) else {}
    app_id = str(feishu.get("app_id") or "").strip()
    app_secret = resolve_secret_value((secrets_result.data.get("secrets") or {}).get("feishu.app_secret") or {})
    if not app_id or not app_secret:
        return None, "app_id/app_secret missing"
    return {"app_id": app_id, "app_secret": app_secret}, ""


def _user_config_identity_key(owner_mobile="", owner_open_id=""):
    identity = str(owner_open_id or owner_mobile or "").strip().lower()
    if not identity:
        return "", ""
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return digest[:32], identity


def _redact_identity(identity):
    identity = str(identity or "")
    if len(identity) <= 4:
        return "***" if identity else ""
    return identity[:2] + "***" + identity[-2:]


def validate_enterprise_daemon_owner_identity(api, owner_mobile="", owner_open_id=""):
    owner_mobile = str(owner_mobile or "").strip()
    owner_open_id = str(owner_open_id or "").strip()
    if owner_mobile:
        if api is None:
            return "", "feishu api unavailable"
        resolved_open_id, err = api.mobile_to_open_id(owner_mobile)
        if not resolved_open_id:
            return "", err or f"mobile '{owner_mobile}' not found in this tenant"
        if owner_open_id and owner_open_id != resolved_open_id:
            return "", "owner_mobile does not match owner_open_id"
        return resolved_open_id, ""
    return owner_open_id, ""


def _user_config_backup_path(owner_key):
    return os.fspath(user_config_backups_dir(_root_dir) / f"{owner_key}.json")


def _write_json_file_atomic(path, data, mode=0o600):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.chmod(tmp, mode)
    os.replace(tmp, path)
    os.chmod(path, mode)


def helper_policy_from_enterprise_policy(policy):
    if not isinstance(policy, dict):
        return {}
    for key in ("helper", "machine_helper", "machine_helpers"):
        value = policy.get(key)
        if isinstance(value, dict):
            helper = dict(value)
            feishu = policy.get("feishu") if isinstance(policy.get("feishu"), dict) else {}
            if not helper.get("app_owner_open_id") and feishu.get("owner_open_id"):
                helper["app_owner_open_id"] = feishu.get("owner_open_id")
            if not helper.get("admins") and feishu.get("owner_open_id"):
                helper["admins"] = [feishu.get("owner_open_id")]
            return helper
    keys = {
        "app_owner_open_id",
        "admins",
        "default_visibility",
        "machine_grants",
        "workspace_grants",
        "workspace_id",
        "default_workspace_id",
    }
    helper = {key: policy[key] for key in keys if key in policy}
    feishu = policy.get("feishu") if isinstance(policy.get("feishu"), dict) else {}
    if not helper.get("app_owner_open_id") and feishu.get("owner_open_id"):
        helper["app_owner_open_id"] = feishu.get("owner_open_id")
    if not helper.get("admins") and feishu.get("owner_open_id"):
        helper["admins"] = [feishu.get("owner_open_id")]
    return helper


def resolve_enterprise_owner_from_mobile(api, policy):
    """Fill policy owner open_id from owner_mobile when the admin supplied a phone.

    This is a relay-startup convenience for test and bootstrap environments. It
    does not write the policy file; admins can still export/freeze the resolved
    open_id into the policy bundle afterwards.
    """
    if not isinstance(policy, dict) or api is None:
        return policy
    feishu = policy.get("feishu")
    if not isinstance(feishu, dict):
        return policy
    mobile = str(feishu.get("owner_mobile") or feishu.get("mobile") or "").strip()
    if mobile and not feishu.get("owner_open_id"):
        owner_open_id, err = api.mobile_to_open_id(mobile)
        if owner_open_id:
            feishu["owner_open_id"] = owner_open_id
            log.info("[POLICY] resolved feishu.owner_open_id from configured owner_mobile")
        else:
            log.warning(f"[POLICY] failed to resolve feishu.owner_mobile: {err}")
    owner_open_id = feishu.get("owner_open_id") or ""
    helper = policy.get("helper")
    if isinstance(helper, dict) and owner_open_id:
        if not helper.get("app_owner_open_id"):
            helper["app_owner_open_id"] = owner_open_id
        admins = list(helper.get("admins") or [])
        if owner_open_id not in admins:
            admins.append(owner_open_id)
            helper["admins"] = admins
    return policy


def _ci_http_enabled():
    """Allow synthetic Feishu events only inside CI relay deployments."""
    if os.environ.get("INTERN_AGENT_CI_HTTP") == "1":
        return True
    policy_paths = []
    if _root_dir:
        policy_paths.append(relay_policy_path(_root_dir))
        policy_paths.append(Path(_root_dir) / "enterprise" / "policy.json")
    for policy_path in policy_paths:
        try:
            with open(policy_path, encoding="utf-8") as fp:
                policy = json.load(fp)
            deployment_id = str(policy.get("deployment_id") or "")
            return deployment_id.startswith(("ci_", "bug"))
        except FileNotFoundError:
            continue
        except Exception:
            return False
    return False


class WorkspaceConflict(ValueError):
    def __init__(self, workspace_id, existing):
        super().__init__(f"workspace already exists: {workspace_id}")
        self.workspace_id = workspace_id
        self.existing = dict(existing)


class WorkspaceRegistry:
    """Relay-authoritative workspace/project registry.

    This registry is independent of the Feishu chat registry. It stores which
    enterprise workspaces exist; daemon-local enable/cache state lives on each
    client machine.
    """

    def __init__(self, persist_path=None, policy=None):
        self._lock = threading.Lock()
        self._persist_path = persist_path
        self._policy = policy or {}
        self._data = {
            "schema": WORKSPACE_REGISTRY_SCHEMA,
            "schema_version": WORKSPACE_REGISTRY_VERSION,
            "policy_version": self._policy.get("version", ""),
            "workspaces": {},
        }
        if persist_path and os.path.exists(persist_path):
            try:
                with open(persist_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if loaded.get("schema") != WORKSPACE_REGISTRY_SCHEMA:
                    raise ValueError(f"unexpected workspace registry schema: {loaded.get('schema')!r}")
                loaded.setdefault("schema_version", WORKSPACE_REGISTRY_VERSION)
                loaded.setdefault("workspaces", {})
                self._data = loaded
                self._data["policy_version"] = self._policy.get("version", "")
            except Exception as e:
                log.error(f"[WORKSPACE] failed to load registry {persist_path}: {e}")

    def _save(self):
        if not self._persist_path:
            return
        os.makedirs(os.path.dirname(self._persist_path), exist_ok=True)
        tmp = self._persist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, self._persist_path)

    def _workspace_policy(self):
        workspace = self._policy.get("workspace") if isinstance(self._policy, dict) else None
        return workspace if isinstance(workspace, dict) else {}

    def _allowed_modes(self):
        modes = self._workspace_policy().get("allowed_modes")
        if isinstance(modes, list) and modes:
            return [m for m in modes if m in WORKSPACE_MODES]
        return ["repo_dotdir", "metadata_branch"]

    def _policy_metadata_branch(self):
        return (self._workspace_policy().get("metadata_branch") or "").strip()

    def _repo_dotdir_reasons(self, item):
        return []

    def list(self, include_deleted=False):
        with self._lock:
            items = []
            for item in self._data.get("workspaces", {}).values():
                if item.get("deleted") and not include_deleted:
                    continue
                items.append(dict(item))
            items.sort(key=lambda x: (x.get("display_name") or "", x.get("workspace_id") or ""))
            return {
                "schema": WORKSPACE_REGISTRY_SCHEMA,
                "schema_version": self._data.get("schema_version", WORKSPACE_REGISTRY_VERSION),
                "policy_version": self._data.get("policy_version", ""),
                "workspaces": items,
            }

    def get(self, workspace_id):
        workspace_id = _validate_workspace_id(workspace_id)
        with self._lock:
            item = self._data.get("workspaces", {}).get(workspace_id)
            return dict(item) if item else None

    def create(self, body, created_by=""):
        display_name = (body.get("display_name") or body.get("name") or "").strip()
        repo_url = (body.get("repo_url") or "").strip()
        provider = _normalize_workspace_provider(body.get("provider") or "")
        raw_mode = (body.get("metadata_mode") or body.get("mode") or "").strip().replace("-", "_")
        if not display_name:
            raise ValueError("display_name required")
        if provider == "local":
            raise ValueError("local workspaces are daemon-local and must not be created in relay")
        if provider != "local" and not repo_url:
            raise ValueError("repo_url required")
        if raw_mode == "local_only":
            raise ValueError("remote workspaces cannot use local_only metadata mode")
        mode = _normalize_workspace_mode(raw_mode)
        if mode not in self._allowed_modes():
            raise ValueError(f"metadata_mode {mode!r} is disabled by enterprise policy")
        workspace_id = _workspace_id_from_body(body)
        now = _now_iso()
        metadata_branch = (
            body.get("metadata_branch")
            or self._policy_metadata_branch()
        )
        if mode == "metadata_branch" and not metadata_branch:
            raise ValueError("metadata_branch is required for metadata_branch mode")
        item = {
            "workspace_id": workspace_id,
            "display_name": display_name,
            "provider": provider,
            "repo_url": repo_url,
            "provider_config": body.get("provider_config") or body.get("codeup_config") or {},
            "metadata_mode": mode,
            "metadata_branch": metadata_branch,
            "enabled_by_default": bool(body.get("enabled_by_default", True)),
            "created_by": created_by or body.get("created_by") or "",
            "created_at": now,
            "updated_at": now,
            "policy_version": self._data.get("policy_version", ""),
            "deleted": False,
        }
        if mode == "repo_dotdir":
            reasons = self._repo_dotdir_reasons(item)
            if reasons:
                raise ValueError("; ".join(reasons))
        with self._lock:
            workspaces = self._data.setdefault("workspaces", {})
            repo_key = _normalize_repo_url_key(repo_url)
            for existing in workspaces.values():
                if existing.get("deleted"):
                    continue
                if repo_key and _normalize_repo_url_key(existing.get("repo_url") or "") == repo_key:
                    if existing.get("metadata_mode") == mode:
                        reused = dict(existing)
                        reused["reused"] = True
                        return reused
                    raise ValueError(
                        "workspace for this repo already exists with metadata_mode "
                        f"{existing.get('metadata_mode')!r}; requested {mode!r}. "
                        "Workspace mode is fixed at add time. Delete Globally the existing workspace "
                        "or run workspace migrate-mode before re-adding it."
                    )
            existing = workspaces.get(workspace_id)
            if existing and not existing.get("deleted"):
                raise WorkspaceConflict(workspace_id, existing)
            self._data["workspaces"][workspace_id] = item
            self._save()
        return dict(item)

    def patch(self, workspace_id, body):
        workspace_id = _validate_workspace_id(workspace_id)
        allowed = {
            "display_name", "provider", "repo_url", "provider_config",
            "metadata_branch", "enabled_by_default", "policy_version",
        }
        with self._lock:
            item = self._data.get("workspaces", {}).get(workspace_id)
            if not item or item.get("deleted"):
                return None
            for key in allowed:
                if key in body:
                    if key == "provider":
                        item[key] = _normalize_workspace_provider(body[key])
                        continue
                    item[key] = body[key]
            item["updated_at"] = _now_iso()
            self._save()
            return dict(item)

    def delete(self, workspace_id):
        workspace_id = _validate_workspace_id(workspace_id)
        with self._lock:
            item = self._data.get("workspaces", {}).get(workspace_id)
            if not item or item.get("deleted"):
                return None
            item["deleted"] = True
            item["updated_at"] = _now_iso()
            self._save()
            return dict(item)

# ══════════════════════════════════════════
# Composite key helpers (Phase 3 — repo:intern uniqueness)
# ══════════════════════════════════════════
#
# 所有 RelayRegistry 内部 dict 都以 "<project>:<intern_name>" 为 key，
# 实现 (repo, intern_name) 维度的唯一性。
#
# Enterprise registry keys require explicit project scope:
#   - 任何缺 project 的 composite key 构造 → raise，并打印完整 stack 定位 caller
#   - registry load / sync_online / intern_offline 等路径遇到缺 project 的条目：
#     log.error(含 caller 上下文) 后 skip 该条目。


def _make_composite_key(intern_name, project):
    """Build composite registry key '<project>:<intern_name>'.

    project 必须非空。空 project 说明上游 caller 没传，是 bug 必须定位：
    抛 ValueError 并把 caller 栈帧带进 error msg。
    """
    if not project:
        import traceback
        stack = "".join(traceback.format_stack(limit=6)[:-1])
        raise ValueError(
            f"_make_composite_key: project is required for intern_name={intern_name!r}; "
            f"upstream caller did not pass project. Stack:\n{stack}"
        )
    return f"{project}:{intern_name}"


def _split_composite_key(key):
    """Split composite registry key. Returns (project, intern_name).

    严格模式：key 必须带 ':' 分隔符。没有分隔符说明 key 格式损坏，raise。
    """
    if ":" not in key:
        raise ValueError(
            f"_split_composite_key: key {key!r} missing ':' separator; "
            f"corrupt composite key indicates registry data or caller bug"
        )
    project, name = key.split(":", 1)
    return project, name


def _is_auxiliary_scene_intern(name):
    return any(marker in (name or "") for marker in ("goal_sender", "nonprotect_guard", "_guard"))


def _group_light_from_name(group_name, online):
    if group_name.startswith("🟢"):
        return "green"
    if group_name.startswith("🔴"):
        return "red"
    if group_name:
        return "unknown"
    return "green" if online else "unknown"


def _scene_warnings(active, stale):
    warnings = []
    red_active = [item for item in active if item.get("group_light") == "red"]
    auxiliary_active = [item for item in active if item.get("is_auxiliary")]
    if stale:
        warnings.append({
            "code": "stale_persisted_groups",
            "message": "Persisted Feishu chat mappings exist without active relay runtime entries.",
            "count": len(stale),
            "names": [item.get("name") for item in stale],
        })
    if red_active:
        warnings.append({
            "code": "active_red_groups",
            "message": "Active relay groups still have a red last known Feishu group name.",
            "count": len(red_active),
            "names": [item.get("name") for item in red_active],
        })
    if auxiliary_active:
        warnings.append({
            "code": "active_auxiliary_groups",
            "message": "Auxiliary smoke interns are still in the active scene.",
            "count": len(auxiliary_active),
            "names": [item.get("name") for item in auxiliary_active],
        })
    return warnings


_HELPER_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _safe_machine_helper_slug(machine_id):
    slug = _HELPER_SLUG_RE.sub("_", (machine_id or "").strip().lower()).strip("_")
    if not slug:
        raise ValueError("machine_id required")
    return slug


def _machine_helper_id_for_machine(machine_id):
    return f"machine_helper_{_safe_machine_helper_slug(machine_id)}"


def _machine_helper_project_for_machine(machine_id):
    return f"machine-helper-{_safe_machine_helper_slug(machine_id)}"


def _remove_local_feishu_registry_entry(root, intern_name):
    if not root or not intern_name:
        return False
    path = os.path.join(root, ".feishu_registry", f"{intern_name}.json")
    try:
        os.remove(path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.warning(f"[CHAT] failed to remove local Feishu registry entry {path}: {exc}")
        return False


def helper_policy_allows(policy, user_open_id, machine_id, action, machine_info=None):
    """Return whether a Feishu user may perform a helper action on a machine."""
    if action not in {
        "view",
        "helper_start",
        "helper_stop",
        "helper_delete_group",
        "helper_invite_owner",
        "helper_config",
        "helper_upgrade",
        "workspace_ops",
    }:
        raise ValueError(f"unknown helper policy action: {action!r}")
    if not user_open_id:
        return False

    policy = policy or {}
    admins = set(policy.get("admins") or [])
    app_owner = policy.get("app_owner_open_id") or ""
    if user_open_id in admins or (app_owner and user_open_id == app_owner):
        return True

    machine_info = machine_info or {}
    machine_owner = machine_info.get("owner_open_id") or ""
    if machine_owner and user_open_id == machine_owner:
        return action in {"view", "helper_start", "helper_stop", "helper_invite_owner", "helper_config", "helper_upgrade"}

    grants = ((policy.get("machine_grants") or {}).get(machine_id) or {})
    if user_open_id in set(grants.get(action) or []):
        return True
    if action != "view" and user_open_id in set(grants.get("helper_ops") or []):
        return True
    if action == "view" and policy.get("default_visibility") == "all":
        return True
    return False


def filter_visible_machines_for_helper(machines_summary, user_open_id, policy):
    result = {}
    for machine_id, info in (machines_summary or {}).items():
        if helper_policy_allows(policy, user_open_id, machine_id, "view", info):
            result[machine_id] = dict(info)
    return result


def filter_visible_helpers_for_helper(helpers_summary, machines_summary, user_open_id, policy):
    visible = {}
    for machine_id, helper in (helpers_summary or {}).items():
        machine_info = (machines_summary or {}).get(machine_id, {})
        if helper_policy_allows(policy, user_open_id, machine_id, "view", machine_info):
            visible[machine_id] = dict(helper)
    return visible


# ══════════════════════════════════════════
# 配置
# ══════════════════════════════════════════

def load_config(root):
    """Load relay server config from enterprise_policy/relay under the given root."""
    credentials, err = daemon_credentials_from_root(root)
    if not credentials:
        log.error(err or "enterprise relay credentials missing")
        sys.exit(1)
    app_id = credentials["app_id"]
    app_secret = credentials["app_secret"]

    # Read relay settings from _owner.json
    owner_path = os.fspath(relay_owner_path(root))
    if not os.path.exists(owner_path):
        log.error(f"_owner.json not found: {owner_path}")
        sys.exit(1)
    with open(owner_path, "r") as f:
        owner = json.load(f)
    relay_token = owner.get("relay_token", "")
    if not relay_token:
        log.error("_owner.json missing relay_token")
        sys.exit(1)

    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "relay_token": relay_token,
        "listen_host": "0.0.0.0",
        "relay_ws_port": owner.get("relay_ws_port", 28081),
        "relay_http_port": owner.get("relay_http_port", 28080),
        "ssh_port": owner.get("ssh_port") or owner.get("relay_ssh_port") or owner.get("server_ssh_port") or "",
    }


# ══════════════════════════════════════════
# 飞书 API（精简版，仅用于离线回复）
# ══════════════════════════════════════════

class FeishuAPI:
    # get_user_info cache TTLs
    _USER_INFO_TTL_OK = 24 * 3600     # 成功 24h
    _USER_INFO_TTL_FAIL = 5 * 60      # 失败 5min（避免无权限/不在通讯录时的 retry 风暴）

    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expires = 0
        # open_id → (expire_ts, {name, mobile, avatar_url} or None)
        self._user_info_cache = {}
        self._user_info_lock = threading.Lock()
        # task252: our bot's open_id, lazy-resolved via chat-members API on first need
        self._bot_open_id = None
        self._bot_open_id_lock = threading.Lock()

    def _get_token(self):
        now = time.time()
        if self._token and now < self._token_expires - 300:
            return self._token
        payload = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode()
        req = urllib.request.Request(
            f"{BASE_URL}/auth/v3/tenant_access_token/internal",
            data=payload, headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=10)
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

    def reply_message(self, message_id, text):
        content = json.dumps({"text": text})
        _, err = self._request("POST", f"/im/v1/messages/{message_id}/reply", {
            "msg_type": "text", "content": content})
        return err

    def update_chat(self, chat_id, name=None, avatar=None, description=None):
        # avatar 字段保留：task204 回滚后当前不主动设置头像（改由群名 emoji 区分类型），
        # 但 avatar="" 仍可用于把群头像重置为飞书默认（见 migrate_group_metadata.py --reset-avatar）。
        # task259: description="" 表示清空群描述；None 表示不动 description。
        body = {}
        if name is not None:
            body["name"] = name
        if avatar is not None:
            body["avatar"] = avatar
        if description is not None:
            body["description"] = description
        if not body:
            return None
        _, err = self._request("PUT", f"/im/v1/chats/{chat_id}", body)
        return err

    def get_chat_info(self, chat_id):
        """task259: GET /im/v1/chats/{chat_id} → (dict, err)。

        Used to read the current group `description` before patching the
        trigger_mode header. Lark wraps the actual chat fields under `data` —
        we unwrap and return the inner dict so callers can `.get("description")`.
        """
        data, err = self._request("GET", f"/im/v1/chats/{chat_id}")
        if err:
            return None, err
        return (data or {}), None

    def download_message_resource(self, message_id, file_key, rtype):
        """GET /im/v1/messages/{message_id}/resources/{file_key}?type=image|file

        飞书该 endpoint 响应体是 binary（不是 JSON），所以不能复用 `_request`。
        Content-Disposition 会带 filename*=UTF-8''... 或 filename="..."；解析出来作为
        落盘文件名。解析失败 → 用 file_key 兜底（仅当飞书没返回 Content-Disposition
        时发生，属于飞书侧异常，此时 caller 可自行决定文件名）。

        返回 (bytes, filename, err)；任一环节失败 → bytes/filename=None，err 非空
        （caller 必须把 err 传递到 reply_message，不得吞错 —— 项目规则 6）。
        """
        if rtype not in ("image", "file"):
            return None, None, f"invalid rtype={rtype!r}, must be 'image' or 'file'"
        token = self._get_token()
        if not token:
            return None, None, "no token"
        url = f"{BASE_URL}/im/v1/messages/{message_id}/resources/{file_key}?type={rtype}"
        req = urllib.request.Request(url, method="GET", headers={
            "Authorization": f"Bearer {token}"})
        try:
            resp = urllib.request.urlopen(req, timeout=30)
            data = resp.read()
            filename = _parse_content_disposition_filename(
                resp.headers.get("Content-Disposition", "")) or ""
            return data, filename, None
        except urllib.error.HTTPError as e:
            return None, None, f"HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as e:
            return None, None, str(e)

    def create_chat(self, name, description="", owner_open_id=""):
        body = {"name": name, "description": description or f"Intern agent: {name}",
                "chat_type": "private"}
        if owner_open_id:
            body["user_id_list"] = [owner_open_id]
        data, err = self._request("POST", "/im/v1/chats?user_id_type=open_id", body)
        if err:
            return None, err
        return data.get("chat_id") if data else None, None

    def delete_chat(self, chat_id):
        _, err = self._request("DELETE", f"/im/v1/chats/{chat_id}")
        return err

    def get_chat_members(self, chat_id):
        """获取群成员 open_id 列表"""
        members = []
        page_token = ""
        while True:
            path = f"/im/v1/chats/{chat_id}/members?member_id_type=open_id&page_size=100"
            if page_token:
                path += f"&page_token={page_token}"
            data, err = self._request("GET", path)
            if err:
                return None, err
            items = data.get("items", []) if data else []
            for item in items:
                mid = item.get("member_id", "")
                if mid:
                    members.append(mid)
            if not data or not data.get("has_more"):
                break
            page_token = data.get("page_token", "")
        return members, None

    def add_chat_members(self, chat_id, open_ids):
        """添加成员到群"""
        _, err = self._request(
            "POST", f"/im/v1/chats/{chat_id}/members?member_id_type=open_id",
            {"id_list": open_ids})
        return err

    def list_chats(self):
        chats = []
        page_token = ""
        while True:
            path = "/im/v1/chats?page_size=100"
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

    def send_message(self, chat_id, text):
        lines = text.split("\n")
        content_lines = [[{"tag": "text", "text": line}] for line in lines]
        content = json.dumps({"zh_cn": {"content": content_lines}})
        data, err = self._request("POST", "/im/v1/messages?receive_id_type=chat_id", {
            "receive_id": chat_id, "msg_type": "post", "content": content})
        if err:
            return None, err
        return data.get("message_id") if data else None, None

    def send_to_user(self, open_id, text):
        """通过 open_id 直接给用户发消息"""
        lines = text.split("\n")
        content_lines = [[{"tag": "text", "text": line}] for line in lines]
        content = json.dumps({"zh_cn": {"content": content_lines}})
        data, err = self._request("POST", "/im/v1/messages?receive_id_type=open_id", {
            "receive_id": open_id, "msg_type": "post", "content": content})
        if err:
            return None, err
        return data.get("message_id") if data else None, None

    def send_interactive_card(self, chat_id, card_json):
        """task258: POST /im/v1/messages with msg_type=interactive. Returns
        (message_id, err). Used for the /config card and other supervisor-facing
        forms originated by the relay."""
        content = json.dumps(card_json)
        data, err = self._request("POST", "/im/v1/messages?receive_id_type=chat_id", {
            "receive_id": chat_id, "msg_type": "interactive", "content": content})
        if err:
            return None, err
        return data.get("message_id") if data else None, None

    def update_interactive_card(self, message_id, card_json):
        content = json.dumps(card_json)
        _, err = self._request("PATCH", f"/im/v1/messages/{message_id}", {
            "msg_type": "interactive", "content": content})
        return err

    def mobile_to_open_id(self, mobile):
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
        """Resolve open_id → {name, mobile, avatar_url}. Returns (info_dict_or_None, error).
        Cached in-process: 24h on success, 5min on failure (avoid retry storms when
        the app lacks contact permission or the user isn't in the tenant)."""
        if not open_id:
            return None, "empty open_id"
        now = time.time()
        with self._user_info_lock:
            cached = self._user_info_cache.get(open_id)
            if cached and cached[0] > now:
                info = cached[1]
                return (dict(info) if info else None), (None if info else "cached negative")
        data, err = self._request(
            "GET", f"/contact/v3/users/{open_id}?user_id_type=open_id")
        if err or not data:
            with self._user_info_lock:
                self._user_info_cache[open_id] = (now + self._USER_INFO_TTL_FAIL, None)
            return None, err or "empty response"
        user = data.get("user") or {}
        info = {
            "name": user.get("name", ""),
            "mobile": user.get("mobile", ""),
            "avatar_url": (user.get("avatar") or {}).get("avatar_72", ""),
        }
        with self._user_info_lock:
            self._user_info_cache[open_id] = (now + self._USER_INFO_TTL_OK, info)
        return dict(info), None

    def get_bot_open_id(self, hint_chat_id=None):
        """task260: resolve this app's bot open_id via /bot/v3/info.

        task252 originally used `/im/v1/chats/<chat>/members?member_id_type=open_id`
        + `member_type=="bot"` filter, but that members endpoint **does not return
        bot members** (it only returns user members; their `member_type` is None).
        So `at_only` mode silent-dropped 100% of messages including legitimate
        @bot ones. Fixed by switching to `/bot/v3/info` which returns the app's
        own bot info — single source of truth, no chat dependency.

        Caveat: `/bot/v3/info` puts the payload under top-level `bot`, not under
        `data` like other endpoints, so `_request` (which unwraps `data`) cannot
        be used as-is. We do a raw urllib call inline and parse `result["bot"]`.

        Cached globally after first success. `hint_chat_id` kept for callsite
        backwards-compat but is no longer used. Returns (open_id_or_None,
        err_or_None); None forces at_only callers to safer-side silent-drop.
        """
        with self._bot_open_id_lock:
            if self._bot_open_id:
                return self._bot_open_id, None
        token = self._get_token()
        if not token:
            return None, "no token"
        req = urllib.request.Request(
            f"{BASE_URL}/bot/v3/info",
            headers={"Authorization": f"Bearer {token}"})
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as e:
            return None, str(e)
        if result.get("code") != 0:
            return None, f"code={result.get('code')}, msg={result.get('msg')}"
        bot = result.get("bot") or {}
        open_id = bot.get("open_id")
        if not open_id:
            return None, f"no open_id in /bot/v3/info response: {result}"
        with self._bot_open_id_lock:
            self._bot_open_id = open_id
            return open_id, None




# ══════════════════════════════════════════
# 全局注册表
# ══════════════════════════════════════════

class RelayRegistry:
    """Thread-safe global intern registry.

    所有内部 dict 用 composite key '<project>:<intern_name>'，实现 (repo, intern) 唯一性。
    Public methods 接受 (intern_name, ..., project=None)；project 缺失时通过
    `_resolve_key` 要求 project 显式传入，缺失 project 会抛错。

    Data shapes:
      _interns:       composite_key → {machine_id, chat_id, type, project, name}
      _chat_index:    chat_id       → composite_key
      _online:        composite_key → machine_id
      _chat_persist:  composite_key → {chat_id, type, project, name}
      _connections:   machine_id    → websocket
      _machine_info:  machine_id    → {connected_at, owner_*, ip, ssh_port, daemon_hash,
                                       extension_version, hooks_version, cli_versions,
                                       capabilities,
                                       resources, resources_updated_at,
                                       warnings, warnings_updated_at}
      _intern_dynamic: composite_key → {status, current_task, last_active, turn_count_today}
    """

    _HELPER_PERSIST_KEY = "__machine_helpers__"
    _HELPER_PERSIST_SCHEMA = "intern-agents.relay-machine-helpers.v1"

    def __init__(self, persist_path=None):
        self._lock = threading.Lock()
        self._loop = None
        self._interns = {}
        self._chat_index = {}
        self._connections = {}
        self._machine_info = {}
        self._online = {}
        self._intern_dynamic = {}
        self._machine_helpers = {}
        self._helper_chat_index = {}
        self._persist_path = persist_path
        self._chat_persist = {}
        if persist_path and os.path.exists(persist_path):
            try:
                with open(persist_path) as f:
                    raw = json.load(f)
                helper_raw = raw.get(self._HELPER_PERSIST_KEY) if isinstance(raw, dict) else None
                for k, v in raw.items():
                    if k == self._HELPER_PERSIST_KEY:
                        continue
                    if not isinstance(v, dict):
                        continue
                    if ":" in k and v.get("project"):
                        # New composite-key format
                        self._chat_persist[k] = v
                    else:
                        # Reject entries without explicit project scope so they
                        # cannot pollute the in-memory registry.
                        log.error(f"[REGISTRY] load skip: key={k!r} missing project field; "
                                  f"entry={v!r}. 运行 scripts/cleanup_stale_registry_entries.py 清理 JSON。")
                self._load_machine_helpers_from_persist(helper_raw)
                log.info(
                    f"[REGISTRY] Loaded {len(self._chat_persist)} persistent chat mappings "
                    f"and {len(self._machine_helpers)} machine helpers from {persist_path}"
                )
            except Exception as e:
                log.error(f"[REGISTRY] Failed to load persistent chats: {e}")

    def set_loop(self, loop):
        self._loop = loop

    # ── Composite key helpers (must hold lock when calling _resolve_key) ──

    def _resolve_key(self, intern_name, project):
        """Return composite key. project MUST be provided; anyone calling with
        project=None is an upstream bug — _make_composite_key will raise with stack."""
        return _make_composite_key(intern_name, project)

    def _save_persist_to_disk(self, data):
        if not self._persist_path:
            return
        try:
            tmp = self._persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self._persist_path)
        except Exception as e:
            log.error(f"[REGISTRY] Failed to save persistent chats: {e}")

    def _machine_helper_entry_for_persist_locked(self, entry):
        audit = []
        for item in list(entry.get("audit_log") or [])[-200:]:
            if not isinstance(item, dict):
                continue
            audit.append({
                "action": str(item.get("action") or ""),
                "operator_open_id": str(item.get("operator_open_id") or ""),
                "detail": dict(item.get("detail") or {}) if isinstance(item.get("detail"), dict) else {},
                "created_at": str(item.get("created_at") or ""),
            })
        machine_id = str(entry.get("machine_id") or "")
        return {
            "machine_id": machine_id,
            "helper_id": str(entry.get("helper_id") or _machine_helper_id_for_machine(machine_id)),
            "project": str(entry.get("project") or _machine_helper_project_for_machine(machine_id)),
            "runtime": str(entry.get("runtime") or "codex"),
            "status": str(entry.get("status") or "stopped"),
            "chat_id": str(entry.get("chat_id") or ""),
            "created_by_open_id": str(entry.get("created_by_open_id") or ""),
            "last_operator_open_id": str(entry.get("last_operator_open_id") or ""),
            "created_at": str(entry.get("created_at") or ""),
            "updated_at": str(entry.get("updated_at") or ""),
            "last_error": str(entry.get("last_error") or ""),
            "selected_machine_id": str(entry.get("selected_machine_id") or ""),
            "detail_mode": (
                entry.get("detail_mode")
                if entry.get("detail_mode") in {"full", "summary"}
                else "full"
            ),
            "audit_log": audit,
        }

    def _build_persist_snapshot_locked(self):
        data = dict(self._chat_persist)
        if self._machine_helpers:
            data[self._HELPER_PERSIST_KEY] = {
                "schema": self._HELPER_PERSIST_SCHEMA,
                "entries": {
                    machine_id: self._machine_helper_entry_for_persist_locked(entry)
                    for machine_id, entry in sorted(self._machine_helpers.items())
                    if machine_id
                },
            }
        return data

    def _normalize_persisted_machine_helper(self, machine_id, raw):
        if not machine_id or not isinstance(raw, dict):
            return None
        created_at = str(raw.get("created_at") or datetime.now().isoformat())
        updated_at = str(raw.get("updated_at") or created_at)
        audit = []
        for item in list(raw.get("audit_log") or [])[-200:]:
            if not isinstance(item, dict):
                continue
            action = str(item.get("action") or "")
            if not action:
                continue
            audit.append({
                "action": action,
                "operator_open_id": str(item.get("operator_open_id") or ""),
                "detail": dict(item.get("detail") or {}) if isinstance(item.get("detail"), dict) else {},
                "created_at": str(item.get("created_at") or ""),
            })
        detail_mode = raw.get("detail_mode")
        if detail_mode not in {"full", "summary"}:
            detail_mode = "full"
        return {
            "machine_id": machine_id,
            "helper_id": str(raw.get("helper_id") or _machine_helper_id_for_machine(machine_id)),
            "project": str(raw.get("project") or _machine_helper_project_for_machine(machine_id)),
            "runtime": str(raw.get("runtime") or "codex"),
            "status": str(raw.get("status") or "stopped"),
            "chat_id": str(raw.get("chat_id") or ""),
            "created_by_open_id": str(raw.get("created_by_open_id") or ""),
            "last_operator_open_id": str(raw.get("last_operator_open_id") or ""),
            "created_at": created_at,
            "updated_at": updated_at,
            "last_error": str(raw.get("last_error") or ""),
            "selected_machine_id": str(raw.get("selected_machine_id") or ""),
            "detail_mode": detail_mode,
            "audit_log": audit,
        }

    def _load_machine_helpers_from_persist(self, raw):
        if not isinstance(raw, dict):
            return
        entries = raw.get("entries") if isinstance(raw.get("entries"), dict) else raw
        if not isinstance(entries, dict):
            return
        for machine_id, entry_raw in entries.items():
            helper = self._normalize_persisted_machine_helper(str(machine_id or ""), entry_raw)
            if not helper:
                continue
            self._machine_helpers[helper["machine_id"]] = helper
        self._rebuild_helper_chat_index_locked()

    def _rebuild_helper_chat_index_locked(self):
        self._helper_chat_index = {}
        for machine_id, entry in self._machine_helpers.items():
            chat_id = entry.get("chat_id") or ""
            if chat_id:
                self._helper_chat_index[chat_id] = machine_id

    def _save_persist(self):
        """Save chat_id mappings to disk for restart recovery."""
        if not self._persist_path:
            return
        with self._lock:
            data = self._build_persist_snapshot_locked()
        self._save_persist_to_disk(data)

    def _save_machine_helpers(self):
        if not self._persist_path:
            return
        with self._lock:
            data = self._build_persist_snapshot_locked()
        self._save_persist_to_disk(data)

    def _build_chat_persist_entry(self, ckey, chat_id, intern_type, project, intern_name):
        """Construct a _chat_persist dict, preserving last_group_name when chat_id is
        unchanged (task242: 重启/auto-register/type-only 刷新场景下 dedupe 状态必须穿越
        重写)。chat_id 变化时 last_group_name 必丢弃，因为旧名指向旧 chat 的飞书侧。

        Must be called with self._lock held.
        """
        new_entry = {
            "chat_id": chat_id,
            "type": intern_type,
            "project": project,
            "name": intern_name,
        }
        old = self._chat_persist.get(ckey)
        if old and old.get("chat_id") == chat_id and old.get("last_group_name"):
            new_entry["last_group_name"] = old["last_group_name"]
        return new_entry

    def _drop_helper_entries_except_locked(self, helper_name, keep_ckey):
        removed = False
        stale_helper_keys = [
            key for key in set(self._interns.keys()) | set(self._chat_persist.keys())
            if key != keep_ckey and _split_composite_key(key)[1] == helper_name
        ]
        for stale_key in stale_helper_keys:
            stale_entry = self._interns.pop(stale_key, None) or {}
            stale_persist = self._chat_persist.pop(stale_key, None) or {}
            stale_chat_id = stale_entry.get("chat_id") or stale_persist.get("chat_id")
            if stale_chat_id:
                self._chat_index.pop(stale_chat_id, None)
            self._online.pop(stale_key, None)
            self._intern_dynamic.pop(stale_key, None)
            removed = True
        return removed

    # ── Connection management (machine-keyed, no intern key changes) ──

    def add_connection(self, machine_id, ws, owner_mobile="", owner_open_id="", ip="", ssh_port=22, daemon_hash="",
                       extension_version="", hooks_version="", cli_versions=None, capabilities=None, workspaces=None):
        with self._lock:
            old_ws = self._connections.get(machine_id)
            if old_ws is not None and old_ws is not ws:
                if self._loop is None:
                    raise RuntimeError("RelayRegistry loop is not set before duplicate connection close")
                old_addr = getattr(old_ws, "remote_address", "?")
                new_addr = getattr(ws, "remote_address", "?")
                log.warning(f"[REGISTRY] Machine '{machine_id}' reconnected; closing old ws {old_addr}, new ws {new_addr}")
                asyncio.run_coroutine_threadsafe(old_ws.close(), self._loop)
            self._connections[machine_id] = ws
            self._machine_info[machine_id] = {
                "connected_at": datetime.now().isoformat(),
                "owner_mobile": owner_mobile,
                "owner_open_id": owner_open_id,
                "ip": ip,
                "ssh_port": ssh_port,
                "daemon_hash": daemon_hash,
                "extension_version": extension_version or "",
                "hooks_version": hooks_version or "",
                "cli_versions": dict(cli_versions) if cli_versions else {},
                "capabilities": list(capabilities) if capabilities else [],
                "workspaces": list(workspaces) if isinstance(workspaces, list) else [],
                "resources": {},
                "resources_updated_at": None,
                "warnings": [],
                "warnings_updated_at": None,
                "metrics": {},
                "metrics_updated_at": None,
            }
        log.info(f"[REGISTRY] Machine '{machine_id}' connected ({len(self._connections)} total)")

    def is_current_connection(self, machine_id, ws):
        with self._lock:
            return self._connections.get(machine_id) is ws

    def has_capability(self, machine_id, cap):
        """Return True if the machine declared `cap` in its auth capabilities list.
        Missing capabilities evaluate to False for feature gating.
        """
        with self._lock:
            info = self._machine_info.get(machine_id)
            if not info:
                return False
            return cap in (info.get("capabilities") or [])

    def update_machine_meta(self, machine_id, resources=None, interns_dynamic=None, warnings=None, metrics=None):
        """Update dynamic meta reported by daemon heartbeat.
          - resources: dict (loadavg, disk_free_gb) → stored on machine_info
          - interns_dynamic: list of {name, project, status, current_task,
                                      last_active, turn_count_today}
          - warnings: list of {code, detail, since} → machine-level warnings
          - metrics: dict (runtime, peer_delivery) → daemon-local load snapshot
        旧 daemon 不发 → 不调用此方法 → _intern_dynamic/resources 保持空，admin 显示 '-'。
        """
        now_iso = datetime.now().isoformat()
        with self._lock:
            info = self._machine_info.get(machine_id)
            if not info:
                return  # unknown machine (shouldn't happen after auth)
            if resources is not None:
                info["resources"] = dict(resources) if isinstance(resources, dict) else {}
                info["resources_updated_at"] = now_iso
            if warnings is not None:
                clean_warnings = []
                for item in warnings if isinstance(warnings, list) else []:
                    if not isinstance(item, dict):
                        continue
                    code = (item.get("code") or "").strip()
                    if not code:
                        continue
                    clean_warnings.append({
                        "code": code,
                        "detail": (item.get("detail") or "").strip(),
                        "since": (item.get("since") or "").strip(),
                    })
                info["warnings"] = clean_warnings
                info["warnings_updated_at"] = now_iso
            if metrics is not None:
                info["metrics"] = dict(metrics) if isinstance(metrics, dict) else {}
                info["metrics_updated_at"] = now_iso
            if interns_dynamic:
                for item in interns_dynamic:
                    name = item.get("name")
                    if not name:
                        continue
                    project = item.get("project")
                    if not project:
                        log.error(f"[REGISTRY] update_machine_meta: intern_dynamic item missing project, "
                                  f"skipping. name={name!r} item={item!r}")
                        continue
                    ckey = _make_composite_key(name, project)
                    self._intern_dynamic[ckey] = {
                        "status": item.get("status", ""),
                        "current_task": item.get("current_task", ""),
                        "role": item.get("role", "independent") or "independent",
                        "team_id": item.get("team_id", "") or item.get("team", ""),
                        "last_active": item.get("last_active", ""),
                        "turn_count_today": item.get("turn_count_today"),
                        "turn_active": item.get("turn_active"),
                        "updated_at": now_iso,
                    }

    def get_intern_dynamic(self, intern_name, project=None):
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            d = self._intern_dynamic.get(ckey)
            return dict(d) if d else {}

    def list_intern_dynamic(self):
        with self._lock:
            result = []
            keys = set(self._interns.keys()) | set(self._intern_dynamic.keys())
            for ckey in sorted(keys):
                project, name = _split_composite_key(ckey)
                dyn = self._intern_dynamic.get(ckey) or {}
                entry = self._interns.get(ckey) or {}
                result.append({
                    "name": name,
                    "project": project,
                    "machine_id": entry.get("machine_id", ""),
                    "status": dyn.get("status", ""),
                    "current_task": dyn.get("current_task", ""),
                    "last_active": dyn.get("last_active", ""),
                    "turn_active": dyn.get("turn_active"),
                    "updated_at": dyn.get("updated_at", ""),
                })
            return result

    def update_machine_static_meta(self, machine_id, extension_version=None, hooks_version=None):
        """Patch static meta (extension_version / hooks_version) from daemon's meta_update.
        None-valued fields are left untouched; '' is a valid reset."""
        with self._lock:
            info = self._machine_info.get(machine_id)
            if not info:
                return
            if extension_version is not None:
                info["extension_version"] = extension_version
            if hooks_version is not None:
                info["hooks_version"] = hooks_version

    def remove_connection(self, machine_id, ws=None):
        """Remove all interns owned by this machine. Returns list of entry dicts (with name, project, chat_id, type) for light updates."""
        with self._lock:
            current_ws = self._connections.get(machine_id)
            if ws is not None and current_ws is not None and current_ws is not ws:
                log.warning(f"[REGISTRY] Machine '{machine_id}' stale disconnect ignored")
                return []
            self._connections.pop(machine_id, None)
            self._machine_info.pop(machine_id, None)
            went_offline = []  # list of entry dicts
            captured_keys = set()
            # 1. Capture interns going offline (online on this machine)
            for ckey, mid in list(self._online.items()):
                if mid == machine_id:
                    del self._online[ckey]
                    entry = self._interns.get(ckey)
                    if entry:
                        went_offline.append({
                            "name": entry.get("name"),
                            "project": entry.get("project"),
                            "chat_id": entry.get("chat_id"),
                            "type": entry.get("type", "copilot"),
                        })
                        captured_keys.add(ckey)
            # 2. Remove interns owned by this machine
            to_remove = [k for k, e in self._interns.items() if e["machine_id"] == machine_id]
            for ckey in to_remove:
                entry = self._interns.pop(ckey)
                self._chat_index.pop(entry.get("chat_id", ""), None)
                self._intern_dynamic.pop(ckey, None)
                if ckey not in captured_keys:
                    went_offline.append({
                        "name": entry.get("name"),
                        "project": entry.get("project"),
                        "chat_id": entry.get("chat_id"),
                        "type": entry.get("type", "copilot"),
                    })
        if to_remove:
            removed_names = [_split_composite_key(k)[1] for k in to_remove]
            log.info(f"[REGISTRY] Machine '{machine_id}' disconnected, removed interns: {removed_names}")
        else:
            log.info(f"[REGISTRY] Machine '{machine_id}' disconnected")
        return went_offline

    # ── Intern registration ──

    def register_interns(self, machine_id, interns):
        """Register interns for a machine. interns: [{name, type, chat_id, project}, ...]
        严格模式：缺 project 的条目会被拒绝，避免生成跨项目 stale entry。
        Does NOT steal interns already owned by another machine."""
        persist_changed = False
        with self._lock:
            registered = []
            skipped = []
            for item in interns:
                name = item["name"]
                chat_id = item["chat_id"]
                project = item.get("project")
                if not project:
                    log.error(f"[REGISTRY] register_interns: item missing project, skipping. "
                              f"machine={machine_id} name={name!r} item={item!r}")
                    skipped.append(name)
                    continue
                ckey = _make_composite_key(name, project)
                if name.startswith("machine_helper_"):
                    persist_changed = self._drop_helper_entries_except_locked(name, ckey) or persist_changed
                old = self._interns.get(ckey)
                if old and old["machine_id"] != machine_id and old["machine_id"] != "":
                    # Already owned by another machine — don't steal; just refresh chat_id/type if changed
                    if old["chat_id"] != chat_id:
                        self._chat_index.pop(old["chat_id"], None)
                        old["chat_id"] = chat_id
                        self._chat_index[chat_id] = ckey
                    old["type"] = item.get("type", old.get("type", "copilot"))
                    old["project"] = project
                    old["name"] = name
                    skipped.append(name)
                    continue
                if old:
                    self._chat_index.pop(old.get("chat_id"), None)
                self._interns[ckey] = {
                    "machine_id": machine_id,
                    "chat_id": chat_id,
                    "type": item.get("type", "copilot"),
                    "project": project,
                    "name": name,
                }
                self._chat_index[chat_id] = ckey
                registered.append(name)
                if chat_id:
                    self._chat_persist[ckey] = self._build_chat_persist_entry(
                        ckey, chat_id, item.get("type", "copilot"), project, name)
                    persist_changed = True
            if skipped:
                log.info(f"[REGISTRY] Machine '{machine_id}' skipped {len(skipped)} interns owned by other machines: {skipped}")
        if persist_changed:
            self._save_persist()
        log.info(f"[REGISTRY] Machine '{machine_id}' registered {len(registered)} interns: {registered}")

    def unregister_interns(self, machine_id, intern_names):
        """Unregister specific interns. intern_names must be list of dicts {name, project}.

        Only removes if owned by the given machine.
        """
        with self._lock:
            for item in intern_names:
                if not isinstance(item, dict):
                    log.error(f"[REGISTRY] unregister_interns: string format rejected. "
                              f"machine={machine_id} item={item!r} (caller must send dict with name+project)")
                    continue
                name = item.get("name", "")
                project = item.get("project")
                if not name or not project:
                    log.error(f"[REGISTRY] unregister_interns: missing name/project. "
                              f"machine={machine_id} item={item!r}")
                    continue
                ckey = self._resolve_key(name, project)
                entry = self._interns.get(ckey)
                if entry and entry["machine_id"] == machine_id:
                    self._chat_index.pop(entry.get("chat_id", ""), None)
                    del self._interns[ckey]
        log.info(f"[REGISTRY] Machine '{machine_id}' unregistered interns: {intern_names}")

    # ── Chat lookup / mapping ──

    def find_intern_by_chat(self, chat_id):
        """Return bare intern_name for given chat_id (back-compat). None if not found."""
        with self._lock:
            ckey = self._chat_index.get(chat_id)
            if not ckey:
                return None
            _, name = _split_composite_key(ckey)
            return name

    def find_entry_by_chat(self, chat_id):
        """Return full entry dict (with name/project/machine_id/chat_id/type) for given chat_id; None if not found."""
        with self._lock:
            ckey = self._chat_index.get(chat_id)
            if not ckey:
                return None
            entry = self._interns.get(ckey)
            return dict(entry) if entry else None

    def find_chat_id(self, intern_name, project=None):
        """Get chat_id for intern. Falls back to persistent storage."""
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            entry = self._interns.get(ckey)
            if entry and entry.get("chat_id"):
                return entry["chat_id"]
            persist = self._chat_persist.get(ckey)
            return persist.get("chat_id") if persist else None

    def find_chat_entries_by_name(self, intern_name):
        """Return all persisted/runtime chat entries for an intern name.

        This is intentionally name-wide and is only used for global helper
        identities such as ``machine_helper_*``. Normal interns remain
        project-scoped.
        """
        with self._lock:
            entries = []
            seen = set()
            keys = set(self._interns.keys()) | set(self._chat_persist.keys())
            for ckey in sorted(keys):
                project, name = _split_composite_key(ckey)
                if name != intern_name:
                    continue
                entry = self._interns.get(ckey) or {}
                persist = self._chat_persist.get(ckey) or {}
                chat_id = entry.get("chat_id") or persist.get("chat_id")
                if not chat_id or chat_id in seen:
                    continue
                seen.add(chat_id)
                entries.append({"project": project, "chat_id": chat_id})
            return entries

    def remove_intern_chats_by_name(self, intern_name):
        """Remove all ordinary intern chat mappings with this bare name.

        Machine helper ids are global per machine. Remove every ordinary intern
        chat mapping for the helper name without touching the separate helper
        registry.
        """
        removed = []
        with self._lock:
            keys = set(self._interns.keys()) | set(self._chat_persist.keys())
            for ckey in sorted(keys):
                project, name = _split_composite_key(ckey)
                if name != intern_name:
                    continue
                entry = self._interns.pop(ckey, None) or {}
                persist = self._chat_persist.pop(ckey, None) or {}
                chat_id = entry.get("chat_id") or persist.get("chat_id")
                if chat_id:
                    self._chat_index.pop(chat_id, None)
                self._online.pop(ckey, None)
                self._intern_dynamic.pop(ckey, None)
                removed.append({"project": project, "chat_id": chat_id})
        if removed:
            self._save_persist()
        return removed

    def get_last_group_name(self, intern_name, project=None):
        """Return last successfully-written group name for this intern, or None.

        task242: relay 重启后 _update_group_light_for_chat 比较 desired new_name 与
        此值，相等则跳过 api.update_chat，避免重启首轮 sync_online 全量刷绿触发飞书
        request trigger frequency limit。返回 None 时调用方按"必须更新一次"处理；首次
        成功 update_chat 后会落盘。
        """
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            persist = self._chat_persist.get(ckey)
            return persist.get("last_group_name") if persist else None

    def set_last_group_name(self, intern_name, project, name):
        """Persist last successful group name. Only call after api.update_chat
        returns no error (dedupe 仅在远端确认成功后才有意义)。

        task242 + 项目规则 #6：若 _chat_persist 中没有对应条目（_update_group_light_for_chat
        理论上只在已注册的 intern 上跑），写一条 warning 后直接 return；不静默建半残条目。
        """
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            persist = self._chat_persist.get(ckey)
            if not persist:
                log.warning(f"[REGISTRY] set_last_group_name: no chat_persist entry for "
                            f"'{intern_name}' (project={project}); skipping (upstream bug)")
                return
            if persist.get("last_group_name") == name:
                return
            persist["last_group_name"] = name
        self._save_persist()

    def update_chat_id(self, intern_name, chat_id, intern_type=None, project=None, machine_id=None):
        """Set or update chat_id.

        If the caller supplies machine_id, the chat mapping is associated with
        that daemon as the detail-mode owner. Without machine_id this preserves
        the legacy placeholder behavior.
        """
        owner_machine_id = str(machine_id or "").strip()
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            resolved_project = _split_composite_key(ckey)[0]
            entry = self._interns.get(ckey)
            if entry:
                old_chat = entry.get("chat_id")
                if old_chat:
                    self._chat_index.pop(old_chat, None)
                entry["chat_id"] = chat_id
                if intern_type:
                    entry["type"] = intern_type
                entry["project"] = resolved_project
                entry["name"] = intern_name
                if owner_machine_id:
                    current_machine_id = entry.get("machine_id", "")
                    if not current_machine_id or current_machine_id == owner_machine_id:
                        entry["machine_id"] = owner_machine_id
                    elif self._connections.get(current_machine_id) is None:
                        log.warning(
                            f"[REGISTRY] update_chat_id rebinds stale owner for "
                            f"'{intern_name}' (project={resolved_project}): "
                            f"{current_machine_id} -> {owner_machine_id}"
                        )
                        entry["machine_id"] = owner_machine_id
                    else:
                        log.warning(
                            f"[REGISTRY] update_chat_id keeps existing owner for "
                            f"'{intern_name}' (project={resolved_project}): "
                            f"{current_machine_id} != {owner_machine_id}"
                        )
                self._chat_index[chat_id] = ckey
            else:
                self._interns[ckey] = {
                    "machine_id": owner_machine_id,
                    "chat_id": chat_id,
                    "type": intern_type or "copilot",
                    "project": resolved_project,
                    "name": intern_name,
                }
                self._chat_index[chat_id] = ckey
            persist_type = intern_type or (entry.get("type", "copilot") if entry else "copilot")
            # task242: _build_chat_persist_entry 保留 last_group_name 当 chat_id 不变
            self._chat_persist[ckey] = self._build_chat_persist_entry(
                ckey, chat_id, persist_type, resolved_project, intern_name)
        self._save_persist()
        log.info(
            f"[REGISTRY] Updated chat_id for '{intern_name}' "
            f"(project={resolved_project}, machine={owner_machine_id or '-'}): {chat_id}"
        )

    def remove_intern_chat(self, intern_name, project=None):
        """Remove an intern's chat/runtime mapping entirely.

        `/api/chat/delete` is called from `internctl delete`, so retaining a
        machine-owned `_interns` entry after the Feishu group is deleted leaves
        stale interns in `/api/machines` until the daemon reconnects. Delete the
        runtime entry, online state, dynamic status and persisted chat together.
        """
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            entry = self._interns.pop(ckey, None)
            if entry:
                self._chat_index.pop(entry.get("chat_id", ""), None)
            self._online.pop(ckey, None)
            self._intern_dynamic.pop(ckey, None)
            self._chat_persist.pop(ckey, None)
        self._save_persist()

    def get_entry(self, intern_name, project=None):
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            return self._interns.get(ckey, {}).copy()

    def get_connection(self, machine_id):
        with self._lock:
            return self._connections.get(machine_id)

    def get_all_interns(self):
        """Return dict keyed by intern_name (back-compat for /api/registry consumers).
        For interns with the same name in different projects, last one wins (acceptable for single-project state)."""
        with self._lock:
            result = {}
            for ckey, entry in self._interns.items():
                name = entry.get("name") or _split_composite_key(ckey)[1]
                result[name] = dict(entry)
            return result

    def get_all_interns_by_key(self):
        """Return dict keyed by composite key '<project>:<intern_name>'. New API for clients that need full disambiguation."""
        with self._lock:
            return {k: dict(v) for k, v in self._interns.items()}

    def get_current_scene(self):
        """Return the user-visible current active group scene.

        This is a diagnosis/product surface, not a routing primitive. It lets
        admins distinguish the current retained/active groups from stale
        persisted mappings that may still be visible in Feishu clients.
        """
        with self._lock:
            active = []
            stale = []
            for ckey in sorted(set(self._interns.keys()) | set(self._chat_persist.keys())):
                project, name = _split_composite_key(ckey)
                entry = self._interns.get(ckey)
                persist = self._chat_persist.get(ckey) or {}
                dyn = self._intern_dynamic.get(ckey) or {}
                source = entry or {}
                chat_id = source.get("chat_id") or persist.get("chat_id", "")
                last_group_name = persist.get("last_group_name", "")
                if entry and entry.get("machine_id"):
                    online_machine = self._online.get(ckey)
                    is_online = bool(online_machine)
                    active.append({
                        "name": name,
                        "project": project,
                        "type": source.get("type", persist.get("type", "")),
                        "chat_id": chat_id,
                        "last_group_name": last_group_name,
                        "machine_id": source.get("machine_id", ""),
                        "online": is_online,
                        "online_machine_id": online_machine or "",
                        "status": dyn.get("status", ""),
                        "current_task": dyn.get("current_task", ""),
                        "is_helper": name.startswith("machine_helper_"),
                        "is_auxiliary": _is_auxiliary_scene_intern(name),
                        "group_light": _group_light_from_name(last_group_name, is_online),
                    })
                else:
                    stale.append({
                        "name": name,
                        "project": project,
                        "type": source.get("type", persist.get("type", "")),
                        "chat_id": chat_id,
                        "last_group_name": last_group_name,
                        "is_helper": name.startswith("machine_helper_"),
                        "is_auxiliary": _is_auxiliary_scene_intern(name),
                        "group_light": _group_light_from_name(last_group_name, False),
                    })
            online_helpers = {
                item.get("name") for item in active
                if item.get("is_helper") and item.get("online")
            }
            if online_helpers:
                active = [
                    item for item in active
                    if not (item.get("is_helper") and not item.get("online") and item.get("name") in online_helpers)
                ]
            return {
                "schema": "intern-agents.relay-current-scene.v1",
                "active_groups": active,
                "stale_persisted_groups": stale,
                "summary": {
                    "active_groups": len(active),
                    "online_groups": len([item for item in active if item.get("online")]),
                    "stale_persisted_groups": len(stale),
                    "active_red_groups": len([item for item in active if item.get("group_light") == "red"]),
                    "active_auxiliary_groups": len([item for item in active if item.get("is_auxiliary")]),
                },
                "warnings": _scene_warnings(active, stale),
            }

    def find_candidates_by_name(self, name):
        """task213: list all (project, machine_id, intern_type, online) entries matching intern name.

        Used by peer routing when caller omits to_project — A daemon asks relay to
        resolve (project, name). 0 → unknown_target / 1 → auto-use / N → ambiguous.
        """
        if not name:
            return []
        with self._lock:
            result = []
            for ckey, entry in self._interns.items():
                entry_name = entry.get("name") or _split_composite_key(ckey)[1]
                if entry_name != name:
                    continue
                project = entry.get("project") or _split_composite_key(ckey)[0]
                online_mid = self._online.get(ckey)
                result.append({
                    "project": project,
                    "machine_id": entry.get("machine_id", ""),
                    "intern_type": entry.get("type", ""),
                    "role": self._intern_dynamic.get(ckey, {}).get("role", "independent"),
                    "team_id": self._intern_dynamic.get(ckey, {}).get("team_id", ""),
                    "online": bool(online_mid),
                })
            return result

    # ── Online state ──

    def set_online(self, intern_name, machine_id, chat_id=None, intern_type=None, project=None):
        """Mark intern as online. New machine always wins."""
        persist_changed = False
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            resolved_project = _split_composite_key(ckey)[0]
            if intern_name.startswith("machine_helper_"):
                persist_changed = self._drop_helper_entries_except_locked(intern_name, ckey) or persist_changed
            entry = self._interns.get(ckey)
            if not entry:
                if not chat_id:
                    log.warning(f"[REGISTRY] set_online rejected: '{intern_name}' (project={resolved_project}) not registered and no chat_id provided")
                    return False, None
                entry = {
                    "machine_id": machine_id,
                    "chat_id": chat_id,
                    "type": intern_type or "copilot",
                    "project": resolved_project,
                    "name": intern_name,
                }
                self._interns[ckey] = entry
                self._chat_index[chat_id] = ckey
                # task242: _build_chat_persist_entry 保留 last_group_name 当 chat_id 不变。
                # restart 后 _interns 为空但 _chat_persist 已 load；首轮 sync_online 走此分支
                # auto-register，必须保留旧 last_group_name 才能命中 dedupe。
                self._chat_persist[ckey] = self._build_chat_persist_entry(
                    ckey, chat_id, entry["type"], resolved_project, intern_name)
                persist_changed = True
                log.info(f"[REGISTRY] Auto-registered '{intern_name}' (project={resolved_project}) from sync_online (chat_id={chat_id}, type={entry['type']})")
            else:
                if chat_id and entry.get("chat_id") != chat_id:
                    self._chat_index.pop(entry.get("chat_id", ""), None)
                    entry["chat_id"] = chat_id
                    self._chat_index[chat_id] = ckey
                    # task242: chat_id 真变化 → helper 内部 old.chat_id != chat_id → 不保留
                    # last_group_name；新 chat 飞书侧名字未知，必须重发一次 update_chat。
                    self._chat_persist[ckey] = self._build_chat_persist_entry(
                        ckey, chat_id, intern_type or entry.get("type", "copilot"),
                        resolved_project, intern_name)
                    persist_changed = True
                if intern_type:
                    entry["type"] = intern_type
                entry["project"] = resolved_project
                entry["name"] = intern_name
            if entry["machine_id"] != machine_id:
                old_machine = entry["machine_id"]
                entry["machine_id"] = machine_id
                log.info(f"[REGISTRY] Intern '{intern_name}' (project={resolved_project}) migrated from '{old_machine}' to '{machine_id}' via set_online")
            old_online = self._online.get(ckey)
            if old_online and old_online != machine_id:
                log.info(f"[REGISTRY] Intern '{intern_name}' (project={resolved_project}) was online on '{old_online}', now taken by '{machine_id}'")
            self._online[ckey] = machine_id

            # Auto-offline other Copilot interns on the same machine.
            # tmux-based interns (Claude/Codex) coexist freely on a machine and never auto-offline each other.
            offlined_copilot = None  # (name, project) tuple or None
            this_type = entry.get("type", "copilot")
            tmux_types = ("claude", "codex")
            if this_type not in tmux_types:
                for other_ckey, other_mid in list(self._online.items()):
                    if other_ckey == ckey:
                        continue
                    if other_mid != machine_id:
                        continue
                    other_entry = self._interns.get(other_ckey, {})
                    if other_entry.get("type") in tmux_types:
                        continue
                    del self._online[other_ckey]
                    other_project, other_name = _split_composite_key(other_ckey)
                    offlined_copilot = (other_entry.get("name") or other_name, other_project)
                    log.info(f"[REGISTRY] Auto-offlined Copilot {offlined_copilot!r} on '{machine_id}' (replaced by '{intern_name}')")
                    break

        if persist_changed:
            self._save_persist()
        return True, offlined_copilot

    def set_offline(self, intern_name, machine_id, project=None):
        """Mark intern as offline. Only removes if owned by the given machine."""
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            if self._online.get(ckey) == machine_id:
                del self._online[ckey]
                return True
            return False

    def is_online(self, intern_name, project=None):
        """Returns (online, machine_id or None)."""
        with self._lock:
            ckey = self._resolve_key(intern_name, project)
            mid = self._online.get(ckey)
            return (True, mid) if mid else (False, None)

    def helper_online_elsewhere(self, helper_name, project=None):
        if not helper_name.startswith("machine_helper_"):
            return False, None, None
        with self._lock:
            for ckey, machine_id in self._online.items():
                entry_project, entry_name = _split_composite_key(ckey)
                if entry_name != helper_name:
                    continue
                if project and entry_project == project:
                    continue
                return True, machine_id, entry_project
            return False, None, None

    def get_all_online(self):
        """Return dict keyed by composite key (composite_key → machine_id)."""
        with self._lock:
            return dict(self._online)

    def get_all_online_by_name(self):
        """Return dict keyed by bare intern_name (back-compat). Last write wins on duplicates."""
        with self._lock:
            result = {}
            for ckey, mid in self._online.items():
                _, name = _split_composite_key(ckey)
                result[name] = mid
            return result

    # ── Machine helper registry (independent from ordinary intern registry) ──

    def register_machine_helper(self, machine_id, helper_id=None, runtime="codex", chat_id=None,
                                status="stopped", created_by_open_id="", last_operator_open_id="",
                                last_error="", selected_machine_id=None, detail_mode=None,
                                project=None):
        if not machine_id:
            raise ValueError("machine_id required")
        helper_id = helper_id or _machine_helper_id_for_machine(machine_id)
        now_iso = datetime.now().isoformat()
        with self._lock:
            old = self._machine_helpers.get(machine_id) or {}
            old_chat_id = old.get("chat_id", "")
            new_chat_id = old.get("chat_id", "") if chat_id is None else chat_id
            if old_chat_id and old_chat_id != new_chat_id:
                self._helper_chat_index.pop(old_chat_id, None)
            entry = {
                "machine_id": machine_id,
                "helper_id": helper_id,
                "project": project or old.get("project") or _machine_helper_project_for_machine(machine_id),
                "runtime": runtime or old.get("runtime") or "codex",
                "status": status or old.get("status") or "stopped",
                "chat_id": new_chat_id,
                "created_by_open_id": created_by_open_id or old.get("created_by_open_id", ""),
                "last_operator_open_id": last_operator_open_id or old.get("last_operator_open_id", ""),
                "created_at": old.get("created_at") or now_iso,
                "updated_at": now_iso,
                "last_error": last_error,
                "selected_machine_id": (
                    old.get("selected_machine_id", "") if selected_machine_id is None
                    else selected_machine_id
                ),
                "detail_mode": old.get("detail_mode", "full") if detail_mode is None else detail_mode,
                "audit_log": list(old.get("audit_log") or []),
            }
            self._machine_helpers[machine_id] = entry
            if entry["chat_id"]:
                self._helper_chat_index[entry["chat_id"]] = machine_id
            result = dict(entry)
        self._save_machine_helpers()
        return result

    def append_machine_helper_audit(self, machine_id, action, operator_open_id="", detail=None):
        if not machine_id:
            raise ValueError("machine_id required")
        if not self.get_machine_helper(machine_id):
            self.register_machine_helper(machine_id)
        with self._lock:
            entry = self._machine_helpers.get(machine_id)
            audit = list(entry.get("audit_log") or [])
            audit.append({
                "action": action,
                "operator_open_id": operator_open_id,
                "detail": dict(detail or {}),
                "created_at": datetime.now().isoformat(),
            })
            entry["audit_log"] = audit[-200:]
            entry["updated_at"] = datetime.now().isoformat()
            self._machine_helpers[machine_id] = entry
            result = list(entry["audit_log"])
        self._save_machine_helpers()
        return result

    def update_machine_helper_status(self, machine_id, status, runtime=None, chat_id=None,
                                     last_operator_open_id="", last_error=""):
        if not machine_id:
            raise ValueError("machine_id required")
        with self._lock:
            current = self._machine_helpers.get(machine_id)
        if not current:
            return self.register_machine_helper(
                machine_id,
                runtime=runtime or "codex",
                chat_id=chat_id or "",
                status=status,
                last_operator_open_id=last_operator_open_id,
                last_error=last_error,
            )
        return self.register_machine_helper(
            machine_id,
            helper_id=current.get("helper_id"),
            runtime=runtime or current.get("runtime", "codex"),
            chat_id=current.get("chat_id", "") if chat_id is None else chat_id,
            status=status,
            created_by_open_id=current.get("created_by_open_id", ""),
            last_operator_open_id=last_operator_open_id or current.get("last_operator_open_id", ""),
            last_error=last_error,
        )

    def find_helper_by_chat(self, chat_id):
        with self._lock:
            machine_id = self._helper_chat_index.get(chat_id)
            if not machine_id:
                return None
            entry = self._machine_helpers.get(machine_id)
            return dict(entry) if entry else None

    def get_machine_helper(self, machine_id):
        with self._lock:
            entry = self._machine_helpers.get(machine_id)
            return dict(entry) if entry else {}

    def get_helpers_summary(self):
        with self._lock:
            return {machine_id: dict(entry) for machine_id, entry in self._machine_helpers.items()}

    def get_machines_summary(self):
        with self._lock:
            result = {}
            for mid, info in self._machine_info.items():
                interns = []
                interns_detail = []
                online_names = []
                for ckey, entry in self._interns.items():
                    if entry["machine_id"] != mid:
                        continue
                    name = entry.get("name") or _split_composite_key(ckey)[1]
                    interns.append(name)
                    project = entry.get("project") or _split_composite_key(ckey)[0]
                    is_online = self._online.get(ckey) == mid
                    if is_online:
                        online_names.append(name)
                    dyn = self._intern_dynamic.get(ckey) or {}
                    interns_detail.append({
                        "name": name,
                        "project": project,
                        "type": entry.get("type", "copilot"),
                        "online": is_online,
                        "status": dyn.get("status", ""),
                        "current_task": dyn.get("current_task", ""),
                        "last_active": dyn.get("last_active", ""),
                        "turn_count_today": dyn.get("turn_count_today"),
                        "turn_active": dyn.get("turn_active"),
                        "dynamic_updated_at": dyn.get("updated_at", ""),
                    })
                connected = mid in self._connections
                result[mid] = {
                    "connected_at": info["connected_at"],
                    "owner_mobile": info.get("owner_mobile", ""),
                    "owner_open_id": info.get("owner_open_id", ""),
                    "ip": info.get("ip", ""),
                    "ssh_port": info.get("ssh_port", 22),
                    "daemon_hash": info.get("daemon_hash", ""),
                    "extension_version": info.get("extension_version", ""),
                    "hooks_version": info.get("hooks_version", ""),
                    "cli_versions": dict(info.get("cli_versions") or {}),
                    "capabilities": list(info.get("capabilities") or []),
                    "workspaces": list(info.get("workspaces") or []),
                    "resources": dict(info.get("resources") or {}),
                    "resources_updated_at": info.get("resources_updated_at"),
                    "warnings": [dict(w) for w in (info.get("warnings") or [])],
                    "warnings_updated_at": info.get("warnings_updated_at"),
                    "metrics": dict(info.get("metrics") or {}),
                    "metrics_updated_at": info.get("metrics_updated_at"),
                    "ws_connected": connected,
                    "interns": interns,
                    "interns_detail": interns_detail,
                    "online_interns": online_names,
                }
            return result


# ══════════════════════════════════════════
# 飞书消息解析（复用 daemon 逻辑）
# ══════════════════════════════════════════

# ── 红绿灯管理（server 侧统一处理） ──

# Current enterprise group names carry the intern type in the name:
#   claude = 🤖 / codex = 🚀 / copilot = empty
_TYPE_EMOJI = {"claude": "🤖 ", "codex": "🚀 ", "copilot": ""}


def _build_group_name(intern_name, is_online, intern_type, project):
    """Name format: `🟢 🤖 rule_bob/axis_intern_agents`.

    - prefix: 🟢 online / 🔴 offline
    - type emoji: 🤖 claude / 🚀 codex / (empty) copilot
    - stripped: intern_name with `intern_` prefix removed
    - project: disambiguates same intern across projects
    """
    prefix = "🟢" if is_online else "🔴"
    stripped = intern_name[len("intern_"):] if intern_name.startswith("intern_") else intern_name
    badge = _TYPE_EMOJI.get(intern_type or "copilot", "")
    return f"{prefix} {badge}{stripped}/{project}"


def _update_group_light_for_chat(api, chat_id, intern_name, intern_type, is_online, project, machine_id=None, registry=None):
    if not project:
        log.error(f"[LIGHT] _update_group_light_for_chat: project is required for '{intern_name}'; skipping.")
        return
    if not chat_id:
        log.error(f"[LIGHT] _update_group_light_for_chat: chat_id is required for '{intern_name}' (project={project}); skipping.")
        return
    new_name = _build_group_name(intern_name, is_online, intern_type, project)
    color = "green" if is_online else "red"
    machine_part = machine_id or "?"
    if registry is not None:
        current_online, current_machine = registry.is_online(intern_name, project=project)
        if is_online and not current_online:
            log.info(f"[LIGHT] ⏭ skip stale GREEN {intern_name} "
                     f"(project={project} machine={machine_part}); currently offline")
            return
        if not is_online and current_online:
            log.info(f"[LIGHT] ⏭ skip stale RED {intern_name} "
                     f"(project={project} machine={machine_part}); currently online on {current_machine}")
            return
        if not is_online and intern_name.startswith("machine_helper_"):
            helper_online, helper_machine, helper_project = registry.helper_online_elsewhere(intern_name, project=project)
            if helper_online:
                log.info(f"[LIGHT] ⏭ skip stale helper RED {intern_name} "
                         f"(project={project} machine={machine_part}); currently online on "
                         f"{helper_machine} project={helper_project}")
                return
    # task242: 单点 dedupe — 比较 desired new_name 与上次成功写入飞书的 last_group_name；
    # 相等则跳过 api.update_chat，避免 relay 重启后 sync_online 首轮全量刷绿触发 request
    # trigger frequency limit。registry 缺省时退化为旧行为（无 dedupe），仅用于早期或
    # 测试场景；生产调用方都必须显式传 registry。
    if registry is not None:
        last_name = registry.get_last_group_name(intern_name, project)
        if last_name == new_name and not intern_name.startswith("machine_helper_"):
            log.info(f"[LIGHT] ⏭ skip update_chat {intern_name} (project={project} machine={machine_part}) "
                     f"name unchanged: {new_name!r}")
            return
    err = api.update_chat(chat_id, name=new_name)
    if err:
        log.error(f"[LIGHT] ✗ update_chat failed {intern_name} (project={project} machine={machine_part}) color={color}: {err}")
        return
    log.info(f"[LIGHT] {'🟢' if is_online else '🔴'} {intern_name} (project={project} machine={machine_part})")
    if registry is not None:
        if not is_online:
            current_online, current_machine = registry.is_online(intern_name, project=project)
            if current_online:
                green_name = _build_group_name(intern_name, True, intern_type, project)
                green_err = api.update_chat(chat_id, name=green_name)
                if green_err:
                    log.error(f"[LIGHT] ✗ restore GREEN after stale RED failed {intern_name} "
                              f"(project={project} machine={machine_part}, online={current_machine}): {green_err}")
                    return
                log.info(f"[LIGHT] 🟢 {intern_name} (project={project} machine={machine_part}) "
                         f"restored after stale RED race; currently online on {current_machine}")
                registry.set_last_group_name(intern_name, project, green_name)
                return
        registry.set_last_group_name(intern_name, project, new_name)


def _update_group_light(api, registry, intern_name, is_online, project, machine_id=None):
    """Update Feishu group name with online/offline light.

    task204 严格模式：project 必须显式传入。旧版按 intern_name 后缀搜索 registry
    会命中历史 stale 的 axis_intern_agents:* entry，把群名挂错 project。
    """
    if not project:
        log.error(f"[LIGHT] _update_group_light: project is required for '{intern_name}'; "
                  f"caller did not pass project. Skipping.")
        return
    entry = registry.get_entry(intern_name, project=project)
    if not entry:
        return
    chat_id = entry.get("chat_id")
    intern_type = entry.get("type", "copilot")
    _update_group_light_for_chat(api, chat_id, intern_name, intern_type, is_online, project, machine_id=machine_id, registry=registry)


def _refresh_existing_chat_light(api, registry, intern_name, project):
    """Refresh an existing chat's light after chat/type metadata updates.

    The HTTP chat/create path is metadata repair, not online-state authority.
    It may refresh green for an already-online intern after a type change, but it
    must not write red when registry is offline; authoritative offline/red comes
    from sync_online diffs or disconnect grace.
    """
    is_online_state, light_machine = registry.is_online(intern_name, project=project)
    if is_online_state:
        log.info(f"[LIGHT] schedule GREEN existing-chat refresh {intern_name} "
                 f"(project={project} machine={light_machine})")
        threading.Thread(
            target=_update_group_light,
            args=(api, registry, intern_name, True, project, light_machine),
            daemon=True).start()
        return

    log.info(f"[LIGHT] skip RED existing-chat refresh {intern_name} "
             f"(project={project}); waiting for authoritative sync_online/offline path")


def _render_post_elem(elem):
    """task228: post 富文本单个 element → 字符串片段。

    飞书 PC 端检测到 URL 会把整条消息自动升格成 msg_type=post，URL 变成 a
    tag；如果只看 tag=="text" 会直接丢 URL。识别的 tag：
      - text / md / code_inline：直接 `.text`。
      - a：`[display](href)`；display 与 href 相同则裸输出 href 避免冗余。
      - at：`@user_name`；兜底 `@user_id`。
      - img：这里不处理文字（image_key 归 extract_attachments 做附件抽取）。
      - 未知 tag：忽略不抛错（下游已经在 FEISHU_WS DEBUG log 里有完整
        raw content 可复盘；不静默吃整条消息）。
    """
    if not isinstance(elem, dict):
        return ""
    tag = elem.get("tag")
    if tag in ("text", "md", "code_inline"):
        return elem.get("text", "") or ""
    if tag == "a":
        href = (elem.get("href") or "").strip()
        display = (elem.get("text") or "").strip()
        if not href:
            return display
        if not display or display == href:
            return href
        return f"[{display}]({href})"
    if tag == "at":
        name = (elem.get("user_name") or "").strip()
        if name:
            return name if name.startswith("@") else f"@{name}"
        uid = (elem.get("user_id") or "").strip()
        return f"@{uid}" if uid else ""
    # img 交给 extract_attachments；其他 tag（emotion/hr/code_block 等）先不识别。
    return ""


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
                        piece = _render_post_elem(elem)
                        if piece:
                            texts.append(piece)
            else:
                # Legacy format: {"zh_cn": {"title":"...","content":[...]}}
                for lang_content in data.values():
                    if isinstance(lang_content, dict):
                        for line in lang_content.get("content", []):
                            for elem in line:
                                piece = _render_post_elem(elem)
                                if piece:
                                    texts.append(piece)
            return " ".join(texts).strip()
    except Exception:
        pass
    return content.strip() if isinstance(content, str) else ""


def _iter_post_elements(data):
    """task228: 遍历 post content JSON 中所有 element（flat 和 localized 两种格式）。"""
    content_lines = data.get("content", [])
    if isinstance(content_lines, list) and content_lines and isinstance(content_lines[0], list):
        for line in content_lines:
            if isinstance(line, list):
                for elem in line:
                    yield elem
        return
    for lang_content in data.values():
        if isinstance(lang_content, dict):
            for line in lang_content.get("content", []) or []:
                if isinstance(line, list):
                    for elem in line:
                        yield elem


def extract_attachments(msg_type, content):
    """task228: 从飞书消息的 content JSON 抽取附件元信息（仅 key/name/ext，不下载）。

    - msg_type=image → `[{"kind":"image","key":image_key,"ext":".png"}]`
      image_key 通常长这样 `img_v3_0xxx`，ext 统一用 `.png`（飞书图片都是 PNG）。
    - msg_type=file → `[{"kind":"file","key":file_key,"name":file_name}]`
      file_name 由飞书 content 字段给出。
    - msg_type=post → 遍历所有 `tag=="img"` element 收集 image_key（post
      富文本里可以嵌多张图，飞书 PC 端也会把发送时带图片+正文的消息升格为
      post）。
    - msg_type=audio/media/sticker 等暂不支持 → 返回 `[]`。
    - 解析 JSON 失败 / 缺 key → 返回 `[]`（不抛错；handle_message 侧会因为 text
      也空而整条 return，或单独走 text 路径）。
    """
    if msg_type not in ("image", "file", "post"):
        return []
    try:
        data = json.loads(content)
    except Exception:
        return []
    if msg_type == "image":
        image_key = data.get("image_key") or ""
        if not image_key:
            return []
        return [{"kind": "image", "key": image_key, "ext": ".png"}]
    if msg_type == "file":
        file_key = data.get("file_key") or ""
        file_name = (data.get("file_name") or "").strip()
        if not file_key:
            return []
        return [{"kind": "file", "key": file_key, "name": file_name}]
    # post 富文本嵌图
    atts = []
    for elem in _iter_post_elements(data):
        if isinstance(elem, dict) and elem.get("tag") == "img":
            image_key = (elem.get("image_key") or "").strip()
            if image_key:
                atts.append({"kind": "image", "key": image_key, "ext": ".png"})
    return atts


class _AttachmentError(Exception):
    """task228: 附件下载/大小检查失败的统一异常。handle_message 捕获后 reply_message
    给主管看原因，不能静默吞（项目规则 6）。"""


def _download_attachments(api, message_id, intern_name, atts_meta):
    """逐个下载附件并做大小检查，返回 ws 转发用的 payload list：
        [{"kind": "image|file", "filename": "...", "bytes_b64": "..."}, ...]

    任意一条失败（下载错、大小超）→ raise `_AttachmentError`；caller 收到后整条
    消息 reject（text 也不转发，避免"主管以为 AI 看到图"）。
    """
    payload = []
    for meta in atts_meta:
        kind = meta["kind"]
        key = meta["key"]
        rtype = "image" if kind == "image" else "file"
        data_bytes, server_filename, err = api.download_message_resource(message_id, key, rtype)
        if err or data_bytes is None:
            raise _AttachmentError(f"下载 {kind} ({key[:12]}...) 失败: {err}")
        size = len(data_bytes)
        if size > ATTACHMENT_MAX_BYTES:
            raise _AttachmentError(
                f"{kind} 过大 ({size/1024/1024:.1f} MB > {ATTACHMENT_MAX_BYTES/1024/1024:.0f} MB)，"
                f"请压缩或 scp 传输")
        # 选定落盘文件名：file 优先用飞书返回的 file_name，没有则用 Content-Disposition；
        # image 直接用 key + ext。最终仍要做 basename 保护（防目录穿越）。
        if kind == "file":
            chosen = meta.get("name") or server_filename or f"{key}.bin"
        else:
            chosen = server_filename or f"{key}{meta.get('ext', '.png')}"
        filename = os.path.basename(chosen) or f"{key}.bin"
        payload.append({
            "kind": kind,
            "filename": filename,
            "bytes_b64": base64.b64encode(data_bytes).decode("ascii"),
        })
        log.info(f"[ROUTE] attachment downloaded for '{intern_name}': {kind} {filename} ({size} bytes)")
    return payload


def _json_payload_size_bytes(data):
    return len(json.dumps(data, ensure_ascii=False).encode("utf-8"))


def _format_bytes_mb(size):
    return f"{size / 1024 / 1024:.1f} MiB"


def _delivery_failure_reason_text(reason, payload_bytes=0):
    reason = str(reason or "")
    if reason == "payload_too_large":
        size_text = _format_bytes_mb(payload_bytes) if payload_bytes else "超过上限"
        return (
            f"附件或消息过大，relay-daemon WS payload 为 {size_text}，"
            f"超过 {_format_bytes_mb(RELAY_WS_MAX_SIZE_BYTES)} 上限；请压缩或拆分附件后重试"
        )
    if reason == "target_machine_not_connected":
        return "目标机器未连接 relay-daemon 通道"
    if reason == "relay_loop_not_running":
        return "relay WebSocket 服务未就绪"
    if reason.startswith("ws_send_failed:"):
        return f"relay-daemon 通道发送失败：{reason.split(':', 1)[1].strip()}"
    if reason.startswith("ws_send_timeout_or_error:"):
        return f"relay-daemon 通道发送超时或失败：{reason.split(':', 1)[1].strip()}"
    if reason == "send_failed":
        return "relay-daemon 通道投递失败"
    return reason or "relay-daemon 通道投递失败"


def _reply_delivery_failure(api, message_id, intern_name, reason, payload_bytes=0):
    text = (
        f"⚠️ Relay 投递失败：未能把这条消息发送给 `{intern_name}`。\n"
        f"原因：{_delivery_failure_reason_text(reason, payload_bytes)}"
    )
    err = api.reply_message(message_id, text)
    if err:
        log.error(f"[ROUTE] delivery failure reply failed message_id={message_id}: {err}")
    return err


def _send_to_machine_with_reason(relay_ws_server, machine_id, payload):
    payload_bytes = _json_payload_size_bytes(payload)
    if payload_bytes > RELAY_WS_MAX_SIZE_BYTES:
        return False, "payload_too_large", payload_bytes

    method = getattr(type(relay_ws_server), "send_to_machine_result", None)
    if callable(method):
        ok, reason = relay_ws_server.send_to_machine_result(
            machine_id,
            payload,
            payload_bytes=payload_bytes,
        )
        return ok, reason, payload_bytes

    sent = relay_ws_server.send_to_machine(machine_id, payload)
    return bool(sent), "" if sent else "send_failed", payload_bytes


def _helper_result_registry_chat_id(msg, action, ok, status):
    if ok and action in {"stop", "delete_group", "confirm_delete"} and status in {"stopped", "deleted", "updated"}:
        return ""
    return msg.get("chat_id") if "chat_id" in msg else None


def _apply_helper_action_result_to_registry(registry, msg, fallback_machine_id=""):
    helper_machine_id = msg.get("machine_id") or fallback_machine_id
    action = msg.get("helper_action", "")
    ok = bool(msg.get("ok"))
    status = msg.get("status") or ("running" if ok and action == "start" else "")
    if not status:
        status = "failed" if not ok else "updated"
    current = registry.get_machine_helper(helper_machine_id) if helper_machine_id else {}
    runtime = msg.get("runtime") or current.get("runtime") or "codex"
    entry = registry.update_machine_helper_status(
        helper_machine_id,
        status,
        runtime=runtime,
        chat_id=_helper_result_registry_chat_id(msg, action, ok, status),
        last_error="" if ok else msg.get("error", ""),
    )
    registry.append_machine_helper_audit(
        helper_machine_id,
        f"{action}_result",
        detail={k: v for k, v in msg.items() if k not in {"type"}},
    )
    return entry, action, ok, status


# ══════════════════════════════════════════
# Relay WebSocket Server（Local Agent 连接）
# ══════════════════════════════════════════

class RelayWSServer:
    """WebSocket server accepting connections from Local Agents.

    Protocol:
    - Client → Server: auth, register_interns, unregister_interns, intern_online, intern_offline, heartbeat, check_online
    - Server → Client: auth_result, feishu_message, heartbeat_ack, intern_online_rejected, check_online_result
    """

    def __init__(self, host, port, relay_token, registry, api):
        self.host = host
        self.port = port
        self.relay_token = relay_token
        self.registry = registry
        self.api = api
        self._loop = None
        self._server = None
        self._pending_red_lock = threading.Lock()
        self._pending_red = {}
        # task283: relay→daemon sync RPC for detail_mode get/set. We're the
        # initiator here, so the pending map lives on this side (mirror of
        # daemon's _peer_pending for task213). Replies arrive via
        # detail_mode_{get,set}_result on the same WS connection — see the
        # dispatch around the peer_resolve_target_result branch.
        self._detail_mode_pending_lock = threading.Lock()
        self._detail_mode_pending = {}  # request_id → {"event": Event, "result": dict}
        # task373: no_collapse_mode shares the daemon-local truth-source shape
        # with detail_mode because the hook process that splits messages runs
        # on the daemon machine.
        self._no_collapse_mode_pending_lock = threading.Lock()
        self._no_collapse_mode_pending = {}
        self._helper_action_pending_lock = threading.Lock()
        self._helper_action_pending = {}

    def start(self):
        thread = threading.Thread(target=self._run, daemon=True)
        thread.start()
        log.info(f"Relay WS server starting on ws://{self.host}:{self.port}")

    def _run(self):
        import websockets

        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self.registry.set_loop(self._loop)

        async def handler(ws):
            machine_id = None
            authenticated = False
            log.info(f"[RELAY_WS] New connection from {ws.remote_address}")

            try:
                async for raw in ws:
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    msg_type = msg.get("type")
                    _relay_metrics.record(f"ws:in:{msg_type or 'unknown'}")

                    # First frame must be auth
                    if not authenticated:
                        if msg_type != "auth":
                            await ws.send(json.dumps({
                                "type": "auth_result", "ok": False,
                                "error": "first frame must be auth"}))
                            await ws.close()
                            return
                        token = msg.get("token", "")
                        machine_id = msg.get("machine_id", "")
                        if not machine_id or not hmac.compare_digest(token, self.relay_token):
                            await ws.send(json.dumps({
                                "type": "auth_result", "ok": False,
                                "error": "invalid token or machine_id"}))
                            await ws.close()
                            return
                        authenticated = True
                        owner_mobile = msg.get("owner_mobile", "")
                        owner_open_id = msg.get("owner_open_id", "")
                        if owner_mobile and not owner_open_id:
                            loop = asyncio.get_event_loop()
                            oid, _ = await loop.run_in_executor(None, self.api.mobile_to_open_id, owner_mobile)
                            owner_open_id = oid or ""
                        self.registry.add_connection(
                            machine_id, ws,
                            owner_mobile=owner_mobile,
                            owner_open_id=owner_open_id,
                            ip=msg.get("ip", ""),
                            ssh_port=msg.get("ssh_port", 22),
                            daemon_hash=msg.get("script_hash", ""),
                            extension_version=msg.get("extension_version", ""),
                            hooks_version=msg.get("hooks_version", ""),
                            cli_versions=msg.get("cli_versions") or {},
                            capabilities=msg.get("capabilities") or [],
                            workspaces=msg.get("workspaces") or [],
                        )
                        await ws.send(json.dumps({"type": "auth_result", "ok": True}))
                        log.info(f"[RELAY_WS] Machine '{machine_id}' authenticated")
                        continue

                    # Authenticated messages
                    if msg_type == "register_interns":
                        interns = msg.get("interns", [])
                        self.registry.register_interns(machine_id, interns)

                    elif msg_type == "unregister_interns":
                        names = msg.get("interns", [])
                        self.registry.unregister_interns(machine_id, names)

                    elif msg_type == "intern_online":
                        intern_name = msg.get("intern_name", "")
                        if intern_name:
                            online_project = msg.get("project")
                            if not online_project:
                                log.error(f"[RELAY_WS] intern_online from '{machine_id}' missing project for "
                                          f"intern={intern_name!r}; msg={msg!r}")
                                await ws.send(json.dumps({
                                    "type": "intern_online_rejected",
                                    "intern_name": intern_name,
                                    "error": "project required",
                                }))
                                continue
                            ok, offlined_copilot = self.registry.set_online(
                                intern_name, machine_id,
                                chat_id=msg.get("chat_id"),
                                intern_type=msg.get("intern_type"),
                                project=online_project)
                            if ok:
                                log.info(f"[RELAY_WS] Intern '{intern_name}' online on '{machine_id}'")
                                self._cancel_pending_red(intern_name, online_project, machine_id)
                                threading.Thread(
                                    target=_update_group_light,
                                    args=(self.api, self.registry, intern_name, True, online_project, machine_id),
                                    daemon=True).start()
                                # If another Copilot was auto-offlined, update its light too
                                if offlined_copilot:
                                    oc_name, oc_project = offlined_copilot
                                    threading.Thread(
                                        target=_update_group_light,
                                        args=(self.api, self.registry, oc_name, False, oc_project, machine_id),
                                        daemon=True).start()
                            else:
                                log.warning(f"[RELAY_WS] set_online failed for '{intern_name}'")
                                await ws.send(json.dumps({
                                    "type": "intern_online_rejected",
                                    "intern_name": intern_name,
                                    "error": "set_online failed",
                                }))

                    elif msg_type == "intern_offline":
                        intern_name = msg.get("intern_name", "")
                        if intern_name:
                            offline_project = msg.get("project")
                            if not offline_project:
                                log.error(f"[RELAY_WS] intern_offline from '{machine_id}' missing project for "
                                          f"intern={intern_name!r}; msg={msg!r}")
                                continue
                            removed = self.registry.set_offline(intern_name, machine_id, project=offline_project)
                            if removed:
                                log.info(f"[RELAY_WS] Intern '{intern_name}' offline on '{machine_id}'")
                                threading.Thread(
                                    target=_update_group_light,
                                    args=(self.api, self.registry, intern_name, False, offline_project, machine_id),
                                    daemon=True).start()

                    elif msg_type == "sync_online":
                        # Daemon sends full online set — relay computes diff.
                        # Optional meta fields: resources, interns_dynamic, warnings, metrics.
                        online_interns = msg.get("online_interns", [])
                        self._handle_sync_online(machine_id, online_interns)
                        if (
                            "resources" in msg
                            or "interns_dynamic" in msg
                            or "warnings" in msg
                            or "metrics" in msg
                        ):
                            self.registry.update_machine_meta(
                                machine_id,
                                resources=msg.get("resources"),
                                interns_dynamic=msg.get("interns_dynamic"),
                                warnings=msg.get("warnings") if "warnings" in msg else None,
                                metrics=msg.get("metrics") if "metrics" in msg else None,
                            )

                    elif msg_type == "interns_state":
                        # Periodic (5s) state snapshot — memory-only update, no feishu API path.
                        # Same payload shape as sync_online; light control stays on sync_online.
                        self.registry.update_machine_meta(
                            machine_id,
                            resources=msg.get("resources"),
                            interns_dynamic=msg.get("interns_dynamic"),
                            warnings=msg.get("warnings") if "warnings" in msg else None,
                            metrics=msg.get("metrics") if "metrics" in msg else None,
                        )

                    elif msg_type == "meta_update":
                        # Daemon pushes fresh extension/hooks version (pulled from plugin).
                        self.registry.update_machine_static_meta(
                            machine_id,
                            extension_version=msg.get("extension_version"),
                            hooks_version=msg.get("hooks_version"),
                        )
                        log.info(f"[RELAY_WS] meta_update from '{machine_id}': "
                                 f"ext={msg.get('extension_version', '')}, "
                                 f"hooks={msg.get('hooks_version', '')}")

                    elif msg_type == "check_online":
                        intern_name = msg.get("intern_name", "")
                        online, on_machine = self.registry.is_online(intern_name, project=msg.get("project"))
                        await ws.send(json.dumps({
                            "type": "check_online_result",
                            "intern_name": intern_name,
                            "online": online,
                            "machine_id": on_machine,
                        }))

                    elif msg_type == "peer_resolve_target":
                        # task213: A daemon asks relay to find all (project, name) candidates
                        request_id = msg.get("request_id", "")
                        to_name = msg.get("to_intern_name", "")
                        candidates = self.registry.find_candidates_by_name(to_name)
                        await ws.send(json.dumps({
                            "type": "peer_resolve_target_result",
                            "request_id": request_id,
                            "to_intern_name": to_name,
                            "candidates": candidates,
                        }))

                    elif msg_type == "intern_peer_message":
                        # task213: forward peer text/attachments to target machine.
                        # Stamps sender_machine_id so target's reply can be routed back.
                        request_id = msg.get("request_id", "")
                        if "mode" not in msg:
                            await ws.send(json.dumps({
                                "type": "intern_peer_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "source_outdated",
                                "required_field": "mode",
                                "message": "source daemon is too old: peer send requests must include mode",
                            }))
                            continue
                        if "from_role" not in msg:
                            await ws.send(json.dumps({
                                "type": "intern_peer_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "source_outdated",
                                "required_field": "from_role",
                                "message": "source daemon is too old: peer send requests must include role contract fields",
                            }))
                            continue
                        to_name = msg.get("to_intern_name", "")
                        to_project = msg.get("to_project", "")
                        entry = (self.registry.get_entry(to_name, project=to_project)
                                 if to_name and to_project else {})
                        target_mid = entry.get("machine_id") if entry else ""
                        if not target_mid:
                            await ws.send(json.dumps({
                                "type": "intern_peer_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "unknown_target",
                            }))
                            continue
                        target_ws = self.registry.get_connection(target_mid)
                        if not target_ws:
                            await ws.send(json.dumps({
                                "type": "intern_peer_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "offline",
                            }))
                            continue
                        # task261: target daemon 未上报 "peer" capability → 旧版本不识别
                        # intern_peer_message msg_type 会静默 drop，前置 gate 防止 A 端
                        # 10s 超时后误报 relay_unreachable。A daemon 收到 target_outdated
                        # 后会给 A 飞书群发 systemMessage 提示主管升级 B 所在机器插件。
                        if not self.registry.has_capability(target_mid, "peer"):
                            await ws.send(json.dumps({
                                "type": "intern_peer_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "target_outdated",
                                "required_capability": "peer",
                            }))
                            continue
                        mode = msg.get("mode") or "default"
                        if mode != "default" and not self.registry.has_capability(target_mid, "peer_modes"):
                            await ws.send(json.dumps({
                                "type": "intern_peer_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "target_outdated",
                                "required_capability": "peer_modes",
                                "message": "target daemon is too old to receive peer send modes",
                            }))
                            continue
                        if not self.registry.has_capability(target_mid, "team_contract"):
                            await ws.send(json.dumps({
                                "type": "intern_peer_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "target_outdated",
                                "required_capability": "team_contract",
                                "message": "target daemon is too old to enforce team communication contract",
                            }))
                            continue
                        forward = dict(msg)
                        forward["sender_machine_id"] = machine_id
                        try:
                            await target_ws.send(json.dumps(forward))
                        except Exception as e:
                            log.warning(f"[RELAY_WS] peer forward to '{target_mid}' failed: {e}")
                            await ws.send(json.dumps({
                                "type": "intern_peer_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "offline",
                            }))

                    elif msg_type == "intern_peer_message_result":
                        # task213: B daemon → relay → forward back to A daemon
                        sender_mid = msg.get("sender_machine_id", "")
                        if sender_mid:
                            sender_ws = self.registry.get_connection(sender_mid)
                            if sender_ws:
                                relay_payload = {k: v for k, v in msg.items()
                                                  if k != "sender_machine_id"}
                                try:
                                    await sender_ws.send(json.dumps(relay_payload))
                                except Exception as e:
                                    log.warning(f"[RELAY_WS] peer result to '{sender_mid}' failed: {e}")

                    elif msg_type == "intern_goal_command":
                        # task320: forward goal set/cancel to target machine.
                        request_id = msg.get("request_id", "")
                        if "from_role" not in msg:
                            await ws.send(json.dumps({
                                "type": "intern_goal_command_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "source_outdated",
                                "required_field": "from_role",
                                "message": "source daemon is too old: goal requests must include role contract fields",
                            }))
                            continue
                        to_name = msg.get("to_intern_name", "")
                        to_project = msg.get("to_project", "")
                        entry = (self.registry.get_entry(to_name, project=to_project)
                                 if to_name and to_project else {})
                        target_mid = entry.get("machine_id") if entry else ""
                        if not target_mid:
                            await ws.send(json.dumps({
                                "type": "intern_goal_command_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "unknown_target",
                            }))
                            continue
                        target_ws = self.registry.get_connection(target_mid)
                        if not target_ws:
                            await ws.send(json.dumps({
                                "type": "intern_goal_command_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "offline",
                            }))
                            continue
                        if not self.registry.has_capability(target_mid, "goal_api"):
                            await ws.send(json.dumps({
                                "type": "intern_goal_command_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "target_outdated",
                                "required_capability": "goal_api",
                                "message": "target daemon is too old to receive goal API commands",
                            }))
                            continue
                        if not self.registry.has_capability(target_mid, "team_contract"):
                            await ws.send(json.dumps({
                                "type": "intern_goal_command_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "target_outdated",
                                "required_capability": "team_contract",
                                "message": "target daemon is too old to enforce team communication contract",
                            }))
                            continue
                        forward = dict(msg)
                        forward["sender_machine_id"] = machine_id
                        try:
                            await target_ws.send(json.dumps(forward))
                        except Exception as e:
                            log.warning(f"[RELAY_WS] goal command forward to '{target_mid}' failed: {e}")
                            await ws.send(json.dumps({
                                "type": "intern_goal_command_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "offline",
                            }))

                    elif msg_type == "intern_goal_command_result":
                        # task320: B daemon → relay → forward goal receipt back to A daemon.
                        sender_mid = msg.get("sender_machine_id", "")
                        if sender_mid:
                            sender_ws = self.registry.get_connection(sender_mid)
                            if sender_ws:
                                relay_payload = {k: v for k, v in msg.items()
                                                  if k != "sender_machine_id"}
                                try:
                                    await sender_ws.send(json.dumps(relay_payload))
                                except Exception as e:
                                    log.warning(f"[RELAY_WS] goal result to '{sender_mid}' failed: {e}")

                    elif msg_type == "intern_mail_message":
                        # task309: forward mail-to message to target daemon, which writes target intern mailbox.
                        request_id = msg.get("request_id", "")
                        to_name = msg.get("to_intern_name", "")
                        to_project = msg.get("to_project", "")
                        entry = (self.registry.get_entry(to_name, project=to_project)
                                 if to_name and to_project else {})
                        target_mid = entry.get("machine_id") if entry else ""
                        if not target_mid:
                            await ws.send(json.dumps({
                                "type": "intern_mail_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "unknown_target",
                            }))
                            continue
                        target_ws = self.registry.get_connection(target_mid)
                        if not target_ws:
                            await ws.send(json.dumps({
                                "type": "intern_mail_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "offline",
                            }))
                            continue
                        if not self.registry.has_capability(target_mid, "mailbox"):
                            await ws.send(json.dumps({
                                "type": "intern_mail_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "target_outdated",
                                "required_capability": "mailbox",
                                "message": "target daemon is too old to receive mail-to messages",
                            }))
                            continue
                        forward = dict(msg)
                        forward["sender_machine_id"] = machine_id
                        try:
                            await target_ws.send(json.dumps(forward))
                        except Exception as e:
                            log.warning(f"[RELAY_WS] mail message forward to '{target_mid}' failed: {e}")
                            await ws.send(json.dumps({
                                "type": "intern_mail_message_result",
                                "request_id": request_id,
                                "status": "undeliverable",
                                "reason": "offline",
                            }))

                    elif msg_type == "intern_mail_message_result":
                        # task309: target daemon → relay → source daemon mailbox write receipt.
                        sender_mid = msg.get("sender_machine_id", "")
                        if sender_mid:
                            sender_ws = self.registry.get_connection(sender_mid)
                            if sender_ws:
                                relay_payload = {k: v for k, v in msg.items()
                                                  if k != "sender_machine_id"}
                                try:
                                    await sender_ws.send(json.dumps(relay_payload))
                                except Exception as e:
                                    log.warning(f"[RELAY_WS] mail result to '{sender_mid}' failed: {e}")

                    elif msg_type in ("detail_mode_get_result", "detail_mode_set_result"):
                        # task283: daemon's reply to relay's detail_mode RPC.
                        # We're the initiator; route the result into the pending
                        # map so detail_mode_request can return synchronously.
                        request_id = msg.get("request_id", "")
                        with self._detail_mode_pending_lock:
                            entry = self._detail_mode_pending.get(request_id)
                        if entry:
                            entry["result"].update(
                                {k: v for k, v in msg.items() if k != "type"})
                            entry["event"].set()
                        else:
                            # Late reply after timeout — log so we notice slow
                            # daemons but don't try to recover (caller already
                            # surfaced "timeout" to the supervisor).
                            log.debug(f"[DETAIL] late {msg_type} for request_id={request_id} "
                                      f"from machine={machine_id}")

                    elif msg_type in ("no_collapse_mode_get_result", "no_collapse_mode_set_result"):
                        # task373: daemon's reply to relay's no_collapse_mode RPC.
                        request_id = msg.get("request_id", "")
                        with self._no_collapse_mode_pending_lock:
                            entry = self._no_collapse_mode_pending.get(request_id)
                        if entry:
                            entry["result"].update(
                                {k: v for k, v in msg.items() if k != "type"})
                            entry["event"].set()
                        else:
                            log.debug(f"[NO_COLLAPSE] late {msg_type} for request_id={request_id} "
                                      f"from machine={machine_id}")

                    elif msg_type == "helper_action_result":
                        request_id = msg.get("request_id", "")
                        with self._helper_action_pending_lock:
                            pending = self._helper_action_pending.get(request_id)
                        if pending:
                            pending["result"].update(
                                {k: v for k, v in msg.items() if k != "type"})
                            pending["event"].set()
                        entry, action, ok, status = _apply_helper_action_result_to_registry(
                            self.registry, msg, fallback_machine_id=machine_id)
                        chat_id = msg.get("reply_chat_id") or entry.get("chat_id", "")
                        if chat_id and self.api and not msg.get("silent_result"):
                            if action == "upgrade_client" and ok:
                                upgrade = msg.get("upgrade") if isinstance(msg.get("upgrade"), dict) else {}
                                daemon_effect = ((upgrade.get("runtime_effects") or {}).get("daemon") or {})
                                restart_text = (
                                    "daemon restart 已调度"
                                    if daemon_effect.get("restart_scheduled")
                                    else "daemon 未重启"
                                )
                                text = (
                                    f"客户端升级完成：`{upgrade.get('current_version') or '-'}"
                                    f"` → `{upgrade.get('latest_version') or msg.get('latest_version') or '-'}`\n"
                                    f"{upgrade.get('message') or ''}\n"
                                    f"{restart_text}；relay 升级仍由管理员手动触发。"
                                )
                            elif action == "upgrade_client" and not ok:
                                text = f"客户端升级失败：{msg.get('error', '')}"
                            elif action == "upgrade_check" and ok:
                                upgrade = msg.get("upgrade") if isinstance(msg.get("upgrade"), dict) else {}
                                text = upgrade.get("message") or f"客户端升级检查完成：`{status}`"
                            elif action == "upgrade_check" and not ok:
                                text = f"客户端升级检查失败：{msg.get('error', '')}"
                            else:
                                text = (
                                    f"helper `{action}` 成功：`{status}`"
                                    if ok else
                                    f"helper `{action}` 失败：{msg.get('error', '')}"
                                )
                            threading.Thread(
                                target=lambda cid=chat_id, body=text: self.api.send_message(cid, body),
                                daemon=True).start()

                    elif msg_type == "heartbeat":
                        await ws.send(json.dumps({
                            "type": "heartbeat_ack",
                            "machine_known": self.registry.is_current_connection(machine_id, ws),
                        }))

            except Exception as e:
                log.warning(f"[RELAY_WS] Connection error for machine '{machine_id}': {e}")
            finally:
                if machine_id:
                    went_offline = self.registry.remove_connection(machine_id, ws=ws)
                    # Update lights for interns that went offline (list of entry dicts)
                    for info in went_offline:
                        self._schedule_pending_red(info, machine_id, reason="ws_disconnect")

        async def serve():
            self._server = await websockets.serve(
                handler,
                self.host,
                self.port,
                max_size=RELAY_WS_MAX_SIZE_BYTES,
            )
            log.info(f"Relay WS server listening on ws://{self.host}:{self.port}")
            await self._server.wait_closed()

        self._loop.run_until_complete(serve())

    def _handle_sync_online(self, machine_id, online_interns):
        """Handle sync_online: daemon sends full online set, relay computes diff.

        online_interns must be a list of dicts:
        [{"name": "...", "chat_id": "...", "type": "...", "project": "..."}, ...].
        """
        normalized = []
        for item in online_interns:
            if not isinstance(item, dict):
                log.error(f"[SYNC_ONLINE] machine={machine_id} item must be dict with name+project, "
                          f"skipping. item={item!r}")
                continue
            normalized.append(item)

        # Build set of composite keys reported online (for diff)
        # Missing project entries do not participate in the online set and are
        # logged again in the main loop below.
        reported_keys = set()
        for item in normalized:
            project = item.get("project")
            if not project:
                log.error(f"[SYNC_ONLINE] reported_keys: machine={machine_id} item missing project, "
                          f"skipping. item={item!r}")
                continue
            reported_keys.add(_make_composite_key(item["name"], project))

        # Snapshot current online state (composite_key → machine_id)
        current_online = self.registry.get_all_online()

        # 1. Process interns going online (or staying online)
        for item in normalized:
            intern_name = item["name"]
            chat_id = item.get("chat_id")
            intern_type = item.get("type")
            project = item.get("project")
            if not project:
                log.error(f"[SYNC_ONLINE] online-loop: machine={machine_id} intern={intern_name!r} "
                          f"missing project, skipping. item={item!r}")
                continue
            ckey = _make_composite_key(intern_name, project)
            existing_machine = current_online.get(ckey)
            if existing_machine == machine_id:
                self._cancel_pending_red(intern_name, project, machine_id)
                # Already online here. Still refresh type if it changed (e.g. copilot → codex)
                # so the group name badge stays accurate without forcing offline/online cycles.
                if intern_type:
                    entry = self.registry.get_entry(intern_name, project=project)
                    if entry and entry.get("type") != intern_type:
                        self.registry.update_chat_id(
                            intern_name, entry.get("chat_id") or chat_id,
                            intern_type=intern_type, project=project)
                        threading.Thread(
                            target=_update_group_light,
                            args=(self.api, self.registry, intern_name, True, project, machine_id),
                            daemon=True).start()
                continue
            if existing_machine and existing_machine != machine_id:
                # Cross-machine conflict: warn via feishu, then switch
                entry = self.registry.get_entry(intern_name, project=project)
                conflict_chat = entry.get("chat_id") if entry else chat_id
                if conflict_chat and self.api:
                    threading.Thread(
                        target=lambda cid=conflict_chat, n=intern_name, old=existing_machine, new=machine_id: (
                            self.api.send_message(cid,
                                f"⚠️ {n} 从机器 {old} 切换到 {new}")
                        ),
                        daemon=True).start()
                log.warning(f"[SYNC_ONLINE] '{intern_name}' (project={project}) conflict: {existing_machine} → {machine_id}")
            ok, offlined_copilot = self.registry.set_online(
                intern_name, machine_id,
                chat_id=chat_id, intern_type=intern_type, project=project)
            if ok:
                self._cancel_pending_red(intern_name, project, machine_id)
                threading.Thread(
                    target=_update_group_light,
                    args=(self.api, self.registry, intern_name, True, project, machine_id),
                    daemon=True).start()
                if offlined_copilot:
                    oc_name, oc_project = offlined_copilot
                    threading.Thread(
                        target=_update_group_light,
                        args=(self.api, self.registry, oc_name, False, oc_project, machine_id),
                        daemon=True).start()

        # 2. Interns currently online on this machine but NOT in the new set → offline
        for ckey, mid in current_online.items():
            if mid == machine_id and ckey not in reported_keys:
                project, intern_name = _split_composite_key(ckey)
                removed = self.registry.set_offline(intern_name, machine_id, project=project)
                if removed:
                    log.info(f"[SYNC_ONLINE] '{intern_name}' (project={project}) went offline on '{machine_id}'")
                    threading.Thread(
                        target=_update_group_light,
                        args=(self.api, self.registry, intern_name, False, project, machine_id),
                        daemon=True).start()

        log.info(f"[SYNC_ONLINE] Machine '{machine_id}' sync: reported={online_interns}")

    def _schedule_pending_red(self, info, machine_id, reason):
        intern_name = info.get("name")
        project = info.get("project")
        chat_id = info.get("chat_id")
        intern_type = info.get("type", "copilot")
        if not intern_name or not project:
            log.error(f"[LIGHT] pending RED rejected: missing name/project (machine={machine_id} info={info!r})")
            return
        if not chat_id:
            log.error(f"[LIGHT] pending RED rejected: missing chat_id {intern_name} (project={project} machine={machine_id})")
            return

        ckey = _make_composite_key(intern_name, project)
        generation = object()
        timer = threading.Timer(RECONNECT_GRACE_SECONDS, self._fire_pending_red, args=(ckey, generation))
        timer.daemon = True
        pending = {
            "timer": timer,
            "generation": generation,
            "intern_name": intern_name,
            "project": project,
            "chat_id": chat_id,
            "intern_type": intern_type,
            "machine_id": machine_id,
            "reason": reason,
        }

        with self._pending_red_lock:
            old = self._pending_red.get(ckey)
            if old:
                old["timer"].cancel()
                log.info(f"[LIGHT] ⏳ replace pending RED {intern_name} (project={project} was machine={old.get('machine_id')}, now {machine_id})")
            self._pending_red[ckey] = pending

        log.info(f"[LIGHT] ⏳ pending RED {intern_name} in {RECONNECT_GRACE_SECONDS}s (project={project} machine={machine_id} reason={reason})")
        timer.start()

    def _cancel_pending_red(self, intern_name, project, machine_id):
        ckey = _make_composite_key(intern_name, project)
        with self._pending_red_lock:
            pending = self._pending_red.pop(ckey, None)
        if pending:
            pending["timer"].cancel()
            log.info(f"[LIGHT] ✋ cancel pending RED {intern_name} (project={project} came back online on {machine_id})")

    def _fire_pending_red(self, ckey, generation):
        with self._pending_red_lock:
            pending = self._pending_red.get(ckey)
            if not pending or pending.get("generation") is not generation:
                return
            self._pending_red.pop(ckey, None)

        intern_name = pending["intern_name"]
        project = pending["project"]
        machine_id = pending["machine_id"]
        online, online_machine = self.registry.is_online(intern_name, project=project)
        if online:
            log.info(f"[LIGHT] ⏭ skip RED {intern_name} (project={project} already online on {online_machine})")
            return

        log.info(f"[LIGHT] 🔴 {intern_name} (project={project} after grace, last machine={machine_id})")
        _update_group_light_for_chat(
            self.api,
            pending["chat_id"],
            intern_name,
            pending.get("intern_type", "copilot"),
            False,
            project,
            machine_id=machine_id,
            registry=self.registry,
        )

    def cancel_pending_red_on_shutdown(self):
        with self._pending_red_lock:
            pending_items = list(self._pending_red.values())
            self._pending_red.clear()
        for pending in pending_items:
            pending["timer"].cancel()
            log.info(f"[LIGHT] 🧹 cancel pending RED {pending['intern_name']} on shutdown (project={pending['project']} machine={pending['machine_id']})")

    def send_to_machine(self, machine_id, data):
        """Send a message to a specific machine's Local Agent. Returns True if sent."""
        sent, _reason = self.send_to_machine_result(machine_id, data)
        return sent

    def send_to_machine_result(self, machine_id, data, payload_bytes=None):
        """Send to Local Agent. Returns (sent, failure_reason)."""
        msg_type = data.get("type", "unknown") if isinstance(data, dict) else "unknown"
        metric_key = f"ws:out:{msg_type}"
        started = time.time()
        if payload_bytes is None:
            payload_bytes = _json_payload_size_bytes(data)
        if payload_bytes > RELAY_WS_MAX_SIZE_BYTES:
            _relay_metrics.record(metric_key, error=True)
            return False, "payload_too_large"

        ws = self.registry.get_connection(machine_id)
        if not ws or not self._loop:
            _relay_metrics.record(metric_key, error=True)
            if not ws:
                return False, "target_machine_not_connected"
            return False, "relay_loop_not_running"

        msg = json.dumps(data, ensure_ascii=False)

        async def _send():
            try:
                await ws.send(msg)
                return True, ""
            except Exception as e:
                log.warning(f"[RELAY_WS] Failed to send to '{machine_id}': {e}")
                return False, f"ws_send_failed: {e}"

        future = asyncio.run_coroutine_threadsafe(_send(), self._loop)
        try:
            sent, reason = future.result(timeout=5)
            _relay_metrics.record(
                metric_key,
                elapsed_ms=int((time.time() - started) * 1000),
                error=not sent,
            )
            return sent, reason
        except Exception as e:
            _relay_metrics.record(
                metric_key,
                elapsed_ms=int((time.time() - started) * 1000),
                error=True,
            )
            return False, f"ws_send_timeout_or_error: {e}"

    def helper_action_request(self, machine_id, action, timeout=20, **extra):
        """Synchronous relay→daemon helper action RPC used by helper slash UX."""
        if not machine_id:
            return None, "machine_id_required"
        if not self.registry.get_connection(machine_id):
            return None, "daemon_offline"
        if not self.registry.has_capability(machine_id, "machine_helper"):
            return None, "daemon_outdated"
        request_id = extra.get("request_id") or _uuid.uuid4().hex
        event = threading.Event()
        holder = {}
        with self._helper_action_pending_lock:
            self._helper_action_pending[request_id] = {"event": event, "result": holder}
        try:
            payload = {
                "type": "helper_action",
                "helper_action": action,
                "machine_id": machine_id,
                "request_id": request_id,
                **extra,
            }
            payload["request_id"] = request_id
            if not self.send_to_machine(machine_id, payload):
                return None, "send_failed"
            if not event.wait(timeout=timeout):
                return None, "timeout"
            if holder.get("ok") is False:
                return None, holder.get("error") or "helper_action_failed"
            return holder, None
        finally:
            with self._helper_action_pending_lock:
                self._helper_action_pending.pop(request_id, None)

    def detail_mode_request(self, chat_id, op, mode=None, timeout=10):
        """task283: synchronous relay→daemon RPC for per-chat detail_mode.

        Resolves chat_id → owning daemon via registry, sends a `detail_mode_get`
        or `detail_mode_set` WS message with a fresh request_id, then blocks
        (with timeout) until the matching `detail_mode_{op}_result` lands and
        wakes the pending Event.

        Returns (result_dict, error_str). On success error_str is None and
        result_dict has at least `mode`; for `set` it also has `changed`. On
        failure result_dict is None and error_str is one of:
          - "unknown_chat"      — chat_id not in relay registry
          - "daemon_offline"    — owning daemon has no live WS connection
          - "daemon_outdated"   — daemon hasn't advertised "detail_mode" cap
          - "send_failed"       — WS send raised; daemon may be mid-disconnect
          - "timeout"           — no reply within `timeout` seconds
          - "<server-side msg>" — daemon returned a structured error string
        """
        if op not in ("get", "set"):
            raise ValueError(f"detail_mode op must be 'get' or 'set', got {op!r}")
        if op == "set" and not mode:
            raise ValueError("detail_mode_request(op='set') requires mode")

        entry = self.registry.find_entry_by_chat(chat_id)
        if not entry:
            return None, "unknown_chat"
        machine_id = entry.get("machine_id", "")
        if not machine_id or not self.registry.get_connection(machine_id):
            return None, "daemon_offline"
        # task283: explicit capability gate — gives the caller a clean error
        # string ("daemon needs upgrade") instead of a 10s timeout when the
        # owning daemon hasn't been redeployed with task283 yet.
        if not self.registry.has_capability(machine_id, "detail_mode"):
            return None, "daemon_outdated"

        request_id = _uuid.uuid4().hex
        event = threading.Event()
        holder = {}
        with self._detail_mode_pending_lock:
            self._detail_mode_pending[request_id] = {"event": event, "result": holder}
        try:
            payload = {
                "type": f"detail_mode_{op}",
                "request_id": request_id,
                "chat_id": chat_id,
            }
            if op == "set":
                payload["mode"] = mode
            if not self.send_to_machine(machine_id, payload):
                return None, "send_failed"
            if not event.wait(timeout=timeout):
                return None, "timeout"
            if holder.get("error"):
                return None, holder["error"]
            return holder, None
        finally:
            with self._detail_mode_pending_lock:
                self._detail_mode_pending.pop(request_id, None)

    def no_collapse_mode_request(self, chat_id, op, mode=None, timeout=10):
        """task373: synchronous relay→daemon RPC for per-chat no_collapse_mode.

        The hook reads no_collapse_mode on the daemon machine, so /config must
        proxy reads/writes through the owning daemon instead of writing relay
        local state.
        """
        if op not in ("get", "set"):
            raise ValueError(f"no_collapse_mode op must be 'get' or 'set', got {op!r}")
        if op == "set" and not mode:
            raise ValueError("no_collapse_mode_request(op='set') requires mode")

        entry = self.registry.find_entry_by_chat(chat_id)
        if not entry:
            return None, "unknown_chat"
        machine_id = entry.get("machine_id", "")
        if not machine_id or not self.registry.get_connection(machine_id):
            return None, "daemon_offline"
        if not self.registry.has_capability(machine_id, "no_collapse_mode"):
            return None, "daemon_outdated"

        request_id = _uuid.uuid4().hex
        event = threading.Event()
        holder = {}
        with self._no_collapse_mode_pending_lock:
            self._no_collapse_mode_pending[request_id] = {
                "event": event, "result": holder}
        try:
            payload = {
                "type": f"no_collapse_mode_{op}",
                "request_id": request_id,
                "chat_id": chat_id,
            }
            if op == "set":
                payload["mode"] = mode
            if not self.send_to_machine(machine_id, payload):
                return None, "send_failed"
            if not event.wait(timeout=timeout):
                return None, "timeout"
            if holder.get("error"):
                return None, holder["error"]
            return holder, None
        finally:
            with self._no_collapse_mode_pending_lock:
                self._no_collapse_mode_pending.pop(request_id, None)


# ══════════════════════════════════════════
# 飞书入站消息处理 + 路由
# ══════════════════════════════════════════

_feishu_msg_count = 0
_feishu_last_msg_time = 0
_feishu_im_message_count = 0
_feishu_im_message_last_time = 0
_feishu_card_action_count = 0
_feishu_card_action_last_time = 0


# task249: 飞书 open platform 对未明确 ack 的事件按 20s / 5min / 1h 等节奏重投，
# 同一 event_id 在 24h 内唯一。应用层必须按 event_id 自行幂等，否则同一条消息
# 会被多次注入到 intern（已观测：单条消息 + 截图被打 4 次到 tmux）。
_SEEN_EVENT_IDS_TTL_SEC = 24 * 60 * 60
_SEEN_EVENT_IDS_MAX = 10000
_seen_event_ids: "OrderedDict[str, float]" = OrderedDict()
_seen_event_ids_lock = threading.Lock()


def _evict_expired_event_ids_locked(now):
    cutoff = now - _SEEN_EVENT_IDS_TTL_SEC
    while _seen_event_ids:
        oldest_id, oldest_ts = next(iter(_seen_event_ids.items()))
        if oldest_ts >= cutoff:
            break
        _seen_event_ids.popitem(last=False)


def _check_and_record_event(event_id, event_type):
    """命中重复 event_id → True（调用方应 early return）；首次 → False。

    event_id 为空 → False（无法 dedup，pass through，并打 warn 因为 P2 事件不该缺）。
    """
    if not event_id:
        log.warning(f"[DEDUP] missing event_id type={event_type}, skipping dedup")
        return False
    now = time.time()
    with _seen_event_ids_lock:
        _evict_expired_event_ids_locked(now)
        if event_id in _seen_event_ids:
            log.info(f"[DEDUP] drop retry event_id={event_id} type={event_type}")
            return True
        _seen_event_ids[event_id] = now
        while len(_seen_event_ids) > _SEEN_EVENT_IDS_MAX:
            _seen_event_ids.popitem(last=False)
        return False


def _extract_event_id(data):
    header = getattr(data, "header", None)
    if header is None:
        return ""
    return getattr(header, "event_id", "") or ""


def _value_from_obj(obj, *keys):
    if obj is None:
        return ""
    if isinstance(obj, dict):
        for key in keys:
            value = obj.get(key)
            if value:
                return str(value)
        return ""
    for key in keys:
        value = getattr(obj, key, "")
        if value:
            return str(value)
    raw = getattr(obj, "__dict__", None)
    if isinstance(raw, dict):
        for key in keys:
            value = raw.get(key)
            if value:
                return str(value)
    for method in ("to_dict", "model_dump"):
        fn = getattr(obj, method, None)
        if callable(fn):
            try:
                data = fn()
            except Exception:
                data = None
            if isinstance(data, dict):
                for key in keys:
                    value = data.get(key)
                    if value:
                        return str(value)
    return ""


def _extract_user_identifier(identity, _seen=None):
    """Return the best available Feishu user identifier from SDK objects.

    Most runtime policies use open_id, but Feishu SDK objects have not been
    consistent across message and card events. Prefer open_id, then fall back to
    user_id/union_id so we never reject a valid event as an anonymous operator
    just because the SDK nested the id differently.
    """
    if identity is None:
        return ""
    _seen = _seen or set()
    marker = id(identity)
    if marker in _seen:
        return ""
    _seen.add(marker)
    direct = _value_from_obj(identity, "open_id", "user_id", "union_id", "id")
    if direct:
        return direct
    for key in ("sender_id", "operator_id", "user_id", "operator", "user"):
        nested = _value_from_obj(identity, key)
        if nested and not isinstance(nested, str):
            value = _extract_user_identifier(nested, _seen)
            if value:
                return value
        nested_obj = None
        if isinstance(identity, dict):
            nested_obj = identity.get(key)
        elif identity is not None:
            nested_obj = getattr(identity, key, None)
        value = _extract_user_identifier(nested_obj, _seen)
        if value:
            return value
    return ""


def _sender_user_identifier(sender):
    return _extract_user_identifier(sender)


def _operator_user_identifier(event):
    for identity in (
            getattr(event, "operator", None),
            getattr(event, "operator_id", None)):
        value = _extract_user_identifier(identity)
        if value:
            return value
    return ""


def _helper_policy_for_callback(helper_policy):
    return helper_policy() if callable(helper_policy) else (helper_policy or {})


def _helper_policy_action_for_card_action(action_name):
    mapping = {
        "start": "helper_start",
        "stop": "helper_stop",
        "status": "view",
        "select_machine": "view",
        "set_detail_mode": "view",
        "cancel": "view",
        "migrate": "helper_start",
        "delete_group": "helper_delete_group",
        "confirm_delete": "helper_delete_group",
        "invite_owner": "helper_invite_owner",
        "upgrade_client": "helper_upgrade",
        "upgrade_cancel": "view",
    }
    if action_name not in mapping:
        raise ValueError(f"unknown helper_action: {action_name!r}")
    return mapping[action_name]


_RELAY_NATIVE_SLASH_COMMANDS = NATIVE_SLASH_COMMANDS_BY_INTERN_TYPE


def _helper_usage_text():
    return (
        "Machine Helper 用法：\n"
        "- `/helper status [machine_id]` 查看 helper 状态\n"
        "- `/helper start [machine_id] [问题描述]` 启动 helper\n"
        "- `/helper stop [machine_id]` 停止 helper\n"
        "- `/helper invite-owner [machine_id] [上下文]` 邀请 owner 协助\n"
        "- `/helper migrate <ip:port>` 协助迁移到新机器"
    )


def _helper_project_exists(registry, project):
    project = (project or "").strip()
    if not project:
        return True
    if _workspace_registry is not None:
        try:
            if _workspace_registry.get(project):
                return True
        except Exception:
            pass
    try:
        machines = registry.get_machines_summary() or {}
    except Exception:
        machines = {}
    for info in machines.values():
        for workspace in info.get("workspaces") or []:
            if isinstance(workspace, dict):
                if workspace.get("workspace_id") == project or workspace.get("project") == project:
                    return True
            elif str(workspace) == project:
                return True
        for intern in info.get("interns_detail") or []:
            if isinstance(intern, dict) and intern.get("project") == project:
                return True
    try:
        for entry in (registry.get_all_interns_by_key() or {}).values():
            if isinstance(entry, dict) and entry.get("project") == project:
                return True
    except Exception:
        pass
    return False


def _split_helper_project_args(args):
    project = ""
    rest = []
    idx = 0
    while idx < len(args):
        item = args[idx]
        if item == "--project":
            if idx + 1 >= len(args):
                raise ValueError("/helper --project requires a project id")
            project = args[idx + 1]
            idx += 2
            continue
        if item.startswith("--project="):
            project = item.split("=", 1)[1]
            idx += 1
            continue
        rest.append(item)
        idx += 1
    return project, rest


def _parse_helper_command(text):
    parts = (text or "").strip().split()
    if not parts or parts[0] != RELAY_HELPER_COMMAND:
        return None
    project, command_args = _split_helper_project_args(parts[1:])
    if any(item == "--workspace" or item.startswith("--workspace=") or item.startswith("workspace_id=") for item in command_args):
        raise ValueError("/helper is machine-level; workspace selection is not supported")
    if not command_args:
        return {"action": "help", "machine_id": "", "args": [], "project": project}
    action = command_args[0].replace("_", "-")
    args = command_args[1:]
    if action not in {"status", "start", "stop", "invite-owner", "migrate"}:
        raise ValueError(f"unknown {RELAY_HELPER_COMMAND} action")
    machine_id = args[0] if args else ""
    rest = args[1:]
    return {"action": action, "machine_id": machine_id, "args": rest, "project": project}


def _helper_action_to_policy_action(action):
    if action == "invite-owner":
        return "helper_invite_owner"
    if action == "migrate":
        return "helper_start"
    if action == "status":
        return "view"
    return _helper_policy_action_for_card_action(action)


def _helper_action_to_daemon_action(action):
    return "invite_owner" if action == "invite-owner" else action


def _helper_card_action_value(action):
    return "invite_owner" if action == "invite-owner" else action


_HELPER_STARTED_STATUSES = {
    "running",
    "inviting_owner",
    "owner_invited",
    "upgrading_client",
    "migration_prompting",
    "migration_prompt_sent",
}


def _machine_helper_is_started(helper):
    return (helper or {}).get("status") in _HELPER_STARTED_STATUSES


def _machine_helper_status_label(helper):
    status = (helper or {}).get("status") or "stopped"
    if status in _HELPER_STARTED_STATUSES:
        return "已启动"
    if status == "starting":
        return "启动中"
    if status == "stopping":
        return "停止中"
    if status == "failed":
        return "失败"
    return "未启动"


def _machine_helper_cli_ready(info):
    versions = (info or {}).get("cli_versions") or {}
    return bool(versions.get("codex") or versions.get("claude"))


def _machine_helper_preferred_runtime(info, helper=None):
    existing = (helper or {}).get("runtime") or ""
    if existing in {"codex", "claude"}:
        return existing
    versions = (info or {}).get("cli_versions") or {}
    if versions.get("codex"):
        return "codex"
    if versions.get("claude"):
        return "claude"
    return "codex"


def _normalize_helper_detail_mode(value):
    mode = str(value or "").strip().lower().replace("-", "_")
    if mode in ("full", "detail", "detailed", "verbose"):
        return "full"
    if mode in ("summary", "summarized", "quiet", "minimal"):
        return "summary"
    return ""


def _helper_selected_machine_from_value(registry, value, chat_id):
    machine_id = value.get("machine_id") or value.get("selected_machine_id") or ""
    if machine_id:
        return machine_id
    helper = registry.find_helper_by_chat(chat_id) if chat_id else None
    return (helper or {}).get("selected_machine_id") or ""


def _helper_detail_mode_from_value(registry, value, machine_id):
    mode = _normalize_helper_detail_mode(value.get("detail_mode"))
    if mode:
        return mode
    helper = registry.get_machine_helper(machine_id) if machine_id else {}
    return _normalize_helper_detail_mode(helper.get("detail_mode")) or "full"


def _helper_state_value(base_value, action, selected_machine_id, detail_mode):
    value = {
        "helper_action": action,
        "selected_machine_id": selected_machine_id or "",
        "operator_open_id": base_value.get("operator_open_id") or "",
        "message_id": base_value.get("message_id") or "",
        "runtime": base_value.get("runtime") or "codex",
        "detail_mode": detail_mode or "full",
    }
    if selected_machine_id:
        value["helper_id"] = _machine_helper_id_for_machine(selected_machine_id)
    if base_value.get("issue_summary"):
        value["issue_summary"] = base_value.get("issue_summary")
    if base_value.get("chat_id"):
        value["chat_id"] = base_value.get("chat_id")
    return value


def _build_helper_state_card(registry, base_value, selected_machine_id, detail_mode, note=""):
    detail_mode = _normalize_helper_detail_mode(detail_mode) or "full"
    machines = registry.get_machines_summary() or {}
    machine_lines = []
    for mid, info in sorted(machines.items()):
        marker = "✅" if mid == selected_machine_id else "•"
        status = "online" if info.get("ws_connected") else "offline"
        machine_lines.append(f"{marker} `{mid}` ({status})")
    elements = [{
        "tag": "div",
        "text": {"tag": "lark_md", "content": (
            f"selected_machine=`{selected_machine_id or '未选择'}`\n"
            f"detail_mode=`{detail_mode}`"
        )},
    }]
    if note:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": note}})
    if machine_lines:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(machine_lines)}})
    machine_actions = []
    for mid in sorted(machines):
        value = _helper_state_value(base_value, "select_machine", mid, detail_mode)
        value["machine_id"] = mid
        machine_actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": mid},
            "type": "primary" if mid == selected_machine_id else "default",
            "value": value,
        })
    if machine_actions:
        elements.append({"tag": "action", "actions": machine_actions})
    detail_actions = []
    for mode in ("full", "summary"):
        value = _helper_state_value(base_value, "set_detail_mode", selected_machine_id, mode)
        detail_actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": mode},
            "type": "primary" if mode == detail_mode else "default",
            "value": value,
        })
    elements.append({"tag": "action", "actions": detail_actions})
    if selected_machine_id:
        elements.append({"tag": "action", "actions": [
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "启动 helper"},
                "type": "primary",
                "value": _helper_state_value(base_value, "start", selected_machine_id, detail_mode),
            },
            {
                "tag": "button",
                "text": {"tag": "plain_text", "content": "停止 helper"},
                "type": "danger",
                "value": _helper_state_value(base_value, "stop", selected_machine_id, detail_mode),
            },
        ]})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": "blue", "title": {"tag": "plain_text", "content": "Machine Helper"}},
        "elements": elements,
    }


def _helper_status_text(helper):
    if not helper:
        return "helper 尚未创建"
    return (
        f"helper_id: `{helper.get('helper_id', '')}`\n"
        f"machine_id: `{helper.get('machine_id', '')}`\n"
        f"status: `{helper.get('status', '')}`\n"
        f"runtime: `{helper.get('runtime', '')}`"
    )


def _helper_chat_name(machine_id):
    return f"Machine Helper/{machine_id}"


def _ensure_machine_helper_chat(api, registry, machine_id, operator_open_id):
    existing = registry.get_machine_helper(machine_id)
    if existing.get("chat_id"):
        return existing["chat_id"], True, None
    chat_id, err = api.create_chat(
        _helper_chat_name(machine_id),
        f"Machine helper for {machine_id}",
        operator_open_id,
    )
    if err or not chat_id:
        return "", False, err or "create_chat returned no chat_id"
    registry.register_machine_helper(
        machine_id,
        helper_id=_machine_helper_id_for_machine(machine_id),
        runtime=existing.get("runtime") or "codex",
        chat_id=chat_id,
        status=existing.get("status") or "stopped",
        created_by_open_id=operator_open_id,
        last_operator_open_id=operator_open_id,
    )
    registry.append_machine_helper_audit(machine_id, "create_group", operator_open_id, {"chat_id": chat_id})
    api.send_message(chat_id, f"Machine helper group created for `{machine_id}`")
    return chat_id, False, None


def _helper_action_cancel_value(action, sender_open_id, message_id):
    return {
        "helper_action": "cancel",
        "source_helper_action": action,
        "operator_open_id": sender_open_id,
        "message_id": message_id,
        "runtime": "codex",
    }


def _build_helper_action_cancel_card(action):
    action_text = {
        "start": "启动 helper",
        "stop": "停止 helper",
        "invite-owner": "邀请 owner",
        "invite_owner": "邀请 owner",
    }.get(action or "", "helper 操作")
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": "grey", "title": {"tag": "plain_text", "content": "helper 操作已取消"}},
        "elements": [{
            "tag": "div",
            "text": {"tag": "plain_text", "content": f"已取消：{action_text}"},
        }],
    }


def _helper_action_row_text(machine_id, helper, label):
    helper_id = (helper or {}).get("helper_id") or _machine_helper_id_for_machine(machine_id)
    return f"- `{machine_id}` / `{helper_id}`：{label}"


def _build_helper_action_card(registry, action, sender_open_id, message_id, helper_policy=None, issue_summary=""):
    policy = _helper_policy_for_callback(helper_policy)
    machines = registry.get_machines_summary() or {}
    helpers = registry.get_helpers_summary() or {}
    visible = filter_visible_machines_for_helper(machines, sender_open_id, policy)
    if action == "start":
        candidates = {mid: info for mid, info in visible.items() if info.get("ws_connected")}
        title = "启动 helper"
        template = "blue"
        help_text = "选择要启动 helper 的机器。helper 启动后会创建新的 helper 群；已启动的 helper 不会重复启动。"
        button_type = "primary"
    elif action == "stop":
        candidates = {
            mid: visible.get(mid, {})
            for mid in helpers
            if mid in visible
        }
        title = "停止 helper"
        template = "orange"
        help_text = "选择要停止的 helper。只有已启动 helper 可以停止。"
        button_type = "danger"
    elif action == "invite-owner":
        candidates = {
            mid: visible.get(mid, {})
            for mid in helpers
            if mid in visible
        }
        title = "邀请 owner"
        template = "blue"
        help_text = "选择要邀请 app owner 协助排障的 helper。只有已启动 helper 可以邀请 owner。"
        button_type = "primary"
    else:
        return None, f"unknown helper action: {action}"

    elements = [{"tag": "div", "text": {"tag": "lark_md", "content": help_text}}]
    actions = []
    readonly = []
    for mid, info in sorted(candidates.items()):
        helper = helpers.get(mid) or {}
        helper_id = helper.get("helper_id") or _machine_helper_id_for_machine(mid)
        started = _machine_helper_is_started(helper)
        if not info.get("ws_connected"):
            readonly.append(_helper_action_row_text(mid, helper, "机器离线"))
            continue
        if not helper_policy_allows(policy, sender_open_id, mid, _helper_action_to_policy_action(action), info):
            readonly.append(_helper_action_row_text(mid, helper, "无权限"))
            continue
        if action == "start":
            if started:
                readonly.append(_helper_action_row_text(mid, helper, "已启动"))
                continue
            if not _machine_helper_cli_ready(info):
                readonly.append(_helper_action_row_text(mid, helper, "不可启动：Codex/Claude CLI not ready"))
                continue
        elif not started:
            readonly.append(_helper_action_row_text(mid, helper, _machine_helper_status_label(helper)))
            continue
        runtime = _machine_helper_preferred_runtime(info, helper)
        value = {
            "helper_action": _helper_card_action_value(action),
            "machine_id": mid,
            "helper_id": helper_id,
            "operator_open_id": sender_open_id,
            "message_id": message_id,
            "runtime": runtime,
        }
        if issue_summary:
            value["issue_summary"] = issue_summary
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": mid},
            "type": button_type,
            "value": value,
        })
    if actions:
        elements.append({"tag": "action", "actions": actions})
    if readonly:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "状态：\n" + "\n".join(readonly)}})
    if not actions and not readonly:
        elements.append({"tag": "div", "text": {"tag": "plain_text", "content": "当前没有可操作的机器。"}})
    elements.append({"tag": "action", "actions": [{
        "tag": "button",
        "text": {"tag": "plain_text", "content": "✖️ 取消"},
        "type": "default",
        "value": _helper_action_cancel_value(action, sender_open_id, message_id),
    }]})
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {"template": template, "title": {"tag": "plain_text", "content": title}},
        "elements": elements,
    }, ""


def _send_helper_action_card(api, registry, helper_policy, action, chat_id, message_id, sender_open_id, issue_summary=""):
    card, err = _build_helper_action_card(
        registry, action, sender_open_id, message_id, helper_policy=helper_policy, issue_summary=issue_summary)
    if err:
        _reply_message_with_log(api, message_id, f"⚠️ {err}", "HELPER")
        return True
    msg_id, send_err = api.send_interactive_card(chat_id, card)
    if send_err:
        _reply_message_with_log(api, message_id, f"⚠️ helper 选择卡发送失败：{send_err}", "HELPER")
    else:
        log.info(f"[HELPER] {action} card sent chat={chat_id} msg={msg_id}")
    return True


def _machine_helper_owner_open_id(policy, machine_info):
    policy = policy or {}
    return (
        policy.get("app_owner_open_id")
        or (machine_info or {}).get("owner_open_id")
        or ""
    )


def _reply_message_with_log(api, message_id, text, context):
    err = api.reply_message(message_id, text)
    if err:
        log.error(f"[{context}] reply_message failed message_id={message_id}: {err}")
    else:
        log.info(f"[{context}] replied message_id={message_id}")
    return err


_MACHINE_CONFIG_CARD_ACTION = "machine_config_submit"
_MACHINE_CONFIG_OPERATION_SAVE = "save"
_MACHINE_CONFIG_OPERATION_CANCEL = "cancel"
_MACHINE_CONFIG_CANCELED_CARD_LIMIT = 512
_machine_config_canceled_cards = OrderedDict()
_machine_config_canceled_cards_lock = threading.Lock()


def _new_machine_config_card_token():
    return _uuid.uuid4().hex


def _machine_config_card_state_key(value, card_message_id=""):
    if isinstance(value, dict):
        token = str(value.get("machine_config_card_token") or "").strip()
        if token:
            return f"token:{token}"
    card_message_id = str(card_message_id or "").strip()
    if not card_message_id and not isinstance(value, dict):
        card_message_id = str(value or "").strip()
    if not card_message_id:
        return ""
    return f"message:{card_message_id}"


def _mark_machine_config_card_canceled(value, card_message_id=""):
    key = _machine_config_card_state_key(value, card_message_id)
    if not key:
        return
    with _machine_config_canceled_cards_lock:
        _machine_config_canceled_cards.pop(key, None)
        _machine_config_canceled_cards[key] = time.time()
        while len(_machine_config_canceled_cards) > _MACHINE_CONFIG_CANCELED_CARD_LIMIT:
            _machine_config_canceled_cards.popitem(last=False)


def _machine_config_card_is_canceled(value, card_message_id=""):
    key = _machine_config_card_state_key(value, card_message_id)
    if not key:
        return False
    with _machine_config_canceled_cards_lock:
        return key in _machine_config_canceled_cards


def _machine_config_schema(policy=None):
    return enterprise_env_switch_schema(policy)


def _resolve_machine_config_policy_schema(machine_config_schema=None):
    if isinstance(machine_config_schema, dict):
        return {"env_switches": machine_config_schema}, machine_config_schema, ""
    if not _root_dir:
        return {}, _machine_config_schema(), ""
    policy, error = _current_enterprise_policy(_root_dir)
    if error:
        return {}, {}, error
    return policy, _machine_config_schema(policy), ""


def _machine_config_has_fields(schema):
    return any(isinstance(group, dict) and str(group.get("key") or "").strip() for group in schema.get("groups") or [])


def _resolve_machine_config_target(registry, chat_id):
    entry = registry.find_entry_by_chat(chat_id)
    if entry and entry.get("machine_id"):
        return entry.get("machine_id"), entry, ""
    helper_entry = registry.find_helper_by_chat(chat_id)
    if helper_entry and helper_entry.get("machine_id"):
        return helper_entry.get("machine_id"), helper_entry, ""
    machines = registry.get_machines_summary() or {}
    connected = [mid for mid, info in machines.items() if info.get("ws_connected")]
    if len(connected) == 1:
        return connected[0], machines.get(connected[0], {}), ""
    if not connected:
        return "", {}, "当前没有已连接机器，无法打开 /machine_config"
    return "", {}, "当前群无法确定机器，请在具体 intern 群或 helper 群使用 /machine_config"


def _machine_config_field_blocks(schema, selected_state=None):
    selected_state = selected_state if isinstance(selected_state, dict) else {}
    enabled = set(selected_state.get("enabled_groups") or [])
    group_values = selected_state.get("group_values") if isinstance(selected_state.get("group_values"), dict) else {}
    blocks = []
    form_elements = []
    for group in schema.get("groups") or []:
        if not isinstance(group, dict):
            continue
        key = str(group.get("key") or "").strip()
        if not key:
            continue
        title = str(group.get("title") or group.get("label") or key)
        description = str(group.get("description") or "").strip()
        group_blocks = []
        detail_lines = [f"**{title}**"]
        if description:
            detail_lines.append(description)
        flags = []
        if group.get("enable_codex") is True:
            flags.append("Codex")
        if group.get("enable_claude") is True:
            flags.append("Claude")
        if flags:
            detail_lines.append("提供：" + " / ".join(flags))
        group_blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(detail_lines)}})
        form_elements.append({
            "tag": "select_static",
            "name": f"env_switch__{key}",
            "placeholder": {"tag": "plain_text", "content": f"启用 {title}"},
            "initial_option": "enabled" if key in enabled else "disabled",
            "options": [
                {"text": {"tag": "plain_text", "content": "启用"}, "value": "enabled"},
                {"text": {"tag": "plain_text", "content": "停用"}, "value": "disabled"},
            ],
        })
        values = group_values.get(key) if isinstance(group_values.get(key), dict) else {}
        for field in group.get("fields") or []:
            if not isinstance(field, dict):
                continue
            field_key = str(field.get("key") or "").strip()
            if not field_key:
                continue
            label = str(field.get("label") or field.get("title") or field_key)
            field_description = str(field.get("description") or "").strip()
            detail = f"  • {label}"
            if field_description:
                detail += f"：{field_description}"
            group_blocks.append({"tag": "div", "text": {"tag": "lark_md", "content": detail}})
            form_elements.append({
                "tag": "input",
                "name": f"env_switch_field__{key}__{field_key}",
                "placeholder": {"tag": "plain_text", "content": label},
                "default_value": str(values.get(field_key) or field.get("default") or ""),
            })
        blocks.extend(group_blocks)
    return blocks, form_elements


def _build_machine_config_card(machine_id, operator_open_id, operator_name, card_message_id="", schema=None,
                               selected_fields=None, card_token=""):
    schema = schema or _machine_config_schema()
    card_token = str(card_token or "").strip() or _new_machine_config_card_token()
    field_blocks, form_elements = _machine_config_field_blocks(schema, selected_state=selected_fields)
    base_value = {
        "machine_config_action": _MACHINE_CONFIG_CARD_ACTION,
        "machine_id": machine_id,
        "operator_open_id": operator_open_id,
        "operator_name": operator_name,
        "card_message_id": card_message_id,
        "machine_config_card_token": card_token,
        "schema": schema["schema"],
    }
    form_elements.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "保存"},
        "type": "primary",
        "action_type": "form_submit",
        "name": "save",
        "value": {**base_value, "machine_config_operation": _MACHINE_CONFIG_OPERATION_SAVE},
    })
    form_elements.append({
        "tag": "button",
        "text": {"tag": "plain_text", "content": "取消"},
        "type": "default",
        "action_type": "form_submit",
        "name": "cancel",
        "value": {**base_value, "machine_config_operation": _MACHINE_CONFIG_OPERATION_CANCEL},
    })
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "机器配置"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                f"机器：`{machine_id}`\n"
                "配置由 relay schema 生成；保存后由本机 daemon 接管这张卡片并展示进度。"
            )}},
            {"tag": "note", "elements": [{"tag": "plain_text",
                "content": f"仅 {operator_name} 可保存配置"}]},
            {"tag": "hr"},
            *field_blocks,
            {
                "tag": "form",
                "name": "machine_config_form",
                "elements": form_elements,
            },
        ],
    }


def _build_machine_config_cancel_card(machine_id, operator_name):
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "grey",
            "title": {"tag": "plain_text", "content": "机器配置已取消"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                f"机器：`{machine_id}`\n"
                "已取消，本次没有保存配置，也不会触发 daemon policy sync。"
            )}},
            {"tag": "note", "elements": [{"tag": "plain_text",
                "content": f"由 {operator_name} 取消 · 再次发送 /machine_config 即可修改"}]},
        ],
    }


def _card_message_id_from_event(event, value):
    for obj in (value, getattr(event, "context", None), getattr(event, "action", None), event):
        found = _value_from_obj(
            obj,
            "card_message_id",
            "message_id",
            "open_message_id",
            "open_card_id",
            "card_id",
        )
        if found:
            return found
    return ""


def _handle_machine_config_command(api, registry, relay_ws_server, chat_id, message_id,
                                   sender_open_id, sender_name, helper_policy=None,
                                   machine_config_schema=None):
    machine_id, machine_info, reason = _resolve_machine_config_target(registry, chat_id)
    if reason:
        _reply_message_with_log(api, message_id, f"⚠️ {reason}", "MACHINE_CONFIG")
        return True
    _policy, schema, schema_error = _resolve_machine_config_policy_schema(machine_config_schema)
    if schema_error:
        _reply_message_with_log(api, message_id, f"⚠️ /machine_config 失败：{schema_error}", "MACHINE_CONFIG")
        return True
    if not _machine_config_has_fields(schema):
        _reply_message_with_log(api, message_id, "⚠️ 当前 relay policy 未提供 machine_config 配置项", "MACHINE_CONFIG")
        return True
    selected_fields = env_switch_state_for_machine(_root_dir, machine_id) if _root_dir else {}
    card_token = _new_machine_config_card_token()
    card = _build_machine_config_card(
        machine_id, sender_open_id, sender_name, schema=schema,
        selected_fields=selected_fields, card_token=card_token)
    card_message_id, err = api.send_interactive_card(chat_id, card)
    if err:
        _reply_message_with_log(api, message_id, f"⚠️ /machine_config 失败：{err}", "MACHINE_CONFIG")
        return True
    if card_message_id:
        update_err = api.update_interactive_card(
            card_message_id,
            _build_machine_config_card(
                machine_id, sender_open_id, sender_name,
                card_message_id=card_message_id, schema=schema,
                selected_fields=selected_fields, card_token=card_token),
        )
        if update_err:
            log.warning(f"[MACHINE_CONFIG] card id backfill failed msg={card_message_id}: {update_err}")
    log.info(f"[MACHINE_CONFIG] card sent chat={chat_id} machine={machine_id} msg={card_message_id}")
    return True


def _handle_machine_config_card_action(api, registry, relay_ws_server, value, form_value,
                                       chat_id, event, helper_policy=None,
                                       machine_config_schema=None):
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTriggerResponse, CallBackToast, CallBackCard
    )

    resp = P2CardActionTriggerResponse()
    resp.toast = CallBackToast()

    machine_id = value.get("machine_id") or ""
    expected_open_id = value.get("operator_open_id") or ""
    expected_name = value.get("operator_name") or "卡片发起者"
    actual_open_id = _operator_user_identifier(event)
    if expected_open_id and actual_open_id and actual_open_id != expected_open_id:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": f"仅 {expected_name} 可保存配置", "en_us": f"Only {expected_name} can save"}
        return resp
    if not machine_id:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": "machine_config 回调缺少机器", "en_us": "machine_config callback missing machine"}
        return resp
    card_message_id = _card_message_id_from_event(event, value)
    operation = value.get("machine_config_operation") or _MACHINE_CONFIG_OPERATION_SAVE
    if operation == _MACHINE_CONFIG_OPERATION_CANCEL:
        _mark_machine_config_card_canceled(value, card_message_id)
        resp.toast.type = "success"
        resp.toast.i18n = {"zh_cn": "已取消（无变更）", "en_us": "Canceled; no changes"}
        resp.card = CallBackCard()
        resp.card.type = "raw"
        resp.card.data = _build_machine_config_cancel_card(machine_id, expected_name)
        return resp
    if _machine_config_card_is_canceled(value, card_message_id):
        resp.toast.type = "error"
        resp.toast.i18n = {
            "zh_cn": "这张机器配置卡片已取消，请重新发送 /machine_config",
            "en_us": "This machine_config card was canceled; send /machine_config again",
        }
        return resp
    try:
        enterprise_policy, schema, schema_error = _resolve_machine_config_policy_schema(machine_config_schema)
        if schema_error:
            resp.toast.type = "error"
            resp.toast.i18n = {"zh_cn": f"machine_config 读取失败：{schema_error}", "en_us": f"machine_config load failed: {schema_error}"}
            return resp
        enabled_groups = [
            str(key)[len("env_switch__"):]
            for key, value in (form_value or {}).items()
            if str(key).startswith("env_switch__") and str(value) == "enabled"
        ]
        group_values = {}
        for form_key, form_input in (form_value or {}).items():
            form_key = str(form_key)
            prefix = "env_switch_field__"
            if not form_key.startswith(prefix):
                continue
            rest = form_key[len(prefix):]
            if "__" not in rest:
                continue
            group_key, field_key = rest.split("__", 1)
            if not group_key or not field_key:
                continue
            group_values.setdefault(group_key, {})[field_key] = str(form_input or "")
        normalized = normalize_env_switch_state(schema, enabled_groups, group_values)
    except MachineConfigPolicyError as exc:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": f"machine_config 无效：{exc}", "en_us": f"Invalid machine_config: {exc}"}
        return resp
    if not card_message_id:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": "无法定位要更新的飞书卡片", "en_us": "Cannot locate card message"}
        return resp
    operation_id = value.get("operation_id") or _uuid.uuid4().hex
    try:
        if enterprise_policy is None:
            enterprise_policy = load_enterprise_policy(_root_dir)
        save_report = save_env_switch_state(
            work_root=_root_dir,
            policy=enterprise_policy,
            machine_id=machine_id,
            enabled_groups=normalized["enabled_groups"],
            group_values=normalized["group_values"],
            operator_open_id=actual_open_id or expected_open_id,
            operation_id=operation_id,
        )
    except MachineConfigPolicyError as exc:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": f"machine_config 保存失败：{exc}", "en_us": f"machine_config save failed: {exc}"}
        return resp
    payload = {
        "type": "daemon_policy_sync",
        "operation_id": operation_id,
        "machine_id": machine_id,
        "source_chat_id": chat_id,
        "card_message_id": card_message_id,
        "operator_open_id": actual_open_id or expected_open_id,
        "enabled_groups": normalized["enabled_groups"],
        "group_values": normalized["group_values"],
        "changed_groups": save_report.get("changed_groups") or [],
        "schema": schema.get("schema") or value.get("schema") or "intern-agents.env-switches.v1",
    }
    sent = relay_ws_server.send_to_machine(machine_id, payload)
    resp.toast.type = "success" if sent else "warning"
    resp.toast.i18n = (
        {"zh_cn": "配置已保存，本机 daemon 将同步 policy 并更新进度", "en_us": "Saved; daemon will sync policy and update progress"}
        if sent else
        {"zh_cn": "配置已保存；目标机器离线，当前不会立即生效", "en_us": "Saved; target machine is offline and will not apply immediately"}
    )
    return resp


def _build_client_upgrade_card(machine_id, operator_open_id, reply_chat_id, upgrade_report):
    current = upgrade_report.get("current_version") or "unknown"
    latest = upgrade_report.get("latest_version") or "unknown"
    message = upgrade_report.get("message") or f"Update available: {current} -> {latest}."
    base_value = {
        "machine_id": machine_id,
        "helper_id": _machine_helper_id_for_machine(machine_id),
        "operator_open_id": operator_open_id,
        "reply_chat_id": reply_chat_id,
        "current_version": current,
        "latest_version": latest,
        "runtime": "codex",
    }
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": f"客户端可升级到 {latest}"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                f"机器：`{machine_id}`\n"
                f"当前版本：`{current}`\n"
                f"目标版本：`{latest}`\n"
                f"{message}\n\n"
                "点击升级会执行与 TreeView/SSH 相同的 `internctl upgrade`：安装客户端、同步 hooks/CLI runtime，并在 daemon 正在运行时调度 daemon restart。relay 不会被重启。"
            )}},
            {"tag": "action", "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"升级到 {latest}"},
                    "type": "primary",
                    "value": {**base_value, "helper_action": "upgrade_client"},
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "取消"},
                    "type": "default",
                    "value": {**base_value, "helper_action": "upgrade_cancel"},
                },
            ]},
        ],
    }


def _build_client_upgrade_cancel_card(machine_id, current, latest):
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "grey",
            "title": {"tag": "plain_text", "content": "客户端升级已取消"},
        },
        "elements": [
            {"tag": "div", "text": {"tag": "lark_md", "content": (
                f"机器：`{machine_id}`\n"
                f"当前版本：`{current or 'unknown'}`\n"
                f"目标版本：`{latest or 'unknown'}`\n"
                "本次没有执行升级，也不会重启 daemon。"
            )}},
        ],
    }


def _handle_upgrade_command(api, registry, relay_ws_server, chat_id, message_id,
                            sender_open_id, machine_id, helper_policy=None):
    if not machine_id:
        _reply_message_with_log(api, message_id, "⚠️ /upgrade 无法识别当前机器", "UPGRADE")
        return True
    machine_info = (registry.get_machines_summary() or {}).get(machine_id, {})
    if not helper_policy_allows(
            _helper_policy_for_callback(helper_policy),
            sender_open_id,
            machine_id,
            "helper_upgrade",
            machine_info):
        _reply_message_with_log(api, message_id, "⚠️ 无权限执行客户端升级", "UPGRADE")
        return True
    helper_id = _machine_helper_id_for_machine(machine_id)
    result, error = relay_ws_server.helper_action_request(
        machine_id,
        "upgrade_check",
        helper_id=helper_id,
        operator_open_id=sender_open_id,
        reply_chat_id=chat_id,
        silent_result=True,
        timeout=30,
    )
    if error:
        _reply_message_with_log(api, message_id, f"⚠️ /upgrade 检查失败：{error}", "UPGRADE")
        return True
    upgrade_report = result.get("upgrade") if isinstance(result.get("upgrade"), dict) else result
    if not upgrade_report.get("update_available"):
        message = upgrade_report.get("message") or (
            f"已经是最新版本：`{upgrade_report.get('current_version') or result.get('current_version') or 'unknown'}`"
        )
        _reply_message_with_log(api, message_id, message, "UPGRADE")
        return True
    card = _build_client_upgrade_card(machine_id, sender_open_id, chat_id, upgrade_report)
    card_message_id, err = api.send_interactive_card(chat_id, card)
    if err:
        _reply_message_with_log(api, message_id, f"⚠️ /upgrade 卡片发送失败：{err}", "UPGRADE")
        return True
    log.info(f"[UPGRADE] card sent chat={chat_id} machine={machine_id} msg={card_message_id}")
    return True


def _handle_helper_command(api, registry, relay_ws_server, text, chat_id, message_id,
                           sender_open_id, helper_policy=None):
    def reply(body):
        return _reply_message_with_log(api, message_id, body, "HELPER")

    try:
        cmd = _parse_helper_command(text)
    except ValueError as e:
        reply(f"⚠️ {e}")
        return True
    if not cmd:
        return False

    policy = _helper_policy_for_callback(helper_policy)
    machines = registry.get_machines_summary() or {}
    helpers = registry.get_helpers_summary()
    action = cmd["action"]
    machine_id = cmd["machine_id"]
    project = cmd.get("project") or ""
    if project and not _helper_project_exists(registry, project):
        reply(
            "⚠️ slash_routing_error\n"
            "schema: `intern-agents.slash-routing-error.v1`\n"
            "reason: `project_not_found`\n"
            f"command: `{RELAY_HELPER_COMMAND}`\n"
            f"project: `{project}`"
        )
        return True
    if action == "help":
        reply(_helper_usage_text())
        return True
    endpoint = ""
    if action == "migrate":
        endpoint = machine_id
        machine_id = next(iter(machines), "") if len(machines) == 1 else ""
        if not machine_id:
            api.reply_message(message_id, "⚠️ /helper migrate 需要当前只连接一台机器，或请先启动指定机器 helper 后在 helper 群内说明目标 ip:port")
            return True
        cmd["machine_id"] = machine_id
    if action in {"start", "stop", "invite-owner"}:
        if machine_id and machine_id not in machines:
            cmd["args"] = [machine_id] + list(cmd.get("args") or [])
            machine_id = ""
            cmd["machine_id"] = ""
        if not machine_id:
            issue_summary = " ".join(cmd["args"]).strip()
            return _send_helper_action_card(
                api, registry, helper_policy, action, chat_id, message_id, sender_open_id, issue_summary=issue_summary)
    if action == "status" and not machine_id:
        visible = filter_visible_helpers_for_helper(helpers, machines, sender_open_id, policy)
        if not visible:
            reply("当前没有可见 helper")
            return True
        lines = [_helper_status_text(v) for _, v in sorted(visible.items())]
        reply("\n\n".join(lines))
        return True

    machine_info = machines.get(machine_id, {})
    if not helper_policy_allows(policy, sender_open_id, machine_id,
                                _helper_action_to_policy_action(action), machine_info):
        reply("⚠️ 无权限执行 helper 操作")
        return True

    if action == "status":
        reply(_helper_status_text(registry.get_machine_helper(machine_id)))
        return True
    helper_id = _machine_helper_id_for_machine(machine_id)
    request_id = _uuid.uuid4().hex
    issue_summary = " ".join(cmd["args"]).strip()
    if action == "migrate":
        issue_summary = f"新机器迁移目标 {endpoint}"

    existing_helper = registry.get_machine_helper(machine_id)
    helper_chat_id = existing_helper.get("chat_id", "")
    if action == "start" and not _machine_helper_is_started(existing_helper):
        helper_chat_id = ""
    if action == "invite-owner":
        helper_chat_id, existing, err = _ensure_machine_helper_chat(api, registry, machine_id, sender_open_id)
        if err:
            reply(f"⚠️ helper 群创建失败：{err}")
            return True

        owner_open_id = _machine_helper_owner_open_id(policy, machine_info)
        if not owner_open_id:
            reply("⚠️ 未配置 app owner 或 machine owner open_id")
            return True
        members, members_err = api.get_chat_members(helper_chat_id)
        if members_err:
            reply(f"⚠️ 读取 helper 群成员失败：{members_err}")
            return True
        if owner_open_id not in (members or []):
            add_err = api.add_chat_members(helper_chat_id, [owner_open_id])
            if add_err:
                reply(f"⚠️ 邀请 owner 失败：{add_err}")
                return True
        issue_summary = issue_summary or "请向 app owner 说明当前机器问题、用户诉求、已做排查和需要 owner 确认的事项。"

    runtime = _machine_helper_preferred_runtime(machine_info, existing_helper)
    status_by_action = {
        "start": "starting",
        "stop": "stopping",
        "invite-owner": "inviting_owner",
        "migrate": "migration_prompting",
    }
    registry.register_machine_helper(
        machine_id,
        helper_id=helper_id,
        runtime=runtime,
        chat_id=helper_chat_id,
        status=status_by_action[action],
        last_operator_open_id=sender_open_id,
    )
    registry.append_machine_helper_audit(
        machine_id, action, sender_open_id,
        {"request_id": request_id, "source_chat_id": chat_id, "helper_chat_id": helper_chat_id})
    payload = {
        "type": "helper_action",
        "helper_action": _helper_action_to_daemon_action(action),
        "machine_id": machine_id,
        "helper_id": helper_id,
        "chat_id": helper_chat_id,
        "request_id": request_id,
        "operator_open_id": sender_open_id,
        "issue_summary": issue_summary,
        "runtime": runtime,
    }
    if endpoint:
        payload["endpoint"] = endpoint
    sent = relay_ws_server.send_to_machine(machine_id, payload)
    reply(
        f"helper 操作已发送：`{action}` → `{helper_id}`"
        if sent else
        f"⚠️ helper 所在机器当前离线：`{machine_id}`"
    )
    return True


def _main_bot_help_text():
    return (
        "我是 Intern Agents 主入口。\n"
        "可用命令：\n"
        "- `/help` 查看帮助\n"
        "- `/status` 查看 relay、机器、intern 和 helper 状态\n"
        "- `/list machines|workspaces|interns|helpers` 列出资源\n"
        f"- `{RELAY_HELPER_COMMAND} status` 查看可用 helper\n"
        f"- `{RELAY_HELPER_COMMAND} start [machine_id]` 启动 helper\n"
        f"- `{RELAY_HELPER_COMMAND} stop [machine_id]` 停止 helper\n"
        f"- `{RELAY_MACHINE_CONFIG_COMMAND}` 配置当前机器级能力\n"
        f"- `{RELAY_HELPER_COMMAND} migrate <ip:port>` 协助迁移到新机器\n\n"
        "要和具体 intern 交流，请进入对应 intern 群；如果刚安装，请先完成 setup 并创建/启动 intern。"
    )


def _format_main_bot_status(registry):
    try:
        machines = registry.get_machines_summary() or {}
        helpers = registry.get_helpers_summary() or {}
        scene = registry.get_current_scene() if hasattr(registry, "get_current_scene") else {}
    except Exception as e:
        return f"主入口状态不可用：{e}"
    connected = [m for m in machines.values() if m.get("ws_connected")]
    interns_total = sum(len(m.get("interns_detail") or []) for m in machines.values())
    interns_online = sum(1 for m in machines.values() for item in (m.get("interns_detail") or []) if item.get("online"))
    summary = scene.get("summary") if isinstance(scene, dict) else {}
    lines = [
        "Intern Agents 主入口状态",
        f"Machines: {len(connected)} connected / {len(machines)} visible",
        f"Interns: {interns_online} online / {interns_total} total",
        f"Helpers: {len(helpers)}",
    ]
    if isinstance(summary, dict):
        lines.append(
            "Scene: "
            f"active={summary.get('active_groups', 0)}, "
            f"red={summary.get('active_red_groups', 0)}, "
            f"stale={summary.get('stale_persisted_groups', 0)}"
        )
    return "\n".join(lines)


def _format_main_bot_list(registry, topic=""):
    try:
        machines = registry.get_machines_summary() or {}
        helpers = registry.get_helpers_summary() or {}
    except Exception as e:
        return f"主入口列表不可用：{e}"
    topic = (topic or "").strip().lower()
    if topic in ("", "all"):
        return _format_main_bot_status(registry)
    if topic == "machines":
        lines = ["Machines"]
        for mid, info in sorted(machines.items()):
            status = "online" if info.get("ws_connected") else "offline"
            lines.append(f"- {mid}: {status}, interns={len(info.get('interns') or [])}")
        return "\n".join(lines) if len(lines) > 1 else "Machines\nNo visible machines."
    if topic == "workspaces":
        rows = []
        for mid, info in sorted(machines.items()):
            workspaces = info.get("workspaces") or []
            names = []
            for item in workspaces:
                if isinstance(item, dict):
                    names.append(item.get("workspace_id") or item.get("display_name") or "")
                else:
                    names.append(str(item))
            rows.append(f"- {mid}: {', '.join(x for x in names if x) or 'no known workspaces'}")
        return "Workspaces\n" + ("\n".join(rows) if rows else "No visible machines.")
    if topic == "interns":
        lines = ["Interns"]
        for mid, info in sorted(machines.items()):
            details = info.get("interns_detail") or []
            if not details:
                continue
            lines.append(f"- {mid}")
            for item in sorted(details, key=lambda x: (x.get("project") or "", x.get("name") or "")):
                state = "online" if item.get("online") else "offline"
                lines.append(f"  - {item.get('name')} / {item.get('project')} / {item.get('type')} / {state}")
        return "\n".join(lines) if len(lines) > 1 else "Interns\nNo visible interns."
    if topic in {"helpers", "helper"}:
        lines = ["Helpers"]
        for mid, helper in sorted(helpers.items()):
            lines.append(f"- {mid}: {helper.get('status', '')}, id={helper.get('helper_id', '')}")
        return "\n".join(lines) if len(lines) > 1 else "Helpers\nNo visible helpers."
    return "未知列表类型。请使用 `/list machines|workspaces|interns|helpers`。"


def _handle_unmapped_main_bot_message(api, registry, message_id, text):
    stripped = (text or "").strip()
    if stripped.startswith("/status") or stripped.startswith("/debug"):
        _reply_message_with_log(api, message_id, _format_main_bot_status(registry), "MAIN_BOT")
        return True
    if stripped.startswith("/list"):
        parts = stripped.split()
        topic = parts[1] if len(parts) >= 2 else ""
        _reply_message_with_log(api, message_id, _format_main_bot_list(registry, topic), "MAIN_BOT")
        return True
    if stripped.startswith("/create-intern") or stripped.startswith("/add-workspace"):
        _reply_message_with_log(
            api,
            message_id,
            "主入口不直接创建 intern 或 workspace。请先完成 setup；需要排障时使用 `/helper start`。",
            "MAIN_BOT",
        )
        return True
    _reply_message_with_log(api, message_id, _main_bot_help_text(), "MAIN_BOT")
    return True


def _message_chat_type(msg):
    for key in ("chat_type", "_chat_type"):
        value = getattr(msg, key, "")
        if isinstance(value, str) and value:
            return value.lower()
    raw = getattr(msg, "__dict__", {}) or {}
    if isinstance(raw, dict):
        for key in ("chat_type", "_chat_type"):
            value = raw.get(key)
            if isinstance(value, str) and value:
                return value.lower()
    for method in ("to_dict", "model_dump"):
        fn = getattr(msg, method, None)
        if callable(fn):
            try:
                data = fn()
            except Exception:
                data = None
            if isinstance(data, dict):
                value = data.get("chat_type")
                if isinstance(value, str) and value:
                    return value.lower()
    fn = getattr(msg, "to_json", None)
    if callable(fn):
        try:
            data = json.loads(fn())
        except Exception:
            data = None
        if isinstance(data, dict):
            value = data.get("chat_type")
            if isinstance(value, str) and value:
                return value.lower()
    return ""


def _handle_helper_card_action(api, registry, relay_ws_server, value, form_value, chat_id, event, helper_policy=None):
    from lark_oapi.event.callback.model.p2_card_action_trigger import (
        P2CardActionTriggerResponse, CallBackToast, CallBackCard
    )

    action_name = value.get("helper_action") or ""
    machine_id = _helper_selected_machine_from_value(registry, value, chat_id)
    expected_open_id = value.get("operator_open_id") or ""
    actual_open_id = _operator_user_identifier(event)
    detail_mode = _helper_detail_mode_from_value(registry, value, machine_id)

    resp = P2CardActionTriggerResponse()
    resp.toast = CallBackToast()

    if expected_open_id and actual_open_id and actual_open_id != expected_open_id:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": "仅卡片发起者可操作 helper", "en_us": "Only the card owner can operate helper"}
        return resp
    if not action_name:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": "helper 回调数据异常", "en_us": "Invalid helper callback"}
        return resp
    if action_name in {"start", "stop", "invite_owner", "delete_group", "confirm_delete", "select_machine", "upgrade_client", "upgrade_cancel"} and not machine_id:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": "请先选择 helper 机器", "en_us": "Select a helper machine first"}
        return resp
    if not actual_open_id:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": "无法识别操作人，helper 操作已拒绝", "en_us": "Helper operator is unknown"}
        return resp

    try:
        policy_action = _helper_policy_action_for_card_action(action_name)
    except ValueError as e:
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": str(e), "en_us": str(e)}
        return resp
    machine_info = (registry.get_machines_summary() or {}).get(machine_id, {})
    if action_name != "upgrade_cancel" and machine_id and not helper_policy_allows(
            _helper_policy_for_callback(helper_policy),
            actual_open_id,
            machine_id,
            policy_action,
            machine_info):
        resp.toast.type = "error"
        resp.toast.i18n = {"zh_cn": "无权限执行 helper 操作", "en_us": "Not allowed to operate helper"}
        return resp

    if action_name == "cancel":
        resp.toast.type = "success"
        resp.toast.i18n = {"zh_cn": "helper 操作已取消", "en_us": "Helper action canceled"}
        resp.card = CallBackCard()
        resp.card.type = "raw"
        resp.card.data = _build_helper_action_cancel_card(value.get("source_helper_action") or "")
        return resp

    if action_name == "select_machine":
        helper_id = _machine_helper_id_for_machine(machine_id)
        existing_helper = registry.get_machine_helper(machine_id) or {}
        registry.register_machine_helper(
            machine_id,
            helper_id=helper_id,
            runtime=value.get("runtime") or _machine_helper_preferred_runtime(machine_info, existing_helper),
            status=(registry.get_machine_helper(machine_id) or {}).get("status") or "stopped",
            selected_machine_id=machine_id,
            detail_mode=detail_mode,
            last_operator_open_id=actual_open_id or expected_open_id,
        )
        registry.append_machine_helper_audit(
            machine_id, action_name, actual_open_id,
            {"source_chat_id": chat_id, "detail_mode": detail_mode})
        resp.toast.type = "success"
        resp.toast.i18n = {"zh_cn": "已选择 helper 机器", "en_us": "Helper machine selected"}
        resp.card = CallBackCard()
        resp.card.type = "raw"
        resp.card.data = _build_helper_state_card(
            registry, value, machine_id, detail_mode, note="helper 机器已选择")
        return resp

    if action_name == "set_detail_mode":
        detail_mode = _normalize_helper_detail_mode(
            value.get("detail_mode")
            or (form_value or {}).get("detail_mode")
            or (form_value or {}).get("mode"))
        if not detail_mode:
            resp.toast.type = "error"
            resp.toast.i18n = {"zh_cn": "未知 helper detail_mode", "en_us": "Unknown helper detail_mode"}
            return resp
        if machine_id:
            helper_id = value.get("helper_id") or _machine_helper_id_for_machine(machine_id)
            existing_helper = registry.get_machine_helper(machine_id) or {}
            registry.register_machine_helper(
                machine_id,
                helper_id=helper_id,
                runtime=value.get("runtime") or _machine_helper_preferred_runtime(machine_info, existing_helper),
                status=(registry.get_machine_helper(machine_id) or {}).get("status") or "stopped",
                selected_machine_id=machine_id,
                detail_mode=detail_mode,
                last_operator_open_id=actual_open_id or expected_open_id,
            )
            registry.append_machine_helper_audit(
                machine_id, action_name, actual_open_id,
                {"source_chat_id": chat_id, "detail_mode": detail_mode})
        resp.toast.type = "success"
        resp.toast.i18n = {"zh_cn": "helper detail_mode 已保存", "en_us": "Helper detail_mode saved"}
        resp.card = CallBackCard()
        resp.card.type = "raw"
        resp.card.data = _build_helper_state_card(
            registry, value, machine_id, detail_mode, note="detail_mode 已保存")
        return resp

    if action_name == "upgrade_cancel":
        resp.toast.type = "success"
        resp.toast.i18n = {"zh_cn": "客户端升级已取消", "en_us": "Client upgrade canceled"}
        resp.card = CallBackCard()
        resp.card.type = "raw"
        resp.card.data = _build_client_upgrade_cancel_card(
            machine_id,
            value.get("current_version") or "",
            value.get("latest_version") or "",
        )
        return resp

    helper_id = value.get("helper_id") or _machine_helper_id_for_machine(machine_id)
    request_id = value.get("request_id") or _uuid.uuid4().hex
    existing_helper = registry.get_machine_helper(machine_id) or {}
    runtime = value.get("runtime") or _machine_helper_preferred_runtime(machine_info, existing_helper)
    if action_name == "start" and not _machine_helper_is_started(existing_helper):
        helper_chat_id = ""
    else:
        helper_chat_id = value.get("chat_id") or existing_helper.get("chat_id", "")
    if action_name == "invite_owner" and not helper_chat_id:
        helper_chat_id, _, chat_err = _ensure_machine_helper_chat(api, registry, machine_id, actual_open_id)
        if chat_err:
            resp.toast.type = "error"
            resp.toast.i18n = {"zh_cn": f"helper 群创建失败：{chat_err}", "en_us": f"Helper group failed: {chat_err}"}
            return resp
    if action_name == "invite_owner":
        owner_open_id = _machine_helper_owner_open_id(_helper_policy_for_callback(helper_policy), machine_info)
        if not owner_open_id:
            resp.toast.type = "error"
            resp.toast.i18n = {"zh_cn": "未配置 app owner 或 machine owner open_id", "en_us": "No owner open_id configured"}
            return resp
        members, members_err = api.get_chat_members(helper_chat_id)
        if members_err:
            resp.toast.type = "error"
            resp.toast.i18n = {"zh_cn": f"读取 helper 群成员失败：{members_err}", "en_us": f"Read helper members failed: {members_err}"}
            return resp
        if owner_open_id not in (members or []):
            add_err = api.add_chat_members(helper_chat_id, [owner_open_id])
            if add_err:
                resp.toast.type = "error"
                resp.toast.i18n = {"zh_cn": f"邀请 owner 失败：{add_err}", "en_us": f"Invite owner failed: {add_err}"}
                return resp
    status_by_action = {
        "start": "starting",
        "stop": "stopping",
        "delete_group": "deleting_group",
        "confirm_delete": "deleting_group",
        "invite_owner": "inviting_owner",
        "upgrade_client": "upgrading_client",
    }
    registry.register_machine_helper(
        machine_id,
        helper_id=helper_id,
        runtime=runtime,
        chat_id=helper_chat_id,
        status=status_by_action.get(action_name, "pending"),
        last_operator_open_id=actual_open_id or expected_open_id,
        selected_machine_id=machine_id,
        detail_mode=detail_mode,
    )
    registry.append_machine_helper_audit(machine_id, action_name, actual_open_id, {"request_id": request_id, "source_chat_id": chat_id})
    payload = {
        "type": "helper_action",
        "helper_action": action_name,
        "machine_id": machine_id,
        "helper_id": helper_id,
        "chat_id": helper_chat_id,
        "request_id": request_id,
        "operator_open_id": actual_open_id or expected_open_id,
        "detail_mode": detail_mode,
        "runtime": runtime,
    }
    if form_value:
        payload["form_value"] = form_value
    if value.get("issue_summary"):
        payload["issue_summary"] = value.get("issue_summary")
    if value.get("reply_chat_id"):
        payload["reply_chat_id"] = value.get("reply_chat_id")
    sent = relay_ws_server.send_to_machine(machine_id, payload)

    resp.toast.type = "success" if sent else "warning"
    resp.toast.i18n = (
        {"zh_cn": "helper 操作已发送", "en_us": "Helper action sent"}
        if sent else
        {"zh_cn": "helper 所在机器当前离线", "en_us": "Helper machine is offline"}
    )
    resp.card = CallBackCard()
    resp.card.type = "raw"
    resp.card.data = {
        "header": {
            "template": "blue" if sent else "red",
            "title": {"tag": "plain_text", "content": "客户端升级处理中" if action_name == "upgrade_client" else "Machine Helper"},
        },
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": (
            f"helper_action=`{action_name}`\n"
            f"machine_id=`{machine_id}`\n"
            f"detail_mode=`{detail_mode}`\n"
            f"request_id=`{request_id}`"
        )}}],
    }
    return resp


def create_card_callback_handler(api, registry, relay_ws_server, helper_policy=None, machine_config_schema=None):
    """Create handler for card.action.trigger callbacks (interactive card button clicks)."""

    def handle_card_action(data):
        global _feishu_msg_count, _feishu_last_msg_time
        global _feishu_card_action_count, _feishu_card_action_last_time
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse, CallBackToast, CallBackCard
        )

        # task249: 飞书重投 dedup。命中后返回空 response 让 SDK 正常 ack 飞书，
        # 首次 handle 已经更新过卡片，重投无需再改 UI。
        if _check_and_record_event(_extract_event_id(data), "card_action"):
            return P2CardActionTriggerResponse()

        _feishu_msg_count += 1
        _feishu_last_msg_time = time.time()
        _feishu_card_action_count += 1
        _feishu_card_action_last_time = _feishu_last_msg_time
        _relay_metrics.record("feishu:card_action")

        try:
            event = data.event
            action = event.action
            context = event.context
            raw_value = action.value if isinstance(action.value, dict) else {}
            form_value = action.form_value  # None for button clicks, dict for form submissions
            config_value = _normalize_config_callback_value(raw_value, form_value)
            value = (
                config_value
                if (
                    config_value.get("config_action") == _CONFIG_CARD_ACTION
                    or config_value.get("config_action") == _CONFIG_CANCEL_ACTION
                )
                else raw_value
            )

            intern_name = value.get("intern_name", "")
            question_id = value.get("question_id", "")
            question_title = value.get("question_title", "")
            chat_id = context.open_chat_id if context else ""
            log.info(
                "[FEISHU_WS] CardAction #%s: chat=%s action=%s machine=%s operator=%s",
                _feishu_card_action_count,
                chat_id,
                value.get("machine_config_action") or value.get("helper_action") or value.get("config_action") or value.get("action") or "",
                value.get("machine_id") or "",
                _operator_user_identifier(event),
            )

            if value.get("machine_config_action") == _MACHINE_CONFIG_CARD_ACTION:
                return _handle_machine_config_card_action(
                    api, registry, relay_ws_server, value, form_value, chat_id, event,
                    helper_policy, machine_config_schema)

            # task343 Phase 2: helper cards use their own sentinel and must not
            # fall through to /config or AskUser/request_user_input callbacks.
            if value.get("helper_action"):
                return _handle_helper_card_action(
                    api, registry, relay_ws_server, value, form_value, chat_id, event, helper_policy)

            # task258: /config card submit — relay owns this, do not route to
            # any intern. Detected by the _CONFIG_CARD_ACTION sentinel placed
            # in value by _build_config_card.
            if (
                value.get("config_action") == _CONFIG_CARD_ACTION
                or value.get("config_action") == _CONFIG_CANCEL_ACTION
            ):
                # task281: only the /config sender (recorded in value at card
                # build time) may submit. Foreign clickers get a toast and the
                # card is left untouched — no resp.card so the shared card
                # view stays on the form for everyone, including the rejected
                # operator (no misleading "saved" view).
                expected_open_id = value.get("operator_open_id") or ""
                expected_name = value.get("operator_name") or "卡片发起者"
                actual_open_id = _operator_user_identifier(event)
                if expected_open_id and actual_open_id != expected_open_id:
                    log.info(
                        f"[CONFIG] chat={chat_id} rejected by foreign operator "
                        f"actual={actual_open_id} expected={expected_open_id}")
                    resp = P2CardActionTriggerResponse()
                    resp.toast = CallBackToast()
                    resp.toast.type = "error"
                    resp.toast.i18n = {
                        "zh_cn": f"⛔ 仅 {expected_name} 可保存配置",
                        "en_us": f"Only {expected_name} can save",
                    }
                    return resp

                operation = value.get("config_operation")
                if not operation and value.get("config_action") == _CONFIG_CANCEL_ACTION:
                    operation = _CONFIG_OPERATION_CANCEL
                operation = operation or _CONFIG_OPERATION_SAVE
                if operation == _CONFIG_OPERATION_CANCEL:
                    _invalidate_config_card_state(value)
                    snapshot = _config_snapshot_from_action_value(value, chat_id)
                    resp = P2CardActionTriggerResponse()
                    resp.toast = CallBackToast()
                    resp.toast.type = "success"
                    resp.toast.i18n = {"zh_cn": "已取消（无变更）", "en_us": "Canceled; no changes"}
                    resp.card = CallBackCard()
                    resp.card.type = "raw"
                    resp.card.data = _build_config_cancel_card(chat_id, snapshot, expected_name)
                    return resp

                if _is_config_card_state_invalidated(value):
                    log.info(
                        f"[CONFIG] chat={chat_id} rejected stale submit after cancel "
                        f"operator={actual_open_id}")
                    snapshot = _config_snapshot_from_action_value(value, chat_id)
                    resp = P2CardActionTriggerResponse()
                    resp.toast = CallBackToast()
                    resp.toast.type = "error"
                    resp.toast.i18n = {
                        "zh_cn": "此配置卡片已取消，请重新发送 /config",
                        "en_us": "This config card was canceled; send /config again",
                    }
                    resp.card = CallBackCard()
                    resp.card.type = "raw"
                    resp.card.data = _build_config_cancel_card(chat_id, snapshot, expected_name)
                    return resp

                fv = _config_form_values(form_value)
                snapshot, changed, description_error = _handle_config_card_submit(
                    chat_id, fv, relay_ws_server)
                resp = P2CardActionTriggerResponse()
                resp.toast = CallBackToast()
                if description_error:
                    resp.toast.type = "warning"
                    resp.toast.i18n = {
                        "zh_cn": "配置已保存，群描述同步失败",
                        "en_us": "Config saved; group description sync failed",
                    }
                elif any(changed.values()):
                    resp.toast.type = "success"
                    resp.toast.i18n = {"zh_cn": "配置已更新", "en_us": "Config updated"}
                else:
                    resp.toast.type = "success"
                    resp.toast.i18n = {"zh_cn": "已确认（无变化）", "en_us": "No changes"}
                resp.card = CallBackCard()
                resp.card.type = "raw"
                resp.card.data = _build_config_result_card(
                    chat_id, snapshot, changed, expected_name, description_error)
                return resp

            if not intern_name:
                log.warning("[CARD_CALLBACK] Missing intern_name, ignoring")
                resp = P2CardActionTriggerResponse()
                resp.toast = CallBackToast()
                resp.toast.type = "error"
                resp.toast.i18n = {"zh_cn": "回调数据异常", "en_us": "Invalid callback data"}
                return resp

            # Build routing message based on callback type
            if form_value:
                # Form submission (free text or multi-question form)
                question_keys = value.get("question_keys", [])
                log.info(
                    f"[CARD_CALLBACK] form: intern={intern_name}, question_id={question_id or '-'}, "
                    f"form_value={str(form_value)[:120]}, chat_id={chat_id}"
                )

                route_msg = {
                    "type": "card_callback",
                    "intern_name": intern_name,
                    "question_id": question_id,
                    "form_value": form_value,
                    "question_keys": question_keys,
                    "is_form": True,
                    "chat_id": chat_id,
                }
                # Build display text from form values (list → "a+b" 而非 str(list))
                def _stringify(v):
                    if isinstance(v, list):
                        return "+".join(str(x) for x in v)
                    return str(v)
                answer_parts = [_stringify(v) for k, v in form_value.items() if v and k != "submit"]
                answer_display = ", ".join(answer_parts) if answer_parts else "(无)"
            else:
                # Button click (single answer)
                answer_value = value.get("answer", "")
                log.info(
                    f"[CARD_CALLBACK] button: intern={intern_name}, question_id={question_id or '-'}, "
                    f"answer={answer_value[:80]}, chat_id={chat_id}"
                )

                if not answer_value:
                    resp = P2CardActionTriggerResponse()
                    resp.toast = CallBackToast()
                    resp.toast.type = "error"
                    resp.toast.i18n = {"zh_cn": "回调数据异常", "en_us": "Invalid callback data"}
                    return resp

                route_msg = {
                    "type": "card_callback",
                    "intern_name": intern_name,
                    "question_id": question_id,
                    "answer": answer_value,
                    "chat_id": chat_id,
                }
                answer_display = answer_value

            # Route to machine. 用 chat_id 查 entry（project-safe），避免按 intern_name
            # 无 project 查询命中 stale entry。
            entry = registry.find_entry_by_chat(chat_id)
            if not entry:
                helper_entry = registry.find_helper_by_chat(chat_id)
                if helper_entry:
                    helper_machine_id = helper_entry.get("machine_id", "")
                    entry = {
                        "machine_id": helper_machine_id,
                        "name": helper_entry.get("helper_id", ""),
                        "project": helper_entry.get("project") or (
                            _machine_helper_project_for_machine(helper_machine_id) if helper_machine_id else ""
                        ),
                    }
                    route_msg["intern_name"] = helper_entry.get("helper_id", intern_name)
                    route_msg["machine_helper"] = True
            machine_id = entry.get("machine_id", "") if entry else ""
            if entry and entry.get("project"):
                route_msg["project"] = entry.get("project")

            sent = False
            route_error = ""
            if machine_id:
                sent = relay_ws_server.send_to_machine(machine_id, route_msg)
                if not sent:
                    route_error = f"machine '{machine_id}' is not connected"
                    log.warning(
                        f"[CARD_CALLBACK] Failed to forward to machine '{machine_id}' "
                        f"intern={route_msg.get('intern_name', intern_name)} "
                        f"project={route_msg.get('project') or '-'} question_id={question_id or '-'}"
                    )
                else:
                    log.info(
                        f"[CARD_CALLBACK] Forwarded to machine '{machine_id}' "
                        f"intern={route_msg.get('intern_name', intern_name)} "
                        f"project={route_msg.get('project') or '-'} question_id={question_id or '-'}"
                    )
            else:
                route_error = "machine route not found"
                log.warning(
                    f"[CARD_CALLBACK] No machine for intern '{intern_name}' "
                    f"project={route_msg.get('project') or '-'} question_id={question_id or '-'}"
                )

            resp = P2CardActionTriggerResponse()
            resp.toast = CallBackToast()
            card_elements = []
            if question_title:
                card_elements.append({"tag": "div", "text": {
                    "tag": "lark_md",
                    "content": f"**{question_title}**"
                }})
            if sent:
                resp.toast.type = "success"
                resp.toast.i18n = {
                    "zh_cn": "回答已提交，等待 daemon 校验",
                    "en_us": "Answer submitted; waiting for daemon validation",
                }
                card_elements.append({"tag": "div", "text": {
                    "tag": "plain_text",
                    "content": f"已提交回答: {answer_display}"
                }})
                card_elements.append({"tag": "note", "elements": [{
                    "tag": "plain_text",
                    "content": "daemon 校验通过后会把卡片更新为已回答；若问题已失效，会更新为失败原因。",
                }]})
                header_template = "blue"
                header_title = f"⏳ {intern_name} 的回答已提交"
            else:
                resp.toast.type = "error"
                resp.toast.i18n = {
                    "zh_cn": "回答未提交，请稍后重试",
                    "en_us": "Answer was not submitted; please retry later",
                }
                card_elements.append({"tag": "div", "text": {
                    "tag": "plain_text",
                    "content": f"回答未提交: {answer_display}"
                }})
                card_elements.append({"tag": "div", "text": {
                    "tag": "plain_text",
                    "content": f"原因: {route_error}"
                }})
                header_template = "red"
                header_title = f"⚠️ {intern_name} 的回答未提交"

            resp.card = CallBackCard()
            resp.card.type = "raw"
            resp.card.data = {
                "header": {
                    "template": header_template,
                    "title": {"tag": "plain_text", "content": header_title}
                },
                "elements": card_elements
            }
            return resp

        except Exception as e:
            log.error(f"[CARD_CALLBACK] Error: {e}", exc_info=True)
            resp = P2CardActionTriggerResponse()
            resp.toast = CallBackToast()
            resp.toast.type = "error"
            resp.toast.i18n = {"zh_cn": "处理失败", "en_us": "Processing failed"}
            return resp

    return handle_card_action


def _is_at_bot(mentions, bot_open_id):
    """task252: True iff any mention's id.open_id matches bot_open_id."""
    if not mentions or not bot_open_id:
        return False
    for m in mentions:
        mid = getattr(m, "id", None)
        if mid and getattr(mid, "open_id", "") == bot_open_id:
            return True
    return False


def _resolve_sender_name(api, sender_open_id):
    """task252: best-effort sender_open_id → display name for at_only prefix.
    Falls back to last-8 of open_id on lookup failure (not "unknown" — gives
    operators something searchable in logs without leaking the full open_id)."""
    if not sender_open_id:
        return "unknown"
    info, _err = api.get_user_info(sender_open_id)
    if info and info.get("name"):
        return info["name"]
    return sender_open_id[-8:] if len(sender_open_id) >= 8 else sender_open_id


def _resolve_mention_placeholders(text, mentions):
    """task263: replace feishu @_user_N placeholders with @<name>.

    Feishu delivers message text with opaque keys (e.g. "@_user_1 @_user_2 测试")
    and a parallel mentions[] carrying real names. Without rewriting the text,
    the intern downstream only sees placeholders and can't tell who was @-ed.
    Empty/missing inputs transparently pass through.
    """
    if not text or not mentions:
        return text
    for m in mentions:
        key = getattr(m, "key", None)
        name = getattr(m, "name", None)
        if not key or not name:
            continue
        text = text.replace(key, f"@{name}")
    return text


_TRAILING_MENTION_RE = re.compile(r"@\S+\s*$")


def _pad_trailing_mention(text):
    """task263: append a space if the message ends with @<token>.

    Claude Code TUI treats a trailing @<prefix> as a file-path completion
    trigger; when the message arrives via tmux send-keys, the input freezes
    on the completion menu and the message never gets sent. A single trailing
    space defuses the completion. Safe to apply to all routed text.
    """
    if not text:
        return text
    if _TRAILING_MENTION_RE.search(text) and not text.endswith(" "):
        return text + " "
    return text


# task263/task268: slash-commands are control instructions and, when they are
# known native intern commands, must bypass at_only @bot gating without adding
# a sender prefix. BUG_0036 adds a mapped-route unknown-slash guard before the
# at_only branch, so this helper remains a lightweight leading-slash detector
# rather than the final "will forward to machine" decision.


def _is_relay_passthrough_command(text):
    """task263: True iff text (after strip) begins with '/'."""
    if not text:
        return False
    return text.strip().startswith("/")


def _slash_command_token(text):
    stripped = (text or "").strip()
    if not stripped.startswith("/"):
        return ""
    return stripped.split()[0].lower()


def _native_slash_commands_for_type(intern_type):
    return _RELAY_NATIVE_SLASH_COMMANDS.get(intern_type or "", {})


def _is_known_native_slash_command(intern_type, text):
    cmd = _slash_command_token(text)
    return bool(cmd and cmd in _native_slash_commands_for_type(intern_type))


def _format_slash_routing_error(reason, command, intern_type="codex", detail=""):
    cmd = _slash_command_token(command) or (command or "").strip() or "<empty>"
    lines = [
        "⚠️ slash_routing_error",
        "schema: `intern-agents.slash-routing-error.v1`",
        f"reason: `{reason}`",
        f"command: `{cmd}`",
    ]
    if detail:
        lines.append(detail)
    if reason == "unknown_command":
        supported = _native_slash_commands_for_type(intern_type)
        lines.append("")
        lines.append("可用命令:")
        lines.append(format_available_slash_commands(intern_type or "codex", supported.items()))
    return "\n".join(lines)


def _reply_unmapped_group_slash(api, message_id, text):
    return _reply_message_with_log(
        api,
        message_id,
        _format_slash_routing_error(
            "chat_not_registered",
            text,
            detail="当前群未绑定 intern/helper；请进入已创建的 intern 群，或在主入口私聊使用 `/help`。"),
        "SLASH",
    )


def _reply_mapped_unknown_slash(api, message_id, text, intern_type):
    return _reply_message_with_log(
        api,
        message_id,
        _format_slash_routing_error(
            "unknown_command",
            text,
            intern_type=intern_type,
            detail="未知 slash 已由 relay 拦截，未转发到 intern 机器。"),
        "SLASH",
    )


# task259/task375: render bot-owned config into the Feishu chat description.
# Everything after the first "\n---\n" is user-owned and preserved across
# config updates. task375 expands the old trigger_mode-only line into a compact
# full config block.
_BOT_DESC_PREFIX = "🤖 Intern config:"
_LEGACY_TRIGGER_DESC_PREFIX = "🤖 trigger_mode:"
_BOT_DESC_SEP = "\n---\n"


def _split_chat_description(current):
    """Return user-owned description text after stripping the bot-owned block.

    Supports both task375's full-config prefix and task259's legacy
    trigger_mode-only prefix so existing group descriptions migrate in place.
    """
    if not current:
        return ""
    if not (
        current.startswith(_BOT_DESC_PREFIX)
        or current.startswith(_LEGACY_TRIGGER_DESC_PREFIX)
    ):
        return current
    idx = current.find(_BOT_DESC_SEP)
    if idx < 0:
        return ""
    return current[idx + len(_BOT_DESC_SEP):]


def _format_chat_config_description(snapshot):
    return "\n".join([
        _BOT_DESC_PREFIX,
        f"trigger_mode={snapshot['trigger_mode']}",
        f"detail_mode={snapshot['detail_mode']}",
        f"no_collapse_mode={snapshot['no_collapse_mode']}",
    ])


def _render_chat_description(current, snapshot):
    """Compute the group description for `snapshot`, preserving user content.

    Returns the new description string. Caller is responsible for comparing
    against `current` and skipping the PATCH on no-op (task259 supervisor
    requirement: do not write when nothing would change).
    """
    user_part = _split_chat_description(current)
    bot_block = _format_chat_config_description(snapshot)
    if not user_part:
        return bot_block
    return bot_block + _BOT_DESC_SEP + user_part


def _short_config_sync_error(error):
    return str(error or "").replace("`", "'")[:180]


def _collect_config_snapshot(chat_id, relay_ws, detail_mode=None,
                             no_collapse_mode=None):
    snapshot = {
        "trigger_mode": chat_config.get_trigger_mode(chat_id),
    }
    if detail_mode is None:
        result, error = relay_ws.detail_mode_request(chat_id, op="get")
        if error:
            return None, f"无法读取 detail_mode — {_detail_mode_error_text(error)}"
        detail_mode = result["mode"]
    if no_collapse_mode is None:
        result, error = relay_ws.no_collapse_mode_request(chat_id, op="get")
        if error:
            return None, (
                "无法读取 no_collapse_mode — "
                f"{_no_collapse_mode_error_text(error)}")
        no_collapse_mode = result["mode"]
    snapshot["detail_mode"] = detail_mode
    snapshot["no_collapse_mode"] = no_collapse_mode
    return snapshot, None


def _sync_chat_config_description(api, chat_id, snapshot):
    """Synchronize the Feishu group description with the current config.

    Returns None on success, or an error string that callers must surface to
    the supervisor. The config stores remain the truth source, but task375
    requires description sync failures to be visible.
    """
    if api is None:
        return "Feishu API unavailable"
    info, err = api.get_chat_info(chat_id)
    if err:
        log.warning(f"[CHAT_DESC] get_chat_info failed chat={chat_id}: {err}")
        return f"get_chat_info failed: {err}"
    current = (info or {}).get("description") or ""
    new = _render_chat_description(current, snapshot)
    if new == current:
        log.info(f"[CHAT_DESC] chat={chat_id} config description unchanged, skip patch")
        return None
    err = api.update_chat(chat_id, description=new)
    if err:
        log.warning(f"[CHAT_DESC] update_chat description failed chat={chat_id}: {err}")
        return f"update_chat failed: {err}"
    log.info(f"[CHAT_DESC] chat={chat_id} config description patched "
             f"({len(current)} → {len(new)} chars)")
    return None


def _apply_chat_description_for_mode(api, chat_id, mode):
    """Backward-compatible task259 helper for tests/older callers.

    New task375 paths use _sync_chat_config_description() with a full snapshot.
    If an older caller still invokes this helper, keep the original
    trigger_mode-only behavior instead of writing placeholder fields.
    """
    if api is None:
        return "Feishu API unavailable"
    info, err = api.get_chat_info(chat_id)
    if err:
        log.warning(f"[CHAT_DESC] get_chat_info failed chat={chat_id}: {err}")
        return f"get_chat_info failed: {err}"
    current = (info or {}).get("description") or ""
    user_part = _split_chat_description(current)
    if mode == "all":
        new = user_part
    elif user_part:
        new = f"{_LEGACY_TRIGGER_DESC_PREFIX} {mode}{_BOT_DESC_SEP}{user_part}"
    else:
        new = f"{_LEGACY_TRIGGER_DESC_PREFIX} {mode}"
    if new == current:
        return None
    err = api.update_chat(chat_id, description=new)
    if err:
        log.warning(f"[CHAT_DESC] update_chat description failed chat={chat_id}: {err}")
        return f"update_chat failed: {err}"
    return None


def _handle_trigger_mode_command(api, chat_id, text, message_id, current_mode,
                                 relay_ws):
    """task252: /trigger_mode [all|at_only] — read-or-set the chat's trigger mode.

    Relay-level command: intercepted before forwarding to the intern's daemon,
    so it never reaches the intern's tmux. No-arg form replies with current.
    """
    parts = text.split(None, 1)
    if len(parts) < 2:
        api.reply_message(
            message_id,
            f"当前 trigger_mode: `{current_mode}`。用法：/trigger_mode all | at_only")
        return
    arg = parts[1].strip().lower()
    if arg in ("all", "all_trigger"):
        new_mode = "all"
    elif arg in ("at", "at_only", "at-only", "at_trigger"):
        new_mode = "at_only"
    else:
        api.reply_message(
            message_id,
            f"⚠️ 未知 trigger_mode `{arg}`；可选：all / at_only")
        return
    try:
        changed = chat_config.set_trigger_mode(chat_id, new_mode)
    except Exception as e:
        api.reply_message(message_id, f"⚠️ 切换失败：{e}")
        log.error(f"[TRIGGER] chat={chat_id} set_trigger_mode failed: {e}", exc_info=True)
        return
    snapshot, snapshot_error = _collect_config_snapshot(chat_id, relay_ws)
    desc_error = None
    if snapshot_error:
        desc_error = snapshot_error
    else:
        desc_error = _sync_chat_config_description(api, chat_id, snapshot)
    reply = f"✅ trigger_mode → `{new_mode}`"
    if desc_error:
        reply += f"\n⚠️ 群描述同步失败：{_short_config_sync_error(desc_error)}"
    else:
        reply += "\n✅ 群描述已同步"
    api.reply_message(message_id, reply)
    log.info(f"[TRIGGER] chat={chat_id} trigger_mode {current_mode} → {new_mode} "
             f"changed={changed} desc_error={desc_error!r}")


def _detail_mode_error_text(error):
    """task283: map detail_mode_request error strings to a Feishu-friendly
    reply for the supervisor. Errors come from `RelayWSServer.detail_mode_request`."""
    if error == "unknown_chat":
        return "⚠️ 群未注册到 relay；请先在 daemon 机器上把 intern 跑起来"
    if error == "daemon_offline":
        return "⚠️ intern 所在 daemon 已离线，无法读写 detail_mode"
    if error == "daemon_outdated":
        return ("⚠️ intern 所在 daemon 版本过旧（缺 detail_mode capability），"
                "需要主管在那台机器升级 VSIX 后再试")
    if error == "send_failed":
        return "⚠️ relay 向 daemon 发送失败，连接可能正在断开，请稍后重试"
    if error == "timeout":
        return "⚠️ daemon 10s 内未响应 detail_mode 请求"
    return f"⚠️ 设置失败：{error}"


def _no_collapse_mode_error_text(error):
    """task373: map no_collapse_mode_request errors to Feishu-friendly text."""
    if error == "unknown_chat":
        return "⚠️ 群未注册到 relay；请先在 daemon 机器上把 intern 跑起来"
    if error == "daemon_offline":
        return "⚠️ intern 所在 daemon 已离线，无法读写 no_collapse_mode"
    if error == "daemon_outdated":
        return ("⚠️ intern 所在 daemon 版本过旧（缺 no_collapse_mode capability），"
                "需要主管在那台机器升级 VSIX 后再试")
    if error == "send_failed":
        return "⚠️ relay 向 daemon 发送失败，连接可能正在断开，请重试"
    if error == "timeout":
        return "⚠️ daemon 10s 内未响应 no_collapse_mode 请求"
    return f"⚠️ 设置失败：{error}"


def _handle_detail_mode_command(api, chat_id, text, message_id, relay_ws):
    """task258 + task283: /detail_mode [full|summary] — read-or-set the chat's
    in-progress Feishu detail level. Truth source is the owning daemon's
    daemon-local store; relay just proxies via WS RPC (`detail_mode_get` /
    `detail_mode_set`). See `RelayWSServer.detail_mode_request`.

    full   = current behavior (every tool call + assistant text appended).
    summary = suppress noisy tools (Bash/Read/Write/Edit/Grep/list_dir/Web*),
              keep stage signals (SubAgent/AskUser/Todo*/Task*) + assistant text.
    Affects only in-progress UPDATE; final Stop reply unchanged.
    """
    parts = text.split(None, 1)

    # /detail_mode without args — query current value via RPC.
    if len(parts) < 2:
        result, error = relay_ws.detail_mode_request(chat_id, op="get")
        if error:
            api.reply_message(message_id, _detail_mode_error_text(error))
            return
        api.reply_message(
            message_id,
            f"当前 detail_mode: `{result['mode']}`。用法：/detail_mode full | summary")
        return

    arg = parts[1].strip().lower()
    if arg in ("full", "all", "verbose"):
        new_mode = "full"
    elif arg in ("summary", "minimal", "quiet"):
        new_mode = "summary"
    else:
        api.reply_message(
            message_id,
            f"⚠️ 未知 detail_mode `{arg}`；可选：full / summary")
        return

    result, error = relay_ws.detail_mode_request(chat_id, op="set", mode=new_mode)
    if error:
        api.reply_message(message_id, _detail_mode_error_text(error))
        log.error(f"[DETAIL] chat={chat_id} set_detail_mode RPC failed: {error}")
        return
    snapshot, snapshot_error = _collect_config_snapshot(
        chat_id, relay_ws, detail_mode=result.get("mode") or new_mode)
    desc_error = snapshot_error
    if not desc_error:
        desc_error = _sync_chat_config_description(api, chat_id, snapshot)
    reply = f"✅ detail_mode → `{new_mode}`"
    if desc_error:
        reply += f"\n⚠️ 群描述同步失败：{_short_config_sync_error(desc_error)}"
    else:
        reply += "\n✅ 群描述已同步"
    api.reply_message(message_id, reply)
    log.info(f"[DETAIL] chat={chat_id} detail_mode set via RPC → {new_mode} "
             f"changed={result.get('changed')} desc_error={desc_error!r}")


# task258: marker placed in card.value so handle_card_action can recognize a
# /config submit and short-circuit instead of routing the answer to an intern.
_CONFIG_CARD_ACTION = "config_submit"
_CONFIG_CANCEL_ACTION = "config_cancel"
_CONFIG_OPERATION_SAVE = "save"
_CONFIG_OPERATION_CANCEL = "cancel"
_CONFIG_FORM_FIELDS = {"trigger_mode", "detail_mode", "no_collapse_mode"}
_CONFIG_CARD_INVALIDATED_LIMIT = 2048
_CONFIG_CARD_INVALIDATED = OrderedDict()
_CONFIG_CARD_INVALIDATED_LOCK = threading.Lock()


def _new_config_card_token():
    return _uuid.uuid4().hex


def _decode_config_submit_value(value):
    """Return a button value dict from Feishu form submit payload variants."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, list):
        for item in value:
            decoded = _decode_config_submit_value(item)
            if decoded:
                return decoded
        return {}
    if not isinstance(value, str):
        return {}

    text = value.strip()
    if not text:
        return {}
    if text.startswith("{"):
        try:
            decoded = json.loads(text)
        except Exception:
            decoded = None
        if isinstance(decoded, dict):
            return decoded
    operation = text.lower()
    if operation in {_CONFIG_OPERATION_SAVE, _CONFIG_OPERATION_CANCEL}:
        return {"config_operation": operation}
    return {}


def _config_submit_value(form_value):
    if not isinstance(form_value, dict):
        return {}

    for key in ("submit", "button", "action"):
        decoded = _decode_config_submit_value(form_value.get(key))
        if decoded:
            return decoded

    if (
        form_value.get("config_action") in {
            _CONFIG_CARD_ACTION,
            _CONFIG_CANCEL_ACTION,
        }
        or form_value.get("config_operation") in {
            _CONFIG_OPERATION_SAVE,
            _CONFIG_OPERATION_CANCEL,
        }
    ):
        return {
            key: value
            for key, value in form_value.items()
            if key not in _CONFIG_FORM_FIELDS
        }
    return {}


def _normalize_config_callback_value(value, form_value):
    base_value = dict(value) if isinstance(value, dict) else {}
    submit_value = _config_submit_value(form_value)
    if not submit_value:
        return base_value
    if (
        base_value.get("config_action") not in {
            _CONFIG_CARD_ACTION,
            _CONFIG_CANCEL_ACTION,
        }
        and submit_value.get("config_action") not in {
            _CONFIG_CARD_ACTION,
            _CONFIG_CANCEL_ACTION,
        }
    ):
        return base_value

    merged = dict(base_value)
    merged.update(submit_value)
    return merged


def _config_form_values(form_value):
    if not isinstance(form_value, dict):
        return {}
    return {
        key: value
        for key, value in form_value.items()
        if key in _CONFIG_FORM_FIELDS
    }


def _config_card_state_key(value):
    if not isinstance(value, dict):
        return ""
    token = str(value.get("config_card_token") or "").strip()
    if token:
        return f"token:{token}"

    # Compatibility fallback for cards rendered before config_card_token
    # existed. The cancel and save buttons from the same legacy card carry the
    # same chat/operator/snapshot tuple even though their operations differ.
    snapshot = value.get("snapshot") if isinstance(value.get("snapshot"), dict) else {}
    chat_id = str(value.get("chat_id") or "").strip()
    operator_open_id = str(value.get("operator_open_id") or "").strip()
    if not chat_id or not operator_open_id:
        return ""
    payload = {
        "chat_id": chat_id,
        "operator_open_id": operator_open_id,
        "snapshot": {
            "trigger_mode": snapshot.get("trigger_mode") or "",
            "detail_mode": snapshot.get("detail_mode") or "",
            "no_collapse_mode": snapshot.get("no_collapse_mode") or "",
        },
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()
    return f"legacy:{digest}"


def _invalidate_config_card_state(value):
    key = _config_card_state_key(value)
    if not key:
        return False
    with _CONFIG_CARD_INVALIDATED_LOCK:
        _CONFIG_CARD_INVALIDATED[key] = time.time()
        _CONFIG_CARD_INVALIDATED.move_to_end(key)
        while len(_CONFIG_CARD_INVALIDATED) > _CONFIG_CARD_INVALIDATED_LIMIT:
            _CONFIG_CARD_INVALIDATED.popitem(last=False)
    return True


def _is_config_card_state_invalidated(value):
    key = _config_card_state_key(value)
    if not key:
        return False
    with _CONFIG_CARD_INVALIDATED_LOCK:
        return key in _CONFIG_CARD_INVALIDATED


def _config_snapshot_from_action_value(value, chat_id):
    snapshot = value.get("snapshot") if isinstance(value.get("snapshot"), dict) else {}
    return {
        "trigger_mode": snapshot.get("trigger_mode") or chat_config.get_trigger_mode(chat_id),
        "detail_mode": snapshot.get("detail_mode") or "?",
        "no_collapse_mode": snapshot.get("no_collapse_mode") or "?",
    }


def _build_config_card(chat_id, snapshot, operator_open_id, operator_name):
    """task258: build the /config interactive card payload.

    Single-form card: shows current values + select inputs for trigger_mode,
    detail_mode, and no_collapse_mode + a 保存 button. The form wrapper is critical — placing
    select_static in a standalone `tag:"action"` container makes every
    selection fire card.action.trigger immediately (without our sentinel),
    so handle_card_action would report "回调数据异常" on each pick
    (task258_followup). Inside a `tag:"form"` the selects are inert until
    the button (action_type=form_submit) collects all values into
    `form_value` and fires once.

    On submit the relay receives
    `form_value = {trigger_mode, detail_mode, no_collapse_mode}`
    plus `value.config_action == _CONFIG_CARD_ACTION` and writes back both
    fields. No-op writes are still acked so the supervisor sees confirmation.

    task281: card is shared (config.update_multi=True) so submit-time updates
    propagate to every group member's view; operator_open_id is embedded in
    the button value so the callback can enforce sender-only-save by comparing
    against event.operator.open_id.
    """
    trigger_mode = snapshot.get("trigger_mode", "all")
    detail_mode = snapshot.get("detail_mode", "full")
    no_collapse_mode = snapshot.get("no_collapse_mode", "on")
    card_token = _new_config_card_token()

    base_value = {
        "config_action": _CONFIG_CARD_ACTION,
        "chat_id": chat_id,
        "config_card_token": card_token,
        "operator_open_id": operator_open_id,
        "operator_name": operator_name,
        "snapshot": {
            "trigger_mode": trigger_mode,
            "detail_mode": detail_mode,
            "no_collapse_mode": no_collapse_mode,
        },
    }
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "blue",
            "title": {"tag": "plain_text", "content": "⚙️ Intern 群配置"},
        },
        "elements": [
            {"tag": "div", "text": {
                "tag": "lark_md",
                "content": (
                    f"**当前**：trigger_mode = `{trigger_mode}`，"
                    f"detail_mode = `{detail_mode}`，"
                    f"no_collapse_mode = `{no_collapse_mode}`"
                )}},
            {"tag": "note", "elements": [{"tag": "plain_text",
                "content": f"🔒 仅 {operator_name} 可保存配置"}]},
            {"tag": "hr"},
            {"tag": "div", "text": {
                "tag": "lark_md",
                "content": (
                    "**Trigger Mode** — 群消息是否需要 @bot 才触发 intern\n"
                    "  • `all`：所有非 app 消息都转发（单督导）\n"
                    "  • `at_only`：仅 @bot 的消息转发（多督导）"
                )}},
            {"tag": "div", "text": {
                "tag": "lark_md",
                "content": (
                    "\n**Detail Mode** — 处理中飞书消息的明细级别\n"
                    "  • `full`：转发全部工具调用 + 中间文本（默认）\n"
                    "  • `summary`：仅保留阶段信号（SubAgent / AskUser / Todo / Task）"
                    "+ 中间文本，屏蔽 Bash/Read/Write/Edit/Grep/Web 噪声"
                )}},
            {"tag": "div", "text": {
                "tag": "lark_md",
                "content": (
                    "\n**No Collapse Mode** — 避免飞书把长消息折叠\n"
                    "  • `on`：按实测 70 行阈值提前切换到新消息（默认）\n"
                    "  • `off`：保持长消息行为，允许飞书折叠"
                )}},
            {"tag": "hr"},
            {
                "tag": "form",
                "name": "config_form",
                "elements": [
                    {
                        "tag": "select_static",
                        "name": "trigger_mode",
                        "placeholder": {"tag": "plain_text",
                                        "content": "选择 trigger_mode"},
                        "initial_option": trigger_mode,
                        "options": [
                            {"text": {"tag": "plain_text",
                                      "content": "all（所有消息触发）"},
                             "value": "all"},
                            {"text": {"tag": "plain_text",
                                      "content": "at_only（仅 @bot 触发）"},
                             "value": "at_only"},
                        ],
                    },
                    {
                        "tag": "select_static",
                        "name": "detail_mode",
                        "placeholder": {"tag": "plain_text",
                                        "content": "选择 detail_mode"},
                        "initial_option": detail_mode,
                        "options": [
                            {"text": {"tag": "plain_text",
                                      "content": "full（转发全部明细）"},
                             "value": "full"},
                            {"text": {"tag": "plain_text",
                                      "content": "summary（仅阶段信号）"},
                             "value": "summary"},
                        ],
                    },
                    {
                        "tag": "select_static",
                        "name": "no_collapse_mode",
                        "placeholder": {"tag": "plain_text",
                                        "content": "选择 no_collapse_mode"},
                        "initial_option": no_collapse_mode,
                        "options": [
                            {"text": {"tag": "plain_text",
                                      "content": "off（允许长消息折叠）"},
                             "value": "off"},
                            {"text": {"tag": "plain_text",
                                      "content": "on（超过 70 行切新消息）"},
                             "value": "on"},
                        ],
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "💾 保存"},
                        "type": "primary",
                        "action_type": "form_submit",
                        "name": "save",
                        "value": {**base_value, "config_operation": _CONFIG_OPERATION_SAVE},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": "✖️ 取消"},
                        "type": "default",
                        "action_type": "form_submit",
                        "name": "cancel",
                        "value": {
                            **base_value,
                            "config_action": _CONFIG_CANCEL_ACTION,
                            "config_operation": _CONFIG_OPERATION_CANCEL,
                        },
                    },
                ],
            },
        ],
    }


def _build_config_result_card(chat_id, snapshot, changed, operator_name,
                              description_error=None):
    """task258: render the post-submit card. Shows ✅ for actually-changed
    fields so the supervisor can confirm at a glance.

    task281: shared card (update_multi=True) so every group member sees the
    submit result, not just the clicker; footer credits operator_name so the
    audit trail is visible to all observers.
    """
    lines = [
        ("✅ " if changed.get("trigger_mode") else "• ") +
        f"trigger_mode = `{snapshot['trigger_mode']}`",
        ("✅ " if changed.get("detail_mode") else "• ") +
        f"detail_mode = `{snapshot['detail_mode']}`",
        ("✅ " if changed.get("no_collapse_mode") else "• ") +
        f"no_collapse_mode = `{snapshot['no_collapse_mode']}`",
    ]
    if description_error:
        lines.append(
            "⚠️ group_description sync_failed: "
            f"`{_short_config_sync_error(description_error)}`")
    else:
        lines.append("✅ group_description = `synced`")
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "green",
            "title": {"tag": "plain_text", "content": "⚙️ 配置已保存"},
        },
        "elements": [
            {"tag": "div", "text": {
                "tag": "lark_md",
                "content": "\n".join(lines),
            }},
            {"tag": "note", "elements": [{"tag": "plain_text",
                "content": f"由 {operator_name} 保存 · 再次发送 /config 即可修改"}]},
        ],
    }


def _build_config_cancel_card(chat_id, snapshot, operator_name):
    return {
        "config": {"wide_screen_mode": True, "update_multi": True},
        "header": {
            "template": "grey",
            "title": {"tag": "plain_text", "content": "⚙️ 配置已取消"},
        },
        "elements": [
            {"tag": "div", "text": {
                "tag": "lark_md",
                "content": "\n".join([
                    f"trigger_mode = `{snapshot['trigger_mode']}`",
                    f"detail_mode = `{snapshot['detail_mode']}`",
                    f"no_collapse_mode = `{snapshot['no_collapse_mode']}`",
                    "本次没有保存任何变更。",
                ]),
            }},
            {"tag": "note", "elements": [{"tag": "plain_text",
                "content": f"由 {operator_name} 取消 · 再次发送 /config 即可修改"}]},
        ],
    }


def _handle_config_command(api, chat_id, message_id, sender_open_id, sender_name, relay_ws):
    """task258 + task283: /config — send the interactive config card.

    The supervisor types /config in the chat; relay intercepts (never forwards
    to the intern) and replies with a card showing current trigger_mode (read
    locally from relay's chat_config.json) + detail_mode (read from owning
    daemon via WS RPC) + form selectors + save button. Submission is handled
    in create_card_callback_handler via the _CONFIG_CARD_ACTION sentinel.

    task281: sender_open_id/sender_name bind the card to the originator —
    the callback handler rejects submits where event.operator.open_id differs,
    and the card body shows "仅 {sender_name} 可保存" so other group members
    know clicking does nothing.

    task283/task373: detail_mode and no_collapse_mode are fetched via RPC. If
    an RPC fails (daemon offline / outdated / timeout) we surface a
    reply_message error rather than rendering a card with a misleading
    placeholder — project rule 6 forbids the silent-fallback path.
    """
    trigger_mode = chat_config.get_trigger_mode(chat_id)
    result, error = relay_ws.detail_mode_request(chat_id, op="get")
    if error:
        api.reply_message(
            message_id,
            f"/config 失败：无法读取 detail_mode — {_detail_mode_error_text(error)}")
        log.warning(f"[CONFIG] chat={chat_id} card render aborted: detail_mode RPC error={error}")
        return
    no_collapse_result, no_collapse_error = relay_ws.no_collapse_mode_request(
        chat_id, op="get")
    if no_collapse_error:
        api.reply_message(
            message_id,
            f"/config 失败：无法读取 no_collapse_mode — "
            f"{_no_collapse_mode_error_text(no_collapse_error)}")
        log.warning(
            f"[CONFIG] chat={chat_id} card render aborted: "
            f"no_collapse_mode RPC error={no_collapse_error}")
        return
    snapshot = {
        "trigger_mode": trigger_mode,
        "detail_mode": result["mode"],
        "no_collapse_mode": no_collapse_result["mode"],
    }
    card = _build_config_card(chat_id, snapshot, sender_open_id, sender_name)
    msg_id, err = api.send_interactive_card(chat_id, card)
    if err:
        log.error(f"[CONFIG] chat={chat_id} send card failed: {err}")
        api.reply_message(message_id, f"⚠️ /config 失败：{err}")
        return
    log.info(f"[CONFIG] chat={chat_id} card sent msg={msg_id} snapshot={snapshot}")


def _handle_config_card_submit(chat_id, form_value, relay_ws):
    """task258 + task283 + task373: process /config card submission.

    form_value is a dict with {trigger_mode, detail_mode, no_collapse_mode}
    (and possibly `submit` key from the button). Writes trigger_mode locally
    and daemon-owned modes via daemon RPC.

    Returns (updated_snapshot, changed_dict, description_error). changed_dict
    keys may be missing if the corresponding write was skipped due to invalid
    form value or RPC error. For daemon RPC errors, changed[...] is False and
    the snapshot reflects the pre-submit value so the result card stays honest.
    """
    changed = {}
    snapshot = {}

    # ── trigger_mode (relay-local; unchanged from task258) ───────────────
    tm = form_value.get("trigger_mode")
    if tm in chat_config.valid_modes():
        try:
            changed["trigger_mode"] = chat_config.set_trigger_mode(chat_id, tm)
        except Exception as e:
            log.error(f"[CONFIG] chat={chat_id} set_trigger_mode failed: {e}", exc_info=True)
            changed["trigger_mode"] = False
    snapshot["trigger_mode"] = chat_config.get_trigger_mode(chat_id)

    # ── detail_mode (daemon RPC; task283) ────────────────────────────────
    dm = form_value.get("detail_mode")
    detail_mode_value = None
    if dm in ("full", "summary"):
        result, error = relay_ws.detail_mode_request(chat_id, op="set", mode=dm)
        if error:
            log.error(f"[CONFIG] chat={chat_id} set_detail_mode RPC failed: {error}")
            changed["detail_mode"] = False
        else:
            changed["detail_mode"] = bool(result.get("changed"))
            detail_mode_value = result.get("mode")
    # If submit didn't include detail_mode or RPC failed, fetch current via RPC
    # so the snapshot card shows the truth — don't display a stale form value.
    if detail_mode_value is None:
        result, error = relay_ws.detail_mode_request(chat_id, op="get")
        detail_mode_value = result["mode"] if not error else "?"
    snapshot["detail_mode"] = detail_mode_value

    # ── no_collapse_mode (daemon RPC; task373) ──────────────────────────
    ncm = form_value.get("no_collapse_mode")
    no_collapse_mode_value = None
    if ncm in ("off", "on"):
        result, error = relay_ws.no_collapse_mode_request(
            chat_id, op="set", mode=ncm)
        if error:
            log.error(f"[CONFIG] chat={chat_id} set_no_collapse_mode RPC failed: {error}")
            changed["no_collapse_mode"] = False
        else:
            changed["no_collapse_mode"] = bool(result.get("changed"))
            no_collapse_mode_value = result.get("mode")
    if no_collapse_mode_value is None:
        result, error = relay_ws.no_collapse_mode_request(chat_id, op="get")
        no_collapse_mode_value = result["mode"] if not error else "?"
    snapshot["no_collapse_mode"] = no_collapse_mode_value

    description_error = _sync_chat_config_description(
        _get_api_for_callback(), chat_id, snapshot)

    log.info(f"[CONFIG] chat={chat_id} submit → {snapshot} changed={changed} "
             f"desc_error={description_error!r}")
    return snapshot, changed, description_error


# Lazy holder so _handle_config_card_submit (called from callback context with
# no direct api ref) can still trigger description updates. Set by main().
_api_singleton = None


def _set_api_for_callback(api):
    global _api_singleton
    _api_singleton = api


def _get_api_for_callback():
    return _api_singleton


def create_message_handler(api, registry, relay_ws_server, helper_policy=None, machine_config_schema=None):
    start_time_ms = str(int(time.time() * 1000))

    def handle_message(data):
        global _feishu_msg_count, _feishu_last_msg_time
        global _feishu_im_message_count, _feishu_im_message_last_time
        try:
            # task249: 飞书重投 dedup。早于任何副作用（_feishu_msg_count 增量、
            # 附件下载、tmux 注入），同 event_id 重投直接 drop。
            if _check_and_record_event(_extract_event_id(data), "im_msg"):
                return

            msg = data.event.message
            sender = data.event.sender
            chat_id = msg.chat_id
            message_id = msg.message_id
            msg_type = msg.message_type
            content = msg.content
            chat_type = _message_chat_type(msg)

            _feishu_msg_count += 1
            _feishu_last_msg_time = time.time()
            _feishu_im_message_count += 1
            _feishu_im_message_last_time = _feishu_last_msg_time
            sender_type = sender.sender_type if sender else "unknown"
            _relay_metrics.record(f"feishu:message:{msg_type}")
            log.info(f"[FEISHU_WS] Incoming #{_feishu_msg_count}: chat={chat_id}, sender_type={sender_type}, msg_type={msg_type}, message_id={message_id}")

            # Ignore bot's own messages
            if sender and sender.sender_type == "app":
                return

            # Ignore messages before daemon start (SDK may deliver backlog)
            create_time = getattr(msg, 'create_time', '') or ''
            if create_time and create_time < start_time_ms:
                log.info(f"Ignoring old message {message_id} (create_time={create_time} < start={start_time_ms})")
                return

            text = parse_text(content, msg_type)
            text = _resolve_mention_placeholders(text, msg.mentions)
            atts_meta = extract_attachments(msg_type, content)
            if not text and not atts_meta:
                return

            sender_open_id = _sender_user_identifier(sender)
            if text and text.strip().startswith(RELAY_HELPER_COMMAND):
                _relay_metrics.record(f"slash:relay:{RELAY_HELPER_COMMAND}")
                if _handle_helper_command(
                        api, registry, relay_ws_server, text.strip(), chat_id,
                        message_id, sender_open_id, helper_policy):
                    return
            if text and text.strip() == RELAY_MACHINE_CONFIG_COMMAND:
                sender_name = _resolve_sender_name(api, sender_open_id)
                if _handle_machine_config_command(
                        api, registry, relay_ws_server, chat_id, message_id,
                        sender_open_id, sender_name, helper_policy, machine_config_schema):
                    return

            # Route: chat_id → entry → machine_id → forward
            entry = registry.find_entry_by_chat(chat_id)
            helper_entry = None
            if not entry:
                helper_entry = registry.find_helper_by_chat(chat_id)
                if helper_entry:
                    helper_machine_id = helper_entry.get("machine_id", "")
                    entry = {
                        "machine_id": helper_machine_id,
                        "name": helper_entry.get("helper_id", ""),
                        "project": helper_entry.get("project") or (
                            _machine_helper_project_for_machine(helper_machine_id) if helper_machine_id else ""
                        ),
                        "type": helper_entry.get("runtime", "codex"),
                    }
                else:
                    if chat_type == "p2p":
                        _handle_unmapped_main_bot_message(api, registry, message_id, text)
                    elif text and _slash_command_token(text):
                        _reply_unmapped_group_slash(api, message_id, text)
                    else:
                        log.debug(f"No intern/helper for chat_id={chat_id}, ignoring")
                    return
            intern_name = entry.get("name") or registry.find_intern_by_chat(chat_id)

            machine_id = entry["machine_id"]

            if text and text.strip() == RELAY_UPGRADE_COMMAND:
                _relay_metrics.record(f"slash:relay:{RELAY_UPGRADE_COMMAND}")
                _handle_upgrade_command(
                    api, registry, relay_ws_server, chat_id, message_id,
                    sender_open_id, machine_id, helper_policy=helper_policy)
                return

            # task252: relay-level /trigger_mode command — intercept before
            # routing. Never forwarded to intern's tmux (it's a relay config
            # mutation, not an intern instruction).
            trigger_mode = chat_config.get_trigger_mode(chat_id)
            if text and text.strip().startswith(RELAY_TRIGGER_MODE_COMMAND):
                _relay_metrics.record(f"slash:relay:{RELAY_TRIGGER_MODE_COMMAND}")
                _handle_trigger_mode_command(
                    api, chat_id, text.strip(), message_id, trigger_mode,
                    relay_ws_server)
                return

            # task258: same pattern for /detail_mode and /config — relay-level
            # commands that mutate chat_config, never reach the intern.
            if text and text.strip().startswith(RELAY_DETAIL_MODE_COMMAND):
                _relay_metrics.record(f"slash:relay:{RELAY_DETAIL_MODE_COMMAND}")
                _handle_detail_mode_command(
                    api, chat_id, text.strip(), message_id, relay_ws_server)
                return
            if text and text.strip() == RELAY_CONFIG_COMMAND:
                _relay_metrics.record(f"slash:relay:{RELAY_CONFIG_COMMAND}")
                # task281: bind card to sender — only this user may submit.
                sender_open_id = _sender_user_identifier(sender)
                sender_name = _resolve_sender_name(api, sender_open_id)
                _handle_config_command(
                    api, chat_id, message_id, sender_open_id, sender_name, relay_ws_server)
                return

            if text and _slash_command_token(text) and not _is_known_native_slash_command(entry.get("type", "codex"), text):
                _reply_mapped_unknown_slash(api, message_id, text, entry.get("type", "codex"))
                return

            # task252: at_only mode — only @bot messages pass; others silent drop.
            # Sender display name is prepended only in at_only (multi-supervisor
            # group); all mode keeps the zero-change single-supervisor UX.
            # task263: known native relay-passthrough commands (e.g. /stop)
            # bypass the @bot gate so the supervisor can interrupt the intern
            # without first @-ing the bot. BUG_0036 has already rejected
            # mapped unknown slash commands above.
            # task268: passthrough 分支不拼 sender prefix —— Claude Code TUI 只把
            # 首字符 `/` 当 slash 命令触发器，`[from @...] /compact` 会被当成
            # 自然语言投给 LLM。slash 命令是控制指令，发起人身份不影响命令行为；
            # 多督导审计由 [TRIGGER] log 兜底。
            if trigger_mode == "at_only":
                if _is_relay_passthrough_command(text):
                    sender_open_id = _sender_user_identifier(sender)
                    sender_name = _resolve_sender_name(api, sender_open_id)
                    log.info(
                        f"[TRIGGER] chat={chat_id} at_only passthrough "
                        f"sender={sender_name} cmd={text[:40]!r}")
                else:
                    bot_open_id, bot_err = api.get_bot_open_id(chat_id)
                    if not bot_open_id:
                        log.warning(
                            f"[TRIGGER] chat={chat_id} at_only but bot_open_id "
                            f"unresolved ({bot_err}); silent drop message_id={message_id}")
                        return
                    if not _is_at_bot(msg.mentions, bot_open_id):
                        log.info(
                            f"[TRIGGER] chat={chat_id} at_only silent drop "
                            f"message_id={message_id}")
                        return
                    sender_open_id = _sender_user_identifier(sender)
                    sender_name = _resolve_sender_name(api, sender_open_id)
                    if text:
                        text = f"[from @{sender_name}] {text}"
                    log.info(
                        f"[TRIGGER] chat={chat_id} at_only passed sender={sender_name}")

            if text and _is_relay_passthrough_command(text):
                _relay_metrics.record(f"slash:passthrough:{text.strip().split()[0].lower()}")

            # task263: append a trailing space when text ends with @<token>
            # so Claude Code TUI doesn't capture it as a file-completion trigger.
            text = _pad_trailing_mention(text)

            log.info(f"[ROUTE] Feishu msg for '{intern_name}' → machine '{machine_id}': "
                     f"text={text[:80]!r} atts={len(atts_meta)}")

            # task228: feature gate attachments. Missing "attachments"
            # capability is rejected with an upgrade hint instead of silent drop.
            # text 仍走原路径；只对附件做门禁。
            attachments_payload = []
            if atts_meta:
                if not registry.has_capability(machine_id, "attachments"):
                    err = api.reply_message(
                        message_id,
                        f"⚠️ {intern_name} 所在机器的客户端尚未支持附件转发，请升级插件后重试")
                    if err:
                        log.error(f"[ROUTE] capability-missing reply failed: {err}")
                    if not text:
                        return
                    atts_meta = []  # 不再转发附件，但 text 继续走下去
                else:
                    try:
                        attachments_payload = _download_attachments(
                            api, message_id, intern_name, atts_meta)
                    except _AttachmentError as e:
                        api.reply_message(message_id, f"⚠️ 附件处理失败：{e}")
                        log.warning(f"[ROUTE] attachment error for {intern_name}: {e}")
                        # 项目规则 6：错误不隐藏；不把 text 单独转发，避免主管以为
                        # AI 看到了附件（reply_message 已经把失败原因告诉主管）。
                        return

            payload = {
                "type": "feishu_message",
                "intern_name": intern_name,
                "text": text,
                "message_id": message_id,
                "chat_id": chat_id,
                "project": entry.get("project", ""),
                "sender_id": _sender_user_identifier(sender),
            }
            if helper_entry:
                payload["machine_helper"] = True
            if attachments_payload:
                payload["attachments"] = attachments_payload

            sent, failure_reason, payload_bytes = _send_to_machine_with_reason(
                relay_ws_server,
                machine_id,
                payload,
            )

            if not sent:
                _reply_delivery_failure(
                    api,
                    message_id,
                    intern_name,
                    failure_reason,
                    payload_bytes,
                )
                log.warning(
                    f"[ROUTE] delivery to machine '{machine_id}' failed for "
                    f"{intern_name}: {failure_reason}")
                return

            # 纯附件消息（无 text）：daemon 只落盘+累积 pending，不唤醒 AI。
            # 由 relay 侧显式告知主管补 text 才会让 intern 查看。
            if attachments_payload and not text:
                filenames = ", ".join(a.get("filename", a.get("kind", "?")) for a in attachments_payload)
                err = api.reply_message(
                    message_id,
                    f"📎 已收到附件（{filenames}）。请再发一条 text 触发 {intern_name} 查看。")
                if err:
                    log.error(f"[ROUTE] attachment-only reply failed: {err}")

        except Exception as e:
            log.error(f"Message handler error: {e}", exc_info=True)

    return handle_message


def create_feishu_event_handlers(api, registry, relay_ws_server, enterprise_policy=None):
    helper_policy = helper_policy_from_enterprise_policy(enterprise_policy)
    return (
        create_message_handler(
            api, registry, relay_ws_server,
            helper_policy=helper_policy, machine_config_schema=None),
        create_card_callback_handler(
            api, registry, relay_ws_server,
            helper_policy=helper_policy, machine_config_schema=None),
    )


class FeishuRelayIngress:
    """Relay Feishu ingress surface shared by runtime and CI mock relay.

    CI mock relay subclasses this class and only replaces how Feishu events are
    constructed. Message/card routing remains owned by the real relay handlers.
    """

    def __init__(self, api, registry, relay_ws_server, enterprise_policy=None):
        self.api = api
        self.registry = registry
        self.relay_ws_server = relay_ws_server
        self.message_handler, self.card_handler = create_feishu_event_handlers(
            api,
            registry,
            relay_ws_server,
            enterprise_policy=enterprise_policy,
        )

    def handle_message_event(self, data):
        return self.message_handler(data)

    def handle_card_action_event(self, data):
        return self.card_handler(data)


# ══════════════════════════════════════════
# HTTP 监控 API
# ══════════════════════════════════════════

_registry = None
_workspace_registry = None
_api = None
_start_time = None
_feishu_ws_ok = False
_feishu_ws_client = None
_feishu_thread = None
_relay_ws = None
_log_transfers = {}  # request_id → {machine_id, status, local_path}
_root_dir = None  # WORK_AGENTS_ROOT, set in main()
_shutdown_event = None  # threading.Event(), set in main()


def _build_admin_load_snapshot():
    machines = _registry.get_machines_summary()
    daemons = {}
    total_peer_jobs = 0
    max_target_depth = 0
    for machine_id, info in machines.items():
        metrics = info.get("metrics") or {}
        peer_delivery = metrics.get("peer_delivery") or {}
        total_peer_jobs += int(peer_delivery.get("total_jobs") or 0)
        max_target_depth = max(max_target_depth, int(peer_delivery.get("max_target_depth") or 0))
        daemons[machine_id] = {
            "ws_connected": info.get("ws_connected", False),
            "resources": info.get("resources", {}),
            "resources_updated_at": info.get("resources_updated_at"),
            "warnings": info.get("warnings", []),
            "metrics": metrics,
            "metrics_updated_at": info.get("metrics_updated_at"),
        }
    return {
        "schema": "intern-agents.admin-load.v1",
        "generated_at": _now_iso(),
        "summary": {
            "machines": len(machines),
            "machines_connected": len([item for item in machines.values() if item.get("ws_connected")]),
            "daemon_peer_jobs": total_peer_jobs,
            "daemon_max_peer_target_depth": max_target_depth,
            "relay_threads": threading.active_count(),
            "relay_uptime_seconds": int(time.time() - _start_time) if _start_time else 0,
        },
        "relay": {
            "metrics": _relay_metrics.snapshot(),
            "threads": threading.active_count(),
        },
        "daemons": daemons,
    }


def _build_admin_analysis_snapshot():
    rows = []
    relay_snapshot = _relay_metrics.snapshot()
    for item in relay_snapshot.get("interfaces", []):
        row = dict(item)
        row.update({"source": "relay", "machine_id": ""})
        rows.append(row)

    machines = _registry.get_machines_summary()
    for machine_id, info in machines.items():
        runtime = ((info.get("metrics") or {}).get("runtime") or {})
        for item in runtime.get("interfaces", []):
            row = dict(item)
            row.update({"source": "daemon", "machine_id": machine_id})
            rows.append(row)

    rows.sort(key=lambda row: (-int(row.get("count") or 0), row.get("source", ""), row.get("machine_id", ""), row.get("key", "")))
    return {
        "schema": "intern-agents.admin-analysis.v1",
        "generated_at": _now_iso(),
        "interfaces": rows,
    }


class MonitorHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        log.debug(f"[HTTP] {format % args}")

    def _start_metric(self, method):
        parsed = urllib.parse.urlparse(self.path)
        self._metric_key = f"http:{method} {parsed.path}"
        self._metric_started_at = time.time()
        self._metric_recorded = False

    def _json_response(self, code, data):
        if getattr(self, "_metric_key", None) and not getattr(self, "_metric_recorded", False):
            elapsed_ms = int((time.time() - self._metric_started_at) * 1000)
            _relay_metrics.record(
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

    def _binary_response(self, code, data, *, content_type, headers=None):
        if getattr(self, "_metric_key", None) and not getattr(self, "_metric_recorded", False):
            elapsed_ms = int((time.time() - self._metric_started_at) * 1000)
            _relay_metrics.record(
                self._metric_key,
                elapsed_ms=elapsed_ms,
                status_code=code,
                error=code >= 400,
            )
            self._metric_recorded = True
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def _redirect_response(self, location, code=302):
        if getattr(self, "_metric_key", None) and not getattr(self, "_metric_recorded", False):
            elapsed_ms = int((time.time() - self._metric_started_at) * 1000)
            _relay_metrics.record(
                self._metric_key,
                elapsed_ms=elapsed_ms,
                status_code=code,
                error=False,
            )
            self._metric_recorded = True
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _relay_token(self):
        try:
            owner_path = os.fspath(relay_owner_path(_root_dir))
            with open(owner_path, "r", encoding="utf-8") as f:
                owner = json.load(f)
            return str(owner.get("relay_token") or "")
        except Exception:
            return ""

    def _authorized(self):
        expected = self._relay_token()
        if not expected:
            return False
        auth = self.headers.get("Authorization", "")
        supplied = ""
        if auth.startswith("Bearer "):
            supplied = auth[len("Bearer "):].strip()
        if not supplied:
            parsed = urllib.parse.urlparse(self.path)
            supplied = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
        return hmac.compare_digest(supplied, expected)

    def _require_authorized(self):
        if self._authorized():
            return True
        self._json_response(401, {"error": "unauthorized"})
        return False

    def do_GET(self):
        self._start_metric("GET")
        parsed = urllib.parse.urlparse(self.path)
        parsed_path = parsed.path
        if parsed_path == "/":
            self._redirect_response("/monitor")
            return
        if self.path.startswith("/api/workspaces"):
            self._handle_workspace_get()
            return
        # Monitor dashboard + aggregated snapshot live in monitor/ subpackage.
        try:
            from monitor import try_handle_get as _monitor_try
        except ImportError:
            _monitor_try = None
        if _monitor_try and _monitor_try(self, _registry, feishu_api=_api):
            return
        if parsed_path == "/api/enterprise/daemon-policy":
            if not self._require_authorized():
                return
            params = urllib.parse.parse_qs(parsed.query)
            owner_mobile = params.get("owner_mobile", [""])[0]
            owner_open_id = params.get("owner_open_id", [""])[0]
            machine_id = params.get("machine_id", [""])[0]
            resolved_owner_open_id, owner_err = validate_enterprise_daemon_owner_identity(_api, owner_mobile, owner_open_id)
            if owner_err:
                return self._json_response(400, {
                    "error": "invalid owner identity",
                    "message": "手机号在飞书中没有对应用户。",
                    "detail": owner_err,
                    "owner_mobile": _redact_identity(owner_mobile),
                })
            enterprise_policy = load_enterprise_policy(_root_dir)
            credentials, cred_err = daemon_credentials_from_root(_root_dir)
            if cred_err:
                return self._json_response(503, {"error": cred_err})
            daemon_policy = daemon_policy_from_enterprise_policy(
                enterprise_policy,
                machine_id=machine_id,
                feishu_credentials=credentials,
            )
            if not daemon_policy:
                return self._json_response(404, {"error": "enterprise policy unavailable on relay"})
            feishu = daemon_policy.get("feishu") if isinstance(daemon_policy.get("feishu"), dict) else {}
            return self._json_response(200, {
                "schema": "intern-agents.enterprise-daemon-policy.v1",
                "policy": daemon_policy,
                "relay": {
                    "relay_url": feishu.get("relay_url", ""),
                    "relay_http_url": feishu.get("relay_http_url") or feishu.get("relay_health_url") or "",
                },
                "owner": {
                    "owner_open_id": resolved_owner_open_id,
                } if resolved_owner_open_id else {},
                "contains_secrets": True,
            })
        if parsed_path == "/api/enterprise/user-config":
            if not self._require_authorized():
                return
            return self._handle_user_config_get()
        if parsed_path == "/api/releases/latest":
            if not self._require_authorized():
                return
            host = self.headers.get("Host", "")
            base_url = f"http://{host}" if host else ""
            try:
                release = _discover_latest_client_release(_root_dir, base_url=base_url)
            except Exception as e:
                return self._json_response(500, {
                    "schema": CLIENT_RELEASE_FEED_SCHEMA,
                    "ok": False,
                    "error": f"client release discovery failed: {e}",
                    "client_only": True,
                    "relay_upgrade": "manual_admin",
                })
            if not release:
                return self._json_response(404, {
                    "schema": CLIENT_RELEASE_FEED_SCHEMA,
                    "ok": False,
                    "error": "client release not found",
                    "searched_dirs": [os.fspath(path) for path in _client_release_dir_candidates(_root_dir)],
                    "client_only": True,
                    "relay_upgrade": "manual_admin",
                })
            return self._json_response(200, {
                "schema": CLIENT_RELEASE_FEED_SCHEMA,
                "ok": True,
                "client_only": True,
                "relay_upgrade": "manual_admin",
                "release": release,
            })
        if parsed_path.startswith("/api/releases/vsix/"):
            if not self._require_authorized():
                return
            filename = urllib.parse.unquote(parsed_path.rsplit("/", 1)[-1])
            path = _find_client_release_file(_root_dir, filename)
            if not path:
                return self._json_response(404, {"error": "client release not found"})
            try:
                data = path.read_bytes()
            except Exception as e:
                return self._json_response(500, {"error": f"client release read failed: {e}"})
            return self._binary_response(
                200,
                data,
                content_type="application/octet-stream",
                headers={"Content-Disposition": f"attachment; filename={path.name}"},
            )
        if self.path == "/api/status":
            machines = _registry.get_machines_summary()
            interns = _registry.get_all_interns()
            online = _registry.get_all_online()
            # Feishu WS internal state
            ws_detail = {}
            if _feishu_ws_client:
                ws_detail["conn_alive"] = _feishu_ws_client._conn is not None
                ws_detail["conn_id"] = getattr(_feishu_ws_client, '_conn_id', '')
                ws_detail["thread_alive"] = _feishu_thread.is_alive() if _feishu_thread else False
            self._json_response(200, {
                "running": True,
                "version": __version__,
                "script_hash": _script_hash,
                "uptime_seconds": int(time.time() - _start_time) if _start_time else 0,
                "feishu_ws_connected": _feishu_ws_ok,
                "feishu_msg_count": _feishu_msg_count,
                "feishu_last_msg_ago": int(time.time() - _feishu_last_msg_time) if _feishu_last_msg_time else None,
                "feishu_im_message_count": _feishu_im_message_count,
                "feishu_im_message_last_ago": int(time.time() - _feishu_im_message_last_time) if _feishu_im_message_last_time else None,
                "feishu_card_action_count": _feishu_card_action_count,
                "feishu_card_action_last_ago": int(time.time() - _feishu_card_action_last_time) if _feishu_card_action_last_time else None,
                "feishu_ws_detail": ws_detail,
                "machines_connected": len(machines),
                "interns_registered": len(interns),
                "interns_online": len(online),
            })
        elif self.path == "/api/machines":
            self._json_response(200, _registry.get_machines_summary())
        elif self.path == "/api/scene":
            self._json_response(200, _registry.get_current_scene())
        elif self.path == "/api/registry":
            self._json_response(200, _registry.get_all_interns())
        elif self.path == "/api/online":
            self._json_response(200, _registry.get_all_online_by_name())
        elif self.path.startswith("/api/intern/check_online"):
            # Parse intern_name + optional project from query string
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            intern_name = params.get("intern_name", [""])[0]
            project = params.get("project", [""])[0] or None
            if not intern_name:
                self._json_response(400, {"error": "intern_name required"})
                return
            online, on_machine = _registry.is_online(intern_name, project=project)
            self._json_response(200, {
                "intern_name": intern_name,
                "online": online,
                "machine_id": on_machine,
            })
        elif self.path.startswith("/api/chat/lookup"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            intern_name = params.get("intern", [""])[0]
            project = params.get("project", [""])[0] or None
            if not intern_name:
                self._json_response(400, {"error": "intern param required"})
                return
            chat_id = _registry.find_chat_id(intern_name, project=project)
            self._json_response(200, {"intern_name": intern_name, "chat_id": chat_id or ""})
        elif self.path.startswith("/api/chat/trigger_mode"):
            # task252: read current trigger_mode for an intern's chat.
            # GET /api/chat/trigger_mode?intern=NAME&project=PROJ
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            intern_name = params.get("intern", [""])[0]
            project = params.get("project", [""])[0] or None
            if not intern_name:
                return self._json_response(400, {"error": "intern param required"})
            chat_id = _registry.find_chat_id(intern_name, project=project)
            if not chat_id:
                return self._json_response(
                    404, {"error": f"no chat for intern={intern_name!r} project={project!r}"})
            mode = chat_config.get_trigger_mode(chat_id)
            self._json_response(200, {"intern_name": intern_name, "chat_id": chat_id, "mode": mode})
        elif self.path.startswith("/api/chat/detail_mode"):
            # task258 + task283: read current detail_mode for an intern's chat
            # via daemon RPC. The relay no longer stores detail_mode itself —
            # daemon-local file is the truth source. HTTP contract unchanged.
            # GET /api/chat/detail_mode?intern=NAME&project=PROJ
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            intern_name = params.get("intern", [""])[0]
            project = params.get("project", [""])[0] or None
            if not intern_name:
                return self._json_response(400, {"error": "intern param required"})
            chat_id = _registry.find_chat_id(intern_name, project=project)
            if not chat_id:
                return self._json_response(
                    404, {"error": f"no chat for intern={intern_name!r} project={project!r}"})
            result, error = _relay_ws.detail_mode_request(chat_id, op="get")
            if error:
                # Map structural errors to specific HTTP codes so callers
                # (VS Code extension, monitoring) can react sensibly.
                code = {
                    "daemon_offline": 503,
                    "daemon_outdated": 502,
                    "send_failed": 502,
                    "timeout": 504,
                }.get(error, 502)
                return self._json_response(code, {"error": error,
                                                   "intern_name": intern_name,
                                                   "chat_id": chat_id})
            self._json_response(200, {"intern_name": intern_name,
                                       "chat_id": chat_id, "mode": result["mode"]})
        elif self.path == "/api/chat/list":
            # Returns dict keyed by intern_name (back-compat). Includes project field for new clients.
            all_interns = _registry.get_all_interns_by_key()
            result = {}
            for ckey, entry in all_interns.items():
                name = entry.get("name") or _split_composite_key(ckey)[1]
                project = entry.get("project")
                if not project:
                    # 严格模式：registry 里 entry 必有 project（load 时已拒绝缺 project 的）；
                    # 真走到这条说明 in-memory 态被 update_chat_id 无 project 调用污染，log 并 skip
                    log.error(f"[API] /api/registry: entry {ckey!r} missing project field; skipping")
                    continue
                result[name] = {
                    "chat_id": entry.get("chat_id", ""),
                    "type": entry.get("type", "copilot"),
                    "project": project,
                }
            self._json_response(200, result)
        elif self.path == "/api/health":
            self._json_response(200, {"ok": True})
        elif self.path == "/api/admin/load":
            self._json_response(200, _build_admin_load_snapshot())
        elif self.path == "/api/admin/analysis":
            self._json_response(200, _build_admin_analysis_snapshot())
        elif self.path == "/api/admin/dashboard":
            machines = _registry.get_machines_summary()
            machine_data = {}
            for mid, minfo in machines.items():
                # Group interns_detail by project
                projects = {}
                for d in minfo.get("interns_detail", []):
                    proj = d.get("project") or "unknown"
                    projects.setdefault(proj, []).append({
                        "name": d["name"],
                        "type": d.get("type", "copilot"),
                        "online": d.get("online", False),
                        "status": d.get("status", ""),
                        "current_task": d.get("current_task", ""),
                        "last_active": d.get("last_active", ""),
                        "turn_count_today": d.get("turn_count_today"),
                    })
                # Resolve owner open_id → name/mobile/avatar via Feishu (cached)
                owner_name = ""
                owner_mobile = minfo.get("owner_mobile", "")
                owner_avatar = ""
                open_id = minfo.get("owner_open_id", "")
                if open_id and _api:
                    user_info, _err = _api.get_user_info(open_id)
                    if user_info:
                        owner_name = user_info.get("name", "")
                        owner_mobile = user_info.get("mobile", "") or owner_mobile
                        owner_avatar = user_info.get("avatar_url", "")
                machine_data[mid] = {
                    "owner_mobile": owner_mobile,
                    "owner_open_id": open_id,
                    "owner_name": owner_name,
                    "owner_avatar": owner_avatar,
                    "ip": minfo.get("ip", ""),
                    "ssh_port": minfo.get("ssh_port", 22),
                    "daemon_hash": minfo.get("daemon_hash", ""),
                    "extension_version": minfo.get("extension_version", ""),
                    "hooks_version": minfo.get("hooks_version", ""),
                    "cli_versions": minfo.get("cli_versions", {}),
                    "capabilities": list(minfo.get("capabilities") or []),
                    "workspaces": list(minfo.get("workspaces") or []),
                    "resources": minfo.get("resources", {}),
                    "resources_updated_at": minfo.get("resources_updated_at"),
                    "warnings": minfo.get("warnings", []),
                    "warnings_updated_at": minfo.get("warnings_updated_at"),
                    "metrics": minfo.get("metrics", {}),
                    "metrics_updated_at": minfo.get("metrics_updated_at"),
                    "connected_at": minfo["connected_at"],
                    "ws_connected": minfo["ws_connected"],
                    "projects": projects,
                }
            self._json_response(200, {
                "relay_hash": _script_hash,
                "relay_metrics": _relay_metrics.snapshot(),
                "machines": machine_data,
            })
        elif self.path.startswith("/api/admin/log_status"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            request_id = params.get("request_id", [""])[0]
            if not request_id or request_id not in _log_transfers:
                self._json_response(404, {"error": "transfer not found"})
                return
            info = _log_transfers[request_id]
            self._json_response(200, {
                "request_id": request_id,
                "status": info["status"],
                "machine_id": info["machine_id"],
                "local_path": info.get("local_path", ""),
            })
        elif self.path == "/admin":
            self._serve_admin_html()
        else:
            self._json_response(404, {"error": "not found"})

    def _serve_admin_html(self):
        html_path = os.path.join(os.path.dirname(__file__), "admin.html")
        try:
            with open(html_path, "r") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))
        except FileNotFoundError:
            self._json_response(404, {"error": "admin.html not found"})

    def _public_user_config_record(self, record):
        return {
            "owner_key": record.get("owner_key", ""),
            "owner": record.get("owner", ""),
            "created_at": record.get("created_at", ""),
            "updated_at": record.get("updated_at", ""),
            "backup_schema": record.get("backup_schema", ""),
            "manifest": record.get("manifest") if isinstance(record.get("manifest"), dict) else {},
        }

    def _handle_user_config_get(self):
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        owner_mobile = params.get("owner_mobile", [""])[0]
        owner_open_id = params.get("owner_open_id", [""])[0]
        owner_key, _identity = _user_config_identity_key(owner_mobile, owner_open_id)
        if not owner_key:
            return self._json_response(400, {"error": "owner_mobile or owner_open_id required"})
        path = _user_config_backup_path(owner_key)
        if not os.path.isfile(path):
            return self._json_response(404, {"error": "user config backup not found"})
        try:
            with open(path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except Exception as e:
            return self._json_response(500, {"error": f"user config backup read failed: {e}"})
        backup = record.get("backup")
        if not isinstance(backup, dict):
            return self._json_response(500, {"error": "user config backup record is invalid"})
        return self._json_response(200, {
            "schema": "intern-agents.enterprise-user-config.v1",
            "ok": True,
            "record": self._public_user_config_record(record),
            "backup": backup,
        })

    def _handle_user_config_post(self):
        if not self._require_authorized():
            return
        body = self._read_body()
        owner_key, identity = _user_config_identity_key(body.get("owner_mobile"), body.get("owner_open_id"))
        if not owner_key:
            return self._json_response(400, {"error": "owner_mobile or owner_open_id required"})
        backup = body.get("backup")
        if not isinstance(backup, dict):
            return self._json_response(400, {"error": "backup object required"})
        if backup.get("schema") != "intern-agents.user-config-backup.v1":
            return self._json_response(400, {"error": "unsupported user config backup schema"})
        path = _user_config_backup_path(owner_key)
        created_at = datetime.now(timezone.utc).isoformat()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    previous = json.load(f)
                created_at = previous.get("created_at") or created_at
            except Exception:
                pass
        record = {
            "schema": "intern-agents.enterprise-user-config-record.v1",
            "owner_key": owner_key,
            "owner": _redact_identity(identity),
            "created_at": created_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "backup_schema": backup.get("schema", ""),
            "manifest": backup.get("manifest") if isinstance(backup.get("manifest"), dict) else {},
            "backup": backup,
        }
        try:
            _write_json_file_atomic(path, record, mode=0o600)
        except Exception as e:
            return self._json_response(500, {"error": f"user config backup write failed: {e}"})
        return self._json_response(200, {
            "schema": "intern-agents.enterprise-user-config.v1",
            "ok": True,
            "record": self._public_user_config_record(record),
        })

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length:
            return json.loads(self.rfile.read(length))
        return {}

    def _workspace_path_parts(self):
        parsed = urllib.parse.urlparse(self.path)
        return [part for part in parsed.path.split("/") if part], urllib.parse.parse_qs(parsed.query)

    def _handle_workspace_get(self):
        if _workspace_registry is None:
            return self._json_response(503, {"error": "workspace registry unavailable"})
        parts, params = self._workspace_path_parts()
        if parts == ["api", "workspaces"]:
            include_deleted = params.get("include_deleted", ["false"])[0].lower() == "true"
            return self._json_response(200, _workspace_registry.list(include_deleted=include_deleted))
        if len(parts) == 3 and parts[:2] == ["api", "workspaces"]:
            try:
                item = _workspace_registry.get(parts[2])
            except ValueError as e:
                return self._json_response(400, {"error": str(e)})
            if not item or item.get("deleted"):
                return self._json_response(404, {"error": "workspace not found"})
            return self._json_response(200, item)
        return self._json_response(404, {"error": "not found"})

    def _handle_workspace_post(self, body):
        if _workspace_registry is None:
            return self._json_response(503, {"error": "workspace registry unavailable"})
        parts, _ = self._workspace_path_parts()
        try:
            if parts == ["api", "workspaces"]:
                created_by = body.get("created_by") or self.headers.get("X-Intern-Actor", "")
                item = _workspace_registry.create(body, created_by=created_by)
                reused = bool(item.pop("reused", False))
                return self._json_response(200 if reused else 201, {"ok": True, "reused": reused, "workspace": item, **item})
        except WorkspaceConflict as e:
            return self._json_response(409, {
                "error": "workspace already exists",
                "workspace_id": e.workspace_id,
                "workspace": e.existing,
            })
        except ValueError as e:
            return self._json_response(400, {"error": str(e)})
        return self._json_response(404, {"error": "not found"})

    def _handle_workspace_patch(self):
        if _workspace_registry is None:
            return self._json_response(503, {"error": "workspace registry unavailable"})
        parts, _ = self._workspace_path_parts()
        try:
            if len(parts) == 3 and parts[:2] == ["api", "workspaces"]:
                body = self._read_body()
                item = _workspace_registry.patch(parts[2], body)
                if not item:
                    return self._json_response(404, {"error": "workspace not found"})
                return self._json_response(200, {"ok": True, "workspace": item, **item})
        except ValueError as e:
            return self._json_response(400, {"error": str(e)})
        return self._json_response(404, {"error": "not found"})

    def _handle_workspace_delete(self):
        if _workspace_registry is None:
            return self._json_response(503, {"error": "workspace registry unavailable"})
        parts, _ = self._workspace_path_parts()
        try:
            if len(parts) == 3 and parts[:2] == ["api", "workspaces"]:
                item = _workspace_registry.delete(parts[2])
                if not item:
                    return self._json_response(404, {"error": "workspace not found"})
                return self._json_response(200, {"ok": True, "deleted": True, "workspace": item})
        except ValueError as e:
            return self._json_response(400, {"error": str(e)})
        return self._json_response(404, {"error": "not found"})

    def _ensure_chat_member(self, chat_id, owner_open_id, intern_name):
        """验证 owner 是否在群内，不在则添加"""
        try:
            members, err = _api.get_chat_members(chat_id)
            if err:
                log.error(f"[CHAT] get_members failed for '{intern_name}': {err}")
                return
            if owner_open_id not in members:
                err = _api.add_chat_members(chat_id, [owner_open_id])
                if err:
                    log.error(f"[CHAT] add_member failed for '{intern_name}': {err}")
                else:
                    log.info(f"[CHAT] Added owner to existing group '{intern_name}'")
        except Exception as e:
            log.error(f"[CHAT] ensure_member failed for '{intern_name}': {e}")

    def do_POST(self):
        self._start_metric("POST")
        if urllib.parse.urlparse(self.path).path == "/api/enterprise/user-config":
            self._handle_user_config_post()
            return

        if self.path.startswith("/api/workspaces"):
            body = self._read_body()
            self._handle_workspace_post(body)
            return

        if self.path in ("/api/ci/feishu_message", "/api/ci/card_callback"):
            body = self._read_body()
            if not _ci_http_enabled():
                return self._json_response(403, {"error": "CI synthetic Feishu events are disabled"})
            chat_id = body.get("chat_id", "")
            intern_name = body.get("intern_name", "")
            if not chat_id or not intern_name:
                return self._json_response(400, {"error": "chat_id and intern_name required"})
            entry = _registry.find_entry_by_chat(chat_id)
            machine_id = entry.get("machine_id", "") if entry else ""
            project = body.get("project", "") or (entry.get("project", "") if entry else "")
            if not machine_id:
                return self._json_response(404, {"error": f"no machine for chat_id={chat_id}"})

            if self.path == "/api/ci/feishu_message":
                text = body.get("text", "")
                if not text:
                    return self._json_response(400, {"error": "text required"})
                message_id = body.get("message_id") or f"ci_msg_{_uuid.uuid4().hex}"
                visible_id, visible_err = _api.send_message(chat_id, f"🧪 CI 模拟飞书消息：{text}")
                route_msg = {
                    "type": "feishu_message",
                    "intern_name": intern_name,
                    "text": text,
                    "message_id": message_id,
                    "chat_id": chat_id,
                    "project": project,
                    "sender_id": body.get("sender_id") or "ou_ci_simulated",
                }
                sent = _relay_ws.send_to_machine(machine_id, route_msg)
                return self._json_response(200, {
                    "ok": bool(sent),
                    "type": "feishu_message",
                    "machine_id": machine_id,
                    "message_id": message_id,
                    "visible_message_id": visible_id,
                    "visible_error": visible_err,
                })

            question_id = body.get("question_id", "")
            if not question_id:
                return self._json_response(400, {"error": "question_id required"})

            form_value = body.get("form_value")
            question_keys = body.get("question_keys") or []
            if form_value is not None:
                if not isinstance(form_value, dict) or not isinstance(question_keys, list) or not question_keys:
                    return self._json_response(400, {"error": "form_value object and question_keys list required"})
                visible_answer = ", ".join(
                    str(v) if not isinstance(v, list) else "+".join(str(x) for x in v)
                    for k, v in form_value.items()
                    if k != "submit" and v
                ) or "(无)"
                visible_id, visible_err = _api.send_message(
                    chat_id,
                    f"🧪 CI 模拟飞书表单提交：{visible_answer}（question_id={question_id}）",
                )
                route_msg = {
                    "type": "card_callback",
                    "intern_name": intern_name,
                    "question_id": question_id,
                    "form_value": form_value,
                    "question_keys": question_keys,
                    "is_form": True,
                    "chat_id": chat_id,
                    "project": project,
                }
                sent = _relay_ws.send_to_machine(machine_id, route_msg)
                return self._json_response(200, {
                    "ok": bool(sent),
                    "type": "card_callback",
                    "mode": "form",
                    "machine_id": machine_id,
                    "question_id": question_id,
                    "form_value": form_value,
                    "question_keys": question_keys,
                    "visible_message_id": visible_id,
                    "visible_error": visible_err,
                })

            answer = body.get("answer", "")
            if not answer:
                return self._json_response(400, {"error": "answer or form_value required"})
            visible_id, visible_err = _api.send_message(
                chat_id,
                f"🧪 CI 模拟飞书选择：{answer}（question_id={question_id}）",
            )
            route_msg = {
                "type": "card_callback",
                "intern_name": intern_name,
                "question_id": question_id,
                "answer": answer,
                "chat_id": chat_id,
                "project": project,
            }
            sent = _relay_ws.send_to_machine(machine_id, route_msg)
            return self._json_response(200, {
                "ok": bool(sent),
                "type": "card_callback",
                "mode": "button",
                "machine_id": machine_id,
                "question_id": question_id,
                "answer": answer,
                "visible_message_id": visible_id,
                "visible_error": visible_err,
            })

        if self.path == "/api/helper/chat/create":
            body = self._read_body()
            machine_id = body.get("machine_id", "")
            if not machine_id:
                return self._json_response(400, {"error": "machine_id required"})
            helper_id = body.get("helper_id") or _machine_helper_id_for_machine(machine_id)
            runtime = body.get("runtime") or "codex"
            operator_open_id = body.get("operator_open_id") or ""
            existing = _registry.get_machine_helper(machine_id)
            if existing.get("chat_id"):
                entry = _registry.register_machine_helper(
                    machine_id,
                    helper_id=existing.get("helper_id") or helper_id,
                    runtime=runtime,
                    chat_id=existing["chat_id"],
                    status=existing.get("status") or "starting",
                    last_operator_open_id=operator_open_id,
                )
                return self._json_response(200, {
                    "chat_id": existing["chat_id"],
                    "helper_id": entry.get("helper_id") or helper_id,
                    "existing": True,
                })
            owner_open_id = operator_open_id or (_registry.get_machines_summary().get(machine_id, {}) or {}).get("owner_open_id", "")
            if not owner_open_id:
                return self._json_response(400, {"error": "operator_open_id or machine owner_open_id required"})
            chat_id, err = _api.create_chat(
                _helper_chat_name(machine_id),
                f"Machine helper for {machine_id}",
                owner_open_id,
            )
            if err or not chat_id:
                return self._json_response(500, {"error": f"create_chat failed: {err or 'empty chat_id'}"})
            entry = _registry.register_machine_helper(
                machine_id,
                helper_id=helper_id,
                runtime=runtime,
                chat_id=chat_id,
                status=existing.get("status") or "starting",
                created_by_open_id=operator_open_id,
                last_operator_open_id=operator_open_id,
            )
            _registry.append_machine_helper_audit(machine_id, "create_group", operator_open_id, {"chat_id": chat_id})
            _api.send_message(chat_id, f"Machine helper **{helper_id}** 群已创建。")
            log.info(f"[CHAT] Created helper group for machine '{machine_id}': {chat_id}")
            return self._json_response(200, {"chat_id": chat_id, "helper_id": entry.get("helper_id") or helper_id, "existing": False})

        if self.path == "/api/chat/create":
            body = self._read_body()
            intern_name = body.get("intern_name", "")
            if not intern_name:
                return self._json_response(400, {"error": "intern_name required"})
            project = body.get("project") or None
            if not project:
                # Enterprise daemon must send explicit project scope.
                log.error(f"[API] /api/chat/create: body missing project for intern={intern_name!r}; body={body!r}")
                return self._json_response(400, {"error": "project required (daemon must send it)"})
            # Lookup owner open_id from daemon's local _owner.json identity.
            mobile = body.get("owner_mobile")
            owner_open_id = body.get("owner_open_id") or ""
            source_machine_id = str(body.get("machine_id") or "").strip()
            if not owner_open_id and mobile:
                owner_open_id, err = _api.mobile_to_open_id(mobile)
                if err or not owner_open_id:
                    return self._json_response(500, {"error": f"mobile lookup failed: {err}"})
            if not owner_open_id:
                return self._json_response(400, {"error": "owner_open_id or owner_mobile required (daemon must send local _owner.json identity)"})
            # Check if already has a chat (registry + persistence)
            existing_chat = _registry.find_chat_id(intern_name, project=project)
            if not existing_chat:
                # Fallback: scan Feishu groups by exact (name, project) match.
                # 仅在 registry 持久化丢失（如 relay 重启 + persist 文件被清）时复用飞书侧已存在的群。
                # ★ 必须精确匹配 "name/project"，不能只前缀匹配 "name/"，
                #   否则不同 project 的同名 intern 会被错误复用同一个群。
                stripped_name = intern_name[len("intern_"):] if intern_name.startswith("intern_") else intern_name
                targets = {f"{stripped_name}/{project}", f"{intern_name}/{project}"}
                try:
                    chats = _api.list_chats()
                    for chat in chats:
                        name = chat.get("name", "")
                        # Strip light prefix + badge to get "intern_name/project"
                        clean = re.sub(r'^[🟢🔴⚪🤖🚀\s]+', '', name).strip()
                        clean = re.sub(r'^\[(?:Claude🤖|Claude)\]\s*', '', clean).strip()
                        if clean in targets:
                            existing_chat = chat["chat_id"]
                            log.info(f"[CHAT] Found existing group for '{clean}' via list_chats: {existing_chat}")
                            # 不在这里写 registry：下面 existing_chat 分支会统一写，
                            # 并能正确比较 pre-request 的旧 type 触发头像刷新
                            break
                except Exception as e:
                    log.warning(f"[CHAT] list_chats fallback failed: {e}")
            if existing_chat:
                # Q3: 已存在群——验证 owner 是否在群内，不在则添加
                self._ensure_chat_member(existing_chat, owner_open_id, intern_name)
                # Type may have changed since the chat was created; refresh registry
                # and group name so the visible type badge stays accurate.
                body_type = body.get("type")
                if body_type:
                    old_entry = _registry.get_entry(intern_name, project=project)
                    old_type = (old_entry or {}).get("type")
                    type_changed = old_type != body_type
                    _registry.update_chat_id(intern_name, existing_chat,
                                             intern_type=body_type, project=project,
                                             machine_id=source_machine_id)
                    if type_changed:
                        _refresh_existing_chat_light(_api, _registry, intern_name, project)
                        log.info(f"[TYPE_CHANGE] intern '{intern_name}' {old_type}→{body_type}; "
                                 f"group name emoji will refresh on next light update")
                return self._json_response(200, {"chat_id": existing_chat, "existing": True})
            # Create Feishu group
            intern_type = body.get("type", "copilot")
            group_name = _build_group_name(intern_name, False, intern_type, project)
            chat_id, err = _api.create_chat(group_name, f"Intern: {intern_name}", owner_open_id)
            if err:
                return self._json_response(500, {"error": f"create_chat failed: {err}"})
            # Register in relay registry
            _registry.update_chat_id(
                intern_name, chat_id, intern_type=intern_type,
                project=project, machine_id=source_machine_id)
            # Send welcome message
            _api.send_message(chat_id, f"🤖 Intern **{intern_name}** 群已创建")
            log.info(f"[CHAT] Created group for '{intern_name}' (project={project}): {chat_id}")
            self._json_response(200, {"chat_id": chat_id, "existing": False})

        elif self.path == "/api/chat/delete":
            body = self._read_body()
            intern_name = body.get("intern_name", "")
            if not intern_name:
                return self._json_response(400, {"error": "intern_name required"})
            project = body.get("project") or None
            targets = []
            if intern_name.startswith("machine_helper_"):
                # Helper ids are machine-global. Delete every ordinary chat
                # mapping for this helper name so stale red helper groups do
                # not remain visible in Feishu after workspace switches.
                targets = _registry.find_chat_entries_by_name(intern_name)
            elif project:
                chat_id = _registry.find_chat_id(intern_name, project=project)
                if chat_id:
                    targets = [{"project": project, "chat_id": chat_id}]
            else:
                return self._json_response(400, {"error": "project required"})

            deleted_chat_ids = []
            for item in targets:
                chat_id = item.get("chat_id")
                if not chat_id or chat_id in deleted_chat_ids:
                    continue
                err = _api.delete_chat(chat_id)
                if err:
                    log.error(f"[CHAT] delete_chat failed for '{intern_name}' chat={chat_id}: {err}")
                else:
                    deleted_chat_ids.append(chat_id)
            if intern_name.startswith("machine_helper_"):
                removed = _registry.remove_intern_chats_by_name(intern_name)
            else:
                _registry.remove_intern_chat(intern_name, project=project)
                removed = targets
            local_registry_removed = _remove_local_feishu_registry_entry(_root_dir, intern_name)
            log.info(f"[CHAT] Deleted group for '{intern_name}' (project={project or 'auto'}) "
                     f"targets={targets} removed={removed} local_registry_removed={local_registry_removed}")
            self._json_response(200, {
                "ok": True,
                "deleted_chat_ids": deleted_chat_ids,
                "removed": removed,
                "local_registry_removed": local_registry_removed,
            })

        elif self.path == "/api/admin/request_logs":
            body = self._read_body()
            machine_id = body.get("machine_id", "")
            intern_name = body.get("intern_name")  # optional, None = all logs
            if not machine_id:
                return self._json_response(400, {"error": "machine_id required"})
            import uuid
            request_id = uuid.uuid4().hex[:12]
            _log_transfers[request_id] = {
                "machine_id": machine_id,
                "status": "pending",
                "local_path": "",
            }
            sent = _relay_ws.send_to_machine(machine_id, {
                "type": "request_logs",
                "request_id": request_id,
                "intern_name": intern_name,
                "relay_upload_url": _build_relay_upload_url(
                    self.headers.get("Host", ""),
                    self.server.server_address[1],
                    request_id,
                ),
            })
            if not sent:
                _log_transfers[request_id]["status"] = "failed"
                return self._json_response(502, {"error": f"machine '{machine_id}' not connected"})
            self._json_response(200, {"request_id": request_id, "status": "pending"})

        elif self.path.startswith("/api/admin/upload_logs"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            params = parse_qs(parsed.query)
            request_id = params.get("request_id", [""])[0]
            if not request_id or request_id not in _log_transfers:
                return self._json_response(404, {"error": "transfer not found"})
            transfer = _log_transfers[request_id]
            transfer["status"] = "uploading"
            # Read binary body
            length = int(self.headers.get("Content-Length", 0))
            if not length:
                transfer["status"] = "failed"
                return self._json_response(400, {"error": "empty body"})
            # Save to local filesystem
            machine_id = transfer["machine_id"]
            version_key = _log_version_key or current_version_key(
                script_path=__file__,
                component="relay",
                component_version=__version__,
            )
            transfer_dir = transfer_log_dir(_root_dir, version_key, machine_id)
            transfer_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{timestamp}.tar.gz"
            local_path = transfer_dir / filename
            with open(local_path, "wb") as f:
                remaining = length
                while remaining > 0:
                    chunk = self.rfile.read(min(remaining, 65536))
                    if not chunk:
                        break
                    f.write(chunk)
                    remaining -= len(chunk)
            # Extract and remove archive
            import tarfile
            extract_dir = os.path.join(transfer_dir, timestamp)
            with tarfile.open(local_path, "r:gz") as tar:
                tar.extractall(path=extract_dir)
            os.unlink(local_path)
            transfer["status"] = "ready"
            transfer["local_path"] = extract_dir
            log.info(f"[LOG_TRANSFER] Extracted logs from '{machine_id}' to {extract_dir} ({length} bytes)")
            self._json_response(200, {"ok": True, "local_path": extract_dir})

        elif self.path == "/api/shutdown":
            log.info("Shutdown requested via API")
            self._json_response(200, {"ok": True})
            threading.Thread(target=lambda: (_shutdown_event.set()), daemon=True).start()

        elif self.path == "/api/chat/trigger_mode":
            # task252: set per-chat trigger mode. Body: {intern_name, project, mode}.
            # Resolves chat_id via registry; persists via chat_config.
            body = self._read_body()
            intern_name = body.get("intern_name") or ""
            project = body.get("project") or ""
            mode = body.get("mode") or ""
            if not intern_name or not project:
                return self._json_response(
                    400, {"error": "intern_name and project required"})
            if mode not in chat_config.valid_modes():
                return self._json_response(
                    400, {"error": f"invalid mode {mode!r}; "
                                   f"must be one of {chat_config.valid_modes()}"})
            chat_id = _registry.find_chat_id(intern_name, project=project)
            if not chat_id:
                return self._json_response(
                    404, {"error": f"no chat for intern={intern_name!r} "
                                   f"project={project!r}"})
            try:
                changed = chat_config.set_trigger_mode(chat_id, mode)
            except Exception as e:
                log.error(f"[API] /api/chat/trigger_mode set failed: {e}", exc_info=True)
                return self._json_response(500, {"error": str(e)})
            log.info(f"[TRIGGER] /api/chat/trigger_mode chat={chat_id} "
                     f"intern={intern_name} project={project} mode={mode}")
            description_error = None
            snapshot, snapshot_error = _collect_config_snapshot(chat_id, _relay_ws)
            if snapshot_error:
                description_error = snapshot_error
            else:
                description_error = _sync_chat_config_description(
                    _api, chat_id, snapshot)
            body = {
                "ok": True,
                "chat_id": chat_id,
                "mode": mode,
                "changed": changed,
                "description_synced": description_error is None,
            }
            if description_error:
                body["description_error"] = description_error
            self._json_response(200, body)

        elif self.path == "/api/chat/detail_mode":
            # task258 + task283: set per-chat detail_mode via daemon RPC.
            # Body: {intern_name, project, mode}. Truth source is daemon-local
            # (relay no longer stores this field). HTTP contract unchanged so
            # existing clients (VS Code extension, etc.) keep working.
            body = self._read_body()
            intern_name = body.get("intern_name") or ""
            project = body.get("project") or ""
            mode = body.get("mode") or ""
            if not intern_name or not project:
                return self._json_response(
                    400, {"error": "intern_name and project required"})
            if mode not in ("full", "summary"):
                return self._json_response(
                    400, {"error": f"invalid mode {mode!r}; must be one of "
                                   f"['full', 'summary']"})
            chat_id = _registry.find_chat_id(intern_name, project=project)
            if not chat_id:
                return self._json_response(
                    404, {"error": f"no chat for intern={intern_name!r} "
                                   f"project={project!r}"})
            source_machine_id = str(body.get("machine_id") or "").strip()
            if source_machine_id:
                entry = _registry.get_entry(intern_name, project=project)
                _registry.update_chat_id(
                    intern_name,
                    chat_id,
                    intern_type=(entry or {}).get("type"),
                    project=project,
                    machine_id=source_machine_id,
                )
            result, error = _relay_ws.detail_mode_request(
                chat_id, op="set", mode=mode)
            if error:
                code = {
                    "daemon_offline": 503,
                    "daemon_outdated": 502,
                    "send_failed": 502,
                    "timeout": 504,
                }.get(error, 502)
                return self._json_response(code, {"error": error,
                                                   "intern_name": intern_name,
                                                   "chat_id": chat_id})
            log.info(f"[DETAIL] /api/chat/detail_mode chat={chat_id} "
                     f"intern={intern_name} project={project} mode={mode} "
                     f"changed={result.get('changed')}")
            description_error = None
            snapshot, snapshot_error = _collect_config_snapshot(
                chat_id, _relay_ws, detail_mode=result.get("mode") or mode)
            if snapshot_error:
                description_error = snapshot_error
            else:
                description_error = _sync_chat_config_description(
                    _api, chat_id, snapshot)
            body = {"ok": True, "chat_id": chat_id,
                    "mode": mode,
                    "changed": result.get("changed"),
                    "description_synced": description_error is None}
            if description_error:
                body["description_error"] = description_error
            self._json_response(200, body)

        else:
            self._json_response(404, {"error": "not found"})

    def do_PATCH(self):
        self._start_metric("PATCH")
        if self.path.startswith("/api/workspaces"):
            self._handle_workspace_patch()
            return
        self._json_response(404, {"error": "not found"})

    def do_DELETE(self):
        self._start_metric("DELETE")
        if self.path.startswith("/api/workspaces"):
            self._handle_workspace_delete()
            return
        self._json_response(404, {"error": "not found"})

def main():
    global _registry, _workspace_registry, _api, _start_time, _feishu_ws_ok, _feishu_ws_client, _feishu_thread, _relay_ws, _root_dir, _log_version_key

    global _registry, _workspace_registry, _api, _start_time, _feishu_ws_ok, _feishu_ws_client, _feishu_thread, _relay_ws, _root_dir, _shutdown_event, _log_version_key

    parser = argparse.ArgumentParser(description="Feishu Relay Server")
    parser.add_argument("--root", required=True, help="Path to WORK_AGENTS_ROOT (contains enterprise_policy/relay/_owner.json)")
    parser.add_argument("--no-feishu", action="store_true", help="Skip Feishu WS connection (for testing admin panel / log transfer only)")
    args = parser.parse_args()

    cfg = load_config(args.root)
    _root_dir = args.root
    _start_time = time.time()
    _log_version_key = current_version_key(
        script_path=__file__,
        component="relay",
        component_version=__version__,
    )

    # Setup log file under root
    log_dir = system_log_dir(args.root, "relay", version_key=_log_version_key)
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "feishu_relay.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(file_handler)

    # Route lark_oapi SDK logger to our file handler so we capture WS events
    sdk_logger = logging.getLogger("lark_oapi")
    sdk_logger.addHandler(file_handler)
    sdk_logger.setLevel(logging.DEBUG)
    # Also capture the root-level SDK output
    from lark_oapi.core.log import logger as lark_logger
    lark_logger.addHandler(file_handler)
    lark_logger.setLevel(logging.DEBUG)

    log.info("=" * 60)
    log.info(f"Feishu Relay Server v{__version__} starting...")
    log.info(f"  app_id:    {cfg['app_id'][:8]}...")
    log.info(f"  WS port:   {cfg['relay_ws_port']}")
    log.info(f"  HTTP port: {cfg['relay_http_port']}")

    _shutdown_event = threading.Event()

    # 1. Registry
    persist_path = os.path.join(args.root, "llm_intern_logs", "_daemon", "relay_registry.json")
    os.makedirs(os.path.dirname(persist_path), exist_ok=True)
    _registry = RelayRegistry(persist_path=persist_path)
    workspace_persist_path = os.path.join(args.root, ".feishu_registry", "workspace_registry.json")
    enterprise_policy = load_enterprise_policy(args.root)
    policy_source = enterprise_policy.get("_source_path") if enterprise_policy else ""
    if policy_source:
        log.info(f"[WORKSPACE] loaded enterprise policy: {policy_source}")
    else:
        log.warning("[WORKSPACE] no enterprise policy file found; workspace policy is empty")
    _workspace_registry = WorkspaceRegistry(persist_path=workspace_persist_path, policy=enterprise_policy)

    # 2. Feishu API (for offline replies + light management + chat management)
    api = FeishuAPI(cfg["app_id"], cfg["app_secret"])
    _api = api
    enterprise_policy = resolve_enterprise_owner_from_mobile(api, enterprise_policy)
    # task258: expose api for callback-context code paths (e.g. /config card
    # submit, which has no direct api ref but may need to patch chat description).
    _set_api_for_callback(api)

    # 3. Relay WebSocket server
    relay_ws = RelayWSServer(
        host=cfg["listen_host"],
        port=cfg["relay_ws_port"],
        relay_token=cfg["relay_token"],
        registry=_registry,
        api=api,
    )
    relay_ws.start()
    _relay_ws = relay_ws

    # 4. HTTP monitoring server (threaded for concurrent log uploads)
    from http.server import ThreadingHTTPServer
    http_server = ThreadingHTTPServer((cfg["listen_host"], cfg["relay_http_port"]), MonitorHandler)
    http_thread = threading.Thread(target=http_server.serve_forever, daemon=True)
    http_thread.start()
    log.info(f"HTTP monitoring on http://{cfg['listen_host']}:{cfg['relay_http_port']}")

    # 5. Feishu WebSocket (inbound messages)
    if args.no_feishu:
        log.info("--no-feishu: skipping Feishu WS connection")
    else:
        try:
            import lark_oapi as lark
            from lark_oapi import ws as lark_ws

            handler_fn, card_handler_fn = create_feishu_event_handlers(
                api, _registry, relay_ws, enterprise_policy=enterprise_policy)
            event_handler = lark.EventDispatcherHandler.builder("", "").register_p2_im_message_receive_v1(
                lambda data: handler_fn(data)
            ).register_p2_card_action_trigger(
                card_handler_fn
            ).build()

            ws_client = lark_ws.Client(
                app_id=cfg["app_id"], app_secret=cfg["app_secret"],
                event_handler=event_handler,
                log_level=lark.LogLevel.DEBUG,
            )

            _feishu_ws_client = ws_client
            _feishu_thread = threading.Thread(target=ws_client.start, daemon=True)
            _feishu_thread.start()
            _feishu_ws_ok = True
            log.info("Feishu WebSocket started")
        except ImportError:
            log.error("FATAL: lark_oapi not installed. Run: pip install lark-oapi")
            sys.exit(1)

        # 5b. Feishu WS health check thread (every 60s)
        def feishu_ws_health_check():
            while not _shutdown_event.is_set():
                _shutdown_event.wait(60)
                if _shutdown_event.is_set():
                    break
                thread_alive = _feishu_thread.is_alive() if _feishu_thread else False
                conn_obj = _feishu_ws_client._conn if _feishu_ws_client else None
                conn_open = conn_obj is not None
                conn_id = getattr(_feishu_ws_client, '_conn_id', '?') if _feishu_ws_client else '?'
                last_ago = int(time.time() - _feishu_last_msg_time) if _feishu_last_msg_time else -1
                log.info(
                    f"[FEISHU_HEALTH] thread={thread_alive}, conn={conn_open}, conn_id={conn_id}, "
                    f"events={_feishu_msg_count}, im={_feishu_im_message_count}, "
                    f"card={_feishu_card_action_count}, last_msg_ago={last_ago}s")
                if not thread_alive:
                    log.error("[FEISHU_HEALTH] CRITICAL: Feishu WS thread is DEAD!")

        health_thread = threading.Thread(target=feishu_ws_health_check, daemon=True)
        health_thread.start()

    # 6. Signal handling
    def signal_handler(sig, frame):
        log.info("Received signal, shutting down...")
        _shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    log.info("Relay Server ready. Waiting for shutdown...")

    # 7. 启动通知：给主管发飞书消息
    try:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        identity = collect_startup_machine_identity(cfg, args.root, environ=os.environ)
        notification = render_startup_notification(identity, started_at=now)
        notify_mobile = os.environ.get("INTERN_OWNER_MOBILE", "")
        open_id, err = (api.mobile_to_open_id(notify_mobile) if notify_mobile else (None, "no mobile"))
        if open_id:
            api.send_to_user(open_id, notification)
            log.info(f"[STARTUP] Sent startup notification to {notify_mobile}")
        else:
            log.warning(f"[STARTUP] Failed to resolve mobile {notify_mobile}: {err}")
    except Exception as e:
        log.warning(f"[STARTUP] Failed to send startup notification: {e}")

    _shutdown_event.wait()

    if _relay_ws:
        _relay_ws.cancel_pending_red_on_shutdown()
    http_server.shutdown()
    log.info("Relay Server stopped.")
    sys.exit(0)


if __name__ == "__main__":
    main()
