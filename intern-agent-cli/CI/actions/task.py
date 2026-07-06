from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from CI.helpers.native_error import NativeCaseError


@dataclass
class TaskActions:
    ctx: Any

    def _remote(self) -> Any:
        remote = getattr(self.ctx, "remote_context", None)
        if remote is None:
            raise RuntimeError("ctx.action.task.* requires RemoteCaseContext")
        return remote

    def parse_status_metadata_remote(self, status_path: Path | str) -> dict[str, str]:
        path = Path(status_path)
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        match = re.search(r"<!--\s*METADATA:(?P<body>.+?)\s*-->", text)
        if not match:
            return {}
        result: dict[str, str] = {}
        for pair in match.group("body").split(","):
            if "=" not in pair:
                continue
            key, value = pair.split("=", 1)
            result[key.strip().lower()] = value.strip()
        return result

    def write_fixture_remote(
        self,
        metadata_root: Path | str,
        task_id: str,
        *,
        status: str = "Open",
        assignee: str = "",
    ) -> dict[str, str]:
        root = Path(metadata_root)
        task_dir = root / "tasks" / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        readme = task_dir / "README.md"
        history = task_dir / "history_log.md"
        knowledge = task_dir / "task_knowledge.md"
        readme.write_text(
            f"# {task_id}\n\n<!-- METADATA:STATUS={status},ASSIGNEE={assignee} -->\n\n## Fixture\n\n- CI fixture task.\n",
            encoding="utf-8",
        )
        history.write_text(f"# {task_id} - history\n\n<!-- METADATA:SESSION=0 -->\n", encoding="utf-8")
        knowledge.write_text(f"# {task_id} - knowledge\n\n<!-- METADATA:SESSION=0 -->\n", encoding="utf-8")
        return {
            "task_dir": str(task_dir),
            "readme": str(readme),
            "history": str(history),
            "knowledge": str(knowledge),
            "status": status,
            "assignee": assignee,
        }

    def write_readme_fixture_remote(
        self,
        tasks_dir: Path | str,
        name: str,
        *,
        status: str,
        assignee: str = "",
        metadata_line: int = 3,
    ) -> dict[str, Any]:
        task_dir = Path(tasks_dir) / name
        task_dir.mkdir(parents=True, exist_ok=True)
        metadata = f"<!-- METADATA:STATUS={status},ASSIGNEE={assignee} -->"
        if metadata_line == 3:
            text = f"# {name} - CI fixture\n\n{metadata}\n\n## Scope\n\n- Task TreeView fixture.\n"
        elif metadata_line == 4:
            text = f"# {name} - CI fixture\n\n## Wrong line\n{metadata}\n\n- Task TreeView malformed fixture.\n"
        else:
            raise NativeCaseError(f"unsupported metadata line: {metadata_line}")
        readme = task_dir / "README.md"
        readme.write_text(text, encoding="utf-8")
        return {
            "task": name,
            "task_dir": str(task_dir),
            "readme": str(readme),
            "status": status,
            "assignee": assignee,
            "metadata_line": metadata_line,
        }

    def write_intern_status_metadata_remote(
        self,
        status_path: Path | str,
        *,
        status: str,
        task: str,
        role: str,
        team_id: str,
        pr: str | None = None,
    ) -> dict[str, Any]:
        path = Path(status_path)
        text = path.read_text(encoding="utf-8", errors="replace")
        metadata = f"<!-- METADATA:STATUS={status},TASK={task},ROLE={role},TEAM_ID={team_id} -->"
        lines: list[str] = []
        replaced = False
        for line in text.splitlines():
            if "<!-- METADATA:" in line:
                lines.append(metadata)
                replaced = True
                continue
            stripped = line.strip()
            if stripped.startswith("| Status |"):
                lines.append(f"| Status | {status} |")
            elif stripped.startswith("| Current Task |"):
                lines.append(f"| Current Task | {task} |")
            elif stripped.startswith("| PR |") and pr is not None:
                lines.append(f"| PR | {pr} |")
            else:
                lines.append(line)
        if not replaced:
            lines.insert(2, metadata)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {
            "status_path": str(path),
            "status": status,
            "task": task,
            "role": role,
            "team_id": team_id,
            "pr": pr,
            "metadata": metadata,
            "metadata_replaced": replaced,
            "status_meta": self.parse_status_metadata_remote(path),
        }

    def write_working_fixture_remote(
        self,
        workspace: dict[str, Any],
        intern: str,
        metadata: dict[str, Any],
        task_id: str,
    ) -> dict[str, Any]:
        metadata_root = Path(str(metadata.get("metadata_root") or ""))
        if not metadata_root.is_dir():
            raise NativeCaseError(f"metadata_root missing for task fixture: {metadata_root}")
        task = self.write_fixture_remote(metadata_root, task_id, status="InProgress", assignee=intern)
        status_path = Path(str(metadata.get("status_path") or ""))
        status_meta = self.parse_status_metadata_remote(status_path)
        status_update = self.write_intern_status_metadata_remote(
            status_path,
            status="Working",
            task=task_id,
            role=status_meta.get("role") or "independent",
            team_id=status_meta.get("team_id") or "",
        )
        return {
            "workspace": str(workspace.get("display") or ""),
            "intern": intern,
            "task_id": task_id,
            "task_dir": str(metadata_root / "tasks" / task_id),
            "task": task,
            "status_update": status_update,
        }

    def seed_treeview_intern_status_remote(
        self,
        workspace: dict[str, Any],
        *,
        intern: str,
        task: str,
        pr: str,
    ) -> dict[str, str]:
        root = self.ctx.action.workspace.metadata_root_remote(workspace)
        status_dir = root / "interns" / intern
        status_dir.mkdir(parents=True, exist_ok=True)
        status_path = status_dir / "status.md"
        status_path.write_text(
            f"# {intern} - status\n\n"
            f"<!-- METADATA:STATUS=Working,TASK={task},ROLE=independent,TEAM_ID= -->\n\n"
            "| Field | Value |\n"
            "|------|-----|\n"
            f"| Name | {intern} |\n"
            "| Status | Working |\n"
            f"| Current Task | {task} |\n"
            "| Branch | ci-task-treeview |\n"
            f"| PR | {pr} |\n",
            encoding="utf-8",
        )
        knowledge_path = status_dir / "knowledge.md"
        knowledge_path.write_text(f"# {intern} - knowledge\n\n<!-- METADATA:SESSION=0 -->\n\n- CI fixture.\n", encoding="utf-8")
        return {
            "status_path": str(status_path),
            "knowledge_path": str(knowledge_path),
            "metadata_root": str(root),
            "intern": intern,
            "task": task,
            "pr": pr,
        }
