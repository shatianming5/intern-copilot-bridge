from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shlex
import time
from typing import Any

from CI.helpers.native_error import NativeCaseError
from CI.helpers.product_cli_helper import parse_json_output, tail


def parse_resume_this_intern_hint_text(text: str) -> dict[str, Any]:
    lines = [line.rstrip() for line in (text or "").splitlines()]
    for index, line in enumerate(lines):
        if "Resume this intern" not in line or "echo" in line:
            continue
        command_parts: list[str] = []
        for candidate in lines[index + 1:index + 8]:
            cleaned = re.sub(r"^[\s>$#›]+", "", candidate).strip()
            if not cleaned or cleaned.startswith("echo "):
                continue
            if "internctl" in cleaned or command_parts:
                command_parts.append(cleaned)
                if "--type" in cleaned or len(command_parts) >= 3:
                    break
        command = " ".join(command_parts).strip()
        if command:
            return {"hint_line": line.strip(), "command": command, "line_index": index}
    return {}


@dataclass
class SessionActions:
    ctx: Any

    def _remote(self) -> Any:
        remote = getattr(self.ctx, "remote_context", None)
        if remote is None:
            raise RuntimeError("ctx.action.session.* requires RemoteCaseContext")
        return remote

    def start_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        remote.run_cmd(
            f"session start {intern}",
            [*remote.internctl, "session", "start", intern, "--project", str(workspace["display"]), "--no-attach"],
            timeout=240,
        )
        status = self.status_remote(intern)
        if status.get("running") is not True:
            raise NativeCaseError(f"session did not reach running state for {intern}: {status}")
        remote.created["sessions"].append(intern)
        tmux_session = str(status.get("tmux_session") or "")
        if tmux_session:
            remote.tmux_sessions[intern] = tmux_session
        return status

    def status_remote(self, intern: str) -> dict[str, Any]:
        remote = self._remote()
        return remote.json_cmd(
            f"session status {intern}",
            [*remote.internctl, "session", "status", intern, "--json"],
            timeout=90,
        )

    def stop_remote(self, intern: str) -> dict[str, Any]:
        remote = self._remote()
        result = remote.run_cmd(
            f"session stop {intern}",
            [*remote.internctl, "session", "stop", intern],
            timeout=120,
            check=False,
        )
        return {
            "intern": intern,
            "returncode": result.returncode,
            "stdout": tail(result.stdout, 1200),
            "stderr": tail(result.stderr, 1200),
        }

    def registry_remote(self) -> dict[str, Any]:
        remote = self._remote()
        return remote.session_registry()

    def status_for_workspace_remote(self, workspace: dict[str, Any], intern: str, *, check: bool = True) -> dict[str, Any]:
        remote = self._remote()
        result = remote.run_cmd(
            f"session status {intern} scoped",
            [*remote.internctl, "session", "status", intern, "--project", str(workspace["display"]), "--json"],
            timeout=90,
            check=check,
        )
        if result.returncode != 0 and not check:
            payload = parse_json_output(f"session status {intern} scoped", result.stdout) if result.stdout.strip() else {}
            if not isinstance(payload, dict):
                payload = {}
            payload.update({"returncode": result.returncode, "stderr": result.stderr})
            return payload
        payload = parse_json_output(f"session status {intern} scoped", result.stdout)
        if not isinstance(payload, dict):
            raise NativeCaseError(f"session status {intern} is not an object")
        return payload

    def start_for_workspace_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        session_type: str = "codex",
    ) -> dict[str, Any]:
        remote = self._remote()
        remote.run_cmd(
            f"session start {intern} scoped",
            [
                *remote.internctl,
                "session",
                "start",
                intern,
                "--project",
                str(workspace["display"]),
                "--type",
                session_type,
                "--no-attach",
            ],
            timeout=300,
        )
        status = self.status_for_workspace_remote(workspace, intern)
        if status.get("running") is not True:
            raise NativeCaseError(f"session did not reach running state for {intern}: {status}")
        tmux_session = str(status.get("tmux_session") or intern)
        remote.created["sessions"].append(tmux_session)
        remote.tmux_sessions[intern] = tmux_session
        return status

    def stop_for_workspace_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        result = remote.run_cmd(
            f"session stop {intern} scoped",
            [*remote.internctl, "session", "stop", intern, "--project", str(workspace["display"])],
            timeout=120,
            check=False,
        )
        return {
            "intern": intern,
            "project": str(workspace["display"]),
            "returncode": result.returncode,
            "stdout": tail(result.stdout, 1200),
            "stderr": tail(result.stderr, 1200),
        }

    def tmux_session_name_remote(self, session: str) -> str:
        remote = self._remote()
        cached = remote.tmux_sessions.get(session)
        if cached:
            return cached
        if session.startswith("ia_"):
            return session
        try:
            status = self.status_remote(session)
        except Exception:
            return session
        tmux_session = str(status.get("tmux_session") or "")
        if tmux_session:
            remote.tmux_sessions[session] = tmux_session
            return tmux_session
        return session

    def tmux_capture_remote(self, session: str, *, lines: int = 120) -> str:
        remote = self._remote()
        tmux_session = self.tmux_session_name_remote(session)
        return remote.tmux_helper.capture(
            session=session,
            tmux_session=tmux_session,
            lines=lines,
            joined=False,
            run_cmd=remote.run_cmd,
        )

    def tmux_capture_joined_remote(self, session: str, *, lines: int = 120) -> str:
        remote = self._remote()
        tmux_session = self.tmux_session_name_remote(session)
        return remote.tmux_helper.capture(
            session=session,
            tmux_session=tmux_session,
            lines=lines,
            joined=True,
            run_cmd=remote.run_cmd,
        )

    def wait_tmux_input_ready_remote(self, session: str, *, timeout: int = 120) -> dict[str, Any]:
        import time

        remote = self._remote()
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = self.tmux_capture_remote(session, lines=100)
            tail_text = last[-6000:]
            state = remote.tmux_helper.input_state(tail_text)
            if state["skip_update"]:
                remote.run_cmd(
                    f"tmux skip update {session}",
                    remote.tmux_helper.send_keys_command(session, state["skip_keys"]),
                    timeout=30,
                    check=False,
                )
                time.sleep(2)
                continue
            if state["ready"]:
                return {"ready": True, "tail": tail(tail_text, 1000)}
            time.sleep(2)
        raise NativeCaseError(f"timed out waiting for tmux input prompt for {session}: {tail(last, 2000)}")

    def tmux_send_remote(self, session: str, text: str) -> dict[str, Any]:
        remote = self._remote()
        return remote.tmux_helper.send_text(
            session=session,
            text=text,
            run_cmd=remote.run_cmd,
            wait_ready=lambda target: self.wait_tmux_input_ready_remote(target),
        )

    def registry_entries_for_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        project = str(workspace["display"])
        workspace_id = str(workspace["workspace_id"])
        entries: dict[str, Any] = {}
        for key, entry in remote.session_registry().items():
            if not isinstance(entry, dict):
                continue
            if entry.get("intern_name") != intern:
                continue
            scopes = {str(entry.get("project") or ""), str(entry.get("workspace_id") or "")}
            if ":" in str(key):
                scopes.add(str(key).split(":", 1)[0])
            if project in scopes or workspace_id in scopes:
                entries[str(key)] = dict(entry)
        return entries

    def resource_lookup_remote(self, session_resource: str) -> dict[str, Any]:
        remote = self._remote()
        for key, entry in remote.session_registry().items():
            if isinstance(entry, dict) and entry.get("sessionResource") == session_resource:
                intern = str(entry.get("intern_name") or str(key).rsplit(":", 1)[-1])
                project = str(entry.get("project") or (str(key).split(":", 1)[0] if ":" in str(key) else ""))
                return {
                    "found": True,
                    "key": str(key),
                    "project": project,
                    "intern": intern,
                    "entry": dict(entry),
                }
        return {"found": False, "key": "", "project": "", "intern": "", "entry": {}}

    def active_intern_from_resource_remote(self, session_resource: str) -> dict[str, Any]:
        lookup = self.resource_lookup_remote(session_resource)
        if not lookup.get("found"):
            return {
                "active": None,
                "reason_kind": "unknown_session_resource",
                "lookup": lookup,
                "intern": {},
                "matches": [],
                "items": [],
            }
        workspace = {
            "display": str(lookup["project"]),
            "workspace_id": str((lookup.get("entry") or {}).get("workspace_id") or ""),
        }
        intern_name = str(lookup["intern"])
        items = self.ctx.action.intern.list_json_remote()
        matches = [
            item for item in items
            if item.get("name") == intern_name
            and item.get("workspace_id") == workspace["workspace_id"]
            and item.get("project") == workspace["display"]
        ]
        intern = dict(matches[0]) if len(matches) == 1 else {}
        active = (
            {"project": lookup["project"], "intern": lookup["intern"], "state": intern.get("status")}
            if len(matches) == 1
            else None
        )
        if lookup.get("found") and len(matches) != 1:
            raise NativeCaseError(
                "session resource did not resolve to exactly one active intern: "
                + json.dumps({"lookup": lookup, "matches": matches, "items": items}, ensure_ascii=False)[:1200]
            )
        return {
            "active": active,
            "reason_kind": "" if len(matches) == 1 else "intern_list_match_count",
            "lookup": lookup,
            "intern": intern,
            "matches": matches,
            "items": items,
        }

    def write_registry_entry_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        session_resource: str,
        *,
        session_type: str = "codex",
        tmux_session: str = "",
    ) -> dict[str, Any]:
        remote = self._remote()
        path = remote.work_root / ".intern_sessions.json"
        sessions = self.registry_remote()
        key = f"{workspace['workspace_id']}:{intern}"
        existing = sessions.get(key) if isinstance(sessions.get(key), dict) else {}
        entry = {
            **existing,
            "sessionResource": session_resource,
            "sessionId": existing.get("sessionId", ""),
            "type": session_type,
            "intern_name": intern,
            "project": str(workspace["display"]),
            "workspace_id": str(workspace["workspace_id"]),
            "intern_dir": str(remote.ctx.action.intern.runtime_dir_remote(workspace, intern)),
        }
        if tmux_session:
            entry["tmux_session"] = tmux_session
        sessions[key] = entry
        path.write_text(json.dumps(sessions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"path": str(path), "key": key, "entry": entry}

    def delete_registry_entry_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        path = remote.work_root / ".intern_sessions.json"
        sessions = self.registry_remote()
        key = f"{workspace['workspace_id']}:{intern}"
        removed = sessions.pop(key, None)
        path.write_text(json.dumps(sessions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"path": str(path), "key": key, "removed": removed, "remaining": sessions}

    @staticmethod
    def _uuid_like(value: str) -> bool:
        return bool(re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            value or "",
        ))

    @staticmethod
    def _codex_session_id_from_rollout_name(path: Path) -> str:
        match = re.search(
            r"rollout-.*-([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\.jsonl$",
            path.name,
        )
        return match.group(1) if match else ""

    def _codex_transcript_candidate_paths(self, runtime: Path) -> list[Path]:
        candidates: list[Path] = []
        hook_state = runtime / ".hook_state.json"
        if hook_state.is_file():
            try:
                state = json.loads(hook_state.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                state = {}
            log = state.get("log") if isinstance(state, dict) else {}
            transcript_path = str(log.get("transcript_path") or "") if isinstance(log, dict) else ""
            if transcript_path:
                candidates.append(Path(transcript_path))

        codex_home = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
        sessions_dir = codex_home / "sessions"
        if sessions_dir.is_dir():
            scanned: list[tuple[float, Path]] = []
            try:
                for path in sessions_dir.rglob("rollout-*.jsonl"):
                    try:
                        scanned.append((path.stat().st_mtime, path))
                    except OSError:
                        continue
            except OSError:
                scanned = []
            candidates.extend(path for _, path in sorted(scanned, reverse=True)[:240])

        seen: set[str] = set()
        unique: list[Path] = []
        for path in candidates:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def product_latest_codex_session_id_remote(self, intern_dir: Path) -> dict[str, Any]:
        detail = {
            "source": "commands.session._latest_codex_session_id",
            "intern_dir": str(intern_dir),
            "session_id": "",
            "available": False,
            "error": "",
        }
        try:
            from commands import session as session_cmd

            session_id = str(session_cmd._latest_codex_session_id(str(intern_dir)) or "")
        except Exception as exc:  # noqa: BLE001
            detail["error"] = repr(exc)
            return detail
        detail["session_id"] = session_id
        detail["available"] = self._uuid_like(session_id)
        if session_id and not detail["available"]:
            detail["error"] = "product helper returned a non-UUID session id"
        return detail

    def codex_session_id_evidence_remote(self, workspace: dict[str, Any], intern: str) -> dict[str, Any]:
        remote = self._remote()
        runtime = remote.runtime_dir(workspace, intern).resolve(strict=False)
        codex_home = Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))
        product_latest = self.product_latest_codex_session_id_remote(runtime)
        matched: list[dict[str, Any]] = []
        recent: list[dict[str, Any]] = []
        candidates = self._codex_transcript_candidate_paths(runtime)
        runtime_text = str(runtime)

        for path in candidates:
            try:
                stat = path.stat()
                first_line = path.open("r", encoding="utf-8", errors="replace").readline()
            except OSError:
                continue
            if not first_line:
                continue
            payload: dict[str, Any] = {}
            try:
                parsed = json.loads(first_line)
            except json.JSONDecodeError:
                parsed = {}
            if isinstance(parsed, dict):
                nested = parsed.get("payload")
                payload = nested if isinstance(nested, dict) else parsed
            cwd = str(payload.get("cwd") or "")
            session_id = str(payload.get("id") or "")
            if not self._uuid_like(session_id):
                session_id = self._codex_session_id_from_rollout_name(path)
            cwd_match = False
            if cwd:
                try:
                    cwd_text = str(Path(cwd).resolve(strict=False))
                except Exception:  # noqa: BLE001
                    cwd_text = cwd
                cwd_match = cwd_text == runtime_text or cwd_text.startswith(runtime_text + os.sep)
            fallback_match = runtime_text in first_line
            item = {
                "path": str(path),
                "mtime": stat.st_mtime,
                "cwd": cwd,
                "session_id": session_id,
                "cwd_match": cwd_match,
                "fallback_match": fallback_match,
            }
            if len(recent) < 10:
                recent.append(item)
            if session_id and (cwd_match or fallback_match):
                matched.append(item)

        matched.sort(key=lambda item: float(item.get("mtime") or 0), reverse=True)
        evidence: dict[str, Any] = {
            "schema": "intern-agents.ci.codex-session-id-evidence.v1",
            "workspace": str(workspace.get("display") or ""),
            "workspace_id": str(workspace.get("workspace_id") or ""),
            "intern": intern,
            "runtime_dir": runtime_text,
            "codex_home": str(codex_home),
            "sessions_dir": str(codex_home / "sessions"),
            "candidate_count": len(candidates),
            "matched_count": len(matched),
            "recent_candidates": recent,
            "product_latest": product_latest,
            "available": bool(product_latest.get("available") or matched),
            "failure_classification": "",
        }
        if product_latest.get("available"):
            evidence.update({
                "session_id": product_latest.get("session_id"),
                "transcript_path": "",
                "transcript_cwd": "",
                "source": "commands.session._latest_codex_session_id",
            })
        elif matched:
            chosen = matched[0]
            evidence.update({
                "session_id": chosen.get("session_id"),
                "transcript_path": chosen.get("path"),
                "transcript_cwd": chosen.get("cwd"),
                "source": "codex_transcript",
            })
        else:
            evidence.update({
                "session_id": "",
                "transcript_path": "",
                "source": "",
                "reason": "ci_capability_gap_session_id_discovery",
                "failure_classification": "ci_capability_gap_session_id_discovery",
            })
        return evidence

    def restart_for_workspace_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        session_type: str,
        timeout: int = 360,
    ) -> dict[str, Any]:
        remote = self._remote()
        restart = remote.run_cmd(
            f"session restart {intern} scoped",
            [
                *remote.internctl,
                "session",
                "restart",
                intern,
                "--project",
                str(workspace["display"]),
                "--type",
                session_type,
                "--no-attach",
            ],
            timeout=timeout,
            check=False,
        )
        output = restart.stdout + restart.stderr
        lowered = output.lower()
        uuid_match = re.search(
            r"restarted via resume\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})",
            output,
            flags=re.IGNORECASE,
        )
        status = self.status_for_workspace_remote(workspace, intern)
        tmux_session = str(status.get("tmux_session") or intern)
        return {
            "returncode": restart.returncode,
            "stdout": restart.stdout,
            "stderr": restart.stderr,
            "output": output,
            "resume_uuid": uuid_match.group(1) if uuid_match else "",
            "reported_resume": "restarted via resume" in output,
            "reported_fresh": "restarted fresh" in output or "restarted\n" in output or output.strip().endswith(": restarted"),
            "session_status": status,
            "tmux_session": tmux_session,
            "failure_classification": (
                ""
                if restart.returncode == 0
                else "product_bug_claude_restart_not_resume"
                if session_type == "claude" and ("restart resume failed" in lowered or "uuid capture failed" in lowered)
                else "product_bug_claude_session_not_live"
                if session_type == "claude"
                else "ci_assertion_or_product_bug"
            ),
        }

    @staticmethod
    def _parse_resume_this_intern_hint(text: str) -> dict[str, Any]:
        return parse_resume_this_intern_hint_text(text)

    def wait_resume_this_intern_hint_remote(self, session: str, *, timeout: int = 180) -> dict[str, Any]:
        deadline = time.time() + timeout
        last = ""
        while time.time() < deadline:
            last = self.tmux_capture_joined_remote(session, lines=180)
            parsed = self._parse_resume_this_intern_hint(last)
            if parsed:
                return parsed | {"tail": tail(last, 2400), "tmux_session": session}
            time.sleep(2)
        return {"tmux_session": session, "tail": tail(last, 4000), "missing": True}

    def tmux_provider_processes_remote(self, tmux_session: str, provider: str) -> dict[str, Any]:
        remote = self._remote()
        pane_pids = remote.run_cmd(
            f"tmux pane pids {tmux_session}",
            ["tmux", "list-panes", "-t", f"={tmux_session}", "-F", "#{pane_pid}"],
            timeout=30,
            check=False,
        )
        root_pids = {
            int(line.strip())
            for line in pane_pids.stdout.splitlines()
            if line.strip().isdigit()
        }
        if pane_pids.returncode != 0 or not root_pids:
            return {
                "tmux_session": tmux_session,
                "provider": provider,
                "root_pids": sorted(root_pids),
                "matches": [],
                "error": pane_pids.stderr or pane_pids.stdout,
            }

        process_list = remote.run_cmd(
            f"process list for {tmux_session}",
            ["ps", "-eww", "-o", "pid=,ppid=,comm=,args="],
            timeout=30,
            check=False,
        )
        children: dict[int, list[int]] = {}
        processes: dict[int, dict[str, Any]] = {}
        for raw_line in process_list.stdout.splitlines():
            parts = raw_line.strip().split(None, 3)
            if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
                continue
            pid = int(parts[0])
            ppid = int(parts[1])
            comm = parts[2]
            args = parts[3] if len(parts) > 3 else comm
            processes[pid] = {"pid": pid, "ppid": ppid, "comm": comm, "args": args}
            children.setdefault(ppid, []).append(pid)

        descendants: set[int] = set()
        stack = list(root_pids)
        while stack:
            pid = stack.pop()
            if pid in descendants:
                continue
            descendants.add(pid)
            stack.extend(children.get(pid, []))

        provider_lower = provider.lower()

        def is_provider_process(process: dict[str, Any]) -> bool:
            comm = str(process.get("comm") or "").lower()
            args = str(process.get("args") or "")
            args_lower = args.lower()
            first_arg = ""
            try:
                first_arg = shlex.split(args)[0]
            except Exception:  # noqa: BLE001
                first_arg = args.split(" ", 1)[0] if args else ""
            first_name = Path(first_arg).name.lower()
            return (
                comm == provider_lower
                or first_name == provider_lower
                or f"/{provider_lower}" in args_lower
                or f"{provider_lower} --" in args_lower
            )

        matches = [
            {
                "pid": processes[pid]["pid"],
                "ppid": processes[pid]["ppid"],
                "comm": processes[pid]["comm"],
                "args_tail": tail(processes[pid]["args"], 4000),
            }
            for pid in sorted(descendants)
            if pid in processes and is_provider_process(processes[pid])
        ]
        return {
            "tmux_session": tmux_session,
            "provider": provider,
            "root_pids": sorted(root_pids),
            "matches": matches,
            "process_list_ok": process_list.returncode == 0,
        }

    def tmux_environment_values_remote(self, tmux_session: str, keys: tuple[str, ...]) -> dict[str, str]:
        remote = self._remote()
        values: dict[str, str] = {}
        for key in keys:
            result = remote.run_cmd(
                f"tmux env {tmux_session} {key}",
                ["tmux", "show-environment", "-t", f"={tmux_session}", key],
                timeout=30,
                check=False,
            )
            prefix = f"{key}="
            if result.returncode == 0 and result.stdout.strip().startswith(prefix):
                values[key] = result.stdout.strip()[len(prefix):]
            else:
                values[key] = ""
        return values

    def wait_provider_session_live_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        *,
        provider: str,
        timeout: int,
    ) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            status = self.status_for_workspace_remote(workspace, intern, check=False)
            tmux_session = str(status.get("tmux_session") or intern)
            tmux = self._remote().run_cmd(
                f"tmux has session {tmux_session}",
                ["tmux", "has-session", "-t", f"={tmux_session}"],
                timeout=30,
                check=False,
            )
            processes = self.tmux_provider_processes_remote(tmux_session, provider) if tmux.returncode == 0 else {}
            last = {"session_status": status, "tmux": {"returncode": tmux.returncode, "stderr": tmux.stderr}, "processes": processes}
            if status.get("running") is True and tmux.returncode == 0 and processes.get("matches"):
                ready: dict[str, Any] = {}
                try:
                    ready = self.wait_tmux_input_ready_remote(tmux_session, timeout=min(120, max(10, int(deadline - time.time()))))
                except Exception as exc:  # noqa: BLE001
                    ready = {"ready": False, "error": str(exc), "tail": tail(self.tmux_capture_remote(tmux_session, lines=120), 1000)}
                last["ready"] = ready
                if ready.get("ready") is True:
                    return last
            time.sleep(3)
        return last | {"live": False}

    def wait_codex_live_after_manual_resume_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        tmux_session: str,
        *,
        timeout: int = 300,
    ) -> dict[str, Any]:
        from CI.assertions import session as session_assertions

        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            status = self.status_for_workspace_remote(workspace, intern, check=False)
            tmux = self._remote().run_cmd(
                f"tmux has manual codex resume session {tmux_session}",
                ["tmux", "has-session", "-t", f"={tmux_session}"],
                timeout=30,
                check=False,
            )
            pane_text = self.tmux_capture_joined_remote(tmux_session, lines=240) if tmux.returncode == 0 else ""
            processes = self.tmux_provider_processes_remote(tmux_session, "codex") if tmux.returncode == 0 else {}
            resumed_id = session_assertions.codex_session_id_from_text(pane_text)
            lowered = pane_text.lower()
            last = {
                "session_status": status,
                "tmux": {"returncode": tmux.returncode, "stderr": tmux.stderr},
                "processes": processes,
                "resumed_session_id": resumed_id,
                "pane_tail": tail(pane_text, 4000),
                "resume_failure_seen": "resume failed:" in lowered or "codex session id unavailable" in lowered,
            }
            if status.get("running") is True and tmux.returncode == 0 and processes.get("matches"):
                ready = self.wait_tmux_input_ready_remote(tmux_session, timeout=min(120, max(10, int(deadline - time.time()))))
                if ready.get("ready") is True:
                    return {**last, "ready": ready}
            time.sleep(3)
        return last | {"live": False}

    def prepare_claude_policy_token_remote(self) -> dict[str, Any]:
        remote = self._remote()
        auth_env_keys = {
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_API_KEY",
        }

        def env_file_evidence(path: Path) -> dict[str, Any]:
            managed_keys: list[str] = []
            export_keys: list[str] = []
            if path.is_file():
                for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = raw_line.strip()
                    if line.startswith("# managed_env_keys:"):
                        managed_keys = [
                            item.strip()
                            for item in line.split(":", 1)[1].split()
                            if item.strip()
                        ]
                        continue
                    export_match = re.match(r"^(?:export\s+)?([A-Z_][A-Z0-9_]*)=", line)
                    if export_match:
                        export_keys.append(export_match.group(1))
            return {
                "path": str(path),
                "exists": path.is_file(),
                "managed_env_keys": sorted(set(managed_keys)),
                "export_keys": sorted(set(export_keys)),
                "auth_env_keys_present": sorted(auth_env_keys & set(export_keys)),
            }

        runtime_env = env_file_evidence(remote.work_root / "enterprise_policy" / "daemon" / "runtime" / "claude.env")
        user_env = env_file_evidence(remote.work_root / "enterprise_policy" / "daemon" / "user.env")
        process_env_keys = sorted(key for key in auth_env_keys if remote.env.get(key))
        report_path = remote.work_root / "enterprise_policy" / "daemon" / "runtime" / "session_env_report.json"
        report_claude: dict[str, Any] = {}
        if report_path.is_file():
            try:
                report = json.loads(report_path.read_text(encoding="utf-8"))
                provider = (report.get("providers") or {}).get("claude") if isinstance(report, dict) else {}
                if isinstance(provider, dict):
                    report_claude = {
                        "enabled": provider.get("enabled"),
                        "hash_env": provider.get("hash_env"),
                        "hash_present": bool(provider.get("hash")),
                        "managed_env_keys": provider.get("managed_env_keys") or [],
                        "missing_secret_refs": sorted((provider.get("missing_secret_refs") or {}).keys())
                        if isinstance(provider.get("missing_secret_refs"), dict) else [],
                        "changed": provider.get("changed"),
                    }
            except Exception as exc:  # noqa: BLE001
                report_claude = {"parse_error": str(exc)}
        auth_sources = {
            "runtime_env": runtime_env["auth_env_keys_present"],
            "user_env": user_env["auth_env_keys_present"],
            "process_env": process_env_keys,
        }
        evidence = {
            "policy_alias": "sk-xiaohan.yi",
            "secret_value": "<redacted>",
            "runtime_env": runtime_env,
            "user_env": user_env,
            "session_env_report": {
                "path": str(report_path),
                "exists": report_path.is_file(),
                "claude": report_claude,
            },
            "auth_sources": auth_sources,
            "auth_materialized": any(bool(values) for values in auth_sources.values()),
        }
        remote.artifacts["claude_policy_token"] = evidence
        return evidence
