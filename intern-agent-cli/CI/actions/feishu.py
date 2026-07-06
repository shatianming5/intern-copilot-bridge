from __future__ import annotations

from dataclasses import dataclass
import time
import urllib.parse
from typing import Any

from CI.helpers.native_error import NativeCaseError
from CI.helpers.product_cli_helper import parse_json_output, tail


@dataclass
class FeishuActions:
    ctx: Any

    def _remote(self) -> Any:
        remote = getattr(self.ctx, "remote_context", None)
        if remote is None:
            raise RuntimeError("ctx.action.feishu.* requires RemoteCaseContext")
        return remote

    def relay_registry_entry_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        project = str(workspace["display"])
        registry = remote.relay_json(f"relay registry lookup {intern}", "GET", "/api/registry", timeout=60)
        entry = registry.get(intern) if isinstance(registry, dict) else None
        if isinstance(entry, dict) and entry.get("project") == project:
            return dict(entry)
        for value in registry.values() if isinstance(registry, dict) else []:
            if not isinstance(value, dict):
                continue
            if value.get("name") == intern and value.get("project") == project:
                return dict(value)
        return {}

    def wait_relay_registry_entry_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        timeout: int = 180,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            entry = self.relay_registry_entry_remote(workspace, intern)
            last = entry
            if entry.get("chat_id"):
                return entry
            time.sleep(2)
        raise NativeCaseError(f"relay registry entry timed out for {workspace['display']}:{intern}: {last}")

    def wait_current_scene_green_light_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        expected_type: str = "",
        timeout: int = 180,
    ) -> dict[str, Any]:
        remote = self._remote()
        project = str(workspace["display"])
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            scene = remote.relay_json(f"relay current scene {intern}", "GET", "/api/scene", timeout=60)
            groups = scene.get("active_groups") if isinstance(scene, dict) else []
            if isinstance(groups, list):
                for group in groups:
                    if not isinstance(group, dict):
                        continue
                    if group.get("name") != intern or group.get("project") != project:
                        continue
                    last = {"scene_summary": scene.get("summary", {}), "group": dict(group)}
                    type_ok = not expected_type or group.get("type") == expected_type
                    green_ok = group.get("group_light") == "green" and str(group.get("last_group_name") or "").startswith("🟢")
                    if type_ok and green_ok:
                        return last
            else:
                last = {"scene": scene}
            time.sleep(2)
        raise NativeCaseError(f"green Feishu group light timed out for {project}:{intern}: {last}")

    def relay_registry_absent_evidence_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        check_daemon_lookup: bool = True,
    ) -> dict[str, Any]:
        remote = self._remote()
        entry = self.relay_registry_entry_remote(workspace, intern)
        lookup: dict[str, Any] = {}
        if check_daemon_lookup:
            query = urllib.parse.urlencode({"intern": intern, "project": str(workspace["display"])})
            lookup = remote.http_json(f"chat lookup absent {intern}", "GET", "/api/chat/lookup?" + query, timeout=30)
        return {"entry": entry, "lookup": lookup}

    def wait_chat_lookup_remote(self, workspace: dict[str, Any], intern: str, *, timeout: int = 300) -> dict[str, Any]:
        remote = self._remote()
        project = str(workspace["display"])
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            query = urllib.parse.urlencode({"intern": intern, "project": project})
            try:
                result = remote.http_json("chat lookup " + intern, "GET", "/api/chat/lookup?" + query, timeout=30)
                if result.get("chat_id"):
                    return result
                last = result
            except Exception as exc:  # noqa: BLE001
                last = {"error": str(exc)}
            time.sleep(2)
        raise NativeCaseError(f"chat lookup timed out for {project}:{intern}: {last}")

    def group_mode_cli_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        command: str,
        mode: str,
        check: bool = True,
    ) -> dict[str, Any]:
        remote = self._remote()
        project = str(workspace["display"])
        result = remote.run_cmd(
            f"group {command} {project}:{intern} {mode}",
            [*remote.internctl, "group", command, intern, "--project", project, "--mode", mode, "--json"],
            timeout=120,
            check=False,
        )
        payload: dict[str, Any] = {}
        if result.stdout.strip():
            try:
                parsed = parse_json_output(f"group {command} {intern}", result.stdout)
            except Exception:
                payload = {"raw": tail(result.stdout, 2000)}
            else:
                payload = parsed if isinstance(parsed, dict) else {"raw": parsed}
        report = {
            "returncode": result.returncode,
            "stdout": tail(result.stdout, 2000),
            "stderr": tail(result.stderr, 2000),
            "payload": payload,
            "command": command,
            "mode": mode,
            "project": project,
            "intern": intern,
        }
        if check and result.returncode != 0:
            raise NativeCaseError(f"group {command} failed: {report!r}")
        return report

    def group_config_remote(self, workspace: dict[str, Any], intern: str, *, check: bool = True) -> dict[str, Any]:
        remote = self._remote()
        project = str(workspace["display"])
        query = urllib.parse.urlencode({"intern": intern, "project": project})
        trigger = remote.relay_request_json(
            f"group trigger mode read {project}:{intern}",
            "GET",
            "/api/chat/trigger_mode?" + query,
            timeout=60,
            check=check,
        )
        detail = remote.relay_request_json(
            f"group detail mode read {project}:{intern}",
            "GET",
            "/api/chat/detail_mode?" + query,
            timeout=60,
            check=check,
        )
        trigger_body = trigger.get("body") if isinstance(trigger.get("body"), dict) else {}
        detail_body = detail.get("body") if isinstance(detail.get("body"), dict) else {}
        return {
            "project": project,
            "intern": intern,
            "trigger_status": trigger.get("status_code"),
            "detail_status": detail.get("status_code"),
            "trigger_mode": trigger_body.get("mode"),
            "detail_mode": detail_body.get("mode"),
            "trigger": trigger,
            "detail": detail,
        }

    def set_group_config_direct_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        trigger_mode: str,
        detail_mode: str,
        check: bool = True,
    ) -> dict[str, Any]:
        remote = self._remote()
        project = str(workspace["display"])
        trigger = remote.relay_request_json(
            f"seed trigger mode {project}:{intern}",
            "POST",
            "/api/chat/trigger_mode",
            {"intern_name": intern, "project": project, "mode": trigger_mode},
            timeout=90,
            check=check,
        )
        detail = remote.relay_request_json(
            f"seed detail mode {project}:{intern}",
            "POST",
            "/api/chat/detail_mode",
            {"intern_name": intern, "project": project, "mode": detail_mode},
            timeout=90,
            check=check,
        )
        config = self.group_config_remote(workspace, intern, check=check)
        ok = config.get("trigger_mode") == trigger_mode and config.get("detail_mode") == detail_mode
        return {"trigger": trigger, "detail": detail, "config": config, "ok": ok}

    def wait_question_poll_remote(
        self,
        intern: str,
        question_id: str,
        *,
        project: str = "",
        owner: str = "",
        status: str = "",
        timeout: int = 120,
    ) -> dict[str, Any]:
        remote = self._remote()
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            query_payload = {"intern_name": intern, "question_id": question_id}
            if project:
                query_payload["project"] = project
            query = urllib.parse.urlencode(query_payload)
            result = remote.http_json("question poll " + question_id[:8], "GET", "/api/question/poll?" + query, timeout=30)
            last = result
            if owner and result.get("owner") != owner:
                time.sleep(2)
                continue
            if status and result.get("status") != status:
                time.sleep(2)
                continue
            return result
        raise NativeCaseError(f"question poll timed out for {intern} question_id={question_id}: {last}")
