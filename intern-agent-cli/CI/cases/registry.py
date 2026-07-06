from __future__ import annotations

import importlib
import importlib.util
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from CI.cases.base import CaseDefinition


STAGE_CASE_SLOT_RE = re.compile(r"^(?P<prefix>F|J)_(?P<case_no>\d{4})$")
CASE_ID_RE = re.compile(r"^(?P<prefix>F|J)_(?P<case_no>\d{4})_[A-Za-z0-9][A-Za-z0-9_]*$")

REMOTE_CASE_RUNNERS: dict[str, str] = {
    "F_0001_codeup_repo_dotdir_workspace_add_remove": "CI.cases.F.F_0001:run_f_workspace_codeup_repo_dotdir_add_remove",
    "F_0002_codeup_metadata_branch_workspace_add_remove": "CI.cases.F.F_0002:run_f_workspace_codeup_metadata_branch_add_remove",
    "F_0003_github_workspace_add_remove": "CI.cases.F.F_0003:run_f_workspace_github_add_remove",
    "F_0004_local_workspace_add_remove": "CI.cases.F.F_0004:run_f_workspace_local_add_remove",
    "F_0005_workspace_duplicate_invalid_add_rollback": "CI.cases.F.F_0005:run_f_workspace_duplicate_invalid_rollback",
    "F_0006_workspace_mode_switch_contract": "CI.cases.F.F_0006:run_f_workspace_mode_switch_contract",
    "F_0007_intern_create_status_contract": "CI.cases.F.F_0007:run_f_intern_create_status_contract",
    "F_0008_intern_duplicate_invalid_create_rollback": "CI.cases.F.F_0008:run_f_intern_duplicate_invalid_create_rollback",
    "F_0009_codex_session_lifecycle_no_prompt": "CI.cases.F.F_0009:run_f_codex_session_lifecycle_no_prompt",
    "F_0010_intern_delete_force_guard_remote": "CI.cases.F.F_0010:run_f_intern_delete_force_guard_remote",
    "F_0011_daemon_status_readiness_api": "CI.cases.F.F_0011:run_f_daemon_status_readiness_api",
    "F_0012_daemon_group_proxy_registry_mutation": "CI.cases.F.F_0012:run_f_daemon_group_proxy_registry_mutation",
    "F_0013_relay_chat_project_scope_lifecycle": "CI.cases.F.F_0013:run_f_relay_chat_project_scope_lifecycle",
    "F_0015_question_card_callback_autofill_cleanup": "CI.cases.F.F_0015:run_f_question_card_callback_autofill_cleanup",
    "F_0016_slash_config_mode_persistence": "CI.cases.F.F_0016:run_f_slash_config_mode_persistence",
    "F_0017_helper_slash_open_start_status": "CI.cases.F.F_0017:run_f_helper_slash_open_start_status",
    "F_0018_helper_machine_detail_stop": "CI.cases.F.F_0018:run_f_helper_machine_detail_stop",
    "F_0019_main_bot_readonly_slash_commands": "CI.cases.F.F_0019:run_f_main_bot_readonly_slash_commands",
    "F_0020_slash_routing_errors_rbac_unknown": "CI.cases.F.F_0020:run_f_slash_routing_errors_rbac_unknown",
    "F_0021_workspace_disable_delete_gui_contract": "CI.cases.F.F_0021:run_f_workspace_disable_delete_gui_contract",
    "F_0022_workspace_enable_doctor_refresh_contract": "CI.cases.F.F_0022:run_f_workspace_enable_doctor_refresh_contract",
    "F_0023_task_treeview_projection_contract": "CI.cases.F.F_0023:run_f_task_treeview_projection_contract",
    "F_0024_task_delete_gui_contract": "CI.cases.F.F_0024:run_f_task_delete_gui_contract",
    "F_0025_codex_intern_treeview_projection_scope": "CI.cases.F.F_0025:run_f_codex_intern_treeview_projection_scope",
    "F_0026_codex_active_intern_status_chat_routing": "CI.cases.F.F_0026:run_f_codex_active_intern_status_chat_routing",
    "F_0027_codex_session_context_command_rollback": "CI.cases.F.F_0027:run_f_codex_session_context_command_rollback",
    "F_0028_codex_group_mode_treeview_context_commands": "CI.cases.F.F_0028:run_f_codex_group_mode_treeview_context_commands",
    "F_0029_skill_source_treeview_projection_mutation": "CI.cases.F.F_0029:run_f_skill_source_treeview_projection_mutation",
    "F_0030_codex_skill_repo_personal_enable_contract": "CI.cases.F.F_0030:run_f_codex_skill_repo_personal_enable_contract",
    "F_0031_treeview_top_level_config_status_contract": "CI.cases.F.F_0031:run_f_treeview_top_level_config_status_contract",
    "F_0032_treeview_menu_visibility_context_contract": "CI.cases.F.F_0032:run_f_treeview_menu_visibility_context_contract",
    "F_0033_codex_no_prompt_exit_restart_contract": "CI.cases.F.F_0033:run_f_codex_no_prompt_exit_restart_contract",
    "F_0034_policy_env_idle_codex_auto_restart_contract": "CI.cases.F.F_0034:run_f_policy_env_idle_codex_auto_restart_contract",
    "F_0035_config_card_cancel_no_mutation_contract": "CI.cases.F.F_0035:run_f_config_card_cancel_no_mutation_contract",
    "F_0036_machine_config_card_policy_sync_safety_contract": "CI.cases.F.F_0036:run_f_machine_config_card_policy_sync_safety_contract",
    "F_0037_daemon_reconnect_registry_policy_resync_contract": "CI.cases.F.F_0037:run_f_daemon_reconnect_registry_policy_resync_contract",
    "F_0041_real_feishu_ingress_slash_card_callback_contract": "CI.cases.F.F_0041:run_f_real_feishu_ingress_slash_card_callback_contract",
    "F_0043_claude_intern_create_session_lifecycle_contract": "CI.cases.F.F_0043:run_f_claude_intern_create_session_lifecycle_contract",
    "F_0044_claude_treeview_projection_command_parity_contract": "CI.cases.F.F_0044:run_f_claude_treeview_projection_command_parity_contract",
    "F_0045_claude_skill_farm_group_parity_contract": "CI.cases.F.F_0045:run_f_claude_skill_farm_group_parity_contract",
    "F_0052_session_resume_cli_claude_contract": "CI.cases.F.F_0052:run_f_session_resume_cli_claude_contract",
    "J_0014_peer_send_routing_error_contract": "CI.cases.J.J_0014:run_f_peer_send_routing_error_contract",
    "J_0033_codex_exit_resume_same_session_journey": "CI.cases.J.J_0033:run_j_codex_exit_resume_same_session_journey",
    "J_0059_codex_request_user_input_answer_fidelity_journey": "CI.cases.J.J_0059:run_j_codex_request_user_input_answer_fidelity_journey",
    "J_0065_policy_reconcile_same_session_journey": "CI.cases.J.J_0065:run_j_policy_reconcile_same_session_journey",
}


