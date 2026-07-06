from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class CodeupActions:
    ctx: Any

    def git(self, args: list[str], *, cwd: str | Path | None = None, timeout: int = 120) -> dict[str, Any]:
        workdir = Path(cwd) if cwd else Path(self.ctx.repo_root)
        result = subprocess.run(
            ["git", *args],
            cwd=str(workdir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": result.returncode == 0,
            "status": "passed" if result.returncode == 0 else "failed",
            "argv": ["git", *args],
            "cwd": str(workdir),
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    def assert_branch_absent(self, branch: str, *, remote: str = "origin", cwd: str | Path | None = None) -> dict[str, Any]:
        result = self.git(["ls-remote", "--heads", remote, branch], cwd=cwd)
        absent = result["ok"] and not result["stdout"].strip()
        return {
            "ok": absent,
            "status": "passed" if absent else "failed",
            "branch": branch,
            "remote": remote,
            "git": result,
        }
