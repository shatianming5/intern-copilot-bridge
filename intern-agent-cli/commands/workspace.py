"""internctl workspace — enterprise workspace registry commands."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from lib.cli_contract import ensure_cli_report_contract
from lib.codeup import codeup_branch_protection
from lib.metadata_checkout import DEFAULT_METADATA_BRANCH
from lib.user_env import load_enterprise_user_env

PID_FILE = os.environ.get("FEISHU_DAEMON_ADDR_FILE") or "/tmp/feishu_daemon.json"
MIGRATION_MARKER = "workspace-mode-migration.json"


def setup_parser(subparsers: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    p = subparsers.add_parser("workspace", help="Manage enterprise workspaces")
    ws_sub = p.add_subparsers(dest="workspace_command")

    list_cmd = ws_sub.add_parser("list", help="List relay workspaces with local enable state")
    list_cmd.add_argument("--json", action="store_true")
    list_cmd.set_defaults(func=run_list)

    create = ws_sub.add_parser("create", help="Add a workspace")
    create.add_argument("--repo-url", required=True)
    create.add_argument("--display-name", required=True)
    create.add_argument("--provider", required=True, choices=["github", "codeup", "gitlab", "local"])
    create.add_argument("--mode", required=True, choices=["repo_dotdir", "metadata_branch", "local_only"])
    create.add_argument("--metadata-branch", default="")
    create.add_argument("--json", action="store_true")
    create.set_defaults(func=run_create)

    migrate_mode = ws_sub.add_parser("migrate-mode", help="Create a workspace metadata mode migration PR")
    migrate_mode.add_argument("--repo-url", required=True)
    migrate_mode.add_argument("--target", required=True, choices=["repo_dotdir", "metadata_branch"])
    migrate_mode.add_argument("--metadata-branch", default=DEFAULT_METADATA_BRANCH)
    migrate_mode.add_argument("--branch", default="")
    migrate_mode.add_argument("--json", action="store_true")
    migrate_mode.set_defaults(func=run_migrate_mode)

    enable = ws_sub.add_parser("enable", help="Enable a workspace on this machine")
    enable.add_argument("workspace_id")
    enable.add_argument("--local-path", default="")
    enable.add_argument("--json", action="store_true")
    enable.set_defaults(func=run_enable)

    disable = ws_sub.add_parser("disable", help="Disable a workspace on this machine")
    disable.add_argument("workspace_id")
    disable.add_argument("--json", action="store_true")
    disable.set_defaults(func=run_disable)

    doctor = ws_sub.add_parser("doctor", help="Inspect local workspace health")
    doctor.add_argument("workspace_id")
    doctor.add_argument("--json", action="store_true")
    doctor.set_defaults(func=run_doctor)

    delete = ws_sub.add_parser("delete", help="Delete a relay workspace registry entry")
    delete.add_argument("workspace_id")
    delete.add_argument("--confirm", action="store_true")
    delete.add_argument("--json", action="store_true")
    delete.set_defaults(func=run_delete)


def _daemon_base() -> str:
    try:
        data = json.loads(Path(PID_FILE).read_text(encoding="utf-8"))
        port = int(data["http_port"])
    except Exception as exc:
        raise RuntimeError(f"daemon address unavailable: {PID_FILE}: {exc}") from exc
    return f"http://127.0.0.1:{port}"


def _load_workspace_user_env() -> None:
    root = os.environ.get("WORK_AGENTS_ROOT") or os.getcwd()
    load_enterprise_user_env(root)


def _request(method: str, path: str, payload: dict | None = None, timeout: float = 30.0) -> tuple[int, dict]:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(_daemon_base() + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            body = json.loads(raw or "{}")
        except Exception:
            body = {"error": raw}
        return int(exc.code), body


def _print(data: dict, json_output: bool) -> None:
    if json_output:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def _workspace_guard_failure(
    args: argparse.Namespace,
    message: str,
    *,
    command: str | None = None,
    available: bool | None = None,
    default_next_action: str | None = None,
) -> int:
    body: dict = {"error": message, "message": message}
    if available is not None:
        body["available"] = available
        body["reasons"] = [message]
        body["warnings"] = []
        body["required_actions"] = []
    body = ensure_cli_report_contract(
        body,
        ok=False,
        command=command or f"workspace {getattr(args, 'workspace_command', '')}",
        default_next_action=default_next_action or "Use metadata_branch for protected Codeup default branches, or fix local Codeup credentials and retry.",
    )
    _print(body, getattr(args, "json", False))
    return 1


def _normalize_repo_url_key(value: str) -> str:
    normalized = (value or "").strip()
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if normalized.endswith(".git"):
        normalized = normalized[:-4]
    return normalized.lower()


def _validate_codeup_repo_dotdir(repo_url: str) -> str:
    protected, branch, err = codeup_branch_protection(repo_url)
    if protected is True:
        return f"default branch {branch or '<unknown>'} is protected; repo_dotdir would require direct metadata writes"
    if err:
        return f"could not verify Codeup default branch protection: {err}"
    return ""


def _git_timeout_seconds() -> int:
    raw = os.environ.get("INTERN_METADATA_GIT_TIMEOUT", "30")
    try:
        value = int(raw)
    except ValueError:
        value = 30
    return max(1, value)


def _git_env() -> dict[str, str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_ASKPASS", "/bin/false")
    env.setdefault("SSH_ASKPASS", "/bin/false")
    ssh_command = env.get("GIT_SSH_COMMAND", "ssh")
    if "BatchMode" not in ssh_command:
        ssh_command = f"{ssh_command} -o BatchMode=yes"
    if "ConnectTimeout" not in ssh_command:
        ssh_command = f"{ssh_command} -o ConnectTimeout=10"
    env["GIT_SSH_COMMAND"] = ssh_command
    return env


def _validate_metadata_branch_exists(repo_url: str, branch: str) -> str:
    value = (branch or "").strip()
    if not value:
        return ""
    timeout = _git_timeout_seconds()
    env = _git_env()
    try:
        ref_check = subprocess.run(
            ["git", "check-ref-format", "--branch", value],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"metadata_branch validation timed out for {value!r}"
    if ref_check.returncode != 0:
        detail = (ref_check.stderr or ref_check.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        return f"invalid metadata_branch {value!r}{suffix}"
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--exit-code", repo_url, f"refs/heads/{value}"],
            capture_output=True,
            text=True,
            env=env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"metadata_branch preflight timed out for {value!r}"
    if result.returncode == 0 and result.stdout.strip():
        return ""
    detail = (result.stderr or result.stdout or "").strip()
    if result.returncode == 2:
        if value == DEFAULT_METADATA_BRANCH:
            return ""
        suffix = f": {detail}" if detail else ""
        return f"remote branch {value!r} is unavailable for metadata_branch checkout{suffix}"
    suffix = f": {detail}" if detail else ""
    return f"metadata_branch preflight failed for {value!r}{suffix}"


def _workspace_list() -> list[dict]:
    status, body = _request("GET", "/api/workspaces")
    if status >= 400:
        raise RuntimeError(body.get("error") or f"workspace list failed: HTTP {status}")
    workspaces = body.get("workspaces") or []
    return [item for item in workspaces if isinstance(item, dict)]


def _remote_workspace_record_exists_for_repo(args: argparse.Namespace) -> bool:
    if args.provider == "local":
        return False
    repo_key = _normalize_repo_url_key(args.repo_url)
    if not repo_key:
        return False
    try:
        workspaces = _workspace_list()
    except Exception:
        return False
    for item in workspaces:
        if str(item.get("provider") or "").strip().lower() == "local":
            continue
        if _normalize_repo_url_key(str(item.get("repo_url") or "")) == repo_key:
            return True
    return False


def _run_request(args: argparse.Namespace, method: str, path: str, payload: dict | None = None, success=(200, 201)) -> int:
    try:
        status, body = _request(method, path, payload)
    except Exception as exc:
        if getattr(args, "json", False):
            body = ensure_cli_report_contract(
                {"error": "WORKSPACE_DAEMON_UNAVAILABLE", "message": str(exc)},
                ok=False,
                command=f"workspace {getattr(args, 'workspace_command', '')}",
                default_next_action="Start the local daemon with `internctl daemon start`, then rerun the workspace command.",
            )
            _print(body, True)
            return 1
        print(f"workspace command failed: {exc}", file=sys.stderr)
        return 1
    ok = status in success and body.get("ok", True) is not False
    body = ensure_cli_report_contract(
        body,
        ok=ok,
        command=f"workspace {getattr(args, 'workspace_command', '')}",
        default_next_action="Review the workspace daemon response, fix the blocking check, then rerun the workspace command.",
    )
    _print(body, getattr(args, "json", False))
    return 0 if ok else 1


def run_list(args: argparse.Namespace) -> int:
    return _run_request(args, "GET", "/api/workspaces")


def run_create(args: argparse.Namespace) -> int:
    _load_workspace_user_env()
    if args.provider == "local" and args.mode != "local_only":
        return _workspace_guard_failure(
            args,
            "local workspaces only support local_only metadata mode",
            command="workspace create",
            default_next_action="Use --mode local_only for local paths, or choose a remote provider for repo_dotdir/metadata_branch.",
        )
    if args.provider != "local" and args.mode == "local_only":
        return _workspace_guard_failure(
            args,
            "remote workspaces cannot use local_only metadata mode",
            command="workspace create",
            default_next_action="Use repo_dotdir or metadata_branch for remote repositories.",
        )
    needs_new_workspace_guard = (
        (args.provider == "codeup" and args.mode == "repo_dotdir")
        or (args.mode == "metadata_branch" and bool(args.metadata_branch))
    )
    existing_remote_record = _remote_workspace_record_exists_for_repo(args) if needs_new_workspace_guard else False
    if not existing_remote_record and args.provider == "codeup" and args.mode == "repo_dotdir":
        reason = _validate_codeup_repo_dotdir(args.repo_url)
        if reason:
            return _workspace_guard_failure(args, reason)
    if not existing_remote_record and args.mode == "metadata_branch" and args.metadata_branch:
        reason = _validate_metadata_branch_exists(args.repo_url, args.metadata_branch)
        if reason:
            return _workspace_guard_failure(
                args,
                reason,
                command="workspace create",
                default_next_action="Create or choose an existing metadata branch, then rerun workspace create.",
            )
    payload = {
        "repo_url": args.repo_url,
        "display_name": args.display_name,
        "provider": args.provider,
        "metadata_mode": args.mode,
    }
    if args.metadata_branch:
        payload["metadata_branch"] = args.metadata_branch
    return _run_request(args, "POST", "/api/workspaces", payload, success=(200, 201))


def _migration_branch(args: argparse.Namespace) -> str:
    if args.branch:
        return args.branch
    stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    return f"intern/workspace-mode-migration/{args.target}/{stamp}"


def _run_git(args: list[str], cwd: str, *, check: bool = True, timeout: float = 120.0) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=_git_env(), timeout=timeout)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"git {' '.join(args)} failed{suffix}")
    return result


def _default_branch(repo_dir: Path) -> str:
    result = _run_git(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], cwd=os.fspath(repo_dir), check=False)
    value = result.stdout.strip()
    if value.startswith("origin/"):
        return value.removeprefix("origin/")
    for candidate in ("main", "master"):
        probe = _run_git(["rev-parse", "--verify", f"origin/{candidate}"], cwd=os.fspath(repo_dir), check=False)
        if probe.returncode == 0:
            return candidate
    branches = _run_git(["for-each-ref", "--format=%(refname:short)", "refs/remotes/origin"], cwd=os.fspath(repo_dir)).stdout.splitlines()
    for branch in branches:
        branch = branch.strip()
        if branch and branch != "origin/HEAD":
            return branch.removeprefix("origin/")
    raise RuntimeError("could not determine default branch for migration PR")


def _metadata_signature(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    signature = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in {".git", ".gitmodules"} for part in path.relative_to(root).parts):
            continue
        rel = path.relative_to(root).as_posix()
        if rel == MIGRATION_MARKER:
            continue
        signature[rel] = path.read_bytes()
    return signature


def _metadata_copy_ignore(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in {".git", ".gitmodules"}}


def _snapshot_metadata_tree(source: Path, destination: Path, *, label: str) -> dict[str, bytes]:
    signature = _metadata_signature(source)
    if not source.is_dir():
        raise RuntimeError(f"source metadata .intern_workspace not found for {label}")
    if not signature:
        raise RuntimeError(f"source metadata .intern_workspace has no content to migrate for {label}")
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(source, destination, ignore=_metadata_copy_ignore)
    return signature


def _ensure_target_metadata_safe(source_signature: dict[str, bytes], target: Path, *, label: str) -> None:
    target_signature = _metadata_signature(target)
    if target_signature and target_signature != source_signature:
        raise RuntimeError(f"target metadata .intern_workspace already exists with different content for {label}")


def _copy_metadata_tree(source: Path, target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target, ignore=_metadata_copy_ignore)


def _skill_source_submodule_paths(repo_dir: Path) -> list[str]:
    if not (repo_dir / ".gitmodules").is_file():
        return []
    result = _run_git(
        ["config", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$"],
        cwd=os.fspath(repo_dir),
        check=False,
    )
    if result.returncode != 0:
        return []
    paths = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        value = parts[1].strip().replace("\\", "/")
        if value == ".intern_workspace/.skill_sources" or value.startswith(".intern_workspace/.skill_sources/"):
            paths.append(value)
    return sorted(set(paths))


def _materialize_skill_source_submodules(repo_dir: Path) -> list[str]:
    paths = _skill_source_submodule_paths(repo_dir)
    if not paths:
        return []
    try:
        _run_git(["submodule", "update", "--init", "--recursive", "--", *paths], cwd=os.fspath(repo_dir))
    except RuntimeError as exc:
        raise RuntimeError(
            "could not materialize skill source submodules under .intern_workspace/.skill_sources before migration: "
            f"{exc}"
        ) from exc
    return paths


def _enabled_skill_keys(config_path: Path) -> list[str]:
    if not config_path.is_file():
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError(f"invalid skill config {config_path}: {exc}") from exc
    enabled = data.get("enabled")
    if not isinstance(enabled, list):
        raise RuntimeError(f"invalid skill config {config_path}: missing enabled list")
    return [str(item) for item in enabled]


def _validate_enabled_skill_sources(metadata_root: Path) -> None:
    errors = []
    sources_root = metadata_root / ".skill_sources"
    for key in _enabled_skill_keys(metadata_root / ".intern_skill.json"):
        skill_md = sources_root / key / "SKILL.md"
        if not skill_md.is_file():
            errors.append(f"repo skill {key!r} missing SKILL.md at {skill_md}")
    interns_root = metadata_root / "interns"
    if interns_root.is_dir():
        for config_path in sorted(interns_root.glob("*/.intern_skill.json")):
            intern = config_path.parent.name
            for key in _enabled_skill_keys(config_path):
                candidates = [
                    sources_root / key / "SKILL.md",
                    sources_root / "personal" / intern / key / "SKILL.md",
                ]
                if not any(path.is_file() for path in candidates):
                    errors.append(
                        f"personal skill {key!r} for {intern!r} missing SKILL.md under {sources_root}"
                    )
    if errors:
        raise RuntimeError("metadata skill source validation failed before migration: " + "; ".join(errors))


def _prepare_source_metadata_snapshot(repo_dir: Path, destination: Path, *, label: str) -> tuple[dict[str, bytes], list[str]]:
    materialized_submodules = _materialize_skill_source_submodules(repo_dir)
    metadata_root = repo_dir / ".intern_workspace"
    _validate_enabled_skill_sources(metadata_root)
    signature = _snapshot_metadata_tree(metadata_root, destination, label=label)
    return signature, materialized_submodules


def _write_migration_marker(repo_dir: Path, args: argparse.Namespace, *, source_mode: str, base_branch: str) -> None:
    marker = repo_dir / ".intern_workspace" / MIGRATION_MARKER
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "schema": "intern-agents.workspace-mode-migration.v1",
        "repo_url": args.repo_url,
        "source": source_mode,
        "target": args.target,
        "base_branch": base_branch,
        "metadata_branch": args.metadata_branch if args.target == "metadata_branch" or source_mode == "metadata_branch" else "",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": "Apply this PR after deleting any relay workspace record for the same repo.",
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _remote_branch_ref(branch: str) -> str:
    if not branch:
        raise RuntimeError("branch name required")
    return f"origin/{branch}"


def _require_remote_branch(repo_dir: Path, branch: str) -> None:
    result = _run_git(["rev-parse", "--verify", _remote_branch_ref(branch)], cwd=os.fspath(repo_dir), check=False)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"remote branch {branch!r} is unavailable for migration{suffix}")


def _clean_migration_worktree(repo_dir: Path) -> None:
    _run_git(["reset", "--hard"], cwd=os.fspath(repo_dir))
    _run_git(["clean", "-ffdx"], cwd=os.fspath(repo_dir))
    shutil.rmtree(repo_dir / ".intern_workspace", ignore_errors=True)
    try:
        (repo_dir / ".gitmodules").unlink()
    except FileNotFoundError:
        pass


def _prepare_metadata_branch_migration(repo_dir: Path, args: argparse.Namespace, branch: str, workdir: str) -> tuple[str, list[str], list[str]]:
    default_branch = _default_branch(repo_dir)
    metadata_branch = (args.metadata_branch or "").strip()
    if not metadata_branch:
        raise RuntimeError("--metadata-branch is required when migrating to metadata_branch")
    _require_remote_branch(repo_dir, metadata_branch)
    _run_git(["checkout", _remote_branch_ref(default_branch)], cwd=os.fspath(repo_dir))
    snapshot = Path(workdir) / "source-metadata"
    source_signature, materialized_submodules = _prepare_source_metadata_snapshot(
        repo_dir,
        snapshot,
        label=f"repo_dotdir on {default_branch}",
    )
    _clean_migration_worktree(repo_dir)
    _run_git(["checkout", "-f", "-B", branch, _remote_branch_ref(metadata_branch)], cwd=os.fspath(repo_dir))
    _ensure_target_metadata_safe(source_signature, repo_dir / ".intern_workspace", label=f"metadata_branch {metadata_branch}")
    _copy_metadata_tree(snapshot, repo_dir / ".intern_workspace")
    _write_migration_marker(repo_dir, args, source_mode="repo_dotdir", base_branch=metadata_branch)
    return metadata_branch, sorted(source_signature), materialized_submodules


def _prepare_repo_dotdir_migration(repo_dir: Path, args: argparse.Namespace, branch: str, workdir: str) -> tuple[str, list[str], list[str]]:
    default_branch = _default_branch(repo_dir)
    metadata_branch = (args.metadata_branch or "").strip()
    if not metadata_branch:
        raise RuntimeError("--metadata-branch is required when migrating from metadata_branch")
    _require_remote_branch(repo_dir, metadata_branch)
    _run_git(["checkout", "--detach", _remote_branch_ref(metadata_branch)], cwd=os.fspath(repo_dir))
    snapshot = Path(workdir) / "source-metadata"
    source_signature, materialized_submodules = _prepare_source_metadata_snapshot(
        repo_dir,
        snapshot,
        label=f"metadata_branch {metadata_branch}",
    )
    _clean_migration_worktree(repo_dir)
    _run_git(["checkout", "-f", "-B", branch, _remote_branch_ref(default_branch)], cwd=os.fspath(repo_dir))
    _ensure_target_metadata_safe(source_signature, repo_dir / ".intern_workspace", label=f"repo_dotdir on {default_branch}")
    _copy_metadata_tree(snapshot, repo_dir / ".intern_workspace")
    _write_migration_marker(repo_dir, args, source_mode="metadata_branch", base_branch=default_branch)
    return default_branch, sorted(source_signature), materialized_submodules


def _create_migration_pr(repo_dir: Path, repo_url: str, branch: str, target: str, *, base_branch: str | None = None) -> dict:
    base = base_branch or _default_branch(repo_dir)
    title = f"Prepare workspace metadata mode migration to {target}"
    body = (
        "This PR prepares repository metadata for a workspace metadata mode migration.\n\n"
        f"- Target mode: `{target}`\n"
        "- Relay workspace mode is not changed by this PR.\n"
        "- Delete the existing relay workspace record before re-adding the repo with the target mode.\n"
    )
    lowered = repo_url.lower()
    if "github.com" in lowered:
        tool = shutil.which("gh")
        if not tool:
            raise RuntimeError("gh CLI is required to create a GitHub migration PR")
        cmd = [tool, "pr", "create", "--base", base, "--head", branch, "--title", title, "--body", body]
    elif "codeup" in lowered or "aliyun.com" in lowered:
        tool = shutil.which("codeup_pr")
        if not tool:
            raise RuntimeError("codeup_pr CLI is required to create a Codeup migration MR")
        cmd = [tool, "create", "--base", base, "--head", branch, "--title", title, "--body", body]
    else:
        raise RuntimeError("could not infer PR tool from repo_url; supported providers: GitHub and Codeup")
    result = subprocess.run(cmd, cwd=os.fspath(repo_dir), capture_output=True, text=True, env=_git_env(), timeout=float(_git_timeout_seconds() * 4))
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"migration PR creation failed: {detail}")
    return {
        "created": True,
        "base": base,
        "branch": branch,
        "tool": Path(cmd[0]).name,
        "output": result.stdout.strip(),
    }


def _create_migration_pr_payload(args: argparse.Namespace) -> dict:
    branch = _migration_branch(args)
    workdir = tempfile.mkdtemp(prefix="intern-workspace-mode-migrate-")
    try:
        repo_dir = Path(workdir) / "repo"
        _run_git(["clone", args.repo_url, os.fspath(repo_dir)], cwd=workdir, timeout=float(_git_timeout_seconds() * 4))
        if args.target == "metadata_branch":
            base_branch, migrated_files, materialized_submodules = _prepare_metadata_branch_migration(repo_dir, args, branch, workdir)
        elif args.target == "repo_dotdir":
            base_branch, migrated_files, materialized_submodules = _prepare_repo_dotdir_migration(repo_dir, args, branch, workdir)
        else:
            raise RuntimeError(f"unsupported target metadata mode: {args.target}")
        _run_git(["add", ".intern_workspace"], cwd=os.fspath(repo_dir))
        diff = _run_git(["diff", "--cached", "--quiet"], cwd=os.fspath(repo_dir), check=False)
        if diff.returncode == 0:
            raise RuntimeError("migration branch has no metadata changes to commit")
        _run_git(["commit", "-m", f"Prepare workspace metadata mode migration to {args.target}"], cwd=os.fspath(repo_dir))
        _run_git(["push", "-u", "origin", branch], cwd=os.fspath(repo_dir), timeout=float(_git_timeout_seconds() * 4))
        pr = _create_migration_pr(repo_dir, args.repo_url, branch, args.target, base_branch=base_branch)
        return {
            "branch": branch,
            "base_branch": base_branch,
            "target": args.target,
            "repo_url": args.repo_url,
            "migrated_files": migrated_files,
            "materialized_submodules": materialized_submodules,
            "pr": pr,
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def run_migrate_mode(args: argparse.Namespace) -> int:
    _load_workspace_user_env()
    try:
        repo_key = _normalize_repo_url_key(args.repo_url)
        matches = [
            item for item in _workspace_list()
            if _normalize_repo_url_key(str(item.get("repo_url") or "")) == repo_key
            and str(item.get("provider") or "") != "local"
        ]
    except Exception as exc:
        return _workspace_guard_failure(
            args,
            f"workspace migration preflight failed: {exc}",
            command="workspace migrate-mode",
            default_next_action="Start the local daemon and ensure it can sync relay workspaces, then rerun workspace migrate-mode.",
        )
    if matches:
        existing = matches[0]
        return _workspace_guard_failure(
            args,
            "relay workspace record still exists for this repo; delete it globally before creating a migration PR",
            command="workspace migrate-mode",
            default_next_action=(
                f"Run `internctl workspace delete {existing.get('workspace_id')} --confirm --json`, "
                f"then rerun workspace migrate-mode --repo-url <repo> --target {args.target}."
            ),
        )
    try:
        payload = _create_migration_pr_payload(args)
    except Exception as exc:
        return _workspace_guard_failure(
            args,
            f"workspace migration PR creation failed: {exc}",
            command="workspace migrate-mode",
            default_next_action="Fix git credentials or repo access, then rerun workspace migrate-mode.",
        )
    payload = ensure_cli_report_contract(
        {"ok": True, "migration": payload},
        ok=True,
        command="workspace migrate-mode",
        default_next_action="Open the pushed migration branch as a PR and merge it before re-adding the workspace.",
    )
    _print(payload, getattr(args, "json", False))
    return 0


def run_enable(args: argparse.Namespace) -> int:
    payload = {"local_path": args.local_path} if args.local_path else {}
    return _run_request(args, "POST", f"/api/workspaces/{urllib.parse.quote(args.workspace_id)}/enable", payload)


def run_disable(args: argparse.Namespace) -> int:
    return _run_request(args, "POST", f"/api/workspaces/{urllib.parse.quote(args.workspace_id)}/disable", {})


def run_doctor(args: argparse.Namespace) -> int:
    return _run_request(args, "POST", f"/api/workspaces/{urllib.parse.quote(args.workspace_id)}/doctor", {})


def run_delete(args: argparse.Namespace) -> int:
    if not args.confirm:
        print("refusing to delete workspace without --confirm", file=sys.stderr)
        return 1
    return _run_request(args, "DELETE", f"/api/workspaces/{urllib.parse.quote(args.workspace_id)}")
