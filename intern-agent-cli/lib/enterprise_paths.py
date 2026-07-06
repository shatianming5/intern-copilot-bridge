"""Enterprise policy/config filesystem layout."""

from __future__ import annotations

import os
from pathlib import Path


def work_root_path(work_root: str | os.PathLike[str] | None = None) -> Path:
    return Path(work_root or os.environ.get("WORK_AGENTS_ROOT") or "/work-agents")


def enterprise_policy_root(work_root: str | os.PathLike[str] | None = None) -> Path:
    return work_root_path(work_root) / "enterprise_policy"


def relay_policy_dir(work_root: str | os.PathLike[str] | None = None) -> Path:
    return enterprise_policy_root(work_root) / "relay"


def daemon_policy_dir(work_root: str | os.PathLike[str] | None = None) -> Path:
    return enterprise_policy_root(work_root) / "daemon"


def relay_policy_path(work_root: str | os.PathLike[str] | None = None) -> Path:
    return relay_policy_dir(work_root) / "policy.json"


def relay_secrets_path(work_root: str | os.PathLike[str] | None = None) -> Path:
    return relay_policy_dir(work_root) / "secrets.json"


def relay_owner_path(work_root: str | os.PathLike[str] | None = None) -> Path:
    return relay_policy_dir(work_root) / "_owner.json"


def daemon_owner_path(work_root: str | os.PathLike[str] | None = None) -> Path:
    return daemon_policy_dir(work_root) / "_owner.json"


def daemon_policy_path(work_root: str | os.PathLike[str] | None = None) -> Path:
    return daemon_policy_dir(work_root) / "policy.json"


def daemon_user_env_path(work_root: str | os.PathLike[str] | None = None) -> Path:
    return daemon_policy_dir(work_root) / "user.env"


def daemon_chat_config_path(work_root: str | os.PathLike[str] | None = None) -> Path:
    return daemon_policy_dir(work_root) / "chat_config.json"


def daemon_runtime_dir(work_root: str | os.PathLike[str] | None = None) -> Path:
    return daemon_policy_dir(work_root) / "runtime"


def machine_config_state_path(work_root: str | os.PathLike[str] | None = None) -> Path:
    return relay_policy_dir(work_root) / "machine_config_state.json"


def user_config_backups_dir(work_root: str | os.PathLike[str] | None = None) -> Path:
    return relay_policy_dir(work_root) / "user_config_backups"
