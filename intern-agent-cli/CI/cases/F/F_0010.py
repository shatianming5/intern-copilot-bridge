import json
from pathlib import Path
import shutil
from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0010.s01_reset_case_namespace",
    "F_0010.s02_create_workspace_and_idle_intern",
    "F_0010.s03_gui_delete_idle_intern",
    "F_0010.s04_seed_working_intern",
    "F_0010.s05_non_force_delete_rejected_preserved",
    "F_0010.s06_gui_force_delete_cleans_all",
    "F_0010.s07_delete_uses_runtime_metadata_when_source_missing",
    "F_0010.s08_missing_metadata_requires_force",
)


CASE = CaseDefinition(
    id="F_0010_intern_delete_force_guard_remote",
    name="F_0010_intern_delete_force_guard_remote",
    description=(
        "Validates intern delete safety: Idle delete cleans state, Working delete without force is rejected, "
        "force delete cleans runtime/session/task/group registry, and delete falls back to runtime metadata "
        "when the workspace source metadata view is missing."
    ),
    stage="remote",
    timeout_seconds=2400,
    kind="f_intern_session_remote",
    tags=("F", "intern", "delete", "force", "session", "task", "daemon", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "intern.delete_force_guard_remote",
            "gui.intern.delete",
            "gui.intern.force_delete",
            "delete_intern",
            "start_intern_session",
            "daemon.chat_lookup_empty",
            "relay.read_chat_presence",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "ctx.equals",
            "ctx.action_ok",
            "native.delete_force_guard",
            "native.relay_registry_entry",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "feishu_chat:ci_f_0010", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0010_idle", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0010_missing", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0010_runtime", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0010_working", "mode": "exclusive"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_f_0010", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "task:axis_intern_agents_backup:task_ci_f_0010_delete_guard", "mode": "exclusive"},
            {"resource": "tmux:ci_f_0010", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0010_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0010",
            "workspace:ci_f_0010_workspace",
            "intern:intern_ci_f_0010_idle",
            "intern:intern_ci_f_0010_working",
            "intern:intern_ci_f_0010_runtime",
            "intern:intern_ci_f_0010_missing",
            "task:task_ci_f_0010_delete_guard",
            "case_scoped_feishu_group",
            "tmux",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy",
        "notes": (
            "Idle and force-deleted interns are expected to be absent at the end; workspace and report artifacts are retained.",
            "If a force-deleted Working intern leaves a live tmux session, classify as product bug evidence rather than editing product code in this PR.",
            "Runtime metadata fallback covers the MR #112/task413 regression where source metadata is missing but runtime .hook_state.json still points at valid status.md.",
        ),
    },
)


def run_f_intern_delete_force_guard_remote(case: Any) -> None:
    self = case
    state: dict[str, Any] = {}

    def write_runtime_metadata_contract(workspace: dict[str, Any], intern: str, source_metadata: dict[str, Any]) -> dict[str, Any]:
        runtime = self.ctx.action.intern.runtime_dir_remote(workspace, intern)
        runtime_metadata_root = runtime / "metadata" / "local" / ".intern_workspace"
        intern_dir = runtime_metadata_root / "interns" / intern
        intern_dir.mkdir(parents=True, exist_ok=True)
        status_path = intern_dir / "status.md"
        knowledge_path = intern_dir / "knowledge.md"
        status_path.write_text(
            f"# {intern} - status\n\n"
            "<!-- METADATA:STATUS=Idle,TASK=,ROLE=independent,TEAM_ID= -->\n\n"
            "| 字段 | 值 |\n"
            "|------|-----|\n"
            f"| Name | {intern} |\n"
            "| Status | Idle |\n"
            "| Current Task | N/A |\n",
            encoding="utf-8",
        )
        knowledge_path.write_text(f"# {intern} - knowledge\n\n<!-- METADATA:SESSION=0 -->\n", encoding="utf-8")
        contract = dict(source_metadata)
        contract.update({
            "ok": True,
            "metadata_mode": "local_only",
            "repo_provider": "local",
            "project": str(workspace["display"]),
            "workspace_id": str(workspace["workspace_id"]),
            "intern_name": intern,
            "metadata_checkout_path": "",
            "metadata_root": str(runtime_metadata_root),
            "tasks_dir": str(runtime_metadata_root / "tasks"),
            "status_path": str(status_path),
            "knowledge_path": str(knowledge_path),
        })
        hook_state_path = runtime / ".hook_state.json"
        hook_state: dict[str, Any] = {}
        if hook_state_path.is_file():
            try:
                loaded = json.loads(hook_state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    hook_state = loaded
            except Exception:
                hook_state = {}
        hook_state.update({
            "project": str(workspace["display"]),
            "workspace_id": str(workspace["workspace_id"]),
            "metadata_resolver": contract,
        })
        hook_state_path.write_text(json.dumps(hook_state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return contract

    def remove_source_metadata_dir(metadata: dict[str, Any]) -> dict[str, str]:
        status_path = Path(str(metadata.get("status_path") or ""))
        knowledge_path = Path(str(metadata.get("knowledge_path") or ""))
        source_dir = status_path.parent
        if source_dir.is_dir():
            shutil.rmtree(source_dir)
        return {
            "source_status_path": str(status_path),
            "source_knowledge_path": str(knowledge_path),
            "source_dir": str(source_dir),
            "source_dir_exists": str(source_dir.exists()),
        }

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace_and_idle_intern() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace",
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        idle = self.ctx.action.intern.create_case_remote(workspace, "idle", repo_url=str(repo))
        idle_metadata = self.ctx.action.intern.metadata_resolve_checked_remote(workspace, idle)
        idle_chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace, idle, timeout=self.args.timeout)
        idle_relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace, idle, timeout=self.args.timeout)
        state.update({
            "repo": repo,
            "workspace": workspace,
            "idle": idle,
            "idle_metadata": idle_metadata,
            "idle_chat": idle_chat,
            "idle_relay": idle_relay,
        })
        return {"repo": str(repo), "workspace": workspace, "idle": idle, "chat": idle_chat, "relay": idle_relay}

    def s03_gui_delete_idle_intern() -> dict[str, Any]:
        workspace = state["workspace"]
        idle = state["idle"]
        self.run_cmd(
            f"delete idle intern {idle}",
            [*self.internctl, "delete", idle, "--project", str(workspace["display"]), "--confirm"],
            timeout=240,
        )
        removed = self.require_checks(self.ctx.action.intern.removed_remote(workspace, idle, metadata=state["idle_metadata"]))
        return {
            "removed": removed,
            "gui_command": "intern.deleteIntern",
            "cli_equivalent": "internctl delete <intern> --project <project> --confirm",
        }

    def s04_seed_working_intern() -> dict[str, Any]:
        workspace = state["workspace"]
        repo = state["repo"]
        working = self.ctx.action.intern.create_case_remote(workspace, "working", repo_url=str(repo))
        working_metadata = self.ctx.action.intern.metadata_resolve_checked_remote(workspace, working)
        working_chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace, working, timeout=self.args.timeout)
        session = self.ctx.action.session.start_for_workspace_remote(workspace, working)
        task_id = self.task_id("delete_guard")
        task = self.ctx.action.task.write_working_fixture_remote(workspace, working, working_metadata, task_id)
        status = self.ctx.action.intern.status_json_remote(workspace, working)
        self.require(
            "working_intern_seeded",
            status.get("status") == "Working" and status.get("task") == task_id and session.get("running") is True,
            {"status": status, "session": session, "task": task},
        )
        state.update({
            "working": working,
            "working_metadata": working_metadata,
            "working_chat": working_chat,
            "working_session": session,
            "working_task": task,
            "working_task_dir": Path(str(task["task_dir"])),
            "working_tmux_session": str(session.get("tmux_session") or working),
        })
        return {"working": working, "chat": working_chat, "session": session, "task": task, "status": status}

    def s05_non_force_delete_rejected_preserved() -> dict[str, Any]:
        workspace = state["workspace"]
        working = state["working"]
        result = self.run_cmd(
            f"non-force delete working intern {working}",
            [*self.internctl, "delete", working, "--project", str(workspace["display"]), "--confirm"],
            timeout=180,
            check=False,
        )
        combined = result.stdout + result.stderr
        status_path = Path(str(state["working_metadata"].get("status_path") or ""))
        runtime = self.ctx.action.intern.runtime_dir_remote(workspace, working)
        sessions = self.ctx.action.session.registry_entries_for_remote(workspace, working)
        session = self.ctx.action.session.status_for_workspace_remote(workspace, working)
        task_dir = state["working_task_dir"]
        relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace, working, timeout=60)
        self.require(
            "working_delete_requires_force",
            result.returncode != 0
            and ("正在工作中" in combined or "status=Working" in combined)
            and status_path.is_file()
            and task_dir.is_dir()
            and runtime.is_dir()
            and bool(sessions)
            and session.get("running") is True
            and bool(relay.get("chat_id")),
            {
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "status_path": str(status_path),
                "task_dir": str(task_dir),
                "runtime": str(runtime),
                "session_entries": sessions,
                "session_status": session,
                "relay": relay,
            },
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "status_retained": status_path.is_file(),
            "task_retained": task_dir.is_dir(),
            "runtime_retained": runtime.is_dir(),
            "session_retained": bool(sessions),
            "tmux_running": session.get("running") is True,
            "relay": relay,
        }

    def s06_gui_force_delete_cleans_all() -> dict[str, Any]:
        workspace = state["workspace"]
        working = state["working"]
        result = self.run_cmd(
            f"force delete working intern {working}",
            [*self.internctl, "delete", working, "--project", str(workspace["display"]), "--confirm", "--force"],
            timeout=240,
        )
        removed = self.require_checks(self.ctx.action.intern.removed_remote(
            workspace,
            working,
            metadata=state["working_metadata"],
            task_dir=state["working_task_dir"],
            tmux_session=state["working_tmux_session"],
            expect_tmux_absent=True,
        ))
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "removed": removed,
            "gui_command": "intern.forceDeleteIntern",
            "cli_equivalent": "internctl delete <intern> --project <project> --confirm --force",
        }

    def s07_delete_uses_runtime_metadata_when_source_missing() -> dict[str, Any]:
        workspace = state["workspace"]
        repo = state["repo"]
        runtime_intern = self.ctx.action.intern.create_case_remote(workspace, "runtime", repo_url=str(repo))
        source_metadata = self.ctx.action.intern.metadata_resolve_checked_remote(workspace, runtime_intern)
        runtime_contract = write_runtime_metadata_contract(workspace, runtime_intern, source_metadata)
        source_removed = remove_source_metadata_dir(source_metadata)
        result = self.run_cmd(
            f"runtime metadata delete {runtime_intern}",
            [*self.internctl, "delete", runtime_intern, "--project", str(workspace["display"]), "--confirm"],
            timeout=240,
            check=False,
        )
        runtime_status_path = Path(str(runtime_contract.get("status_path") or ""))
        runtime_knowledge_path = Path(str(runtime_contract.get("knowledge_path") or ""))
        self.require_product_bug_evidence(
            "delete_uses_runtime_metadata_when_source_missing",
            result.returncode == 0,
            {
                "finding_summaries": [
                    "source metadata is absent, runtime status/knowledge and session registry are present, but ordinary internctl delete reports intern not found instead of using runtime metadata_resolver",
                ],
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "source_removed": source_removed,
                "runtime_status_exists_before_delete_assert": runtime_status_path.is_file(),
                "runtime_knowledge_exists_before_delete_assert": runtime_knowledge_path.is_file(),
                "session_entries": self.ctx.action.session.registry_entries_for_remote(workspace, runtime_intern),
                "hook_state_path": str(self.ctx.action.intern.runtime_dir_remote(workspace, runtime_intern) / ".hook_state.json"),
                "runtime_contract": {
                    "metadata_mode": runtime_contract.get("metadata_mode"),
                    "metadata_root": runtime_contract.get("metadata_root"),
                    "status_path": runtime_contract.get("status_path"),
                    "knowledge_path": runtime_contract.get("knowledge_path"),
                },
            },
        )
        removed = self.require_checks(self.ctx.action.intern.removed_remote(workspace, runtime_intern, metadata=runtime_contract))
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "source_removed": source_removed,
            "runtime_contract": {
                "metadata_mode": runtime_contract.get("metadata_mode"),
                "metadata_root": runtime_contract.get("metadata_root"),
                "status_path": runtime_contract.get("status_path"),
                "knowledge_path": runtime_contract.get("knowledge_path"),
            },
            "removed": removed,
            "assertion": "ordinary internctl delete uses runtime .hook_state.json metadata_resolver when source metadata is missing",
        }

    def s08_missing_metadata_requires_force() -> dict[str, Any]:
        workspace = state["workspace"]
        repo = state["repo"]
        missing = self.ctx.action.intern.create_case_remote(workspace, "missing", repo_url=str(repo))
        source_metadata = self.ctx.action.intern.metadata_resolve_checked_remote(workspace, missing)
        source_removed = remove_source_metadata_dir(source_metadata)
        result = self.run_cmd(
            f"missing metadata ordinary delete {missing}",
            [*self.internctl, "delete", missing, "--project", str(workspace["display"]), "--confirm"],
            timeout=180,
            check=False,
        )
        combined = result.stdout + result.stderr
        self.require(
            "missing_metadata_delete_requires_force",
            result.returncode != 0 and "--force" in combined,
            {"returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr, "source_removed": source_removed},
        )
        force = self.run_cmd(
            f"missing metadata force delete {missing}",
            [*self.internctl, "delete", missing, "--project", str(workspace["display"]), "--confirm", "--force"],
            timeout=240,
        )
        removed = self.require_checks(self.ctx.action.intern.removed_remote(workspace, missing, metadata=source_metadata))
        return {
            "ordinary_returncode": result.returncode,
            "ordinary_stdout": result.stdout,
            "ordinary_stderr": result.stderr,
            "force_stdout": force.stdout,
            "force_stderr": force.stderr,
            "removed": removed,
            "assertion": "ordinary delete explains --force when source and runtime metadata are both unavailable",
        }

    self.run_ordered_scenarios([
        ("F_0010.s01_reset_case_namespace", s01_reset_case_namespace),
        ("F_0010.s02_create_workspace_and_idle_intern", s02_create_workspace_and_idle_intern),
        ("F_0010.s03_gui_delete_idle_intern", s03_gui_delete_idle_intern),
        ("F_0010.s04_seed_working_intern", s04_seed_working_intern),
        ("F_0010.s05_non_force_delete_rejected_preserved", s05_non_force_delete_rejected_preserved),
        ("F_0010.s06_gui_force_delete_cleans_all", s06_gui_force_delete_cleans_all),
        ("F_0010.s07_delete_uses_runtime_metadata_when_source_missing", s07_delete_uses_runtime_metadata_when_source_missing),
        ("F_0010.s08_missing_metadata_requires_force", s08_missing_metadata_requires_force),
    ])
