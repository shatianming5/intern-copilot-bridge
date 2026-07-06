"""Codeup API helpers that run only on user-side machines."""

from __future__ import annotations

import json
import os
import fnmatch
from pathlib import Path
import re
import shlex
import socket
import subprocess
import urllib.error
import urllib.request


CODEUP_API_BASE = "https://openapi-rdc.aliyuncs.com"


def extract_codeup_repo_path(repo_url: str) -> str:
    match = re.search(r"codeup\.aliyun\.com/(.+?)(?:\.git)?$", repo_url or "")
    if match:
        return match.group(1)
    match = re.search(r"codeup\.aliyun\.com:(.+?)(?:\.git)?$", repo_url or "")
    if match:
        return match.group(1)
    return ""


def _codeup_json_request(method: str, path: str, token: str, body: dict | None = None):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        CODEUP_API_BASE + path,
        headers={"Content-Type": "application/json", "x-yunxiao-token": token},
        data=data,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def describe_codeup_exception(exc: Exception) -> str:
    if not isinstance(exc, urllib.error.HTTPError):
        return str(exc)
    body = ""
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    payload = {}
    if body:
        try:
            parsed = json.loads(body)
            payload = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            payload = {}
    error_code = str(payload.get("errorCode") or payload.get("code") or "").strip()
    error_message = str(payload.get("errorMessage") or payload.get("message") or body or exc.reason).strip()
    request_id = str(payload.get("requestId") or payload.get("requestID") or payload.get("RequestId") or "").strip()
    parts = [f"Codeup API HTTP {exc.code}"]
    if error_code:
        parts.append(error_code)
    if error_message:
        parts.append(error_message)
    if request_id:
        parts.append(f"requestId={request_id}")
    message = ": ".join(parts)
    if exc.code == 403:
        message += "; regenerate the Codeup token with Code Management / SSH Key read-write permission."
    return message


def _codeup_json_get(path: str, token: str):
    return _codeup_json_request("GET", path, token)


def _codeup_json_post(path: str, token: str, body: dict):
    return _codeup_json_request("POST", path, token, body)


def _codeup_org_id(token: str) -> str:
    configured = (
        os.environ.get("CODEUP_ORG_ID", "").strip()
        or os.environ.get("CODEUP_ORGANIZATION_ID", "").strip()
    )
    if configured:
        return configured
    orgs = _codeup_json_get("/oapi/v1/platform/organizations", token)
    if not isinstance(orgs, list) or not orgs:
        raise RuntimeError("Codeup organization list is empty")
    return str(orgs[0].get("id") or "")


def _codeup_repository_id(token: str, org_id: str, repo_path: str) -> str:
    page = 1
    per_page = 50
    while True:
        data = _codeup_json_get(
            f"/oapi/v1/codeup/organizations/{org_id}/repositories?page={page}&perPage={per_page}",
            token,
        )
        repos = data if isinstance(data, list) else data.get("result", [])
        if not repos:
            return ""
        for repo in repos:
            path = str(repo.get("pathWithNamespace") or "")
            if path == repo_path or (repo_path and path.endswith(repo_path)):
                return str(repo.get("id") or "")
        if len(repos) < per_page:
            return ""
        page += 1


def codeup_branch_protection(repo_url: str) -> tuple[bool | None, str, str]:
    token = os.environ.get("CODEUP_ACCESS_TOKEN", "").strip()
    if not token:
        return None, "", "CODEUP_ACCESS_TOKEN is not set"
    repo_path = extract_codeup_repo_path(repo_url)
    if not repo_path:
        return None, "", "repo_url is not a Codeup URL"
    try:
        org_id = _codeup_org_id(token)
        repo_id = _codeup_repository_id(token, org_id, repo_path)
        if not repo_id:
            return None, "", f"repository id not found for {repo_path}"
        branches = _codeup_json_get(
            f"/oapi/v1/codeup/organizations/{org_id}/repositories/{repo_id}/branches",
            token,
        )
        if not isinstance(branches, list):
            return None, "", "Codeup branches response is not a list"
        default = next((item for item in branches if isinstance(item, dict) and item.get("defaultBranch")), None)
        if not default:
            default = branches[0] if branches and isinstance(branches[0], dict) else None
        if not default:
            return None, "", "Codeup branch list is empty"
        return bool(default.get("protected")), str(default.get("name") or ""), ""
    except Exception as exc:
        return None, "", str(exc)


