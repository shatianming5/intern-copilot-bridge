from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def _load_entrypoint(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load CLI entrypoint: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@dataclass
class CliActions:
    ctx: Any

    def internctl(self, argv: list[str], *, env: dict[str, str] | None = None) -> dict[str, Any]:
        return self._run_python_cli("internctl.py", "ci_internctl_entrypoint", argv, env=env)

    def intern_adminctl(self, argv: list[str], *, env: dict[str, str] | None = None) -> dict[str, Any]:
        return self._run_python_cli("intern-adminctl.py", "ci_intern_adminctl_entrypoint", argv, env=env)

    def _run_python_cli(self, script_name: str, module_name: str, argv: list[str], *, env: dict[str, str] | None) -> dict[str, Any]:
        root = Path(self.ctx.repo_root)
        script = root / "intern-cli" / script_name
        if not script.is_file():
            script = Path(self.ctx.cli_root) / script_name
        old_env = os.environ.copy()
        stdout = io.StringIO()
        stderr = io.StringIO()
        code = 0
        try:
            if env:
                os.environ.update(env)
            module = _load_entrypoint(script, module_name)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                try:
                    module.main(list(argv))
                except SystemExit as exc:
                    code = int(exc.code or 0) if isinstance(exc.code, int) else 1
        finally:
            os.environ.clear()
            os.environ.update(old_env)
        return {
            "ok": code == 0,
            "status": "passed" if code == 0 else "failed",
            "argv": [script_name, *argv],
            "returncode": code,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }
