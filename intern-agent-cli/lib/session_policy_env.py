"""Enterprise policy materialization for Claude/Codex session env.

Relay serves a daemon-safe policy.  This module combines that policy with the
local owner identity and local ``enterprise_policy/daemon/user.env`` secrets, then writes
provider-specific runtime env scripts consumed by session start/resume paths.
Reports intentionally expose only keys, target ids, and hashes.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import stat
from typing import Any

from lib.enterprise_paths import daemon_owner_path, daemon_runtime_dir, daemon_user_env_path

REPORT_SCHEMA = "intern-agents.session-runtime-env.v1"
PROVIDERS = ("codex", "claude")
HASH_ENV = {
    "codex": "CODEX_POLICY_ENV_HASH",
    "claude": "CLAUDE_POLICY_ENV_HASH",
}
ARGS_ENV = {
    "codex": "INTERN_CODEX_POLICY_ARGS",
    "claude": "INTERN_CLAUDE_POLICY_ARGS",
}
ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")


class SessionPolicyEnvError(ValueError):
    """Raised when session env policy is structurally invalid."""


@dataclass(frozen=True)
class TargetContext:
    owner_mobile: str = ""
    owner_open_id: str = ""
    machine_id: str = ""
    machine_tags: tuple[str, ...] = ()


def runtime_dir(work_root: str | os.PathLike[str]) -> Path:
    return daemon_runtime_dir(work_root)


def provider_env_path(work_root: str | os.PathLike[str], provider: str) -> Path:
    _validate_provider(provider)
    return runtime_dir(work_root) / f"{provider}.env"


def runtime_report_path(work_root: str | os.PathLike[str]) -> Path:
    return runtime_dir(work_root) / "session_env_report.json"


def has_session_env_policy(policy: dict[str, Any]) -> bool:
    if not isinstance(policy, dict):
        return False
    if isinstance(policy.get("session_env"), dict):
        return True
    for provider in PROVIDERS:
        provider_policy = policy.get(provider)
        if isinstance(provider_policy, dict) and isinstance(provider_policy.get("session_env"), dict):
            return True
    return False


def owner_context(owner: dict[str, Any] | None) -> TargetContext:
    owner = owner or {}
    tags = owner.get("machine_tags") or owner.get("tags") or []
    if isinstance(tags, str):
        tags = [item.strip() for item in tags.split(",")]
    if not isinstance(tags, list):
        tags = []
    return TargetContext(
        owner_mobile=str(owner.get("owner_mobile") or owner.get("mobile") or "").strip(),
        owner_open_id=str(owner.get("owner_open_id") or owner.get("open_id") or "").strip(),
        machine_id=str(owner.get("machine_id") or "").strip(),
        machine_tags=tuple(sorted(str(tag).strip() for tag in tags if str(tag).strip())),
    )


def load_owner_config(work_root: str | os.PathLike[str]) -> dict[str, Any]:
    path = daemon_owner_path(work_root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def load_user_env(work_root: str | os.PathLike[str]) -> dict[str, str]:
    path = daemon_user_env_path(work_root)
    if not path.is_file():
        return {}
    result: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _is_env_name(key):
            continue
        result[key] = _parse_shell_value(value.strip())
    return result


def materialize_session_env(
    *,
    work_root: str | os.PathLike[str],
    policy: dict[str, Any],
    owner: dict[str, Any] | None = None,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Write provider runtime env files and return a redacted diagnostic report."""
    if not isinstance(policy, dict):
        raise SessionPolicyEnvError("policy must be an object")
    root = Path(work_root)
    ctx = owner_context(owner if owner is not None else load_owner_config(root))
    secret_source = dict(environ if environ is not None else os.environ)
    secret_source.update(load_user_env(root))
    session_policy = _session_policy(policy)
    out_dir = runtime_dir(root)
    previous = _read_report(runtime_report_path(root))
    providers: dict[str, dict[str, Any]] = {}

    for provider in PROVIDERS:
        provider_result = _materialize_provider(
            provider=provider,
            session_policy=session_policy,
            policy=policy,
            ctx=ctx,
            secret_source=secret_source,
            previous_provider=(previous.get("providers") or {}).get(provider)
            if isinstance(previous.get("providers"), dict) else None,
        )
        _write_env_file(provider_env_path(root, provider), provider_result)
        providers[provider] = _provider_report(provider_result, provider_env_path(root, provider))

    report = {
        "schema": REPORT_SCHEMA,
        "ok": True,
        "target": {
            "owner_mobile": _redact_identity(ctx.owner_mobile),
            "owner_open_id": _redact_identity(ctx.owner_open_id),
            "machine_id": ctx.machine_id,
            "machine_tags": list(ctx.machine_tags),
        },
        "providers": providers,
    }
    _write_json_atomic(runtime_report_path(root), report, mode=0o600)
    return report


