"""Encrypted enterprise user configuration backup helpers."""

from __future__ import annotations

import base64
from datetime import datetime, timezone
import fnmatch
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import socket
import stat
import subprocess
from typing import Any

from lib.user_env import enterprise_user_env_paths, load_enterprise_user_env, write_enterprise_user_env_values


BACKUP_SCHEMA = "intern-agents.user-config-backup.v1"
PAYLOAD_SCHEMA = "intern-agents.user-config-payload.v1"
KDF_ITERATIONS = 200_000
MAX_FILE_BYTES = 2 * 1024 * 1024
SSH_AUTH_PROBE_TIMEOUT_SECONDS = 30

ENV_KEYS = (
    "CODEUP_ACCESS_TOKEN",
    "CODEUP_ORG_ID",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "OPENAI_API_KEY",
    "GITHUB_TOKEN",
    "GH_TOKEN",
)

FILE_CANDIDATES = (
    "~/.config/gh/hosts.yml",
    "~/.codex/auth.json",
    "~/.codex/config.toml",
    "~/.claude.json",
    "~/.claude/settings.json",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_encrypted_user_config(root: str | os.PathLike[str], password: str, setup_report: dict[str, Any] | None = None) -> dict[str, Any]:
    if not password:
        raise ValueError("password is required")
    root_path = Path(root)
    if setup_report:
        env_values, files, components = _collect_report_driven_config(root_path, setup_report)
    else:
        env_values = load_enterprise_user_env(root_path, env={})
        for key in ENV_KEYS:
            value = os.environ.get(key)
            if value:
                env_values[key] = value
        files = _collect_files(root_path)
        components = {}

    payload = {
        "schema": PAYLOAD_SCHEMA,
        "created_at": utc_now(),
        "hostname": socket.gethostname(),
        "env": env_values,
        "files": files,
        "components": components,
    }
    plaintext = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    envelope = _encrypt_payload(plaintext, password)
    envelope["manifest"] = {
        "created_at": payload["created_at"],
        "env_keys": sorted(env_values.keys()),
        "file_count": len(payload["files"]),
        "file_labels": [item["label"] for item in payload["files"]],
        "components": sorted(components.keys()),
    }
    return envelope


def restore_encrypted_user_config(
    root: str | os.PathLike[str],
    password: str,
    envelope: dict[str, Any],
) -> dict[str, Any]:
    if not password:
        raise ValueError("password is required")
    plaintext = _decrypt_payload(envelope, password)
    payload = json.loads(plaintext.decode("utf-8"))
    if payload.get("schema") != PAYLOAD_SCHEMA:
        raise ValueError(f"unsupported payload schema: {payload.get('schema')!r}")

    root_path = Path(root)
    written_files: list[str] = []
    for entry in payload.get("files") or []:
        if not isinstance(entry, dict):
            continue
        target = Path(str(entry.get("path") or "")).expanduser()
        if not _allowed_restore_path(target, root_path):
            continue
        data = base64.b64decode(str(entry.get("data") or ""))
        _write_bytes_atomic(target, data, int(entry.get("mode") or 0o600))
        written_files.append(str(target))
    _normalize_restored_ssh_permissions()

    env_values = payload.get("env") if isinstance(payload.get("env"), dict) else {}
    env_path = None
    if env_values:
        env_path = write_enterprise_user_env_values(root_path, {str(k): str(v) for k, v in env_values.items()})
    configured_hosts = _restore_component_configs(payload)

    return {
        "schema": "intern-agents.user-config-restore-result.v1",
        "ok": True,
        "restored_at": utc_now(),
        "env_key_count": len(env_values),
        "env_path": str(env_path) if env_path else "",
        "file_count": len(written_files),
        "files": written_files,
        "configured_ssh_hosts": configured_hosts,
    }


def _collect_report_driven_config(root: Path, report: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]], dict[str, Any]]:
    user_env_values = load_enterprise_user_env(root, env={})
    env_values: dict[str, str] = {}
    paths: list[Path] = []
    components: dict[str, Any] = {}

    def add_env(component: str, key: str, *, required: bool = False) -> None:
        value = os.environ.get(key) or user_env_values.get(key) or ""
        if value:
            env_values[key] = value
            _component(components, component).setdefault("env", []).append(key)
            return
        if required:
            raise RuntimeError(f"{component} passed setup, but {key} was not found in user env")

    if _check_passed(report, "codeup.token"):
        token_check = _check_by_id(report, "codeup.token")
        add_env("codeup", str(_check_detail(token_check, "env_name") or "CODEUP_ACCESS_TOKEN"), required=True)
        for optional in ("CODEUP_ORG_ID", "CODEUP_ORGANIZATION_ID"):
            add_env("codeup", optional)

    if _check_passed(report, "codeup.ssh"):
        _add_ssh_component(paths, components, "codeup", "git@codeup.aliyun.com")

    if _check_passed(report, "github.cli"):
        gh_hosts = Path("~/.config/gh/hosts.yml").expanduser()
        if gh_hosts.is_file():
            paths.append(gh_hosts)
            _component(components, "github").setdefault("files", []).append(_portable_restore_path(gh_hosts))
        else:
            add_env("github", "GITHUB_TOKEN")
            add_env("github", "GH_TOKEN")
            if not any(key in env_values for key in ("GITHUB_TOKEN", "GH_TOKEN")):
                raise RuntimeError("github.cli passed setup, but neither ~/.config/gh/hosts.yml nor GITHUB_TOKEN/GH_TOKEN was found")

    if _check_passed(report, "github.ssh"):
        _add_ssh_component(paths, components, "github", "git@github.com")

    if _check_passed(report, "agent.codex_auth"):
        _add_existing_file(paths, components, "codex", Path("~/.codex/auth.json").expanduser(), required=True)
        _add_existing_file(paths, components, "codex", Path("~/.codex/config.toml").expanduser(), required=False)

    if _check_passed(report, "agent.claude_auth"):
        claude_check = _check_by_id(report, "agent.claude_auth")
        raw_envs = _check_detail(claude_check, "env_names")
        env_names = raw_envs if isinstance(raw_envs, list) else ["ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN"]
        for name in env_names:
            add_env("claude", str(name))
        _add_existing_file(paths, components, "claude", Path("~/.claude.json").expanduser(), required=False)
        _add_existing_file(paths, components, "claude", Path("~/.claude/settings.json").expanduser(), required=False)

    paths.extend(path for path in enterprise_user_env_paths(root) if path.is_file())
    files = _collect_files_from_paths(paths)
    return env_values, files, components


