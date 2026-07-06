"""intern-adminctl relay — Relay Server 管理

子命令：
  intern-adminctl relay setup-server    配置 _owner.json（生成 relay_token + 端口）
  intern-adminctl relay setup-client    配置 _owner.json（输入远程 relay_url + token）
  intern-adminctl relay reset-policy    恢复 relay 默认 enterprise policy
  intern-adminctl relay sync            把最新 bundled relay 同步到 stable 目录
  intern-adminctl relay start           启动 Relay Server（后台）
  intern-adminctl relay stop            停止 Relay Server
  intern-adminctl relay status          查看 Relay Server 状态
"""
import glob
import hashlib
import json
import os
import re
import shutil
import sys
import signal
import subprocess
import time
import secrets
import urllib.request
import socket
import zipfile
from pathlib import Path

from lib.enterprise_boundary import emit_admin_rejection, enterprise_mode_active
from lib.enterprise_paths import daemon_owner_path, daemon_policy_path, relay_owner_path, relay_policy_path, relay_secrets_path
from lib.enterprise_policy import POLICY_SCHEMA, SECRET_SCHEMA
from lib.log_paths import system_log_dir


def setup_parser(subparsers):
    p = subparsers.add_parser("relay", help="Manage Feishu Relay Server")
    sub = p.add_subparsers(dest="relay_command", help="Relay sub-commands")

    # setup-server: configure _owner.json with relay token and ports
    s1 = sub.add_parser("setup-server", help="Configure relay server, enterprise policy, and secret bundle")
    s1.add_argument("--token", help="Relay token (auto-generated if omitted)")
    s1.add_argument("--ws-port", type=int, default=28081, help="Relay WS port (default: 28081)")
    s1.add_argument("--http-port", type=int, default=28080, help="Relay HTTP port (default: 28080)")
    s1.add_argument("--public-relay-url", help="Public daemon WS URL, e.g. ws://10.0.0.1:28081")
    s1.add_argument("--public-http-url", help="Public daemon HTTP URL, e.g. http://10.0.0.1:28080")
    s1.add_argument("--deployment-id", default="enterprise-default", help="Enterprise deployment id")
    s1.add_argument("--owner-mobile", help="Default owner mobile for bootstrap/admin machines")
    s1.add_argument("--owner-open-id", help="Default owner open_id for bootstrap/admin machines")
    s1.add_argument("--app-id", help="Feishu app_id to write into enterprise policy")
    s1.add_argument("--app-secret", help="Feishu app_secret to write into enterprise secret bundle")
    s1.add_argument("--app-secret-env", help="Environment variable containing the Feishu app_secret")
    s1.add_argument("--policy-output", help="Policy output path (default: $WORK_AGENTS_ROOT/enterprise_policy/relay/policy.json)")
    s1.add_argument("--secrets-output", help="Secret bundle output path (default: $WORK_AGENTS_ROOT/enterprise_policy/relay/secrets.json)")
    s1.add_argument("--json", action="store_true", help="Output JSON report")
    s1.set_defaults(func=run)

    # setup-client: configure _owner.json for relay client mode
    s2 = sub.add_parser("setup-client", help="Configure _owner.json for relay client mode")
    s2.add_argument("relay_url", help="Relay server WS URL, e.g. ws://10.0.0.1:28081")
    s2.add_argument("--token", required=True, help="Relay token (must match server)")
    s2.add_argument("--owner-mobile", help="Owner mobile for this daemon machine")
    s2.add_argument("--owner-open-id", help="Owner open_id for this daemon machine")
    s2.set_defaults(func=run)

    # sync: copy bundled relay scripts to stable directory
    s_sync = sub.add_parser("sync", help="Sync bundled relay scripts to stable directory")
    s_sync.set_defaults(func=run)

    s_publish = sub.add_parser("publish-client-release", help="Publish an Intern Agent Helper VSIX to the relay-local client release bucket")
    s_publish.add_argument("vsix", help="Path to intern-agent-helper-<version>.vsix")
    s_publish.add_argument("--release-dir", help="Override relay client release bucket; default: $WORK_AGENTS_ROOT/.feishu_relay/releases")
    s_publish.add_argument("--json", action="store_true", help="Output JSON report")
    s_publish.set_defaults(func=run)

    s_reset = sub.add_parser("reset-policy", help="Restore relay enterprise policy to the bundled default")
    s_reset.add_argument("--json", action="store_true", help="Output JSON report")
    s_reset.set_defaults(func=run)

    # start: launch relay server
    s3 = sub.add_parser("start", help="Start Relay Server in background")
    s3.add_argument("--foreground", "-f", action="store_true", help="Run in foreground (not background)")
    s3.set_defaults(func=run)

    # stop
    s4 = sub.add_parser("stop", help="Stop Relay Server")
    s4.set_defaults(func=run)

    # status
    s5 = sub.add_parser("status", help="Show Relay Server status")
    s5.add_argument("--json", action="store_true", help="Output JSON")
    s5.set_defaults(func=run)

    # scene: current active Feishu groups / stale mappings
    s_scene = sub.add_parser("scene", help="Show current active Feishu group scene")
    s_scene.add_argument("--json", action="store_true", help="Output JSON")
    s_scene.set_defaults(func=run)

    # restart: sync + stop + start (rule #8 标准重启命令)
    s6 = sub.add_parser("restart", help="Restart relay: sync + stop + start")
    s6.set_defaults(func=run)

    p.set_defaults(func=run)


def _get_root():
    return os.environ.get("WORK_AGENTS_ROOT") or os.getcwd()


def _load_user_env(root):
    env = os.environ.copy()
    env["WORK_AGENTS_ROOT"] = root
    return env


