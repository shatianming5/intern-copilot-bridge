from pathlib import Path
import re
from typing import Any
from CI.helpers.product_cli_helper import tail
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0008.s01_reset_case_namespace",
    "F_0008.s02_create_workspace_and_baseline_intern",
    "F_0008.s03_duplicate_create_rejected_single_record",
    "F_0008.s04_invalid_name_rollback",
    "F_0008.s05_unsupported_backend_rollback",
    "F_0008.s06_missing_workspace_rollback",
)


CASE = CaseDefinition(
    id="F_0008_intern_duplicate_invalid_create_rollback",
    name="F_0008_intern_duplicate_invalid_create_rollback",
    description=(
        "Validates intern create rollback for same-project duplicate names, invalid names, unsupported backends, "
        "and missing workspaces without leaving metadata, runtime, session, or chat registry residue."
    ),
    stage="remote",
    timeout_seconds=1500,
    kind="f_intern_session_remote",
    tags=("F", "intern", "create", "rollback", "cli", "daemon", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "intern.create_error_rollback_remote",
            "create_intern",
            "daemon.chat_lookup_empty",
            "relay.read_chat_presence",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "ctx.action_ok",
            "native.intern_create_rollback",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0008_worker", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0008", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0008_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0008",
            "workspace:ci_f_0008_workspace",
            "intern:intern_ci_f_0008_worker",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy",
        "notes": (
            "Baseline intern is retained for inspection; failing attempts must not create extra intern, session, or chat records.",
            "Case initialization only cleans this case namespace.",
        ),
    },
)