def _component(components: dict[str, Any], name: str) -> dict[str, Any]:
    entry = components.setdefault(name, {})
    return entry if isinstance(entry, dict) else {}


def _check_by_id(report: dict[str, Any], check_id: str) -> dict[str, Any]:
    for check in report.get("checks") or []:
        if isinstance(check, dict) and check.get("id") == check_id:
            return check
    return {}


def _check_passed(report: dict[str, Any], check_id: str) -> bool:
    return bool(_check_by_id(report, check_id).get("passed"))


def _check_detail(check: dict[str, Any], key: str) -> Any:
    details = check.get("details") if isinstance(check.get("details"), dict) else {}
    return details.get(key)


def _add_existing_file(paths: list[Path], components: dict[str, Any], component: str, path: Path, *, required: bool) -> None:
    if path.is_file():
        paths.append(path)
        _component(components, component).setdefault("files", []).append(_portable_restore_path(path))
        return
    if required:
        raise RuntimeError(f"{component} passed setup, but {path} was not found")


def _add_ssh_component(paths: list[Path], components: dict[str, Any], component: str, host: str) -> None:
    identity_files = _ssh_identity_files_for_host(host, component)
    if not identity_files:
        raise RuntimeError(f"{component}.ssh passed setup, but no explicit IdentityFile was found for {host} in ~/.ssh/config")
    ssh_dir = Path.home() / ".ssh"
    for name in ("config", "known_hosts"):
        path = ssh_dir / name
        if path.is_file():
            paths.append(path)
    portable_identity_files: list[str] = []
    for private_key in identity_files:
        if not _under_home_ssh(private_key):
            raise RuntimeError(f"{component}.ssh IdentityFile must be under ~/.ssh to be backed up safely: {private_key}")
        paths.append(private_key)
        portable_identity_files.append(_portable_restore_path(private_key))
        public_key = Path(os.fspath(private_key) + ".pub")
        if public_key.is_file():
            paths.append(public_key)
    _component(components, component).setdefault("ssh_hosts", []).append({
        "host": host,
        "identity_files": portable_identity_files,
    })


def _under_home_ssh(path: Path) -> bool:
    try:
        resolved = path.resolve()
        ssh_dir = (Path.home() / ".ssh").resolve()
    except OSError:
        return False
    return resolved == ssh_dir or ssh_dir in resolved.parents


def _ssh_identity_files_for_host(host: str, component: str = "") -> list[Path]:
    hostname = host.split("@", 1)[-1]
    identity_files = _ssh_config_identity_files(hostname)
    if identity_files:
        return identity_files
    if component == "github" and hostname == "github.com":
        return _ssh_authenticated_identity_files_for_host(host)
    return []