# Extension ID 固定为 'llm-intern-agents.intern-agent-helper'（打包时定死），版本号通配
_VSCODE_EXT_GLOB = "llm-intern-agents.intern-agent-helper-*"
_VSCODE_EXT_ROOTS = (
    os.path.expanduser("~/.vscode-server-insiders/extensions"),
    os.path.expanduser("~/.vscode-server/extensions"),
    os.path.expanduser("~/.vscode-insiders/extensions"),
    os.path.expanduser("~/.vscode/extensions"),
)


def _parse_semver(name):
    # 期望 ...helper-<version>，版本为 x.y.z 或 x.y.z-<rc>；取数字 tuple
    m = re.search(r"helper-(\d+(?:\.\d+)+)", name)
    if not m:
        return (0,)
    return tuple(int(p) for p in m.group(1).split(".") if p.isdigit())


def _load_bundled_build_meta(ext_dir):
    meta_path = os.path.join(ext_dir, "build-meta.json")
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        if isinstance(meta, dict):
            return meta
    except Exception:
        pass
    return {}


def _current_bundled_ext_dir():
    current = Path(__file__).resolve()
    for parent in current.parents:
        if parent.name == "bundled-cli":
            return os.fspath(parent.parent)
    return ""


def _hash_sync_tree(root):
    digest = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in sorted(dirnames) if name != "__pycache__"]
        for filename in sorted(filenames):
            if filename.endswith(".pyc"):
                continue
            path = os.path.join(dirpath, filename)
            rel = os.path.relpath(path, root)
            digest.update(rel.encode("utf-8"))
            with open(path, "rb") as f:
                digest.update(f.read())
    return digest.hexdigest()[:16]


def _find_bundled_relay_source():
    """Return the installed VSIX bundle that should seed stable relay sync.

    Version still wins first, but same-version installs are ordered by
    build-meta time/commit and then by the explicit extension root priority.
    This avoids picking an older non-Insiders package just because its path
    sorts later than the freshly installed Insiders package.
    """
    candidates = []
    current_ext_dir = _current_bundled_ext_dir()
    for ext_root in _VSCODE_EXT_ROOTS:
        if not os.path.isdir(ext_root):
            continue
        try:
            root_index = _VSCODE_EXT_ROOTS.index(ext_root)
        except ValueError:
            root_index = len(_VSCODE_EXT_ROOTS)
        for ext_dir in glob.glob(os.path.join(ext_root, _VSCODE_EXT_GLOB)):
            if ".bak-" in os.path.basename(ext_dir):
                continue
            script = os.path.join(ext_dir, "bundled-cli", "scripts", "relay", "feishu_relay.py")
            if os.path.isfile(script):
                meta = _load_bundled_build_meta(ext_dir)
                candidates.append({
                    "version": _parse_semver(os.path.basename(ext_dir)),
                    "script": script,
                    "scripts_dir": os.path.dirname(os.path.dirname(script)),
                    "ext_dir": ext_dir,
                    "root": ext_root,
                    "root_index": root_index,
                    "build_time": str(meta.get("time") or ""),
                    "commit": str(meta.get("commit") or ""),
                    "relay_hash": str((meta.get("hashes") or {}).get("relay") or ""),
                    "is_current_bundle": bool(current_ext_dir and os.path.abspath(ext_dir) == os.path.abspath(current_ext_dir)),
                })
    if not candidates:
        return None
    candidates.sort(
        key=lambda c: (
            c["version"],
            c["build_time"],
            c["commit"],
            c["is_current_bundle"],
            -c["root_index"],
            c["ext_dir"],
        ),
        reverse=True,
    )
    return candidates[0]


def _find_bundled_relay_script():
    """搜已安装 VSIX 解压目录里最新版本的 feishu_relay.py（bundled 优先）。"""
    source = _find_bundled_relay_source()
    return source["script"] if source else None


def _find_bundled_scripts_dir():
    """找最新 VSIX bundled 的 scripts/ 父目录（含 relay/ 等）。"""
    source = _find_bundled_relay_source()
    if not source:
        # Headless/server install flows often execute the unpacked bundled CLI
        # directly, before VS Code has installed the VSIX into an extension dir.
        # In that case commands/relay.py lives at <bundle>/commands/relay.py.
        local_scripts = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts")
        local_relay = os.path.join(local_scripts, "relay", "feishu_relay.py")
        if os.path.isfile(local_relay):
            return local_scripts
        return None
    return source["scripts_dir"]


def _stable_root(root):
    """stable relay 父目录：{root}/.feishu_relay/

    内部布局：
        .feishu_relay/relay/      — feishu_relay.py + admin.html + chat_config.py + monitor/...
        .feishu_relay/common/     — avatar_cache.py + avatar_generator.py（feishu_relay.py 兄弟目录 import 依赖）

    与 .feishu_relay.pid（dotfile）共前缀但路径独立；与 project/version-scoped relay log 完全分目录。
    """
    return os.path.join(root, ".feishu_relay")


def _stable_relay_dir(root):
    return os.path.join(_stable_root(root), "relay")


def _stable_relay_script(root):
    return os.path.join(_stable_relay_dir(root), "feishu_relay.py")


def _stable_lib_dir(root):
    return os.path.join(_stable_root(root), "lib")


def _stable_common_dir(root):
    return os.path.join(_stable_root(root), "common")


def _client_release_dir(root):
    return os.path.join(_stable_root(root), "releases")


def _resolve_relay_script(root):
    """task233: relay 始终从 stable 目录启动。stable 不存在则 raise（项目规则 #6 禁止静默 fallback）。

    主管首次升级到 task233 新方案时需先跑 `intern-adminctl relay sync` 创建 stable，
    后续 VSIX 升级也需主管手动 sync（extension activate 检测 hash mismatch 会 warning 提示）。
    """
    stable_script = _stable_relay_script(root)
    if not os.path.isfile(stable_script):
        raise FileNotFoundError(
            f"Stable relay script not found at {stable_script}. "
            f"Run `intern-adminctl relay sync` to populate the stable directory from the latest installed VSIX."
        )
    return stable_script


