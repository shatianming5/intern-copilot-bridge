from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile
from typing import Any

from CI.helpers.deployment_config import (
    CODEX_BASE_ARGS,
    DEFAULT_CODEX_LB_API_KEY,
    DEFAULT_CODEX_LB_BASE_URL,
    DEFAULT_CODEX_LB_ENV_KEY,
    DEFAULT_CODEX_LB_SECRET_ENV,
    DEFAULT_CLAUDE_BASE_URL,
    ci_deployment_id,
    claude_opus_47_session_env,
    codex_lb_env_switches,
)
from CI.runner.reporting import tail


REMOTE_PYTHON_WHEEL_PACKAGES = ("websockets", "lark-oapi")
SSH_AUTH_CONFIG_HOSTS = ("codeup.aliyun.com", "github.com")
DEFAULT_CODEUP_SSH_KEY_CANDIDATES = ("~/.ssh/id_ed25519_codeup", "~/.ssh/id_rsa_codeup")


def _ci_cache_root() -> Path:
    override = os.environ.get("INTERN_CI_CACHE_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return Path("/tmp/intern_agent_CI/cache")

def make_extension_tarball(vsix_path: Path, artifact_dir: Path) -> Path:
    if not vsix_path.exists():
        raise FileNotFoundError(f"VSIX not found: {vsix_path}")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    extract_dir = artifact_dir / "vsix-unpacked"
    extension_dir = extract_dir / "extension"
    tar_path = artifact_dir / "extension.tgz"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(vsix_path) as zf:
        zf.extractall(extract_dir)
    if not extension_dir.exists():
        raise RuntimeError(f"VSIX did not contain extension/ directory: {vsix_path}")
    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(extension_dir, arcname="extension")
    return tar_path

def make_codex_auth_tarball(home: Path, artifact_dir: Path) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    codex_dir = home / ".codex"
    required = [codex_dir / "auth.json", codex_dir / "config.toml"]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing Codex auth files: " + ", ".join(missing))
    tar_path = artifact_dir / "codex-auth.tgz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for path in required + [codex_dir / "installation_id"]:
            if path.exists():
                tf.add(path, arcname=f".codex/{path.name}")
    return tar_path

def _python_wheels_cache_paths() -> tuple[Path, Path]:
    cache_dir = _ci_cache_root() / "python-wheel-cache"
    return cache_dir / "python-wheels.tgz", cache_dir / "python-wheels.manifest.json"

def _write_python_wheels_manifest(path: Path, *, source: str, tar_path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "intern-agents.ci.python-wheels.v1",
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "source": source,
                "packages": list(REMOTE_PYTHON_WHEEL_PACKAGES),
                "platform": "manylinux2014_x86_64",
                "python_version": "310",
                "abi": "cp310",
                "tarball": str(tar_path),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

def _copy_cached_python_wheels(artifact_dir: Path, *, source: str) -> Path | None:
    cache_tar, cache_manifest = _python_wheels_cache_paths()
    if not cache_tar.exists():
        return None
    artifact_dir.mkdir(parents=True, exist_ok=True)
    tar_path = artifact_dir / "python-wheels.tgz"
    if cache_tar.resolve() != tar_path.resolve():
        shutil.copy2(cache_tar, tar_path)
    if cache_manifest.exists():
        shutil.copy2(cache_manifest, artifact_dir / "python-wheels.manifest.json")
    _write_python_wheels_manifest(artifact_dir / "python-wheels.source.json", source=source, tar_path=tar_path)
    return tar_path

def make_python_wheels_tarball(artifact_dir: Path, *, timeout: int = 300, prefer_cache: bool = True) -> Path:
    if prefer_cache:
        cached = _copy_cached_python_wheels(artifact_dir, source="cache")
        if cached is not None:
            return cached

    wheels_dir = artifact_dir / "python-wheels"
    if wheels_dir.exists():
        shutil.rmtree(wheels_dir)
    wheels_dir.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--only-binary=:all:",
        "--python-version",
        "310",
        "--implementation",
        "cp",
        "--abi",
        "cp310",
        "--platform",
        "manylinux2014_x86_64",
        "--timeout",
        "30",
        "--retries",
        "2",
        "--dest",
        str(wheels_dir),
        *REMOTE_PYTHON_WHEEL_PACKAGES,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        cached = _copy_cached_python_wheels(artifact_dir, source="cache_after_timeout")
        if cached is not None:
            return cached
        raise RuntimeError("pip download for remote Python wheels timed out and no cache exists") from exc
    if result.returncode != 0:
        cached = _copy_cached_python_wheels(artifact_dir, source="cache_after_download_failure")
        if cached is not None:
            return cached
        raise RuntimeError("pip download for remote Python wheels failed: " + tail(result.stderr or result.stdout, 1200))
    tar_path = artifact_dir / "python-wheels.tgz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for path in wheels_dir.glob("*.whl"):
            tf.add(path, arcname=f"wheels/{path.name}")
    cache_tar, cache_manifest = _python_wheels_cache_paths()
    cache_tar.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(tar_path, cache_tar)
    _write_python_wheels_manifest(cache_manifest, source="pip_download", tar_path=cache_tar)
    _write_python_wheels_manifest(artifact_dir / "python-wheels.manifest.json", source="pip_download", tar_path=tar_path)
    return tar_path

def _ssh_host_patterns_match(patterns: list[str], host: str) -> bool:
    return any(pattern == host or fnmatch.fnmatch(host, pattern) for pattern in patterns)

def _expand_ssh_identity_path(value: str, home: Path, host: str) -> Path:
    value = value.replace("%h", host)
    if value.startswith("~/"):
        return home / value[2:]
    return Path(value).expanduser()

def _configured_identity_files(home: Path, hosts: tuple[str, ...]) -> list[Path]:
    config_path = home / ".ssh" / "config"
    if not config_path.exists():
        return []
    identities: list[Path] = []
    active_hosts: list[str] = []
    for raw_line in config_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if not parts:
            continue
        keyword = parts[0].lower()
        if keyword == "host":
            active_hosts = parts[1:]
            continue
        if keyword != "identityfile" or len(parts) < 2 or not active_hosts:
            continue
        for host in hosts:
            if _ssh_host_patterns_match(active_hosts, host):
                identities.append(_expand_ssh_identity_path(parts[1], home, host))
                break
    deduped: list[Path] = []
    seen: set[Path] = set()
    for identity in identities:
        resolved = identity.expanduser()
        if resolved in seen:
            continue
        seen.add(resolved)
        deduped.append(resolved)
    return deduped

def resolve_codeup_ssh_key(home: Path, requested: str | os.PathLike[str] | None = None) -> Path:
    requested_value = os.fspath(requested).strip() if requested else ""
    if requested_value:
        return _expand_ssh_identity_path(os.path.expandvars(requested_value), home, "codeup.aliyun.com")

    configured = _configured_identity_files(home, ("codeup.aliyun.com",))
    for identity in configured:
        if identity.exists():
            return identity

    for candidate in DEFAULT_CODEUP_SSH_KEY_CANDIDATES:
        identity = _expand_ssh_identity_path(candidate, home, "codeup.aliyun.com")
        if identity.exists():
            return identity

    if configured:
        return configured[0]
    return _expand_ssh_identity_path(DEFAULT_CODEUP_SSH_KEY_CANDIDATES[0], home, "codeup.aliyun.com")

def make_ssh_auth_tarball(home: Path, artifact_dir: Path, key_path: Path) -> Path:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    ssh_dir = home / ".ssh"
    required = [ssh_dir / "config", key_path.expanduser()]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("missing SSH auth files: " + ", ".join(missing))
    tar_path = artifact_dir / "ssh-auth.tgz"
    identity_files = [
        path for path in [*required, *_configured_identity_files(home, SSH_AUTH_CONFIG_HOSTS)] if path.exists()
    ]
    archive_entries: dict[str, Path] = {f".ssh/{path.name}": path for path in identity_files}
    with tarfile.open(tar_path, "w:gz") as tf:
        for arcname, path in archive_entries.items():
            tf.add(path, arcname=arcname)
        if (ssh_dir / "known_hosts").exists():
            tf.add(ssh_dir / "known_hosts", arcname=".ssh/known_hosts")
        expanded_key = key_path.expanduser()
        if expanded_key.name != "id_rsa_codeup":
            tf.add(expanded_key, arcname=".ssh/id_rsa_codeup")
    return tar_path

def _write_json(path: Path, data: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.chmod(mode)

def make_enterprise_config_tarball(
    artifact_dir: Path,
    *,
    prefix: str,
    relay_host: str,
    app_id: str,
    app_secret: str,
    owner_mobile: str,
    codeup_token: str,
    claude_access_token: str,
    codex_lb_base_url: str = DEFAULT_CODEX_LB_BASE_URL,
    codex_lb_api_key: str = DEFAULT_CODEX_LB_API_KEY,
    codex_lb_env_key: str = DEFAULT_CODEX_LB_ENV_KEY,
    codex_lb_secret_env: str = DEFAULT_CODEX_LB_SECRET_ENV,
    claude_base_url: str = DEFAULT_CLAUDE_BASE_URL,
) -> tuple[Path, dict[str, str]]:
    if not app_id:
        raise RuntimeError("missing Feishu app id; pass --feishu-app-id or ENTERPRISE_CI_FEISHU_APP_ID")
    if not app_secret:
        raise RuntimeError("missing Feishu app secret; pass --feishu-app-secret or ENTERPRISE_CI_FEISHU_APP_SECRET")
    if not codeup_token:
        raise RuntimeError("missing CODEUP_ACCESS_TOKEN; full CI setup requires Codeup token and SSH")
    if not claude_access_token:
        raise RuntimeError("missing ANTHROPIC_AUTH_TOKEN; full CI setup requires Claude credentials")
    if not codex_lb_base_url:
        raise RuntimeError("missing CODEX LB base URL; full CI setup requires CODEX_POLICY_LB_BASE_URL")
    codex_lb_api_key = codex_lb_api_key or DEFAULT_CODEX_LB_API_KEY

    bundle_dir = artifact_dir / "enterprise-config"
    if bundle_dir.exists():
        shutil.rmtree(bundle_dir)
    bundle_dir.mkdir(parents=True, exist_ok=True)

    relay_token = secrets.token_urlsafe(24)
    relay_url = f"ws://{relay_host}:28081"
    relay_http_url = f"http://{relay_host}:28080"
    relay_health_url = f"{relay_http_url}/api/status"
    policy = {
        "schema": "intern-agents.enterprise-policy.v1",
        "deployment_id": ci_deployment_id(prefix),
        "capabilities": {
            "feishu": "admin_only",
            "codeup": "required",
            "workspace": "optional",
            "codex": "required",
            "claude": "required",
            "copilot": "disabled",
        },
        "feishu": {
            "app_id": app_id,
            "relay_url": relay_url,
            "relay_http_url": relay_http_url,
            "relay_health_url": relay_health_url,
            "owner_mobile": owner_mobile,
        },
        "codeup": {
            "access_token_env": "CODEUP_ACCESS_TOKEN",
            "token_guide_url": "https://acnn1zogjo15.feishu.cn/wiki/HBNvw4nDJi5GoakUNfOcnjVrnqh",
            "token_guide_text": "Open the enterprise installation manual and follow the Codeup token section.",
        },
        "claude": {
            "access_token_env": ["ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"],
            "access_token_guide_text": "Contact the enterprise administrator for Claude authentication instructions.",
            "access_token_guide_url": "",
            "session_env": claude_opus_47_session_env(base_url=claude_base_url),
        },
        "codex": {
            "session_env": {"args": CODEX_BASE_ARGS},
        },
        "env_switches": codex_lb_env_switches(
            base_url=codex_lb_base_url,
            api_key=codex_lb_api_key,
            env_key=codex_lb_env_key,
            secret_env=codex_lb_secret_env,
            default_enabled=True,
        ),
        "workspace": {
            "allowed_modes": ["repo_dotdir", "metadata_branch"],
            "default_mode": "repo_dotdir",
            "metadata_branch": "intern_workspace",
        },
    }
    secrets_bundle = {
        "schema": "intern-agents.enterprise-secrets.v1",
        "secrets": {
            "feishu.app_secret": {"type": "sealed_value", "value": app_secret},
            "relay.token": {"type": "sealed_value", "value": relay_token},
        },
    }
    owner = {
        "relay_url": relay_url,
        "relay_token": relay_token,
        "mobile": owner_mobile,
        "relay_ws_port": 28081,
        "relay_http_port": 28080,
        "relay_http_url": relay_http_url,
    }
    _write_json(bundle_dir / "enterprise_policy" / "relay" / "policy.json", policy)
    _write_json(bundle_dir / "enterprise_policy" / "relay" / "secrets.json", secrets_bundle)
    (bundle_dir / "enterprise_policy" / "daemon" / "user.env").parent.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "enterprise_policy" / "daemon" / "user.env").write_text(
        "\n".join([
            f"export CODEUP_ACCESS_TOKEN={shlex.quote(codeup_token)}",
            f"export ANTHROPIC_AUTH_TOKEN={shlex.quote(claude_access_token)}",
            'export PATH="/root/.local/bin:${PATH:-/usr/local/bin:/usr/bin:/bin}"',
            "",
        ]),
        encoding="utf-8",
    )
    (bundle_dir / "enterprise_policy" / "daemon" / "user.env").chmod(0o600)
    _write_json(bundle_dir / "enterprise_policy" / "relay" / "_owner.json", owner)
    daemon_owner = dict(owner)
    daemon_owner.pop("relay_ws_port", None)
    daemon_owner.pop("relay_http_port", None)
    _write_json(bundle_dir / "enterprise_policy" / "daemon" / "_owner.json", daemon_owner)

    tar_path = artifact_dir / "enterprise-config.tgz"
    with tarfile.open(tar_path, "w:gz") as tf:
        for item in bundle_dir.rglob("*"):
            if item.is_file():
                tf.add(item, arcname=str(item.relative_to(bundle_dir)))
    return tar_path, {
        "relay_url": relay_url,
        "relay_health_url": relay_health_url,
        "codex_lb_base_url": codex_lb_base_url,
        "codex_lb_env_key": codex_lb_env_key,
        "codex_lb_secret_env": codex_lb_secret_env,
        "claude_base_url": claude_base_url,
    }
