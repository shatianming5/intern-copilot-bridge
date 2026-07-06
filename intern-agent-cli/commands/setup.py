"""internctl setup — 环境校验与自动配置。"""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from lib.codeup import describe_codeup_exception, setup_codeup_ssh_key
from lib.enterprise_boundary import emit_admin_rejection
from lib.enterprise_paths import daemon_owner_path, daemon_policy_path
from lib.enterprise_setup import EnterpriseSetupEngine, print_json_report, write_export
from lib.machine_config_policy import (
    MachineConfigPolicyError,
    policy_with_env_switch_state,
    save_env_switch_state,
)
from lib.session_policy_env import materialize_session_env
from lib.user_config_backup import build_encrypted_user_config, restore_encrypted_user_config
from lib.user_env import load_enterprise_user_env, write_enterprise_user_env_values

WORK_AGENTS_ROOT: str = os.environ.get("WORK_AGENTS_ROOT") or "/work-agents"


def _load_enterprise_user_env(work_root: str) -> None:
    load_enterprise_user_env(work_root)


def setup_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("setup", help="企业 setup JSON contract")
    p.set_defaults(func=run)

    setup_sub = p.add_subparsers(dest="setup_command")

    status = setup_sub.add_parser("status", help="输出企业 setup 状态 JSON contract")
    status.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    status.add_argument("--policy", help="企业策略文件路径")
    status.add_argument("--secrets", help="企业 secret bundle 路径")
    status.set_defaults(func=run_enterprise_status)

    doctor = setup_sub.add_parser("doctor", help="执行企业 setup 深度诊断 JSON contract")
    doctor.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    doctor.add_argument("--policy", help="企业策略文件路径")
    doctor.add_argument("--secrets", help="企业 secret bundle 路径")
    doctor.set_defaults(func=run_enterprise_doctor)

    apply = setup_sub.add_parser("apply", help="执行用户侧可自动修复项并输出 JSON contract")
    apply.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    apply.add_argument("--policy", help="企业策略文件路径")
    apply.add_argument("--secrets", help="企业 secret bundle 路径")
    apply.add_argument(
        "--install-runtime",
        action="store_true",
        help="安装必需的本机 runtime 依赖（如 tmux、Codex CLI、daemon Python 包）",
    )
    apply.set_defaults(func=run_enterprise_apply)

    connect = setup_sub.add_parser("connect-relay", help="配置用户侧 daemon relay 并从 relay 拉取 daemon policy")
    connect.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    connect.add_argument("--relay-url", required=True, help="Relay server URL, e.g. ws://10.0.0.1:28081 or http://10.0.0.1:28080")
    connect.add_argument("--relay-http-url", help="Optional Relay HTTP URL override; otherwise inferred from --relay-url")
    connect.add_argument("--token", required=True, help="Relay token")
    connect.add_argument("--owner-mobile", help="当前用户手机号")
    connect.add_argument("--owner-open-id", help="当前用户 open_id")
    connect.add_argument("--machine-id", help="本机 machine_id；默认由 daemon 生成")
    connect.add_argument("--restore-user-config", action="store_true", help="连接 relay 后尝试从服务器恢复用户配置密文")
    connect.add_argument("--restore-password", help=argparse.SUPPRESS)
    connect.add_argument("--restore-password-env", help="读取恢复密码的环境变量名")
    connect.set_defaults(func=run_enterprise_connect_relay)

    refresh_policy = setup_sub.add_parser("refresh-policy", help="使用已有 relay owner 配置重新拉取 daemon policy")
    refresh_policy.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    refresh_policy.set_defaults(func=run_enterprise_refresh_policy)

    backup = setup_sub.add_parser("backup-user-config", help="加密打包用户配置并上传到 relay")
    backup.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    backup.add_argument("--password", help=argparse.SUPPRESS)
    backup.add_argument("--password-env", help="读取加密密码的环境变量名")
    backup.set_defaults(func=run_enterprise_backup_user_config)

    user_env = setup_sub.add_parser("set-user-env", help="写入用户侧 enterprise env 配置")
    user_env.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    user_env.add_argument("--key", required=True, help="环境变量名")
    user_env.add_argument("--value", help=argparse.SUPPRESS)
    user_env.add_argument("--value-env", help="读取值的环境变量名")
    user_env.set_defaults(func=run_enterprise_set_user_env)

    env_switches = setup_sub.add_parser("set-env-switches", help="写入本机 runtime profile/env switch 选择")
    env_switches.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    env_switches.add_argument("--enabled-groups-json", required=True, help="JSON array of enabled env switch group keys")
    env_switches.add_argument("--group-values-json", default="{}", help="JSON object keyed by env switch group key")
    env_switches.add_argument("--machine-id", help="本机 machine_id；默认读取 daemon owner config 或本地 daemon")
    env_switches.set_defaults(func=run_enterprise_set_env_switches)

    codeup_ssh = setup_sub.add_parser("codeup-ssh", help="生成并上传 Codeup SSH key")
    codeup_ssh.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    codeup_ssh.add_argument("--token-env", help="读取 Codeup token 的环境变量名；默认使用 CODEUP_ACCESS_TOKEN")
    codeup_ssh.set_defaults(func=run_enterprise_codeup_ssh)

    export = setup_sub.add_parser("export", help="导出脱敏企业 setup report")
    export.add_argument("--json", action="store_true", required=True, help="输出机器可读 JSON")
    export.add_argument("--policy", help="企业策略文件路径")
    export.add_argument("--secrets", help="企业 secret bundle 路径")
    export.add_argument("--output", help="同时写入指定 JSON 文件")
    export.set_defaults(func=run_enterprise_export)


