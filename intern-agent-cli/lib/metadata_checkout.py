"""Enterprise metadata checkout helpers."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

DEFAULT_METADATA_BRANCH = "intern_workspace"


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


def _run_git(args: list[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=_git_timeout_seconds(),
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        raise RuntimeError(f"git {' '.join(args)} failed (exit {result.returncode}){suffix}")
    return result


def _run_git_at(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return _run_git(args, cwd=".", check=check)


def _validate_branch(branch: str) -> str:
    value = (branch or "").strip()
    if not value:
        raise RuntimeError("metadata_branch is required for metadata_branch mode")
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", value],
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=_git_timeout_seconds(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"invalid metadata_branch {value!r}: {detail}")
    return value


def _remote_branch_exists(repo_url: str, branch: str) -> bool:
    result = _run_git_at(["ls-remote", "--exit-code", repo_url, f"refs/heads/{branch}"], check=False)
    if result.returncode == 0 and result.stdout.strip():
        return True
    if result.returncode == 2:
        return False
    detail = (result.stderr or result.stdout or "").strip()
    suffix = f": {detail}" if detail else ""
    raise RuntimeError(f"metadata_branch preflight failed for {branch!r}{suffix}")


def _ensure_git_identity(checkout_path: str) -> None:
    name = _run_git(["config", "user.name"], cwd=checkout_path, check=False).stdout.strip()
    email = _run_git(["config", "user.email"], cwd=checkout_path, check=False).stdout.strip()
    if not name:
        _run_git(["config", "user.name", "intern-agent"], cwd=checkout_path)
    if not email:
        _run_git(["config", "user.email", "intern-agent@local"], cwd=checkout_path)


def _write_initial_metadata_tree(checkout_path: str) -> None:
    metadata_root = Path(checkout_path) / ".intern_workspace"
    for child in ("interns", "tasks", "teams"):
        (metadata_root / child).mkdir(parents=True, exist_ok=True)
    readme = metadata_root / "README.md"
    if not readme.exists():
        readme.write_text("# intern workspace metadata\n", encoding="utf-8")


def _init_metadata_branch(repo_url: str, checkout_path: str, branch: str) -> None:
    if branch != DEFAULT_METADATA_BRANCH:
        raise RuntimeError(f"remote branch {branch!r} is unavailable for metadata_branch checkout")
    target = Path(checkout_path)
    if target.exists():
        if any(target.iterdir()):
            raise RuntimeError(f"metadata checkout path is not a git repo and is not empty: {checkout_path}")
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        target.mkdir(parents=True, exist_ok=False)
        _run_git(["init"], cwd=checkout_path)
        _run_git(["checkout", "--orphan", branch], cwd=checkout_path)
        _run_git(["remote", "add", "origin", repo_url], cwd=checkout_path)
        _ensure_git_identity(checkout_path)
        _write_initial_metadata_tree(checkout_path)
        _run_git(["add", ".intern_workspace"], cwd=checkout_path)
        _run_git(["commit", "-m", f"init {branch} metadata"], cwd=checkout_path)
        _run_git(["push", "origin", f"HEAD:{branch}"], cwd=checkout_path)
    except Exception:
        shutil.rmtree(checkout_path, ignore_errors=True)
        raise


def _clone_metadata_branch(repo_url: str, checkout_path: str, branch: str) -> None:
    if not repo_url:
        raise RuntimeError("repo_url is required to initialize metadata_branch checkout")
    target = Path(checkout_path)
    if target.exists():
        if any(target.iterdir()):
            raise RuntimeError(f"metadata checkout path is not a git repo and is not empty: {checkout_path}")
        target.rmdir()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not _remote_branch_exists(repo_url, branch):
        _init_metadata_branch(repo_url, checkout_path, branch)
        return
    result = subprocess.run(
        ["git", "clone", "--branch", branch, "--single-branch", repo_url, checkout_path],
        capture_output=True,
        text=True,
        env=_git_env(),
        timeout=_git_timeout_seconds(),
    )
    if result.returncode != 0:
        shutil.rmtree(checkout_path, ignore_errors=True)
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(f"metadata branch checkout failed for {branch!r}: {detail}")


def ensure_metadata_branch_checkout(
    workspace: dict,
    *,
    workspace_id: str,
    checkout_path: str | None = None,
    branch: str | None = None,
) -> dict:
    """Ensure metadata_branch mode has a usable local git checkout on the metadata branch."""
    metadata_branch = _validate_branch(branch or str(workspace.get("metadata_branch") or ""))
    metadata_checkout = checkout_path or str(workspace.get("metadata_cache_path") or "")
    if not metadata_checkout or not os.path.isabs(metadata_checkout):
        raise RuntimeError(f"workspace {workspace_id} missing absolute metadata_cache_path for metadata_branch mode")

    repo_url = str(workspace.get("repo_url") or "")
    if not os.path.isdir(os.path.join(metadata_checkout, ".git")):
        _clone_metadata_branch(repo_url, metadata_checkout, metadata_branch)
    else:
        origin = _run_git(["remote", "get-url", "origin"], cwd=metadata_checkout, check=False).stdout.strip()
        if not origin:
            if not repo_url:
                raise RuntimeError(f"metadata checkout {metadata_checkout} has no origin remote")
            _run_git(["remote", "add", "origin", repo_url], cwd=metadata_checkout)
        elif repo_url and origin != repo_url:
            raise RuntimeError(
                f"metadata checkout origin mismatch for {workspace_id}: expected {repo_url}, found {origin}"
            )

        _run_git(
            ["fetch", "origin", f"+refs/heads/{metadata_branch}:refs/remotes/origin/{metadata_branch}"],
            cwd=metadata_checkout,
        )
        local_branch = _run_git(
            ["rev-parse", "--verify", f"refs/heads/{metadata_branch}"],
            cwd=metadata_checkout,
            check=False,
        )
        if local_branch.returncode == 0:
            _run_git(["checkout", metadata_branch], cwd=metadata_checkout)
            merge = _run_git(
                ["merge", "--ff-only", f"origin/{metadata_branch}"],
                cwd=metadata_checkout,
                check=False,
            )
            if merge.returncode != 0:
                same_tree = _run_git(
                    ["diff", "--quiet", "HEAD", f"origin/{metadata_branch}"],
                    cwd=metadata_checkout,
                    check=False,
                ).returncode == 0
                if same_tree:
                    _run_git(["reset", "--hard", f"origin/{metadata_branch}"], cwd=metadata_checkout)
                else:
                    detail = (merge.stderr or merge.stdout or "").strip()
                    suffix = f": {detail}" if detail else ""
                    raise RuntimeError(
                        f"git merge --ff-only origin/{metadata_branch} failed "
                        f"(exit {merge.returncode}){suffix}"
                    )
        else:
            _run_git(["checkout", "-B", metadata_branch, f"origin/{metadata_branch}"], cwd=metadata_checkout)

    current = _run_git(["branch", "--show-current"], cwd=metadata_checkout).stdout.strip()
    if current != metadata_branch:
        raise RuntimeError(
            f"metadata checkout branch mismatch for {workspace_id}: expected {metadata_branch}, found {current}"
        )
    return {
        "ok": True,
        "workspace_id": workspace_id,
        "metadata_checkout_path": metadata_checkout,
        "metadata_branch": metadata_branch,
    }
