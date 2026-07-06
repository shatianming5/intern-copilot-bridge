from __future__ import annotations

import json
import random
import re
import time
import urllib.parse
from typing import Any

from CI.cases.base import CaseDefinition
from CI.helpers.product_cli_helper import tail


ROUND_COUNT = 10
RANDOM_SEED = 59059
ANSWER_KINDS = ("A", "B", "C", "freeform")

ROUND_SCENARIO_IDS = tuple(
    f"J_0059.s{5 + index:02d}_round_{index + 1:02d}_request_user_input_answer_fidelity"
    for index in range(ROUND_COUNT)
)

SCENARIO_IDS = (
    "J_0059.s01_reset_case_namespace",
    "J_0059.s02_create_workspace_task_and_codex_group",
    "J_0059.s03_start_codex_session",
    "J_0059.s04_feishu_group_green_light_before_question_loop",
    *ROUND_SCENARIO_IDS,
    "J_0059.s15_final_consistency_summary",
)


CASE = CaseDefinition(
    id="J_0059_codex_request_user_input_answer_fidelity_journey",
    name="Codex request_user_input answer fidelity journey",
    description=(
        "Starts a real Codex intern journey, asks Codex to call request_user_input "
        "ten times across varied question/card shapes, answers through Feishu cards, "
        "and verifies the answer Codex prints matches the submitted Feishu answer."
    ),
    stage="remote",
    timeout_seconds=5400,
    kind="f_intern_session_remote",
    tags=("J", "codex", "request_user_input", "askuser", "feishu", "card", "answer-fidelity"),
    parallel_safe=False,
    extra={
        "ci_stage": "J",
        "scenario_ids": SCENARIO_IDS,
        "actions": (
            "create_feishu_group",
            "create_intern",
            "create_task",
            "start_intern_session",
            "send_user_message",
            "submit_askuser_card",
            "answer_askuser",
            "daemon.read_status",
            "relay.read_chat_presence",
            "collect_artifacts",
            "export_report",
        ),
        "assertions": (
            "ctx.require",
            "native.request_user_input_owner_channel",
        ),
        "resource_locks": (
            {"resource": "daemon:debug-pool", "mode": "write"},
            {"resource": "feishu_chat:ci_j_0059", "mode": "exclusive"},
            {"resource": "intern:axis_intern_agents_backup:intern_ci_j_0059_codex", "mode": "exclusive"},
            {"resource": "llm:codex", "mode": "read"},
            {"resource": "machine_pool:debug", "mode": "read"},
            {"resource": "namespace:ci_j_0059", "mode": "exclusive"},
            {"resource": "relay:test-relay", "mode": "write"},
            {"resource": "session:axis_intern_agents_backup:intern_ci_j_0059_codex", "mode": "exclusive"},
            {"resource": "task:axis_intern_agents_backup:task_ci_j_0059_question_fidelity", "mode": "exclusive"},
            {"resource": "tmux:ci_j_0059", "mode": "exclusive"},
            {"resource": "workspace:axis_intern_agents_backup:ci_j_0059_workspace", "mode": "exclusive"},
        ),
        "resources": (
            "namespace:ci_j_0059",
            "workspace:ci_j_0059_workspace",
            "intern:intern_ci_j_0059_codex",
            "task:task_ci_j_0059_question_fidelity",
            "case_scoped_feishu_group",
            "llm:codex",
            "tmux",
            "daemon",
            "relay",
            "machine:debug-pool",
        ),
        "run_mode": "deployed_remote_no_redeploy_llm_user_prompt_question_journey",
        "journey_steps": (
            "Create a case-scoped workspace, task anchor, Codex intern, and Feishu group.",
            "Start Codex and wait for the Feishu group to show the active green scene.",
            "Run ten request_user_input rounds covering single-select, free text, multi-question forms, option counts, long questions, and long option labels.",
            "Submit button choices through card callbacks and free text/multi-question answers through the card form, then compare Codex's printed result.",
        ),
        "notes": (
            "This is J-scoped because it sends real Codex prompts and visible Feishu answer events.",
            "The random seed is fixed so CI is reproducible while still mixing quick-select and free replies.",
            "The case waits for the pending question owner to become codex_tui before submitting the answer.",
            "Free-text answers use the card form callback path; /answer remains only an emergency fallback.",
        ),
    },
)