def _enterprise_engine(args: argparse.Namespace) -> EnterpriseSetupEngine:
    work_root = os.environ.get("WORK_AGENTS_ROOT") or WORK_AGENTS_ROOT
    _load_enterprise_user_env(work_root)
    return EnterpriseSetupEngine(
        work_root,
        policy_path=getattr(args, "policy", None),
        secret_path=getattr(args, "secrets", None),
    )


def run_enterprise_status(args: argparse.Namespace) -> int:
    report = _enterprise_engine(args).status()
    print_json_report(report)
    return 0 if report["ready"] else 1


def run_enterprise_doctor(args: argparse.Namespace) -> int:
    report = _enterprise_engine(args).doctor()
    print_json_report(report)
    return 0 if report["ready"] else 1


def run_enterprise_apply(args: argparse.Namespace) -> int:
    report = _enterprise_engine(args).apply(install_runtime=bool(getattr(args, "install_runtime", False)))
    print_json_report(report)
    return 0 if report["ready"] else 1


def _relay_netloc(hostname: str, port: int | None) -> str:
    netloc = hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return netloc


def _normalize_relay_urls(relay_url: str, relay_http_url: str = "") -> tuple[str, str]:
    parsed = urllib.parse.urlparse(relay_url)
    if parsed.scheme not in {"ws", "wss", "http", "https"} or not parsed.hostname:
        raise ValueError("relay-url must be ws://, wss://, http://, or https:// with a host")
    port = parsed.port
    if parsed.scheme in {"ws", "wss"}:
        ws_scheme = parsed.scheme
        http_scheme = "https" if parsed.scheme == "wss" else "http"
        ws_port = port
        http_port = port - 1 if port and port > 1 else port
    else:
        http_scheme = parsed.scheme
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        http_port = port
        ws_port = port + 1 if port else port
    ws_url = urllib.parse.urlunparse((ws_scheme, _relay_netloc(parsed.hostname, ws_port), "", "", "", ""))
    inferred_http_url = urllib.parse.urlunparse((http_scheme, _relay_netloc(parsed.hostname, http_port), "", "", "", ""))
    return ws_url, (relay_http_url.strip().rstrip("/") if relay_http_url else inferred_http_url)