def _relay_pid_file(root):
    return os.path.join(root, ".feishu_relay.pid")


def _relay_config_path(root):
    return os.fspath(relay_owner_path(root))


def _daemon_config_path(root):
    return os.fspath(daemon_owner_path(root))


def _resolve_setup_server_credentials(args):
    app_id = str(getattr(args, "app_id", "") or "").strip()
    app_secret_env = str(getattr(args, "app_secret_env", "") or "").strip()
    if app_secret_env:
        app_secret = os.environ.get(app_secret_env, "").strip()
    else:
        app_secret = str(getattr(args, "app_secret", "") or "").strip()
    missing = []
    if not app_id:
        missing.append("--app-id")
    if not app_secret:
        missing.append("--app-secret or --app-secret-env")
    if missing:
        return None, None, "missing Feishu credentials for relay setup-server: " + ", ".join(missing)
    return app_id, app_secret, ""


def _detect_reachable_host():
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
    try:
        return socket.gethostbyname(socket.gethostname())
    except OSError:
        return "127.0.0.1"


def _write_json_file(path, data, *, mode=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mode is not None:
        tmp.chmod(mode)
    tmp.replace(path)
    if mode is not None:
        path.chmod(mode)


def _read_json_file(path):
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def _backup_json_file(path, *, reason):
    path = Path(path)
    if not path.exists():
        return ""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{path.stem}.before_{reason}.{stamp}{path.suffix}"
    shutil.copy2(path, backup_path)
    return os.fspath(backup_path)


def _build_enterprise_policy(*, deployment_id, relay_url, relay_http_url, app_id, owner_mobile="", owner_open_id=""):
    codex_base_args = ["--enable", "hooks", "--dangerously-bypass-approvals-and-sandbox"]
    codex_lb_base_url = os.environ.get("CODEX_LB_BASE_URL", "")
    codex_lb_env_key = os.environ.get("CODEX_LB_ENV_KEY", "LB_API_KEY")
    codex_lb_api_key = os.environ.get("CODEX_LB_API_KEY", "")
    codex_lb_args = [
        *codex_base_args,
        "-c", 'model_provider="lb"',
        "-c", 'model_providers.lb.name="codex-lb"',
        "-c", f'model_providers.lb.base_url="{codex_lb_base_url}"',
        "-c", 'model_providers.lb.wire_api="responses"',
        "-c", f'model_providers.lb.env_key="{codex_lb_env_key}"',
    ]
    codex_lb_session_env = {
        "env": {
            "CODEX_POLICY_LB_BASE_URL": codex_lb_base_url,
            "CODEX_LB_ENV_KEY": codex_lb_env_key,
            codex_lb_env_key: codex_lb_api_key,
        },
        "args": codex_lb_args,
    }
    feishu = {
        "relay_url": relay_url,
        "relay_http_url": relay_http_url,
        "app_id": app_id,
    }
    if owner_mobile:
        feishu["owner_mobile"] = owner_mobile
    if owner_open_id:
        feishu["owner_open_id"] = owner_open_id
    return {
        "schema": POLICY_SCHEMA,
        "deployment_id": deployment_id,
        "capabilities": {
            "codeup": {"state": "required"},
            "codex": {"state": "required"},
            "feishu": {"state": "required"},
            "workspace": {"state": "required"},
            "github": {"state": "optional"},
            "gitlab": {"state": "optional"},
            "claude": {"state": "optional"},
            "copilot": {"state": "optional"},
        },
        "feishu": feishu,
        "codeup": {
            "access_token_env": "CODEUP_ACCESS_TOKEN",
            "token_guide_url": "https://acnn1zogjo15.feishu.cn/wiki/HBNvw4nDJi5GoakUNfOcnjVrnqh",
            "token_guide_text": "Open the enterprise installation manual and follow the Codeup token section.",
        },
        "claude": {
            "access_token_env": ["ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"],
            "access_token_guide_text": "Contact the enterprise administrator for Claude authentication instructions.",
            "access_token_guide_url": "",
            "session_env": {
                "env": {
                    "ANTHROPIC_BASE_URL": os.environ.get("CLAUDE_BASE_URL", ""),
                    "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
                },
                "args": ["--permission-mode", "bypassPermissions", "--model", "claude-opus-4-7"],
            },
        },
        "codex": {
            "session_env": {
                "args": codex_base_args,
            },
        },
        "env_switches": {
            "schema": "intern-agents.env-switches.v1",
            "groups": [
                {
                    "key": "codex_lb",
                    "title": "Codex LB Provider",
                    "description": "Use the enterprise managed LB route for Codex requests on this machine.",
                    "default_enabled": False,
                    "enable_codex": True,
                    "enable_claude": False,
                    "fields": [],
                    "policy_patch": {
                        "codex": {
                            "session_env": codex_lb_session_env,
                        },
                    },
                },
            ],
        },
        "workspace": {
            "allowed_modes": ["repo_dotdir", "metadata_branch"],
            "default_mode": "repo_dotdir",
            "metadata_branch": "intern_workspace",
        },
    }


def _build_daemon_policy(policy, *, app_secret=""):
    daemon_policy = {k: v for k, v in policy.items() if not str(k).startswith("_")}
    daemon_policy["role"] = "daemon"
    daemon_policy["daemon_policy"] = True
    daemon_policy_feishu = daemon_policy.get("feishu") if isinstance(daemon_policy.get("feishu"), dict) else {}
    daemon_policy_feishu = dict(daemon_policy_feishu)
    if app_secret:
        daemon_policy_feishu["app_secret"] = app_secret
    daemon_policy["feishu"] = daemon_policy_feishu
    return daemon_policy


def _build_enterprise_secrets(*, deployment_id, relay_token, app_secret):
    return {
        "schema": SECRET_SCHEMA,
        "deployment_id": deployment_id,
        "secrets": {
            "relay.token": {"type": "sealed_value", "value": relay_token},
            "feishu.app_secret": {"type": "sealed_value", "value": app_secret},
        },
    }


def _relay_http_url_from_ws(relay_url):
    match = re.match(r"^ws://([^/:]+):(\d+)(?:/.*)?$", relay_url or "")
    if not match:
        return ""
    return f"http://{match.group(1)}:{int(match.group(2)) - 1}"


def _owner_is_local_relay(owner):
    relay_url = owner.get("relay_url", "")
    return (
        bool(owner.get("relay_ws_port") or owner.get("relay_http_port"))
        or "localhost" in relay_url
        or "127.0.0.1" in relay_url
    )


def _owner_relay_http_base(owner):
    if _owner_is_local_relay(owner):
        return f"http://localhost:{owner.get('relay_http_port', 28080)}"
    return (owner.get("relay_http_url") or _relay_http_url_from_ws(owner.get("relay_url", ""))).rstrip("/")


def _cmd_setup_server(args):
    root = _get_root()
    app_id, app_secret, credential_error = _resolve_setup_server_credentials(args)
    if credential_error:
        print(f"Error: {credential_error}", file=sys.stderr)
        return 1
    token = getattr(args, 'token', None) or secrets.token_urlsafe(32)
    ws_port = getattr(args, 'ws_port', 28081)
    http_port = getattr(args, 'http_port', 28080)
    host = _detect_reachable_host()
    relay_url = getattr(args, "public_relay_url", None) or f"ws://{host}:{ws_port}"
    relay_http_url = getattr(args, "public_http_url", None) or f"http://{host}:{http_port}"
    deployment_id = getattr(args, "deployment_id", None) or "enterprise-default"
    owner_mobile = getattr(args, "owner_mobile", None) or ""
    owner_open_id = getattr(args, "owner_open_id", None) or ""
    policy_output = Path(getattr(args, "policy_output", None) or relay_policy_path(root))
    secrets_output = Path(getattr(args, "secrets_output", None) or relay_secrets_path(root))

    # Read existing relay _owner.json and merge relay server fields
    owner_path = _relay_config_path(root)
    owner = {}
    if os.path.exists(owner_path):
        try:
            owner = json.load(open(owner_path))
        except (json.JSONDecodeError, IOError):
            pass

    owner["relay_url"] = relay_url
    owner["relay_token"] = token
    owner["relay_ws_port"] = ws_port
    owner["relay_http_port"] = http_port
    owner["relay_http_url"] = relay_http_url
    if owner_mobile:
        owner["mobile"] = owner_mobile
    if owner_open_id:
        owner["owner_open_id"] = owner_open_id

    _write_json_file(owner_path, owner, mode=0o600)
    daemon_owner_path_ = _daemon_config_path(root)
    daemon_policy_path_ = os.fspath(daemon_policy_path(root))
    daemon_owner = {
        "relay_url": relay_url,
        "relay_http_url": relay_http_url,
        "relay_token": token,
    }
    if owner_mobile:
        daemon_owner["mobile"] = owner_mobile
    if owner_open_id:
        daemon_owner["owner_open_id"] = owner_open_id
        daemon_owner["open_id"] = owner_open_id
    _write_json_file(daemon_owner_path_, daemon_owner, mode=0o600)
    policy = _build_enterprise_policy(
        deployment_id=deployment_id,
        relay_url=relay_url,
        relay_http_url=relay_http_url,
        app_id=app_id,
        owner_mobile=owner_mobile,
        owner_open_id=owner_open_id,
    )
    secret_bundle = _build_enterprise_secrets(
        deployment_id=deployment_id,
        relay_token=token,
        app_secret=app_secret,
    )
    _write_json_file(policy_output, policy)
    daemon_policy = _build_daemon_policy(policy, app_secret=app_secret)
    _write_json_file(daemon_policy_path_, daemon_policy, mode=0o600)
    _write_json_file(secrets_output, secret_bundle, mode=0o600)
    if getattr(args, "json", False):
        print(json.dumps({
            "schema": "intern-agents.relay-setup-server.v1",
            "ok": True,
            "work_agents_root": root,
            "owner_path": owner_path,
            "daemon_owner_path": daemon_owner_path_,
            "daemon_policy_path": daemon_policy_path_,
            "policy_path": str(policy_output),
            "secrets_path": str(secrets_output),
            "relay_url": relay_url,
            "relay_http_url": relay_http_url,
            "ws_port": ws_port,
            "http_port": http_port,
            "deployment_id": deployment_id,
            "token_generated": not bool(getattr(args, "token", None)),
        }, ensure_ascii=False, indent=2))
    else:
        print(f"✅ Updated {owner_path}")
        print(f"✅ Updated daemon config: {daemon_owner_path_}")
        print(f"✅ Wrote daemon policy: {daemon_policy_path_}")
        print(f"✅ Wrote enterprise policy: {policy_output}")
        print(f"✅ Wrote enterprise secrets: {secrets_output} (0600)")
        print(f"   app_id:      {app_id[:8]}...")
        print(f"   relay_url:   {relay_url}")
        print(f"   relay_http:  {relay_http_url}")
        print(f"   relay_token: {token}")
        print(f"   WS port:     {ws_port}")
        print(f"   HTTP port:   {http_port}")
        print(f"\n💡 Share relay_url + token with daemon clients; do not share secrets.json.")
    return 0


def _reset_policy_inputs(root):
    owner = _read_json_file(relay_owner_path(root))
    existing_policy = _read_json_file(relay_policy_path(root))
    feishu = existing_policy.get("feishu") if isinstance(existing_policy.get("feishu"), dict) else {}
    relay_url = (
        owner.get("relay_url")
        or feishu.get("relay_url")
        or ""
    )
    relay_http_url = (
        owner.get("relay_http_url")
        or feishu.get("relay_http_url")
        or _relay_http_url_from_ws(relay_url)
        or ""
    )
    return {
        "deployment_id": existing_policy.get("deployment_id") or "enterprise-default",
        "relay_url": relay_url,
        "relay_http_url": relay_http_url,
        "app_id": feishu.get("app_id") or "",
        "owner_mobile": (
            owner.get("mobile")
            or owner.get("owner_mobile")
            or feishu.get("owner_mobile")
            or ""
        ),
        "owner_open_id": (
            owner.get("owner_open_id")
            or owner.get("open_id")
            or feishu.get("owner_open_id")
            or ""
        ),
    }


def _cmd_reset_policy(args):
    root = _get_root()
    inputs = _reset_policy_inputs(root)
    missing = [key for key in ("relay_url", "relay_http_url", "app_id") if not inputs.get(key)]
    if missing:
        print(
            "Error: cannot reset relay policy; missing " + ", ".join(missing) +
            ". Run 'intern-adminctl relay setup-server' first or restore relay _owner.json/policy.json.",
            file=sys.stderr,
        )
        return 1

    policy_path = relay_policy_path(root)
    policy = _build_enterprise_policy(
        deployment_id=inputs["deployment_id"],
        relay_url=inputs["relay_url"],
        relay_http_url=inputs["relay_http_url"],
        app_id=inputs["app_id"],
        owner_mobile=inputs["owner_mobile"],
        owner_open_id=inputs["owner_open_id"],
    )
    backups = {
        "policy": _backup_json_file(policy_path, reason="reset_policy"),
    }
    _write_json_file(policy_path, policy)
    report = {
        "schema": "intern-agents.relay-reset-policy.v1",
        "ok": True,
        "work_agents_root": root,
        "policy_path": os.fspath(policy_path),
        "backups": {key: value for key, value in backups.items() if value},
        "deployment_id": inputs["deployment_id"],
        "relay_url": inputs["relay_url"],
        "relay_http_url": inputs["relay_http_url"],
        "app_id": inputs["app_id"],
        "env_switch_groups": [
            group.get("key")
            for group in policy.get("env_switches", {}).get("groups", [])
            if isinstance(group, dict) and group.get("key")
        ],
    }
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"✅ Reset relay policy: {policy_path}")
        if report["backups"]:
            for label, backup in report["backups"].items():
                print(f"   backup[{label}]: {backup}")
        print("   env_switch_groups: " + ", ".join(report["env_switch_groups"]))
    return 0