def _ci_dir() -> Path:
    return Path(__file__).resolve().parents[1]


def _cases_dir(ci_dir: Path | None = None) -> Path:
    return (ci_dir or _ci_dir()) / "cases"


def _slot_identity(path: Path) -> tuple[str, str]:
    if path.parent.name in {"F", "J"}:
        match = STAGE_CASE_SLOT_RE.fullmatch(path.stem)
        if not match or match.group("prefix") != path.parent.name:
            raise ValueError(f"invalid CI {path.parent.name} case slot filename: {path.relative_to(path.parents[1])}")
        return match.group("prefix"), match.group("case_no")
    raise ValueError(f"invalid CI F/J case path: {path}")


def _slot_conflict_hint(prefix: str, case_no: str) -> str:
    if prefix == "F":
        return (
            f"F id conflict at F_{case_no}: F numbers are independent from J numbers; "
            "if another branch already owns this F slot, choose the next free F number"
        )
    if prefix == "J":
        return (
            f"J id conflict at J_{case_no}: J numbers are independent from F numbers; "
            "if another branch already owns this J slot, choose the next free J number"
        )
    raise ValueError(f"unsupported CI case prefix: {prefix}")


def discover_case_files(
    *,
    cases_dir: Path | None = None,
) -> tuple[Path, ...]:
    base = cases_dir or _cases_dir()
    slot_order = {"F": 0, "J": 1}
    paths = [*base.glob("F/F_*.py"), *base.glob("J/J_*.py")]
    return tuple(sorted(paths, key=lambda item: (slot_order[_slot_identity(item)[0]], _slot_identity(item)[1], item.name)))


