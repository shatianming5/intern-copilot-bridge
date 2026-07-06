from __future__ import annotations

import os
import shlex
import sys
import tarfile
from pathlib import Path
from typing import Any

from CI.helpers import deployment_primitives as full_primitives
from CI.cases.base import CaseDefinition
from CI.runner.reporting import run_command, write_json


REMOTE_STAGE_REL = "CI/runner/stage_3_F.py"

PACKAGE_ENV_EXACT_BLOCKLIST = {
    "CODEX_LB_API_KEY",
    "CODEX_LB_BASE_URL",
    "CODEX_LB_ENV_KEY",
    "CODEX_POLICY_LB_BASE_URL",
    "LB_API_KEY",
}


def package_stage_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if (
            key in PACKAGE_ENV_EXACT_BLOCKLIST
            or key.startswith("CODEX_POLICY_")
            or key.startswith("INTERN_CODEX_")
        ):
            env.pop(key, None)
    return env


def run_package_stage(*, root: Path, artifact_dir: Path, timeout: int, dry_run: bool) -> dict[str, Any]:
    package_report = artifact_dir / "package-release-gate.json"
    vsix_path = artifact_dir / "intern-agent-helper-enterprise.vsix"
    ext = root / "vscode-extension"
    package_env = package_stage_env()
    commands: list[tuple[str, Path, list[str]]] = [
        ("sync_build_metadata", ext, ["node", "./scripts/generate-build-meta.cjs"]),
        ("vscode_jest", ext, ["npm", "test", "--", "--runInBand"]),
        ("vscode_compile", ext, ["npm", "run", "compile"]),
        ("vscode_package", ext, ["npm", "run", "package"]),
        ("vscode_dependency_audit", ext, ["npm", "audit", "--omit=dev"]),
        ("vsix_package", ext, ["npx", "--no-install", "vsce", "package", "--out", str(vsix_path)]),
        ("verify_vsix", root, [sys.executable, "intern-cli/scripts/verify_vsix_package.py", str(vsix_path), "--json"]),
    ]
    steps = []
    for name, cwd, command in commands:
        step = run_command(command, cwd=cwd, timeout=timeout, dry_run=dry_run, env=package_env)
        step["name"] = name
        steps.append(step)
        if not step.get("ok") and not dry_run:
            break
    ok = all(step.get("ok") for step in steps) and len(steps) == len(commands)
    report = {
        "schema": "intern-agents.ci-package-stage.v1",
        "steps": steps,
        "ok": ok,
        "status": "passed" if ok else ("skipped" if dry_run else "failed"),
        "vsix": str(vsix_path),
    }
    write_json(package_report, report)
    return report | {"report": str(package_report), "vsix": str(vsix_path)}


