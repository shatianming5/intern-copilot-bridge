"""Runtime log path helpers.

The log root is still ``llm_intern_logs`` for setup/cleanup compatibility, but
runtime log writers add version and project dimensions below it.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

SYSTEM_PROJECT_KEY = "_system"


def safe_segment(value: str) -> str:
    raw = str(value or "").strip()
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip(".")
    if not safe:
        raise ValueError("log path segment cannot be empty")
    return safe


def log_root(work_root: str | os.PathLike[str]) -> Path:
    return Path(work_root) / "llm_intern_logs"


def version_key_from_meta(meta: dict[str, Any]) -> str:
    versions = meta.get("versions") if isinstance(meta.get("versions"), dict) else {}
    extension_version = str(versions.get("extension") or meta.get("extension_version") or "unknown")
    return safe_segment(extension_version)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fp:
        data = json.load(fp)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _candidate_build_meta_paths(
    *,
    build_meta_path: str | os.PathLike[str] | None = None,
    bundle_dir: str | os.PathLike[str] | None = None,
    script_path: str | os.PathLike[str] | None = None,
) -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("INTERN_AGENT_BUILD_META")
    if build_meta_path:
        candidates.append(Path(build_meta_path))
    if env_path:
        candidates.append(Path(env_path))
    if bundle_dir:
        candidates.append(Path(bundle_dir).resolve().parent / "build-meta.json")
    if script_path:
        current = Path(script_path).resolve()
        for parent in [current.parent, *current.parents]:
            candidates.append(parent / "build-meta.json")
            if parent.name == "bundled-cli":
                candidates.append(parent.parent / "build-meta.json")
    current_file = Path(__file__).resolve()
    for parent in [current_file.parent, *current_file.parents]:
        candidates.append(parent / "vscode-extension" / "build-meta.json")
        candidates.append(parent / "build-meta.json")
    seen: set[str] = set()
    unique: list[Path] = []
    for item in candidates:
        key = str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def current_version_key(
    *,
    build_meta_path: str | os.PathLike[str] | None = None,
    bundle_dir: str | os.PathLike[str] | None = None,
    script_path: str | os.PathLike[str] | None = None,
    component: str = "",
    component_version: str = "",
) -> str:
    for path in _candidate_build_meta_paths(
        build_meta_path=build_meta_path,
        bundle_dir=bundle_dir,
        script_path=script_path,
    ):
        if path.is_file():
            return version_key_from_meta(_read_json(path))
    if component and component_version:
        return safe_segment(component_version)
    raise FileNotFoundError("build-meta.json not found and no component version was provided")


def version_log_root(work_root: str | os.PathLike[str], version_key: str) -> Path:
    return log_root(work_root) / "versions" / safe_segment(version_key)


def project_log_root(
    work_root: str | os.PathLike[str],
    version_key: str,
    project_key: str,
) -> Path:
    return version_log_root(work_root, version_key) / "projects" / safe_segment(project_key)


def intern_log_dir(
    work_root: str | os.PathLike[str],
    version_key: str,
    project_key: str,
    intern_name: str,
) -> Path:
    return project_log_root(work_root, version_key, project_key) / "interns" / safe_segment(intern_name)


def task_log_dir(
    work_root: str | os.PathLike[str],
    version_key: str,
    project_key: str,
    task_id: str,
) -> Path:
    return project_log_root(work_root, version_key, project_key) / "tasks" / safe_segment(task_id)


def system_log_dir(
    work_root: str | os.PathLike[str],
    component: str,
    *,
    version_key: str | None = None,
    build_meta_path: str | os.PathLike[str] | None = None,
    bundle_dir: str | os.PathLike[str] | None = None,
    script_path: str | os.PathLike[str] | None = None,
    component_version: str = "",
) -> Path:
    resolved_version = version_key or current_version_key(
        build_meta_path=build_meta_path,
        bundle_dir=bundle_dir,
        script_path=script_path,
        component=component,
        component_version=component_version,
    )
    return version_log_root(work_root, resolved_version) / SYSTEM_PROJECT_KEY / safe_segment(component)


def transfer_log_dir(work_root: str | os.PathLike[str], version_key: str, machine_id: str) -> Path:
    return version_log_root(work_root, version_key) / SYSTEM_PROJECT_KEY / "transfers" / safe_segment(machine_id)
