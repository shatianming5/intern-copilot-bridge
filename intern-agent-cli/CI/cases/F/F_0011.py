import os
from typing import Any

from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0011.s01_daemon_cli_and_http_status",
    "F_0011.s03_relay_status_and_machine_connections",
    "F_0011.s02_codex_lb_policy_and_secret",
)


CASE = CaseDefinition(
    id="F_0011_daemon_status_readiness_api",
    name="Daemon status and readiness API",
    description=(
        "Validates deployed daemon CLI/HTTP readiness, relay connectivity, "
        "machine identity, Codex load-balance policy, and redacted LB secret evidence."
    ),
    stage="remote",
    timeout_seconds=900,
    kind="f_daemon_relay_api",
    tags=("F", "daemon", "relay", "status"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "cli.internctl",
            "cli.intern_adminctl",
            "daemon.read_status",
            "relay.read_status_machines_health",
            "check_daemon_health",
            "check_relay_health",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.action_ok",
            "native.callback_health_probe",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-a", "mode": "read"},
            {"resource": "daemon:debug-b", "mode": "read"},
            {"resource": "namespace:ci_f_0011", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "read"},
            {"resource": "secret:codex-lb:redacted", "mode": "read"},
        ),
        "resources": (
            "namespace:ci_f_0011",
            "daemon:debug-a",
            "daemon:debug-b",
            "relay-global:test-relay",
            "secret:codex-lb:redacted",
        ),
        "run_mode": "remote_deployed_api",
        "notes": (
            "Do not package, reset, deploy, or restart relay.",
            "Report only redacted secret presence, never the secret value.",
        ),
    },
)


def run_f_daemon_status_readiness_api(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def s01_daemon_cli_and_http_status() -> dict[str, Any]:
        cli = self.json_cmd("F_0011 daemon status cli", [*self.internctl, "daemon", "status", "--json"], timeout=120)
        http = self.http_json("F_0011 daemon http status", "GET", "/api/status", timeout=60)
        cli_status = cli.get("status") if isinstance(cli.get("status"), dict) else {}
        instance_id = str(http.get("instance_id") or cli_status.get("instance_id") or "")
        expected_machine = os.environ.get("INTERN_CI_MACHINE_ID", "")
        self.require("f0011_daemon_cli_running", cli.get("running") is True, cli)
        self.require("f0011_daemon_http_running", http.get("running") is True, http)
        self.require("f0011_daemon_relay_connected", http.get("relay_connected") is True or cli_status.get("relay_connected") is True, {"cli": cli, "http": http})
        self.require("f0011_daemon_machine_id_present", bool(instance_id), {"instance_id": instance_id, "cli": cli, "http": http})
        if expected_machine:
            self.require("f0011_daemon_machine_id_matches_env", instance_id == expected_machine, {"instance_id": instance_id, "expected": expected_machine})
        state["daemon"] = {"cli": cli, "http": http, "instance_id": instance_id}
        return state["daemon"]

    def s02_codex_lb_policy_and_secret() -> dict[str, Any]:
        lb = self.json_cmd("F_0011 codex load balance status", [*self.internctl, "config", "codex-load-balance", "status", "--json"], timeout=90, check=False)
        user_env_path = self.work_root / "enterprise_policy" / "daemon" / "user.env"
        user_env_text = user_env_path.read_text(encoding="utf-8", errors="replace") if user_env_path.is_file() else ""
        codex_env_path = self.work_root / "enterprise_policy" / "daemon" / "runtime" / "codex.env"
        codex_env_text = codex_env_path.read_text(encoding="utf-8", errors="replace") if codex_env_path.is_file() else ""
        env_key = str(self.env.get("CODEX_LB_ENV_KEY") or os.environ.get("CODEX_LB_ENV_KEY") or "")
        if not env_key:
            for line in (user_env_text + "\n" + codex_env_text).splitlines():
                if line.startswith("CODEX_LB_ENV_KEY="):
                    env_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        candidate_keys = [key for key in (env_key, "LB_API_KEY", "CODEX_LB_API_KEY") if key]
        secret_present = any(
            self.env.get(key) or os.environ.get(key) or f"{key}=" in user_env_text or f"{key}=" in codex_env_text
            for key in candidate_keys
        )
        detail = {
            "lb_status": lb,
            "user_env_path": str(user_env_path),
            "codex_env_path": str(codex_env_path),
            "codex_lb_env_key": env_key,
            "candidate_secret_keys": candidate_keys,
            "secret_present": secret_present,
            "secret_value": "<redacted>" if secret_present else "",
        }
        self.require("f0011_codex_lb_enabled", lb.get("ok") is True and lb.get("enabled") is True, detail)
        self.require("f0011_codex_lb_api_key_present_redacted", secret_present, detail)
        state["codex_lb"] = detail
        return detail

    def s03_relay_status_and_machine_connections() -> dict[str, Any]:
        relay_status = self.relay_json("F_0011 relay status", "GET", "/api/status", timeout=60)
        relay_health = self.relay_json("F_0011 relay health", "GET", "/api/health", timeout=60)
        machines = self.relay_json("F_0011 relay machines", "GET", "/api/machines", timeout=60)
        expected = int(self.args.expected_machines or 2)
        machine_count = len(machines) if isinstance(machines, dict) else 0
        instance_id = str((state.get("daemon") or {}).get("instance_id") or "")
        self.require("f0011_relay_running", relay_status.get("running") is True, relay_status)
        self.require("f0011_relay_health_ok", relay_health.get("ok") is True, relay_health)
        self.require(
            "f0011_relay_expected_machine_connections",
            int(relay_status.get("machines_connected") or 0) >= expected and machine_count >= expected,
            {"relay_status": relay_status, "machines": machines, "expected": expected},
        )
        if instance_id:
            self.require("f0011_current_machine_visible_on_relay", instance_id in machines, {"instance_id": instance_id, "machines": machines})
        state["relay"] = {"status": relay_status, "health": relay_health, "machines": machines}
        return state["relay"]

    self.run_ordered_scenarios([
        ("F_0011.s01_daemon_cli_and_http_status", s01_daemon_cli_and_http_status),
        ("F_0011.s03_relay_status_and_machine_connections", s03_relay_status_and_machine_connections),
        ("F_0011.s02_codex_lb_policy_and_secret", s02_codex_lb_policy_and_secret),
    ])
    self.artifacts["daemon_status_readiness"] = state
