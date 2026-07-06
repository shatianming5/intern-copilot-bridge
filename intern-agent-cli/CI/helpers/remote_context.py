from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from CI.actions.context import CaseContext
from CI.helpers.mock_feishu_helper import MockFeishuHelper
from CI.helpers.mock_treeview_helper import MockTreeViewHelper
from CI.helpers.native_error import NativeCaseError
from CI.helpers.product_cli_helper import ProductCliHelper, parse_json_output, tail
from CI.helpers.reporting import redact_report_value
from CI.helpers.remote_machine_helper import FileArtifactHelper, RemoteMachineHelper, TmuxHelper
from CI.helpers.source_contract_helper import SourceContractHelper


def remote_resource_namespace(case_id: str) -> str:
    case_no = case_id.split("_", 2)[1]
    prefix = case_id.split("_", 1)[0].lower()
    return f"ci_{prefix}_{case_no}" if prefix else f"ci_{case_no}"


def remote_runtime_namespace(case_id: str) -> str:
    stage = case_id.split("_", 1)[0].lower()
    if stage in {"f", "j"}:
        return remote_resource_namespace(case_id)
    case_no = case_id.split("_", 2)[1]
    return f"ci_{case_no}"


@dataclass
class RemoteCaseContext:
    args: Any
    machine_id: str
    case_id: str = field(init=False)
    case_no: str = field(init=False)
    work_root: Path = field(init=False)
    repo_root: Path = field(init=False)
    artifact_dir: Path = field(init=False)
    report_path: Path = field(init=False)
    cli_root: Path = field(init=False)
    internctl: list[str] = field(init=False)
    adminctl: list[str] = field(init=False)
    codeup_pr: list[str] = field(init=False)
    env: dict[str, str] = field(init=False)
    product_cli: ProductCliHelper = field(init=False)
    remote_machine: RemoteMachineHelper = field(init=False)
    tmux_helper: TmuxHelper = field(init=False)
    file_artifacts: FileArtifactHelper = field(init=False)
    source_contract: SourceContractHelper = field(init=False)
    mock_feishu: MockFeishuHelper = field(init=False)
    mock_treeview: MockTreeViewHelper = field(init=False)
    ctx: CaseContext = field(init=False)
    steps: list[dict[str, Any]] = field(init=False, default_factory=list)
    checks: list[dict[str, Any]] = field(init=False, default_factory=list)
    scenarios: list[dict[str, Any]] = field(init=False, default_factory=list)
    failure_classification: str = ""
    resource_namespace: str = field(init=False)
    artifacts: dict[str, Any] = field(init=False, default_factory=dict)
    tmux_sessions: dict[str, str] = field(init=False, default_factory=dict)
    created: dict[str, list[str]] = field(init=False)
    run_token: str = field(init=False)

    def __post_init__(self) -> None:
        self.case_id = str(self.args.case_id)
        self.case_no = self.case_id.split("_", 2)[1]
        self.work_root = Path(self.args.work_root)
        self.repo_root = Path(self.args.repo_root)
        self.artifact_dir = Path(self.args.artifact_dir)
        self.report_path = Path(self.args.report)
        self.cli_root = self.repo_root
        self.internctl = [sys.executable, str(self.cli_root / "internctl.py")]
        self.adminctl = [sys.executable, str(self.cli_root / "intern-adminctl.py")]
        self.codeup_pr = [sys.executable, str(self.cli_root / "codeup_pr.py")]
        self.env = os.environ.copy()
        self.env["WORK_AGENTS_ROOT"] = str(self.work_root)
        self.env["PATH"] = f"{self.cli_root}:{self.env.get('PATH', '')}"
        self.product_cli = ProductCliHelper(
            env=self.env,
            default_cwd=self.work_root,
            default_timeout=self.args.timeout,
        )
        self.remote_machine = RemoteMachineHelper(default_timeout=self.args.timeout)
        self.tmux_helper = TmuxHelper(self.artifact_dir)
        self.file_artifacts = FileArtifactHelper()
        self.source_contract = SourceContractHelper(repo_root=self.repo_root, work_root=self.work_root)
        self.mock_feishu = MockFeishuHelper()
        self.mock_treeview = MockTreeViewHelper()
        self.ctx = CaseContext.for_case_id(
            self.case_id,
            repo_root=self.repo_root,
            work_root=self.work_root,
            artifact_dir=self.artifact_dir,
            machine={"id": self.machine_id},
        )
        self.ctx.remote_context = self
        self.resource_namespace = remote_resource_namespace(self.case_id)
        self.created = {
            "workspaces": [],
            "interns": [],
            "sessions": [],
            "repos": [],
        }
        self.run_token = re.sub(r"[^A-Za-z0-9_.-]+", "_", os.environ.get("INTERN_CI_RUN_ID", "")).strip("_")
        if not self.run_token:
            self.run_token = f"r{int(time.time())}_{os.getpid()}"
        self.artifact_dir.mkdir(parents=True, exist_ok=True)

    def identity(self, role: str) -> str:
        return f"intern_{self.resource_namespace}_{role}"

    def workspace_name(self, suffix: str) -> str:
        return f"{self.resource_namespace}_{suffix}"

    def intern_name_prefix(self) -> str:
        return self.identity("")

    def workspace_name_prefix(self) -> str:
        return self.workspace_name("")

    def stage_workspace_prefix(self) -> str:
        return f"{remote_runtime_namespace(self.case_id)}_"

    def stage_workspace_display(self, suffix: str) -> str:
        safe_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", suffix).strip("_") or "workspace"
        return f"{self.stage_workspace_prefix()}{safe_suffix}_{self.run_token}"

    def stage_intern_name(self, suffix: str = "probe") -> str:
        safe_suffix = re.sub(r"[^A-Za-z0-9_.-]+", "_", suffix).strip("_") or "probe"
        return f"intern_{remote_runtime_namespace(self.case_id)}_{safe_suffix}_{self.run_token}"

    def task_id(self, purpose: str) -> str:
        return f"task_{remote_runtime_namespace(self.case_id)}_{purpose}"

    def file_name(self, purpose: str) -> str:
        return f"file_{remote_runtime_namespace(self.case_id)}_{purpose}.txt"

    def run_cmd(
        self,
        name: str,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        result, step = self.product_cli.run_command(name, cmd, cwd=cwd, timeout=timeout)
        self.steps.append(step)
        if check and result.returncode != 0:
            raise NativeCaseError(f"{name} failed: {tail(result.stderr or result.stdout, 1200)}")
        return result

    def json_cmd(
        self,
        name: str,
        cmd: list[str],
        *,
        cwd: Path | None = None,
        timeout: int | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        result = self.run_cmd(name, cmd, cwd=cwd, timeout=timeout, check=check)
        if result.returncode != 0 and not check and not result.stdout.strip():
            return {"ok": False, "returncode": result.returncode, "stderr": result.stderr}
        payload = parse_json_output(name, result.stdout)
        if not isinstance(payload, dict):
            raise NativeCaseError(f"{name} JSON output is not an object")
        return payload

    @staticmethod
    def daemon_base() -> str:
        payload = json.loads(Path("/tmp/feishu_daemon.json").read_text(encoding="utf-8"))
        port = int(payload["http_port"])
        return f"http://127.0.0.1:{port}"

    def relay_base(self) -> str:
        owner_path = self.work_root / "enterprise_policy" / "daemon" / "_owner.json"
        if owner_path.is_file():
            owner = json.loads(owner_path.read_text(encoding="utf-8"))
            relay_http = str(owner.get("relay_http_url") or "").rstrip("/")
            if relay_http:
                return relay_http
        return "http://127.0.0.1:28080"

    def http_json(
        self,
        name: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        result, step = self.remote_machine.request_json(
            name,
            method,
            self.daemon_base() + path,
            payload,
            timeout=timeout,
            include_url=False,
            path=path,
        )
        status = int(result["status_code"])
        body = result["body"]
        self.steps.append(redact_report_value(step))
        if status >= 400:
            redacted_body = json.dumps(redact_report_value(body), ensure_ascii=False)
            raise NativeCaseError(f"{name} failed: HTTP {status}: {redacted_body[:1200]}")
        if not isinstance(body, dict):
            raise NativeCaseError(f"{name} response is not an object")
        return body

    def relay_json(
        self,
        name: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        result, step = self.remote_machine.request_json(
            name,
            method,
            self.relay_base() + path,
            payload,
            timeout=timeout,
            include_url=False,
            path=path,
        )
        status = int(result["status_code"])
        body = result["body"]
        self.steps.append(redact_report_value(step))
        if status >= 400:
            redacted_body = json.dumps(redact_report_value(body), ensure_ascii=False)
            raise NativeCaseError(f"{name} failed: HTTP {status}: {redacted_body[:1200]}")
        if not isinstance(body, dict):
            raise NativeCaseError(f"{name} response is not an object")
        return body

    def request_json(
        self,
        name: str,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        check: bool = True,
        log_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        result, step = self.remote_machine.request_json(
            name,
            method,
            url,
            payload,
            headers=headers,
            timeout=timeout,
            log_payload=log_payload,
        )
        status = int(result["status_code"])
        body = result["body"]
        self.steps.append(redact_report_value(step))
        if status >= 400 and check:
            redacted_body = json.dumps(redact_report_value(body), ensure_ascii=False)
            raise NativeCaseError(f"{name} failed: HTTP {status}: {redacted_body[:1200]}")
        if not isinstance(body, dict):
            raise NativeCaseError(f"{name} response is not an object")
        return {"status_code": status, "body": body, "ok": status < 400}

    def daemon_request_json(
        self,
        name: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        return self.request_json(
            name,
            method,
            self.daemon_base() + path,
            payload,
            timeout=timeout,
            check=check,
        )

    def relay_request_json(
        self,
        name: str,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        return self.request_json(
            name,
            method,
            self.relay_base() + path,
            payload,
            timeout=timeout,
            check=check,
        )

    def request_any_json(
        self,
        name: str,
        method: str,
        url: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: int | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        result, step = self.remote_machine.request_json(
            name,
            method,
            url,
            payload,
            timeout=timeout,
        )
        status = int(result["status_code"])
        body = result["body"]
        self.steps.append(redact_report_value(step))
        if status >= 400 and check:
            redacted_body = json.dumps(redact_report_value(body), ensure_ascii=False)
            raise NativeCaseError(f"{name} failed: HTTP {status}: {redacted_body[:1200]}")
        return {"status_code": status, "body": body, "ok": status < 400}

    def feishu_credentials(self) -> tuple[str, str]:
        key_path = self.work_root / "key.txt"
        if not key_path.is_file():
            raise NativeCaseError(f"Feishu app credential file missing: {key_path}")
        lines = [line.strip() for line in key_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(lines) < 2:
            raise NativeCaseError(f"Feishu app credential file is incomplete: {key_path}")
        return lines[0], lines[1]

    def session_registry(self) -> dict[str, Any]:
        path = self.work_root / ".intern_sessions.json"
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise NativeCaseError(f"session registry is not an object: {path}")
        return data

    def runtime_dir(self, workspace: dict[str, Any], intern: str) -> Path:
        return self.work_root / "state" / "v1" / str(workspace["workspace_id"]) / "interns" / intern