def _cmd_setup_client(args):
    root = _get_root()

    # Read existing daemon _owner.json and merge relay fields
    owner_path = _daemon_config_path(root)
    owner = {}
    if os.path.exists(owner_path):
        try:
            owner = json.load(open(owner_path))
        except (json.JSONDecodeError, IOError):
            pass

    owner["relay_url"] = args.relay_url
    owner["relay_token"] = args.token
    relay_http_url = _relay_http_url_from_ws(args.relay_url)
    if relay_http_url:
        owner["relay_http_url"] = relay_http_url
    if getattr(args, "owner_mobile", None):
        owner["mobile"] = args.owner_mobile
    if getattr(args, "owner_open_id", None):
        owner["owner_open_id"] = args.owner_open_id
    # Drop stale server-only fields — this machine is now a client, and
    # leaving relay_ws_port/relay_http_port around causes non-owner detection
    # paths to misidentify this machine as owner.
    owner.pop("relay_ws_port", None)
    owner.pop("relay_http_port", None)

    os.makedirs(os.path.dirname(owner_path), exist_ok=True)
    with open(owner_path, "w") as f:
        json.dump(owner, f, indent=4)
    print(f"✅ Updated {owner_path}")
    print(f"   relay_url:    {args.relay_url}")
    print(f"\n💡 Restart daemon (or relaunch intern session) to apply the new relay_url.")
    return 0


