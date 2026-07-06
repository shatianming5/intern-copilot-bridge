"""Hidden internctl commands used by GUI surfaces."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from lib.cli_contract import ensure_cli_report_contract
from lib.git_ops import ensure_git_identity, get_current_branch_or_none, get_default_branch, run_git
from lib.intern_registry import list_enterprise_interns
from lib.state_v1 import StateStore, workspace_metadata_checkout_path


def _setup_internal_commands(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    delete = subparsers.add_parser("task-delete")
    delete.add_argument("workspace_key")
    delete.add_argument("task_id")
    delete.add_argument("--confirm", action="store_true")
    delete.add_argument("--json", action="store_true")
    delete.set_defaults(func=run_task_delete)

    list_tasks = subparsers.add_parser("task-list")
    list_tasks.add_argument("workspace_key")
    list_tasks.add_argument("--json", action="store_true")
    list_tasks.set_defaults(func=run_task_list)


def setup_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("internal")
    internal_sub = p.add_subparsers(dest="internal_command")
    _setup_internal_commands(internal_sub)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="internctl internal")
    subparsers = parser.add_subparsers(dest="internal_command")
    _setup_internal_commands(subparsers)
    parsed = parser.parse_args(argv)
    if hasattr(parsed, "func"):
        return parsed.func(parsed)
    parser.print_help()
    return 1


@dataclass(frozen=True)
class TaskRoot:
    root: Path
    mode: str
    branch: str | None = None
    repo: Path | None = None
    repo_relative_path: str = ".intern_workspace"


def _print(data: dict, _json_output: bool) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _parse_metadata_line(line: str) -> dict[str, str] | None:
    trimmed = str(line or "").strip()
    prefix = "<!-- METADATA:"
    suffix = " -->"
    if not trimmed.startswith(prefix) or not trimmed.endswith(suffix):
        return None
    body = trimmed[len(prefix):-len(suffix)].strip()
    if not body:
        return None
    result: dict[str, str] = {}
    for part in body.split(","):
        key, sep, value = part.partition("=")
        key = key.strip()
        if sep and key:
            result[key] = value.strip()
    return result or None


def _task_metadata(task_dir: Path) -> dict[str, str]:
    readme = task_dir / "README.md"
    if not readme.exists():
        return {"status": "Open", "assignee": "", "metadata_state": "missing_readme"}
    lines = readme.read_text(encoding="utf-8").splitlines()
    metadata = _parse_metadata_line(lines[2]) if len(lines) >= 3 else None
    if not metadata or not metadata.get("STATUS"):
        return {"status": "Malformed", "assignee": "", "metadata_state": "malformed_metadata"}
    status = metadata["STATUS"].strip()
    assignee = metadata.get("ASSIGNEE", "").strip()
    if status == "Done":
        status = "Completed"
    return {"status": status, "assignee": assignee, "metadata_state": "ok"}


def _workspace_intern_status(workspace_key: str) -> dict[str, dict[str, str]]:
    statuses: dict[str, dict[str, str]] = {}
    for intern in list_enterprise_interns():
        scopes = {
            str(intern.extra.get("project") or ""),
            str(intern.extra.get("workspace_id") or ""),
        }
        if workspace_key not in scopes or not intern.task:
            continue
        statuses[intern.task] = {
            "name": intern.name,
            "status": intern.status,
            "task": intern.task,
            "pr": str(intern.extra.get("pr") or ""),
            "status_path": str(intern.extra.get("status_path") or ""),
        }
    return statuses


def _status_for_task(task_name: str, statuses: dict[str, dict[str, str]]) -> dict[str, str] | None:
    if task_name in statuses:
        return statuses[task_name]
    for current_task, status in statuses.items():
        if task_name.startswith(current_task + "_"):
            return status
    return None


def _metadata_root_from_status_path(status_path: str) -> Path | None:
    if not status_path:
        return None
    path = Path(status_path)
    if path.name != "status.md" or path.parent.parent.name != "interns":
        return None
    return path.parent.parent.parent


def _intern_task_metadata(status_item: dict[str, str], task_name: str) -> dict[str, str] | None:
    metadata_root = _metadata_root_from_status_path(status_item.get("status_path", ""))
    if not metadata_root:
        return None
    candidates: list[str] = []
    for candidate in (task_name, status_item.get("task", "")):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    for candidate in candidates:
        task_dir = metadata_root / "tasks" / candidate
        if (task_dir / "README.md").exists():
            metadata = _task_metadata(task_dir)
            metadata["path"] = str(task_dir)
            metadata["readme_path"] = str(task_dir / "README.md")
            return metadata
    return None


def _task_roots(store: StateStore, workspace_key: str) -> list[TaskRoot]:
    ref = store.load_workspace(workspace_key)
    workspace = ref.data
    metadata = workspace.get("metadata", {}) if isinstance(workspace.get("metadata"), dict) else {}
    mode = metadata.get("mode")
    if mode not in ("repo_dotdir", "metadata_branch", "local_only"):
        raise RuntimeError(f"workspace {workspace_key} has invalid metadata mode: {mode or '<empty>'}")
    if mode == "metadata_branch":
        return [TaskRoot(
            root=workspace_metadata_checkout_path(store.work_root, ref.workspace_key) / ".intern_workspace" / "tasks",
            mode="metadata_branch",
            branch=str(metadata.get("branch") or "intern_workspace"),
        )]
    if mode == "local_only":
        local_path = metadata.get("local_path") or ""
        if not local_path or not Path(str(local_path)).is_absolute():
            raise RuntimeError(f"workspace {workspace_key} missing absolute local_only metadata path")
        return [TaskRoot(root=Path(local_path) / "tasks", mode="local_only")]
    repo = workspace.get("local_path") or ""
    if not repo or not Path(str(repo)).is_absolute():
        raise RuntimeError(f"workspace {workspace_key} missing absolute local_path for repo_dotdir")
    repo_relative_path = str(metadata.get("repo_relative_path") or ".intern_workspace")
    return [TaskRoot(
        root=Path(repo) / repo_relative_path / "tasks",
        mode="repo_dotdir",
        repo=Path(repo),
        repo_relative_path=repo_relative_path,
    )]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def _metadata_repo_for_root(root: Path) -> Path | None:
    current = root
    while current != current.parent:
        if (current / ".git").exists():
            top = _git(current, "rev-parse", "--show-toplevel")
            if top.returncode == 0 and top.stdout.strip():
                return Path(top.stdout.strip())
        current = current.parent
    return None


def _remote_tree_has_path(repo: Path, remote_ref: str, rel_path: str) -> bool:
    result = run_git(["cat-file", "-e", f"{remote_ref}:{rel_path}"], cwd=str(repo), check=False)
    return result.returncode == 0


def _sync_repo_dotdir_metadata(spec: TaskRoot, workspace_key: str) -> None:
    if spec.mode != "repo_dotdir" or spec.repo is None:
        return
    repo = spec.repo
    if not (repo / ".git").exists():
        return

    branch = get_default_branch(str(repo))
    metadata_paths = [
        f"{spec.repo_relative_path}/tasks",
        f"{spec.repo_relative_path}/interns",
    ]
    dirty = run_git(
        ["status", "--porcelain", "--untracked-files=no", "--", *metadata_paths],
        cwd=str(repo),
        check=False,
    ).stdout.strip()
    if dirty:
        raise RuntimeError(
            "repo_dotdir metadata sync blocked by local tracked metadata changes "
            f"for workspace {workspace_key} at {repo}: {dirty}"
        )

    run_git(["fetch", "origin", branch], cwd=str(repo))
    remote_ref = f"refs/remotes/origin/{branch}"
    existing_paths = [
        rel_path
        for rel_path in metadata_paths
        if _remote_tree_has_path(repo, remote_ref, rel_path)
    ]
    if not existing_paths:
        return

    run_git(["restore", "--source", remote_ref, "--worktree", "--", *existing_paths], cwd=str(repo))


def _error_report(command: str, message: str, *, json_output: bool, **extra: str) -> None:
    report = ensure_cli_report_contract(
        {"ok": False, "error": message, "message": message, **extra},
        ok=False,
        command=command,
        default_next_action="Fix the task metadata path, branch state, or git remote, then rerun the command.",
    )
    if json_output:
        _print(report, True)
    else:
        print(message, file=sys.stderr)


def _prepare_repo_for_delete(repo: Path, mode: str, branch: str | None) -> str | None:
    if mode == "repo_dotdir":
        default_branch = get_default_branch(str(repo))
        current_branch = get_current_branch_or_none(str(repo))
        if current_branch != default_branch:
            raise RuntimeError(
                f"deleting a task requires metadata repo {repo} to be on {default_branch}; current branch: {current_branch or 'DETACHED'}"
            )
        run_git(["pull", "--rebase", "--autostash", "origin", default_branch], cwd=str(repo))
        return default_branch
    if mode == "metadata_branch" and branch:
        run_git(["pull", "--rebase", "--autostash", "origin", branch], cwd=str(repo), check=False)
        return branch
    return branch


def run_task_list(args: argparse.Namespace) -> int:
    try:
        store = StateStore()
        intern_status = _workspace_intern_status(args.workspace_key)
        tasks: dict[str, dict[str, str]] = {}
        malformed_tasks: list[dict[str, str]] = []
        for spec in _task_roots(store, args.workspace_key):
            _sync_repo_dotdir_metadata(spec, args.workspace_key)
            if not spec.root.exists():
                continue
            for task_dir in sorted(item for item in spec.root.iterdir() if item.is_dir()):
                meta = _task_metadata(task_dir)
                if meta["status"] == "Malformed":
                    malformed_tasks.append({
                        "name": task_dir.name,
                        "path": str(task_dir),
                        "readme_path": str(task_dir / "README.md"),
                        "reason": meta.get("metadata_state", "malformed_metadata"),
                    })
                    continue
                task = {
                    "name": task_dir.name,
                    "status": meta["status"],
                    "assignee": meta["assignee"],
                    "path": str(task_dir),
                    "readme_path": str(task_dir / "README.md"),
                    "pr": "",
                }
                status_item = _status_for_task(task_dir.name, intern_status)
                if status_item:
                    branch_meta = _intern_task_metadata(status_item, task_dir.name)
                    if branch_meta and branch_meta["status"] == "Malformed":
                        malformed_tasks.append({
                            "name": task_dir.name,
                            "path": branch_meta.get("path") or str(task_dir),
                            "readme_path": branch_meta.get("readme_path") or str(task_dir / "README.md"),
                            "reason": branch_meta.get("metadata_state", "malformed_metadata"),
                        })
                        continue
                    if branch_meta:
                        task["status"] = branch_meta["status"]
                        task["assignee"] = branch_meta["assignee"] or status_item.get("name") or task["assignee"]
                        task["path"] = branch_meta["path"]
                        task["readme_path"] = branch_meta["readme_path"]
                    else:
                        task["assignee"] = status_item.get("name") or task["assignee"]
                    if status_item.get("status") == "Working" and task["status"] == "Open":
                        task["status"] = "InProgress"
                    task["pr"] = status_item.get("pr") or ""
                tasks[task_dir.name] = task
    except Exception as exc:
        print(f"internal task-list failed: {exc}", file=sys.stderr)
        return 1
    _print({"workspace_key": args.workspace_key, "tasks": list(tasks.values()), "malformed_tasks": malformed_tasks}, args.json)
    return 0


def run_task_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("refusing to delete task without --confirm", file=sys.stderr)
        return 1
    try:
        store = StateStore()
        task_specs = [(spec.root / args.task_id, spec) for spec in _task_roots(store, args.workspace_key)]
        existing = [(task_dir, spec) for task_dir, spec in task_specs if task_dir.exists()]
        if not existing:
            _error_report(
                "internal task-delete",
                "TASK_NOT_FOUND",
                json_output=args.json,
                workspace_key=args.workspace_key,
                task_id=args.task_id,
            )
            return 1
        for task_dir, _spec in existing:
            if _task_metadata(task_dir)["status"] == "InProgress":
                _error_report(
                    "internal task-delete",
                    "TASK_IN_PROGRESS",
                    json_output=args.json,
                    workspace_key=args.workspace_key,
                    task_id=args.task_id,
                )
                return 1
        changed_repos: dict[Path, str | None] = {}
        deleted: list[str] = []
        for task_dir, spec in existing:
            repo = _metadata_repo_for_root(task_dir)
            if repo:
                push_branch = _prepare_repo_for_delete(repo, spec.mode, spec.branch)
                rel = task_dir.relative_to(repo)
                result = run_git(["rm", "-rf", "--", str(rel)], cwd=str(repo), check=False)
                if result.returncode != 0 and task_dir.exists():
                    shutil.rmtree(task_dir, ignore_errors=True)
                    run_git(["add", "-A", "--", str(rel)], cwd=str(repo))
                changed_repos[repo] = push_branch
            else:
                shutil.rmtree(task_dir, ignore_errors=True)
            deleted.append(str(task_dir))
        commits: dict[str, str] = {}
        branches: dict[str, str] = {}
        for repo, branch in changed_repos.items():
            diff = run_git(["diff", "--cached", "--quiet"], cwd=str(repo), check=False)
            if branch:
                branches[str(repo)] = branch
            if diff.returncode != 0:
                ensure_git_identity(str(repo))
                run_git(["commit", "-m", f"[task] delete {args.task_id}"], cwd=str(repo))
                commit = run_git(["rev-parse", "--short", "HEAD"], cwd=str(repo)).stdout.strip()
                if branch:
                    run_git(["push", "origin", branch], cwd=str(repo))
                commits[str(repo)] = commit
    except Exception as exc:
        _error_report(
            "internal task-delete",
            f"internal task-delete failed: {exc}",
            json_output=getattr(args, "json", False),
            workspace_key=getattr(args, "workspace_key", ""),
            task_id=getattr(args, "task_id", ""),
        )
        return 1
    _print({
        "ok": True,
        "workspace_key": args.workspace_key,
        "task_id": args.task_id,
        "deleted": deleted,
        "commits": commits,
        "branches": branches,
    }, args.json)
    return 0