def _load_case_from_file(path: Path) -> CaseDefinition:
    module_name = f"_ci_case_slot_fixture_{path.stem}_{abs(hash(path.resolve()))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not import CI case slot: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module_name, None)
    return getattr(module, "CASE")


def _validate_case_entries(entries: Sequence[tuple[Path, str, object]]) -> None:
    seen_ids: set[str] = set()
    seen_slots: set[str] = set()
    for path, source, raw_case in entries:
        slot_prefix, slot_case_no = _slot_identity(path)
        if not isinstance(raw_case, CaseDefinition):
            raise TypeError(f"{source}.CASE must be CaseDefinition")
        id_match = CASE_ID_RE.fullmatch(raw_case.id)
        if not id_match:
            raise ValueError(f"invalid CI case id in {source}: {raw_case.id}")
        case_no = id_match.group("case_no")
        id_prefix = id_match.group("prefix")
        slot_key = f"{id_prefix}_{case_no}"
        if case_no != slot_case_no or id_prefix != slot_prefix:
            raise ValueError(
                f"CI case slot {slot_prefix}_{slot_case_no} owns {slot_prefix}_{slot_case_no}, "
                f"but CASE id is {raw_case.id}; {_slot_conflict_hint(slot_prefix, slot_case_no)}"
            )
        if raw_case.id in seen_ids:
            raise ValueError(f"duplicate CI case id: {raw_case.id}; {_slot_conflict_hint(id_prefix, case_no)}")
        if slot_key in seen_slots:
            raise ValueError(f"duplicate CI case number: {slot_key}; {_slot_conflict_hint(id_prefix, case_no)}")
        seen_ids.add(raw_case.id)
        seen_slots.add(slot_key)


def load_cases(
    *,
    include_disabled: bool = False,
    cases_dir: Path | None = None,
) -> list[CaseDefinition]:
    entries: list[tuple[Path, str, object]] = []
    for path in discover_case_files(cases_dir=cases_dir):
        entries.append((path, str(path), _load_case_from_file(path)))
    _validate_case_entries(entries)
    cases = [case for _, _, case in entries if isinstance(case, CaseDefinition)]
    return [case for case in cases if case.enabled or include_disabled]


CASE_SET_KIND_ORDER: dict[str, tuple[str, ...]] = {
    "setup": ("setup_basic",),
    "runner_platform": (
        "runner_resource_lock",
        "runner_case_selection",
        "runner_scenario_report",
        "runner_artifact_failure",
        "release_case_set_promotion",
    ),
    "runner_cleanup_report": (
        "runner_shared_cleanup",
        "runner_timeout_rerun",
        "observability_report_export",
    ),
    "registry": ("runner_registry_doc",),
    "intern_session_cli": (
        "intern_cli_create_independent",
        "intern_cli_create_role",
        "intern_cli_duplicate_guard",
        "session_copilot_no_tmux",
        "intern_status_tree_refresh",
    ),
    "workspace_cli": (
        "workspace_cli_local",
        "workspace_cli_mode_transition",
        "workspace_cli_delete_guard",
        "workspace_metadata_resolver",
        "workspace_enterprise_setup_provider",
    ),
    "workspace_provider_relay": (
        "workspace_cli_github",
        "workspace_registry_conflict",
        "workspace_relay_sync",
    ),
    "task_metadata": (
        "task_metadata_history_knowledge",
        "task_delete_guard",
    ),
    "askuser_request": (
        "askuser_codex_request",
        "askuser_claude_question",
        "askuser_permission_timeout_cancel",
    ),
    "vscode_treeview": (
        "workspace_gui_add_remove",
        "workspace_gui_enable_disable_doctor",
        "intern_gui_create_delete",
        "intern_gui_session_button",
        "task_gui_delete_status",
        "vscode_treeview_navigation",
    ),
    "peer_mailbox_goal": (
        "peer_same_machine",
        "peer_failure_rbac",
        "mailbox_same_cross_read",
        "goal_failure_cancel",
    ),
    "debug_diagnostics": (
        "session_logs_artifacts_diagnostics",
        "debug_session_tmux_snapshot",
        "debug_artifact_log_collection",
        "debug_relay_daemon_health",
    ),
    "setup_config_skill_security": (
        "setup_check_apply",
        "config_cli_toggle",
        "skill_source_add_remove",
        "skill_enable_sync_repo",
        "security_secret_redaction",
    ),
}