def _cmd_sync(args):
    """task233 Phase 2: 把最新 bundled relay 同步到 stable 目录。

    源：最新 VSIX 解压目录里 `bundled-cli/scripts/relay/` + `bundled-cli/scripts/common/`
    目标：`$WORK_AGENTS_ROOT/.feishu_relay/relay/`

    用 rsync -a --delete 单文件 atomic（按 Q4 决策不做整目录 rename）。

    不重启 relay。running relay 仍跑旧 Python 字节码；admin.html / monitor static-asset
    在下次按需读时立刻生效新版（视为可接受的"零碎升级"）。Python 代码逻辑变化需
    主管走规则 #8 重启 relay。
    """
    root = _get_root()
    bundled_source = _find_bundled_relay_source()
    bundled_scripts = bundled_source["scripts_dir"] if bundled_source else _find_bundled_scripts_dir()
    if not bundled_scripts:
        print(
            "Error: no installed VSIX found under any of: " + ", ".join(_VSCODE_EXT_ROOTS),
            file=sys.stderr,
        )
        return 1

    bundled_relay = os.path.join(bundled_scripts, "relay")
    bundled_common = os.path.join(bundled_scripts, "common")
    bundled_lib = os.path.join(os.path.dirname(bundled_scripts), "lib")
    if not os.path.isdir(bundled_relay):
        print(f"Error: bundled relay dir missing: {bundled_relay}", file=sys.stderr)
        return 1
    if not os.path.isdir(bundled_common):
        print(f"Error: bundled common dir missing: {bundled_common}", file=sys.stderr)
        return 1
    if not os.path.isdir(bundled_lib):
        print(f"Error: bundled lib dir missing: {bundled_lib}", file=sys.stderr)
        return 1

    stable_root = _stable_root(root)
    stable_relay = _stable_relay_dir(root)
    stable_common = _stable_common_dir(root)
    stable_lib = _stable_lib_dir(root)
    os.makedirs(stable_root, exist_ok=True)

    rsync = shutil.which("rsync")

    print(f"Syncing bundled → stable:")
    print(f"  source: {bundled_scripts}/")
    if bundled_source:
        print(
            "  source build: "
            f"ext={bundled_source.get('ext_dir', '-')} "
            f"commit={bundled_source.get('commit') or '-'} "
            f"time={bundled_source.get('build_time') or '-'} "
            f"relay_hash={bundled_source.get('relay_hash') or '-'}"
        )
    else:
        print("  source build: local unpacked bundled-cli (no VSIX build-meta)")
    print(f"  dest:   {stable_root}/")

    for src, dst, name in [
        (bundled_relay, stable_relay, "relay"),
        (bundled_common, stable_common, "common"),
        (bundled_lib, stable_lib, "lib"),
    ]:
        os.makedirs(dst, exist_ok=True)
        if rsync:
            # rsync -a --delete: archive mode + delete extraneous dest files.
            cmd = [
                rsync, "-a", "--delete",
                "--exclude=__pycache__/",
                "--exclude=*.pyc",
                f"{src}/", f"{dst}/",
            ]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                print(f"Error: rsync {name} failed (exit {result.returncode}): {result.stderr}", file=sys.stderr)
                return 1
        else:
            shutil.rmtree(dst, ignore_errors=True)
            shutil.copytree(
                src,
                dst,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )
        print(f"  ✅ {name}/ synced")

    stable_hash = _hash_sync_tree(stable_relay)
    expected_hash = bundled_source.get("relay_hash") if bundled_source else ""
    print(f"  stable relay hash: {stable_hash}")
    if expected_hash and stable_hash != expected_hash:
        print(
            f"Error: stable relay hash mismatch after sync: "
            f"expected {expected_hash}, got {stable_hash}",
            file=sys.stderr,
        )
        return 1

    print(f"\n✅ Stable relay ready at {stable_relay}")
    print(f"   Run `intern-adminctl relay start` to launch from stable directory.")
    return 0


