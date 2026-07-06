from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Any

from CI.assertions import policy as policy_assertions
from CI.helpers.native_error import NativeCaseError
from CI.helpers.reporting import redact_report_value


@dataclass
class PolicyActions:
    ctx: Any

    def _remote(self) -> Any:
        remote = getattr(self.ctx, "remote_context", None)
        if remote is None:
            raise RuntimeError("ctx.action.policy.* requires RemoteCaseContext")
        return remote

    def _policy_state_path(self) -> Path:
        return self._remote().work_root / "enterprise_policy" / "relay" / "machine_config_state.json"

    def _relay_policy_path(self) -> Path:
        return self._remote().work_root / "enterprise_policy" / "relay" / "policy.json"

    def _daemon_policy_path(self) -> Path:
        return self._remote().work_root / "enterprise_policy" / "daemon" / "policy.json"

    def _session_env_report_path(self) -> Path:
        return self._remote().work_root / "enterprise_policy" / "daemon" / "runtime" / "session_env_report.json"

    def _load_json_object(self, path: Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            return self._remote().file_artifacts.load_json_object(path, default=default)
        except ValueError as exc:
            raise NativeCaseError(str(exc)) from exc

    def _write_json_atomic(self, path: Path, data: dict[str, Any]) -> None:
        self._remote().file_artifacts.write_json_atomic(path, data)

    def _require_check(self, name: str, ok: bool, detail: dict[str, Any]) -> None:
        remote = self._remote()
        require = getattr(remote, "require", None)
        if callable(require):
            require(name, ok, detail)
            return
        remote.checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            raise NativeCaseError(f"assertion failed: {name}")

    def daemon_status_remote(self, label: str = "daemon status") -> dict[str, Any]:
        remote = self._remote()
        return remote.json_cmd(label, [*remote.internctl, "daemon", "status", "--json"], timeout=90)

    def current_daemon_machine_id_remote(self, status: dict[str, Any] | None = None) -> str:
        remote = self._remote()
        status = status or self.daemon_status_remote("daemon status for machine id")
        nested = status.get("status") if isinstance(status.get("status"), dict) else {}
        machine_value = getattr(remote, "machine_id", "")
        if callable(machine_value):
            machine_value = machine_value()
        machine_id = str(nested.get("instance_id") or os.environ.get("INTERN_CI_MACHINE_ID") or machine_value or "")
        self._require_check("current_daemon_machine_id_present", bool(machine_id), {"daemon_status": status})
        return machine_id

    def _machine_config_field_spec(self, policy: dict[str, Any], field_key: str) -> dict[str, Any]:
        env_switches = policy.get("env_switches") if isinstance(policy.get("env_switches"), dict) else {}
        for group in env_switches.get("groups") or []:
            if not isinstance(group, dict):
                continue
            group_key = str(group.get("key") or "")
            for field in group.get("fields") or []:
                if isinstance(field, dict) and str(field.get("key") or "") == field_key:
                    return {"schema": "env_switches", "group_key": group_key, "field": field}
        machine_config = policy.get("machine_config") if isinstance(policy.get("machine_config"), dict) else {}
        for group in machine_config.get("groups") or []:
            if not isinstance(group, dict):
                continue
            for field in group.get("fields") or []:
                if isinstance(field, dict) and str(field.get("key") or "") == field_key:
                    return {"schema": "machine_config", "group_key": "", "field": field}
        raise NativeCaseError(
            "ci_capability_gap_policy_mutation_driver: relay policy does not define env_switch/machine_config field " + field_key,
            details={"policy_path": str(self._relay_policy_path()), "field": field_key},
        )

    def machine_config_marker_remote(self, *, field_key: str, marker: str) -> dict[str, Any]:
        remote = self._remote()
        policy_path = self._relay_policy_path()
        state_path = self._policy_state_path()
        if not policy_path.is_file():
            raise NativeCaseError(
                "ci_capability_gap_policy_mutation_driver: relay policy file is not available on this debug machine",
                details={"policy_path": str(policy_path), "state_path": str(state_path)},
            )
        policy = self._load_json_object(policy_path)
        spec = self._machine_config_field_spec(policy, field_key)
        field = spec["field"]
        options = [
            str(option.get("value") or "")
            for option in field.get("options") or []
            if isinstance(option, dict) and str(option.get("value") or "")
        ]
        if len(options) < 2:
            raise NativeCaseError(
                "ci_capability_gap_policy_mutation_driver: machine_config field does not have two selectable values",
                details={"field": field_key, "options": options},
            )
        machine_id = self.current_daemon_machine_id_remote()
        state = self._load_json_object(state_path, default={"schema": "intern-agents.env-switch-state.v1", "machines": {}})
        machines = state.get("machines") if isinstance(state.get("machines"), dict) else {}
        current_record = machines.get(machine_id) if isinstance(machines.get(machine_id), dict) else {}
        group_key = str(spec.get("group_key") or "")
        use_env_switches = spec.get("schema") == "env_switches" and group_key
        current_values_by_group = dict(current_record.get("group_values") or {}) if use_env_switches else {}
        current_fields = (
            dict(current_values_by_group.get(group_key) or {})
            if use_env_switches
            else dict(current_record.get("fields") or {})
        )
        default_value = str(field.get("default") or options[0])
        current_value = str(current_fields.get(field_key) or default_value)
        target_value = "enabled" if current_value != "enabled" and "enabled" in options else ""
        if not target_value:
            target_value = "disabled" if current_value != "disabled" and "disabled" in options else ""
        if not target_value:
            target_value = next((value for value in options if value != current_value), "")
        if not target_value or target_value == current_value:
            raise NativeCaseError(
                "ci_capability_gap_policy_mutation_driver: no alternate machine_config value available",
                details={"field": field_key, "current_value": current_value, "options": options},
            )
        next_state = json.loads(json.dumps(state))
        next_state["schema"] = "intern-agents.env-switch-state.v1" if use_env_switches else str(
            next_state.get("schema") or "intern-agents.machine-config-state.v1"
        )
        next_machines = next_state.setdefault("machines", {})
        next_record = dict(next_machines.get(machine_id) if isinstance(next_machines.get(machine_id), dict) else {})
        if use_env_switches:
            enabled_groups = [str(item) for item in next_record.get("enabled_groups") or [] if str(item)]
            if group_key not in enabled_groups:
                enabled_groups.append(group_key)
            next_group_values = dict(next_record.get("group_values") or {})
            next_fields = dict(next_group_values.get(group_key) or {})
            next_fields[field_key] = target_value
            next_group_values[group_key] = next_fields
            next_record["enabled_groups"] = enabled_groups
            next_record["group_values"] = next_group_values
        else:
            next_fields = dict(next_record.get("fields") or {})
            next_fields[field_key] = target_value
            next_record["fields"] = next_fields
        next_record.update({
            "operation_id": marker,
            "ci_case_id": remote.case_id,
            "ci_resource_namespace": remote.resource_namespace,
        })
        next_machines[machine_id] = next_record
        backup = {
            "state_path": str(state_path),
            "machine_id": machine_id,
            "field": field_key,
            "group_key": group_key,
            "schema": spec.get("schema"),
            "marker": marker,
            "previous_fields": current_fields,
            "previous_enabled_groups": list(current_record.get("enabled_groups") or []),
            "previous_group_values": dict(current_record.get("group_values") or {}),
            "previous_operation_id": str(current_record.get("operation_id") or ""),
            "previous_value": current_value,
            "target_value": target_value,
            "previous_record_present": machine_id in machines,
        }
        backup_path = remote.artifact_dir / f"{remote.case_no}_machine_config_backup.json"
        backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._write_json_atomic(state_path, next_state)
        remote.artifacts["machine_config_mutation"] = backup | {"backup_path": str(backup_path)}
        return backup | {"backup_path": str(backup_path), "options": options}

    def restore_machine_config_marker_remote(self, mutation: dict[str, Any]) -> dict[str, Any]:
        if not mutation:
            return {"restored": False, "reason": "no_mutation"}
        state_path = Path(str(mutation.get("state_path") or ""))
        machine_id = str(mutation.get("machine_id") or "")
        field_key = str(mutation.get("field") or "")
        marker = str(mutation.get("marker") or "")
        if not state_path.is_file() or not machine_id or not field_key:
            return {"restored": False, "reason": "missing_restore_target", "mutation": mutation}
        state = self._load_json_object(state_path, default={"schema": "intern-agents.env-switch-state.v1", "machines": {}})
        machines = state.get("machines") if isinstance(state.get("machines"), dict) else {}
        record = machines.get(machine_id) if isinstance(machines.get(machine_id), dict) else {}
        group_key = str(mutation.get("group_key") or "")
        use_env_switches = mutation.get("schema") == "env_switches" and group_key
        fields = (
            dict((record.get("group_values") or {}).get(group_key) or {})
            if use_env_switches
            else dict(record.get("fields") or {})
        )
        if marker and record.get("operation_id") not in {"", marker} and fields.get(field_key) != mutation.get("target_value"):
            return {
                "restored": False,
                "reason": "current_state_changed_after_case",
                "machine_id": machine_id,
                "field": field_key,
                "current_operation_id": record.get("operation_id"),
            }
        previous_fields = dict(mutation.get("previous_fields") or {})
        if use_env_switches:
            record["enabled_groups"] = list(mutation.get("previous_enabled_groups") or [])
            record["group_values"] = dict(mutation.get("previous_group_values") or {})
        else:
            if field_key in previous_fields:
                fields[field_key] = previous_fields[field_key]
            else:
                fields.pop(field_key, None)
            record["fields"] = fields
        if mutation.get("previous_operation_id"):
            record["operation_id"] = mutation.get("previous_operation_id")
        else:
            record.pop("operation_id", None)
        if use_env_switches or fields or record.get("operation_id"):
            machines[machine_id] = record
        elif machine_id in machines:
            machines.pop(machine_id, None)
        state["machines"] = machines
        self._write_json_atomic(state_path, state)
        return {
            "restored": True,
            "machine_id": machine_id,
            "field": field_key,
            "restored_value": previous_fields.get(field_key, ""),
            "state_path": str(state_path),
        }

    def relay_provider_env_marker_remote(self, *, provider: str, marker: str, env_key: str = "") -> dict[str, Any]:
        provider = str(provider or "").strip().lower()
        if provider not in {"codex", "claude"}:
            raise NativeCaseError(
                "ci_capability_gap_policy_mutation_driver: unsupported provider for relay policy marker",
                details={"provider": provider, "supported": ["codex", "claude"]},
            )
        marker = str(marker or "").strip()
        if not marker:
            raise NativeCaseError("ci_capability_gap_policy_mutation_driver: relay provider marker is empty")
        env_key = str(env_key or f"CI_BUG0065_{provider.upper()}_POLICY_MARKER").strip()
        if not env_key or not env_key.replace("_", "A").isalnum() or not env_key[0].isalpha():
            raise NativeCaseError(
                "ci_capability_gap_policy_mutation_driver: invalid provider policy env marker key",
                details={"env_key": env_key},
            )
        policy_path = self._relay_policy_path()
        if not policy_path.is_file():
            raise NativeCaseError(
                "ci_capability_gap_policy_mutation_driver: relay policy file is not available on this debug machine",
                details={"policy_path": str(policy_path)},
            )
        policy = self._load_json_object(policy_path)
        next_policy = json.loads(json.dumps(policy))
        provider_policy = next_policy.setdefault(provider, {})
        if not isinstance(provider_policy, dict):
            raise NativeCaseError(
                "ci_capability_gap_policy_mutation_driver: provider policy is not an object",
                details={"provider": provider, "policy_path": str(policy_path)},
            )
        session_env = provider_policy.setdefault("session_env", {})
        if not isinstance(session_env, dict):
            raise NativeCaseError(
                "ci_capability_gap_policy_mutation_driver: provider session_env is not an object",
                details={"provider": provider, "policy_path": str(policy_path)},
            )
        env = session_env.setdefault("env", {})
        if not isinstance(env, dict):
            raise NativeCaseError(
                "ci_capability_gap_policy_mutation_driver: provider session_env.env is not an object",
                details={"provider": provider, "policy_path": str(policy_path)},
            )
        previous_present = env_key in env
        previous_value = str(env.get(env_key) or "")
        env[env_key] = marker
        self._write_json_atomic(policy_path, next_policy)
        backup = {
            "policy_path": str(policy_path),
            "provider": provider,
            "env_key": env_key,
            "marker": marker,
            "previous_present": previous_present,
            "previous_value": previous_value,
        }
        backup_path = self._remote().artifact_dir / f"{self._remote().case_no}_{provider}_policy_env_marker_backup.json"
        backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self._remote().artifacts[f"{provider}_policy_env_marker"] = backup | {"backup_path": str(backup_path)}
        return backup | {"backup_path": str(backup_path)}

    def restore_relay_provider_env_marker_remote(self, mutation: dict[str, Any]) -> dict[str, Any]:
        if not mutation:
            return {"restored": False, "reason": "no_mutation"}
        policy_path = Path(str(mutation.get("policy_path") or ""))
        provider = str(mutation.get("provider") or "").strip().lower()
        env_key = str(mutation.get("env_key") or "").strip()
        marker = str(mutation.get("marker") or "")
        if not policy_path.is_file() or provider not in {"codex", "claude"} or not env_key:
            return {"restored": False, "reason": "missing_restore_target", "mutation": mutation}
        policy = self._load_json_object(policy_path)
        provider_policy = policy.get(provider) if isinstance(policy.get(provider), dict) else {}
        session_env = provider_policy.get("session_env") if isinstance(provider_policy.get("session_env"), dict) else {}
        env = session_env.get("env") if isinstance(session_env.get("env"), dict) else {}
        current_value = str(env.get(env_key) or "")
        if marker and current_value != marker:
            return {
                "restored": False,
                "reason": "current_policy_changed_after_case",
                "provider": provider,
                "env_key": env_key,
                "current_present": env_key in env,
            }
        next_policy = json.loads(json.dumps(policy))
        next_provider = next_policy.setdefault(provider, {})
        next_session_env = next_provider.setdefault("session_env", {})
        next_env = next_session_env.setdefault("env", {})
        if mutation.get("previous_present"):
            next_env[env_key] = str(mutation.get("previous_value") or "")
        else:
            next_env.pop(env_key, None)
        self._write_json_atomic(policy_path, next_policy)
        return {
            "restored": True,
            "policy_path": str(policy_path),
            "provider": provider,
            "env_key": env_key,
            "previous_present": bool(mutation.get("previous_present")),
        }

    def daemon_policy_fingerprint_remote(self) -> dict[str, Any]:
        path = self._daemon_policy_path()
        exists = path.is_file()
        stat = path.stat() if exists else None
        data = self._load_json_object(path) if exists else {}
        codex = data.get("codex") if isinstance(data.get("codex"), dict) else {}
        return {
            "path": str(path),
            "exists": exists,
            "mtime_ns": stat.st_mtime_ns if stat else 0,
            "size": stat.st_size if stat else 0,
            "schema": str(data.get("schema") or ""),
            "deployment_id": str(data.get("deployment_id") or ""),
            "daemon_policy": bool(data.get("daemon_policy")),
            "session_env_present": isinstance(data.get("session_env"), dict),
            "codex_session_env_present": isinstance(codex.get("session_env"), dict),
        }

    def session_env_report_remote(self) -> dict[str, Any]:
        report = self._load_json_object(self._session_env_report_path(), default={})
        return redact_report_value(report)

    def codex_env_report_remote(self) -> dict[str, Any]:
        report = self.session_env_report_remote()
        providers = report.get("providers") if isinstance(report.get("providers"), dict) else {}
        codex = providers.get("codex") if isinstance(providers.get("codex"), dict) else {}
        return codex

    def session_fingerprint_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        status = remote.ctx.action.session.status_for_workspace_remote(workspace, intern)
        tmux_session = str(status.get("tmux_session") or "")
        panes: list[dict[str, str]] = []
        policy_hash = ""
        if tmux_session:
            panes_result = remote.run_cmd(
                f"tmux panes {intern}",
                ["tmux", "list-panes", "-s", "-t", f"={tmux_session}", "-F", "#{pane_id}\t#{pane_pid}\t#{pane_current_command}"],
                timeout=30,
                check=False,
            )
            if panes_result.returncode == 0:
                for line in panes_result.stdout.splitlines():
                    parts = line.split("\t")
                    if len(parts) >= 3:
                        panes.append({"pane_id": parts[0], "pane_pid": parts[1], "command": parts[2]})
            policy_hashes: dict[str, str] = {}
            for key in ("CODEX_POLICY_ENV_HASH", "CLAUDE_POLICY_ENV_HASH"):
                env_result = remote.run_cmd(
                    f"tmux policy hash {intern} {key}",
                    ["tmux", "show-environment", "-t", f"={tmux_session}", key],
                    timeout=30,
                    check=False,
                )
                if env_result.returncode == 0:
                    raw = env_result.stdout.strip()
                    if raw.startswith(key + "="):
                        policy_hashes[key] = raw.split("=", 1)[1]
            policy_hash = policy_hashes.get("CODEX_POLICY_ENV_HASH", "")
        else:
            policy_hashes = {}
        return {
            "session_status": status,
            "tmux_session": tmux_session,
            "pane_pids": [item["pane_pid"] for item in panes],
            "panes": panes,
            "codex_policy_env_hash": policy_hash,
            "claude_policy_env_hash": policy_hashes.get("CLAUDE_POLICY_ENV_HASH", ""),
            "policy_env_hashes": policy_hashes,
        }

    def wait_session_policy_restart_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        before: dict[str, Any],
        *,
        expected_hash: str,
        timeout: int = 300,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        last_check: dict[str, Any] = {}
        while time.time() < deadline:
            current = self.session_fingerprint_remote(workspace, intern)
            last = current
            check = policy_assertions.idle_codex_policy_restart_check(before, current, expected_hash=expected_hash)
            last_check = check
            if check["ok"]:
                return {"restarted": True, "before": before, "after": current, "expected_hash": expected_hash}
            time.sleep(3)
        raise NativeCaseError(
            "product_bug_idle_codex_not_restarted: Idle Codex session did not restart after policy env hash changed",
            details={"before": before, "last": last, "expected_hash": expected_hash, "last_check": last_check},
        )

    def assert_no_duplicate_policy_restart_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        before: dict[str, Any],
        *,
        expected_hash: str,
        timeout: int = 45,
    ) -> dict[str, Any]:
        remote = self._remote()
        deadline = time.time() + timeout
        last = before
        while time.time() < deadline:
            current = self.session_fingerprint_remote(workspace, intern)
            last = current
            duplicate_check = policy_assertions.policy_replay_no_duplicate_restart_check(before, current, expected_hash=expected_hash)
            if not duplicate_check["ok"]:
                raise NativeCaseError(
                    "product_bug_policy_replay_duplicate_restart: unchanged policy replay restarted Codex again",
                    details=duplicate_check["detail"],
                )
            time.sleep(3)
        final_check = policy_assertions.unchanged_policy_replay_kept_codex_session_check(before, last, expected_hash=expected_hash)
        self._require_check(final_check["name"], final_check["ok"], final_check["detail"])
        return {"duplicate_restart": False, "before": before, "after": last, "expected_hash": expected_hash}

    def wait_relay_machine_state_remote(self, machine_id: str, *, online: bool, timeout: int = 120) -> dict[str, Any]:
        remote = self._remote()
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            machines = remote.relay_json(
                ("relay machine online " if online else "relay machine offline ") + machine_id,
                "GET",
                "/api/machines",
                timeout=30,
            )
            entry = machines.get(machine_id) if isinstance(machines, dict) else None
            connected = bool(entry) and (entry.get("ws_connected") is not False if isinstance(entry, dict) else True)
            last = {"machine_id": machine_id, "entry": entry or {}, "machines_count": len(machines) if isinstance(machines, dict) else 0}
            if connected is online:
                return last | {"online": online}
            time.sleep(2)
        raise NativeCaseError(
            f"product_bug_daemon_reconnect_not_registered: relay machine did not reach expected online={online}",
            details={"machine_id": machine_id, "last": last},
        )

    def daemon_sync_existing_deployment_remote(self, label: str, *, machine_id: str) -> dict[str, Any]:
        remote = self._remote()
        before = self.daemon_status_remote(label + " daemon status before")
        restart = remote.run_cmd(label + " daemon restart", [*remote.internctl, "daemon", "restart"], timeout=240)
        after = self.daemon_status_remote(label + " daemon status after")
        online = self.wait_relay_machine_state_remote(machine_id, online=True, timeout=180)
        check = policy_assertions.daemon_connected_after_lifecycle_check(
            label.replace(" ", "_") + "_daemon_restarted_connected",
            before=before,
            stdout_key="restart_stdout",
            stdout=restart.stdout,
            status=after,
            relay_online=online,
        )
        self._require_check(check["name"], check["ok"], check["detail"])
        return {"before": before, "restart_stdout": restart.stdout, "after": after, "relay_online": online}

    def start_single_daemon_remote(self, label: str, *, machine_id: str) -> dict[str, Any]:
        remote = self._remote()
        start = remote.run_cmd(label + " daemon start", [*remote.internctl, "daemon", "start"], timeout=180)
        status = self.daemon_status_remote(label + " daemon status after start")
        online = self.wait_relay_machine_state_remote(machine_id, online=True, timeout=180)
        check = policy_assertions.daemon_connected_after_lifecycle_check(
            label.replace(" ", "_") + "_daemon_started_connected",
            stdout_key="start_stdout",
            stdout=start.stdout,
            status=status,
            relay_online=online,
        )
        self._require_check(check["name"], check["ok"], check["detail"])
        return {"start_stdout": start.stdout, "status": status, "relay_online": online}

    def assert_no_relay_restart_or_global_reset_remote(self, before: dict[str, Any]) -> dict[str, Any]:
        remote = self._remote()
        relay = remote.relay_json("relay status no restart assertion", "GET", "/api/status", timeout=60)
        check = policy_assertions.no_relay_restart_or_global_reset_check(before, relay, remote.steps)
        self._require_check(check["name"], check["ok"], check["detail"])
        return check["detail"]
