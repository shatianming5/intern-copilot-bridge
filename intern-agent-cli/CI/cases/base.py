from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CaseDefinition:
    id: str
    name: str
    description: str = ""
    stage: str = "remote"
    enabled: bool = True
    timeout_seconds: int = 3600
    tags: tuple[str, ...] = ()
    smoke_scenarios: tuple[str, ...] = ()
    include_dialogue_merge: bool = False
    reset_ci_metadata_branches: bool = False
    run_setup_apply: bool = False
    restart_services: bool = False
    require_helper: bool = False
    require_dialogue_merge: bool = False
    require_feishu_inbound: bool = False
    allow_aux_retained_scene: bool = True
    allow_existing_feishu_chats: bool = True
    parallel_safe: bool = False
    kind: str = "smoke"
    ci_native: bool = True
    extra: dict = field(default_factory=dict)

    @property
    def case_no(self) -> str:
        return self.id.split("_", 2)[1]

    def to_registry_entry(self) -> dict:
        entry = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "stage": self.stage,
            "enabled": self.enabled,
            "timeout_seconds": self.timeout_seconds,
            "tags": list(self.tags),
            "parallel_safe": self.parallel_safe,
            "kind": self.kind,
            "ci_native": self.ci_native,
        }
        if self.extra.get("scenario_ids"):
            entry["scenario_ids"] = list(self.extra["scenario_ids"])
        if self.extra.get("resources"):
            entry["resources"] = list(self.extra["resources"])
        if self.extra.get("resource_locks"):
            entry["resource_locks"] = list(self.extra["resource_locks"])
        if self.extra.get("lock_params"):
            entry["lock_params"] = dict(self.extra["lock_params"])
        if self.extra.get("ci_stage"):
            entry["ci_stage"] = self.extra["ci_stage"]
        if self.extra.get("actions"):
            actions = self.extra["actions"]
            entry["actions"] = [actions] if isinstance(actions, str) else list(actions)
        if self.extra.get("assertions"):
            assertions = self.extra["assertions"]
            entry["assertions"] = [assertions] if isinstance(assertions, str) else list(assertions)
        if self.extra.get("journey_steps"):
            entry["journey_steps"] = list(self.extra["journey_steps"])
        if self.extra.get("run_mode"):
            entry["run_mode"] = self.extra["run_mode"]
        if self.extra.get("notes"):
            notes = self.extra["notes"]
            entry["notes"] = [notes] if isinstance(notes, str) else list(notes)
        return entry