def _client_release_sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _client_release_content_sha256(path):
    digest = hashlib.sha256()
    with zipfile.ZipFile(path) as zf:
        members = [
            name for name in zf.namelist()
            if name.startswith("extension/")
            and not name.endswith("/")
            and "__pycache__" not in Path(name).parts
            and not name.endswith(".pyc")
        ]
        for name in sorted(members):
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(zf.read(name))
            digest.update(b"\0")
    return digest.hexdigest()


def _read_vsix_package(vsix_path):
    with zipfile.ZipFile(vsix_path) as zf:
        return json.loads(zf.read("extension/package.json").decode("utf-8"))


def _write_owner_client_release_dir(root, release_dir):
    owner_path = _relay_config_path(root)
    owner = {}
    if os.path.exists(owner_path):
        with open(owner_path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            owner = loaded
    owner["client_releases_dir"] = os.path.abspath(release_dir)
    os.makedirs(os.path.dirname(owner_path), exist_ok=True)
    tmp = owner_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(owner, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, owner_path)


def _cmd_publish_client_release(args):
    root = _get_root()
    src = os.path.abspath(os.path.expanduser(str(args.vsix)))
    if not os.path.isfile(src):
        print(f"Error: VSIX not found: {src}", file=sys.stderr)
        return 1
    name = os.path.basename(src)
    if not re.match(r"^intern-agent-helper-\d+(?:\.\d+)+(?:[-.][A-Za-z0-9]+)?\.vsix$", name):
        print(f"Error: unexpected client VSIX filename: {name}", file=sys.stderr)
        return 1
    try:
        package_json = _read_vsix_package(src)
    except Exception as exc:
        print(f"Error: invalid VSIX package.json: {exc}", file=sys.stderr)
        return 1
    if package_json.get("name") != "intern-agent-helper" or package_json.get("publisher") != "llm-intern-agents":
        print("Error: VSIX is not llm-intern-agents.intern-agent-helper", file=sys.stderr)
        return 1

    release_dir = os.path.abspath(os.path.expanduser(str(args.release_dir or _client_release_dir(root))))
    os.makedirs(release_dir, exist_ok=True)
    dest = os.path.join(release_dir, name)
    shutil.copy2(src, dest)
    _write_owner_client_release_dir(root, release_dir)
    report = {
        "schema": "intern-agents.relay-client-release-publish.v1",
        "ok": True,
        "client_only": True,
        "relay_upgrade": "manual_admin",
        "release_dir": release_dir,
        "path": dest,
        "filename": name,
        "version": str(package_json.get("version") or ""),
        "sha256": _client_release_sha256(dest),
        "content_sha256": _client_release_content_sha256(dest),
        "owner_path": _relay_config_path(root),
    }
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"✅ Published client release: {dest}")
        print(f"   version: {report['version']}")
        print(f"   sha256:  {report['sha256']}")
        print(f"   content: {report['content_sha256']}")
        print(f"   bucket:  {release_dir}")
    return 0