def _materialize_provider(
    *,
    provider: str,
    session_policy: dict[str, Any],
    policy: dict[str, Any],
    ctx: TargetContext,
    secret_source: dict[str, str],
    previous_provider: Any,
) -> dict[str, Any]:
    base = _provider_config(session_policy.get(provider))
    root_provider = policy.get(provider)
    if isinstance(root_provider, dict):
        base = _merge_provider_config(base, _provider_config(root_provider.get("session_env")))

    managed_keys = _managed_keys(base)
    matched: list[str] = []
    enabled = bool(base.get("enabled", True))
    env: dict[str, str] = {}
    missing_secret_refs: dict[str, str] = {}
    _apply_provider_config(base, env, missing_secret_refs, managed_keys, secret_source)
    args = _args_items(base.get("args"))

    overrides = session_policy.get("overrides") if isinstance(session_policy.get("overrides"), list) else []
    for index, raw_override in enumerate(overrides):
        if not isinstance(raw_override, dict):
            continue
        target = raw_override.get("target", raw_override.get("targets", {}))
        if not target_matches(target if isinstance(target, dict) else {}, ctx):
            continue
        providers = raw_override.get("providers")
        override_cfg = None
        if isinstance(providers, dict):
            override_cfg = providers.get(provider)
        if override_cfg is None:
            override_cfg = raw_override.get(provider)
        override_cfg = _provider_config(override_cfg)
        if not override_cfg:
            continue
        matched.append(str(raw_override.get("id") or f"override[{index}]"))
        managed_keys.update(_managed_keys(override_cfg))
        if "enabled" in override_cfg:
            enabled = bool(override_cfg.get("enabled"))
        if "args" in override_cfg:
            args = _args_items(override_cfg.get("args"))
        _apply_provider_config(override_cfg, env, missing_secret_refs, managed_keys, secret_source)

    if not enabled:
        env = {}
        missing_secret_refs = {}
        args = []
    elif args:
        args_key = ARGS_ENV[provider]
        env[args_key] = " ".join(args)
        managed_keys.add(args_key)

    previous_keys = _previous_contract_keys(previous_provider)
    unset_env_keys = sorted(key for key in previous_keys if key not in managed_keys)
    digest = _env_hash(provider, enabled, env, matched)
    previous_hash = previous_provider.get("hash") if isinstance(previous_provider, dict) else ""
    needs_restart = bool(unset_env_keys) or (bool(previous_hash) and previous_hash != digest) or (
        not previous_hash and (bool(env) or bool(managed_keys))
    )
    return {
        "provider": provider,
        "enabled": enabled,
        "env": env,
        "hash": digest,
        "hash_env": HASH_ENV[provider],
        "args": args,
        "matched_targets": matched,
        "managed_env_keys": sorted(managed_keys),
        "unset_env_keys": unset_env_keys,
        "missing_secret_refs": missing_secret_refs,
        "previous_hash": previous_hash or "",
        "changed": needs_restart,
    }


def target_matches(target: dict[str, Any] | None, ctx: TargetContext) -> bool:
    target = target or {}
    if target.get("all") is True:
        return True
    checks = [
        _target_values(target, "owner_mobile", "owner_mobiles", "mobile", "mobiles"),
        _target_values(target, "owner_open_id", "owner_open_ids", "open_id", "open_ids"),
        _target_values(target, "machine_id", "machine_ids"),
    ]
    actual = [ctx.owner_mobile, ctx.owner_open_id, ctx.machine_id]
    for values, actual_value in zip(checks, actual):
        if values and actual_value not in values:
            return False
    tag_values = _target_values(target, "machine_tag", "machine_tags", "tag", "tags")
    if tag_values and not (set(tag_values) & set(ctx.machine_tags)):
        return False
    if not any(checks) and not tag_values:
        return True
    return True