def _ssh_authenticated_identity_files_for_host(host: str) -> list[Path]:
    output = _ssh_verbose_auth_output(host)
    if not _ssh_debug_shows_publickey_auth(output):
        raise RuntimeError(
            f"github.ssh passed setup, but `ssh -vT {host}` did not confirm public-key authentication. "
            "Run `ssh -T git@github.com` again, or create/upload a managed GitHub SSH key from setup."
        )
    identity_files = _ssh_accepted_identity_paths_from_debug(output)
    if identity_files:
        return identity_files
    raise RuntimeError(
        "github.ssh passed setup, but the SSH debug output did not reveal a local private key file. "
        "This usually means GitHub authentication uses an agent-only key, a FIDO/security-key identity, "
        "a private key outside ~/.ssh, or a debug format that cannot be mapped safely. "
        "Create/upload a managed GitHub SSH key from setup, or add an explicit Host github.com IdentityFile entry in ~/.ssh/config."
    )


def _ssh_verbose_auth_output(host: str) -> str:
    try:
        result = subprocess.run(
            [
                "ssh",
                "-vT",
                "-o", "BatchMode=yes",
                "-o", "StrictHostKeyChecking=accept-new",
                "-o", "NumberOfPasswordPrompts=0",
                "-o", "ConnectTimeout=15",
                host,
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=SSH_AUTH_PROBE_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"github.ssh passed setup, but `ssh -vT {host}` timed out while locating the authenticated identity") from exc
    except OSError as exc:
        raise RuntimeError(f"github.ssh passed setup, but `ssh -vT {host}` could not run while locating the authenticated identity: {exc}") from exc
    return "\n".join(part for part in (result.stderr, result.stdout) if part)


def _ssh_debug_shows_publickey_auth(output: str) -> bool:
    lowered = output.lower()
    return "authenticated to " in lowered and 'using "publickey"' in lowered


def _ssh_accepted_identity_paths_from_debug(output: str) -> list[Path]:
    paths: list[Path] = []
    for line in output.splitlines():
        if "Server accepts key:" not in line:
            continue
        detail = line.split("Server accepts key:", 1)[1].strip()
        identity = _ssh_debug_identity_token(detail)
        path = _ssh_debug_identity_path(identity)
        if path and path.is_file():
            paths.append(path)
    return _dedupe_paths(paths)


def _ssh_debug_identity_token(detail: str) -> str:
    before_fingerprint = re.split(r"\s+(?:SHA256|MD5):", detail, maxsplit=1)[0].strip()
    key_types = {
        "RSA",
        "DSA",
        "ECDSA",
        "ED25519",
        "SSH-RSA",
        "SSH-ED25519",
        "RSA-SHA2-256",
        "RSA-SHA2-512",
        "RSA-CERT",
        "DSA-CERT",
        "ECDSA-CERT",
        "ED25519-CERT",
        "ECDSA-SK",
        "ED25519-SK",
        "ECDSA-SK-CERT",
        "ED25519-SK-CERT",
        "SK-SSH-ED25519@OPENSSH.COM",
        "SK-ECDSA-SHA2-NISTP256@OPENSSH.COM",
    }
    parts = before_fingerprint.split()
    for index, part in enumerate(parts):
        if part.upper() in key_types:
            return " ".join(parts[:index])
    return ""


def _ssh_debug_identity_path(identity: str) -> Path | None:
    value = identity.strip()
    if not value or not (value.startswith("/") or value.startswith("~")):
        return None
    return Path(os.path.expandvars(value)).expanduser()


def _ssh_config_identity_files(host: str | None = None) -> list[Path]:
    config_path = Path.home() / ".ssh" / "config"
    if not config_path.is_file():
        return []
    paths: list[Path] = []
    host_matches = host is None
    try:
        lines = config_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            parts = shlex.split(stripped, comments=True)
        except ValueError:
            continue
        if not parts:
            continue
        key = parts[0].lower()
        if key == "host":
            host_matches = host is None or _ssh_host_patterns_match(parts[1:], host)
            continue
        if key != "identityfile" or not host_matches or len(parts) < 2:
            continue
        path = _expand_ssh_path(parts[1], host or "")
        if path.is_file():
            paths.append(path)
    return _dedupe_paths(paths)


def _ssh_host_patterns_match(patterns: list[str], host: str) -> bool:
    matched = False
    host_l = host.lower()
    for pattern in patterns:
        negated = pattern.startswith("!")
        raw = (pattern[1:] if negated else pattern).lower()
        if fnmatch.fnmatchcase(host_l, raw):
            if negated:
                return False
            matched = True
    return matched


def _expand_ssh_path(value: str, host: str) -> Path:
    home = Path.home()
    hostname = host.split("@", 1)[-1]
    expanded = (
        value
        .replace("%%", "%")
        .replace("%d", os.fspath(home))
        .replace("%h", hostname)
        .replace("%r", host.split("@", 1)[0] if "@" in host else "")
    )
    expanded = os.path.expandvars(expanded)
    path = Path(expanded).expanduser()
    if not path.is_absolute():
        path = home / path
    return path


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        try:
            key = os.fspath(path.resolve())
        except OSError:
            key = os.fspath(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _collect_files(root: Path) -> list[dict[str, Any]]:
    paths = [Path(candidate).expanduser() for candidate in FILE_CANDIDATES]
    paths.extend(_ssh_config_file_candidates())
    paths.extend(path for path in enterprise_user_env_paths(root) if path.is_file())
    return _collect_files_from_paths(paths)


def _collect_files_from_paths(paths: list[Path]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for path in paths:
        try:
            resolved = str(path.resolve())
        except OSError:
            resolved = str(path)
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        try:
            st = path.stat()
            if st.st_size > MAX_FILE_BYTES:
                continue
            data = path.read_bytes()
            mode = stat.S_IMODE(st.st_mode)
        except OSError:
            continue
        items.append({
            "label": _file_label(path),
            "path": _portable_restore_path(path),
            "mode": mode,
            "size": len(data),
            "data": base64.b64encode(data).decode("ascii"),
        })
    return items


def _file_label(path: Path) -> str:
    home = Path.home()
    try:
        return "~/" + str(path.resolve().relative_to(home.resolve()))
    except Exception:
        return path.name


def _portable_restore_path(path: Path) -> str:
    home = Path.home()
    try:
        return "~/" + str(path.resolve().relative_to(home.resolve()))
    except Exception:
        return str(path)


def _ssh_config_file_candidates() -> list[Path]:
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.is_dir():
        return []
    candidates: list[Path] = []
    for name in ("config", "known_hosts", "known_hosts.old"):
        path = ssh_dir / name
        if path.is_file():
            candidates.append(path)
    for private_key in _ssh_config_identity_files():
        if not _under_home_ssh(private_key):
            continue
        candidates.append(private_key)
        public_key = Path(os.fspath(private_key) + ".pub")
        if public_key.is_file():
            candidates.append(public_key)
    return candidates


def _encrypt_payload(plaintext: bytes, password: str) -> dict[str, Any]:
    salt = os.urandom(16)
    nonce = os.urandom(16)
    enc_key, mac_key = _derive_keys(password, salt)
    ciphertext = _xor_stream(plaintext, enc_key, nonce)
    header = {
        "schema": BACKUP_SCHEMA,
        "version": 1,
        "created_at": utc_now(),
        "kdf": {
            "name": "pbkdf2_hmac_sha256",
            "iterations": KDF_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
        },
        "cipher": {
            "name": "hmac_sha256_stream_xor",
            "nonce": base64.b64encode(nonce).decode("ascii"),
        },
    }
    mac_input = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8") + ciphertext
    header["payload"] = base64.b64encode(ciphertext).decode("ascii")
    header["mac"] = base64.b64encode(hmac.new(mac_key, mac_input, hashlib.sha256).digest()).decode("ascii")
    return header


def _decrypt_payload(envelope: dict[str, Any], password: str) -> bytes:
    if envelope.get("schema") != BACKUP_SCHEMA:
        raise ValueError(f"unsupported backup schema: {envelope.get('schema')!r}")
    kdf = envelope.get("kdf") if isinstance(envelope.get("kdf"), dict) else {}
    cipher = envelope.get("cipher") if isinstance(envelope.get("cipher"), dict) else {}
    if kdf.get("name") != "pbkdf2_hmac_sha256" or cipher.get("name") != "hmac_sha256_stream_xor":
        raise ValueError("unsupported encrypted backup format")
    salt = base64.b64decode(str(kdf.get("salt") or ""))
    nonce = base64.b64decode(str(cipher.get("nonce") or ""))
    ciphertext = base64.b64decode(str(envelope.get("payload") or ""))
    mac = base64.b64decode(str(envelope.get("mac") or ""))
    enc_key, mac_key = _derive_keys(password, salt, int(kdf.get("iterations") or KDF_ITERATIONS))
    header = {
        "schema": envelope.get("schema"),
        "version": envelope.get("version"),
        "created_at": envelope.get("created_at"),
        "kdf": kdf,
        "cipher": cipher,
    }
    mac_input = json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8") + ciphertext
    expected = hmac.new(mac_key, mac_input, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("invalid password or corrupted user config backup")
    return _xor_stream(ciphertext, enc_key, nonce)


def _derive_keys(password: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> tuple[bytes, bytes]:
    material = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=64)
    return material[:32], material[32:]


def _xor_stream(data: bytes, key: bytes, nonce: bytes) -> bytes:
    out = bytearray(len(data))
    offset = 0
    counter = 0
    while offset < len(data):
        block = hmac.new(key, nonce + counter.to_bytes(8, "big"), hashlib.sha256).digest()
        chunk = data[offset:offset + len(block)]
        for i, value in enumerate(chunk):
            out[offset + i] = value ^ block[i]
        offset += len(chunk)
        counter += 1
    return bytes(out)


def _allowed_restore_path(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        home = Path.home().resolve()
        root_resolved = root.resolve()
    except OSError:
        return False
    allowed_roots = [
        home / ".ssh",
        home / ".config" / "gh",
        home / ".codex",
        home / ".claude",
        home / ".intern-agent-helper" / "enterprise",
        home / ".config" / "intern-agent-helper" / "enterprise",
        root_resolved / "enterprise",
    ]
    allowed_files = {home / ".claude.json", home / ".codeup_env"}
    if resolved in allowed_files:
        return True
    return any(resolved == base or base in resolved.parents for base in allowed_roots)


def _write_bytes_atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent == Path.home() / ".ssh":
        path.parent.chmod(0o700)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    safe_mode = _safe_restore_mode(path, mode)
    tmp.chmod(safe_mode)
    tmp.replace(path)
    path.chmod(safe_mode)


def _safe_restore_mode(path: Path, mode: int) -> int:
    name = path.name
    if path.parent == Path.home() / ".ssh":
        if name.endswith(".pub") or name in {"known_hosts", "known_hosts.old"}:
            return 0o644
        return 0o600
    return mode if mode and not (mode & 0o077) else 0o600


def _normalize_restored_ssh_permissions() -> None:
    ssh_dir = Path.home() / ".ssh"
    if not ssh_dir.exists():
        return
    ssh_dir.chmod(0o700)
    for path in ssh_dir.iterdir():
        if not path.is_file():
            continue
        path.chmod(_safe_restore_mode(path, stat.S_IMODE(path.stat().st_mode)))


def _restore_component_configs(payload: dict[str, Any]) -> list[str]:
    components = payload.get("components") if isinstance(payload.get("components"), dict) else {}
    configured: list[str] = []
    for component in components.values():
        if not isinstance(component, dict):
            continue
        ssh_hosts = component.get("ssh_hosts") if isinstance(component.get("ssh_hosts"), list) else []
        for item in ssh_hosts:
            if not isinstance(item, dict):
                continue
            host = str(item.get("host") or "").strip()
            identity_files = item.get("identity_files") if isinstance(item.get("identity_files"), list) else []
            if not host or not identity_files:
                continue
            identities = [Path(str(value)).expanduser() for value in identity_files if str(value).strip()]
            if not identities:
                continue
            for identity in identities:
                if not identity.is_file():
                    raise RuntimeError(f"restored SSH identity for {host} is missing: {identity}")
            _write_managed_ssh_config(host, identities)
            configured.append(host)
    return configured


def _write_managed_ssh_config(host: str, identities: list[Path]) -> None:
    hostname = host.split("@", 1)[-1]
    user = host.split("@", 1)[0] if "@" in host else "git"
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    config_path = ssh_dir / "config"
    safe_name = hostname.replace("/", "_").replace(":", "_")
    marker_start = f"# >>> intern-agents restored {safe_name}"
    marker_end = f"# <<< intern-agents restored {safe_name}"
    identity_lines = [f"  IdentityFile {identity}" for identity in identities]
    block = "\n".join([
        marker_start,
        f"Host {hostname}",
        f"  HostName {hostname}",
        f"  User {user}",
        *identity_lines,
        "  IdentitiesOnly yes",
        "  StrictHostKeyChecking accept-new",
        marker_end,
        "",
    ])
    current = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    start = current.find(marker_start)
    end = current.find(marker_end, start + len(marker_start)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        end = current.find("\n", end)
        end = len(current) if end < 0 else end + 1
        updated = current[:start] + block + current[end:]
    else:
        updated = (current.rstrip() + "\n\n" + block) if current.strip() else block
    config_path.write_text(updated, encoding="utf-8")
    config_path.chmod(0o600)