def _case_extra_sets(case: CaseDefinition) -> tuple[str, ...]:
    raw = case.extra.get("case_sets", ())
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, Sequence):
        return tuple(str(item) for item in raw)
    return ()


def resolve_remote_case_runner(case_id: str) -> Callable[[Any], None]:
    runner_path = REMOTE_CASE_RUNNERS[case_id]
    module_name, function_name = runner_path.split(":", 1)
    module = importlib.import_module(module_name)
    runner = getattr(module, function_name)
    if not callable(runner):
        raise TypeError(f"remote case runner is not callable: {runner_path}")
    return runner


def validate_remote_case_runner_registry(cases: Sequence[CaseDefinition]) -> None:
    expected = {case.id for case in cases if case.stage == "remote"}
    actual = set(REMOTE_CASE_RUNNERS)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    errors: list[str] = []
    if missing:
        errors.append("missing remote runner mapping for " + ", ".join(missing))
    if extra:
        errors.append("remote runner mapping references unknown/non-remote case " + ", ".join(extra))
    for case_id, runner_path in sorted(REMOTE_CASE_RUNNERS.items()):
        if ":" not in runner_path:
            errors.append(f"{case_id} runner path must use module:function syntax")
            continue
        try:
            resolve_remote_case_runner(case_id)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{case_id} runner path is not importable/callable: {runner_path}: {exc}")
    if errors:
        raise ValueError("invalid CI remote case runner registry: " + "; ".join(errors))


def _add_case(case_sets: dict[str, list[str]], set_name: str, case: CaseDefinition) -> None:
    case_sets.setdefault(set_name, [])
    if case.id not in case_sets[set_name]:
        case_sets[set_name].append(case.id)


def load_case_sets(cases: Sequence[CaseDefinition] | None = None) -> dict[str, list[str]]:
    all_cases = list(cases) if cases is not None else load_cases(include_disabled=True)
    by_kind = {case.kind: case for case in all_cases}
    case_sets: dict[str, list[str]] = {
        "setup": [],
        "native": [],
        "dialogue": [],
        "core": [],
        "full": [],
        "mock_feishu": [],
        "runner_platform": [],
        "runner_cleanup_report": [],
        "registry": [],
        "intern_session_cli": [],
        "workspace_cli": [],
        "workspace_provider_relay": [],
        "task_metadata": [],
        "askuser_request": [],
        "vscode_treeview": [],
        "peer_mailbox_goal": [],
        "debug_diagnostics": [],
        "setup_config_skill_security": [],
        "F": [],
        "J": [],
    }
    for case in all_cases:
        stage = str(case.extra.get("ci_stage", "")).upper()
        if stage in {"F", "J"}:
            _add_case(case_sets, stage, case)
            if case.enabled and stage == "F":
                _add_case(case_sets, "full", case)
                _add_case(case_sets, "core", case)
            if case.ci_native:
                _add_case(case_sets, "native", case)
        if "remote" in case.tags:
            _add_case(case_sets, "native", case)
            _add_case(case_sets, "core", case)
            _add_case(case_sets, "full", case)
        if "dialogue" in case.tags:
            _add_case(case_sets, "dialogue", case)
        if "mock_feishu" in case.tags or case.kind == "mock_feishu":
            _add_case(case_sets, "mock_feishu", case)
        for set_name in _case_extra_sets(case):
            _add_case(case_sets, set_name, case)
    for set_name, kinds in CASE_SET_KIND_ORDER.items():
        for kind in kinds:
            case = by_kind.get(kind)
            if case is not None:
                _add_case(case_sets, set_name, case)
    return {name: ids for name, ids in case_sets.items() if ids or name in {"F", "J"}}


