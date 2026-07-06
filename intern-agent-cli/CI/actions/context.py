from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from CI.cases.base import CaseDefinition
from CI.cases.registry import case_by_id_or_name


@dataclass(frozen=True)
class CaseIdentity:
    case_id: str
    case_name: str = ""

    @property
    def case_no(self) -> str:
        return self.case_id.split("_", 2)[1]

    def workspace_name(self, name: str) -> str:
        return f"ci_{self.case_no}_{name}"

    def intern_name(self, role: str = "worker") -> str:
        return f"intern_ci_{self.case_no}_{role}"

    def task_name(self, name: str) -> str:
        return f"task_ci_{self.case_no}_{name}"

    def file_name(self, name: str, suffix: str = ".txt") -> str:
        return f"file_ci_{self.case_no}_{name}{suffix}"


@dataclass
class ActionRoot:
    ctx: "CaseContext"

    def __post_init__(self) -> None:
        from CI.actions.registry import ACTION_ROOT_FACTORIES

        for root_name, factory in ACTION_ROOT_FACTORIES.items():
            setattr(self, root_name, factory(self.ctx))


@dataclass
class CaseContext:
    case: CaseDefinition
    repo_root: Path
    work_root: Path
    artifact_dir: Path
    machine: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root)
        self.work_root = Path(self.work_root)
        self.artifact_dir = Path(self.artifact_dir)
        self.cli_root = self.repo_root / "intern-cli" if (self.repo_root / "intern-cli").is_dir() else self.repo_root
        self.identity = CaseIdentity(self.case.id, self.case.name)
        self.action = ActionRoot(self)
        from CI.assertions import CaseAssertions

        self.assertion = CaseAssertions(self)

    @classmethod
    def for_case_id(
        cls,
        case_id: str,
        *,
        repo_root: str | Path,
        work_root: str | Path,
        artifact_dir: str | Path,
        machine: dict[str, Any] | None = None,
    ) -> "CaseContext":
        return cls(
            case=case_by_id_or_name(case_id, include_disabled=True),
            repo_root=Path(repo_root),
            work_root=Path(work_root),
            artifact_dir=Path(artifact_dir),
            machine=machine or {},
        )
