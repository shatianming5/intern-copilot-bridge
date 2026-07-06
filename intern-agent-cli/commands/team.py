"""internctl team — workspace team metadata commands."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from commands import create as create_cmd
from commands import delete as delete_cmd
from commands import session as session_cmd
from lib.enterprise_paths import daemon_owner_path
from lib.git_ops import add_commit_push, get_default_branch, remove_and_push, run_git
from lib.intern_registry import parse_status_md
from lib.team_registry import (
    WORK_AGENTS_ROOT,
    MAX_TEAM_WORKERS,
    build_team_metadata,
    coordinator_json_path,
    coordinators_dir,
    default_team_lead_name,
    default_worker_names,
    intern_workspace_root,
    interns_dir,
    list_enterprise_project_names,
    list_teams,
    project_metadata_prefix,
    project_metadata_rel_path,
    project_repo_path,
    read_coordinator,
    read_team,
    tasks_dir,
    team_json_path,
    validate_member_role,
    validate_team_name,
    write_coordinator,
    write_team,
)


def setup_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = subparsers.add_parser("team", help="管理 workspace team metadata")
    team_sub = parser.add_subparsers(dest="team_command", help="team commands")

    create = team_sub.add_parser("create", help="创建 workspace team metadata")
    create.add_argument("--project", default="axis_intern_agents", help="项目名称")
    create.add_argument("team_name", nargs="?", help="team name，在当前 workspace 内唯一；默认成员名使用 intern_<team_name>_*")
    create.add_argument("--worker-count", type=int, default=0, help=f"同时创建 worker 数量，0-{MAX_TEAM_WORKERS}")
    create.add_argument("--repo-url", default=create_cmd.DEFAULT_REPO_URL, help="Git repo URL，传给 intern 创建流程")
    create.add_argument("--type", choices=["copilot", "claude", "codex"], default="copilot", help="创建出的 intern 类型")
    create.add_argument("--coordinator-id", default="", help="创建后默认绑定到该 coordinator")
    create.set_defaults(func=run_create)

    list_cmd = team_sub.add_parser("list", help="列出 workspace teams")
    list_cmd.add_argument("--project", default="axis_intern_agents", help="项目名称")
    list_cmd.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON")
    list_cmd.set_defaults(func=run_list)

    status = team_sub.add_parser("status", help="显示 team metadata")
    status.add_argument("team_id", help="team name")
    status.add_argument("--project", default="axis_intern_agents", help="项目名称")
    status.add_argument("--json", dest="as_json", action="store_true", help="输出 JSON")
    status.set_defaults(func=run_status)

    delete = team_sub.add_parser("delete", help="删除 workspace team 及成员")
    delete.add_argument("team_id", help="team name")
    delete.add_argument("--project", default="axis_intern_agents", help="项目名称")
    delete.add_argument("--confirm", action="store_true", help="跳过交互确认")
    delete.add_argument("--force", action="store_true", help="强制删除非 Idle member；用于 create team 回滚")
    delete.set_defaults(func=run_delete)

    assign = team_sub.add_parser("assign-worker-task", help="team_lead 创建 task 文档后通知 worker 接受")
    assign.add_argument("team_id", help="team name")
    assign.add_argument("worker_name", help="目标 worker intern 名")
    assign.add_argument("--project", default="axis_intern_agents", help="项目名称")
    assign.add_argument("--lead-name", default="", help="发送通知的 team_lead intern 名；默认读取 team metadata")
    assign.add_argument("--task-id", required=True, help="要创建的 metadata tasks/<task_id> 目录名")
    assign.add_argument("--title", required=True, help="task 标题")
    assign.add_argument("--background", required=True, help="背景说明")
    assign.add_argument("--goal", required=True, help="任务目标")
    assign.add_argument("--acceptance", action="append", required=True, help="验收标准，可重复传入")
    assign.add_argument("--details", default="", help="补充实现细节")
    assign.add_argument("--no-notify", action="store_true", help="只创建 task，不通过 peer send 通知 worker")
    assign.set_defaults(func=run_assign_worker_task)


def run_create(args: argparse.Namespace) -> int:
    project = args.project
    try:
        team_name = _resolve_team_name(args)
        worker_count = int(args.worker_count)
        workers = default_worker_names(team_name, worker_count)
    except ValueError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return 1

    team_lead = default_team_lead_name(team_name)
    if os.path.exists(team_json_path(project, team_name)):
        if _team_exists_in_head(project, team_name):
            print(f"❌ team '{team_name}' 已存在", file=sys.stderr)
            return 1
        return _resume_pending_team_create(args, team_name, workers, team_lead)

    prepared_members: list[dict[str, Any]] = []
    pushed_metadata_paths: list[str] = []
    created_feishu_members: list[str] = []
    coordinator_snapshot: tuple[str, str, dict[str, Any]] | None = None
    phase = "initialize team create"
    try:
        coordinator_id = args.coordinator_id.strip()
        phase = "pull project metadata"
        _pull_project_repo(project)
        phase = "preflight team member names"
        _preflight_team_members(project, [team_lead, *workers])
        phase = "read daemon owner identity"
        owner_identity = _read_daemon_owner_identity()
        phase = f"resolve coordinator {coordinator_id}" if coordinator_id else "resolve coordinator"
        coordinator_project = _resolve_coordinator_project(project, coordinator_id) if coordinator_id else ""
        if coordinator_id:
            phase = f"read coordinator {coordinator_id}"
            coordinator_snapshot = (coordinator_project, coordinator_id, read_coordinator(coordinator_project, coordinator_id))
        for member_name, role in [(team_lead, "team_lead"), *[(worker, "worker") for worker in workers]]:
            phase = f"prepare metadata for {member_name}"
            prepared_members.append(_prepare_team_member_metadata(project, args.repo_url, args.type, member_name, role, team_name))

        phase = f"write team metadata for {team_name}"
        data = build_team_metadata(
            project=project,
            team_id=team_name,
            team_lead=team_lead,
            workers=workers,
            coordinator=None,
        )
        data["owner"] = owner_identity
        write_team(project, team_name, data)
        commit_paths = [
            *[path for member in prepared_members for path in member["commit_paths"]],
            project_metadata_rel_path(project, "teams", team_name, "team.json"),
        ]
        if coordinator_id:
            phase = f"bind coordinator {coordinator_id} to team {team_name}"
            coordinator_project = _bind_coordinator_to_team(project, coordinator_id, team_name, coordinator_project)
            if coordinator_project == project:
                commit_paths.append(project_metadata_rel_path(project, "coordinators", coordinator_id, "coordinator.json"))
        push_branch = _project_push_branch(project)
        phase = f"push team metadata for {team_name}"
        _commit_project_metadata(project, commit_paths, f"[team] create {team_name}", branch=push_branch)
        pushed_metadata_paths = list(commit_paths)
        if coordinator_id and coordinator_project != project:
            phase = f"push coordinator binding {coordinator_id}"
            _commit_project_metadata(
                coordinator_project,
                [project_metadata_rel_path(coordinator_project, "coordinators", coordinator_id, "coordinator.json")],
                f"[team] bind {coordinator_id} to {team_name}",
            )
        phase = "sync prepared member repos"
        _sync_prepared_member_repos(prepared_members, branch=push_branch)
        for member in prepared_members:
            phase = f"create Feishu group for {member['name']}"
            create_cmd._create_feishu_group(
                name=member["name"],
                project=project,
                intern_type=args.type,
                workspace_id=member["workspace_id"],
            )
            created_feishu_members.append(member["name"])
        for member in prepared_members:
            phase = f"register session for {member['name']}"
            _register_prepared_member_session(member)
            phase = f"notify daemon for {member['name']}"
            create_cmd._notify_daemon_best_effort(name=member["name"])
    except Exception as exc:
        print(f"❌ 创建 team 失败: {phase}: {exc}", file=sys.stderr)
        _rollback_batch_team_create(
            project=project,
            team_name=team_name,
            prepared_members=prepared_members,
            pushed_metadata_paths=pushed_metadata_paths,
            created_feishu_members=created_feishu_members,
            coordinator_snapshot=coordinator_snapshot,
        )
        return 1

    print(f"✅ team '{team_name}' 创建成功（lead: {team_lead}, workers: {len(workers)}）")
    return 0


def _resume_pending_team_create(args: argparse.Namespace, team_name: str, workers: list[str], team_lead: str) -> int:
    project = args.project
    coordinator_id = args.coordinator_id.strip()
    try:
        team_data = read_team(project, team_name)
        _validate_pending_team_metadata(team_data, team_name, team_lead, workers)
        if not _has_owner_identity(team_data):
            team_data["owner"] = _read_daemon_owner_identity()
            team_data["updated_at"] = build_updated_timestamp()
            write_team(project, team_name, team_data)
        validate_member_role(project, team_lead, "team_lead", team_name)
        for worker in workers:
            validate_member_role(project, worker, "worker", team_name)

        commit_paths = [project_metadata_rel_path(project, "teams", team_name, "team.json")]
        if coordinator_id:
            coordinator = team_data.get("coordinator") if isinstance(team_data.get("coordinator"), dict) else {}
            if coordinator.get("coordinator_id") != coordinator_id:
                coordinator_project = _bind_coordinator_to_team(project, coordinator_id, team_name)
            else:
                coordinator_project = _resolve_coordinator_project(project, coordinator_id)
            commit_paths.append(project_metadata_rel_path(project, "coordinators", coordinator_id, "coordinator.json"))

        _commit_project_metadata(
            project,
            [path for path in commit_paths if "coordinators/" not in path or coordinator_project == project],
            f"[team] create {team_name}",
        )
        if coordinator_id and coordinator_project != project:
            _commit_project_metadata(
                coordinator_project,
                [project_metadata_rel_path(coordinator_project, "coordinators", coordinator_id, "coordinator.json")],
                f"[team] bind {coordinator_id} to {team_name}",
            )
    except Exception as exc:
        print(f"❌ 恢复未完成 team 创建失败: {exc}", file=sys.stderr)
        return 1

    print(f"✅ team '{team_name}' 创建成功（lead: {team_lead}, workers: {len(workers)}）")
    return 0


def _validate_pending_team_metadata(data: dict[str, object], team_name: str, team_lead: str, workers: list[str]) -> None:
    if str(data.get("team_id") or data.get("team_name") or "") != team_name:
        raise ValueError(f"existing team metadata does not match team '{team_name}'")

    lead = data.get("team_lead") if isinstance(data.get("team_lead"), dict) else {}
    if lead.get("intern_name") != team_lead:
        raise ValueError(f"existing team metadata lead does not match '{team_lead}'")

    existing_workers = data.get("workers") if isinstance(data.get("workers"), list) else []
    existing_worker_names = [
        str(worker.get("intern_name") or "")
        for worker in existing_workers
        if isinstance(worker, dict)
    ]
    if existing_worker_names != workers:
        raise ValueError(f"existing team metadata workers do not match {workers}")


def _has_owner_identity(data: dict[str, object]) -> bool:
    owner = data.get("owner") if isinstance(data.get("owner"), dict) else {}
    return bool(
        str(owner.get("open_id") or owner.get("owner_open_id") or "").strip()
        or str(owner.get("mobile") or owner.get("owner_mobile") or "").strip()
    )


def _read_daemon_owner_identity() -> dict[str, str]:
    path = daemon_owner_path(WORK_AGENTS_ROOT)
    with open(path, "r", encoding="utf-8") as fp:
        owner = json.load(fp)
    if not isinstance(owner, dict):
        raise RuntimeError(f"daemon owner config must be an object: {path}")
    mobile = str(owner.get("mobile") or owner.get("owner_mobile") or "").strip()
    open_id = str(owner.get("owner_open_id") or owner.get("open_id") or "").strip()
    display_name = str(owner.get("display_name") or owner.get("owner_name") or owner.get("name") or "").strip()
    if not (open_id or mobile):
        raise RuntimeError(f"daemon owner config missing owner_open_id/open_id or mobile: {path}")
    return {
        "type": "feishu_owner",
        "mobile": mobile,
        "open_id": open_id,
        "display_name": display_name,
    }


def _team_exists_in_head(project: str, team_name: str) -> bool:
    repo_path = project_repo_path(project)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return os.path.exists(team_json_path(project, team_name))
    rel_path = project_metadata_rel_path(project, "teams", team_name, "team.json")
    return run_git(["cat-file", "-e", f"HEAD:{rel_path}"], cwd=repo_path, check=False).returncode == 0


def run_bind(args: argparse.Namespace) -> int:
    project = args.project
    coordinator_id = args.coordinator_id
    team_id = args.team_id
    try:
        coordinator_project = _bind_coordinator_to_team(project, coordinator_id, team_id)
        team_paths = [project_metadata_rel_path(project, "teams", team_id, "team.json")]
        if coordinator_project == project:
            team_paths.append(project_metadata_rel_path(project, "coordinators", coordinator_id, "coordinator.json"))
        _commit_project_metadata(project, team_paths, f"[team] bind {coordinator_id} to {team_id}")
        if coordinator_project != project:
            _commit_project_metadata(
                coordinator_project,
                [project_metadata_rel_path(coordinator_project, "coordinators", coordinator_id, "coordinator.json")],
                f"[team] bind {coordinator_id} to {team_id}",
            )
    except Exception as exc:
        print(f"❌ 绑定 coordinator 失败: {exc}", file=sys.stderr)
        return 1

    if getattr(args, "as_json", False):
        print(json.dumps(read_coordinator(project, coordinator_id), ensure_ascii=False, indent=2))
        return 0

    print(f"✅ coordinator '{coordinator_id}' 已绑定 team '{team_id}'")
    return 0


def _bind_coordinator_to_team(project: str, coordinator_id: str, team_id: str, coordinator_project: str | None = None) -> str:
    if not validate_team_name(coordinator_id):
        raise ValueError(f"coordinator_id 无效: {coordinator_id}（必须匹配 [a-z][a-z0-9_]*）")
    if not validate_team_name(team_id):
        raise ValueError(f"team_id 无效: {team_id}（必须匹配 [a-z][a-z0-9_]*）")

    coordinator_project = coordinator_project or _resolve_coordinator_project(project, coordinator_id)
    coordinator = read_coordinator(coordinator_project, coordinator_id)
    team_data = read_team(project, team_id)
    now = build_updated_timestamp()

    anchor = coordinator.get("anchor") if isinstance(coordinator.get("anchor"), dict) else {}
    coordinator_intern = str(coordinator.get("intern_name") or "")
    anchor_project = str(anchor.get("project") or project)
    binding = {
        "coordinator_id": str(coordinator.get("coordinator_id") or coordinator_id),
        "intern_name": coordinator_intern,
        "anchor_project": anchor_project,
        "anchor_workspace_root": str(anchor.get("repo_path") or intern_workspace_root(coordinator_intern, anchor_project)),
        "bound_at": now,
    }
    if not binding["intern_name"]:
        raise ValueError("coordinator metadata 缺少 intern_name")

    team_data["coordinator"] = binding
    team_data["updated_at"] = now

    managed = coordinator.setdefault("managed_workspaces", [])
    if not isinstance(managed, list):
        raise ValueError("coordinator managed_workspaces must be a list")
    managed[:] = [entry for entry in managed if not (isinstance(entry, dict) and entry.get("project") == project and entry.get("team_id") == team_id)]
    managed.append({
        "project": project,
        "workspace_root": project_repo_path(project),
        "team_id": team_id,
        "team_metadata_path": project_metadata_rel_path(project, "teams", team_id, "team.json"),
        "status": str(team_data.get("status") or "active"),
        "bound_at": now,
        "updated_at": now,
    })

    team_leads = coordinator.setdefault("team_leads", [])
    if not isinstance(team_leads, list):
        raise ValueError("coordinator team_leads must be a list")
    lead = team_data.get("team_lead") if isinstance(team_data.get("team_lead"), dict) else {}
    lead_name = str(lead.get("intern_name") or "")
    if not lead_name:
        raise ValueError("team metadata 缺少 team_lead.intern_name")
    team_leads[:] = [entry for entry in team_leads if not (isinstance(entry, dict) and entry.get("project") == project and entry.get("team_id") == team_id)]
    team_leads.append({
        "intern_name": lead_name,
        "project": project,
        "team_id": team_id,
        "status": str(lead.get("status") or "active"),
        "bound_at": now,
        "updated_at": now,
    })

    coordinator["updated_at"] = now
    write_team(project, team_id, team_data)
    write_coordinator(coordinator_project, coordinator_id, coordinator)
    return coordinator_project


def _resolve_coordinator_project(project: str, coordinator_id: str) -> str:
    if not validate_team_name(coordinator_id):
        raise ValueError(f"coordinator_id 无效: {coordinator_id}（必须匹配 [a-z][a-z0-9_]*）")
    if os.path.isfile(coordinator_json_path(project, coordinator_id)):
        return project

    matches: list[str] = []
    for entry in list_enterprise_project_names():
        if entry == project:
            continue
        if os.path.isfile(coordinator_json_path(entry, coordinator_id)):
            matches.append(entry)
    if not matches:
        raise FileNotFoundError(f"coordinator '{coordinator_id}' metadata not found under enterprise metadata coordinators")
    return matches[0]


def build_updated_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_team_name(args: argparse.Namespace) -> str:
    team_name = args.team_name
    if not team_name:
        raise ValueError("必须传 team_name")
    if not validate_team_name(team_name):
        raise ValueError(f"team_name 无效: {team_name}（必须匹配 [a-z][a-z0-9_]*）")
    return team_name


def _prepare_team_member_metadata(project: str, repo_url: str, intern_type: str, name: str, role: str, team_id: str) -> dict[str, Any]:
    workspace = create_cmd._find_workspace_for_project(project)
    workspace_id = str(workspace.get("workspace_id") or "")
    workspace_repo_url = str(workspace.get("repo_url") or "")
    effective_repo_url = repo_url if repo_url and repo_url != create_cmd.DEFAULT_REPO_URL else workspace_repo_url
    if not effective_repo_url:
        raise RuntimeError(f"workspace '{workspace_id}' missing repo_url")

    intern_root = os.fspath(create_cmd.intern_runtime_dir(WORK_AGENTS_ROOT, workspace_id, name))
    intern_repo = os.path.join(intern_root, project)
    if os.path.exists(intern_root):
        raise ValueError(f"intern '{name}' 已存在: {intern_root}")

    for sub in ("debug", "outputs"):
        os.makedirs(os.path.join(intern_root, sub), exist_ok=True)
    create_cmd.clone(effective_repo_url, intern_repo)
    _set_repo_git_identity(intern_repo, name)

    task_id = create_cmd.team_lead_management_task_id(team_id) if role == "team_lead" and team_id else ""
    initial_status = "Working" if role == "team_lead" and task_id else "Idle"
    locale = create_cmd._read_locale()
    contract = create_cmd.resolve_metadata_for_workspace_id(workspace_id, name, task_id)
    status_path = create_cmd._require_contract_path(contract, "status_path")
    knowledge_path = create_cmd._require_contract_path(contract, "knowledge_path")
    metadata_paths = [status_path, knowledge_path]
    create_cmd._write_text_file(status_path, create_cmd._status_content(name, initial_status, task_id, role, team_id, locale))
    create_cmd._write_text_file(knowledge_path, create_cmd._knowledge_content(name, locale))
    if role == "team_lead" and task_id:
        metadata_paths.extend(
            create_cmd._write_enterprise_task_metadata(
                contract,
                name=name,
                task_id=task_id,
                kind="team_lead",
                team_id=team_id,
            )
        )

    runtime_contract = create_cmd.bind_repo_dotdir_metadata_to_code_repo(contract, intern_repo, name, task_id)
    runtime_contract["code_repo_path"] = intern_repo
    runtime_contract["code_worktree_path"] = intern_repo
    with open(os.path.join(intern_root, ".hook_state.json"), "w", encoding="utf-8") as fp:
        json.dump({"project": project, "workspace_id": workspace_id, "metadata_resolver": runtime_contract}, fp, ensure_ascii=False, indent=2)
        fp.write("\n")

    repo_path = project_repo_path(project)
    return {
        "name": name,
        "project": project,
        "type": intern_type,
        "role": role,
        "team_id": team_id,
        "workspace_id": workspace_id,
        "intern_root": intern_root,
        "intern_repo": intern_repo,
        "metadata_paths": metadata_paths,
        "commit_paths": [os.path.relpath(path, repo_path) for path in metadata_paths],
    }


def _set_repo_git_identity(repo_path: str, name: str) -> None:
    subprocess.run(["git", "config", "user.name", name], cwd=repo_path, capture_output=True, check=False)
    subprocess.run(["git", "config", "user.email", f"{name}@intern.local"], cwd=repo_path, capture_output=True, check=False)


def _sync_prepared_member_repos(prepared_members: list[dict[str, Any]], branch: str | None = None) -> None:
    for member in prepared_members:
        repo_path = str(member["intern_repo"])
        target_branch = branch or run_git(["branch", "--show-current"], cwd=repo_path, check=False).stdout.strip() or get_default_branch(repo_path)
        run_git(["fetch", "origin", target_branch], cwd=repo_path)
        checkout = run_git(["checkout", target_branch], cwd=repo_path, check=False)
        if checkout.returncode != 0:
            run_git(["checkout", "-b", target_branch, f"origin/{target_branch}"], cwd=repo_path)
        run_git(["pull", "--rebase", "--autostash", "origin", target_branch], cwd=repo_path)


def _register_prepared_member_session(member: dict[str, Any]) -> None:
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    sessions: dict[str, Any] = {}
    if os.path.exists(sessions_file):
        with open(sessions_file, "r", encoding="utf-8") as fp:
            loaded = json.load(fp)
        if isinstance(loaded, dict):
            sessions = loaded
    key = create_cmd._session_registry_key(member["name"], member["project"], member["workspace_id"])
    sessions[key] = {
        **(sessions.get(key, {}) if isinstance(sessions.get(key), dict) else {}),
        "type": member["type"],
        "intern_name": member["name"],
        "project": member["project"],
        "workspace_id": member["workspace_id"],
        "intern_dir": member["intern_root"],
    }
    tmp = sessions_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(sessions, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    os.replace(tmp, sessions_file)


def _rollback_batch_team_create(
    *,
    project: str,
    team_name: str,
    prepared_members: list[dict[str, Any]],
    pushed_metadata_paths: list[str],
    created_feishu_members: list[str],
    coordinator_snapshot: tuple[str, str, dict[str, Any]] | None,
) -> None:
    for member_name in reversed(created_feishu_members):
        create_cmd._delete_feishu_group_best_effort(name=member_name, project=project)
    if coordinator_snapshot is not None:
        coordinator_project, coordinator_id, data = coordinator_snapshot
        write_coordinator(coordinator_project, coordinator_id, data)
        try:
            _commit_project_metadata(
                coordinator_project,
                [project_metadata_rel_path(coordinator_project, "coordinators", coordinator_id, "coordinator.json")],
                f"[team] rollback bind {coordinator_id} from {team_name}",
            )
        except Exception as exc:
            print(f"❌ coordinator metadata 回滚失败: {exc}", file=sys.stderr)
    if pushed_metadata_paths:
        rollback_paths = list(pushed_metadata_paths)
        coordinator_paths = [path for path in rollback_paths if "/coordinators/" in f"/{path}"]
        rollback_paths = [path for path in rollback_paths if path not in coordinator_paths]
        try:
            _remove_project_metadata(project, rollback_paths, f"[team] rollback create {team_name}")
        except Exception as exc:
            print(f"❌ team metadata 回滚失败: {exc}", file=sys.stderr)
    for member in prepared_members:
        _clear_team_member_session_registry_entries(
            name=str(member["name"]),
            project=project,
            workspace_id=str(member["workspace_id"]),
            intern_root=str(member["intern_root"]),
        )
        if os.path.isdir(member["intern_root"]):
            shutil.rmtree(member["intern_root"], ignore_errors=True)


def _clear_team_member_session_registry_entries(*, name: str, project: str, workspace_id: str, intern_root: str) -> None:
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    if not os.path.isfile(sessions_file):
        return
    try:
        with open(sessions_file, "r", encoding="utf-8") as fp:
            loaded = json.load(fp)
        if not isinstance(loaded, dict):
            return
        exact_key = create_cmd._session_registry_key(name, project, workspace_id)
        removed: list[str] = []
        for key, entry in list(loaded.items()):
            if not isinstance(entry, dict):
                continue
            if _team_member_registry_entry_matches(
                key=str(key),
                entry=entry,
                name=name,
                project=project,
                workspace_id=workspace_id,
                intern_root=intern_root,
                exact_key=exact_key,
            ):
                loaded.pop(key, None)
                removed.append(str(key))
        if not removed:
            return
        tmp = sessions_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(loaded, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
        os.replace(tmp, sessions_file)
    except Exception as exc:
        print(f"⚠️  session registry 回滚失败: {exc}", file=sys.stderr)


def _team_member_registry_entry_matches(
    *,
    key: str,
    entry: dict[str, Any],
    name: str,
    project: str,
    workspace_id: str,
    intern_root: str,
    exact_key: str,
) -> bool:
    if key == exact_key:
        return True
    entry_name = str(entry.get("intern_name") or key.split(":", 1)[-1])
    if entry_name != name:
        return False
    entry_project = str(entry.get("project") or "")
    entry_workspace_id = str(entry.get("workspace_id") or "")
    if entry_project and entry_project != project:
        return False
    if entry_workspace_id and entry_workspace_id != workspace_id:
        return False
    key_scope = key.split(":", 1)[0] if ":" in key else ""
    if key_scope and key_scope not in {project, workspace_id}:
        return False
    entry_intern_dir = str(entry.get("intern_dir") or "")
    if entry_intern_dir and os.path.abspath(entry_intern_dir) == os.path.abspath(intern_root):
        return True
    scopes = {scope for scope in (entry_project, entry_workspace_id, key_scope) if scope}
    return bool(scopes.intersection({scope for scope in (project, workspace_id) if scope}))


def _remove_project_metadata(project: str, paths: list[str], message: str) -> None:
    if not paths:
        return
    repo_path = project_repo_path(project)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        for path in paths:
            abs_path = os.path.join(repo_path, path)
            if os.path.isdir(abs_path):
                shutil.rmtree(abs_path)
            elif os.path.exists(abs_path):
                os.remove(abs_path)
        return
    existing = [path for path in paths if os.path.exists(os.path.join(repo_path, path))]
    if not existing:
        return
    tracked_files = set(run_git(["ls-files", "--", *existing], cwd=repo_path).stdout.splitlines())
    tracked_paths = [
        path
        for path in existing
        if path in tracked_files or any(file_path.startswith(f"{path.rstrip('/')}/") for file_path in tracked_files)
    ]
    if tracked_paths:
        remove_and_push(
            repo_path=repo_path,
            paths=tracked_paths,
            message=message,
            branch=_project_push_branch(project),
        )
    for path in existing:
        abs_path = os.path.join(repo_path, path)
        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
        elif os.path.exists(abs_path):
            os.remove(abs_path)


def _pull_project_repo(project: str) -> None:
    repo_path = project_repo_path(project)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return
    branch = run_git(["branch", "--show-current"], cwd=repo_path, check=False).stdout.strip()
    if not branch:
        branch = get_default_branch(repo_path)
    run_git(["pull", "--rebase", "--autostash", "origin", branch], cwd=repo_path)


def _project_push_branch(project: str) -> str | None:
    repo_path = project_repo_path(project)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return None
    branch = run_git(["branch", "--show-current"], cwd=repo_path, check=False).stdout.strip()
    if branch:
        return branch
    try:
        return get_default_branch(repo_path)
    except RuntimeError:
        return None


def _commit_project_metadata(project: str, paths: list[str], message: str, branch: str | None = None) -> None:
    if not paths:
        return
    repo_path = project_repo_path(project)
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return
    add_commit_push(
        repo_path=repo_path,
        paths=paths,
        message=message,
        branch=branch if branch is not None else _project_push_branch(project),
    )


def _preflight_team_members(project: str, names: list[str]) -> None:
    metadata_interns_dir = interns_dir(project)
    for name in names:
        member_dir = os.path.join(metadata_interns_dir, name)
        if os.path.isdir(member_dir) and not _remove_empty_stale_dir(member_dir):
            raise ValueError(f"team member intern '{name}' 已存在于 {project} repo")


def _remove_empty_stale_dir(path: str) -> bool:
    try:
        if any(os.scandir(path)):
            return False
        os.rmdir(path)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def run_list(args: argparse.Namespace) -> int:
    try:
        teams = list_teams(args.project)
    except Exception as exc:
        print(f"❌ 读取 team 列表失败: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(teams, ensure_ascii=False, indent=2))
        return 0

    if not teams:
        print("（无 workspace team）")
        return 0

    for team in teams:
        lead = team.get("team_lead", {}).get("intern_name", "-")
        workers = team.get("workers", [])
        coordinator = team.get("coordinator", {}).get("intern_name", "-")
        print(f"{team.get('team_name') or team.get('team_id')}  status={team.get('status')}  lead={lead}  workers={len(workers)}  coordinator={coordinator}")
    return 0


def run_status(args: argparse.Namespace) -> int:
    try:
        team = read_team(args.project, args.team_id)
    except Exception as exc:
        print(f"❌ 读取 team 失败: {exc}", file=sys.stderr)
        return 1

    if args.as_json:
        print(json.dumps(team, ensure_ascii=False, indent=2))
        return 0

    lead = team.get("team_lead", {}).get("intern_name", "-")
    workers = [worker.get("intern_name", "-") for worker in team.get("workers", [])]
    coordinator = team.get("coordinator", {}).get("intern_name", "-")
    print(f"Team:        {team.get('team_name') or team.get('team_id')}")
    print(f"Project:     {team.get('project')}")
    print(f"Status:      {team.get('status')}")
    print(f"Coordinator: {coordinator}")
    print(f"Team Lead:   {lead}")
    print(f"Workers:     {', '.join(workers) if workers else '-'}")
    return 0


TASK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


def run_assign_worker_task(args: argparse.Namespace) -> int:
    project = args.project
    team_id = args.team_id
    worker_name = args.worker_name
    task_id = args.task_id
    try:
        _pull_project_repo(project)
        team_data = read_team(project, team_id)
        lead_name = _resolve_team_lead_for_assignment(team_data, args.lead_name)
        _validate_worker_for_assignment(team_data, worker_name, project, team_id)
        _validate_worker_task_id(task_id)

        task_dir = Path(tasks_dir(project)) / task_id
        if task_dir.exists():
            raise ValueError(f"task '{task_id}' already exists")
        _write_worker_task_docs(
            task_dir=task_dir,
            task_id=task_id,
            title=args.title,
            background=args.background,
            goal=args.goal,
            acceptance=list(args.acceptance or []),
            details=args.details,
            lead_name=lead_name,
            worker_name=worker_name,
            team_id=team_id,
        )
        _commit_project_metadata(
            project,
            [project_metadata_rel_path(project, "tasks", task_id)],
            f"[team] create worker task {task_id}",
        )
        if not getattr(args, "no_notify", False):
            _notify_worker_to_accept_task(
                project=project,
                lead_name=lead_name,
                worker_name=worker_name,
                task_id=task_id,
            )
    except Exception as exc:
        print(f"❌ 分配 worker task 失败: {exc}", file=sys.stderr)
        return 1

    suffix = f"并通知 worker '{worker_name}' 接受" if not getattr(args, "no_notify", False) else "未发送通知"
    print(f"✅ 已创建 task '{task_id}'，{suffix}")
    return 0


def _resolve_team_lead_for_assignment(team_data: dict[str, object], lead_name: str) -> str:
    lead = team_data.get("team_lead") if isinstance(team_data.get("team_lead"), dict) else {}
    expected = str(lead.get("intern_name") or "")
    if not expected:
        raise ValueError("team metadata 缺少 team_lead.intern_name")
    if lead_name and lead_name != expected:
        raise ValueError(f"lead_name '{lead_name}' is not team lead '{expected}'")
    return expected


def _validate_worker_for_assignment(team_data: dict[str, object], worker_name: str, project: str, team_id: str) -> None:
    workers = team_data.get("workers") if isinstance(team_data.get("workers"), list) else []
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        if worker.get("status", "active") == "deleted":
            continue
        if worker.get("intern_name") != worker_name:
            continue
        validate_member_role(project, worker_name, "worker", team_id)
        return
    raise ValueError(f"worker '{worker_name}' is not an active worker of team '{team_id}'")


def _validate_worker_task_id(task_id: str) -> None:
    if not TASK_ID_PATTERN.fullmatch(task_id) or os.path.basename(task_id) != task_id or task_id in (".", ".."):
        raise ValueError(f"task_id 无效: {task_id}（必须匹配 [a-z][a-z0-9_]*）")


def _write_worker_task_docs(
    *,
    task_dir: Path,
    task_id: str,
    title: str,
    background: str,
    goal: str,
    acceptance: list[str],
    details: str,
    lead_name: str,
    worker_name: str,
    team_id: str,
) -> None:
    task_dir.mkdir(parents=True, exist_ok=False)
    acceptance_lines = "\n".join(f"- {item}" for item in acceptance)
    details_section = f"\n## 实现说明\n\n{details.strip()}\n" if details.strip() else ""
    (task_dir / "README.md").write_text(
        f"""# {task_id} - {title}

