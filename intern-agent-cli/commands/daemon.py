"""internctl daemon - manage the local Feishu daemon on headless machines."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import urllib.request

from lib.user_env import load_enterprise_user_env
from lib.log_paths import system_log_dir


PID_FILE = Path("/tmp/feishu_daemon.json")


def setup_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("daemon", help="Manage local daemon")
    sub = p.add_subparsers(dest="daemon_command", help="Daemon sub-commands")

    start = sub.add_parser("start", help="Start local daemon in background")
    start.add_argument("--foreground", "-f", action="store_true", help="Run in foreground")
    start.set_defaults(func=run)

    stop = sub.add_parser("stop", help="Stop local daemon")
    stop.set_defaults(func=run)

    status = sub.add_parser("status", help="Show local daemon status")
    status.add_argument("--json", action="store_true", help="Output JSON")
    status.set_defaults(func=run)

    restart = sub.add_parser("restart", help="Restart local daemon")
    restart.set_defaults(func=run)

    p.set_defaults(func=run)


def _root() -> str:
    return os.environ.get("WORK_AGENTS_ROOT") or os.getcwd()


def _load_user_env(root: str) -> dict[str, str]:
    env = os.environ.copy()
    env["WORK_AGENTS_ROOT"] = root
    load_enterprise_user_env(root, env=env)
    return env


def _daemon_script() -> str:
    cli_root = Path(__file__).resolve().parents[1]
    return str(cli_root / "scripts" / "daemon" / "feishu_daemon.py")


def _pid_payload() -> dict:
    try:
        return json.loads(PID_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _pid_looks_like_daemon(pid: int) -> bool:
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if not proc_cmdline.exists():
        return True
    try:
        return "feishu_daemon.py" in proc_cmdline.read_bytes().replace(b"\0", b" ").decode("utf-8", errors="replace")
    except OSError:
        return True


def _unlink_pid_file_if_stale(expected_pid: int | None = None) -> bool:
    payload = _pid_payload()
    pid = int(payload.get("pid") or 0)
    if expected_pid is not None and pid and pid != expected_pid:
        return False
    if pid and _pid_is_running(pid) and _pid_looks_like_daemon(pid):
        return False
    try:
        PID_FILE.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _status_payload() -> dict:
    payload = _pid_payload()
    pid = int(payload.get("pid") or 0)
    pid_alive = bool(pid and _pid_is_running(pid))
    pid_is_daemon = bool(pid_alive and _pid_looks_like_daemon(pid))
    running = bool(pid_alive and pid_is_daemon)
    result = {
        "schema": "intern-agents.daemon-status.v1",
        "running": running,
        "pid": pid or None,
        "pid_file": str(PID_FILE),
        "pid_file_exists": PID_FILE.exists(),
        "pid_alive": pid_alive,
        "pid_is_daemon": pid_is_daemon,
        "work_agents_root": payload.get("work_agents_root") or "",
        "http_port": payload.get("http_port"),
        "ws_port": payload.get("ws_port"),
        "status": {},
    }
    if pid_alive and not pid_is_daemon:
        result["status_error"] = f"pid_file_points_to_non_daemon_process:{pid}"
    if running and payload.get("http_port"):
        try:
            with urllib.request.urlopen(f"http://localhost:{int(payload['http_port'])}/api/status", timeout=3) as resp:
                result["status"] = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            result["status_error"] = str(exc)
    return result


def _print_status(json_output: bool) -> int:
    status = _status_payload()
    if json_output:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        state = "running" if status["running"] else "not running"
        print(f"Daemon: {state}")
        if status["pid"]:
            print(f"  pid:  {status['pid']}")
        if status["http_port"]:
            print(f"  http: localhost:{status['http_port']}")
        if status["ws_port"]:
            print(f"  ws:   localhost:{status['ws_port']}")
        if status.get("status_error"):
            print(f"  status_error: {status['status_error']}")
    return 0 if status["running"] else 1


def _cmd_start(args) -> int:
    status = _status_payload()
    if status["running"]:
        print(f"Daemon already running (PID {status['pid']}).")
        return 0
    if PID_FILE.exists():
        if not _unlink_pid_file_if_stale():
            print(f"Error: refusing to remove live daemon pid file: {PID_FILE}", file=sys.stderr)
            return 1

    script = _daemon_script()
    if not os.path.isfile(script):
        print(f"Error: daemon script not found: {script}", file=sys.stderr)
        return 1

    root = _root()
    log_dir = system_log_dir(root, "daemon", script_path=script, component_version="unknown")
    log_dir.mkdir(parents=True, exist_ok=True)
    env = _load_user_env(root)
    if getattr(args, "foreground", False):
        os.execvpe("python3", ["python3", script], env)

    log_file = log_dir / "feishu_daemon.wrapper.log"
    with open(log_file, "a", encoding="utf-8") as log:
        proc = subprocess.Popen(
            ["python3", script],
            cwd=root,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    deadline = time.time() + 12
    last = {}
    while time.time() < deadline:
        last = _status_payload()
        if last["running"]:
            print(f"Daemon started (PID {last['pid']}).")
            print(f"  Log: {log_file}")
            return 0
        if proc.poll() is not None:
            print(f"Error: daemon exited immediately. Check log: {log_file}", file=sys.stderr)
            return 1
        time.sleep(0.5)
    print(f"Error: daemon did not become ready before timeout. Check log: {log_file}", file=sys.stderr)
    return 1


def _cmd_stop(_args) -> int:
    payload = _pid_payload()
    pid = int(payload.get("pid") or 0)
    if not pid or not _pid_is_running(pid) or not _pid_looks_like_daemon(pid):
        if PID_FILE.exists():
            _unlink_pid_file_if_stale()
        print("Daemon not running.")
        return 0
    os.kill(pid, signal.SIGTERM)
    deadline = time.time() + 8
    while time.time() < deadline:
        if not _pid_is_running(pid):
            break
        time.sleep(0.3)
    if _pid_is_running(pid):
        print(f"Error: daemon PID {pid} did not stop.", file=sys.stderr)
        return 1
    if PID_FILE.exists():
        _unlink_pid_file_if_stale(expected_pid=pid)
    print(f"Daemon stopped (PID {pid}).")
    return 0


def run(args) -> int:
    cmd = getattr(args, "daemon_command", None)
    if not cmd:
        print("Usage: internctl daemon {start|stop|restart|status}")
        return 1
    if cmd == "start":
        return _cmd_start(args)
    if cmd == "stop":
        return _cmd_stop(args)
    if cmd == "status":
        return _print_status(bool(getattr(args, "json", False)))
    if cmd == "restart":
        rc = _cmd_stop(args)
        if rc != 0:
            return rc
        return _cmd_start(argparse.Namespace(foreground=False))
    print(f"Unknown daemon command: {cmd}", file=sys.stderr)
    return 1
