from __future__ import annotations

import json
from pathlib import Path
import subprocess
import time
from typing import Any


def tail(text: str, limit: int = 8000) -> str:
    value = text or ""
    return value if len(value) <= limit else value[-limit:]


def parse_json_output(name: str, text: str) -> Any:
    raw = text.strip()
    if not raw:
        raise ValueError(f"{name} produced empty JSON output")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    for index, char in enumerate(raw):
        if char not in "{[":
            continue
        try:
            return json.loads(raw[index:])
        except json.JSONDecodeError:
            continue
    raise ValueError(f"{name} produced unparseable JSON: {tail(raw, 1200)}")


class ProductCliHelper:
    def __init__(self, *, env: dict[str, str], default_cwd: Path, default_timeout: int):
        self.env = env
        self.default_cwd = default_cwd
        self.default_timeout = default_timeout

    def run_command(
        self,
        name: str,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        started = time.time()
        actual_cwd = cwd or self.default_cwd
        result = subprocess.run(
            cmd,
            cwd=str(actual_cwd),
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout or self.default_timeout,
        )
        step: dict[str, Any] = {
            "name": name,
            "cmd": " ".join(cmd),
            "cwd": str(actual_cwd),
            "returncode": result.returncode,
            "ok": result.returncode == 0,
            "status": "passed" if result.returncode == 0 else "failed",
            "duration_seconds": round(time.time() - started, 3),
            "stdout": tail(result.stdout),
            "stderr": tail(result.stderr),
        }
        if result.returncode != 0:
            step["failure_reason"] = f"command exited with rc={result.returncode}"
        return result, step