def parse_j0059_result_value(text: str, result_prefix: str) -> str:
    pattern = re.compile(rf"^\s*(?:[•*-]\s*)?{re.escape(result_prefix)}\s*=\s*(.*?)\s*$")
    matches: list[str] = []
    lines = (text or "").splitlines()
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if not match:
            continue
        parts = [match.group(1).strip()]
        for next_line in lines[index + 1:]:
            stripped = next_line.strip()
            if not stripped:
                break
            if re.match(r"^(?:[•◦›❯]|\w+_RESULT_\d+\s*=)", stripped):
                break
            if not next_line.startswith((" ", "\t")):
                break
            parts.append(stripped)
        matches.append(" ".join(part for part in parts if part))
    return matches[-1].strip() if matches else ""


def run_j_codex_request_user_input_answer_fidelity_journey(case: Any) -> None:
    self = case
    state: dict[str, Any] = {"rounds": []}

    def deterministic_answer_plan() -> list[dict[str, Any]]:
        rng = random.Random(RANDOM_SEED)

        def options(*labels: str) -> list[dict[str, str]]:
            return [
                {
                    "label": label,
                    "description": f"J_0059 deterministic option {label}; use this exact label if selected.",
                }
                for label in labels
            ]

        def question(round_no: int, suffix: str, labels: list[str] | None = None) -> dict[str, Any]:
            return {
                "header": f"J0059 {round_no:02d}",
                "question": f"J0059 round {round_no:02d} {suffix}",
                "options": options(*(labels or [])),
            }

        def answer_to_text(value: Any) -> str:
            if isinstance(value, list):
                return ",".join(str(item) for item in value)
            return str(value)

        def plan_item(round_no: int, shape: str, questions: list[dict[str, Any]], answers: dict[str, Any]) -> dict[str, Any]:
            expected = (
                answer_to_text(answers[questions[0]["question"]])
                if len(questions) == 1
                else " | ".join(
                    f"q{i + 1}={answer_to_text(answers[q['question']])}"
                    for i, q in enumerate(questions)
                )
            )
            return {
                "round": round_no,
                "shape": shape,
                "kind": shape,
                "questions": questions,
                "answers": answers,
                "expected_answer": expected,
            }

        def random_abc_or_free(round_no: int) -> str:
            chosen = rng.choice(ANSWER_KINDS)
            if chosen == "freeform":
                return f"freeform-j0059-round-{round_no:02d}-seed-{RANDOM_SEED}"
            return chosen

        q1 = question(1, "single option only; choose the sole visible option.", ["A"])
        q2 = question(2, "two-option branch; choose between A and B.", ["A", "B"])
        q3 = question(3, "three-option/random branch; choose A, B, C, or free text.", ["A", "B", "C"])
        q4 = question(4, "four-option branch; verify navigation past C.", ["A", "B", "C", "D"])
        q5 = {
            "header": "J0059 05",
            "question": (
                "J0059 round 05 long Chinese question text for wrapped terminal visibility: "
                "请确认这个问题会在 Codex TUI 窗口中自动换行但仍然必须被识别为同一个 request_user_input 问题"
            ),
            "options": options("A", "B", "C"),
        }
        long_option = (
            "Alpha option label with enough words to wrap across a narrow Codex TUI pane while still being the exact answer"
        )
        q6 = {
            "header": "J0059 06",
            "question": "J0059 round 06 long option label branch.",
            "options": [
                {
                    "label": long_option,
                    "description": "This intentionally long label validates wrapped option visibility and selection.",
                },
                {
                    "label": "B",
                    "description": "Short fallback option that should not be selected.",
                },
            ],
        }
        q7 = question(7, "forced freeform with visible options; submit through card form.", ["A", "B", "C"])
        q8 = question(8, "second freeform branch with visible options; submit text through the card input.", ["A", "B"])
        q9_a = question(9, "multi-question first select branch.", ["A", "B", "C"])
        q9_b = question(9, "multi-question second free text branch with options.", ["A", "B"])
        q10_a = question(10, "multi-question one-option branch.", ["A"])
        q10_b = question(10, "multi-question two-option branch.", ["A", "B"])
        q10_c = question(10, "multi-question four-option branch.", ["A", "B", "C", "D"])

        return [
            plan_item(1, "single_select_one_option", [q1], {q1["question"]: "A"}),
            plan_item(2, "single_select_two_options", [q2], {q2["question"]: rng.choice(("A", "B"))}),
            plan_item(3, "single_select_three_options_or_freeform", [q3], {q3["question"]: random_abc_or_free(3)}),
            plan_item(4, "single_select_four_options", [q4], {q4["question"]: "D"}),
            plan_item(5, "wrapped_long_question", [q5], {q5["question"]: random_abc_or_free(5)}),
            plan_item(6, "wrapped_long_option", [q6], {q6["question"]: long_option}),
            plan_item(7, "single_question_freeform_form", [q7], {q7["question"]: f"freeform-j0059-round-07-seed-{RANDOM_SEED}"}),
            plan_item(8, "single_question_freeform_with_two_options", [q8], {q8["question"]: f"plain-input-j0059-round-08-seed-{RANDOM_SEED}"}),
            plan_item(9, "multi_question_select_and_text", [q9_a, q9_b], {
                q9_a["question"]: "C",
                q9_b["question"]: f"multi-text-j0059-round-09-seed-{RANDOM_SEED}",
            }),
            plan_item(10, "multi_question_option_count_matrix", [q10_a, q10_b, q10_c], {
                q10_a["question"]: "A",
                q10_b["question"]: "B",
                q10_c["question"]: "D",
            }),
        ]

    def poll_question(question_id: str = "") -> dict[str, Any]:
        query = {
            "intern_name": state["intern"],
            "project": state["project"],
        }
        if question_id:
            query["question_id"] = question_id
        return self.http_json(
            "J_0059 question poll " + (question_id[:8] if question_id else "active"),
            "GET",
            "/api/question/poll?" + urllib.parse.urlencode(query),
            timeout=30,
        )

    def wait_codex_tui_pending(round_no: int, *, timeout: int) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            result = poll_question()
            last = result
            if result.get("status") == "pending" and result.get("question_id") and result.get("owner") == "codex_tui":
                return result
            time.sleep(2)
        self.require_classified_contract(
            f"j0059_round_{round_no:02d}_codex_tui_pending_question",
            False,
            "product_bug_codex_request_user_input_not_tui_owned",
            {"last_poll": last, "round": round_no},
        )
        return last

    def wait_question_card_delivered(round_no: int, question_id: str, *, timeout: int) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            result = poll_question(question_id)
            last = result
            delivered = (
                result.get("status") == "pending"
                and result.get("owner") == "codex_tui"
                and bool(result.get("message_id"))
                and result.get("delivery_state") == "sent"
            )
            if delivered:
                return result
            if result.get("status") in {"invalidated", "missing", "timed_out", "cancelled"}:
                break
            time.sleep(1)
        self.require_classified_contract(
            f"j0059_round_{round_no:02d}_question_card_delivered_before_answer",
            False,
            "product_bug_question_card_not_visible_before_answer",
            {"question_id": question_id, "last_poll": last, "round": round_no},
        )
        return last

    def wait_answered(round_no: int, question_id: str, *, timeout: int) -> dict[str, Any]:
        deadline = time.time() + timeout
        last: dict[str, Any] = {}
        while time.time() < deadline:
            result = poll_question(question_id)
            last = result
            if result.get("status") == "answered":
                return result
            if result.get("status") in {"invalidated", "missing", "timed_out", "cancelled"}:
                break
            time.sleep(2)
        self.require_classified_contract(
            f"j0059_round_{round_no:02d}_question_answered",
            False,
            "product_bug_question_answer_not_consumed",
            {"question_id": question_id, "last_poll": last, "round": round_no},
        )
        return last

    def wait_codex_result(round_no: int, result_prefix: str, expected_answer: str, *, timeout: int) -> dict[str, Any]:
        deadline = time.time() + timeout
        last = ""
        observed = ""
        while time.time() < deadline:
            last = self.ctx.action.session.tmux_capture_joined_remote(str(state["tmux_session"]), lines=500)
            observed = parse_j0059_result_value(last, result_prefix)
            if observed:
                break
            time.sleep(3)
        detail = {
            "round": round_no,
            "result_prefix": result_prefix,
            "expected_answer": expected_answer,
            "observed_answer": observed,
            "pane_tail": tail(last, 4000),
        }
        self.require_classified_contract(
            f"j0059_round_{round_no:02d}_codex_result_matches_answer",
            observed == expected_answer,
            "product_bug_codex_request_user_input_answer_mismatch",
            detail,
        )
        return detail

    def send_round_prompt(plan_item: dict[str, Any], result_prefix: str) -> dict[str, Any]:
        round_no = int(plan_item["round"])
        questions_json = json.dumps(plan_item["questions"], ensure_ascii=False, indent=2)
        result_instruction = (
            "For a single question, the result value is only the selected label or free-text content."
            if len(plan_item["questions"]) == 1
            else "For multiple questions, the result value must be q1=<answer> | q2=<answer> in question order, extending to q3 when present."
        )
        output_template = (
            f"{result_prefix}=<answer>"
            if len(plan_item["questions"]) == 1
            else f"{result_prefix}=" + " | ".join(
                f"q{i + 1}=<answer>" for i, _q in enumerate(plan_item["questions"])
            )
        )
        prompt = (
            "CI J_0059 request_user_input fidelity round.\n"
            f"Round shape: {plan_item['shape']}.\n"
            "Call request_user_input exactly once for this round with questions exactly matching this JSON array:\n"
            f"{questions_json}\n"
            "After the tool returns, print exactly one line containing the result prefix, then an equals sign, then the canonical answer value.\n"
            f"{result_instruction} "
            "For a free-text / None-of-the-above answer, use the user_note or free-text content only; "
            "do not include 'None of the above', 'user_note:', JSON, brackets, or explanations.\n"
            f"The output format template for this round is: {output_template}\n"
            f'The result prefix is "{result_prefix}". Do not print the result line until after the tool returns.\n'
            "Do not run shell commands."
        )
        sent = self.ctx.action.session.tmux_send_remote(str(state["tmux_session"]), prompt)
        return {"prompt": prompt, "sent": sent}

    def submit_round_answer(round_no: int, question_id: str, plan_item: dict[str, Any]) -> dict[str, Any]:
        questions = plan_item["questions"]
        answers = plan_item["answers"]

        def option_labels(question: dict[str, Any]) -> list[str]:
            return [
                str(opt.get("label", opt) if isinstance(opt, dict) else opt)
                for opt in question.get("options", []) or []
            ]

        single_question = len(questions) == 1
        first_question = questions[0]
        first_key = first_question["question"]
        first_answer = str(answers[first_key])
        if single_question and first_answer in option_labels(first_question):
            submitted = self.relay_json(
                f"J_0059 round {round_no:02d} card answer {first_answer[:80]}",
                "POST",
                "/api/ci/card_callback",
                {
                    "chat_id": state["chat_id"],
                    "project": state["project"],
                    "intern_name": state["intern"],
                    "question_id": question_id,
                    "answer": first_answer,
                },
                timeout=90,
            )
            self.require(
                f"j0059_round_{round_no:02d}_button_answer_sent",
                submitted.get("ok") is True and submitted.get("question_id") == question_id,
                submitted,
            )
            return submitted | {"submit_mode": "card_button"}

        form_value: dict[str, Any] = {"submit": "1"}
        for i, question_obj in enumerate(questions):
            key = question_obj["question"]
            value = answers[key]
            labels = option_labels(question_obj)
            if isinstance(value, list):
                form_value[f"q_{i}_multiselect"] = value
            elif str(value) in labels:
                form_value[f"q_{i}_select"] = str(value)
            else:
                form_value[f"q_{i}_input"] = str(value)

        submitted = self.relay_json(
            f"J_0059 round {round_no:02d} card form answer",
            "POST",
            "/api/ci/card_callback",
            {
                "chat_id": state["chat_id"],
                "project": state["project"],
                "intern_name": state["intern"],
                "question_id": question_id,
                "form_value": form_value,
                "question_keys": [q["question"] for q in questions],
            },
            timeout=90,
        )
        self.require(
            f"j0059_round_{round_no:02d}_form_answer_sent",
            submitted.get("ok") is True
            and submitted.get("mode") == "form"
            and submitted.get("question_id") == question_id,
            submitted,
        )
        return submitted | {"submit_mode": "card_form", "form_value": form_value}

    def run_round(plan_item: dict[str, Any]) -> dict[str, Any]:
        round_no = int(plan_item["round"])
        expected_answer = str(plan_item["expected_answer"])
        result_prefix = f"J0059_RESULT_{round_no:02d}"

        sent = send_round_prompt(plan_item, result_prefix)
        pending = wait_codex_tui_pending(round_no, timeout=min(240, self.args.timeout))
        question_id = str(pending.get("question_id") or "")
        card_delivered = wait_question_card_delivered(round_no, question_id, timeout=120)
        submitted = submit_round_answer(round_no, question_id, plan_item)
        answered = wait_answered(round_no, question_id, timeout=180)
        observed = wait_codex_result(round_no, result_prefix, expected_answer, timeout=min(300, self.args.timeout))
        ready = self.ctx.action.session.wait_tmux_input_ready_remote(str(state["tmux_session"]), timeout=240)
        record = {
            **plan_item,
            "question_id": question_id,
            "pending": pending,
            "card_delivered": card_delivered,
            "submitted": submitted,
            "answered": answered,
            "observed": observed,
            "ready_after_round": ready,
        }
        state["rounds"].append(record)
        return record | {"business_prompt_sent": True}

    def s01_reset_case_namespace() -> dict[str, Any]:
        return self.require_checks(self.ctx.action.workspace.case_initial_reset_evidence_remote())

    def s02_create_workspace_task_and_codex_group() -> dict[str, Any]:
        repo = self.ctx.action.workspace.local_repo_fixture_remote("j0059_question")
        display = self.remote_context.stage_workspace_display("workspace")
        workspace = self.ctx.action.workspace.create_case_remote(
            suffix="workspace",
            display_name=display,
            provider="local",
            repo_url=str(repo),
            mode="local_only",
            local_path=str(repo),
        )
        metadata_root = self.ctx.action.workspace.metadata_root_remote(workspace)
        task_id = self.task_id("question_fidelity")
        task = self.ctx.action.task.write_fixture_remote(metadata_root, task_id, status="Open", assignee="")
        created = self.require_checks(self.ctx.action.intern.create_fixture_case_remote(
            workspace,
            "codex",
            intern_type="codex",
            repo_url=str(repo),
            skip_feishu_group=False,
            skip_status_notify=True,
        ))
        intern = created["intern"]
        chat = self.ctx.action.feishu.wait_chat_lookup_remote(workspace, intern, timeout=self.args.timeout)
        relay = self.ctx.action.feishu.wait_relay_registry_entry_remote(workspace, intern, timeout=self.args.timeout)
        chat_id = str(chat.get("chat_id") or "")
        self.require("j0059_chat_id_present", bool(chat_id), chat)
        state.update({
            "repo": repo,
            "workspace": workspace,
            "project": str(workspace["display"]),
            "metadata_root": metadata_root,
            "task_id": task_id,
            "task": task,
            "intern": intern,
            "created": created,
            "chat": chat,
            "chat_id": chat_id,
            "relay": relay,
            "answer_plan": deterministic_answer_plan(),
        })
        return {
            "repo": str(repo),
            "workspace": workspace,
            "task_id": task_id,
            "task": task,
            "intern": created,
            "chat_lookup": chat,
            "relay_registry": relay,
            "answer_plan": state["answer_plan"],
        }

    def s03_start_codex_session() -> dict[str, Any]:
        status = self.ctx.action.session.start_for_workspace_remote(state["workspace"], state["intern"], session_type="codex")
        tmux_session = str(status.get("tmux_session") or state["intern"])
        ready = self.ctx.action.session.wait_tmux_input_ready_remote(tmux_session, timeout=240)
        state["tmux_session"] = tmux_session
        return {"session_status": status, "tmux_session": tmux_session, "ready": ready}

    def s04_feishu_group_green_light_before_question_loop() -> dict[str, Any]:
        green = self.ctx.action.feishu.wait_current_scene_green_light_remote(
            state["workspace"],
            state["intern"],
            expected_type="codex",
            timeout=min(180, self.args.timeout),
        )
        return {"green_light": green, "answer_plan": state["answer_plan"]}

    def s15_final_consistency_summary() -> dict[str, Any]:
        mismatches = [
            {
                "round": item.get("round"),
                "kind": item.get("kind"),
                "expected_answer": item.get("expected_answer"),
                "observed_answer": ((item.get("observed") or {}).get("observed_answer")),
                "question_id": item.get("question_id"),
            }
            for item in state["rounds"]
            if ((item.get("observed") or {}).get("observed_answer")) != item.get("expected_answer")
        ]
        daemon = self.http_json("J_0059 daemon status", "GET", "/api/status", timeout=30)
        status = self.ctx.action.session.status_for_workspace_remote(state["workspace"], state["intern"])
        detail = {
            "round_count": len(state["rounds"]),
            "expected_round_count": ROUND_COUNT,
            "random_seed": RANDOM_SEED,
            "answer_plan": state.get("answer_plan"),
            "mismatches": mismatches,
            "daemon": daemon,
            "session_status": status,
        }
        self.require_classified_contract(
            "j0059_all_rounds_consistent",
            len(state["rounds"]) == ROUND_COUNT and not mismatches,
            "product_bug_codex_request_user_input_answer_mismatch",
            detail,
        )
        return detail | {"business_prompt_sent": True}

    scenarios: list[tuple[str, Any]] = [
        ("J_0059.s01_reset_case_namespace", s01_reset_case_namespace),
        ("J_0059.s02_create_workspace_task_and_codex_group", s02_create_workspace_task_and_codex_group),
        ("J_0059.s03_start_codex_session", s03_start_codex_session),
        ("J_0059.s04_feishu_group_green_light_before_question_loop", s04_feishu_group_green_light_before_question_loop),
    ]
    for scenario_id, plan_item in zip(ROUND_SCENARIO_IDS, deterministic_answer_plan(), strict=True):
        scenarios.append((scenario_id, lambda item=plan_item: run_round(item)))
    scenarios.append(("J_0059.s15_final_consistency_summary", s15_final_consistency_summary))

    self.run_ordered_scenarios(scenarios)
    self.artifacts["codex_request_user_input_answer_fidelity"] = {
        "project": state.get("project"),
        "intern": state.get("intern"),
        "chat_id": state.get("chat_id"),
        "random_seed": RANDOM_SEED,
        "answer_plan": state.get("answer_plan"),
        "rounds": state.get("rounds"),
    }