def _session_policy(policy: dict[str, Any]) -> dict[str, Any]:
    raw = policy.get("session_env")
    if isinstance(raw, dict):
        result = dict(raw)
    else:
        result = {}
    defaults = result.get("defaults")
    if isinstance(defaults, dict):
        default_provider_keys = {key for key in defaults if key in PROVIDERS}
        generic_defaults = {key: value for key, value in defaults.items() if key not in PROVIDERS}
        for provider in PROVIDERS:
            provider_default = defaults.get(provider) if provider in default_provider_keys else {}
            merged_default = _merge_provider_config(
                _provider_config(generic_defaults),
                _provider_config(provider_default),
            )
            result[provider] = _merge_provider_config(merged_default, _provider_config(result.get(provider)))
    return result


def _provider_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _merge_provider_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    if not override:
        return base
    merged = dict(base)
    for key in ("env", "secret_env", "env_from", "unset"):
        left = merged.get(key)
        right = override.get(key)
        if isinstance(left, dict) and isinstance(right, dict):
            merged[key] = {**left, **right}
        elif isinstance(left, list) and isinstance(right, list):
            merged[key] = [*left, *right]
        elif key in override:
            merged[key] = right
    for key, value in override.items():
        if key not in {"env", "secret_env", "env_from", "unset"}:
            merged[key] = value
    return merged


def _apply_provider_config(
    config: dict[str, Any],
    env: dict[str, str],
    missing_secret_refs: dict[str, str],
    managed_keys: set[str],
    secret_source: dict[str, str],
) -> None:
    for key, value in _env_items(config.get("env")):
        env[key] = value
        managed_keys.add(key)
        missing_secret_refs.pop(key, None)
    for key in _unset_items(config.get("unset")):
        env.pop(key, None)
        missing_secret_refs.pop(key, None)
        managed_keys.add(key)
    secret_env = config.get("secret_env")
    if not isinstance(secret_env, dict):
        secret_env = config.get("env_from") if isinstance(config.get("env_from"), dict) else {}
    for key, source_name in secret_env.items():
        key = str(key).strip()
        source_name = str(source_name).strip()
        _validate_env_name(key)
        _validate_env_name(source_name)
        managed_keys.add(key)
        value = secret_source.get(source_name, "")
        if value:
            env[key] = value
            missing_secret_refs.pop(key, None)
        else:
            env.pop(key, None)
            missing_secret_refs[key] = source_name


