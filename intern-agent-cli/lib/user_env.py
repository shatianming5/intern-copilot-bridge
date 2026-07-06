"""Shared enterprise user environment loading."""

from __future__ import annotations

import os
import re
from pathlib import Path

from lib.enterprise_paths import daemon_user_env_path


_ENV_KEY_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def enterprise_user_env_paths(root: str | os.PathLike[str]) -> list[Path]:
    """Return env candidates in override order."""

    return [
        Path("~/.codeup_env").expanduser(),
        Path("~/.config/intern-agent-helper/enterprise/user.env").expanduser(),
        Path("~/.intern-agent-helper/enterprise/user.env").expanduser(),
        daemon_user_env_path(root),
    ]


def parse_env_file(path: str | os.PathLike[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    with Path(path).open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export "):].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if not _ENV_KEY_RE.fullmatch(key):
                continue
            values[key] = value.strip().strip("'\"")
    return values


def quote_env_value(value: str) -> str:
    return "'" + str(value).replace("'", "'\"'\"'") + "'"


def write_enterprise_user_env_values(
    root: str | os.PathLike[str],
    values: dict[str, str],
    *,
    target_path: str | os.PathLike[str] | None = None,
) -> Path:
    """Merge user-entered enterprise env values into the writable user env file."""

    path = Path(target_path).expanduser() if target_path else daemon_user_env_path(root)
    merged = parse_env_file(path) if path.is_file() else {}
    for key, value in values.items():
        if not _ENV_KEY_RE.fullmatch(str(key)):
            raise ValueError(f"invalid env key: {key!r}")
        merged[str(key)] = str(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    lines = ["# Managed by Intern Agent Helper enterprise setup.\n"]
    for key in sorted(merged):
        lines.append(f"export {key}={quote_env_value(merged[key])}\n")
    tmp.write_text("".join(lines), encoding="utf-8")
    tmp.chmod(0o600)
    tmp.replace(path)
    path.chmod(0o600)
    ensure_shell_sources_enterprise_user_env(path)
    return path


def ensure_shell_sources_enterprise_user_env(path: str | os.PathLike[str]) -> Path:
    """Ensure ~/.bashrc sources the managed enterprise env before interactive guards."""

    env_path = Path(path).expanduser()
    bashrc = Path("~/.bashrc").expanduser()
    block = (
        "# Intern Agent Helper enterprise env\n"
        f'[ -f "{env_path}" ] && set -a && . "{env_path}" && set +a\n'
    )
    text = bashrc.read_text(encoding="utf-8") if bashrc.is_file() else ""
    if block in text:
        return bashrc
    lines = text.splitlines(keepends=True)
    insert_at = 0
    for idx, line in enumerate(lines):
        if "[ -z \"$PS1\" ] && return" in line or "[ -z '$PS1' ] && return" in line:
            insert_at = idx
            break
    else:
        insert_at = len(lines)
    lines[insert_at:insert_at] = [block, "\n"]
    bashrc.write_text("".join(lines), encoding="utf-8")
    return bashrc


def load_enterprise_user_env(root: str | os.PathLike[str], env: dict[str, str] | None = None) -> dict[str, str]:
    """Load all enterprise user env files into env and return loaded values."""

    target = env if env is not None else os.environ
    loaded: dict[str, str] = {}
    for path in enterprise_user_env_paths(root):
        if not path.is_file():
            continue
        for key, value in parse_env_file(path).items():
            target[key] = value
            loaded[key] = value
    return loaded