def validate_case_set_references(cases: Sequence[CaseDefinition], case_sets: Mapping[str, Sequence[str]]) -> None:
    known = {case.id for case in cases}
    errors: list[str] = []
    for set_name, case_ids in case_sets.items():
        for case_id in case_ids:
            if case_id not in known:
                errors.append(f"{set_name} references unknown case {case_id}")
    if errors:
        raise ValueError("invalid CI case set references: " + "; ".join(errors))


def _read_optional(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def validate_registry_guidance(
    cases: Sequence[CaseDefinition],
    *,
    ci_dir: Path | None = None,
    readme_text: str | None = None,
    authoring_text: str | None = None,
) -> None:
    ci = ci_dir or _ci_dir()
    readme = _read_optional(ci / "README.md") if readme_text is None else readme_text
    authoring = _read_optional(ci / "AUTHORING.md") if authoring_text is None else authoring_text
    errors: list[str] = []
    for marker in ("AUTHORING.md", "cases/F", "cases/J", "--list-cases", "--list-actions", "--list-assertions"):
        if marker not in readme:
            errors.append(f"README.md missing maintenance marker {marker}")
    for marker in ("--list-cases", "--list-actions", "--list-assertions", "cases/F/F_00xx.py", "cases/J/J_00xx.py"):
        if marker not in authoring:
            errors.append(f"AUTHORING.md missing maintenance marker {marker}")
    if errors:
        raise ValueError("invalid CI registry guidance: " + "; ".join(errors))


def validate_docs_catalog(cases: Sequence[CaseDefinition], *args, **kwargs) -> None:
    kwargs.pop("cases_doc_text", None)
    kwargs.pop("catalog_html_text", None)
    validate_registry_guidance(cases, *args, **kwargs)


def _is_quarantined(case: CaseDefinition) -> bool:
    return bool(case.extra.get("quarantined")) or "quarantine" in case.tags


def _has_full_promotion_evidence(case: CaseDefinition) -> bool:
    promotion = case.extra.get("promotion")
    if isinstance(promotion, dict) and promotion.get("stable") is True:
        return True
    return False


def validate_case_set_promotion_policy(
    cases: Sequence[CaseDefinition],
    case_sets: Mapping[str, Sequence[str]],
    *,
    cases_doc_text: str | None = None,
    catalog_html_text: str | None = None,
) -> None:
    validate_case_set_references(cases, case_sets)
    by_id = {case.id: case for case in cases}
    core_ids = set(case_sets.get("core", ()))
    full_ids = set(case_sets.get("full", ()))
    errors: list[str] = []
    for case_id in sorted(core_ids):
        case = by_id[case_id]
        if not case.enabled:
            errors.append(f"core case set contains disabled case {case_id}")
        if not case.ci_native:
            errors.append(f"core promotion requires CI-native support for {case_id}")
    for case_id in sorted(full_ids):
        case = by_id[case_id]
        if not case.enabled:
            errors.append(f"full case set contains disabled case {case_id}")
        if _is_quarantined(case):
            errors.append(f"full case set contains quarantined case {case_id}")
        if not case.ci_native:
            errors.append(f"full promotion requires CI-native support for {case_id}")
        if case_id not in core_ids and not _has_full_promotion_evidence(case):
            errors.append(
                f"full promotion for {case_id} requires stable evidence in "
                "CaseDefinition.extra['promotion']['stable']"
            )
    if errors:
        raise ValueError("invalid CI case set promotion policy: " + "; ".join(errors))


def validate_registry_tree(ci_dir: Path | None = None) -> None:
    from CI.actions.registry import validate_action_registry
    from CI.assertions.registry import validate_assertion_registry

    ci = ci_dir or _ci_dir()
    cases = load_cases(include_disabled=True, cases_dir=_cases_dir(ci))
    case_sets = load_case_sets(cases)
    validate_case_set_references(cases, case_sets)
    validate_remote_case_runner_registry(cases)
    validate_action_registry()
    validate_assertion_registry()
    validate_registry_guidance(cases, ci_dir=ci)
    validate_case_set_promotion_policy(cases, case_sets)


def case_by_id_or_name(value: str, *, include_disabled: bool = False) -> CaseDefinition:
    wanted = value.strip()
    for case in load_cases(include_disabled=include_disabled):
        if case.id == wanted or case.name == wanted:
            return case
    raise KeyError(f"unknown CI case: {value}")