def run_f_intern_duplicate_invalid_create_rollback(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def attempt_create(
        *,
        project: str,
        intern: str,
        backend: str,
        repo_url: str = "",
        skip_group: bool = True,
    ) -> dict[str, Any]:
        cmd = [*self.internctl, "create", intern, "--project", project, "--type", backend]
        if repo_url:
            cmd.extend(["--repo-url", repo_url])
        if skip_group:
            cmd.extend(["--skip-feishu-group", "--skip-status-notify"])
        result = self.run_cmd(f"attempt create {intern}", cmd, timeout=180, check=False)
        return {
            "intern": intern,
            "project": project,
            "backend": backend,
            "returncode": result.returncode,
            "stdout": tail(result.stdout, 2000),
            "stderr": tail(result.stderr, 2000),
        }

    def no_session_entries_anywhere(intern: str) -> dict[str, Any]:
        entries = {
            str(key): entry for key, entry in self.ctx.action.session.registry_remote().items()
            if isinstance(entry, dict) and entry.get("intern_name") == intern
        }
        self.require("no_session_entries_anywhere_" + re.sub(r"[^A-Za-z0-9_]+", "_", intern), not entries, {"entries": entries})
        return entries

    def no_relay_entry_by_name(intern: str) -> dict[str, Any]:
        registry = self.relay_json(f"relay registry absent by name {intern}", "GET", "/api/registry", timeout=60)
        matches = [
            value for key, value in registry.items()
            if key == intern or (isinstance(value, dict) and value.get("name") == intern)
        ] if isinstance(registry, dict) else []
        self.require("no_relay_entries_anywhere_" + re.sub(r"[^A-Za-z0-9_]+", "_", intern), not matches, {"matches": matches})
        return {"matches": matches}

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace_and_baseline_intern() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        baseline = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "worker", intern_type="codex", repo_url=str(repo)))
        state.update({"repo": repo, "workspace": workspace, "baseline": baseline})
        return {"repo": str(repo), "workspace": workspace, "baseline": baseline}

    def s03_duplicate_create_rejected_single_record() -> dict[str, Any]:
        workspace = state["workspace"]
        repo = state["repo"]
        baseline = state["baseline"]
        sessions_before = self.ctx.action.session.registry_remote()
        duplicate = attempt_create(
            project=str(workspace["display"]),
            intern=baseline["intern"],
            backend="codex",
            repo_url=str(repo),
        )
        combined = duplicate["stdout"] + duplicate["stderr"]
        self.require(
            "duplicate_intern_in_project_rejected",
            duplicate["returncode"] != 0 and ("已存在" in combined or "already exists" in combined),
            duplicate,
        )
        self.require("duplicate_no_session_mutation", self.ctx.action.session.registry_remote() == sessions_before, {"before": sessions_before, "after": self.ctx.action.session.registry_remote()})
        entries = self.ctx.action.session.registry_entries_for_remote(workspace, baseline["intern"])
        self.require("single_intern_record_after_duplicate", len(entries) == 1 and Path(baseline["status_path"]).is_file(), {"entries": entries, "baseline": baseline})
        return {"duplicate": duplicate, "session_entries": entries}

    def s04_invalid_name_rollback() -> dict[str, Any]:
        workspace = state["workspace"]
        repo = state["repo"]
        bad_name = "bad name with spaces"
        invalid = attempt_create(
            project=str(workspace["display"]),
            intern=bad_name,
            backend="codex",
            repo_url=str(repo),
        )
        combined = invalid["stdout"] + invalid["stderr"]
        self.require(
            "invalid_intern_name_rejected",
            invalid["returncode"] != 0 and ("名称无效" in combined or "invalid" in combined.lower()),
            invalid,
        )
        artifacts = self.require_checks(self.ctx.action.intern.no_artifacts_remote(workspace, bad_name))
        return {"invalid": invalid, "artifacts": artifacts}

    def s05_unsupported_backend_rollback() -> dict[str, Any]:
        workspace = state["workspace"]
        repo = state["repo"]
        intern = self.remote_context.identity("bad_backend")
        backend = attempt_create(
            project=str(workspace["display"]),
            intern=intern,
            backend="unknown_backend",
            repo_url=str(repo),
        )
        combined = backend["stdout"] + backend["stderr"]
        self.require(
            "unsupported_backend_rejected",
            backend["returncode"] != 0 and ("invalid choice" in combined or "unsupported" in combined.lower() or "unknown_backend" in combined),
            backend,
        )
        artifacts = self.require_checks(self.ctx.action.intern.no_artifacts_remote(workspace, intern))
        return {"backend": backend, "artifacts": artifacts}

    def s06_missing_workspace_rollback() -> dict[str, Any]:
        intern = self.remote_context.identity("missing_ws")
        missing = attempt_create(
            project="missing_workspace_f_0008",
            intern=intern,
            backend="codex",
            repo_url=str(state["repo"]),
        )
        combined = missing["stdout"] + missing["stderr"]
        self.require(
            "workspace_not_found_rejected",
            missing["returncode"] != 0 and ("workspace lookup failed" in combined or "workspace not found" in combined.lower()),
            missing,
        )
        sessions = no_session_entries_anywhere(intern)
        relay = no_relay_entry_by_name(intern)
        runtime_matches = [
            str(path)
            for path in (self.work_root / "state" / "v1").glob(f"*/interns/{intern}")
            if path.exists()
        ]
        self.require("missing_workspace_no_runtime", not runtime_matches, {"runtime_matches": runtime_matches})
        return {"missing": missing, "session_entries": sessions, "relay": relay, "runtime_matches": runtime_matches}

    self.run_ordered_scenarios([
        ("F_0008.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0008.s02_create_workspace_and_baseline_intern", s02_create_workspace_and_baseline_intern),
        ("F_0008.s03_duplicate_create_rejected_single_record", s03_duplicate_create_rejected_single_record),
        ("F_0008.s04_invalid_name_rollback", s04_invalid_name_rollback),
        ("F_0008.s05_unsupported_backend_rollback", s05_unsupported_backend_rollback),
        ("F_0008.s06_missing_workspace_rollback", s06_missing_workspace_rollback),
    ])
