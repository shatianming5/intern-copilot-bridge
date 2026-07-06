from __future__ import annotations

import os
from pathlib import Path
from typing import Any


DEFAULT_PROTECTED_REPO = os.environ.get("CI_PROTECTED_REPO", "")
DEFAULT_NONPROTECTED_REPO = os.environ.get("CI_NONPROTECTED_REPO", "")
DEFAULT_GITHUB_PROTECTED_REPO = os.environ.get("CI_GITHUB_PROTECTED_REPO", "")
DEFAULT_GITHUB_NONPROTECTED_REPO = os.environ.get("CI_GITHUB_NONPROTECTED_REPO", "")
DEFAULT_ENTERPRISE_CI_FEISHU_APP_ID = os.environ.get("CI_FEISHU_APP_ID", "")
DEFAULT_ENTERPRISE_CI_FEISHU_APP_SECRET = os.environ.get("CI_FEISHU_APP_SECRET", "")
DEFAULT_ENTERPRISE_CI_OWNER_MOBILE = os.environ.get("CI_OWNER_MOBILE", "")
DEFAULT_CODEX_LB_BASE_URL = os.environ.get("CODEX_LB_BASE_URL", "")
DEFAULT_CODEX_LB_API_KEY = os.environ.get("CODEX_LB_API_KEY", "")
DEFAULT_CODEX_LB_ENV_KEY = os.environ.get("CODEX_LB_ENV_KEY", "LB_API_KEY")
DEFAULT_CODEX_LB_SECRET_ENV = "CODEX_LB_API_KEY"
DEFAULT_CLAUDE_BASE_URL = os.environ.get("CLAUDE_BASE_URL", "")
CODEX_BASE_ARGS = ["--enable", "hooks", "--dangerously-bypass-approvals-and-sandbox"]
CLAUDE_OPUS_47_ARGS = ["--permission-mode", "bypassPermissions", "--model", "claude-opus-4-7"]
CODEX_LB_POLICY_MARKER_ENV = "CI_CODEX_POLICY_MARKER"
CODEX_LB_POLICY_MARKER_FIELD = "codex_lb_mode"


def codex_lb_session_env(
    *,
    base_url: str = DEFAULT_CODEX_LB_BASE_URL,
    api_key: str = DEFAULT_CODEX_LB_API_KEY,
    env_key: str = DEFAULT_CODEX_LB_ENV_KEY,
    secret_env: str = DEFAULT_CODEX_LB_SECRET_ENV,
) -> dict[str, Any]:
    env = {
        "CODEX_POLICY_LB_BASE_URL": base_url,
        "CODEX_LB_ENV_KEY": env_key,
    }
    if api_key:
        env[env_key] = api_key
    env[CODEX_LB_POLICY_MARKER_ENV] = "{{" + CODEX_LB_POLICY_MARKER_FIELD + "}}"
    session_env = {
        "env": {
            **env,
        },
        "args": [
            *CODEX_BASE_ARGS,
            "-c", 'model_provider="lb"',
            "-c", 'model_providers.lb.name="codex-lb"',
            "-c", f'model_providers.lb.base_url="{base_url}"',
            "-c", 'model_providers.lb.wire_api="responses"',
            "-c", f'model_providers.lb.env_key="{env_key}"',
        ],
    }
    if not api_key and secret_env:
        session_env["secret_env"] = {
            env_key: secret_env,
        }
    return session_env

def codex_lb_env_switches(
    *,
    base_url: str = DEFAULT_CODEX_LB_BASE_URL,
    api_key: str = DEFAULT_CODEX_LB_API_KEY,
    env_key: str = DEFAULT_CODEX_LB_ENV_KEY,
    secret_env: str = DEFAULT_CODEX_LB_SECRET_ENV,
    default_enabled: bool = True,
) -> dict[str, Any]:
    return {
        "schema": "intern-agents.env-switches.v1",
        "groups": [
            {
                "key": "codex_lb",
                "title": "Codex LB Provider",
                "description": "Use the enterprise managed LB route for Codex requests on this machine.",
                "default_enabled": default_enabled,
                "enable_codex": True,
                "enable_claude": False,
                "fields": [
                    {
                        "key": CODEX_LB_POLICY_MARKER_FIELD,
                        "label": "Codex LB CI policy marker",
                        "description": "CI-only marker used to verify daemon policy sync and session env restarts without changing the LB route.",
                        "type": "select",
                        "default": "baseline",
                        "options": [
                            {"label": "Baseline", "value": "baseline"},
                            {"label": "Alternate", "value": "alternate"},
                        ],
                    },
                ],
                "policy_patch": {
                    "codex": {
                        "session_env": codex_lb_session_env(
                            base_url=base_url,
                            api_key=api_key,
                            env_key=env_key,
                            secret_env=secret_env,
                        ),
                    },
                },
            },
        ],
    }

def claude_opus_47_session_env(*, base_url: str = DEFAULT_CLAUDE_BASE_URL) -> dict[str, Any]:
    session_env: dict[str, Any] = {
        "args": list(CLAUDE_OPUS_47_ARGS),
    }
    if base_url:
        session_env["env"] = {"ANTHROPIC_BASE_URL": base_url}
    return session_env

def ci_deployment_id(prefix: str) -> str:
    raw = str(prefix or "ci").strip() or "ci"
    if raw.startswith(("ci_", "bug")):
        return raw
    return "ci_" + raw

def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]

def resolve_feishu_app_id(raw: str = "") -> str:
    return raw or os.environ.get("ENTERPRISE_CI_FEISHU_APP_ID") or DEFAULT_ENTERPRISE_CI_FEISHU_APP_ID

def resolve_feishu_app_secret(raw: str = "") -> str:
    return raw or os.environ.get("ENTERPRISE_CI_FEISHU_APP_SECRET") or DEFAULT_ENTERPRISE_CI_FEISHU_APP_SECRET
