from __future__ import annotations

import json
import os
import shlex
from pathlib import Path
import re
import subprocess
import time
from collections.abc import Callable
from typing import Any
import urllib.error
import urllib.request

from CI.runner.reporting import run_command


class RemoteMachineHelper:
    def __init__(self, *, default_timeout: int):
        self.default_timeout = default_timeout

    def request_json(
        self,
        name: str,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        log_payload: dict[str, Any] | None = None,
        include_url: bool = True,
        path: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        data = None
        req_headers = {"Accept": "application/json", **(headers or {})}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")
        started = time.time()
        req = urllib.request.Request(url, data=data, method=method, headers=req_headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.default_timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                body = json.loads(raw or "{}")
                status = int(resp.status)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                body = json.loads(raw or "{}")
            except Exception:
                body = {"error": raw}
            status = int(exc.code)
        step: dict[str, Any] = {
            "name": name,
            "method": method,
            "status_code": status,
            "ok": status < 400,
            "status": "passed" if status < 400 else "failed",
            "duration_seconds": round(time.time() - started, 3),
            "headers": req_headers,
            "request": log_payload if log_payload is not None else (payload or {}),
            "response": body,
        }
        if include_url:
            step["url"] = url
        else:
            step["path"] = path
        if status >= 400:
            step["failure_reason"] = f"HTTP {status}"
        return {"status_code": status, "body": body, "ok": status < 400}, step


TMUX_INPUT_PROMPT_RE = re.compile(r"\n[›❯](?:[\s\u00a0]|$)")


def tmux_input_prompt_index(text: str) -> int:
    prompt_matches = list(TMUX_INPUT_PROMPT_RE.finditer(text or ""))
    return prompt_matches[-1].start() if prompt_matches else -1


def safe_artifact_stem(value: str, *, fallback: str = "artifact") -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or fallback


class ArtifactHelper:
    def __init__(self, artifact_dir: Path, *, clock_ns: Callable[[], int] = time.time_ns):
        self.artifact_dir = artifact_dir
        self.clock_ns = clock_ns

    def write_prompt_text(self, session: str, text: str) -> Path:
        safe_session = safe_artifact_stem(session, fallback="session")
        prompt_file = self.artifact_dir / f"{safe_session}-{self.clock_ns()}.prompt.txt"
        prompt_file.write_text(text, encoding="utf-8")
        return prompt_file


class FileArtifactHelper:
    @staticmethod
    def read_json_path(path: Path) -> dict[str, Any]:
        if not path.is_file():
            return {"enabled": []}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    @staticmethod
    def load_json_object(path: Path, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.is_file():
            return dict(default or {})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ValueError(f"invalid JSON at {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"JSON object expected at {path}")
        return data

    @staticmethod
    def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.chmod(0o600)
        tmp.replace(path)
        path.chmod(0o600)

    @staticmethod
    def write_report(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class TmuxHelper:
    def __init__(self, artifact_dir: Path, *, clock_ns: Callable[[], int] = time.time_ns):
        self.artifacts = ArtifactHelper(artifact_dir, clock_ns=clock_ns)
        self.clock_ns = clock_ns

    @staticmethod
    def target(session: str) -> str:
        return f"={session}:"

    @staticmethod
    def capture_command(tmux_session: str, *, lines: int, joined: bool = False) -> list[str]:
        command = ["tmux", "capture-pane"]
        if joined:
            command.append("-J")
        command.extend(["-pt", TmuxHelper.target(tmux_session), "-S", f"-{lines}"])
        return command

    @staticmethod
    def send_keys_command(session: str, keys: list[str]) -> list[str]:
        return ["tmux", "send-keys", "-t", TmuxHelper.target(session), *keys]

    def capture(
        self,
        *,
        session: str,
        tmux_session: str,
        lines: int,
        joined: bool,
        run_cmd: Callable[..., subprocess.CompletedProcess[str]],
    ) -> str:
        name = f"tmux {'joined ' if joined else ''}capture {session}"
        result = run_cmd(
            name,
            self.capture_command(tmux_session, lines=lines, joined=joined),
            timeout=30,
            check=False,
        )
        return result.stdout if result.returncode == 0 else result.stdout + result.stderr

    def input_state(self, tail_text: str) -> dict[str, Any]:
        update_index = tail_text.rfind("Update available")
        prompt_index = tmux_input_prompt_index(tail_text)
        update_choice_index = tail_text.rfind("\n› 1. Update now")
        skip_update = update_index >= 0 and update_choice_index >= update_index and prompt_index <= update_choice_index
        skip_keys: list[str] = []
        if skip_update:
            update_tail = tail_text[update_index:]
            skip_keys = ["2", "Enter"] if "Skip" in update_tail else ["Enter"]
        return {
            "update_index": update_index,
            "prompt_index": prompt_index,
            "update_choice_index": update_choice_index,
            "skip_update": skip_update,
            "skip_keys": skip_keys,
            "ready": (not skip_update) and prompt_index > update_index,
        }

    def send_text(
        self,
        *,
        session: str,
        text: str,
        run_cmd: Callable[..., subprocess.CompletedProcess[str]],
        wait_ready: Callable[[str], dict[str, Any]],
    ) -> dict[str, Any]:
        wait_ready(session)
        safe_session = safe_artifact_stem(session, fallback="session")
        prompt_file = self.artifacts.write_prompt_text(session, text)
        buffer_name = f"ci-{safe_session}-{self.clock_ns()}"
        run_cmd("tmux clear compose", self.send_keys_command(session, ["C-u"]), timeout=30)
        run_cmd("tmux load prompt", ["tmux", "load-buffer", "-b", buffer_name, str(prompt_file)], timeout=30)
        run_cmd(
            "tmux paste prompt",
            ["tmux", "paste-buffer", "-d", "-p", "-b", buffer_name, "-t", self.target(session)],
            timeout=30,
        )
        run_cmd("tmux submit prompt", self.send_keys_command(session, ["Enter"]), timeout=30)
        return {"prompt_file": str(prompt_file), "buffer_name": buffer_name}


def machine_relay_host(machine: dict[str, Any]) -> str:
    return str(machine.get("relay_host") or machine.get("container_host") or machine["host"])

def _ltp_proxy_command() -> str | None:
    proxy_key = (Path.home() / ".ssh" / "ltp_ssh_key").expanduser()
    if not proxy_key.is_file():
        return None
    proxy_host = os.environ.get("CI_SSH_PROXY_HOST", "")
    if not proxy_host:
        return None
    return (
        "ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
        f"-i {shlex.quote(str(proxy_key))} -p 30222 -W %h:%p {proxy_host}"
    )

def ssh_base(machine: dict[str, Any], *, identity_file: Path | None = None) -> list[str]:
    cmd = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "IdentitiesOnly=yes",
    ]
    proxy_command = _ltp_proxy_command()
    if proxy_command:
        cmd += ["-o", f"ProxyCommand={proxy_command}"]
    if identity_file is not None:
        cmd += ["-i", str(identity_file.expanduser())]
    cmd += ["-p", str(machine["port"]), f"root@{machine['host']}"]
    return cmd

def scp_to_machine(
    src: Path,
    machine: dict[str, Any],
    dest: str,
    *,
    cwd: Path,
    timeout: int,
    dry_run: bool,
    identity_file: Path | None = None,
) -> dict[str, Any]:
    cmd = [
        "scp",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "IdentitiesOnly=yes",
    ]
    proxy_command = _ltp_proxy_command()
    if proxy_command:
        cmd += ["-o", f"ProxyCommand={proxy_command}"]
    if identity_file is not None:
        cmd += ["-i", str(identity_file.expanduser())]
    cmd += ["-P", str(machine["port"]), str(src), f"root@{machine['host']}:{dest}"]
    return run_command(cmd, cwd=cwd, timeout=timeout, dry_run=dry_run)

def wait_machine_ssh_ready(
    machine: dict[str, Any],
    *,
    cwd: Path,
    timeout: int,
    interval: int,
    dry_run: bool,
    identity_file: Path | None = None,
) -> dict[str, Any]:
    deadline = time.time() + timeout
    attempts = []
    while True:
        probe = run_command(
            ssh_base(machine, identity_file=identity_file) + ["true"],
            cwd=cwd,
            timeout=min(15, max(1, timeout)),
            dry_run=dry_run,
        )
        attempts.append(probe)
        if probe.get("ok"):
            return {"ok": True, "status": "passed", "attempts": len(attempts), "last": probe}
        if dry_run or time.time() >= deadline:
            return {
                "ok": False,
                "status": "skipped" if dry_run else "failed",
                "attempts": len(attempts),
                "last": probe,
                "failure_reason": "ssh did not become ready",
            }
        time.sleep(interval)

def remote_cli(machine: dict[str, Any], work_root: str, cli: str, args: list[str], *, identity_file: Path | None = None) -> list[str]:
    script = f"{work_root}/extension/bundled-cli/{cli}.py"
    quoted_args = " ".join(shlex.quote(item) for item in args)
    command = (
        f"cd {shlex.quote(work_root)} && "
        f"WORK_AGENTS_ROOT={shlex.quote(work_root)} "
        f"PATH={shlex.quote(work_root)}/extension/bundled-cli:/root/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH "
        f"python3 {shlex.quote(script)} {quoted_args}"
    )
    return ssh_base(machine, identity_file=identity_file) + [command]

def wait_http_json(url: str, *, timeout: int, interval: int) -> dict[str, Any]:
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=min(15, max(1, timeout))) as resp:
                raw = resp.read()
            payload = json.loads(raw.decode("utf-8", "replace")) if raw else {}
            if isinstance(payload, dict):
                if (
                    payload.get("ok") is True
                    or payload.get("running") is True
                    or str(payload.get("status") or "").lower() in {"ok", "ready", "running"}
                ):
                    return payload
                last = payload
        except Exception as exc:  # noqa: BLE001
            last = {"error": str(exc)}
        time.sleep(interval)
    raise TimeoutError(f"HTTP endpoint did not become ready within {timeout}s; last={json.dumps(last)[:800]}")
