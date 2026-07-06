from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time
import urllib.parse
from typing import Any

from CI.helpers.native_error import NativeCaseError
from CI.helpers.product_cli_helper import tail


@dataclass
class RelayDaemonActions:
    ctx: Any

    def _remote(self) -> Any:
        remote = getattr(self.ctx, "remote_context", None)
        if remote is None:
            raise RuntimeError("ctx.action.relay_daemon.* requires RemoteCaseContext")
        return remote

    def owner_identity_payload_remote(self) -> dict[str, str]:
        remote = self._remote()
        owner_path = remote.work_root / "enterprise_policy" / "daemon" / "_owner.json"
        if not owner_path.is_file():
            raise NativeCaseError(f"daemon owner identity missing: {owner_path}")
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        payload: dict[str, str] = {}
        for source, target in (
            ("owner_open_id", "owner_open_id"),
            ("open_id", "owner_open_id"),
            ("owner_mobile", "owner_mobile"),
            ("mobile", "owner_mobile"),
        ):
            value = str(owner.get(source) or "").strip()
            if value and target not in payload:
                payload[target] = value
        return payload

    def relay_chat_lookup_remote(self, intern: str, project: str) -> dict[str, Any]:
        remote = self._remote()
        query = urllib.parse.urlencode({"intern": intern, "project": project})
        return remote.relay_json(f"relay chat lookup {project}:{intern}", "GET", "/api/chat/lookup?" + query, timeout=60)

    def daemon_group_list_entry_remote(self, project: str, intern: str) -> dict[str, Any]:
        remote = self._remote()
        result = remote.request_any_json("daemon group list", "GET", remote.daemon_base() + "/api/group/list", timeout=60)
        groups = result.get("body")
        if not isinstance(groups, list):
            raise NativeCaseError(f"daemon /api/group/list response is not a list: {groups}")
        for entry in groups:
            if isinstance(entry, dict) and entry.get("project") == project and entry.get("intern_name") == intern:
                return entry
        return {}

    def chat_lookup_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        query = urllib.parse.urlencode({"intern": intern, "project": str(workspace["display"])})
        return remote.http_json(f"chat lookup fixture {intern}", "GET", "/api/chat/lookup?" + query, timeout=30)

    @staticmethod
    def _registry_safe_part(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_.-]+", "_", (value or "").strip()).strip("._") or "default"

    def write_no_group_chat_fixture_remote(self, workspace: dict[str, Any], intern: str, suffix: str) -> dict[str, Any]:
        remote = self._remote()
        project = str(workspace["display"])
        chat_id = f"oc_no_group_{remote.resource_namespace}_{suffix}"
        registry_dir = remote.work_root / ".feishu_registry"
        registry_dir.mkdir(parents=True, exist_ok=True)
        path = registry_dir / f"{self._registry_safe_part(project)}__{self._registry_safe_part(intern)}.json"
        payload = {
            "internName": intern,
            "chatId": chat_id,
            "project": project,
            "ci_no_group_fixture": True,
            "case_id": remote.case_id,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"intern": intern, "project": project, "chat_id": chat_id, "path": str(path)}

    def remove_no_group_chat_fixtures_remote(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        removed: list[str] = []
        for entry in entries:
            path = Path(str(entry.get("path") or ""))
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        return {"removed": removed}

    def restart_daemon_for_fixture_registry_remote(self, reason: str) -> dict[str, Any]:
        remote = self._remote()
        restart = remote.run_cmd(f"daemon restart {reason}", [*remote.internctl, "daemon", "restart"], timeout=180)
        status = remote.json_cmd(f"daemon status {reason}", [*remote.internctl, "daemon", "status", "--json"], timeout=90)
        return {"restart_stdout": restart.stdout, "status": status}

    def wait_daemon_log_contains_remote(self, marker: str, *, timeout: int = 45) -> dict[str, Any]:
        remote = self._remote()
        candidates = [
            *remote.work_root.glob("llm_intern_logs/versions/*/projects/_system/daemon/feishu_daemon.log"),
            *remote.work_root.glob("llm_intern_logs/**/feishu_daemon.log"),
        ]
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key not in seen:
                seen.add(key)
                unique.append(path)
        deadline = time.time() + timeout
        last_tail = ""
        checked = [str(path) for path in unique]
        while time.time() < deadline:
            for path in unique:
                if not path.is_file():
                    continue
                text = tail(path.read_text(encoding="utf-8", errors="replace"), 16000)
                last_tail = text
                if marker in text:
                    return {"found": True, "path": str(path), "marker": marker, "tail": tail(text, 2000)}
            time.sleep(2)
        return {"found": False, "marker": marker, "checked_paths": checked, "tail": tail(last_tail, 2000)}