def _ensure_public_key(private_key: Path, public_key: Path) -> None:
    if public_key.is_file():
        return
    if not private_key.is_file():
        return
    result = subprocess.run(
        ["ssh-keygen", "-y", "-f", os.fspath(private_key)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    public_key.write_text(result.stdout.strip() + "\n", encoding="utf-8")
    public_key.chmod(0o644)


def _ssh_config_identity_files(host: str, home: Path | None = None) -> list[Path]:
    root = home or Path.home()
    config_path = root / ".ssh" / "config"
    if not config_path.is_file():
        return []
    identities: list[Path] = []
    host_matches = False
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
            host_matches = _ssh_host_patterns_match(parts[1:], host)
            continue
        if key == "identityfile" and host_matches and len(parts) >= 2:
            identities.append(_expand_ssh_identity_path(parts[1], root, host))
    return _dedupe_paths(identities)


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


def _expand_ssh_identity_path(value: str, home: Path, host: str) -> Path:
    expanded = (
        value
        .replace("%%", "%")
        .replace("%d", os.fspath(home))
        .replace("%h", host)
        .replace("%r", "git")
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


def ensure_codeup_ssh_key_pair(home: Path | None = None) -> tuple[Path, Path, bool]:
    """Return a local Codeup SSH key pair, generating a dedicated key when needed."""

    root = home or Path.home()
    ssh_dir = root / ".ssh"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    candidates = [
        *_ssh_config_identity_files("codeup.aliyun.com", root),
        ssh_dir / "id_ed25519_codeup",
        ssh_dir / "id_rsa_codeup",
    ]
    for private_key in _dedupe_paths(candidates):
        public_key = Path(os.fspath(private_key) + ".pub")
        if private_key.is_file():
            _ensure_public_key(private_key, public_key)
            if public_key.is_file():
                private_key.chmod(0o600)
                public_key.chmod(0o644)
                return private_key, public_key, False

    private_key = ssh_dir / "id_ed25519_codeup"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", "intern-agents-codeup", "-f", os.fspath(private_key)],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )
    public_key = Path(os.fspath(private_key) + ".pub")
    private_key.chmod(0o600)
    public_key.chmod(0o644)
    return private_key, public_key, True


def ensure_codeup_ssh_config(private_key: Path, home: Path | None = None) -> Path:
    root = home or Path.home()
    ssh_dir = root / ".ssh"
    ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    ssh_dir.chmod(0o700)
    config_path = ssh_dir / "config"
    marker_start = "# >>> intern-agents codeup"
    marker_end = "# <<< intern-agents codeup"
    block = "\n".join([
        marker_start,
        "Host codeup.aliyun.com",
        "  HostName codeup.aliyun.com",
        "  User git",
        f"  IdentityFile {private_key}",
        "  IdentitiesOnly yes",
        "  StrictHostKeyChecking accept-new",
        marker_end,
        "",
    ])
    current = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""
    pattern = re.compile(rf"{re.escape(marker_start)}.*?{re.escape(marker_end)}\n?", re.S)
    if pattern.search(current):
        updated = pattern.sub(block, current)
    else:
        updated = (current.rstrip() + "\n\n" + block) if current.strip() else block
    config_path.write_text(updated, encoding="utf-8")
    config_path.chmod(0o600)
    return config_path


def _codeup_key_items(response) -> list[dict]:
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    if isinstance(response, dict):
        for key in ("result", "items", "keys", "data"):
            value = response.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _list_codeup_ssh_keys(token: str, org_id: str) -> list[dict]:
    return _codeup_key_items(_codeup_json_get(f"/oapi/v1/codeup/organizations/{org_id}/keys", token))


def codeup_ssh_key_api_access(token: str) -> dict:
    token = (token or "").strip()
    if not token:
        return {
            "ok": False,
            "code": "CODEUP_TOKEN_MISSING",
            "message": "CODEUP_ACCESS_TOKEN is not set",
        }
    try:
        org_id = _codeup_org_id(token)
        _list_codeup_ssh_keys(token, org_id)
        return {
            "ok": True,
            "code": "OK",
            "message": "Codeup SSH Key API is accessible",
            "organization_id": org_id,
        }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "code": "CODEUP_TOKEN_PERMISSION_DENIED" if exc.code == 403 else "CODEUP_TOKEN_API_FAILED",
            "message": describe_codeup_exception(exc),
        }
    except Exception as exc:
        return {
            "ok": False,
            "code": "CODEUP_TOKEN_API_FAILED",
            "message": str(exc),
        }


def _public_key_uploaded(token: str, org_id: str, public_key: str) -> bool:
    return any(str(item.get("key") or "").strip() == public_key for item in _list_codeup_ssh_keys(token, org_id))


def upload_codeup_ssh_key(token: str, public_key: str, title: str | None = None) -> dict:
    token = (token or "").strip()
    public_key = (public_key or "").strip()
    if not token:
        raise RuntimeError("CODEUP_ACCESS_TOKEN is not set")
    if not public_key:
        raise RuntimeError("Codeup SSH public key is empty")
    org_id = _codeup_org_id(token)
    if _public_key_uploaded(token, org_id, public_key):
        return {"uploaded": False, "already_present": True, "organization_id": org_id}
    body = {
        "key": public_key,
        "keyScope": "ALL",
        "title": title or f"Intern Agents {socket.gethostname()}",
    }
    try:
        created = _codeup_json_post(f"/oapi/v1/codeup/organizations/{org_id}/keys", token, body)
    except urllib.error.HTTPError:
        if _public_key_uploaded(token, org_id, public_key):
            return {"uploaded": False, "already_present": True, "organization_id": org_id}
        raise
    return {"uploaded": True, "already_present": False, "organization_id": org_id, "record": created}


def setup_codeup_ssh_key(token: str, home: Path | None = None) -> dict:
    private_key, public_key_path, generated = ensure_codeup_ssh_key_pair(home)
    configured_keys = _ssh_config_identity_files("codeup.aliyun.com", home)
    if any(_same_path(private_key, configured) for configured in configured_keys):
        config_path = (home or Path.home()) / ".ssh" / "config"
        if config_path.is_file():
            config_path.chmod(0o600)
    else:
        config_path = ensure_codeup_ssh_config(private_key, home)
    public_key = public_key_path.read_text(encoding="utf-8").strip()
    uploaded = upload_codeup_ssh_key(token, public_key)
    return {
        "private_key": os.fspath(private_key),
        "public_key": os.fspath(public_key_path),
        "config": os.fspath(config_path),
        "generated": generated,
        **uploaded,
    }


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return os.fspath(left) == os.fspath(right)