def _env_items(raw: Any) -> list[tuple[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise SessionPolicyEnvError("provider env must be an object")
    items = []
    for key, value in raw.items():
        key = str(key).strip()
        _validate_env_name(key)
        if isinstance(value, (dict, list)):
            raise SessionPolicyEnvError(f"env value for {key} must be scalar")
        items.append((key, str(value)))
    return items


def _unset_items(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise SessionPolicyEnvError("provider unset must be a list")
    result = []
    for key in raw:
        key = str(key).strip()
        _validate_env_name(key)
        result.append(key)
    return result


def _args_items(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = shlex.split(raw)
        except ValueError as exc:
            raise SessionPolicyEnvError(f"provider args are not valid shell words: {exc}") from exc
    if not isinstance(raw, list):
        raise SessionPolicyEnvError("provider args must be a list or shell string")
    result = []
    for item in raw:
        if isinstance(item, (dict, list)):
            raise SessionPolicyEnvError("provider args must contain scalar strings")
        text = str(item).strip()
        if not text:
            continue
        if any(ch.isspace() for ch in text):
            raise SessionPolicyEnvError(f"provider arg must not contain whitespace: {text!r}")
        result.append(text)
    return result


def _managed_keys(config: dict[str, Any]) -> set[str]:
    keys = {key for key, _value in _env_items(config.get("env"))}
    keys.update(_unset_items(config.get("unset")))
    secret_env = config.get("secret_env")
    if not isinstance(secret_env, dict):
        secret_env = config.get("env_from") if isinstance(config.get("env_from"), dict) else {}
    for key in secret_env:
        key = str(key).strip()
        _validate_env_name(key)
        keys.add(key)
    return keys


def _previous_contract_keys(previous_provider: Any) -> set[str]:
    if not isinstance(previous_provider, dict):
        return set()
    result: set[str] = set()
    for field in ("managed_env_keys", "unset_env_keys"):
        raw = previous_provider.get(field)
        if not isinstance(raw, list):
            continue
        for key in raw:
            key = str(key).strip()
            if _is_env_name(key):
                result.add(key)
    return result


def _provider_report(provider_result: dict[str, Any], env_path: Path) -> dict[str, Any]:
    env_keys = sorted(provider_result["env"].keys())
    return {
        "enabled": provider_result["enabled"],
        "file": os.fspath(env_path),
        "hash": provider_result["hash"],
        "hash_env": provider_result["hash_env"],
        "env_keys": env_keys,
        "managed_env_keys": provider_result["managed_env_keys"],
        "unset_env_keys": provider_result["unset_env_keys"],
        "matched_targets": provider_result["matched_targets"],
        "missing_secret_refs": provider_result["missing_secret_refs"],
        "previous_hash": provider_result["previous_hash"],
        "args": list(provider_result.get("args") or []),
        "changed": provider_result["changed"],
        "needs_restart": provider_result["changed"],
    }


def _write_env_file(path: Path, provider_result: dict[str, Any]) -> None:
    lines = [
        "# Generated by internctl setup refresh-policy. Do not edit.",
        f"# provider: {provider_result['provider']}",
        "# managed_env_keys: " + " ".join(provider_result["managed_env_keys"]),
        "# unset_env_keys: " + " ".join(provider_result["unset_env_keys"]),
    ]
    for key in [*provider_result["managed_env_keys"], *provider_result["unset_env_keys"]]:
        lines.append(f"unset {key}")
    lines.append(f"export {provider_result['hash_env']}={shlex.quote(provider_result['hash'])}")
    for key in sorted(provider_result["env"]):
        lines.append(f"export {key}={shlex.quote(provider_result['env'][key])}")
    _write_text_atomic(path, "\n".join(lines) + "\n", mode=0o600)


def _read_report(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_atomic(path: Path, data: dict[str, Any], *, mode: int) -> None:
    _write_text_atomic(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n", mode=mode)


def _write_text_atomic(path: Path, text: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.chmod(mode)
    tmp.replace(path)
    path.chmod(mode)


def _env_hash(provider: str, enabled: bool, env: dict[str, str], matched: list[str]) -> str:
    payload = {
        "provider": provider,
        "enabled": enabled,
        "env": {key: env[key] for key in sorted(env)},
        "matched_targets": matched,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _target_values(target: dict[str, Any], *keys: str) -> set[str]:
    result: set[str] = set()
    for key in keys:
        value = target.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            values = [value]
        elif isinstance(value, list):
            values = value
        else:
            continue
        result.update(str(item).strip() for item in values if str(item).strip())
    return result


def _parse_shell_value(value: str) -> str:
    if not value:
        return ""
    try:
        parsed = shlex.split(value, comments=False, posix=True)
        if len(parsed) == 1:
            return parsed[0]
    except ValueError:
        pass
    return value.strip().strip('"').strip("'")


def _redact_identity(identity: str) -> str:
    identity = str(identity or "")
    if not identity:
        return ""
    if len(identity) <= 4:
        return "***"
    return identity[:2] + "***" + identity[-2:]


def _validate_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise SessionPolicyEnvError(f"unsupported provider: {provider!r}")


def _validate_env_name(name: str) -> None:
    if not _is_env_name(name):
        raise SessionPolicyEnvError(f"invalid env name: {name!r}")


def _is_env_name(name: str) -> bool:
    return bool(ENV_NAME_RE.match(name))


def chmod_private(path: Path) -> None:
    try:
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