def run_feishu_shared_cleanup(
    *,
    root: Path,
    artifact_dir: Path,
    app_id: str,
    app_secret: str,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    cleanup_report = artifact_dir / "feishu-test-chat-cleanup.json"
    confirm_app_id = os.environ.get("ENTERPRISE_CI_TEST_FEISHU_APP_ID") or full_primitives.DEFAULT_ENTERPRISE_CI_FEISHU_APP_ID
    result = run_command(
        [
            sys.executable,
            "intern-cli/scripts/cleanup_feishu_test_chats.py",
            "--app-id",
            app_id,
            "--app-secret",
            app_secret,
            "--confirm-app-id",
            confirm_app_id,
            "--apply",
            "--report",
            str(cleanup_report),
            "--json",
        ],
        cwd=root,
        timeout=min(timeout, 600),
        dry_run=dry_run,
    )
    return result | {"report": str(cleanup_report), "app_id": app_id, "confirm_app_id": confirm_app_id}


def make_remote_payloads(
    *,
    artifact_dir: Path,
    vsix_path: Path,
    prefix: str,
    relay_host: str,
    feishu_app_id: str,
    feishu_app_secret: str,
    owner_mobile: str,
    codeup_token: str,
    codeup_ssh_key: Path,
    codex_lb_base_url: str,
    codex_lb_api_key: str,
    claude_access_token: str,
    claude_base_url: str,
    codex_lb_env_key: str = full_primitives.DEFAULT_CODEX_LB_ENV_KEY,
    codex_lb_secret_env: str = full_primitives.DEFAULT_CODEX_LB_SECRET_ENV,
) -> dict[str, Any]:
    extension_tar = full_primitives.make_extension_tarball(vsix_path, artifact_dir)
    codex_auth_tar = full_primitives.make_codex_auth_tarball(Path.home(), artifact_dir)
    python_wheels_tar = full_primitives.make_python_wheels_tarball(artifact_dir)
    ssh_auth_tar = full_primitives.make_ssh_auth_tarball(Path.home(), artifact_dir, codeup_ssh_key)
    enterprise_config_tar, relay_info = full_primitives.make_enterprise_config_tarball(
        artifact_dir,
        prefix=prefix,
        relay_host=relay_host,
        app_id=feishu_app_id,
        app_secret=feishu_app_secret,
        owner_mobile=owner_mobile,
        codeup_token=codeup_token,
        claude_access_token=claude_access_token,
        codex_lb_base_url=codex_lb_base_url,
        codex_lb_api_key=codex_lb_api_key,
        codex_lb_env_key=codex_lb_env_key,
        codex_lb_secret_env=codex_lb_secret_env,
        claude_base_url=claude_base_url,
    )
    return {
        "extension_tar": extension_tar,
        "codex_auth_tar": codex_auth_tar,
        "python_wheels_tar": python_wheels_tar,
        "ssh_auth_tar": ssh_auth_tar,
        "enterprise_config_tar": enterprise_config_tar,
        "relay_info": relay_info,
    }


def safe_run_id(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    return safe or "ci_run"


def ci_harness_remote_root(work_root: str, run_id: str) -> str:
    return f"{work_root}/ci-harness/{safe_run_id(run_id)}"


def make_ci_harness_tarball(*, repo_root: Path, artifact_dir: Path) -> Path:
    ci_dir = repo_root / "intern-cli" / "CI"
    tar_path = artifact_dir / "ci-harness.tgz"
    tar_path.parent.mkdir(parents=True, exist_ok=True)

    def filter_member(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        parts = Path(info.name).parts
        if "__pycache__" in parts or info.name.endswith((".pyc", ".pyo")):
            return None
        return info

    with tarfile.open(tar_path, "w:gz") as tf:
        tf.add(ci_dir, arcname="CI", filter=filter_member)
    return tar_path


def stage_ci_harness_on_machine(
    machine: dict[str, Any],
    *,
    ci_harness_tar: Path,
    harness_root: str,
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    remote_tmp = "/tmp/intern-agent-ci"
    remote_tar = f"{remote_tmp}/{ci_harness_tar.name}"
    steps = []
    ready = full_primitives.wait_machine_ssh_ready(machine, cwd=cwd, timeout=180, interval=5, dry_run=dry_run)
    steps.append({"name": "ssh_ready", **ready})
    if not ready.get("ok") and not dry_run:
        return {"ok": False, "status": "failed", "machine": machine, "steps": steps, "failure_reason": "remote ssh not ready"}
    prep = run_command(
        full_primitives.ssh_base(machine) + [f"mkdir -p {shlex.quote(remote_tmp)} {shlex.quote(work_root)}/ci-harness"],
        cwd=cwd,
        timeout=timeout,
        dry_run=dry_run,
    )
    steps.append({"name": "prep", **prep})
    if not prep.get("ok") and not dry_run:
        return {"ok": False, "status": "failed", "machine": machine, "steps": steps, "failure_reason": "remote CI harness prep failed"}
    copied = full_primitives.scp_to_machine(
        ci_harness_tar,
        machine,
        remote_tar,
        cwd=cwd,
        timeout=timeout,
        dry_run=dry_run,
    )
    steps.append({"name": "copy_ci_harness", **copied})
    if not copied.get("ok") and not dry_run:
        return {"ok": False, "status": "failed", "machine": machine, "steps": steps, "failure_reason": "copy CI harness failed"}
    install_cmd = (
        f"rm -rf {shlex.quote(harness_root)} && "
        f"mkdir -p {shlex.quote(harness_root)} && "
        f"tar -xzf {shlex.quote(remote_tar)} -C {shlex.quote(harness_root)} && "
        f"test -f {shlex.quote(harness_root)}/{REMOTE_STAGE_REL}"
    )
    installed = run_command(full_primitives.ssh_base(machine) + [install_cmd], cwd=cwd, timeout=timeout, dry_run=dry_run)
    steps.append({"name": "install_ci_harness", **installed})
    return {
        "ok": bool(installed.get("ok")) if not dry_run else False,
        "status": "passed" if installed.get("ok") else ("skipped" if dry_run else "failed"),
        "machine": machine,
        "harness_root": harness_root,
        "steps": steps,
        "failure_reason": "" if installed.get("ok") else installed.get("failure_reason", "install CI harness failed"),
    }


def stage_existing_ci_harness(
    *,
    machines: list[dict[str, Any]],
    repo_root: Path,
    artifact_dir: Path,
    work_root: str,
    run_id: str,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    harness_root = ci_harness_remote_root(work_root, run_id)
    if dry_run:
        return {
            "ok": False,
            "status": "skipped",
            "failure_reason": "dry run",
            "harness_root": harness_root,
            "machines": [],
        }
    ci_harness_tar = make_ci_harness_tarball(repo_root=repo_root, artifact_dir=artifact_dir)
    results = [
        stage_ci_harness_on_machine(
            machine,
            ci_harness_tar=ci_harness_tar,
            harness_root=harness_root,
            work_root=work_root,
            cwd=repo_root,
            timeout=timeout,
            dry_run=dry_run,
        )
        for machine in machines
    ]
    ok = all(item.get("ok") for item in results)
    return {
        "ok": ok,
        "status": "passed" if ok else "failed",
        "harness_root": harness_root,
        "tarball": str(ci_harness_tar),
        "machines": results,
        "failure_reason": "" if ok else "one or more debug machine CI harness syncs failed",
    }


def resolve_codex_lb_base_url() -> str:
    return (
        os.environ.get("CODEX_POLICY_LB_BASE_URL")
        or os.environ.get("CODEX_LB_BASE_URL")
        or full_primitives.DEFAULT_CODEX_LB_BASE_URL
    )


def resolve_codex_lb_api_key() -> str:
    return (
        os.environ.get(full_primitives.DEFAULT_CODEX_LB_SECRET_ENV)
        or os.environ.get(full_primitives.DEFAULT_CODEX_LB_ENV_KEY)
        or full_primitives.DEFAULT_CODEX_LB_API_KEY
    )


def resolve_claude_access_token() -> str:
    return os.environ.get("ANTHROPIC_AUTH_TOKEN") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or ""


def resolve_claude_base_url() -> str:
    return os.environ.get("ANTHROPIC_BASE_URL") or os.environ.get("CLAUDE_BASE_URL") or full_primitives.DEFAULT_CLAUDE_BASE_URL


def reset_remote_ci_state(
    *,
    machines: list[dict[str, Any]],
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    results = [
        full_primitives.reset_remote_ci_state(
            machine,
            work_root=work_root,
            cwd=cwd,
            timeout=timeout,
            dry_run=dry_run,
        )
        for machine in machines
    ]
    ok = all(item.get("ok") for item in results)
    return {
        "ok": ok,
        "status": "passed" if ok else ("skipped" if dry_run else "failed"),
        "machines": results,
        "failure_reason": "" if ok else "one or more debug machine resets failed",
    }


def deploy_remote_package(
    *,
    machines: list[dict[str, Any]],
    payloads: dict[str, Any],
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    results = [
        full_primitives.deploy_machine(
            machine,
            extension_tar=payloads["extension_tar"],
            codex_auth_tar=payloads["codex_auth_tar"],
            python_wheels_tar=payloads["python_wheels_tar"],
            ssh_auth_tar=payloads["ssh_auth_tar"],
            enterprise_config_tar=payloads["enterprise_config_tar"],
            work_root=work_root,
            cwd=cwd,
            timeout=timeout,
            dry_run=dry_run,
        )
        for machine in machines
    ]
    ok = all(item.get("ok") for item in results)
    return {
        "ok": ok,
        "status": "passed" if ok else ("skipped" if dry_run else "failed"),
        "machines": results,
        "failure_reason": "" if ok else "one or more debug machine deployments failed",
    }


def bootstrap_remote_services(
    *,
    machines: list[dict[str, Any]],
    payloads: dict[str, Any],
    work_root: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    codex_lb_base_url: str,
    claude_base_url: str,
) -> dict[str, Any]:
    relay_info = payloads["relay_info"]
    return full_primitives.bootstrap_remote_services(
        machines,
        relay_health_url=relay_info["relay_health_url"],
        work_root=work_root,
        cwd=cwd,
        timeout=timeout,
        dry_run=dry_run,
        codex_lb_base_url=relay_info.get("codex_lb_base_url") or codex_lb_base_url,
        codex_lb_env_key=relay_info.get("codex_lb_env_key") or full_primitives.DEFAULT_CODEX_LB_ENV_KEY,
        claude_base_url=relay_info.get("claude_base_url") or claude_base_url,
    )


def run_shared_repo_cleanup(
    *,
    cases: list[CaseDefinition],
    machine: dict[str, Any],
    work_root: str,
    protected_repo: str,
    nonprotected_repo: str,
    cwd: Path,
    timeout: int,
    dry_run: bool,
) -> dict[str, Any]:
    cleanup_cases = [case for case in cases if case.enabled and case.reset_ci_metadata_branches]
    results = []
    for case in cleanup_cases:
        if dry_run:
            results.append({
                "case_id": case.id,
                "name": case.name,
                "ok": False,
                "status": "skipped",
                "failure_reason": "dry run",
            })
            continue
        if not case.ci_native:
            results.append({
                "case_id": case.id,
                "name": case.name,
                "ok": False,
                "status": "skipped",
                "failure_reason": "shared repo cleanup action is not CI-native for this case yet",
            })
            continue
        results.append({
            "case_id": case.id,
            "name": case.name,
            "ok": True,
            "status": "passed",
            "reason": "active F/J cases have no shared Codeup cleanup",
        })
    ok = all(item.get("ok") or item.get("status") == "skipped" and dry_run for item in results)
    return {
        "ok": ok,
        "status": "passed" if ok else ("skipped" if dry_run else "failed"),
        "machine": machine,
        "cases": results,
        "protected_repo": protected_repo,
        "nonprotected_repo": nonprotected_repo,
        "work_root": work_root,
        "cwd": str(cwd),
        "timeout": timeout,
        "failure_reason": "" if ok else "one or more shared repo cleanup cases failed",
    }
