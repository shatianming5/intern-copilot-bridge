"""Builtin Feishu messaging helpers for sending local artifacts to supervisors.

This module is intentionally independent from the external ``feishu-messaging``
skill. Credentials come from enterprise policy/secrets, and chat lookup is
scoped by ``(project, intern_name)`` so same-named interns in other workspaces do
not receive artifacts by accident.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable

from lib.enterprise_paths import daemon_policy_path, relay_secrets_path, work_root_path
from lib.enterprise_policy import load_enterprise_secrets, resolve_secret_value


BASE_URL = "https://open.feishu.cn/open-apis"
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 30 * 1024 * 1024

EXT_TO_FILE_TYPE = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}

HttpOpen = Callable[..., Any]
_TOKEN_CACHE: dict[str, Any] = {"cache_key": "", "token": "", "expires_at": 0.0}


class FeishuMessagingError(RuntimeError):
    """Base error for the builtin Feishu messaging command family."""


class FeishuCredentialError(FeishuMessagingError):
    """Raised when enterprise Feishu app credentials are unavailable."""


class FeishuRegistryError(FeishuMessagingError):
    """Raised when ``(project, intern_name)`` cannot be resolved to a chat."""


def _safe_segment(value: str) -> str:
    import re

    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._")
    return safe or value


def _read_json_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FeishuCredentialError(f"{label} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise FeishuCredentialError(f"{label} is invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FeishuCredentialError(f"{label} must contain a JSON object: {path}")
    return data


def load_feishu_credentials(work_root: str | os.PathLike[str] | None = None) -> tuple[str, str]:
    """Load Feishu app credentials from enterprise daemon policy/secrets.

    Preferred source is ``enterprise_policy/daemon/policy.json`` with
    ``feishu.app_id`` and ``feishu.app_secret``. If the daemon policy omits the
    secret, the relay secret bundle's ``feishu.app_secret`` is used.
    """

    root = work_root_path(work_root)
    policy_path = daemon_policy_path(root)
    policy = _read_json_file(policy_path, label="daemon policy")
    feishu = policy.get("feishu") if isinstance(policy.get("feishu"), dict) else {}
    app_id = str(feishu.get("app_id") or "").strip()
    app_secret = str(feishu.get("app_secret") or "").strip()
    secret_error = ""

    if not app_secret:
        secrets = load_enterprise_secrets(relay_secrets_path(root), required=False)
        if secrets.ok:
            entry = (secrets.data.get("secrets") or {}).get("feishu.app_secret")
            if isinstance(entry, dict):
                app_secret = resolve_secret_value(entry).strip()
        elif secrets.state != "missing_optional":
            secret_error = f"; secret bundle error: {secrets.error}"

    missing = []
    if not app_id:
        missing.append(f"feishu.app_id in daemon policy {policy_path}")
    if not app_secret:
        missing.append(
            f"feishu.app_secret in daemon policy {policy_path} or relay secrets {relay_secrets_path(root)}"
        )
    if missing:
        raise FeishuCredentialError("missing " + " and ".join(missing) + secret_error)
    return app_id, app_secret


def _registry_directory(work_root: str | os.PathLike[str] | None = None) -> Path:
    explicit = os.environ.get("FEISHU_REGISTRY_DIR")
    if explicit:
        return Path(explicit)
    return work_root_path(work_root) / ".feishu_registry"


def _registry_candidates(intern_name: str, project: str) -> list[str]:
    candidates = [
        f"{_safe_segment(project)}__{_safe_segment(intern_name)}.json",
        f"{_safe_segment(project)}__{intern_name}.json",
        f"{project}__{_safe_segment(intern_name)}.json",
        f"{project}__{intern_name}.json",
    ]
    result: list[str] = []
    for item in candidates:
        if Path(item).name != item:
            continue
        if item not in result:
            result.append(item)
    return result


def _entry_intern_name(entry: dict[str, Any]) -> str:
    return str(entry.get("internName") or entry.get("intern_name") or "").strip()


def _entry_project(entry: dict[str, Any]) -> str:
    return str(entry.get("project") or entry.get("workspace_id") or "").strip()


def _entry_chat_id(entry: dict[str, Any]) -> str:
    return str(entry.get("chatId") or entry.get("chat_id") or "").strip()


def _load_registry_entry(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise FeishuRegistryError(f"registry entry is invalid JSON: {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise FeishuRegistryError(f"registry entry must be a JSON object: {path}")
    return data


def _validate_registry_entry(entry: dict[str, Any], *, intern_name: str, project: str, path: Path) -> str:
    registered_name = _entry_intern_name(entry)
    if registered_name and registered_name != intern_name:
        raise FeishuRegistryError(
            f"registry entry internName mismatch at {path}: expected={intern_name} actual={registered_name}"
        )
    registered_project = _entry_project(entry)
    if registered_project and registered_project != project:
        raise FeishuRegistryError(
            f"registry entry project mismatch at {path}: expected={project} actual={registered_project}"
        )
    chat_id = _entry_chat_id(entry)
    if not chat_id:
        raise FeishuRegistryError(f"registry entry has no chatId/chat_id: {path}")
    return chat_id


def resolve_chat(
    intern_name: str,
    project: str,
    *,
    work_root: str | os.PathLike[str] | None = None,
    registry_dir: str | os.PathLike[str] | None = None,
) -> tuple[str, str]:
    """Resolve a supervisor chat by exact ``(project, intern_name)``.

    The legacy unscoped ``<intern_name>.json`` file is deliberately not used
    when ``project`` is provided. That file cannot distinguish duplicate intern
    names across workspaces.
    """

    intern_name = str(intern_name or "").strip()
    project = str(project or "").strip()
    if not intern_name:
        raise FeishuRegistryError("intern_name is required")
    if not project:
        raise FeishuRegistryError("project is required for scoped Feishu chat lookup")

    registry = Path(registry_dir) if registry_dir is not None else _registry_directory(work_root)
    if not registry.is_dir():
        raise FeishuRegistryError(f"Feishu registry directory not found: {registry}")

    for filename in _registry_candidates(intern_name, project):
        path = registry / filename
        if not path.is_file():
            continue
        entry = _load_registry_entry(path)
        return "chat_id", _validate_registry_entry(
            entry, intern_name=intern_name, project=project, path=path
        )

    matches: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted(registry.glob("*.json")):
        entry = _load_registry_entry(path)
        if _entry_intern_name(entry) == intern_name and _entry_project(entry) == project:
            matches.append((path, entry))
    if len(matches) == 1:
        path, entry = matches[0]
        return "chat_id", _validate_registry_entry(entry, intern_name=intern_name, project=project, path=path)
    if len(matches) > 1:
        paths = ", ".join(str(path) for path, _ in matches)
        raise FeishuRegistryError(
            f"ambiguous Feishu registry entries for project={project} intern_name={intern_name}: {paths}"
        )
    raise FeishuRegistryError(
        f"registry has no project-scoped entry for project={project} intern_name={intern_name} in {registry}"
    )


def _json_request(
    method: str,
    url: str,
    payload: dict[str, Any] | None = None,
    *,
    token: str = "",
    timeout: float = 15.0,
    http_open: HttpOpen | None = None,
) -> dict[str, Any]:
    body = json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    opener = http_open or urllib.request.urlopen
    with opener(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    if result.get("code") != 0:
        raise FeishuMessagingError(
            f"Feishu API failed: method={method} url={url} code={result.get('code')} msg={result.get('msg')}"
        )
    return result


def get_tenant_access_token(
    app_id: str,
    app_secret: str,
    *,
    http_open: HttpOpen | None = None,
) -> str:
    now = time.time()
    cache_key = app_id
    if _TOKEN_CACHE.get("cache_key") == cache_key and _TOKEN_CACHE.get("token") and now < float(_TOKEN_CACHE.get("expires_at") or 0) - 300:
        return str(_TOKEN_CACHE["token"])
    result = _json_request(
        "POST",
        f"{BASE_URL}/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        timeout=10,
        http_open=http_open,
    )
    token = str(result.get("tenant_access_token") or result.get("data", {}).get("tenant_access_token") or "")
    if not token:
        raise FeishuMessagingError("tenant_access_token response missing tenant_access_token")
    _TOKEN_CACHE.update({
        "cache_key": cache_key,
        "token": token,
        "expires_at": now + int(result.get("expire") or 7200),
    })
    return token


def _file_type_for(path: Path) -> str:
    return EXT_TO_FILE_TYPE.get(path.suffix.lower(), "stream")


def _multipart_body(fields: dict[str, str], file_field: str, path: Path, boundary_prefix: str) -> tuple[bytes, str]:
    filename = path.name
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    data = path.read_bytes()
    boundary = boundary_prefix + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
        + data
        + f"\r\n--{boundary}--\r\n".encode("utf-8")
    )
    return b"".join(parts), boundary


def _multipart_request(
    url: str,
    token: str,
    body: bytes,
    boundary: str,
    *,
    timeout: float,
    http_open: HttpOpen | None = None,
) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    opener = http_open or urllib.request.urlopen
    with opener(req, timeout=timeout) as resp:
        result = json.loads(resp.read())
    if result.get("code") != 0:
        raise FeishuMessagingError(f"Feishu upload failed: url={url} code={result.get('code')} msg={result.get('msg')}")
    return result


def upload_image(token: str, path: Path, *, http_open: HttpOpen | None = None) -> str:
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise FeishuMessagingError(
            f"{path} is {size / 1024 / 1024:.1f} MB, exceeds Feishu image limit of 10 MB"
        )
    body, boundary = _multipart_body({"image_type": "message"}, "image", path, "----img")
    result = _multipart_request(
        f"{BASE_URL}/im/v1/images",
        token,
        body,
        boundary,
        timeout=60,
        http_open=http_open,
    )
    image_key = str(result.get("data", {}).get("image_key") or "")
    if not image_key:
        raise FeishuMessagingError("image upload response missing image_key")
    return image_key


def upload_file(token: str, path: Path, *, http_open: HttpOpen | None = None) -> str:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise FeishuMessagingError(
            f"{path} is {size / 1024 / 1024:.1f} MB, exceeds Feishu file limit of 30 MB"
        )
    body, boundary = _multipart_body(
        {"file_type": _file_type_for(path), "file_name": path.name},
        "file",
        path,
        "----file",
    )
    result = _multipart_request(
        f"{BASE_URL}/im/v1/files",
        token,
        body,
        boundary,
        timeout=120,
        http_open=http_open,
    )
    file_key = str(result.get("data", {}).get("file_key") or "")
    if not file_key:
        raise FeishuMessagingError("file upload response missing file_key")
    return file_key


def send_message(
    token: str,
    receive_id_type: str,
    receive_id: str,
    msg_type: str,
    content: dict[str, Any],
    *,
    http_open: HttpOpen | None = None,
) -> str:
    result = _json_request(
        "POST",
        f"{BASE_URL}/im/v1/messages?receive_id_type={urllib.parse.quote(receive_id_type)}",
        {
            "receive_id": receive_id,
            "msg_type": msg_type,
            "content": json.dumps(content, ensure_ascii=False),
        },
        token=token,
        timeout=15,
        http_open=http_open,
    )
    message_id = str(result.get("data", {}).get("message_id") or "")
    if not message_id:
        raise FeishuMessagingError("message send response missing message_id")
    return message_id


def send_image(
    intern_name: str,
    project: str,
    file_path: str | os.PathLike[str],
    *,
    msg: str = "",
    work_root: str | os.PathLike[str] | None = None,
    http_open: HttpOpen | None = None,
) -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FeishuMessagingError(f"file not found: {path}")
    if path.suffix.lower() not in ALLOWED_IMAGE_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_IMAGE_EXTENSIONS))
        raise FeishuMessagingError(f"unsupported image extension {path.suffix!r}; allowed: {allowed}")

    app_id, app_secret = load_feishu_credentials(work_root)
    receive_id_type, receive_id = resolve_chat(intern_name, project, work_root=work_root)
    token = get_tenant_access_token(app_id, app_secret, http_open=http_open)
    image_key = upload_image(token, path, http_open=http_open)
    message_id = send_message(token, receive_id_type, receive_id, "image", {"image_key": image_key}, http_open=http_open)
    result = {
        "kind": "image",
        "target": {"receive_id_type": receive_id_type, "receive_id": receive_id},
        "message_id": message_id,
        "image_key": image_key,
    }
    if msg:
        result["text_message_id"] = send_message(
            token, receive_id_type, receive_id, "text", {"text": msg}, http_open=http_open
        )
    return result


def send_file(
    intern_name: str,
    project: str,
    file_path: str | os.PathLike[str],
    *,
    msg: str = "",
    work_root: str | os.PathLike[str] | None = None,
    http_open: HttpOpen | None = None,
) -> dict[str, Any]:
    path = Path(file_path).expanduser().resolve()
    if not path.is_file():
        raise FeishuMessagingError(f"file not found: {path}")

    app_id, app_secret = load_feishu_credentials(work_root)
    receive_id_type, receive_id = resolve_chat(intern_name, project, work_root=work_root)
    token = get_tenant_access_token(app_id, app_secret, http_open=http_open)
    file_key = upload_file(token, path, http_open=http_open)
    message_id = send_message(token, receive_id_type, receive_id, "file", {"file_key": file_key}, http_open=http_open)
    result = {
        "kind": "file",
        "target": {"receive_id_type": receive_id_type, "receive_id": receive_id},
        "message_id": message_id,
        "file_key": file_key,
    }
    if msg:
        result["text_message_id"] = send_message(
            token, receive_id_type, receive_id, "text", {"text": msg}, http_open=http_open
        )
    return result


def list_chat_members(
    intern_name: str,
    project: str,
    *,
    work_root: str | os.PathLike[str] | None = None,
    http_open: HttpOpen | None = None,
) -> list[dict[str, Any]]:
    app_id, app_secret = load_feishu_credentials(work_root)
    receive_id_type, chat_id = resolve_chat(intern_name, project, work_root=work_root)
    if receive_id_type != "chat_id":
        raise FeishuRegistryError(f"list members requires chat_id target, got {receive_id_type}")
    token = get_tenant_access_token(app_id, app_secret, http_open=http_open)

    members: list[dict[str, Any]] = []
    page_token = ""
    while True:
        query = {"member_id_type": "open_id", "page_size": "100"}
        if page_token:
            query["page_token"] = page_token
        result = _json_request(
            "GET",
            f"{BASE_URL}/im/v1/chats/{urllib.parse.quote(chat_id, safe='')}/members?"
            f"{urllib.parse.urlencode(query)}",
            None,
            token=token,
            timeout=15,
            http_open=http_open,
        )
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        items = data.get("items") if isinstance(data.get("items"), list) else []
        members.extend(item for item in items if isinstance(item, dict))
        if not data.get("has_more"):
            return members
        page_token = str(data.get("page_token") or "")
        if not page_token:
            return members


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--intern-name", required=True, help="current intern name from additionalContext")
    parser.add_argument("--project", required=True, help="current project from additionalContext")
    parser.add_argument("--work-root", default=None, help="WORK_AGENTS_ROOT override for tests/debugging")


def _print_send_result(result: dict[str, Any]) -> None:
    target = result.get("target") or {}
    kind = result.get("kind") or "artifact"
    key_name = "image_key" if kind == "image" else "file_key"
    print(
        f"{kind} sent: to={target.get('receive_id_type')}:{target.get('receive_id')} "
        f"message_id={result.get('message_id')} {key_name}={result.get(key_name)}"
    )
    if result.get("text_message_id"):
        print(f"text sent: message_id={result['text_message_id']}")


def _run_cli(action: Callable[[argparse.Namespace], Any], argv: list[str] | None = None) -> int:
    try:
        action_result = action(argv)
        return int(action_result or 0)
    except (FeishuMessagingError, OSError, ValueError) as exc:
        print(f"feishu messaging failed: {exc}", file=sys.stderr)
        return 1


def main_send_image(argv: list[str] | None = None) -> int:
    def action(inner_argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(description="Send a local image to the current intern supervisor Feishu group.")
        _add_common_args(parser)
        parser.add_argument("--file", required=True, help="image path (PNG/JPG/GIF/BMP/WEBP, <=10 MB)")
        parser.add_argument("--msg", default="", help="optional text note to send after the image")
        args = parser.parse_args(inner_argv)
        _print_send_result(send_image(
            args.intern_name,
            args.project,
            args.file,
            msg=args.msg,
            work_root=args.work_root,
        ))
        return 0

    return _run_cli(action, argv)


def main_send_file(argv: list[str] | None = None) -> int:
    def action(inner_argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(description="Send a local file to the current intern supervisor Feishu group.")
        _add_common_args(parser)
        parser.add_argument("--file", required=True, help="file path (<=30 MB)")
        parser.add_argument("--msg", default="", help="optional text note to send after the file")
        args = parser.parse_args(inner_argv)
        _print_send_result(send_file(
            args.intern_name,
            args.project,
            args.file,
            msg=args.msg,
            work_root=args.work_root,
        ))
        return 0

    return _run_cli(action, argv)


def main_list_chat_members(argv: list[str] | None = None) -> int:
    def action(inner_argv: list[str] | None = None) -> int:
        parser = argparse.ArgumentParser(description="List current intern supervisor Feishu group members as JSON.")
        _add_common_args(parser)
        args = parser.parse_args(inner_argv)
        members = list_chat_members(args.intern_name, args.project, work_root=args.work_root)
        json.dump(members, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    return _run_cli(action, argv)