def _write_json_atomic(path: Path, data: dict, *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if mode is not None:
        tmp.chmod(mode)
    tmp.replace(path)
    if mode is not None:
        path.chmod(mode)


def _fetch_daemon_policy(
    relay_http_url: str,
    token: str,
    *,
    owner_mobile: str = "",
    owner_open_id: str = "",
    machine_id: str = "",
) -> dict:
    params = {}
    if owner_mobile:
        params["owner_mobile"] = owner_mobile
    if owner_open_id:
        params["owner_open_id"] = owner_open_id
    if machine_id:
        params["machine_id"] = machine_id
    query = urllib.parse.urlencode(params)
    url = relay_http_url.rstrip("/") + "/api/enterprise/daemon-policy" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"relay daemon policy fetch failed: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"relay daemon policy fetch failed: {exc}") from exc


def _password_from_args(args: argparse.Namespace, value_attr: str, env_attr: str) -> str:
    env_name = str(getattr(args, env_attr, "") or "").strip()
    if env_name:
        return os.environ.get(env_name, "")
    return str(getattr(args, value_attr, "") or "")


def _owner_query(owner_mobile: str, owner_open_id: str) -> str:
    params = {}
    if owner_mobile:
        params["owner_mobile"] = owner_mobile
    if owner_open_id:
        params["owner_open_id"] = owner_open_id
    return urllib.parse.urlencode(params)


def _validate_daemon_policy_credentials(policy: dict) -> None:
    feishu = policy.get("feishu") if isinstance(policy.get("feishu"), dict) else {}
    app_id = str(feishu.get("app_id") or "").strip()
    app_secret = str(feishu.get("app_secret") or "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("relay daemon policy missing feishu.app_id/app_secret")


def _fetch_user_config_backup(
    relay_http_url: str,
    token: str,
    *,
    owner_mobile: str,
    owner_open_id: str,
) -> tuple[dict | None, dict]:
    query = _owner_query(owner_mobile, owner_open_id)
    url = relay_http_url.rstrip("/") + "/api/enterprise/user-config" + (f"?{query}" if query else "")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, {"missing": True}
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"relay user config fetch failed: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"relay user config fetch failed: {exc}") from exc
    backup = data.get("backup") if isinstance(data, dict) else None
    if not isinstance(backup, dict):
        raise RuntimeError("relay user config response missing backup object")
    record = data.get("record") if isinstance(data.get("record"), dict) else {}
    return backup, record


def _post_user_config_backup(
    relay_http_url: str,
    token: str,
    *,
    owner_mobile: str,
    owner_open_id: str,
    backup: dict,
) -> dict:
    url = relay_http_url.rstrip("/") + "/api/enterprise/user-config"
    body = json.dumps({
        "owner_mobile": owner_mobile,
        "owner_open_id": owner_open_id,
        "backup": backup,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"relay user config upload failed: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"relay user config upload failed: {exc}") from exc


def _read_owner_config(work_root: Path) -> dict:
    owner_path = daemon_owner_path(work_root)
    try:
        data = json.loads(owner_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_local_daemon_status() -> dict:
    addr_path = Path(os.environ.get("FEISHU_DAEMON_ADDR_FILE") or "/tmp/feishu_daemon.json")
    try:
        payload = json.loads(addr_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"local daemon address file unavailable: {addr_path}") from exc
    port = int(payload.get("http_port") or 0)
    if port <= 0:
        raise RuntimeError(f"local daemon address file missing http_port: {addr_path}")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=3) as resp:
        status = json.loads(resp.read().decode("utf-8"))
    if not isinstance(status, dict):
        raise RuntimeError("local daemon /api/status did not return an object")
    return status


def _resolve_env_switch_machine_id(work_root: Path, owner: dict) -> tuple[str, str]:
    machine_id = str(owner.get("machine_id") or "").strip()
    if machine_id:
        return machine_id, "owner_config"

    try:
        status = _read_local_daemon_status()
    except Exception as exc:
        raise RuntimeError(
            "local daemon machine_id unavailable; connect relay or start the local daemon before saving runtime profiles"
        ) from exc

    status_root = str(status.get("work_agents_root") or "").strip()
    if status_root and os.path.abspath(status_root) != os.path.abspath(os.fspath(work_root)):
        raise RuntimeError(
            f"local daemon belongs to a different WORK_AGENTS_ROOT: {status_root}"
        )
    if not status.get("relay_connected"):
        raise RuntimeError("local daemon is not connected to relay; connect relay before saving runtime profiles")
    machine_id = str(status.get("machine_id") or status.get("instance_id") or "").strip()
    if not machine_id:
        raise RuntimeError("local daemon did not report a machine_id")

    owner["machine_id"] = machine_id
    _write_json_atomic(daemon_owner_path(work_root), owner, mode=0o600)
    return machine_id, "local_daemon"


def _materialize_session_env_report(work_root: Path, policy: dict, owner: dict) -> dict:
    return materialize_session_env(
        work_root=work_root,
        policy=policy,
        owner=owner,
        environ=os.environ,
    )


def run_enterprise_set_env_switches(args: argparse.Namespace) -> int:
    work_root = Path(os.environ.get("WORK_AGENTS_ROOT") or WORK_AGENTS_ROOT)
    _load_enterprise_user_env(os.fspath(work_root))
    owner = _read_owner_config(work_root)
    explicit_machine_id = str(getattr(args, "machine_id", "") or "").strip()
    machine_id_source = "argument" if explicit_machine_id else ""
    try:
        if explicit_machine_id:
            machine_id = explicit_machine_id
        else:
            machine_id, machine_id_source = _resolve_env_switch_machine_id(work_root, owner)
    except Exception as exc:
        print(json.dumps({
            "schema": "intern-agents.setup-env-switches.v1",
            "ok": False,
            "error": str(exc),
            "next_actions": ["Connect relay and make sure the local daemon is running, then save runtime profiles again."],
        }, ensure_ascii=False, indent=2))
        return 1
    if not machine_id:
        print(json.dumps({
            "schema": "intern-agents.setup-env-switches.v1",
            "ok": False,
            "error": "local daemon machine_id unavailable; connect relay before saving runtime profiles",
        }, ensure_ascii=False, indent=2))
        return 1
    try:
        enabled_groups = json.loads(str(args.enabled_groups_json or "[]"))
        group_values = json.loads(str(args.group_values_json or "{}"))
    except Exception as exc:
        print(json.dumps({
            "schema": "intern-agents.setup-env-switches.v1",
            "ok": False,
            "error": f"invalid env switch JSON: {exc}",
        }, ensure_ascii=False, indent=2))
        return 1
    policy_path = daemon_policy_path(work_root)
    try:
        daemon_policy = json.loads(policy_path.read_text(encoding="utf-8"))
        if not isinstance(daemon_policy, dict):
            raise ValueError("daemon policy must be an object")
        save_report = save_env_switch_state(
            work_root=work_root,
            policy=daemon_policy,
            machine_id=machine_id,
            enabled_groups=enabled_groups,
            group_values=group_values,
        )
        effective_policy = policy_with_env_switch_state(
            work_root=work_root,
            policy=daemon_policy,
            machine_id=machine_id,
        )
        runtime_env = _materialize_session_env_report(work_root, effective_policy, owner)
    except (OSError, ValueError, MachineConfigPolicyError) as exc:
        print(json.dumps({
            "schema": "intern-agents.setup-env-switches.v1",
            "ok": False,
            "policy_path": os.fspath(policy_path),
            "machine_id": machine_id,
            "machine_id_source": machine_id_source,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "schema": "intern-agents.setup-env-switches.v1",
        "ok": True,
        "machine_id": machine_id,
        "machine_id_source": machine_id_source,
        "policy_path": os.fspath(policy_path),
        "save": save_report,
        "runtime_env": runtime_env,
    }, ensure_ascii=False, indent=2))
    return 0


def run_enterprise_connect_relay(args: argparse.Namespace) -> int:
    work_root = Path(os.environ.get("WORK_AGENTS_ROOT") or WORK_AGENTS_ROOT)
    _load_enterprise_user_env(os.fspath(work_root))
    relay_url, relay_http_url = _normalize_relay_urls(
        str(args.relay_url).strip(),
        str(args.relay_http_url or ""),
    )
    token = str(args.token).strip()
    owner_mobile = str(args.owner_mobile or "").strip()
    owner_open_id = str(args.owner_open_id or "").strip()
    if not (owner_mobile or owner_open_id):
        report = {
            "schema": "intern-agents.setup-connect-relay.v1",
            "ok": False,
            "error": "owner identity is required",
            "next_actions": ["Pass --owner-mobile or --owner-open-id for this daemon machine."],
        }
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    try:
        fetched = _fetch_daemon_policy(
            relay_http_url,
            token,
            owner_mobile=owner_mobile,
            owner_open_id=owner_open_id,
            machine_id=str(getattr(args, "machine_id", "") or "").strip(),
        )
        daemon_policy = fetched.get("policy") if isinstance(fetched, dict) else None
        if not isinstance(daemon_policy, dict):
            raise RuntimeError("relay response missing policy object")
        _validate_daemon_policy_credentials(daemon_policy)
        owner_info = fetched.get("owner") if isinstance(fetched.get("owner"), dict) else {}
        if not owner_open_id and owner_info.get("owner_open_id"):
            owner_open_id = str(owner_info.get("owner_open_id") or "").strip()
    except Exception as exc:
        print(json.dumps({
            "schema": "intern-agents.setup-connect-relay.v1",
            "ok": False,
            "relay_url": relay_url,
            "relay_http_url": relay_http_url,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1

    restore_report = {"requested": bool(getattr(args, "restore_user_config", False))}
    if restore_report["requested"]:
        password = _password_from_args(args, "restore_password", "restore_password_env")
        if not password:
            restore_report.update({
                "ok": False,
                "error": "restore password is required",
                "message": "请输入配置解密密钥。",
            })
        else:
            try:
                backup, record = _fetch_user_config_backup(
                    relay_http_url,
                    token,
                    owner_mobile=owner_mobile,
                    owner_open_id=owner_open_id,
                )
                if backup is None:
                    restore_report.update({
                        "ok": False,
                        "missing": True,
                        "message": "配置不存在于服务器，请重新配置。",
                    })
                else:
                    restored = restore_encrypted_user_config(work_root, password, backup)
                    restore_report.update({
                        "ok": True,
                        "missing": False,
                        "message": "已从服务器恢复并解密用户配置。",
                        "record": record,
                        "result": restored,
                    })
            except Exception as exc:
                restore_report.update({
                    "ok": False,
                    "missing": False,
                    "error": str(exc),
                    "message": "服务器配置恢复失败，请重新配置或检查解密密钥。",
                })
        if not restore_report.get("missing") and not restore_report.get("ok"):
            print(json.dumps({
                "schema": "intern-agents.setup-connect-relay.v1",
                "ok": False,
                "relay_url": relay_url,
                "relay_http_url": relay_http_url,
                "error": restore_report.get("message") or restore_report.get("error") or "restore failed",
                "restore": restore_report,
            }, ensure_ascii=False, indent=2))
            return 1

    owner_path = daemon_owner_path(work_root)
    owner = _read_owner_config(work_root)
    owner.update({
        "relay_url": relay_url,
        "relay_http_url": relay_http_url,
        "relay_token": token,
    })
    if owner_mobile:
        owner["mobile"] = owner_mobile
    if owner_open_id:
        owner["owner_open_id"] = owner_open_id
    if getattr(args, "machine_id", None):
        owner["machine_id"] = str(args.machine_id).strip()
    owner.pop("relay_ws_port", None)
    owner.pop("relay_http_port", None)

    policy_path = daemon_policy_path(work_root)
    _write_json_atomic(owner_path, owner, mode=0o600)
    _write_json_atomic(policy_path, daemon_policy, mode=0o600)
    effective_policy = policy_with_env_switch_state(
        work_root=work_root,
        policy=daemon_policy,
        machine_id=str(owner.get("machine_id") or ""),
    )
    runtime_env = _materialize_session_env_report(work_root, effective_policy, owner)
    report = {
        "schema": "intern-agents.setup-connect-relay.v1",
        "ok": True,
        "work_agents_root": os.fspath(work_root),
        "owner_path": os.fspath(owner_path),
        "daemon_policy_path": os.fspath(policy_path),
        "relay_url": relay_url,
        "relay_http_url": relay_http_url,
        "policy": {
            "schema": daemon_policy.get("schema", ""),
            "deployment_id": daemon_policy.get("deployment_id", ""),
        },
        "runtime_env": runtime_env,
        "restore": restore_report,
        "next_actions": ["Run `internctl setup apply --json --install-runtime` or use the setup GUI Apply button."],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def run_enterprise_refresh_policy(args: argparse.Namespace) -> int:
    work_root = Path(os.environ.get("WORK_AGENTS_ROOT") or WORK_AGENTS_ROOT)
    owner_path = daemon_owner_path(work_root)
    owner = _read_owner_config(work_root)
    relay_url = str(owner.get("relay_url") or "").strip()
    relay_http_url = str(owner.get("relay_http_url") or "").strip()
    token = str(owner.get("relay_token") or "").strip()
    if not token or not (relay_url or relay_http_url):
        print(json.dumps({
            "schema": "intern-agents.setup-refresh-policy.v1",
            "ok": False,
            "code": "missing_owner_config",
            "owner_path": os.fspath(owner_path),
            "message": "relay owner config is missing; run setup connect-relay first",
        }, ensure_ascii=False, indent=2))
        return 0

    try:
        relay_url, relay_http_url = _normalize_relay_urls(relay_url or relay_http_url, relay_http_url)
        fetched = _fetch_daemon_policy(
            relay_http_url,
            token,
            owner_mobile=owner.get("mobile", ""),
            owner_open_id=owner.get("owner_open_id") or owner.get("open_id") or "",
            machine_id=owner.get("machine_id", ""),
        )
        daemon_policy = fetched.get("policy") if isinstance(fetched, dict) else None
        if not isinstance(daemon_policy, dict):
            raise RuntimeError("relay response missing policy object")
        _validate_daemon_policy_credentials(daemon_policy)
    except Exception as exc:
        print(json.dumps({
            "schema": "intern-agents.setup-refresh-policy.v1",
            "ok": False,
            "code": "refresh_failed",
            "owner_path": os.fspath(owner_path),
            "relay_http_url": relay_http_url,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1

    owner.update({
        "relay_url": relay_url,
        "relay_http_url": relay_http_url,
        "relay_token": token,
    })
    policy_path = daemon_policy_path(work_root)
    _write_json_atomic(owner_path, owner, mode=0o600)
    _write_json_atomic(policy_path, daemon_policy, mode=0o600)
    _load_enterprise_user_env(os.fspath(work_root))
    effective_policy = policy_with_env_switch_state(
        work_root=work_root,
        policy=daemon_policy,
        machine_id=str(owner.get("machine_id") or ""),
    )
    runtime_env = _materialize_session_env_report(work_root, effective_policy, owner)
    print(json.dumps({
        "schema": "intern-agents.setup-refresh-policy.v1",
        "ok": True,
        "owner_path": os.fspath(owner_path),
        "daemon_policy_path": os.fspath(policy_path),
        "relay_url": relay_url,
        "relay_http_url": relay_http_url,
        "policy": {
            "schema": daemon_policy.get("schema", ""),
            "deployment_id": daemon_policy.get("deployment_id", ""),
        },
        "runtime_env": runtime_env,
    }, ensure_ascii=False, indent=2))
    return 0


def run_enterprise_backup_user_config(args: argparse.Namespace) -> int:
    work_root = Path(os.environ.get("WORK_AGENTS_ROOT") or WORK_AGENTS_ROOT)
    _load_enterprise_user_env(os.fspath(work_root))
    password = _password_from_args(args, "password", "password_env")
    if not password:
        print(json.dumps({
            "schema": "intern-agents.setup-user-config-backup.v1",
            "ok": False,
            "error": "password is required",
        }, ensure_ascii=False, indent=2))
        return 1
    owner = _read_owner_config(work_root)
    relay_http_url = str(owner.get("relay_http_url") or "").strip()
    token = str(owner.get("relay_token") or "").strip()
    owner_mobile = str(owner.get("mobile") or owner.get("owner_mobile") or "").strip()
    owner_open_id = str(owner.get("owner_open_id") or owner.get("open_id") or "").strip()
    if not relay_http_url or not token or not (owner_mobile or owner_open_id):
        print(json.dumps({
            "schema": "intern-agents.setup-user-config-backup.v1",
            "ok": False,
            "error": "relay connection owner config is incomplete",
            "next_actions": ["Run setup connect-relay first."],
        }, ensure_ascii=False, indent=2))
        return 1
    try:
        setup_report = _enterprise_engine(args).status()
        backup = build_encrypted_user_config(work_root, password, setup_report=setup_report)
        response = _post_user_config_backup(
            relay_http_url,
            token,
            owner_mobile=owner_mobile,
            owner_open_id=owner_open_id,
            backup=backup,
        )
    except Exception as exc:
        print(json.dumps({
            "schema": "intern-agents.setup-user-config-backup.v1",
            "ok": False,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "schema": "intern-agents.setup-user-config-backup.v1",
        "ok": True,
        "message": "encrypted user config uploaded",
        "manifest": backup.get("manifest") if isinstance(backup.get("manifest"), dict) else {},
        "server": response.get("record") if isinstance(response, dict) else {},
    }, ensure_ascii=False, indent=2))
    return 0


def run_enterprise_set_user_env(args: argparse.Namespace) -> int:
    work_root = Path(os.environ.get("WORK_AGENTS_ROOT") or WORK_AGENTS_ROOT)
    key = str(args.key or "").strip()
    value_env = str(getattr(args, "value_env", "") or "").strip()
    value = os.environ.get(value_env, "") if value_env else str(getattr(args, "value", "") or "")
    if not key or not value:
        print(json.dumps({
            "schema": "intern-agents.setup-user-env.v1",
            "ok": False,
            "error": "key and value are required",
        }, ensure_ascii=False, indent=2))
        return 1
    try:
        path = write_enterprise_user_env_values(work_root, {key: value})
    except Exception as exc:
        print(json.dumps({
            "schema": "intern-agents.setup-user-env.v1",
            "ok": False,
            "error": str(exc),
        }, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "schema": "intern-agents.setup-user-env.v1",
        "ok": True,
        "key": key,
        "path": os.fspath(path),
    }, ensure_ascii=False, indent=2))
    return 0


def run_enterprise_codeup_ssh(args: argparse.Namespace) -> int:
    work_root = os.environ.get("WORK_AGENTS_ROOT") or WORK_AGENTS_ROOT
    _load_enterprise_user_env(work_root)
    token_env = str(getattr(args, "token_env", "") or "").strip() or "CODEUP_ACCESS_TOKEN"
    token = os.environ.get(token_env, "").strip()
    if not token:
        print(json.dumps({
            "schema": "intern-agents.setup-codeup-ssh.v1",
            "ok": False,
            "error": f"{token_env} is not set",
            "token_env": token_env,
        }, ensure_ascii=False, indent=2))
        return 1
    try:
        result = setup_codeup_ssh_key(token)
    except Exception as exc:
        error = describe_codeup_exception(exc)
        print(json.dumps({
            "schema": "intern-agents.setup-codeup-ssh.v1",
            "ok": False,
            "error": error,
            "token_env": token_env,
        }, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "schema": "intern-agents.setup-codeup-ssh.v1",
        "ok": True,
        "token_env": token_env,
        **result,
    }, ensure_ascii=False, indent=2))
    return 0


def run_enterprise_export(args: argparse.Namespace) -> int:
    report = _enterprise_engine(args).export()
    if getattr(args, "output", None):
        write_export(report, args.output)
        report["export"]["output"] = args.output
    print_json_report(report)
    return 0 if report["ready"] else 1


def run(args: argparse.Namespace) -> int:
    return emit_admin_rejection(
        "setup",
        detail="Use `internctl setup status|doctor|apply|connect-relay|backup-user-config|set-user-env|codeup-ssh|export --json` for the enterprise setup contract.",
    )
