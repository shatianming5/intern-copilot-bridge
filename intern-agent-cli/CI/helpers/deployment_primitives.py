from __future__ import annotations

from CI.helpers.deployment_config import (
    CLAUDE_OPUS_47_ARGS,
    CODEX_BASE_ARGS,
    CODEX_LB_POLICY_MARKER_ENV,
    CODEX_LB_POLICY_MARKER_FIELD,
    DEFAULT_CODEX_LB_API_KEY,
    DEFAULT_CODEX_LB_BASE_URL,
    DEFAULT_CODEX_LB_ENV_KEY,
    DEFAULT_CODEX_LB_SECRET_ENV,
    DEFAULT_CLAUDE_BASE_URL,
    DEFAULT_ENTERPRISE_CI_FEISHU_APP_ID,
    DEFAULT_ENTERPRISE_CI_FEISHU_APP_SECRET,
    DEFAULT_ENTERPRISE_CI_OWNER_MOBILE,
    DEFAULT_GITHUB_NONPROTECTED_REPO,
    DEFAULT_GITHUB_PROTECTED_REPO,
    DEFAULT_NONPROTECTED_REPO,
    DEFAULT_PROTECTED_REPO,
    ci_deployment_id,
    claude_opus_47_session_env,
    codex_lb_env_switches,
    codex_lb_session_env,
    repo_root,
    resolve_feishu_app_id,
    resolve_feishu_app_secret,
)
from CI.helpers.deployment_payloads import (
    DEFAULT_CODEUP_SSH_KEY_CANDIDATES,
    REMOTE_PYTHON_WHEEL_PACKAGES,
    SSH_AUTH_CONFIG_HOSTS,
    _ci_cache_root,
    _configured_identity_files,
    _copy_cached_python_wheels,
    _expand_ssh_identity_path,
    _python_wheels_cache_paths,
    _ssh_host_patterns_match,
    _write_json,
    _write_python_wheels_manifest,
    make_codex_auth_tarball,
    make_enterprise_config_tarball,
    make_extension_tarball,
    make_python_wheels_tarball,
    make_ssh_auth_tarball,
    resolve_codeup_ssh_key,
)
from CI.helpers.deployment_provider_policy import (
    enable_remote_codex_lb_config,
    verify_remote_claude_policy,
    verify_remote_codex_lb,
)
from CI.helpers.deployment_remote import deploy_machine, enterprise_owner_mode_script, reset_remote_ci_state
from CI.helpers.deployment_services import (
    bootstrap_remote_services,
    wait_remote_daemon_connected,
    wait_remote_relay_connections,
)
from CI.helpers.remote_machine_helper import (
    _ltp_proxy_command,
    machine_relay_host,
    remote_cli,
    scp_to_machine,
    ssh_base,
    wait_http_json,
    wait_machine_ssh_ready,
)