<!-- METADATA:STATUS=Open,ASSIGNEE= -->

## 背景

{background.strip()}

## 任务目标

{goal.strip()}
{details_section}
## 验收标准

{acceptance_lines}

## 分配信息

- Team：{team_id}
- Team lead：{lead_name}
- Worker：{worker_name}
- 分配方式：team_lead 创建本 task 文档后，通知 worker 接受该 task。
""",
        encoding="utf-8",
    )
    (task_dir / "history_log.md").write_text(
        f"""# {task_id} - History Log

<!-- METADATA:SESSION=0 -->

## Session 0 - {datetime.now(timezone.utc).date().isoformat()} UTC - Task created by team lead

- Team lead `{lead_name}` 为 worker `{worker_name}` 创建本 task。
- Worker 应接受本 task，按普通 task/PR 流程开发、测试、提交，并在 PR merge 后完成 task。
""",
        encoding="utf-8",
    )
    (task_dir / "task_knowledge.md").write_text(
        f"""# {task_id} - Task Knowledge

<!-- METADATA:SESSION=0 -->

## 记录规则

- 只记录本任务相关的事实、决策、踩坑和验证结果。
- 每条尽量一句话，避免重复 README 的完整内容。

## Knowledge Entries

1. 本 task 由 team_lead `{lead_name}` 创建并分配给 worker `{worker_name}`。
""",
        encoding="utf-8",
    )


def _notify_worker_to_accept_task(*, project: str, lead_name: str, worker_name: str, task_id: str) -> dict[str, object]:
    addr_path = os.environ.get("FEISHU_DAEMON_ADDR_FILE") or "/tmp/feishu_daemon.json"
    with open(addr_path, "r", encoding="utf-8") as fp:
        port = int(json.load(fp)["http_port"])
    content = (
        f"请接受 task `{task_id}`。任务细节已写入当前 workspace metadata 的 tasks/{task_id}/README.md；"
        "请按普通 task/PR 流程执行。PR merge 后按 worker merge 流程把该 task 标记为 Completed，并通过 mailbox 向 team_lead 汇报。"
    )
    body = json.dumps({
        "from_intern_name": lead_name,
        "to_intern_name": worker_name,
        "to_project": project,
        "mode": "default",
        "content": content,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/intern/peer/send",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        result = json.loads(resp.read().decode("utf-8") or "{}")
    if result.get("status") != "delivered":
        raise RuntimeError(f"peer send failed: {result}")
    return result


def run_delete(args: argparse.Namespace) -> int:
    project = args.project
    team_id = args.team_id
    force = getattr(args, "force", False) is True
    if not validate_team_name(team_id):
        print(f"❌ team_id 无效: {team_id}（必须匹配 [a-z][a-z0-9_]*）", file=sys.stderr)
        return 1

    try:
        team_data = read_team(project, team_id)
        members = _team_member_names(team_data)
        if not force:
            _assert_members_idle(project, members)
    except Exception as exc:
        print(f"❌ 删除 team 前检查失败: {exc}", file=sys.stderr)
        return 1

    if not getattr(args, "confirm", False):
        print(f"⚠️  即将删除 team '{team_id}'（project={project}）及成员 intern：")
        for member in members:
            print(f"   - {member}")
        answer = input("确认删除？(y/N) ").strip().lower()
        if answer != "y":
            print("已取消")
            return 0

    errors: list[str] = []
    deleted_members: list[str] = []
    for member in members:
        _kill_tmux_session(member, project)
        _delete_feishu_group(member, project)
        result = delete_cmd.run(argparse.Namespace(
            name=member,
            project=project,
            confirm=True,
            force=force,
        ))
        if result != 0:
            errors.append(f"delete member {member} failed")
        else:
            deleted_members.append(member)

    try:
        residual_paths = _team_member_provider_metadata_paths(project, team_id, team_data, deleted_members)
        if residual_paths:
            _remove_project_metadata(project, residual_paths, f"[team] cleanup {team_id} member metadata")
    except Exception as exc:
        errors.append(f"cleanup member metadata failed: {exc}")

    try:
        project_paths, external_paths_by_project = _remove_team_metadata(project, team_id, team_data)
        if project_paths:
            _commit_project_metadata(project, project_paths, f"[team] delete {team_id}")
        for coordinator_project, paths in external_paths_by_project.items():
            _commit_project_metadata(coordinator_project, paths, f"[team] unbind from {team_id}")
    except Exception as exc:
        errors.append(f"delete team metadata failed: {exc}")

    if errors:
        print("❌ 删除 team 未完全成功:", file=sys.stderr)
        for error in errors:
            print(f"   - {error}", file=sys.stderr)
        return 1

    print(f"✅ team '{team_id}' 已删除")
    return 0


def _team_member_names(team_data: dict[str, object]) -> list[str]:
    lead = team_data.get("team_lead") if isinstance(team_data.get("team_lead"), dict) else {}
    names = [str(lead.get("intern_name") or "")]
    workers = team_data.get("workers") if isinstance(team_data.get("workers"), list) else []
    names.extend(
        str(worker.get("intern_name") or "")
        for worker in workers
        if isinstance(worker, dict)
    )
    return [name for name in dict.fromkeys(names) if name]


def _team_member_provider_metadata_paths(
    project: str,
    team_id: str,
    team_data: dict[str, object],
    members: list[str],
) -> list[str]:
    member_set = set(members)
    paths: list[str] = []
    for member in _team_member_names(team_data):
        if member in member_set:
            paths.append(project_metadata_rel_path(project, "interns", member))

    lead = team_data.get("team_lead") if isinstance(team_data.get("team_lead"), dict) else {}
    lead_name = str(lead.get("intern_name") or "")
    if lead_name in member_set:
        paths.append(project_metadata_rel_path(project, "tasks", create_cmd.team_lead_management_task_id(team_id)))

    return list(dict.fromkeys(paths))


def _member_exists(project: str, intern_name: str) -> bool:
    return os.path.isfile(os.path.join(interns_dir(project), intern_name, "status.md"))


def _assert_members_idle(project: str, members: list[str]) -> None:
    busy: list[str] = []
    for member in members:
        status_path = os.path.join(interns_dir(project), member, "status.md")
        if not os.path.isfile(status_path):
            continue
        status = parse_status_md(status_path).get("status", "Unknown")
        if status not in ("Idle", "Unknown", ""):
            busy.append(f"{member}({status})")
    if busy:
        raise ValueError(f"team members are not Idle: {', '.join(busy)}")


def _remove_team_metadata(
    project: str,
    team_id: str,
    team_data: dict[str, object],
) -> tuple[list[str], dict[str, list[str]]]:
    changed_paths: list[str] = []
    external_paths_by_project: dict[str, list[str]] = {}

    team_rel = project_metadata_rel_path(project, "teams", team_id)
    team_dir = os.path.join(project_repo_path(project), team_rel)
    if os.path.isdir(team_dir):
        shutil.rmtree(team_dir)
        changed_paths.append(team_rel)

    for coordinator_project, coordinator_id in _candidate_coordinators_for_team(project, team_data):
        data = read_coordinator(coordinator_project, coordinator_id)
        before = json.dumps(data, sort_keys=True, ensure_ascii=False)
        _remove_team_from_coordinator(data, project, team_id)
        after = json.dumps(data, sort_keys=True, ensure_ascii=False)
        if after == before:
            continue
        write_coordinator(coordinator_project, coordinator_id, data)
        rel_path = project_metadata_rel_path(coordinator_project, "coordinators", coordinator_id, "coordinator.json")
        if coordinator_project == project:
            changed_paths.append(rel_path)
        else:
            external_paths_by_project.setdefault(coordinator_project, []).append(rel_path)

    return changed_paths, external_paths_by_project


def _candidate_coordinators_for_team(project: str, team_data: dict[str, object]) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    coordinator = team_data.get("coordinator") if isinstance(team_data.get("coordinator"), dict) else {}
    coordinator_id = str(coordinator.get("coordinator_id") or "")
    if coordinator_id:
        try:
            candidates.append((_resolve_coordinator_project(project, coordinator_id), coordinator_id))
        except Exception:
            candidates.append((project, coordinator_id))

    base = coordinators_dir(project)
    if os.path.isdir(base):
        for local_id in sorted(os.listdir(base)):
            path = os.path.join(base, local_id, "coordinator.json")
            if os.path.isfile(path):
                candidates.append((project, local_id))

    unique: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
    return unique


def _remove_team_from_coordinator(coordinator: dict[str, object], project: str, team_id: str) -> None:
    managed = coordinator.get("managed_workspaces")
    if isinstance(managed, list):
        coordinator["managed_workspaces"] = [
            entry for entry in managed
            if not (isinstance(entry, dict) and entry.get("project") == project and entry.get("team_id") == team_id)
        ]
    team_leads = coordinator.get("team_leads")
    if isinstance(team_leads, list):
        coordinator["team_leads"] = [
            entry for entry in team_leads
            if not (isinstance(entry, dict) and entry.get("project") == project and entry.get("team_id") == team_id)
        ]
    coordinator["updated_at"] = build_updated_timestamp()


def _kill_tmux_session(intern_name: str, project: str) -> None:
    try:
        tmux_session = session_cmd._resolve_tmux_session_name(intern_name, project)
    except Exception:
        return
    subprocess.run(["tmux", "kill-session", "-t", f"={tmux_session}"], capture_output=True, text=True)


def _delete_feishu_group(intern_name: str, project: str) -> None:
    addr_path = os.environ.get("FEISHU_DAEMON_ADDR_FILE") or "/tmp/feishu_daemon.json"
    try:
        with open(addr_path, "r", encoding="utf-8") as fp:
            port = int(json.load(fp)["http_port"])
        body = json.dumps({"intern_name": intern_name, "project": project}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/group/delete",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass
