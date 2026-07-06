from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import shutil
from typing import Any

from CI.assertions import intern as intern_assertions
from CI.helpers.native_error import NativeCaseError
from CI.helpers.product_cli_helper import parse_json_output, tail


@dataclass
class InternActions:
    ctx: Any

    def _remote(self) -> Any:
        remote = getattr(self.ctx, "remote_context", None)
        if remote is None:
            raise RuntimeError("ctx.action.intern.* requires RemoteCaseContext")
        return remote

    def create_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        repo_url: str = "",
        intern_type: str = "codex",
    ) -> dict[str, Any]:
        remote = self._remote()
        result = remote.run_cmd(
            f"intern create {intern}",
            [
                *remote.internctl,
                "create",
                intern,
                "--project",
                str(workspace["display"]),
                "--repo-url",
                repo_url or str(workspace["repo_url"]),
                "--type",
                intern_type,
            ],
            timeout=240,
        )
        metadata = self.metadata_resolve_remote(workspace, intern)
        return {
            "intern": intern,
            "returncode": result.returncode,
            "stdout": tail(result.stdout, 1200),
            "stderr": tail(result.stderr, 1200),
            "metadata": metadata,
        }

    def create_case_remote(
        self,
        workspace: dict[str, Any],
        role: str,
        *,
        repo_url: str = "",
        intern_type: str = "codex",
    ) -> str:
        remote = self._remote()
        intern = remote.identity(role)
        result = self.create_remote(workspace, intern, repo_url=repo_url, intern_type=intern_type)
        remote.created["interns"].append(f"{workspace['display']}:{intern}")
        metadata = result["metadata"]
        if metadata.get("ok") is not True:
            raise NativeCaseError(f"metadata resolver failed for {intern}: {metadata}")
        remote.artifacts[f"metadata_{role}"] = metadata
        return intern

    @staticmethod
    def _parse_status_metadata(status_path: Path) -> dict[str, str]:
        text = status_path.read_text(encoding="utf-8", errors="replace") if status_path.is_file() else ""
        match = re.search(r"<!--\s*METADATA:(?P<body>.+?)\s*-->", text)
        if not match:
            return {}
        result: dict[str, str] = {}
        for pair in match.group("body").split(","):
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            result[key.strip().lower()] = value.strip()
        return result

    def create_fixture_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        intern_type: str = "codex",
        intern_role: str = "independent",
        team_id: str = "",
        repo_url: str = "",
        explicit_repo_url: bool = True,
        coordinator_id: str = "",
        standing_goal: str = "",
        skip_feishu_group: bool = True,
        skip_status_notify: bool = True,
        check: bool = True,
    ) -> dict[str, Any]:
        remote = self._remote()
        cmd = [
            *remote.internctl,
            "create",
            intern,
            "--project",
            str(workspace["display"]),
            "--type",
            intern_type,
            "--role",
            intern_role,
        ]
        if skip_feishu_group:
            cmd.append("--skip-feishu-group")
        if skip_status_notify:
            cmd.append("--skip-status-notify")
        if explicit_repo_url:
            cmd.extend(["--repo-url", repo_url or str(workspace["repo_url"])])
        if team_id:
            cmd.extend(["--team-id", team_id])
        if coordinator_id:
            cmd.extend(["--coordinator-id", coordinator_id])
        if standing_goal:
            cmd.extend(["--standing-goal", standing_goal])
        result = remote.run_cmd(f"fixture intern create {intern}", cmd, timeout=240, check=check)
        if not check and result.returncode != 0:
            return {
                "intern": intern,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }

        metadata = self.metadata_resolve_remote(workspace, intern)
        status_path = Path(str(metadata.get("status_path") or ""))
        knowledge_path = Path(str(metadata.get("knowledge_path") or ""))
        runtime = remote.runtime_dir(workspace, intern)
        hook_state_path = runtime / ".hook_state.json"
        session_key = f"{workspace['workspace_id']}:{intern}"
        sessions = remote.session_registry()
        session_entry = sessions.get(session_key, {})
        return {
            "intern": intern,
            "returncode": result.returncode,
            "metadata": metadata,
            "status_path": str(status_path),
            "knowledge_path": str(knowledge_path),
            "runtime": str(runtime),
            "hook_state_path": str(hook_state_path),
            "status_path_exists": status_path.is_file(),
            "knowledge_path_exists": knowledge_path.is_file(),
            "runtime_exists": runtime.is_dir(),
            "hook_state_exists": hook_state_path.is_file(),
            "status_meta": self._parse_status_metadata(status_path),
            "session_key": session_key,
            "session_entry": session_entry if isinstance(session_entry, dict) else {},
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def create_fixture_case_remote(
        self,
        workspace: dict[str, Any],
        role_name: str,
        *,
        intern_type: str = "codex",
        intern_role: str = "independent",
        team_id: str = "",
        repo_url: str = "",
        explicit_repo_url: bool = True,
        coordinator_id: str = "",
        standing_goal: str = "",
        skip_feishu_group: bool = True,
        skip_status_notify: bool = True,
        check: bool = True,
    ) -> dict[str, Any]:
        remote = self._remote()
        intern = remote.identity(role_name)
        evidence = self.create_fixture_remote(
            workspace,
            intern,
            intern_type=intern_type,
            intern_role=intern_role,
            team_id=team_id,
            repo_url=repo_url,
            explicit_repo_url=explicit_repo_url,
            coordinator_id=coordinator_id,
            standing_goal=standing_goal,
            skip_feishu_group=skip_feishu_group,
            skip_status_notify=skip_status_notify,
            check=check,
        )
        if not check and int(evidence.get("returncode") or 0) != 0:
            return evidence

        remote.created["interns"].append(f"{workspace['display']}:{intern}")
        metadata = evidence["metadata"]
        fixture_interns = remote.artifacts.setdefault("fixture_interns", {})
        if not isinstance(fixture_interns, dict):
            fixture_interns = {}
            remote.artifacts["fixture_interns"] = fixture_interns
        fixture_interns[f"{workspace['workspace_id']}:{intern}"] = {
            "metadata": metadata,
            "runtime": str(evidence.get("runtime") or ""),
        }
        contract = intern_assertions.fixture_intern_contract_checks(
            role_name,
            metadata=metadata,
            status_path=str(evidence.get("status_path") or ""),
            status_path_exists=bool(evidence.get("status_path_exists")),
            knowledge_path=str(evidence.get("knowledge_path") or ""),
            knowledge_path_exists=bool(evidence.get("knowledge_path_exists")),
            runtime=str(evidence.get("runtime") or ""),
            runtime_exists=bool(evidence.get("runtime_exists")),
            hook_state_path=str(evidence.get("hook_state_path") or ""),
            hook_state_exists=bool(evidence.get("hook_state_exists")),
            session_key=str(evidence.get("session_key") or f"{workspace['workspace_id']}:{intern}"),
            session_entry=evidence.get("session_entry") if isinstance(evidence.get("session_entry"), dict) else {},
            status_meta=evidence.get("status_meta") if isinstance(evidence.get("status_meta"), dict) else {},
            expected_type=intern_type,
            expected_role=intern_role,
            expected_team_id=team_id,
        )
        return {
            "intern": intern,
            "metadata": metadata,
            "status_path": str(evidence.get("status_path") or ""),
            "knowledge_path": str(evidence.get("knowledge_path") or ""),
            "runtime": str(evidence.get("runtime") or ""),
            "hook_state_path": str(evidence.get("hook_state_path") or ""),
            "status_meta": evidence.get("status_meta") if isinstance(evidence.get("status_meta"), dict) else {},
            "session_entry": evidence.get("session_entry") if isinstance(evidence.get("session_entry"), dict) else {},
            "stdout": str(evidence.get("stdout") or ""),
            "stderr": str(evidence.get("stderr") or ""),
            "checks": [
                {"name": "metadata_resolver_ok_" + intern, "ok": metadata.get("ok") is True, "detail": metadata},
                *contract["checks"],
            ],
            "contract": contract["detail"],
        }

    def delete_remote(self, workspace: dict[str, Any], intern: str, *, force: bool = True) -> dict[str, Any]:
        remote = self._remote()
        cmd = [*remote.internctl, "delete", intern, "--project", str(workspace["display"]), "--confirm"]
        if force:
            cmd.append("--force")
        result = remote.run_cmd(f"intern delete {intern}", cmd, timeout=240)
        return {
            "intern": intern,
            "force": force,
            "returncode": result.returncode,
            "stdout": tail(result.stdout, 1200),
            "stderr": tail(result.stderr, 1200),
        }

    @staticmethod
    def _cleanup_key(workspace: dict[str, Any], intern: str) -> str:
        return f"{workspace['workspace_id']}:{intern}"

    def _stored_fixture_metadata_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        fixture_interns = remote.artifacts.get("fixture_interns")
        if not isinstance(fixture_interns, dict):
            return {}
        stored = fixture_interns.get(self._cleanup_key(workspace, intern))
        if not isinstance(stored, dict):
            return {}
        metadata = stored.get("metadata")
        return metadata if isinstance(metadata, dict) else {}

    def cleanup_fixture_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        workspace_id = str(workspace["workspace_id"])
        project = str(workspace["display"])
        runtime = self.runtime_dir_remote(workspace, intern)
        removed: list[str] = []
        skipped: list[dict[str, str]] = []
        task_id = ""
        resolver = self._stored_fixture_metadata_remote(workspace, intern)
        state_path = runtime / ".hook_state.json"
        if not resolver and state_path.is_file():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(state.get("metadata_resolver"), dict):
                    resolver = state["metadata_resolver"]
            except Exception:
                resolver = {}
        if not resolver:
            resolved = remote.run_cmd(
                f"metadata resolve cleanup {intern}",
                [
                    *remote.internctl,
                    "metadata",
                    "resolve",
                    "--workspace",
                    workspace_id,
                    "--intern",
                    intern,
                    "--json",
                ],
                timeout=60,
                check=False,
            )
            if resolved.returncode == 0 and resolved.stdout.strip():
                parsed = parse_json_output(f"metadata resolve cleanup {intern}", resolved.stdout)
                if isinstance(parsed, dict) and parsed.get("ok") is True:
                    resolver = parsed
            if not resolver:
                skipped.append({"path": "<metadata_resolver>", "reason": "resolver_unavailable"})

        work_root = remote.work_root.resolve()

        def safe_absolute(raw: Any, label: str) -> Path | None:
            value = str(raw or "").strip()
            if not value:
                skipped.append({"path": value, "reason": f"{label}_empty"})
                return None
            path = Path(value)
            if not path.is_absolute():
                skipped.append({"path": value, "reason": f"{label}_relative"})
                return None
            resolved_path = path.resolve(strict=False)
            if resolved_path in {Path("/"), work_root} or resolved_path == Path(".").resolve():
                skipped.append({"path": str(path), "reason": f"{label}_unsafe_root"})
                return None
            return resolved_path

        def under(path: Path, root: Path) -> bool:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                return False

        metadata_root = safe_absolute(resolver.get("metadata_root"), "metadata_root") if resolver else None
        interns_root = (metadata_root / "interns").resolve(strict=False) if metadata_root else None
        tasks_root = safe_absolute(resolver.get("tasks_dir"), "tasks_dir") if resolver.get("tasks_dir") else None
        if metadata_root and tasks_root and not under(tasks_root, metadata_root):
            skipped.append({"path": str(tasks_root), "reason": "tasks_dir_outside_metadata_root"})
            tasks_root = None

        status_path = safe_absolute(resolver.get("status_path"), "status_path") if resolver else None
        if status_path and status_path.is_file():
            task_id = remote.ctx.action.task.parse_status_metadata_remote(status_path).get("task", "")
        metadata_dirs: set[Path] = set()
        for key in ("status_path", "knowledge_path"):
            path = safe_absolute(resolver.get(key), key) if resolver else None
            if not path:
                continue
            parent = path.parent.resolve(strict=False)
            if not metadata_root or not interns_root:
                skipped.append({"path": str(parent), "reason": f"{key}_missing_metadata_root"})
                continue
            if parent.name != intern or parent.parent.resolve(strict=False) != interns_root:
                skipped.append({"path": str(parent), "reason": f"{key}_not_in_intern_metadata_namespace"})
                continue
            if not under(parent, metadata_root) or parent == metadata_root:
                skipped.append({"path": str(parent), "reason": f"{key}_outside_metadata_root"})
                continue
            metadata_dirs.add(parent)
        for parent in sorted(metadata_dirs, key=str):
            if parent.is_dir():
                shutil.rmtree(parent, ignore_errors=True)
                removed.append(str(parent))
        if task_id:
            if tasks_root and metadata_root and task_id not in {"", ".", ".."}:
                task_dir = (tasks_root / task_id).resolve(strict=False)
                if (
                    task_dir.name == task_id
                    and under(task_dir, tasks_root)
                    and under(task_dir, metadata_root)
                    and task_dir != tasks_root
                    and task_dir.is_dir()
                ):
                    shutil.rmtree(task_dir, ignore_errors=True)
                    removed.append(str(task_dir))
                else:
                    skipped.append({"path": str(task_dir), "reason": "task_dir_not_in_metadata_namespace"})
            else:
                skipped.append({"path": task_id, "reason": "task_cleanup_missing_safe_tasks_root"})
        runtime_root = (work_root / "state" / "v1" / workspace_id / "interns").resolve(strict=False)
        runtime = runtime.resolve(strict=False)
        if runtime.is_dir():
            if workspace_id and runtime.name == intern and runtime.parent == runtime_root and under(runtime, runtime_root) and runtime != runtime_root:
                shutil.rmtree(runtime, ignore_errors=True)
                removed.append(str(runtime))
            else:
                skipped.append({"path": str(runtime), "reason": "runtime_not_in_workspace_namespace"})

        sessions_path = remote.work_root / ".intern_sessions.json"
        removed_sessions: list[str] = []
        if sessions_path.is_file():
            sessions = remote.ctx.action.session.registry_remote()
            for key, entry in list(sessions.items()):
                if not isinstance(entry, dict):
                    continue
                if entry.get("intern_name") == intern and {entry.get("project"), entry.get("workspace_id")} & {project, workspace_id}:
                    sessions.pop(key, None)
                    removed_sessions.append(str(key))
            sessions_path.write_text(json.dumps(sessions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"intern": intern, "removed": removed, "removed_sessions": removed_sessions, "skipped_cleanup": skipped}

    def metadata_resolve_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        return remote.json_cmd(
            f"metadata resolve {intern}",
            [
                *remote.internctl,
                "metadata",
                "resolve",
                "--workspace",
                str(workspace["workspace_id"]),
                "--intern",
                intern,
                "--json",
            ],
            timeout=120,
        )

    def metadata_resolve_checked_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        resolver = self.metadata_resolve_remote(workspace, intern)
        if resolver.get("ok") is not True:
            raise NativeCaseError(f"metadata resolver failed for {intern}: {resolver}")
        return resolver

    def runtime_dir_remote(self, workspace: dict[str, Any], intern: str) -> Path:
        remote = self._remote()
        return remote.runtime_dir(workspace, intern)

    def status_json_remote(self, workspace: dict[str, Any], intern: str, *, check: bool = True) -> dict[str, Any]:
        remote = self._remote()
        result = remote.run_cmd(
            f"status json {intern}",
            [*remote.internctl, "status", intern, "--project", str(workspace["display"]), "--json"],
            timeout=120,
            check=check,
        )
        if result.returncode != 0 and not check:
            return {"ok": False, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr}
        payload = parse_json_output(f"status json {intern}", result.stdout)
        if not isinstance(payload, dict):
            raise NativeCaseError(f"status json {intern} is not an object")
        return payload

    def list_json_remote(self) -> list[dict[str, Any]]:
        remote = self._remote()
        result = remote.run_cmd("intern list json", [*remote.internctl, "list", "--json"], timeout=120)
        payload = parse_json_output("intern list json", result.stdout)
        if not isinstance(payload, list):
            raise NativeCaseError("intern list json is not an array")
        return [item for item in payload if isinstance(item, dict)]

    def list_item_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        items = self.list_json_remote()
        matches = [
            item for item in items
            if item.get("name") == intern
            and item.get("workspace_id") == workspace["workspace_id"]
            and item.get("project") == workspace["display"]
        ]
        detail = {"matches": matches, "items": items}
        item = dict(matches[0]) if len(matches) == 1 else {}
        return {
            **item,
            "item": item,
            "checks": [
                {"name": "intern_list_single_match_" + intern, "ok": len(matches) == 1, "detail": detail},
            ],
        }

    @staticmethod
    def _relay_absent_check(intern: str, relay: dict[str, Any]) -> dict[str, Any]:
        entry = relay.get("entry") if isinstance(relay.get("entry"), dict) else {}
        lookup = relay.get("lookup") if isinstance(relay.get("lookup"), dict) else {}
        return {
            "name": "relay_registry_absent_" + re.sub(r"[^A-Za-z0-9_]+", "_", intern),
            "ok": not entry and not lookup.get("chat_id"),
            "detail": {"entry": entry, "lookup": lookup},
        }

    def no_artifacts_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        runtime = self.runtime_dir_remote(workspace, intern)
        sessions = self.ctx.action.session.registry_entries_for_remote(workspace, intern)
        relay = self.ctx.action.feishu.relay_registry_absent_evidence_remote(workspace, intern)
        result = intern_assertions.no_intern_artifacts_checks(
            intern,
            runtime=str(runtime),
            runtime_exists=runtime.exists(),
            session_entries=sessions,
            relay=relay,
        )
        return {
            **result["detail"],
            "checks": [
                self._relay_absent_check(intern, relay),
                *result["checks"],
            ],
        }

    def removed_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        metadata: dict[str, Any],
        task_dir: Path | None = None,
        tmux_session: str = "",
        expect_tmux_absent: bool = False,
    ) -> dict[str, Any]:
        remote = self._remote()
        status_path = Path(str(metadata.get("status_path") or ""))
        knowledge_path = Path(str(metadata.get("knowledge_path") or ""))
        runtime = self.runtime_dir_remote(workspace, intern)
        sessions = self.ctx.action.session.registry_entries_for_remote(workspace, intern)
        relay = self.ctx.action.feishu.relay_registry_absent_evidence_remote(workspace, intern)
        tmux_detail: dict[str, Any] = {}
        tmux_absent_ok = True
        if tmux_session:
            tmux = remote.run_cmd(
                f"tmux absent after delete {intern}",
                ["tmux", "has-session", "-t", f"={tmux_session}"],
                timeout=30,
                check=False,
            )
            tmux_detail = {"tmux_session": tmux_session, "returncode": tmux.returncode, "stdout": tmux.stdout, "stderr": tmux.stderr}
            tmux_absent_ok = tmux.returncode != 0
        result = intern_assertions.intern_removed_checks(
            intern,
            status_path=str(status_path),
            status_exists=status_path.exists(),
            knowledge_path=str(knowledge_path),
            knowledge_exists=knowledge_path.exists(),
            runtime=str(runtime),
            runtime_exists=runtime.exists(),
            task_dir=str(task_dir) if task_dir else "",
            task_exists=bool(task_dir and task_dir.exists()),
            session_entries=sessions,
            relay=relay,
            tmux=tmux_detail,
            expect_tmux_absent=expect_tmux_absent,
            tmux_absent_ok=tmux_absent_ok,
        )
        return {
            **result["detail"],
            "checks": [
                self._relay_absent_check(intern, relay),
                *result["checks"],
            ],
        }

    def metadata_status_consistent_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        expected_status: str,
        expected_type: str = "codex",
    ) -> dict[str, Any]:
        metadata = self.metadata_resolve_remote(workspace, intern)
        status_path = Path(str(metadata.get("status_path") or ""))
        status_meta = self._parse_status_metadata(status_path)
        status = self.status_json_remote(workspace, intern)
        session_entries = self.ctx.action.session.registry_entries_for_remote(workspace, intern)
        result = intern_assertions.metadata_status_consistent_checks(
            workspace,
            intern,
            metadata=metadata,
            status_path=str(status_path),
            status_path_exists=status_path.is_file(),
            status_meta=status_meta,
            status_json=status,
            session_entries=session_entries,
            expected_status=expected_status,
            expected_type=expected_type,
        )
        return {
            **result["returned"],
            "checks": result["checks"],
            "detail": result["detail"],
        }

    def tree_projection_contains_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        expected_status: str,
        expected_type: str = "codex",
    ) -> dict[str, Any]:
        listed = self.list_item_remote(workspace, intern)
        item = dict(listed.get("item") if isinstance(listed.get("item"), dict) else {})
        result = intern_assertions.tree_projection_contains_checks(
            workspace,
            intern,
            item,
            expected_status=expected_status,
            expected_type=expected_type,
        )
        return {
            **item,
            "item": item,
            "checks": [
                *listed.get("checks", []),
                *result["checks"],
            ],
        }

    def no_team_or_non_codex_fixture_remote(self) -> dict[str, Any]:
        remote = self._remote()
        intern_prefix = remote.intern_name_prefix()
        workspace_prefix = remote.workspace_name_prefix()
        list_items = [
            item for item in self.list_json_remote()
            if str(item.get("name") or "").startswith(intern_prefix)
            or str(item.get("project") or "").startswith(workspace_prefix)
        ]
        session_items = {
            key: entry for key, entry in remote.ctx.action.session.registry_remote().items()
            if isinstance(entry, dict)
            and (
                str(entry.get("intern_name") or "").startswith(intern_prefix)
                or str(entry.get("project") or "").startswith(workspace_prefix)
            )
        }
        result = intern_assertions.no_team_or_non_codex_fixture_checks(
            remote.case_no,
            list_items=list_items,
            session_items=session_items,
        )
        return {**result["detail"], "checks": result["checks"]}
