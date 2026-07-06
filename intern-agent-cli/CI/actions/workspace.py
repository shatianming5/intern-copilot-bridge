from __future__ import annotations

from dataclasses import dataclass
import os
import re
import json
from pathlib import Path
import shutil
from typing import Any

from CI.helpers import deployment_primitives as full_primitives
from CI.assertions.core import native_require_check
from CI.helpers.native_error import NativeCaseError
from CI.helpers.product_cli_helper import parse_json_output, tail

DEFAULT_GITHUB_NONPROTECTED_REPO = getattr(
    full_primitives,
    "DEFAULT_GITHUB_NONPROTECTED_REPO",
    "git@github.com:chlxydl/intern_debug_repo.git",
)


@dataclass
class WorkspaceActions:
    ctx: Any

    def _remote(self) -> Any:
        remote = getattr(self.ctx, "remote_context", None)
        if remote is None:
            raise RuntimeError("ctx.action.workspace.* requires RemoteCaseContext")
        return remote

    def create_enable_remote(
        self,
        *,
        display: str,
        provider: str,
        repo_url: str,
        mode: str,
        local_path: str = "",
        metadata_branch: str = "intern_workspace",
    ) -> dict[str, Any]:
        remote = self._remote()
        created_args = [
            *remote.internctl,
            "workspace",
            "create",
            "--repo-url",
            repo_url,
            "--display-name",
            display,
            "--provider",
            provider,
            "--mode",
            mode,
        ]
        if mode == "metadata_branch":
            created_args.extend(["--metadata-branch", metadata_branch])
        created_args.append("--json")
        created = remote.json_cmd(f"workspace create {display}", created_args, timeout=180)
        workspace_id = str(created.get("workspace_id") or (created.get("workspace") or {}).get("workspace_id") or "")
        enabled: dict[str, Any] = {}
        if workspace_id:
            enable_args = [*remote.internctl, "workspace", "enable", workspace_id, "--json"]
            if local_path:
                enable_args.extend(["--local-path", local_path])
            enabled = remote.json_cmd(f"workspace enable {display}", enable_args, timeout=180)
        workspace = {
            "display": display,
            "workspace_id": workspace_id,
            "provider": provider,
            "repo_url": repo_url,
            "mode": mode,
            "metadata_branch": metadata_branch if mode == "metadata_branch" else "",
            "local_path": enabled.get("local_path") or local_path,
            "create": created,
            "enable": enabled,
        }
        if workspace_id:
            remote.created["workspaces"].append(workspace_id)
        return workspace

    def create_case_remote(
        self,
        *,
        suffix: str = "",
        display_name: str = "",
        provider: str,
        repo_url: str,
        mode: str,
        local_path: str = "",
        metadata_branch: str = "intern_workspace",
    ) -> dict[str, Any]:
        remote = self._remote()
        display = display_name or remote.workspace_name(suffix)
        workspace = self.create_enable_remote(
            display=display,
            provider=provider,
            repo_url=repo_url,
            mode=mode,
            local_path=local_path,
            metadata_branch=metadata_branch,
        )
        workspace_id = str(workspace.get("workspace_id") or "")
        if not workspace_id:
            raise NativeCaseError(f"workspace create returned no workspace_id for {display}: {workspace.get('create')}")
        remote.artifacts[f"workspace_{suffix}"] = workspace
        return workspace

    def local_repo_fixture_remote(self, suffix: str) -> Path:
        remote = self._remote()
        repo = remote.artifact_dir / f"repo_{suffix}"
        if repo.exists():
            shutil.rmtree(repo)
        repo.mkdir(parents=True, exist_ok=True)
        remote.run_cmd(f"git init {suffix}", ["git", "init", str(repo)], timeout=60)
        remote.run_cmd(f"git config user.name {suffix}", ["git", "-C", str(repo), "config", "user.name", "intern-ci"], timeout=30)
        remote.run_cmd(
            f"git config user.email {suffix}",
            ["git", "-C", str(repo), "config", "user.email", "intern-ci@example.invalid"],
            timeout=30,
        )
        (repo / "README.md").write_text(f"# {remote.case_id} {suffix}\n", encoding="utf-8")
        remote.run_cmd(f"git add {suffix}", ["git", "-C", str(repo), "add", "README.md"], timeout=30)
        remote.run_cmd(f"git commit {suffix}", ["git", "-C", str(repo), "commit", "-m", "init"], timeout=60)
        remote.created["repos"].append(str(repo))
        return repo

    def nonprotected_repo_remote(self) -> str:
        remote = self._remote()
        repo = str(getattr(remote.args, "nonprotected_repo", "") or "")
        if not repo:
            raise NativeCaseError("environment_missing: --nonprotected-repo is required for " + remote.case_id)
        return repo

    def github_nonprotected_repo_detail_remote(self) -> dict[str, Any]:
        remote = self._remote()
        env = getattr(remote, "env", None) or os.environ
        for key in ("INTERN_CI_GITHUB_NONPROTECTED_REPO", "ENTERPRISE_CI_GITHUB_TEST_REPO"):
            value = env.get(key)
            if value:
                return {
                    "github_repo": value,
                    "repo_source": key,
                    "used_default": False,
                    "default_repo": DEFAULT_GITHUB_NONPROTECTED_REPO,
                }
        return {
            "github_repo": DEFAULT_GITHUB_NONPROTECTED_REPO,
            "repo_source": "DEFAULT_GITHUB_NONPROTECTED_REPO",
            "used_default": True,
            "default_repo": DEFAULT_GITHUB_NONPROTECTED_REPO,
        }

    def create_args_remote(
        self,
        *,
        provider: str,
        repo_url: str,
        mode: str,
        display_name: str,
        metadata_branch: str = "",
    ) -> list[str]:
        remote = self._remote()
        args = [
            *remote.internctl,
            "workspace",
            "create",
            "--repo-url",
            repo_url,
            "--display-name",
            display_name,
            "--provider",
            provider,
            "--mode",
            mode,
            "--json",
        ]
        if metadata_branch:
            args.extend(["--metadata-branch", metadata_branch])
        return args

    def attempt_create_remote(
        self,
        *,
        provider: str,
        repo_url: str,
        mode: str,
        display_name: str,
        local_path: str = "",
        metadata_branch: str = "",
    ) -> dict[str, Any]:
        remote = self._remote()
        create = remote.run_cmd(
            "attempt workspace create " + display_name,
            self.create_args_remote(
                provider=provider,
                repo_url=repo_url,
                mode=mode,
                display_name=display_name,
                metadata_branch=metadata_branch,
            ),
            timeout=180,
            check=False,
        )
        result: dict[str, Any] = {
            "display_name": display_name,
            "provider": provider,
            "mode": mode,
            "repo_url": repo_url,
            "metadata_branch": metadata_branch,
            "create": {
                "returncode": create.returncode,
                "stdout": tail(create.stdout, 2000),
                "stderr": tail(create.stderr, 2000),
            },
            "failed_at": "create" if create.returncode != 0 else "",
        }
        if create.returncode != 0:
            from CI.assertions import workspace as workspace_assertions

            result["reason_kind"] = workspace_assertions.classify_workspace_attempt_failure(create.stdout + "\n" + create.stderr)
            return result
        payload = parse_json_output("attempt workspace create " + display_name, create.stdout)
        workspace_id = str(payload.get("workspace_id") or (payload.get("workspace") or {}).get("workspace_id") or "")
        result["workspace_id"] = workspace_id
        enable_args = [*remote.internctl, "workspace", "enable", workspace_id, "--json"]
        if local_path:
            enable_args.extend(["--local-path", local_path])
        enable = remote.run_cmd("attempt workspace enable " + display_name, enable_args, timeout=180, check=False)
        result["enable"] = {
            "returncode": enable.returncode,
            "stdout": tail(enable.stdout, 2000),
            "stderr": tail(enable.stderr, 2000),
        }
        if enable.returncode != 0:
            from CI.assertions import workspace as workspace_assertions

            result["failed_at"] = "enable"
            result["reason_kind"] = workspace_assertions.classify_workspace_attempt_failure(enable.stdout + "\n" + enable.stderr)
        else:
            result["failed_at"] = ""
            result["reason_kind"] = ""
        return result

    def git_default_branch_remote(self, repo_url: str, *, name: str) -> dict[str, Any]:
        remote = self._remote()
        result = remote.run_cmd(name, ["git", "ls-remote", "--symref", repo_url, "HEAD"], timeout=180, check=False)
        branch = ""
        for line in result.stdout.splitlines():
            match = re.match(r"ref:\s+refs/heads/([^\s]+)\s+HEAD", line)
            if match:
                branch = match.group(1)
                break
        detail = {
            "repo_url": repo_url,
            "default_branch": branch,
            "returncode": result.returncode,
            "stdout": tail(result.stdout, 1000),
            "stderr": tail(result.stderr, 1000),
        }
        if result.returncode != 0 or not result.stdout.strip():
            raise NativeCaseError(f"{name} failed to resolve default branch: {detail}")
        return detail

    def git_remote_head_remote(self, repo_url: str, branch: str, *, name: str) -> dict[str, Any]:
        remote = self._remote()
        ref = f"refs/heads/{branch}"
        result = remote.run_cmd(name, ["git", "ls-remote", repo_url, ref], timeout=180, check=False)
        revision = ""
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] == ref:
                revision = parts[0]
                break
        detail = {
            "repo_url": repo_url,
            "branch": branch,
            "revision": revision,
            "returncode": result.returncode,
            "stdout": tail(result.stdout, 1200),
            "stderr": tail(result.stderr, 1200),
        }
        if result.returncode != 0 or not revision:
            raise NativeCaseError(f"{name} failed to resolve branch revision: {detail}")
        return detail

    def git_default_head_remote(self, repo_url: str, *, name: str) -> dict[str, Any]:
        default = self.git_default_branch_remote(repo_url, name=name + " default branch")
        branch = str(default.get("default_branch") or "master")
        head = self.git_remote_head_remote(repo_url, branch, name=name + " default head")
        return {"default": default, **head}

    def list_remote(self, name: str = "workspace list") -> dict[str, Any]:
        remote = self._remote()
        return remote.json_cmd(name, [*remote.internctl, "workspace", "list", "--json"], timeout=120)

    def doctor_remote(self, workspace: dict[str, Any], name: str = "workspace doctor") -> dict[str, Any]:
        remote = self._remote()
        return remote.json_cmd(
            name,
            [*remote.internctl, "workspace", "doctor", str(workspace["workspace_id"]), "--json"],
            timeout=180,
        )

    def delete_remote(self, workspace: dict[str, Any]) -> dict[str, Any]:
        remote = self._remote()
        deleted = remote.json_cmd(
            f"workspace delete {workspace['workspace_id']}",
            [*remote.internctl, "workspace", "delete", str(workspace["workspace_id"]), "--confirm", "--json"],
            timeout=120,
        )
        remote.artifacts["workspace_delete"] = deleted
        return deleted

    @staticmethod
    def _display(record: dict[str, Any]) -> str:
        from CI.assertions import workspace as workspace_assertions

        return workspace_assertions.workspace_display(record)

    @staticmethod
    def _record_matches_prefix(record: dict[str, Any], prefix: str) -> bool:
        from CI.assertions import workspace as workspace_assertions

        return workspace_assertions.workspace_record_matches_prefix(record, prefix)

    def records_remote(self, *, source: str = "local", include_deleted: bool = False) -> list[dict[str, Any]]:
        remote = self._remote()
        if source == "relay":
            payload = remote.relay_json("relay workspace list", "GET", "/api/workspaces", timeout=60)
        else:
            payload = self.list_remote("daemon workspace list")
        records = payload.get("workspaces") if isinstance(payload, dict) else []
        result = [item for item in records or [] if isinstance(item, dict)]
        if include_deleted:
            return result
        return [item for item in result if not item.get("deleted")]

    def find_record_remote(self, display_name: str, *, source: str = "local") -> dict[str, Any] | None:
        for item in self.records_remote(source=source):
            if self._display(item) == display_name:
                return item
        return None

    def record_checks_remote(
        self,
        display_name: str,
        *,
        provider: str,
        mode: str,
        repo_url: str = "",
        repo_path: str = "",
    ) -> dict[str, Any]:
        from CI.assertions import workspace as workspace_assertions

        return workspace_assertions.workspace_record_checks(
            display_name,
            local=self.find_record_remote(display_name, source="local"),
            relay=self.find_record_remote(display_name, source="relay"),
            provider=provider,
            mode=mode,
            repo_url=repo_url,
            repo_path=repo_path,
        )

    def relay_sync_checks_remote(self, display_name: str, *, provider: str) -> dict[str, Any]:
        from CI.assertions import workspace as workspace_assertions

        return workspace_assertions.relay_workspace_sync_checks(
            display_name,
            local=self.find_record_remote(display_name, source="local"),
            relay=self.find_record_remote(display_name, source="relay"),
            provider=provider,
        )

    def absent_checks_remote(self, display_name: str) -> dict[str, Any]:
        from CI.assertions import workspace as workspace_assertions

        return workspace_assertions.workspace_absent_check(
            display_name,
            local=self.find_record_remote(display_name, source="local"),
            relay=self.find_record_remote(display_name, source="relay"),
        )

    def no_extra_records_checks_remote(self, prefix: str, *, allowed_displays: set[str]) -> dict[str, Any]:
        from CI.assertions import workspace as workspace_assertions

        records = workspace_assertions.workspace_prefix_records(
            {
                "local": self.records_remote(source="local"),
                "relay": self.records_remote(source="relay"),
            },
            prefix,
        )
        return workspace_assertions.no_extra_workspace_records_check(prefix, records, allowed_displays=allowed_displays)

    def metadata_root_checks_remote(self, workspace: dict[str, Any], mode: str, *, provider: str = "") -> dict[str, Any]:
        from CI.assertions import workspace as workspace_assertions

        remote = self._remote()
        intern = remote.stage_intern_name("metadata_probe")
        resolver = remote.ctx.action.intern.metadata_resolve_remote(workspace, intern)
        if resolver.get("ok") is not True:
            raise NativeCaseError(f"metadata resolver failed for {intern}: {resolver}")
        try:
            return workspace_assertions.workspace_metadata_root_checks(workspace, resolver, mode, provider=provider)
        except ValueError as exc:
            raise NativeCaseError(str(exc)) from exc

    def metadata_branch_created_checks_remote(self, workspace: dict[str, Any], *, provider: str) -> dict[str, Any]:
        from CI.assertions import workspace as workspace_assertions

        remote = self._remote()
        intern = remote.stage_intern_name("metadata_probe")
        resolver = remote.ctx.action.intern.metadata_resolve_remote(workspace, intern)
        if resolver.get("ok") is not True:
            raise NativeCaseError(f"metadata resolver failed for {intern}: {resolver}")
        branch = str(workspace.get("metadata_branch") or "intern_workspace")
        result = remote.run_cmd(
            "metadata branch ls-remote " + branch,
            ["git", "ls-remote", str(workspace.get("repo_url") or ""), f"refs/heads/{branch}"],
            timeout=180,
            check=False,
        )
        ls_remote = {"returncode": result.returncode, "stdout": tail(result.stdout, 1200), "stderr": tail(result.stderr, 1200)}
        return workspace_assertions.metadata_branch_created_checks(
            workspace,
            resolver,
            ls_remote,
            provider=provider,
        )

    def business_branch_unchanged_checks_remote(self, baseline: dict[str, Any], *, label: str) -> dict[str, Any]:
        from CI.assertions import workspace as workspace_assertions

        current = self.git_remote_head_remote(str(baseline["repo_url"]), str(baseline["branch"]), name=label)
        return workspace_assertions.business_branch_unchanged_check(baseline, current, label=label)

    def entry_remote(self, workspace: dict[str, Any], *, name: str = "workspace list") -> dict[str, Any] | None:
        listed = self.list_remote(name)
        for item in listed.get("workspaces") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("workspace_id") or "") == str(workspace["workspace_id"]):
                return item
            if self._display(item) == str(workspace["display"]):
                return item
        return None

    def metadata_root_remote(self, workspace: dict[str, Any], *, name: str = "metadata root workspace entry") -> Path:
        mode = str(workspace.get("metadata_mode") or workspace.get("mode") or "").strip()
        local_path = Path(str(workspace.get("local_path") or ""))
        metadata_cache = Path(str(workspace.get("metadata_cache_path") or ""))
        if not mode or (mode == "repo_dotdir" and not local_path.is_absolute()) or (mode in {"metadata_branch", "local_only"} and not metadata_cache.is_absolute()):
            entry = self.entry_remote(workspace, name=name) or {}
            mode = str(entry.get("metadata_mode") or mode).strip()
            local_path = Path(str(entry.get("local_path") or local_path or ""))
            metadata_cache = Path(str(entry.get("metadata_cache_path") or metadata_cache or ""))
        if mode == "repo_dotdir":
            if not local_path.is_absolute():
                raise NativeCaseError(f"workspace local_path missing: {workspace}")
            root = local_path / ".intern_workspace"
        elif mode == "metadata_branch":
            if not metadata_cache.is_absolute():
                raise NativeCaseError(f"workspace metadata_cache_path missing: {workspace}")
            root = metadata_cache / ".intern_workspace"
        elif mode == "local_only":
            if not metadata_cache.is_absolute():
                raise NativeCaseError(f"workspace metadata_cache_path missing: {workspace}")
            root = metadata_cache / "local" / ".intern_workspace"
        else:
            raise NativeCaseError(f"workspace metadata mode missing: {workspace}")
        root.mkdir(parents=True, exist_ok=True)
        return root

    def prefix_records_remote(self, prefix: str) -> dict[str, list[dict[str, Any]]]:
        return {
            "local": self.records_remote(source="local"),
            "relay": self.records_remote(source="relay"),
        } | {
            "matching_local": [item for item in self.records_remote(source="local") if self._record_matches_prefix(item, prefix)],
            "matching_relay": [item for item in self.records_remote(source="relay") if self._record_matches_prefix(item, prefix)],
        }

    def wait_registered_remote(self, display_name: str, *, timeout: int = 180, require_relay: bool = True) -> dict[str, Any]:
        import time

        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            local = self.find_record_remote(display_name, source="local")
            relay = self.find_record_remote(display_name, source="relay")
            last = {"local": local or {}, "relay": relay or {}}
            if local and (relay or not require_relay):
                return last | {"registered": True}
            time.sleep(3)
        raise NativeCaseError(f"workspace registration timed out for {display_name}: {last}")

    def wait_removed_remote(self, display_name: str, *, timeout: int = 180) -> dict[str, Any]:
        import time

        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            local = self.find_record_remote(display_name, source="local")
            relay = self.find_record_remote(display_name, source="relay")
            last = {"local": local or {}, "relay": relay or {}}
            if not local and not relay:
                return last | {"removed": True}
            time.sleep(3)
        raise NativeCaseError(f"workspace removal timed out for {display_name}: {last}")

    def wait_mode_remote(self, display_name: str, mode: str, *, timeout: int = 180) -> dict[str, Any]:
        import time

        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            local = self.find_record_remote(display_name, source="local")
            relay = self.find_record_remote(display_name, source="relay")
            last = {"local": local or {}, "relay": relay or {}}
            if local and relay and local.get("metadata_mode") == mode and relay.get("metadata_mode") == mode:
                return last | {"mode": mode}
            time.sleep(3)
        raise NativeCaseError(f"workspace mode {mode} timed out for {display_name}: {last}")

    def reset_stage_namespace_remote(self) -> dict[str, Any]:
        remote = self._remote()
        return self.case_initial_reset_remote(remote.stage_workspace_prefix(), remote.intern_name_prefix())

    def case_initial_reset_evidence_remote(self) -> dict[str, Any]:
        remote = self._remote()
        reset = remote.artifacts.get("case_initial_reset")
        if not isinstance(reset, dict):
            reset = self.case_initial_reset_remote(remote.workspace_name_prefix(), remote.intern_name_prefix())
        return {
            "case_initial_reset": reset,
            "checks": [
                native_require_check("case_initial_reset_ok", reset.get("ok") is True, reset),
            ],
        }

    def case_initial_reset_remote(self, workspace_prefix: str, intern_prefix: str) -> dict[str, Any]:
        remote = self._remote()
        reset: dict[str, Any] = {
            "schema": "intern-agents.ci.case-initial-reset.v1",
            "case_id": remote.case_id,
            "resource_namespace": remote.resource_namespace,
            "workspace_prefix": workspace_prefix,
            "intern_prefix": intern_prefix,
            "ok": True,
            "errors": [],
            "groups": [],
            "interns": [],
            "workspaces": [],
            "sessions": [],
            "registry_files": [],
        }

        if remote.artifact_dir.exists():
            shutil.rmtree(remote.artifact_dir, ignore_errors=True)
        remote.artifact_dir.mkdir(parents=True, exist_ok=True)

        sessions = remote.session_registry()
        session_targets: dict[tuple[str, str], dict[str, str]] = {}
        for key, entry in sessions.items():
            if not isinstance(entry, dict):
                continue
            intern = str(entry.get("intern_name") or key.rsplit(":", 1)[-1] or "")
            project = str(entry.get("project") or "")
            workspace_id = str(entry.get("workspace_id") or "")
            if intern.startswith(intern_prefix) or project.startswith(workspace_prefix):
                session_targets[(project, intern)] = {
                    "project": project,
                    "workspace_id": workspace_id,
                    "intern": intern,
                    "session_key": str(key),
                }

        relay_targets = self._case_reset_relay_targets(workspace_prefix, intern_prefix)
        for target in relay_targets:
            key = (str(target.get("project") or ""), str(target.get("intern") or ""))
            if key[1]:
                session_targets.setdefault(key, {
                    "project": key[0],
                    "workspace_id": "",
                    "intern": key[1],
                    "source": "relay_registry",
                })

        for target in sorted(session_targets.values(), key=lambda item: (item.get("project", ""), item.get("intern", ""))):
            project = str(target.get("project") or "")
            intern = str(target.get("intern") or "")
            if not intern:
                continue
            if project:
                try:
                    group = remote.relay_json(
                        f"case reset group delete {project}:{intern}",
                        "POST",
                        "/api/chat/delete",
                        {"intern_name": intern, "project": project},
                        timeout=60,
                    )
                    reset["groups"].append({"project": project, "intern": intern, "result": group})
                except Exception as exc:  # noqa: BLE001
                    reset["errors"].append(f"group delete {project}:{intern}: {exc}")
            else:
                reset["groups"].append({"project": project, "intern": intern, "skipped": "missing_project"})
            remote.ctx.action.session.stop_remote(intern)
            reset["sessions"].append({"intern": intern, "project": project, "action": "session_stop"})
            if project:
                deleted = remote.run_cmd(
                    f"case reset intern delete {project}:{intern}",
                    [*remote.internctl, "delete", intern, "--project", project, "--confirm", "--force"],
                    timeout=180,
                    check=False,
                )
                reset["interns"].append({
                    "project": project,
                    "intern": intern,
                    "returncode": deleted.returncode,
                    "ok": deleted.returncode == 0,
                })

        workspaces = self._case_reset_workspace_targets(workspace_prefix)
        for workspace in sorted(workspaces, key=lambda item: str(item.get("display_name") or item.get("workspace_id") or "")):
            workspace_id = str(workspace.get("workspace_id") or "")
            display = str(workspace.get("display_name") or workspace.get("display") or workspace.get("name") or "")
            if not workspace_id:
                continue
            deleted = remote.run_cmd(
                f"case reset workspace delete {workspace_id}",
                [*remote.internctl, "workspace", "delete", workspace_id, "--confirm", "--json"],
                timeout=180,
                check=False,
            )
            reset["workspaces"].append({
                "workspace_id": workspace_id,
                "display_name": display,
                "returncode": deleted.returncode,
                "ok": deleted.returncode == 0,
            })
            state_dir = (remote.work_root / "state" / "v1" / workspace_id).resolve(strict=False)
            state_root = (remote.work_root / "state" / "v1").resolve(strict=False)
            try:
                state_dir.relative_to(state_root)
            except ValueError:
                continue
            if state_dir.is_dir() and workspace_id and state_dir != state_root:
                shutil.rmtree(state_dir, ignore_errors=True)

        reset["registry_files"] = self._case_reset_local_registry_files(workspace_prefix, intern_prefix)
        self._case_reset_session_registry(workspace_prefix, intern_prefix, reset)
        if reset["errors"]:
            reset["ok"] = False
            reset["failure_reason"] = "; ".join(reset["errors"])[:1200]
        remote.artifacts["case_initial_reset"] = reset
        if not reset["ok"]:
            raise NativeCaseError("case initial reset failed: " + reset["failure_reason"])
        return reset

    def _case_reset_workspace_targets(self, workspace_prefix: str) -> list[dict[str, Any]]:
        remote = self._remote()
        result = remote.run_cmd(
            "case reset workspace list",
            [*remote.internctl, "workspace", "list", "--json"],
            timeout=120,
            check=False,
        )
        if result.returncode != 0:
            raise NativeCaseError(f"case reset workspace list failed: {tail(result.stderr or result.stdout, 1200)}")
        payload = parse_json_output("case reset workspace list", result.stdout)
        workspaces = payload.get("workspaces") if isinstance(payload, dict) else []
        targets: list[dict[str, Any]] = []
        for item in workspaces or []:
            if not isinstance(item, dict):
                continue
            display = str(item.get("display_name") or item.get("display") or item.get("name") or "")
            workspace_id = str(item.get("workspace_id") or "")
            if display.startswith(workspace_prefix) or workspace_id.startswith("ws_" + workspace_prefix):
                targets.append(item)
        return targets

    def _case_reset_relay_targets(self, workspace_prefix: str, intern_prefix: str) -> list[dict[str, str]]:
        remote = self._remote()
        try:
            registry = remote.relay_json("case reset relay registry", "GET", "/api/registry", timeout=60)
        except Exception as exc:  # noqa: BLE001
            raise NativeCaseError(f"case reset relay registry unavailable: {exc}") from exc
        targets: list[dict[str, str]] = []
        items = registry.items() if isinstance(registry, dict) else []
        for key, entry in items:
            key_text = str(key)
            key_project = key_text.split(":", 1)[0] if ":" in key_text else ""
            key_intern = key_text.rsplit(":", 1)[-1]
            if isinstance(entry, dict):
                intern = str(entry.get("name") or entry.get("intern_name") or key_intern or "")
                project = str(entry.get("project") or key_project or "")
            elif isinstance(entry, str):
                intern = key_intern
                project = key_project
            else:
                continue
            if intern.startswith(intern_prefix) or project.startswith(workspace_prefix):
                targets.append({"intern": intern, "project": project})
        return targets

    def _case_reset_local_registry_files(self, workspace_prefix: str, intern_prefix: str) -> list[dict[str, str]]:
        remote = self._remote()
        registry_dir = remote.work_root / ".feishu_registry"
        removed: list[dict[str, str]] = []
        if not registry_dir.is_dir():
            return removed
        for path in registry_dir.glob("*.json"):
            if path.name == "workspace_registry.json":
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
            intern = str(data.get("intern_name") or data.get("name") or path.stem.rsplit("__", 1)[-1] or "")
            project = str(data.get("project") or data.get("workspace") or "")
            if intern.startswith(intern_prefix) or project.startswith(workspace_prefix) or path.stem.startswith(intern_prefix):
                path.unlink(missing_ok=True)
                removed.append({"path": str(path), "intern": intern, "project": project})
        return removed

    def _case_reset_session_registry(self, workspace_prefix: str, intern_prefix: str, reset: dict[str, Any]) -> None:
        remote = self._remote()
        sessions_path = remote.work_root / ".intern_sessions.json"
        if not sessions_path.is_file():
            return
        sessions = remote.session_registry()
        removed = []
        for key, entry in list(sessions.items()):
            if not isinstance(entry, dict):
                continue
            intern = str(entry.get("intern_name") or key.rsplit(":", 1)[-1] or "")
            project = str(entry.get("project") or "")
            if intern.startswith(intern_prefix) or project.startswith(workspace_prefix):
                sessions.pop(key, None)
                removed.append(str(key))
        sessions_path.write_text(json.dumps(sessions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        reset["session_registry_removed"] = removed
