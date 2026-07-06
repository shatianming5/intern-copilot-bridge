from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Any

from CI.helpers.native_error import NativeCaseError


@dataclass
class SkillActions:
    ctx: Any

    def _remote(self) -> Any:
        remote = getattr(self.ctx, "remote_context", None)
        if remote is None:
            raise RuntimeError("ctx.action.skill.* requires RemoteCaseContext")
        return remote

    def run_cmd_remote(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int = 180,
        check: bool = True,
    ) -> dict[str, Any]:
        remote = self._remote()
        cmd = [*remote.internctl, "skill", *args]
        result = remote.run_cmd(name, cmd, timeout=timeout, check=check)
        return {
            "name": name,
            "argv": cmd,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def run_json_remote(
        self,
        name: str,
        args: list[str],
        *,
        timeout: int = 180,
        check: bool = True,
    ) -> dict[str, Any]:
        remote = self._remote()
        return remote.json_cmd(name, [*remote.internctl, "skill", *args], timeout=timeout, check=check)

    def read_json_path_remote(self, path: Path | str) -> dict[str, Any]:
        return self._remote().file_artifacts.read_json_path(Path(path))

    def source_target_remote(self, metadata_root: Path | str, key: str) -> dict[str, str]:
        target = Path(metadata_root) / ".skill_sources" / key
        return {"metadata_root": str(metadata_root), "key": key, "target": str(target)}

    def farm_rel_for_type_remote(self, intern_type: str) -> dict[str, str]:
        if intern_type == "claude":
            rel = Path(".claude") / "skills"
        elif intern_type == "codex":
            rel = Path(".agents") / "skills"
        else:
            raise NativeCaseError(f"unsupported skill farm intern type: {intern_type}")
        return {"intern_type": intern_type, "farm_rel": str(rel)}

    def farm_entries_remote(self, runtime: Path | str, intern_type: str = "codex") -> dict[str, Any]:
        rel = Path(self.farm_rel_for_type_remote(intern_type)["farm_rel"])
        farm = Path(runtime) / rel
        entries = sorted(path.name for path in farm.iterdir()) if farm.is_dir() else []
        return {"runtime": str(runtime), "intern_type": intern_type, "farm": str(farm), "entries": entries}

    def farm_link_remote(self, runtime: Path | str, skill_name: str, intern_type: str = "codex") -> dict[str, str]:
        rel = Path(self.farm_rel_for_type_remote(intern_type)["farm_rel"])
        return {
            "runtime": str(runtime),
            "intern_type": intern_type,
            "skill_name": skill_name,
            "link": str(Path(runtime) / rel / skill_name),
        }

    def write_source_fixture_remote(
        self,
        directory: Path | str,
        *,
        name: str,
        description: str,
        body: str = "",
    ) -> dict[str, str]:
        source_dir = Path(directory)
        source_dir.mkdir(parents=True, exist_ok=True)
        skill_md = source_dir / "SKILL.md"
        skill_md.write_text(
            "---\n"
            f"name: {name}\n"
            f"description: {description}\n"
            "---\n\n"
            f"{body or 'CI deployed skill source contract fixture.'}\n",
            encoding="utf-8",
        )
        return {
            "directory": str(source_dir),
            "skill_md": str(skill_md),
            "name": name,
            "description": description,
        }

    def git_source_fixture_remote(
        self,
        suffix: str,
        *,
        name: str,
        description: str,
        rel_dir: str = "",
    ) -> dict[str, str]:
        remote = self._remote()
        repo = self.ctx.action.workspace.local_repo_fixture_remote(suffix)
        skill_dir = repo / rel_dir if rel_dir else repo
        skill_md = Path(self.write_source_fixture_remote(skill_dir, name=name, description=description)["skill_md"])
        remote.run_cmd(f"git add skill {suffix}", ["git", "-C", str(repo), "add", rel_dir or "SKILL.md"], timeout=30)
        remote.run_cmd(f"git commit skill {suffix}", ["git", "-C", str(repo), "commit", "-m", "seed skill source"], timeout=60)
        head = remote.run_cmd(f"git head {suffix}", ["git", "-C", str(repo), "rev-parse", "HEAD"], timeout=30).stdout.strip()
        return {"repo": str(repo), "skill_md": str(skill_md), "head": head}

    def update_git_source_fixture_remote(
        self,
        repo: Path | str,
        *,
        name: str,
        description: str,
        message: str,
        rel_dir: str = "",
    ) -> dict[str, str]:
        remote = self._remote()
        repo_path = Path(repo)
        skill_dir = repo_path / rel_dir if rel_dir else repo_path
        skill_md = Path(self.write_source_fixture_remote(skill_dir, name=name, description=description, body=description)["skill_md"])
        remote.run_cmd(f"git add update {repo_path.name}", ["git", "-C", str(repo_path), "add", rel_dir or "SKILL.md"], timeout=30)
        remote.run_cmd(f"git commit update {repo_path.name}", ["git", "-C", str(repo_path), "commit", "-m", message], timeout=60)
        head = remote.run_cmd(f"git head update {repo_path.name}", ["git", "-C", str(repo_path), "rev-parse", "HEAD"], timeout=30).stdout.strip()
        return {"repo": str(repo_path), "skill_md": str(skill_md), "head": head}

    @staticmethod
    def _completed_process_from_evidence(evidence: dict[str, Any]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=list(evidence.get("argv") or []),
            returncode=int(evidence.get("returncode") or 0),
            stdout=str(evidence.get("stdout") or ""),
            stderr=str(evidence.get("stderr") or ""),
        )