def _cmd_start(args):
    root = _get_root()
    config_path = _relay_config_path(root)
    pid_file = _relay_pid_file(root)

    if not os.path.exists(config_path):
        print(f"Error: {config_path} not found. Run 'intern-adminctl relay setup-server' first.", file=sys.stderr)
        return 1

    # Verify _owner.json has relay_token
    try:
        owner = json.load(open(config_path))
        if not owner.get("relay_token"):
            print("Error: _owner.json missing relay_token. Run 'intern-adminctl relay setup-server' first.", file=sys.stderr)
            return 1
    except (json.JSONDecodeError, IOError) as e:
        print(f"Error reading {config_path}: {e}", file=sys.stderr)
        return 1

    # Check if already running
    if os.path.exists(pid_file):
        try:
            pid = int(open(pid_file).read().strip())
            os.kill(pid, 0)
            print(f"Relay server already running (PID {pid}). Stop first with 'intern-adminctl relay stop'.")
            return 1
        except (ValueError, ProcessLookupError):
            os.unlink(pid_file)

    try:
        relay_script = _resolve_relay_script(root)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    print(f"Using stable relay script: {relay_script}")

    foreground = getattr(args, 'foreground', False)
    env = _load_user_env(root)

    if foreground:
        print(f"Starting relay server in foreground...")
        os.execvpe("python3", ["python3", relay_script, "--root", root], env)
    else:
        log_dir = system_log_dir(root, "relay", script_path=relay_script, component_version="unknown")
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "feishu_relay.log"

        proc = subprocess.Popen(
            ["python3", relay_script, "--root", root],
            stdout=open(log_file, "w"),
            stderr=subprocess.STDOUT,
            env=env,
            start_new_session=True,
        )

        # Write PID file
        with open(pid_file, "w") as f:
            f.write(str(proc.pid))

        # Wait briefly and check it's alive
        time.sleep(2)
        try:
            os.kill(proc.pid, 0)
            print(f"✅ Relay server started (PID {proc.pid})")
            print(f"   Log: {log_file}")

            ws_port = owner.get('relay_ws_port', 28081)
            http_port = owner.get('relay_http_port', 28080)
            print(f"   WS:   :{ws_port}")
            print(f"   HTTP: :{http_port}")
        except ProcessLookupError:
            print(f"Error: relay server exited immediately. Check log: {log_file}", file=sys.stderr)
            return 1
    return 0


def _cmd_stop(args):
    root = _get_root()
    pid_file = _relay_pid_file(root)

    if not os.path.exists(pid_file):
        print("Relay server not running (no PID file).")
        return 0

    try:
        pid = int(open(pid_file).read().strip())
        os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        print(f"✅ Relay server stopped (PID {pid})")
    except (ValueError, ProcessLookupError):
        print("Relay server not running (stale PID).")
    finally:
        if os.path.exists(pid_file):
            os.unlink(pid_file)
    return 0


def _cmd_restart(args):
    """task274: 标准重启命令 = sync → stop → start，任一步失败 raise 不继续。

    rule #8 指定的唯一权威 relay 重启入口。intern 调用本命令前仍需调
    AskUserQuestion 拿主管确认（CLI 不内置主管确认，保持纯 shell 工具语义）。
    """
    print("Step 1/3: syncing bundled scripts to stable...")
    rc = _cmd_sync(args)
    if rc != 0:
        print("Restart aborted: sync failed.", file=sys.stderr)
        return rc

    print("\nStep 2/3: stopping relay...")
    rc = _cmd_stop(args)
    if rc != 0:
        print("Restart aborted: stop failed.", file=sys.stderr)
        return rc

    print("\nStep 3/3: starting relay...")
    return _cmd_start(args)


def _cmd_status(args):
    root = _get_root()
    pid_file = _relay_pid_file(root)
    json_output = bool(getattr(args, "json", False))
    report = {
        "schema": "intern-agents.relay-status.v1",
        "running": False,
        "pid": None,
        "pid_file": pid_file,
        "work_agents_root": root,
        "http": {},
        "config": {},
        "errors": [],
    }

    # Check PID
    running = False
    if os.path.exists(pid_file):
        try:
            pid = int(open(pid_file).read().strip())
            os.kill(pid, 0)
            running = True
            report["running"] = True
            report["pid"] = pid
            if not json_output:
                print(f"Relay Server: running (PID {pid})")
        except (ValueError, ProcessLookupError):
            report["errors"].append("stale_pid")
            if not json_output:
                print("Relay Server: not running (stale PID)")
    else:
        if not json_output:
            print("Relay Server: not running")

    # Try HTTP status
    config_path = _relay_config_path(root)
    owner = {}
    if os.path.exists(config_path):
        try:
            owner = json.load(open(config_path))
        except (json.JSONDecodeError, IOError):
            owner = {}
            report["errors"].append("owner_config_invalid")
        http_base = _owner_relay_http_base(owner)
        try:
            req = urllib.request.Request(f"{http_base}/api/status")
            resp = urllib.request.urlopen(req, timeout=3)
            data = json.loads(resp.read())
            report["http"] = {
                "ok": True,
                "url": f"{http_base}/api/status",
                "uptime_seconds": data.get("uptime_seconds"),
                "feishu_ws_connected": bool(data.get("feishu_ws_connected")),
                "feishu_msg_count": data.get("feishu_msg_count"),
                "feishu_last_msg_ago": data.get("feishu_last_msg_ago"),
                "feishu_im_message_count": data.get("feishu_im_message_count"),
                "feishu_im_message_last_ago": data.get("feishu_im_message_last_ago"),
                "feishu_card_action_count": data.get("feishu_card_action_count"),
                "feishu_card_action_last_ago": data.get("feishu_card_action_last_ago"),
                "feishu_inbound_verified": int(data.get("feishu_msg_count") or 0) > 0,
                "feishu_card_callback_verified": int(data.get("feishu_card_action_count") or 0) > 0,
                "machines_connected": data.get("machines_connected"),
                "interns_registered": data.get("interns_registered"),
            }
            # In enterprise deployments the relay may be supervised outside
            # this CLI invocation or the local pid file can be stale after
            # manual recovery. A healthy relay HTTP endpoint is the user-visible
            # service truth; keep stale_pid in errors, but do not report the
            # relay as down when /api/status is reachable.
            report["running"] = True
            if not json_output:
                print(f"\n  uptime:             {data.get('uptime_seconds', '?')}s")
                print(f"  feishu_ws:          {'connected' if data.get('feishu_ws_connected') else 'disconnected'}")
                print(f"  feishu_messages:    {data.get('feishu_msg_count', '?')}")
                if data.get("feishu_im_message_count") is not None:
                    print(f"  feishu_im_messages: {data.get('feishu_im_message_count', '?')}")
                if data.get("feishu_card_action_count") is not None:
                    print(f"  feishu_card_actions:{data.get('feishu_card_action_count', '?')}")
                if data.get("feishu_ws_connected") and int(data.get("feishu_msg_count") or 0) <= 0:
                    print("  feishu_inbound:     unverified (send a real bot message; check event subscriptions if it stays 0)")
                if data.get("feishu_ws_connected") and int(data.get("feishu_im_message_count") or data.get("feishu_msg_count") or 0) > 0 and int(data.get("feishu_card_action_count") or 0) <= 0:
                    print("  feishu_cards:       unverified (click a bot card; check card.action.trigger if it errors)")
                print(f"  connected_machines: {data.get('machines_connected', '?')}")
                print(f"  registered_interns: {data.get('interns_registered', '?')}")
        except Exception as exc:
            report["http"] = {"ok": False, "url": f"{http_base}/api/status" if http_base else "", "error": str(exc)}
            if running:
                if not json_output:
                    print("  (HTTP API not yet ready)")
    else:
        report["errors"].append("owner_config_missing")
        if not json_output:
            print(f"\n  No _owner.json found. Run 'intern-adminctl relay setup-server' first.")

    # Show relay config from _owner.json
    if os.path.exists(config_path):
        try:
            if not owner:
                owner = json.load(open(config_path))
            relay_url = owner.get("relay_url", "")
            if relay_url:
                is_local = _owner_is_local_relay(owner)
                report["config"] = {
                    "mode": "server" if is_local else "client",
                    "relay_url": relay_url,
                    "relay_http_url": _owner_relay_http_base(owner),
                    "path": config_path,
                }
                if not json_output:
                    print(f"\nRelay config: {'server' if is_local else 'client'} mode")
                    print(f"  relay_url:    {relay_url}")
        except Exception as exc:
            report["errors"].append(f"owner_config_read_failed:{exc}")
    else:
        if not json_output:
            print(f"\nRelay config: not configured")

    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0


