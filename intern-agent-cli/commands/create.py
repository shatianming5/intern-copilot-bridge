"""internctl create <name> --project <project> — 创建企业版 intern。"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
import urllib.error
import urllib.request
from pathlib import Path

from lib.intern_registry import (
    DEFAULT_INTERN_ROLE,
    INTERN_ROLES,
    WORK_AGENTS_ROOT,
    validate_new_name,
)
from lib.team_registry import team_lead_management_task_id, validate_team_name
from lib.git_ops import clone, add_commit_push, remove_and_push
from lib.enterprise_policy import enterprise_policy_exists
from lib.enterprise_paths import daemon_owner_path
from lib.enterprise_state_v1 import intern_runtime_dir
from lib.metadata_checkout import ensure_metadata_branch_checkout
from commands import workspace as workspace_cmd
from commands.metadata import bind_repo_dotdir_metadata_to_code_repo, resolve_metadata_for_workspace_id

DEFAULT_REPO_URL: str = "git@codeup.aliyun.com:finalsystems/chlxydl/axis_intern_agents.git"
COORDINATOR_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]*$")
DEFAULT_COORDINATOR_STANDING_GOAL = "持续承接用户需求，创建和管理 team_lead，跨 workspace 分配任务并跟进结果。"
GROUP_CREATE_TIMEOUT_SECONDS = 60
GROUP_CREATE_ATTEMPTS = 2


def _read_locale() -> str:
    """读取插件持久化的 locale；zh-cn / en。默认 zh-cn。"""
    try:
        v = (Path.home() / ".config" / "intern_agents" / "locale").read_text(encoding="utf-8").strip().lower()
        if v in ("zh-cn", "en"):
            return v
    except Exception:
        pass
    return "zh-cn"


def _default_coordinator_id(name: str) -> str:
    suffix = name[len("intern_"):] if name.startswith("intern_") else name
    return f"coord_{suffix}"


def _new_coordinator_task_id(name: str) -> str:
    suffix = name[len("intern_"):] if name.startswith("intern_") else name
    return f"task_coordinator_{suffix}_{uuid.uuid4().hex[:8]}"


def _find_workspace_for_project(project: str) -> dict:
    status, body = workspace_cmd._request("GET", "/api/workspaces")
    if status >= 400:
        raise RuntimeError(body.get("error") or f"daemon returned HTTP {status}")
    matches = []
    for item in body.get("workspaces", []):
        if not isinstance(item, dict):
            continue
        candidates = {
            str(item.get("workspace_id") or ""),
            str(item.get("project_id") or ""),
            str(item.get("display_name") or ""),
            str(item.get("name") or ""),
        }
        if project in candidates:
            matches.append(item)
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one workspace for project {project!r}, found {len(matches)}")
    workspace_id = matches[0].get("workspace_id")
    if not workspace_id:
        raise RuntimeError(f"workspace for project {project!r} missing workspace_id")
    return dict(matches[0])


def _find_workspace_id_for_project(project: str) -> str:
    return str(_find_workspace_for_project(project)["workspace_id"])


def _resolve_enterprise_metadata_contract(name: str, project: str, task_id: str = "") -> dict:
    workspace_id = _find_workspace_id_for_project(project)
    return resolve_metadata_for_workspace_id(workspace_id, name, task_id)


def _enterprise_intern_root(workspace_id: str, name: str) -> str:
    return os.fspath(intern_runtime_dir(WORK_AGENTS_ROOT, workspace_id, name))


def _session_registry_key(name: str, project: str, workspace_id: str = "") -> str:
    return f"{workspace_id or project}:{name}"


def _write_text_file(path_value: str, content: str) -> None:
    os.makedirs(os.path.dirname(path_value), exist_ok=True)
    with open(path_value, "w", encoding="utf-8") as f:
        f.write(content)


def _status_content(name: str, initial_status: str, initial_task: str, intern_role: str, team_id: str, locale: str) -> str:
    if locale == "en":
        return (
            f"# {name} - status\n\n"
            f"<!-- METADATA:STATUS={initial_status},TASK={initial_task},ROLE={intern_role},TEAM_ID={team_id} -->\n\n"
            f"| Field | Value |\n|------|-----|\n"
            f"| Name | {name} |\n"
            f"| Status | {initial_status} |\n"
            f"| Role | {intern_role} |\n"
            f"| Team | {team_id or 'N/A'} |\n"
            f"| Current Task | {initial_task} |\n| PR | N/A |\n| Session | 0 |\n"
        )
    return (
        f"# {name} - 状态\n\n"
        f"<!-- METADATA:STATUS={initial_status},TASK={initial_task},ROLE={intern_role},TEAM_ID={team_id} -->\n\n"
        f"| 字段 | 值 |\n|------|-----|\n"
        f"| Name | {name} |\n"
        f"| Status | {initial_status} |\n"
        f"| Role | {intern_role} |\n"
        f"| Team | {team_id or 'N/A'} |\n"
        f"| Current Task | {initial_task} |\n| PR | N/A |\n| Session | 0 |\n"
    )


def _knowledge_content(name: str, locale: str) -> str:
    if locale == "en":
        return (
            f"# {name} - personal knowledge base\n\n"
            f"<!-- METADATA:SESSION=0 -->\n\n---\n\n## Knowledge entries\n\n"
        )
    return (
        f"# {name} - 个人知识库\n\n"
        f"<!-- METADATA:SESSION=0 -->\n\n---\n\n## 知识条目\n\n"
    )


def _require_contract_path(contract: dict, key: str) -> str:
    value = contract.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"metadata resolver missing {key}")
    if not os.path.isabs(value):
        raise RuntimeError(f"metadata resolver {key} must be absolute: {value}")
    return value


def _write_enterprise_task_metadata(contract: dict, *, name: str, task_id: str, kind: str, team_id: str = "") -> list[str]:
    readme_path = _require_contract_path(contract, "task_readme_path")
    history_path = _require_contract_path(contract, "history_log_path")
    knowledge_path = _require_contract_path(contract, "task_knowledge_path")
    if kind == "team_lead":
        title = f"# {task_id} - team lead manage team 常驻任务\n\n"
        body = (
            "<!-- METADATA:STATUS=InProgress,ASSIGNEE={name} -->\n\n"
            "## 背景\n\n"
            "这是 team lead 创建时自动生成的 workspace team 长期管理任务。\n\n"
            "## 任务目标\n\n"
            f"- 持续管理 team `{team_id}`。\n"
            "- 接收 coordinator/用户目标，拆解任务，分配 worker，跟进执行、review 结果并汇总交付。\n\n"
        ).format(name=name)
    else:
        title = f"# {task_id} - coordinator 永续管理任务\n\n"
        body = (
            f"<!-- METADATA:STATUS=InProgress,ASSIGNEE={name} -->\n\n"
            "## 背景\n\n"
            "这是 coordinator 创建时自动生成的用户级长期管理任务。\n\n"
            "## 生命周期规则\n\n"
            "- 只要对应 coordinator 存在，本任务必须保持 InProgress。\n"
        )
    _write_text_file(readme_path, title + body)
    _write_text_file(
        history_path,
        f"# {task_id} - History Log\n\n<!-- METADATA:SESSION=0 -->\n\n## Session 0 - Created with {kind}\n\n- 自动生成本常驻任务。\n",
    )
    _write_text_file(
        knowledge_path,
        f"# {task_id} - Task Knowledge\n\n<!-- METADATA:SESSION=0 -->\n\n## Knowledge Entries\n\n1. 本任务是 {kind} 生命周期任务。\n",
    )
    return [readme_path, history_path, knowledge_path]


def _coordinator_metadata_path(contract: dict, coordinator_id: str) -> str:
    metadata_root = _require_contract_path(contract, "metadata_root")
    return os.path.join(metadata_root, "coordinators", coordinator_id, "coordinator.json")


def _read_daemon_owner_identity() -> dict:
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


def _write_coordinator_metadata(
    contract: dict,
    *,
    project: str,
    name: str,
    coordinator_id: str,
    task_id: str,
    standing_goal: str,
    owner: dict,
) -> str:
    from lib.team_registry import utc_now
    created_at = utc_now()
    repo_path = str(contract.get("code_worktree_path") or contract.get("code_repo_path") or "")
    data = {
        "schema_version": 1,
        "scope": "user",
        "role": "coordinator",
        "coordinator_id": coordinator_id,
        "intern_name": name,
        "owner": owner,
        "anchor": {
            "project": project,
            "repo_path": repo_path,
            "metadata_path": os.path.relpath(
                _coordinator_metadata_path(contract, coordinator_id),
                _require_contract_path(contract, "metadata_root"),
            ),
        },
        "standing_goal": {
            "enabled": True,
            "objective": standing_goal,
            "policy": "never_idle_unless_user_stops",
        },
        "coordinator_task": {
            "task_id": task_id,
            "status": "InProgress",
            "lifecycle": "exists_while_coordinator_exists",
            "completion_policy": "never_complete_while_coordinator_exists",
        },
        "managed_workspaces": [],
        "team_leads": [],
        "created_at": created_at,
        "updated_at": created_at,
    }
    path = _coordinator_metadata_path(contract, coordinator_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
        fp.write("\n")
    os.replace(tmp, path)
    return path


def _contract_checkout_root(contract: dict) -> str | None:
    mode = contract.get("metadata_mode")
    if mode == "local_only":
        return None
    root = contract.get("metadata_checkout_path") or contract.get("code_repo_path") or ""
    if not isinstance(root, str) or not root:
        raise RuntimeError(f"metadata resolver missing checkout root for {mode} mode")
    if not os.path.isabs(root):
        raise RuntimeError(f"metadata checkout root must be absolute: {root}")
    return root


def _relative_paths_under(root: str, paths: list[str]) -> list[str]:
    root_abs = os.path.abspath(root)
    rels: list[str] = []
    for value in paths:
        path_abs = os.path.abspath(value)
        try:
            common = os.path.commonpath([root_abs, path_abs])
        except ValueError as exc:
            raise RuntimeError(f"metadata path is outside checkout root: {value}") from exc
        if common != root_abs:
            raise RuntimeError(f"metadata path is outside checkout root: {value}")
        rels.append(os.path.relpath(path_abs, root_abs))
    return sorted(set(rels))


def _commit_enterprise_metadata(contract: dict, paths: list[str], *, name: str, action: str) -> None:
    root = _contract_checkout_root(contract)
    if root is None:
        return
    rels = _relative_paths_under(root, paths)
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
    )
    add_commit_push(
        repo_path=root,
        paths=rels,
        message=f"[{name}] intern: {action}",
        branch=contract.get("metadata_branch") or None,
        push=push_metadata,
    )


def _daemon_http_port() -> int:
    addr_path = os.environ.get("FEISHU_DAEMON_ADDR_FILE") or "/tmp/feishu_daemon.json"
    with open(addr_path, "r", encoding="utf-8") as fp:
        payload = json.load(fp)
    port = int(payload["http_port"])
    if port <= 0:
        raise RuntimeError(f"invalid daemon http_port in {addr_path}")
    return port


def _post_daemon_json(endpoint: str, payload: dict, *, timeout: int = GROUP_CREATE_TIMEOUT_SECONDS) -> dict:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{_daemon_http_port()}{endpoint}",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8").strip()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"daemon {endpoint} HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        raise RuntimeError(f"daemon {endpoint} request failed: {exc}") from exc


def _post_daemon_json_with_retry(endpoint: str, payload: dict, *, attempts: int = GROUP_CREATE_ATTEMPTS) -> dict:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _post_daemon_json(endpoint, payload)
        except Exception as exc:
            last_error = exc
            if attempt >= attempts:
                break
            print(f"⚠️  daemon {endpoint} 请求失败，重试 {attempt + 1}/{attempts}: {exc}", file=sys.stderr)
    raise RuntimeError(str(last_error or f"daemon {endpoint} request failed"))


def _create_feishu_group(*, name: str, project: str, intern_type: str, workspace_id: str = "") -> None:
    payload = {"intern_name": name, "project": project, "type": intern_type}
    if workspace_id:
        payload["workspace_id"] = workspace_id
    result = _post_daemon_json_with_retry("/api/group/create", payload)
    chat_id = result.get("chat_id") or result.get("open_chat_id")
    if not chat_id:
        raise RuntimeError(f"daemon /api/group/create returned no chat_id: {result}")
    print(f"💬 已创建/绑定飞书群: {project}:{name}")


def _delete_feishu_group_best_effort(*, name: str, project: str) -> None:
    try:
        _post_daemon_json("/api/group/delete", {"intern_name": name, "project": project}, timeout=10)
        print(f"🗑️  已清理飞书群/relay registry: {project}:{name}")
    except Exception as exc:
        print(f"⚠️  飞书群清理失败（可能未创建）: {exc}", file=sys.stderr)


def _notify_daemon_best_effort(*, name: str) -> None:
    for endpoint in ("/api/intern/status_changed", "/api/intern/request_refresh"):
        try:
            _post_daemon_json(endpoint, {"intern_name": name}, timeout=10)
        except Exception as exc:
            print(f"⚠️  daemon {endpoint} 通知失败: {exc}", file=sys.stderr)


def _clear_session_registry_entry(*, name: str, project: str, workspace_id: str) -> None:
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    if not os.path.isfile(sessions_file):
        return
    try:
        with open(sessions_file, "r", encoding="utf-8") as f:
            sessions = json.load(f)
        session_key = _session_registry_key(name, project, workspace_id)
        if isinstance(sessions, dict) and session_key in sessions:
            sessions.pop(session_key, None)
            tmp = sessions_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(sessions, f, ensure_ascii=False, indent=2)
                f.write("\n")
            os.replace(tmp, sessions_file)
    except Exception as exc:
        print(f"⚠️  session registry 回滚失败: {exc}", file=sys.stderr)


def _rollback_enterprise_metadata(contract: dict, paths: list[str], *, name: str) -> None:
    root = _contract_checkout_root(contract)
    existing_dirs = sorted({os.path.dirname(path_value) for path_value in paths if path_value}, key=len, reverse=True)
    if root is None:
        import shutil
        for path_value in existing_dirs:
            if os.path.isdir(path_value):
                shutil.rmtree(path_value)
        return
    existing = [path_value for path_value in existing_dirs if os.path.exists(path_value)]
    if not existing:
        return
    rels = _relative_paths_under(root, existing)
    remove_and_push(
        repo_path=root,
        paths=rels,
        message=f"[{name}] intern: rollback create",
        branch=contract.get("metadata_branch") or None,
    )


def _rollback_created_intern(
    *,
    name: str,
    project: str,
    intern_root: str,
    workspace_id: str,
    metadata_contract: dict | None,
    metadata_paths: list[str],
) -> None:
    if metadata_contract:
        _rollback_enterprise_metadata(metadata_contract, metadata_paths, name=name)

    import shutil
    if os.path.isdir(intern_root):
        shutil.rmtree(intern_root, ignore_errors=True)
    _clear_session_registry_entry(name=name, project=project, workspace_id=workspace_id)


def setup_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("create", help="创建企业版 intern")
    p.add_argument("name", help="intern 名称（小写字母开头，仅含小写字母/数字/下划线）")
    p.add_argument("--project", default="axis_intern_agents", help="项目名称（默认 axis_intern_agents）")
    p.add_argument("--repo-url", default=DEFAULT_REPO_URL, help="Git repo URL")
    p.add_argument("--type", choices=["copilot", "claude", "codex"], default="copilot", help="intern 类型（默认 copilot）")
    p.add_argument(
        "--role",
        choices=INTERN_ROLES,
        default=DEFAULT_INTERN_ROLE,
        help="intern 角色（默认 independent；team mode 可用 coordinator/team_lead/worker）",
    )
    p.add_argument("--team-id", "--team-name", dest="team_id", default="", help="workspace team name（role 为 team_lead/worker 时写入 TEAM_ID）")
    p.add_argument("--coordinator-id", help="coordinator metadata id；仅 --role coordinator 使用")
    p.add_argument(
        "--standing-goal",
        default=DEFAULT_COORDINATOR_STANDING_GOAL,
        help="coordinator 固定长期目标；仅 --role coordinator 使用",
    )
    p.add_argument(
        "--skip-feishu-group",
        action="store_true",
        help="只创建 metadata/runtime，不创建飞书群；仅供离线测试或管理员诊断使用",
    )
    p.add_argument(
        "--skip-status-notify",
        action="store_true",
        help="创建成功后不通知 daemon 刷新状态；仅供离线测试或管理员诊断使用",
    )
    p.set_defaults(func=run)


def run(args: argparse.Namespace) -> int:
    name: str = args.name
    project: str = args.project
    raw_repo_url = getattr(args, "repo_url", "") or ""
    repo_url: str = str(raw_repo_url) if raw_repo_url else DEFAULT_REPO_URL
    intern_type: str = args.type
    intern_role: str = args.role
    team_id: str = args.team_id.strip()
    coordinator_id: str = getattr(args, "coordinator_id", None) or _default_coordinator_id(name)
    standing_goal: str = getattr(args, "standing_goal", None) or DEFAULT_COORDINATOR_STANDING_GOAL
    coordinator_task_id: str = _new_coordinator_task_id(name) if intern_role == "coordinator" else ""
    team_lead_task_id: str = team_lead_management_task_id(team_id) if intern_role == "team_lead" and team_id else ""
    skip_feishu_group = bool(vars(args).get("skip_feishu_group", True))
    skip_status_notify = bool(vars(args).get("skip_status_notify", True))

    if not validate_new_name(name):
        print(
            f"❌ 名称无效: '{name}'（新建 intern 必须以 intern_ 开头，仅含小写字母/数字/下划线）",
            file=sys.stderr,
        )
        return 1

    if team_id and intern_role not in ("team_lead", "worker"):
        print("❌ 只有 role=team_lead/worker 的 intern 可以写入 --team-name", file=sys.stderr)
        return 1
    if team_id and not validate_team_name(team_id):
        print(f"❌ team name 无效: {team_id}（必须匹配 [a-z][a-z0-9_]*）", file=sys.stderr)
        return 1

    if intern_role == "coordinator" and not COORDINATOR_ID_PATTERN.match(coordinator_id):
        print(
            f"❌ coordinator id 无效: '{coordinator_id}'（必须以小写字母开头，仅含小写字母/数字/下划线）",
            file=sys.stderr,
        )
        return 1

    if not enterprise_policy_exists(WORK_AGENTS_ROOT):
        print("❌ create requires enterprise policy/state-v1 workspace", file=sys.stderr)
        return 1

    try:
        workspace = _find_workspace_for_project(project)
    except Exception as exc:
        print(f"❌ workspace lookup failed for project '{project}': {exc}", file=sys.stderr)
        return 1

    workspace_id = str(workspace.get("workspace_id") or "")
    workspace_repo_url = str(workspace.get("repo_url") or "")
    if raw_repo_url and raw_repo_url != DEFAULT_REPO_URL:
        repo_url = str(raw_repo_url)
    else:
        if not workspace_repo_url:
            print(f"❌ workspace '{workspace_id}' missing repo_url", file=sys.stderr)
            return 1
        repo_url = workspace_repo_url
        print(f"📎 使用 workspace registry repo URL: {repo_url}")

    intern_root = _enterprise_intern_root(workspace_id, name)
    intern_repo = os.path.join(intern_root, project)

    if os.path.exists(intern_root):
        # 本地唯一性闸：判断是否是跨 project 同名冲突
        existing_project = None
        state_path = os.path.join(intern_root, ".hook_state.json")
        if os.path.isfile(state_path):
            try:
                with open(state_path, "r", encoding="utf-8") as fp:
                    existing_project = (json.load(fp) or {}).get("project")
            except Exception:
                existing_project = None
        if existing_project and existing_project != project:
            print(
                f"❌ intern '{name}' 本地路径已存在并挂在 project '{existing_project}'，"
                f"无法在 project '{project}' 重复创建。\n"
                f"   本机要求 (intern_name → 单一 project) 唯一。请先：\n"
                f"     internctl delete {name} --project {existing_project}\n"
                f"   再重新创建。",
                file=sys.stderr,
            )
        else:
            print(f"❌ intern '{name}' 已存在: {intern_root}", file=sys.stderr)
        return 1

    # 1. 创建辅助目录
    for sub in ("debug", "outputs"):
        os.makedirs(os.path.join(intern_root, sub), exist_ok=True)

    # 2. Clone 工作 repo
    print(f"📦 Cloning {repo_url} → {intern_repo} ...")
    try:
        clone(repo_url, intern_repo)
    except Exception as e:
        print(f"❌ Clone 失败: {e}", file=sys.stderr)
        return 1
    print("✅ Clone 完成")

    # 2.5. 设置 git user（新 clone 的 repo 没有 global config 时 commit 会失败）
    try:
        subprocess.run(
            ["git", "config", "user.name", name],
            cwd=intern_repo, capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", f"{name}@intern.local"],
            cwd=intern_repo, capture_output=True, check=True,
        )
    except Exception as e:
        print(f"⚠️  设置 git user 失败: {e}", file=sys.stderr)

    locale = _read_locale()
    initial_status = "Working" if intern_role in ("coordinator", "team_lead") and (coordinator_task_id or team_lead_task_id) else "Idle"
    initial_task = coordinator_task_id if intern_role == "coordinator" else team_lead_task_id
    try:
        metadata_contract = resolve_metadata_for_workspace_id(workspace_id, name, initial_task or "")
    except Exception as exc:
        print(f"❌ 无法解析企业 metadata contract: {exc}", file=sys.stderr)
        import shutil
        shutil.rmtree(intern_root, ignore_errors=True)
        return 1
    metadata_contract["code_repo_path"] = intern_repo
    metadata_contract["code_worktree_path"] = intern_repo
    metadata_contract = bind_repo_dotdir_metadata_to_code_repo(
        metadata_contract, intern_repo, name, initial_task or "")
    status_path = _require_contract_path(metadata_contract, "status_path")
    knowledge_path = _require_contract_path(metadata_contract, "knowledge_path")

    metadata_paths: list[str] = [status_path, knowledge_path]
    _write_text_file(status_path, _status_content(name, initial_status, initial_task, intern_role, team_id, locale))
    _write_text_file(knowledge_path, _knowledge_content(name, locale))
    print(f"📝 创建 status.md + knowledge.md")

    # 3.5. 写入 .hook_state.json（hooks 依赖 project / metadata_resolver 字段定位 metadata）
    hook_state_path = os.path.join(intern_root, ".hook_state.json")
    hook_state = {
        "project": project,
        "workspace_id": metadata_contract["workspace_id"],
        "metadata_resolver": metadata_contract,
    }
    with open(hook_state_path, "w", encoding="utf-8") as f:
        json.dump(hook_state, f, ensure_ascii=False, indent=2)
    print(f"📋 写入 .hook_state.json (project={project})")

    if intern_role == "coordinator":
        try:
            owner_identity = _read_daemon_owner_identity()
        except Exception as exc:
            print(f"❌ coordinator owner identity missing: {exc}", file=sys.stderr)
            import shutil
            shutil.rmtree(intern_root, ignore_errors=True)
            return 1
        metadata_paths.append(
            _write_coordinator_metadata(
                metadata_contract,
                project=project,
                name=name,
                coordinator_id=coordinator_id,
                task_id=coordinator_task_id,
                standing_goal=standing_goal,
                owner=owner_identity,
            )
        )
        metadata_paths.extend(
            _write_enterprise_task_metadata(metadata_contract, name=name, task_id=coordinator_task_id, kind="coordinator")
        )
        print(f"📌 创建 coordinator metadata + 永续任务: {coordinator_task_id}")
    elif intern_role == "team_lead" and team_lead_task_id:
        metadata_paths.extend(
            _write_enterprise_task_metadata(
                metadata_contract,
                name=name,
                task_id=team_lead_task_id,
                kind="team_lead",
                team_id=team_id,
            )
        )
        print(f"📌 创建 team lead manage team 常驻任务: {team_lead_task_id}")

    # 4. Git add + commit + push
    try:
        _commit_enterprise_metadata(metadata_contract, metadata_paths, name=name, action="创建")
        if metadata_contract.get("metadata_mode") != "local_only":
            print("🚀 已推送企业 metadata")
    except Exception as e:
        print(f"⚠️  企业 metadata Git 推送失败（本地文件已创建）: {e}", file=sys.stderr)
        return 1

    # 5. 写入 type 到 .intern_sessions.json（registry 持久化）
    sessions_file = os.path.join(WORK_AGENTS_ROOT, ".intern_sessions.json")
    try:
        sessions = {}
        if os.path.exists(sessions_file):
            with open(sessions_file, "r", encoding="utf-8") as f:
                sessions = json.load(f)
        session_key = _session_registry_key(name, project, workspace_id)
        entry = sessions.get(session_key, {})
        if not isinstance(entry, dict):
            entry = {}
        entry["type"] = intern_type
        entry["intern_name"] = name
        entry["project"] = project
        entry["workspace_id"] = workspace_id
        entry["intern_dir"] = intern_root
        sessions[session_key] = entry
        tmp = sessions_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sessions, f, ensure_ascii=False, indent=2)
        os.rename(tmp, sessions_file)
        print(f"📋 注册类型: {intern_type}")
    except Exception as e:
        print(f"⚠️  写入 registry 失败: {e}", file=sys.stderr)

    if not skip_feishu_group:
        try:
            _create_feishu_group(
                name=name,
                project=project,
                intern_type=intern_type,
                workspace_id=workspace_id,
            )
        except Exception as exc:
            print(f"❌ 飞书群创建失败，开始回滚 intern '{name}': {exc}", file=sys.stderr)
            _delete_feishu_group_best_effort(name=name, project=project)
            try:
                _rollback_created_intern(
                    name=name,
                    project=project,
                    intern_root=intern_root,
                    workspace_id=workspace_id,
                    metadata_contract=metadata_contract,
                    metadata_paths=metadata_paths,
                )
            except Exception as rollback_exc:
                print(f"❌ create 回滚失败: {rollback_exc}", file=sys.stderr)
            return 1

    if not skip_feishu_group and not skip_status_notify:
        _notify_daemon_best_effort(name=name)

    print(f"\n✅ intern '{name}' 创建成功！（类型: {intern_type}, role: {intern_role}）")
    return 0
