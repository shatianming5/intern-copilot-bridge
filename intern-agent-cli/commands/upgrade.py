"""Client-side VSIX upgrade command.

The relay is only a release metadata/file server here. Relay process upgrades
remain an administrator action (`intern-adminctl relay sync/restart`).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from lib.enterprise_paths import daemon_owner_path


SCHEMA = "intern-agents.client-upgrade.v1"
EXTENSION_ID = "llm-intern-agents.intern-agent-helper"
EXTENSION_DIR_PREFIX = EXTENSION_ID + "-"
SKIP_HOOK_DIRS = {"__pycache__", "tests", ".pytest_cache", "llm_intern_logs"}
HOOK_SETTINGS_FILES = {"claude_settings.json", "codex_settings.toml"}
HOOK_ENTRIES: list[tuple[str, str]] = [
    ("SessionStart", "session_start_hook.py"),
    ("UserPromptSubmit", "user_prompt_hook.py"),
    ("PreToolUse", "pre_tool_hook.py"),
    ("PostToolUse", "post_tool_hook.py"),
    ("SubagentStart", "subagent_start_hook.py"),
    ("SubagentStop", "subagent_stop_hook.py"),
    ("PreCompact", "pre_compact_hook.py"),
    ("Stop", "stop_hook.py"),
]


def setup_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("upgrade", help="Upgrade the local VS Code client VSIX from the relay release feed")
    p.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    p.add_argument("--check-only", action="store_true", help="Only check whether an update is available")
    p.add_argument("--dry-run", action="store_true", help="Alias of --check-only")
    p.add_argument("--relay-http-url", help="Relay HTTP URL override; otherwise read daemon _owner.json")
    p.add_argument("--token", help="Relay token override; otherwise read daemon _owner.json")
    p.add_argument("--current-version", help="Current extension version override")
    p.add_argument("--current-hash", help=argparse.SUPPRESS)
    p.add_argument("--download-dir", help="Directory for the downloaded VSIX; default: a temp directory")
    p.add_argument("--work-root", help="WORK_AGENTS_ROOT override")
    p.add_argument("--no-restart-daemon", action="store_true", help="Do not schedule daemon restart after a successful upgrade")
    p.set_defaults(func=run)


def _work_root(args: argparse.Namespace) -> Path:
    return Path(str(getattr(args, "work_root", "") or os.environ.get("WORK_AGENTS_ROOT") or "/work-agents"))


def _read_json_file(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError(f"{path} must contain a JSON object")
    return data


def _relay_netloc(hostname: str, port: int | None) -> str:
    netloc = hostname
    if ":" in netloc and not netloc.startswith("["):
        netloc = f"[{netloc}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return netloc


def _relay_http_url_from_any(relay_url: str, relay_http_url: str = "") -> str:
    if relay_http_url.strip():
        return relay_http_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(relay_url)
    if parsed.scheme not in {"ws", "wss", "http", "https"} or not parsed.hostname:
        raise RuntimeError("relay_http_url is missing and relay_url is invalid")
    if parsed.scheme in {"http", "https"}:
        return urllib.parse.urlunparse((parsed.scheme, _relay_netloc(parsed.hostname, parsed.port), "", "", "", "")).rstrip("/")
    http_scheme = "https" if parsed.scheme == "wss" else "http"
    http_port = parsed.port - 1 if parsed.port and parsed.port > 1 else parsed.port
    return urllib.parse.urlunparse((http_scheme, _relay_netloc(parsed.hostname, http_port), "", "", "", "")).rstrip("/")


def _relay_connection(args: argparse.Namespace) -> tuple[str, str, str]:
    work_root = _work_root(args)
    relay_http_url = str(getattr(args, "relay_http_url", "") or "").strip()
    token = str(getattr(args, "token", "") or "").strip()
    owner_path = daemon_owner_path(work_root)
    owner: dict = {}
    if not (relay_http_url and token):
        try:
            owner = _read_json_file(owner_path)
        except FileNotFoundError as exc:
            raise RuntimeError(f"relay owner config missing: {owner_path}") from exc
        except Exception as exc:
            raise RuntimeError(f"relay owner config invalid: {owner_path}: {exc}") from exc
    if not relay_http_url:
        relay_http_url = _relay_http_url_from_any(
            str(owner.get("relay_url") or ""),
            str(owner.get("relay_http_url") or ""),
        )
    if not token:
        token = str(owner.get("relay_token") or "").strip()
    if not token:
        raise RuntimeError(f"relay token missing in {owner_path}")
    return relay_http_url.rstrip("/"), token, os.fspath(owner_path)


def _current_extension_dir() -> Path | None:
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "package.json").is_file() and (parent / "bundled-cli").is_dir():
            return parent
    return None


def _current_version(args: argparse.Namespace) -> str:
    override = str(getattr(args, "current_version", "") or "").strip()
    if override:
        return override
    ext_dir = _current_extension_dir()
    if not ext_dir:
        raise RuntimeError("current extension directory is unavailable; pass --current-version")
    package_json = _read_json_file(ext_dir / "package.json")
    version = str(package_json.get("version") or "").strip()
    if not version:
        raise RuntimeError(f"current extension package.json missing version: {ext_dir / 'package.json'}")
    return version


def _version_parts(version: str) -> tuple[int, ...]:
    main = version.strip().split("-", 1)[0]
    if not main:
        return (0,)
    parts = []
    for piece in main.split("."):
        if not piece.isdigit():
            return (0,)
        parts.append(int(piece))
    return tuple(parts or [0])


def _is_newer(latest: str, current: str) -> bool:
    left = _version_parts(latest)
    right = _version_parts(current)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) > right + (0,) * (width - len(right))


def _request_json(url: str, token: str, *, urlopen=urllib.request.urlopen) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"relay release query failed: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"relay release query failed: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("relay release query returned non-object JSON")
    return data


def _resolve_download_url(relay_http_url: str, release: dict) -> str:
    raw = str(release.get("download_url") or release.get("download_path") or "").strip()
    if not raw:
        raise RuntimeError("release metadata missing download_url/download_path")
    return urllib.parse.urljoin(relay_http_url.rstrip("/") + "/", raw)


def _download_vsix(url: str, token: str, dest: Path, *, urlopen=urllib.request.urlopen) -> None:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urlopen(req, timeout=60) as resp:
            with dest.open("wb") as f:
                shutil.copyfileobj(resp, f)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"VSIX download failed: HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"VSIX download failed: {exc}") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


SKIP_EXTENSION_HASH_DIRS = {"__pycache__", ".git", ".pytest_cache", "llm_intern_logs"}


def _extension_hash_skip(relative: str) -> bool:
    parts = Path(relative).parts
    return relative == ".vsixmanifest" or any(part in SKIP_EXTENSION_HASH_DIRS for part in parts) or relative.endswith(".pyc")


def _normalize_extension_hash_data(relative: str, data: bytes) -> bytes:
    if relative != "extension/package.json":
        return data
    try:
        package_json = json.loads(data.decode("utf-8"))
    except Exception:
        return data
    if isinstance(package_json, dict):
        package_json.pop("__metadata", None)
        return json.dumps(package_json, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return data


def _hash_extension_member(digest, relative: str, data: bytes) -> None:
    data = _normalize_extension_hash_data(relative, data)
    digest.update(relative.encode("utf-8"))
    digest.update(b"\0")
    digest.update(data)
    digest.update(b"\0")


def _extension_dir_content_sha256(ext_dir: Path) -> str:
    digest = hashlib.sha256()
    files = []
    for root, dirnames, filenames in os.walk(ext_dir):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_EXTENSION_HASH_DIRS)
        for filename in sorted(filenames):
            path = Path(root) / filename
            rel = path.relative_to(ext_dir).as_posix()
            if _extension_hash_skip(rel):
                continue
            files.append((f"extension/{rel}", path))
    for rel, path in sorted(files, key=lambda item: item[0]):
        _hash_extension_member(digest, rel, path.read_bytes())
    return digest.hexdigest()


def _current_extension_hash(args: argparse.Namespace) -> str:
    override = str(getattr(args, "current_hash", "") or "").strip()
    if override:
        return override
    ext_dir = _current_extension_dir()
    if not ext_dir:
        return ""
    return _extension_dir_content_sha256(ext_dir)


def _release_content_hash(release: dict) -> str:
    for key in ("content_sha256", "extension_sha256", "content_hash", "hash"):
        value = str(release.get(key) or "").strip()
        if value:
            return value
    return ""


def _download_dir(args: argparse.Namespace) -> Path:
    explicit = str(getattr(args, "download_dir", "") or "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    return Path(tempfile.mkdtemp(prefix="intern-agent-upgrade-"))


def _server_code_cli(home: Path) -> Path | None:
    root = home / ".vscode-server-insiders" / "cli" / "servers"
    candidates = sorted(
        root.glob("*/server/bin/code-server-insiders"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _install_command(vsix_path: Path, *, environ: dict[str, str] | None = None, home: Path | None = None) -> list[str]:
    env = environ or os.environ
    client_cmd = str(env.get("VSCODE_CLIENT_COMMAND") or "").strip()
    has_vscode_ipc = bool(env.get("VSCODE_IPC_HOOK_CLI") or client_cmd)
    if client_cmd:
        return [*shlex.split(client_cmd), "--install-extension", os.fspath(vsix_path), "--force"]
    if has_vscode_ipc:
        code_insiders = shutil.which("code-insiders")
        if not code_insiders:
            raise RuntimeError("code-insiders not found while VS Code IPC is active")
        return [code_insiders, "--install-extension", os.fspath(vsix_path), "--force"]
    home = home or Path.home()
    server_cli = _server_code_cli(home)
    if not server_cli:
        raise RuntimeError("VS Code Server CLI not found under ~/.vscode-server-insiders/cli/servers")
    extensions_dir = home / ".vscode-server-insiders" / "extensions"
    return [
        os.fspath(server_cli),
        "--install-extension",
        os.fspath(vsix_path),
        "--force",
        "--extensions-dir",
        os.fspath(extensions_dir),
    ]


def _run_install(command: list[str], *, runner=subprocess.run) -> dict:
    result = runner(command, text=True, capture_output=True, timeout=180)
    return {
        "command": command,
        "returncode": int(result.returncode),
        "stdout": str(result.stdout or "")[-4000:],
        "stderr": str(result.stderr or "")[-4000:],
    }


def _zip_members_under(zf: zipfile.ZipFile, prefix: str) -> list[str]:
    prefix = prefix.rstrip("/") + "/"
    return [name for name in zf.namelist() if name.startswith(prefix) and not name.endswith("/")]


def _zip_read_text(zf: zipfile.ZipFile, name: str) -> str | None:
    try:
        return zf.read(name).decode("utf-8")
    except KeyError:
        return None


def _safe_hook_member(relative: str) -> bool:
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return False
    return not any(part in SKIP_HOOK_DIRS for part in parts[:-1])


def _clear_hooks_payload(hooks_dir: Path) -> None:
    hooks_dir.mkdir(parents=True, exist_ok=True)
    for entry in hooks_dir.iterdir():
        if entry.name in {"hooks.json", ".version"}:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def _write_hook_member(zf: zipfile.ZipFile, member: str, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zf.open(member) as src, destination.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    return 1


def _render_claude_settings(raw: str) -> str:
    python_cmd = shlex.quote(sys.executable or "python3")
    return raw.replace('exec python3 "$S"', f'exec {python_cmd} "$S"')


def _write_hooks_json(hooks_dir: Path) -> None:
    python_cmd = shlex.quote(sys.executable or "python3")
    hooks: dict[str, list[dict[str, str]]] = {}
    for event, script in HOOK_ENTRIES:
        abs_script = hooks_dir / script
        script_text = os.fspath(abs_script).replace('"', r'\"')
        hooks[event] = [{
            "type": "command",
            "command": f"bash -c 'S=\"{script_text}\"; [ -f \"$S\" ] || exit 0; exec {python_cmd} \"$S\"'",
        }]
    (hooks_dir / "hooks.json").write_text(
        json.dumps({"version": 1, "hooks": hooks}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _candidate_intern_profile_dirs(work_root: Path) -> list[Path]:
    bases = [work_root, work_root / "interns"]
    bases.extend(work_root.glob("state/v1/*/interns"))
    bases.extend(work_root.glob("state/v1/*/*/interns"))
    seen: set[Path] = set()
    result: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if not entry.is_dir() or not entry.name.startswith("intern_"):
                continue
            resolved = entry.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            result.append(entry)
    return result


def _replace_symlink(path: Path, target: Path) -> bool:
    if path.is_symlink() and os.readlink(path) == os.fspath(target):
        return False
    if path.exists() or path.is_symlink():
        path.unlink()
    path.symlink_to(target)
    return True


def _relink_runtime_settings(work_root: Path, github_dir: Path) -> dict:
    claude_target = github_dir / "claude_settings.json"
    codex_target = github_dir / "codex_settings.toml"
    claude_count = 0
    codex_count = 0
    scanned = 0
    for profile in _candidate_intern_profile_dirs(work_root):
        scanned += 1
        claude_dir = profile / ".claude"
        if claude_dir.is_dir() and claude_target.exists():
            if _replace_symlink(claude_dir / "settings.json", claude_target):
                claude_count += 1
        codex_dir = profile / ".codex"
        if codex_dir.is_dir() and codex_target.exists():
            if _replace_symlink(codex_dir / "config.toml", codex_target):
                codex_count += 1
    return {
        "profiles_scanned": scanned,
        "claude_settings_links": claude_count,
        "codex_settings_links": codex_count,
    }


def _sync_hooks_from_vsix(vsix_path: Path, work_root: Path, version: str) -> dict:
    github_dir = work_root / ".github"
    hooks_dir = github_dir / "hooks"
    github_dir.mkdir(parents=True, exist_ok=True)
    _clear_hooks_payload(hooks_dir)

    copied = 0
    settings_written: list[str] = []
    with zipfile.ZipFile(vsix_path) as zf:
        hook_members = _zip_members_under(zf, "extension/hooks")
        if not hook_members:
            raise RuntimeError("downloaded VSIX has no extension/hooks payload")
        for member in hook_members:
            relative = member[len("extension/hooks/"):]
            if not relative or not _safe_hook_member(relative):
                continue
            if relative in HOOK_SETTINGS_FILES:
                raw = _zip_read_text(zf, member)
                if raw is None:
                    continue
                if relative == "claude_settings.json":
                    raw = _render_claude_settings(raw)
                (github_dir / relative).write_text(raw, encoding="utf-8")
                settings_written.append(relative)
                continue
            copied += _write_hook_member(zf, member, hooks_dir / relative)

    (hooks_dir / ".version").write_text(version, encoding="utf-8")
    _write_hooks_json(hooks_dir)
    links = _relink_runtime_settings(work_root, github_dir)
    return {
        "synced": True,
        "version": version,
        "hooks_dir": os.fspath(hooks_dir),
        "hook_files_copied": copied,
        "settings_written": settings_written,
        "hooks_json": os.fspath(hooks_dir / "hooks.json"),
        "version_file": os.fspath(hooks_dir / ".version"),
        **links,
    }


def _extension_roots(home: Path) -> list[Path]:
    return [
        home / ".vscode-server-insiders" / "extensions",
        home / ".vscode-server" / "extensions",
        home / ".vscode-insiders" / "extensions",
        home / ".vscode" / "extensions",
    ]


def _find_installed_extension_dir(version: str, *, home: Path | None = None) -> Path | None:
    target = f"{EXTENSION_DIR_PREFIX}{version}"
    home = home or Path.home()
    current = _current_extension_dir()
    roots = _extension_roots(home)
    if current:
        roots.insert(0, current.parent)
    for root in roots:
        candidate = root / target
        if (candidate / "bundled-cli" / "internctl.py").is_file():
            return candidate
    return None


def _inspect_installed_extension(ext_dir: Path, version: str, expected_content_sha256: str) -> dict:
    package_version = ""
    package_error = ""
    content_sha256 = ""
    content_error = ""
    try:
        package_json = _read_json_file(ext_dir / "package.json")
        package_version = str(package_json.get("version") or "").strip()
    except Exception as exc:
        package_error = str(exc)
    try:
        content_sha256 = _extension_dir_content_sha256(ext_dir)
    except Exception as exc:
        content_error = str(exc)
    version_matches = bool(package_version and package_version == version)
    content_matches = (
        content_sha256.lower() == expected_content_sha256.lower()
        if expected_content_sha256 and content_sha256
        else None
    )
    verified = version_matches and not content_error and content_matches is not False
    result = {
        "verified": verified,
        "dir": os.fspath(ext_dir),
        "version": package_version,
        "expected_version": version,
        "version_matches": version_matches,
        "content_sha256": content_sha256,
        "expected_content_sha256": expected_content_sha256,
        "content_matches": content_matches,
    }
    if package_error:
        result["package_error"] = package_error
    if content_error:
        result["content_error"] = content_error
    return result


def _wrapper_bin_dir() -> Path:
    explicit = os.environ.get("INTERN_CLI_BIN_DIR", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        return path
    usr_local = Path("/usr/local/bin")
    if usr_local.is_dir() and os.access(usr_local, os.W_OK):
        return usr_local
    local = Path.home() / ".local" / "bin"
    local.mkdir(parents=True, exist_ok=True)
    return local


def _write_cli_wrappers(extension_dir: Path) -> dict:
    bundle = extension_dir / "bundled-cli"
    scripts = {
        "internctl": bundle / "internctl.py",
        "intern-adminctl": bundle / "intern-adminctl.py",
        "codeup_pr": bundle / "codeup_pr.py",
    }
    bin_dir = _wrapper_bin_dir()
    written = []
    missing = []
    python_cmd = sys.executable or "python3"
    for command_name, script_path in scripts.items():
        if not script_path.is_file():
            missing.append(command_name)
            continue
        target = bin_dir / command_name
        target.write_text(f'#!/bin/sh\nexec "{python_cmd}" "{script_path}" "$@"\n', encoding="utf-8")
        target.chmod(0o755)
        written.append(os.fspath(target))
    return {
        "updated": bool(written),
        "bin_dir": os.fspath(bin_dir),
        "targets": written,
        "missing": missing,
        "extension_dir": os.fspath(extension_dir),
    }


def _daemon_runtime_snapshot(work_root: Path) -> dict:
    pid_file = Path(os.environ.get("FEISHU_DAEMON_ADDR_FILE") or "/tmp/feishu_daemon.json")
    try:
        payload = _read_json_file(pid_file)
    except Exception:
        return {
            "running": False,
            "restart_required": False,
            "pid_file": os.fspath(pid_file),
            "note": "daemon pid file not found or unreadable",
        }
    pid = int(payload.get("pid") or 0)
    payload_root = str(payload.get("work_agents_root") or "")
    if payload_root and os.path.abspath(payload_root) != os.path.abspath(os.fspath(work_root)):
        return {
            "running": False,
            "restart_required": False,
            "pid": pid or None,
            "pid_file": os.fspath(pid_file),
            "current_work_agents_root": payload_root,
            "note": "daemon pid file belongs to a different WORK_AGENTS_ROOT",
        }
    running = False
    if pid:
        try:
            os.kill(pid, 0)
            running = True
        except OSError:
            running = False
    return {
        "running": running,
        "restart_required": running,
        "pid": pid or None,
        "pid_file": os.fspath(pid_file),
        "current_bundle_dir": payload.get("bundle_dir") or "",
        "note": "daemon is running" if running else "daemon is not running",
    }


def _daemon_restart_log(work_root: Path) -> Path:
    path = work_root / "llm_intern_logs" / "_system" / "client_upgrade" / "daemon-restart.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _schedule_daemon_restart(args: argparse.Namespace, installed_ext_dir: Path | None, daemon: dict) -> dict:
    if getattr(args, "no_restart_daemon", False):
        return {
            **daemon,
            "restart_required": bool(daemon.get("running")),
            "restart_scheduled": False,
            "reason": "disabled by --no-restart-daemon",
        }
    if not daemon.get("running"):
        return {
            **daemon,
            "restart_required": False,
            "restart_scheduled": False,
            "reason": "daemon is not running",
        }
    work_root = _work_root(args)
    cli = (
        installed_ext_dir / "bundled-cli" / "internctl.py"
        if installed_ext_dir
        else Path(__file__).resolve().parents[1] / "internctl.py"
    )
    if not cli.is_file():
        return {
            **daemon,
            "restart_required": True,
            "restart_scheduled": False,
            "reason": f"internctl.py not found for daemon restart: {cli}",
        }
    log_path = _daemon_restart_log(work_root)
    env = os.environ.copy()
    env["WORK_AGENTS_ROOT"] = os.fspath(work_root)
    command = [
        "sh",
        "-c",
        f"sleep 3; exec {shlex.quote(sys.executable or 'python3')} {shlex.quote(os.fspath(cli))} daemon restart >> {shlex.quote(os.fspath(log_path))} 2>&1",
    ]
    try:
        subprocess.Popen(
            command,
            cwd=os.fspath(work_root),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception as exc:
        return {
            **daemon,
            "restart_required": True,
            "restart_scheduled": False,
            "reason": f"failed to schedule daemon restart: {exc}",
            "log": os.fspath(log_path),
        }
    return {
        **daemon,
        "restart_required": False,
        "restart_scheduled": True,
        "restart_delay_seconds": 3,
        "restart_command": [os.fspath(cli), "daemon", "restart"],
        "log": os.fspath(log_path),
        "note": "daemon restart scheduled after upgrade result is returned",
    }


def _base_report(args: argparse.Namespace) -> dict:
    return {
        "schema": SCHEMA,
        "ok": False,
        "client_only": True,
        "relay_upgrade": "manual_admin",
        "work_agents_root": os.fspath(_work_root(args)),
    }


def perform_upgrade(
    args: argparse.Namespace,
    *,
    urlopen=urllib.request.urlopen,
    runner=subprocess.run,
    environ: dict[str, str] | None = None,
) -> tuple[int, dict]:
    report = _base_report(args)
    try:
        relay_http_url, token, owner_path = _relay_connection(args)
        current_version = _current_version(args)
        latest_response = _request_json(f"{relay_http_url}/api/releases/latest", token, urlopen=urlopen)
        release = latest_response.get("release") if isinstance(latest_response.get("release"), dict) else latest_response
        latest_version = str(release.get("version") or "").strip()
        if not latest_version:
            raise RuntimeError("relay release metadata missing version")
        current_hash = _current_extension_hash(args)
        latest_hash = _release_content_hash(release)
        newer_version = _is_newer(latest_version, current_version)
        same_version = not newer_version and not _is_newer(current_version, latest_version)
        hash_drift = bool(same_version and latest_hash and current_hash and latest_hash.lower() != current_hash.lower())
        update_available = newer_version or hash_drift
        update_reason = "newer_version" if newer_version else ("same_version_hash_changed" if hash_drift else "up_to_date")
        report.update({
            "ok": True,
            "relay_http_url": relay_http_url,
            "owner_path": owner_path,
            "current_version": current_version,
            "latest_version": latest_version,
            "current_hash": current_hash,
            "latest_hash": latest_hash,
            "hash_drift": hash_drift,
            "update_available": update_available,
            "update_reason": update_reason,
            "release": release,
        })
        if not update_available:
            report.update({
                "action": "up_to_date",
                "message": f"Intern Agent Helper is already up to date ({current_version}).",
            })
            return 0, report
        if getattr(args, "check_only", False) or getattr(args, "dry_run", False):
            message = (
                f"Update available: {current_version} hash {current_hash[:12] or '-'} -> {latest_hash[:12] or '-'}."
                if hash_drift else
                f"Update available: {current_version} -> {latest_version}."
            )
            report.update({
                "action": "update_available",
                "message": message,
            })
            return 0, report
        dest_dir = _download_dir(args)
        filename = str(release.get("filename") or f"intern-agent-helper-{latest_version}.vsix")
        dest = dest_dir / filename
        try:
            _download_vsix(_resolve_download_url(relay_http_url, release), token, dest, urlopen=urlopen)
        except Exception as exc:
            report.update({
                "ok": False,
                "action": "download_failed",
                "downloaded_path": os.fspath(dest),
                "message": str(exc),
            })
            return 1, report
        actual_sha = _sha256(dest)
        expected_sha = str(release.get("sha256") or "").strip()
        if expected_sha and actual_sha.lower() != expected_sha.lower():
            report.update({
                "ok": False,
                "action": "checksum_failed",
                "downloaded_path": os.fspath(dest),
                "expected_sha256": expected_sha,
                "actual_sha256": actual_sha,
                "message": "Downloaded VSIX checksum did not match release metadata.",
            })
            return 1, report
        try:
            command = _install_command(dest, environ=environ)
        except Exception as exc:
            report.update({
                "ok": False,
                "action": "install_unavailable",
                "downloaded_path": os.fspath(dest),
                "sha256": actual_sha,
                "message": str(exc),
            })
            return 1, report
        install = _run_install(command, runner=runner)
        report.update({
            "downloaded_path": os.fspath(dest),
            "sha256": actual_sha,
            "install": install,
        })
        if install["returncode"] != 0:
            report.update({
                "ok": False,
                "action": "install_failed",
                "message": f"VSIX install command exited with {install['returncode']}.",
            })
            return 1, report
        installed_ext_dir = _find_installed_extension_dir(latest_version)
        if not installed_ext_dir:
            report.update({
                "ok": False,
                "action": "install_incomplete",
                "installed_extension": {
                    "verified": False,
                    "version": latest_version,
                    "reason": "installed extension directory not found after VSIX install command succeeded",
                },
                "message": (
                    f"VSIX install command succeeded, but Intern Agent Helper {latest_version} "
                    "is not visible in the installed extension directories yet. Reload was not offered."
                ),
            })
            return 1, report
        installed_extension = _inspect_installed_extension(installed_ext_dir, latest_version, latest_hash)
        report["installed_extension"] = installed_extension
        if not installed_extension["version_matches"]:
            report.update({
                "ok": False,
                "action": "installed_version_mismatch",
                "message": (
                    "VSIX install command succeeded, but the installed extension package version "
                    f"is {installed_extension.get('version') or 'unreadable'} instead of {latest_version}. "
                    "Reload was not offered."
                ),
            })
            return 1, report
        if installed_extension["content_matches"] is False:
            report.update({
                "ok": False,
                "action": "installed_content_mismatch",
                "message": (
                    "VSIX install command succeeded, but the installed extension content does not match "
                    "the relay release metadata. Reload was not offered."
                ),
            })
            return 1, report
        hooks = _sync_hooks_from_vsix(dest, _work_root(args), latest_version)
        wrappers = _write_cli_wrappers(installed_ext_dir)
        daemon_snapshot = _daemon_runtime_snapshot(_work_root(args))
        daemon_effect = _schedule_daemon_restart(args, installed_ext_dir, daemon_snapshot)
        runtime_effects = {
            "extension_js": {
                "requires_reload": True,
                "note": "VS Code Reload Window is needed only for extension UI/TreeView JavaScript.",
            },
            "hooks": hooks,
            "cli_wrappers": wrappers,
            "daemon": daemon_effect,
            "relay": {
                "upgraded_by_client": False,
                "restart_required_by_client": False,
                "policy": "manual_admin",
                "note": "relay sync/restart remains administrator-managed",
            },
        }
        report.update({
            "ok": True,
            "action": "installed",
            "hooks": hooks,
            "runtime_effects": runtime_effects,
            "message": f"Installed Intern Agent Helper {latest_version}. Installed extension, hooks, and CLI runtime were verified.",
            "next_actions": [
                "Run VS Code: Reload Window only if you need the TreeView/extension UI JavaScript to switch immediately.",
                (
                    "Local daemon restart has been scheduled so daemon code picks up the upgraded bundled runtime."
                    if daemon_effect.get("restart_scheduled")
                    else "Local daemon was not restarted; run `internctl daemon restart` if daemon behavior must pick up the upgraded bundled runtime."
                ),
                "Relay server upgrades remain administrator-managed; do not restart relay from this command.",
            ],
        })
        return 0, report
    except Exception as exc:
        report.update({
            "ok": False,
            "action": "failed",
            "message": str(exc),
        })
        return 1, report


def _print_human(report: dict) -> None:
    if report.get("ok"):
        print(report.get("message") or "Upgrade check completed.")
        for action in report.get("next_actions") or []:
            print(f"- {action}")
        return
    print(report.get("message") or "Upgrade failed.", file=sys.stderr)


def run(args: argparse.Namespace) -> int:
    rc, report = perform_upgrade(args)
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return rc