def _cmd_scene(args):
    root = _get_root()
    json_output = bool(getattr(args, "json", False))
    config_path = _relay_config_path(root)
    if not os.path.exists(config_path):
        report = {
            "schema": "intern-agents.relay-current-scene.v1",
            "active_groups": [],
            "stale_persisted_groups": [],
            "summary": {},
            "warnings": [{"code": "owner_config_missing", "message": f"{config_path} not found"}],
        }
        if json_output:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"Relay scene unavailable: {config_path} not found", file=sys.stderr)
        return 1
    try:
        owner = json.load(open(config_path))
        http_base = _owner_relay_http_base(owner)
    except Exception as exc:
        report = {
            "schema": "intern-agents.relay-current-scene.v1",
            "active_groups": [],
            "stale_persisted_groups": [],
            "summary": {},
            "warnings": [{"code": "owner_config_invalid", "message": str(exc)}],
        }
        if json_output:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"Relay scene unavailable: invalid owner config: {exc}", file=sys.stderr)
        return 1

    try:
        req = urllib.request.Request(f"{http_base}/api/scene")
        resp = urllib.request.urlopen(req, timeout=5)
        report = json.loads(resp.read())
    except Exception as exc:
        report = {
            "schema": "intern-agents.relay-current-scene.v1",
            "active_groups": [],
            "stale_persisted_groups": [],
            "summary": {},
            "warnings": [{"code": "relay_scene_unreachable", "message": str(exc)}],
        }
        if json_output:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(f"Relay scene unavailable: {exc}", file=sys.stderr)
        return 1

    if json_output:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    summary = report.get("summary") or {}
    print("Relay current scene")
    print(f"  active_groups:          {summary.get('active_groups', '?')}")
    print(f"  online_groups:          {summary.get('online_groups', '?')}")
    print(f"  stale_persisted_groups: {summary.get('stale_persisted_groups', '?')}")
    print(f"  active_red_groups:      {summary.get('active_red_groups', '?')}")
    print(f"  active_auxiliary_groups:{summary.get('active_auxiliary_groups', '?')}")
    for item in report.get("active_groups") or []:
        light = item.get("group_light") or "unknown"
        online = "online" if item.get("online") else "offline"
        print(f"  - {light:7} {online:7} {item.get('project')}:{item.get('name')} {item.get('last_group_name') or ''}")
    for warning in report.get("warnings") or []:
        print(f"  warning[{warning.get('code')}]: {warning.get('message')}")
    return 0


def run(args, *, enforce_enterprise_boundary=True):
    cmd = getattr(args, 'relay_command', None)
    if not cmd:
        print("Usage: intern-adminctl relay {setup-server|setup-client|reset-policy|sync|publish-client-release|start|stop|status|scene|restart}")
        return 1

    if enforce_enterprise_boundary and enterprise_mode_active(_get_root()) and cmd not in {"status", "scene"}:
        return emit_admin_rejection(
            f"relay {cmd}",
            detail="Use `internctl setup doctor --json` for user-side daemon/relay status.",
        )

    dispatch = {
        "setup-server": _cmd_setup_server,
        "setup-client": _cmd_setup_client,
        "reset-policy": _cmd_reset_policy,
        "sync": _cmd_sync,
        "publish-client-release": _cmd_publish_client_release,
        "start": _cmd_start,
        "stop": _cmd_stop,
        "status": _cmd_status,
        "scene": _cmd_scene,
        "restart": _cmd_restart,
    }

    fn = dispatch.get(cmd)
    if fn:
        return fn(args)
    else:
        print(f"Unknown relay command: {cmd}")
        return 1
