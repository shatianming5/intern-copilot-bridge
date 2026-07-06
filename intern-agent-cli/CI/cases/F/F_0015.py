import time
from typing import Any
from CI.cases.base import CaseDefinition


SCENARIO_IDS = (
    "F_0015.s01_seed_group_and_register_question",
    "F_0015.s02_ci_callback_ingress_enabled",
    "F_0015.s03_submit_callback_and_poll_answer",
    "F_0015.s04_duplicate_callback_drops_without_recreate",
    "F_0015.s05_missing_callback_drops_without_state",
)


CASE = CaseDefinition(
    id="F_0015_question_card_callback_autofill_cleanup",
    name="Question card callback autofill and cleanup",
    description=(
        "Validates pending question registration, CI synthetic card callback routing, "
        "poll consumption cleanup, and duplicate or missing callback no-op evidence."
    ),
    stage="remote",
    timeout_seconds=1800,
    kind="f_daemon_relay_api",
    tags=("F", "question", "callback", "daemon", "relay"),
    parallel_safe=False,
    extra={
        "ci_stage": "F",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "cli.internctl",
            "create_intern",
            "start_intern_session",
            "daemon.ci_callback_fixture",
            "relay_daemon.remote_wait_daemon_log_contains",
            "relay.ci_synthetic_card_callback",
            "relay.ci_synthetic_errors",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "native.request_user_input_owner_channel",
            "native.callback_health_probe",
        ),
        "resource_locks": (
            {"resource": "feishu_chat:ci_f_0015", "mode": "exclusive"},
            {"resource": "fixture:ci_f_0015:question:case-scoped", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_f_0015_worker", "mode": "exclusive"},
            {"resource": "namespace:ci_f_0015", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_f_0015_question", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_f_0015",
            "workspace:ci_f_0015_question",
            "intern:intern_ci_f_0015_worker",
            "relay-chat:case-scoped",
            "question:case-scoped",
        ),
        "run_mode": "remote_deployed_api",
        "notes": (
            "Relay callback HTTP only proves transport, visible audit, and route to machine.",
            "Duplicate and missing callback semantics are asserted through daemon no-pending evidence plus poll state.",
        ),
    },
)


def run_f_question_card_callback_autofill_cleanup(case: Any) -> None:
    self = case
    repo = self.ctx.action.workspace.local_repo_fixture_remote("f0015_question")
    workspace = self.ctx.action.workspace.create_case_remote(
        suffix="question",
        provider="local",
        repo_url=str(repo),
        mode="local_only",
        local_path=str(repo),
    )
    intern = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(workspace, "worker", repo_url=str(repo)))["intern"]
    project = str(workspace["display"])
    question_id = f"q_f0015_{int(time.time())}"
    missing_question_id = f"missing_f0015_{int(time.time())}"
    state: dict[str, Any] = {"workspace": workspace, "intern": intern, "project": project, "question_id": question_id}

    def s01_seed_group_and_register_question() -> dict[str, Any]:
        self.ctx.action.session.start_remote(workspace, intern)
        chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace, intern, timeout=self.args.timeout)
        chat_id = str(chat.get("chat_id") or "")
        self.require("f0015_chat_id_present", bool(chat_id), chat)
        ask = self.http_json(
            "F_0015 question ask",
            "POST",
            "/api/question/ask",
            {
                "project": project,
                "intern_name": intern,
                "tool_name": "AskUserQuestion",
                "questions": [
                    {
                        "header": "F0015 choice",
                        "question": "Please choose A or B",
                        "options": [
                            {"label": "A", "description": "Option A"},
                            {"label": "B", "description": "Option B"},
                        ],
                    }
                ],
                "metadata": {"question_id": question_id, "project": project, "source": "ci_f_0015"},
            },
            timeout=120,
        )
        self.require("f0015_question_registered", ask.get("ok") is True and ask.get("question_id") == question_id, ask)
        self.require("f0015_single_card_message_sent", bool(ask.get("message_id")), ask)
        pending = self.ctx.action.feishu.wait_question_poll_remote(intern, question_id, project=project, status="pending", timeout=90)
        state.update({"chat": chat, "chat_id": chat_id, "ask": ask, "pending": pending})
        return {"chat": chat, "ask": ask, "pending": pending}

    def s02_ci_callback_ingress_enabled() -> dict[str, Any]:
        probe = self.relay_request_json("F_0015 ci callback empty body probe", "POST", "/api/ci/card_callback", {}, timeout=60, check=False)
        self.require(
            "f0015_ci_callback_ingress_enabled",
            probe.get("status_code") == 400 and probe.get("status_code") != 403,
            {"probe": probe},
        )
        state["ingress_probe"] = probe
        return probe

    def s03_submit_callback_and_poll_answer() -> dict[str, Any]:
        callback = self.relay_json(
            "F_0015 ci card callback answer",
            "POST",
            "/api/ci/card_callback",
            {
                "chat_id": state["chat_id"],
                "project": project,
                "intern_name": intern,
                "question_id": question_id,
                "answer": "B",
            },
            timeout=90,
        )
        self.require("f0015_callback_transport_ok", callback.get("ok") is True and callback.get("machine_id"), callback)
        self.require("f0015_callback_visible_audit", bool(callback.get("visible_message_id")) and not callback.get("visible_error"), callback)
        answered = self.ctx.action.feishu.wait_question_poll_remote(intern, question_id, project=project, status="answered", timeout=120)
        answers = answered.get("answers") if isinstance(answered.get("answers"), dict) else {}
        self.require("f0015_poll_answer_b", "B" in {str(value) for value in answers.values()}, {"answered": answered})
        cleared = self.ctx.action.feishu.wait_question_poll_remote(intern, question_id, project=project, status="none", timeout=60)
        self.require("f0015_pending_cleared_after_poll", cleared.get("status") == "none", cleared)
        state.update({"callback": callback, "answered": answered, "cleared": cleared})
        return {"callback": callback, "answered": answered, "cleared": cleared}

    def s04_duplicate_callback_drops_without_recreate() -> dict[str, Any]:
        duplicate = self.relay_json(
            "F_0015 duplicate card callback",
            "POST",
            "/api/ci/card_callback",
            {
                "chat_id": state["chat_id"],
                "project": project,
                "intern_name": intern,
                "question_id": question_id,
                "answer": "A",
            },
            timeout=90,
        )
        self.require("f0015_duplicate_callback_transport_ok", duplicate.get("ok") is True and duplicate.get("machine_id"), duplicate)
        drop = self.ctx.action.relay_daemon.wait_daemon_log_contains_remote(f"(card callback question_id={question_id})", timeout=45)
        self.require("f0015_duplicate_no_pending_drop_logged", drop.get("found") is True, drop)
        absent = self.ctx.action.feishu.wait_question_poll_remote(intern, question_id, project=project, status="none", timeout=60)
        self.require("f0015_duplicate_does_not_recreate_pending", absent.get("status") == "none", {"duplicate": duplicate, "drop": drop, "absent": absent})
        state["duplicate_callback"] = {"duplicate": duplicate, "drop": drop, "absent": absent}
        return state["duplicate_callback"]

    def s05_missing_callback_drops_without_state() -> dict[str, Any]:
        missing = self.relay_json(
            "F_0015 missing question card callback",
            "POST",
            "/api/ci/card_callback",
            {
                "chat_id": state["chat_id"],
                "project": project,
                "intern_name": intern,
                "question_id": missing_question_id,
                "answer": "A",
            },
            timeout=90,
        )
        self.require("f0015_missing_callback_transport_ok", missing.get("ok") is True and missing.get("machine_id"), missing)
        drop = self.ctx.action.relay_daemon.wait_daemon_log_contains_remote(f"(card callback question_id={missing_question_id})", timeout=45)
        self.require("f0015_missing_no_pending_drop_logged", drop.get("found") is True, drop)
        absent = self.ctx.action.feishu.wait_question_poll_remote(intern, missing_question_id, project=project, status="none", timeout=60)
        self.require("f0015_missing_does_not_create_pending", absent.get("status") == "none", {"missing": missing, "drop": drop, "absent": absent})
        state["missing_callback"] = {"missing": missing, "drop": drop, "absent": absent}
        return state["missing_callback"]

    self.run_ordered_scenarios([
        ("F_0015.s01_seed_group_and_register_question", s01_seed_group_and_register_question),
        ("F_0015.s02_ci_callback_ingress_enabled", s02_ci_callback_ingress_enabled),
        ("F_0015.s03_submit_callback_and_poll_answer", s03_submit_callback_and_poll_answer),
        ("F_0015.s04_duplicate_callback_drops_without_recreate", s04_duplicate_callback_drops_without_recreate),
        ("F_0015.s05_missing_callback_drops_without_state", s05_missing_callback_drops_without_state),
    ])
    self.artifacts["question_card_callback_autofill_cleanup"] = state
