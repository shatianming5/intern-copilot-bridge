"""internctl delete <name> [--confirm] — 删除 intern。"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request

from lib.intern_registry import (
    WORK_AGENTS_ROOT,
    parse_status_md,
    validate_name,
)
from lib.git_ops import remove_and_push, run_git
from lib.enterprise_policy import enterprise_policy_exists
from lib.enterprise_state_v1 import intern_runtime_dir
from lib.metadata_checkout import ensure_metadata_branch_checkout
from lib.tmux_session import scoped_tmux_session_name
from commands.create import (
    _contract_checkout_root,
    _find_workspace_id_for_project,
    _relative_paths_under,
    _require_contract_path,
    _session_registry_key,
)
from commands.metadata import resolve_metadata_for_workspace_id


def setup_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """注册 delete 子命令。"""
    p = subparsers.add_parser("delete", help="删除 intern")
    p.add_argument("name", help="intern 名称")
    p.add_argument("--project", default="axis_intern_agents", help="项目名称（默认 axis_intern_agents）")
    p.add_argument("--confirm", action="store_true", help="跳过交互确认")
    p.add_argument("--force", action="store_true", help="强制删除非 Idle intern；仅用于 create 失败回滚")
    p.set_defaults(func=run)


def _enterprise_intern_root(workspace_id: str, name: str) -> str:
    return os.fspath(intern_runtime_dir(WORK_AGENTS_ROOT, workspace_id, name))


def _remove_enterprise_metadata(contract: dict, paths: list[str], *, name: str) -> None:
    root = _contract_checkout_root(contract)
    if root is None:
        for path_value in sorted(set(paths), key=len, reverse=True):
            if os.path.isdir(path_value):
                shutil.rmtree(path_value)
            elif os.path.exists(path_value):
                os.remove(path_value)
        return
    existing = [p for p in paths if os.path.exists(p)]
    if not existing:
        return
    rels = _relative_paths_under(root, existing)
    if not os.path.isdir(os.path.join(root, ".git")):
        raise RuntimeError(f"metadata checkout is not a git repo: {root}")
    if contract.get("metadata_mode") == "metadata_branch":
        ensure_metadata_branch_checkout(
            contract,
            workspace_id=str(contract.get("workspace_id") or ""),
            checkout_path=root,
            branch=str(contract.get("metadata_branch") or ""),
        )
    push_metadata = not (
        contract.get("metadata_mode") == "repo_dotdir"
        and contract.get("repo_provider") == "local"
        and not run_git(["config", "--get", "remote.origin.url"], cwd=root, check=False).stdout.strip()
    )
    remove_and_push(
        repo_path=root,
        paths=rels,
        message=f"[{name}] intern: 删除",
        branch=contract.get("metadata_branch") or None,
        push=push_metadata,
    )


def _clear_session_registry(*, name: str, project: str, workspace_id: str) -> None:
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    if not os.path.isfile(sessions_file):
        return
    sessions = _load_session_registry()
    exact_key = _session_registry_key(name, project, workspace_id)
    removed: list[str] = []
    for key, entry in list(sessions.items()):
        if not isinstance(entry, dict):
            continue
        if _registry_entry_matches(
            key=str(key),
            entry=entry,
            name=name,
            project=project,
            workspace_id=workspace_id,
            exact_key=exact_key,
        ):
            sessions.pop(key, None)
            removed.append(str(key))
    if removed:
        tmp = sessions_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, sessions_file)
        print(f"🗑️  已删除 session registry: {', '.join(removed)}")


def _load_session_registry() -> dict:
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    if not os.path.isfile(sessions_file):
        return {}
    with open(sessions_file, "r", encoding="utf-8") as f:
        sessions = json.load(f)
    if not isinstance(sessions, dict):
        raise RuntimeError(f"session registry must be a JSON object: {sessions_file}")
    return sessions


def _delete_feishu_group(*, name: str, project: str) -> None:
    addr_path = os.environ.get("FEISHU_DAEMON_ADDR_FILE") or "/tmp/feishu_daemon.json"
    try:
        with open(addr_path, "r", encoding="utf-8") as fp:
            port = int(json.load(fp)["http_port"])
        body = json.dumps({"intern_name": name, "project": project}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/group/delete",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()
        print(f"🗑️  已删除飞书群/relay registry: {project}:{name}")
    except Exception as e:
        print(f"⚠️  飞书群/relay registry 清理失败: {e}", file=sys.stderr)


def _remove_runtime_dir(path: str) -> None:
    if not os.path.isdir(path):
        return
    shutil.rmtree(path, ignore_errors=True)
    if os.path.exists(path):
        raise RuntimeError(f"runtime dir still exists after removal: {path}")
    print(f"🗑️  已删除本地目录: {path}")


def _registry_entry_name(key: str, entry: dict) -> str:
    return str(entry.get("intern_name") or key.split(":", 1)[-1])


def _registry_entry_scopes(key: str, entry: dict) -> set[str]:
    scopes = {
        str(entry.get("project") or ""),
        str(entry.get("workspace_id") or ""),
    }
    if ":" in key:
        scopes.add(key.split(":", 1)[0])
    return {scope for scope in scopes if scope}


def _registry_entry_matches(*, key: str, entry: dict, name: str, project: str, workspace_id: str, exact_key: str) -> bool:
    if key == exact_key:
        return True
    if _registry_entry_name(key, entry) != name:
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
    if entry_intern_dir:
        target_dir = _enterprise_intern_root(workspace_id, name)
        if os.path.abspath(entry_intern_dir) == os.path.abspath(target_dir):
            return True
    scopes = _registry_entry_scopes(key, entry)
    return bool(scopes.intersection({scope for scope in (project, workspace_id) if scope}))


def _unique_tmux_candidates(candidates: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        session_name = str(candidate or "").strip()
        if not session_name or session_name in seen:
            continue
        seen.add(session_name)
        unique.append(session_name)
    return unique


def _target_session_registry_entries(
    *,
    name: str,
    project: str,
    workspace_id: str,
    sessions: dict,
) -> list[tuple[str, dict]]:
    exact_key = _session_registry_key(name, project, workspace_id)
    entries: list[tuple[str, dict]] = []
    for key, entry in sessions.items():
        if not isinstance(entry, dict):
            continue
        if _registry_entry_matches(
            key=str(key),
            entry=entry,
            name=name,
            project=project,
            workspace_id=workspace_id,
            exact_key=exact_key,
        ):
            entries.append((str(key), dict(entry)))
    return entries


def _runtime_metadata_contract(*, name: str, project: str, workspace_id: str) -> dict | None:
    sessions = _load_session_registry()
    entries = _target_session_registry_entries(
        name=name,
        project=project,
        workspace_id=workspace_id,
        sessions=sessions,
    )
    for _key, entry in entries:
        intern_dir = str(entry.get("intern_dir") or "")
        if not intern_dir:
            continue
        state_path = os.path.join(intern_dir, ".hook_state.json")
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                state = json.load(f)
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        if not isinstance(state, dict):
            continue
        state_project = str(state.get("project") or "")
        if state_project and state_project != project:
            continue
        state_workspace_id = str(state.get("workspace_id") or "")
        if state_workspace_id and state_workspace_id != workspace_id:
            continue
        resolver = state.get("metadata_resolver")
        if not isinstance(resolver, dict):
            continue
        resolver_name = str(resolver.get("intern_name") or "")
        if resolver_name and resolver_name != name:
            continue
        resolver_workspace_id = str(resolver.get("workspace_id") or "")
        if resolver_workspace_id and resolver_workspace_id != workspace_id:
            continue
        status_path = str(resolver.get("status_path") or "")
        knowledge_path = str(resolver.get("knowledge_path") or "")
        if status_path and knowledge_path:
            return dict(resolver)
    return None


def _tmux_session_candidates(*, name: str, project: str, workspace_id: str) -> list[str]:
    sessions = _load_session_registry()
    entries = _target_session_registry_entries(
        name=name,
        project=project,
        workspace_id=workspace_id,
        sessions=sessions,
    )
    candidates: list[str] = []
    for key, entry in entries:
        explicit = str(entry.get("tmux_session") or "").strip()
        if explicit:
            candidates.append(explicit)
        elif key == name:
            candidates.append(name)
        intern_dir = str(entry.get("intern_dir") or "")
        if intern_dir:
            candidates.append(scoped_tmux_session_name(
                name,
                project=str(entry.get("project") or project or ""),
                workspace_id=str(entry.get("workspace_id") or workspace_id or ""),
                intern_dir=intern_dir,
            ))

    candidates.append(scoped_tmux_session_name(
        name,
        project=project,
        workspace_id=workspace_id,
        intern_dir=_enterprise_intern_root(workspace_id, name),
    ))

    return _unique_tmux_candidates(candidates)


def _tmux_session_running(session_name: str) -> bool:
    try:
        result = subprocess.run(["tmux", "has-session", "-t", f"={session_name}"], capture_output=True)
    except FileNotFoundError:
        return False
    return result.returncode == 0


def _kill_tmux_session(session_name: str) -> tuple[bool, bool, str]:
    try:
        result = subprocess.run(["tmux", "kill-session", "-t", f"={session_name}"], capture_output=True, text=True)
    except FileNotFoundError:
        return True, False, ""
    except Exception as exc:
        return False, False, str(exc)
    if result.returncode == 0:
        return True, True, ""
    if not _tmux_session_running(session_name):
        return True, False, ""
    detail = result.stderr.strip() or result.stdout.strip() or "tmux kill-session failed"
    return False, False, detail


def _kill_owned_tmux_sessions(*, name: str, project: str, workspace_id: str) -> None:
    errors: list[str] = []
    killed: list[str] = []
    for session_name in _tmux_session_candidates(name=name, project=project, workspace_id=workspace_id):
        ok, did_kill, detail = _kill_tmux_session(session_name)
        if did_kill:
            killed.append(session_name)
        if not ok:
            errors.append(f"{session_name}: {detail}")
    if errors:
        raise RuntimeError("; ".join(errors))
    if killed:
        print(f"🗑️  已终止 tmux session: {', '.join(killed)}")


def _run_enterprise_delete(args: argparse.Namespace, *, name: str, project: str, workspace_id: str, force: bool) -> int:
    contract_error: Exception | None = None
    try:
        contract = resolve_metadata_for_workspace_id(workspace_id, name, "")
    except Exception as exc:
        contract_error = exc
        contract = _runtime_metadata_contract(name=name, project=project, workspace_id=workspace_id)
        if contract is None:
            if force:
                print(f"⚠️  无法解析企业 metadata contract: {exc}", file=sys.stderr)
                print("⚠️  --force 已指定，继续清理 tmux、本地目录、session registry 和飞书群", file=sys.stderr)
                try:
                    _kill_owned_tmux_sessions(name=name, project=project, workspace_id=workspace_id)
                except Exception as cleanup_exc:
                    print(f"❌ tmux/session 清理失败: {cleanup_exc}", file=sys.stderr)
                    return 1
                intern_root = _enterprise_intern_root(workspace_id, name)
                _remove_runtime_dir(intern_root)
                try:
                    _clear_session_registry(name=name, project=project, workspace_id=workspace_id)
                except Exception as cleanup_exc:
                    print(f"❌ session registry 清理失败: {cleanup_exc}", file=sys.stderr)
                    return 1
                _delete_feishu_group(name=name, project=project)
                print(f"\n✅ intern '{name}' 已删除")
                return 0
            print(f"❌ 无法解析企业 metadata contract: {exc}", file=sys.stderr)
            return 1

    runtime_contract = _runtime_metadata_contract(name=name, project=project, workspace_id=workspace_id)
    if runtime_contract is not None:
        runtime_status_path = str(runtime_contract.get("status_path") or "")
        if os.path.exists(runtime_status_path):
            contract = runtime_contract

    try:
        status_path = _require_contract_path(contract, "status_path")
        knowledge_path = _require_contract_path(contract, "knowledge_path")
    except Exception as exc:
        if force:
            if contract_error is not None:
                print(f"⚠️  无法解析企业 metadata contract: {contract_error}", file=sys.stderr)
            else:
                print(f"⚠️  无法解析企业 metadata contract: {exc}", file=sys.stderr)
            print("⚠️  --force 已指定，继续清理 tmux、本地目录、session registry 和飞书群", file=sys.stderr)
            try:
                _kill_owned_tmux_sessions(name=name, project=project, workspace_id=workspace_id)
            except Exception as cleanup_exc:
                print(f"❌ tmux/session 清理失败: {cleanup_exc}", file=sys.stderr)
                return 1
            intern_root = _enterprise_intern_root(workspace_id, name)
            _remove_runtime_dir(intern_root)
            try:
                _clear_session_registry(name=name, project=project, workspace_id=workspace_id)
            except Exception as cleanup_exc:
                print(f"❌ session registry 清理失败: {cleanup_exc}", file=sys.stderr)
                return 1
            _delete_feishu_group(name=name, project=project)
            print(f"\n✅ intern '{name}' 已删除")
            return 0
        print(f"❌ 无法解析企业 metadata contract: {exc}", file=sys.stderr)
        return 1

    if not os.path.exists(status_path):
        if not force:
            print(
                f"❌ intern '{name}' metadata 不存在（project={project}, workspace={workspace_id}）。"
                "如需清理残留运行态，请使用 --force",
                file=sys.stderr,
            )
            return 1
        print(f"⚠️  intern '{name}' metadata 不存在，继续清理本地目录", file=sys.stderr)

    meta = parse_status_md(status_path)
    status = meta.get("status", "Unknown")
    if os.path.exists(status_path) and status not in ("Idle", "Unknown", "") and not force:
        print(
            f"❌ intern '{name}' 正在工作中（status={status}），"
            f"请先停止任务再删除",
            file=sys.stderr,
        )
        return 1

    intern_root = _enterprise_intern_root(workspace_id, name)
    task_id = meta.get("task", "")
    delete_paths = [os.path.dirname(status_path)]
    task_contract = contract
    if task_id:
        try:
            task_contract = resolve_metadata_for_workspace_id(workspace_id, name, task_id)
            readme_path = _require_contract_path(task_contract, "task_readme_path")
            delete_paths.append(os.path.dirname(readme_path))
        except Exception as exc:
            print(f"⚠️  无法解析 task metadata contract，跳过 task metadata 删除: {exc}", file=sys.stderr)

    if not args.confirm:
        print(f"⚠️  即将删除 intern '{name}'（project={project}, workspace={workspace_id}）：")
        print(f"   - metadata {status_path}")
        if task_id:
            print(f"   - task metadata {task_id}")
        print(f"   - 本地目录 {intern_root}/")
        answer = input("确认删除？(y/N) ").strip().lower()
        if answer != "y":
            print("已取消")
            return 0

    try:
        _kill_owned_tmux_sessions(name=name, project=project, workspace_id=workspace_id)
    except Exception as e:
        print(f"❌ tmux/session 清理失败: {e}", file=sys.stderr)
        return 1

    try:
        _remove_enterprise_metadata(task_contract, delete_paths, name=name)
        print("🗑️  已删除企业 metadata")
    except Exception as e:
        print(f"⚠️  企业 metadata 删除失败: {e}", file=sys.stderr)
        if not force:
            return 1
        print("⚠️  --force 已指定，继续清理本地目录和 session registry", file=sys.stderr)

    if os.path.isdir(intern_root):
        _remove_runtime_dir(intern_root)

    try:
        _clear_session_registry(name=name, project=project, workspace_id=workspace_id)
    except Exception as e:
        print(f"❌ session registry 清理失败: {e}", file=sys.stderr)
        return 1
    _delete_feishu_group(name=name, project=project)

    print(f"\n✅ intern '{name}' 已删除")
    return 0


def run(args: argparse.Namespace) -> int:
    """执行 delete 命令。"""
    name: str = args.name
    project: str = args.project
    force = getattr(args, "force", False) is True

    if not validate_name(name):
        print(f"❌ 名称无效: '{name}'", file=sys.stderr)
        return 1

    if not enterprise_policy_exists(WORK_AGENTS_ROOT):
        print("❌ delete requires enterprise policy/state-v1 workspace", file=sys.stderr)
        return 1

    workspace_id = _find_workspace_id_for_project(project)
    if not workspace_id:
        print(f"❌ workspace not found for project: {project}", file=sys.stderr)
        return 1
    return _run_enterprise_delete(args, name=name, project=project, workspace_id=workspace_id, force=force)
