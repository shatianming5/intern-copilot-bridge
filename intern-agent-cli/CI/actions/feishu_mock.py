#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest import mock

CLI_ROOT = Path(__file__).resolve().parents[2]
if str(CLI_ROOT) not in sys.path:
    sys.path.insert(0, str(CLI_ROOT))


def _add_relay_import_roots() -> None:
    roots = [CLI_ROOT]
    for item in sys.path:
        if item:
            roots.append(Path(item))
    for root in roots:
        relay_root = root / "scripts" / "relay"
        if (relay_root / "chat_config.py").is_file() and str(relay_root) not in sys.path:
            sys.path.insert(0, str(relay_root))


_add_relay_import_roots()

from scripts.relay import feishu_relay
from CI.helpers.mock_feishu_helper import MockFeishuHelper


class _Obj:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


@dataclass
class MockFeishuApi:
    visible_messages: list[dict[str, Any]] = field(default_factory=list)
    replies: list[dict[str, Any]] = field(default_factory=list)

    def send_visible_message(self, chat_id: str, text: str, *, source: str) -> dict[str, Any]:
        item = {
            "chat_id": chat_id,
            "text": text,
            "source": source,
            "ts": time.time(),
        }
        self.visible_messages.append(item)
        return item

    def reply_message(self, message_id: str, text: str) -> str:
        self.replies.append({"message_id": message_id, "text": text})
        return ""

    def get_bot_open_id(self, chat_id: str) -> tuple[str, str]:
        return "ou_ci_bot", ""

    def get_user_info(self, open_id: str) -> dict[str, Any]:
        return {"name": open_id, "open_id": open_id}


@dataclass
class MockRelayRegistry:
    entries_by_chat: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register_chat(
        self,
        *,
        chat_id: str,
        intern_name: str,
        machine_id: str,
        runtime: str = "codex",
        project: str = "",
    ) -> None:
        self.entries_by_chat[chat_id] = {
            "chat_id": chat_id,
            "name": intern_name,
            "machine_id": machine_id,
            "type": runtime,
            "capabilities": ["attachments"],
            "project": project,
        }

    def find_entry_by_chat(self, chat_id: str) -> dict[str, Any] | None:
        return self.entries_by_chat.get(chat_id)

    def find_helper_by_chat(self, chat_id: str) -> dict[str, Any] | None:
        return None

    def find_intern_by_chat(self, chat_id: str) -> str:
        entry = self.entries_by_chat.get(chat_id) or {}
        return str(entry.get("name") or "")

    def has_capability(self, machine_id: str, capability: str) -> bool:
        return True

    def get_machines_summary(self) -> dict[str, Any]:
        machines: dict[str, Any] = {}
        for entry in self.entries_by_chat.values():
            machine_id = str(entry.get("machine_id") or "")
            if machine_id:
                machines[machine_id] = {"machine_id": machine_id, "online": True}
        return machines


@dataclass
class MockRelayWebSocket:
    sent_payloads: list[dict[str, Any]] = field(default_factory=list)

    def send_to_machine(self, machine_id: str, payload: dict[str, Any]) -> bool:
        self.sent_payloads.append({"machine_id": machine_id, "payload": payload})
        return True

    def detail_mode_request(self, chat_id: str, op: str, mode: str = "") -> tuple[dict[str, Any], str]:
        return {"mode": mode or "summary", "changed": False}, ""


class CIMockRelay(feishu_relay.FeishuRelayIngress):
    """CI-only relay that inherits the real relay ingress and mocks Feishu input."""

    def __init__(
        self,
        *,
        api: MockFeishuApi | None = None,
        registry: MockRelayRegistry | None = None,
        relay_ws_server: MockRelayWebSocket | None = None,
    ) -> None:
        self._reset_relay_counters()
        super().__init__(
            api or MockFeishuApi(),
            registry or MockRelayRegistry(),
            relay_ws_server or MockRelayWebSocket(),
            enterprise_policy={},
        )

    def _reset_relay_counters(self) -> None:
        for name in (
            "_feishu_msg_count",
            "_feishu_last_msg_time",
            "_feishu_im_message_count",
            "_feishu_im_message_last_time",
            "_feishu_card_action_count",
            "_feishu_card_action_last_time",
        ):
            if hasattr(feishu_relay, name):
                setattr(feishu_relay, name, 0)
        seen = getattr(feishu_relay, "_seen_event_ids", None)
        if hasattr(seen, "clear"):
            seen.clear()

    def counts(self) -> dict[str, int]:
        return {
            "feishu_msg_count": int(getattr(feishu_relay, "_feishu_msg_count", 0) or 0),
            "feishu_im_message_count": int(getattr(feishu_relay, "_feishu_im_message_count", 0) or 0),
            "feishu_card_action_count": int(getattr(feishu_relay, "_feishu_card_action_count", 0) or 0),
        }

    def ensure_chat(self, *, chat_id: str, intern_name: str, machine_id: str, project: str = "") -> None:
        self.registry.register_chat(
            chat_id=chat_id,
            intern_name=intern_name,
            machine_id=machine_id,
            project=project,
        )

    def emit_message(self, *, chat_id: str, text: str, sender_open_id: str, message_id: str = "") -> dict[str, Any]:
        message_id = message_id or f"ci_msg_{uuid.uuid4().hex}"
        data = _Obj(
            header=_Obj(event_id=f"evt_{message_id}"),
            event=_Obj(
                message=_Obj(
                    chat_id=chat_id,
                    message_id=message_id,
                    message_type="text",
                    content=json.dumps({"text": text}, ensure_ascii=False),
                    create_time=str(int(time.time() * 1000) + 1000),
                    mentions=[],
                ),
                sender=_Obj(sender_type="user", sender_id=_Obj(open_id=sender_open_id), open_id=sender_open_id),
            ),
        )
        before = len(self.relay_ws_server.sent_payloads)
        self.handle_message_event(data)
        return {
            "ok": len(self.relay_ws_server.sent_payloads) > before,
            "message_id": message_id,
            "counts": self.counts(),
            "sent": self.relay_ws_server.sent_payloads[before:],
        }

    def emit_card_action(
        self,
        *,
        chat_id: str,
        intern_name: str,
        sender_open_id: str,
        answer: str = "",
        question_id: str = "",
        question_title: str = "CI mock question",
        form_value: dict[str, Any] | None = None,
        question_keys: list[str] | None = None,
    ) -> dict[str, Any]:
        event_id = f"evt_card_{uuid.uuid4().hex}"
        value = {
            "intern_name": intern_name,
            "question_title": question_title,
        }
        if answer:
            value["answer"] = answer
        if question_keys:
            value["question_keys"] = question_keys
        if question_id:
            value["question_id"] = question_id
        data = _Obj(
            header=_Obj(event_id=event_id),
            event=_Obj(
                action=_Obj(
                    value=value,
                    form_value=form_value,
                ),
                context=_Obj(open_chat_id=chat_id),
                operator_id=_Obj(open_id=sender_open_id),
            ),
        )
        before = len(self.relay_ws_server.sent_payloads)
        response = self.handle_card_action_event(data)
        return {
            "ok": len(self.relay_ws_server.sent_payloads) > before,
            "event_id": event_id,
            "counts": self.counts(),
            "sent": self.relay_ws_server.sent_payloads[before:],
            "toast_type": getattr(getattr(response, "toast", None), "type", ""),
        }


@dataclass
class FeishuMockActions:
    ctx: Any
    relay: CIMockRelay | None = None
    helper: MockFeishuHelper = field(default_factory=MockFeishuHelper)

    def _relay(self) -> CIMockRelay:
        if self.relay is None:
            self.relay = CIMockRelay()
        return self.relay

    def _chat_id(self, chat_id: str = "") -> str:
        return chat_id or f"oc_ci_{self.ctx.identity.case_no}"

    def _machine_id(self, machine_id: str = "") -> str:
        return machine_id or str(self.ctx.machine.get("id") or "debug-a")

    def _intern_name(self, intern_name: str = "") -> str:
        return intern_name or self.ctx.identity.intern_name("worker")

    def _send_visible(self, chat_id: str, action: str, *, source: str) -> dict[str, Any]:
        visible = self._relay().api.send_visible_message(
            chat_id,
            self.helper.visible_text(action),
            source=source,
        )
        visible.update(self.helper.retained_scene_policy(chat_id))
        return visible

    def send_text(
        self,
        text: str,
        *,
        chat_id: str = "",
        intern_name: str = "",
        machine_id: str = "",
        sender_open_id: str = "ou_ci_user",
    ) -> dict[str, Any]:
        chat_id = self._chat_id(chat_id)
        intern_name = self._intern_name(intern_name)
        machine_id = self._machine_id(machine_id)
        relay = self._relay()
        relay.ensure_chat(chat_id=chat_id, intern_name=intern_name, machine_id=machine_id)
        visible = self._send_visible(
            chat_id,
            self.helper.message_action(text),
            source="feishu_mock.send_text",
        )
        result = relay.emit_message(
            chat_id=chat_id,
            text=text,
            sender_open_id=sender_open_id,
        )
        return result | {"visible_message": visible}

    def click_card(
        self,
        *,
        answer: str,
        chat_id: str = "",
        intern_name: str = "",
        machine_id: str = "",
        sender_open_id: str = "ou_ci_user",
        question_id: str = "",
        question_title: str = "CI mock question",
    ) -> dict[str, Any]:
        chat_id = self._chat_id(chat_id)
        intern_name = self._intern_name(intern_name)
        machine_id = self._machine_id(machine_id)
        relay = self._relay()
        relay.ensure_chat(chat_id=chat_id, intern_name=intern_name, machine_id=machine_id)
        visible = self._send_visible(
            chat_id,
            self.helper.card_action({"action": answer}),
            source="feishu_mock.click_card",
        )
        result = relay.emit_card_action(
            chat_id=chat_id,
            intern_name=intern_name,
            answer=answer,
            sender_open_id=sender_open_id,
            question_id=question_id,
            question_title=question_title,
        )
        return result | {"visible_message": visible}

    def submit_card_form(
        self,
        *,
        form_value: dict[str, Any],
        question_keys: list[str],
        chat_id: str = "",
        intern_name: str = "",
        machine_id: str = "",
        sender_open_id: str = "ou_ci_user",
        question_id: str = "",
        question_title: str = "CI mock question",
    ) -> dict[str, Any]:
        chat_id = self._chat_id(chat_id)
        intern_name = self._intern_name(intern_name)
        machine_id = self._machine_id(machine_id)
        relay = self._relay()
        relay.ensure_chat(chat_id=chat_id, intern_name=intern_name, machine_id=machine_id)
        visible = self._send_visible(
            chat_id,
            self.helper.card_action({}, form_value=form_value),
            source="feishu_mock.submit_card_form",
        )
        result = relay.emit_card_action(
            chat_id=chat_id,
            intern_name=intern_name,
            sender_open_id=sender_open_id,
            question_id=question_id,
            question_title=question_title,
            form_value=form_value,
            question_keys=question_keys,
        )
        return result | {"visible_message": visible}


class _FakeFeishuApi:
    def __init__(self) -> None:
        self.sent_cards: list[dict[str, Any]] = []
        self.updated_cards: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []

    def send_interactive_card(self, chat_id: str, card: dict[str, Any]) -> tuple[str, str | None]:
        msg_id = f"om_ci_{len(self.sent_cards) + 1}"
        self.sent_cards.append({"chat_id": chat_id, "card": card, "message_id": msg_id})
        return msg_id, None

    def update_interactive_card(self, message_id: str, card: dict[str, Any]) -> str | None:
        self.updated_cards.append({"message_id": message_id, "card": card})
        return None

    def send_message(self, chat_id: str, text: str) -> tuple[str, str | None]:
        msg_id = f"om_ci_text_{len(self.messages) + 1}"
        self.messages.append({"chat_id": chat_id, "text": text, "message_id": msg_id})
        return msg_id, None


class _FakeRegistry:
    def __init__(self, chat_id: str, project: str = "") -> None:
        self.chat_id = chat_id
        self.project = project

    def find_chat_id(self, intern_name: str, project: str = "") -> str:
        return self.chat_id if (not self.project or project == self.project) else ""


class _FakeThread:
    created: list["_FakeThread"] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args = args
        self.kwargs = kwargs
        self.started = False
        self.__class__.created.append(self)

    def start(self) -> None:
        self.started = True


def _load_daemon_module():
    module_name = "ci_feishu_daemon_for_codex_rui"
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = CLI_ROOT / "scripts" / "daemon" / "feishu_daemon.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load daemon module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _codex_rui_owner_self_test(*, case_id: str, repo_root: Path, work_root: Path, artifact_dir: Path) -> dict[str, Any]:
    from CI.actions.context import CaseContext

    daemon = _load_daemon_module()
    ctx = CaseContext.for_case_id(
        case_id,
        repo_root=repo_root,
        work_root=work_root,
        artifact_dir=artifact_dir,
        machine={"id": "debug-a"},
    )
    intern_name = ctx.identity.intern_name("worker")
    project = ctx.identity.workspace_name("codex_rui")
    chat_id = f"oc_ci_{ctx.identity.case_no}"
    call_id = f"call_ci_{ctx.identity.case_no}"
    questions = [{
        "id": "confirm_ci",
        "header": "CI",
        "question": "Continue Codex TUI operation?",
        "options": [
            {"label": "continue", "description": "Proceed through Codex TUI"},
            {"label": "stop", "description": "Do not proceed"},
        ],
    }]
    payload = {
        "type": "function_call",
        "name": "request_user_input",
        "call_id": call_id,
        "arguments": json.dumps({"questions": questions}, ensure_ascii=False),
    }
    feishu_api = _FakeFeishuApi()
    registry = _FakeRegistry(chat_id, project=project)
    old_api = daemon._api
    old_registry = daemon._registry

    try:
        daemon._api = feishu_api
        daemon._registry = registry
        with daemon._pq_lock:
            daemon._pending_questions.clear()
        with daemon._codex_rui_lock:
            daemon._codex_rui_seen_calls.clear()

        status, ask_resp = daemon._register_pending_question(
            intern_name,
            "request_user_input",
            questions,
            metadata={
                "source": "pre_tool_hook",
                "transcript_path": "/tmp/ci-codex-rui.jsonl",
                "tool_use_id": f"tool_ci_{ctx.identity.case_no}",
            },
            project=project,
        )
        ctx.assertion.equals(status, 200, "PreToolUse Codex question registers successfully")
        question_id = ask_resp.get("question_id", "")
        ctx.assertion.require(bool(question_id), "PreToolUse register returns question_id")
        ctx.assertion.equals(len(feishu_api.sent_cards), 1, "PreToolUse sends one Feishu card")

        _FakeThread.created = []
        with mock.patch.object(daemon.threading, "Thread", _FakeThread):
            daemon._handle_codex_request_user_input_call(
                intern_name, "/tmp/ci-codex-rui.jsonl", payload, project=project)

        ctx.assertion.equals(len(feishu_api.sent_cards), 1, "TUI watcher adopts instead of sending a second card")
        ctx.assertion.equals(len(feishu_api.updated_cards), 0, "TUI adoption does not supersede the first card")
        ctx.assertion.equals(len(_FakeThread.created), 1, "TUI owner waiter is registered")
        pending_key = daemon._pending_question_key(intern_name, project)
        with daemon._pq_lock:
            entry = daemon._pending_questions[pending_key]
        ctx.assertion.equals(entry["owner"], "codex_tui", "TUI watcher owns matching Codex question")
        ctx.assertion.equals(entry["question_id"], question_id, "TUI owner keeps original question_id")
        ctx.assertion.equals(entry["codex_tui"]["call_id"], call_id, "TUI owner records call_id")

        relay = CIMockRelay(registry=MockRelayRegistry(), relay_ws_server=MockRelayWebSocket())
        relay.ensure_chat(chat_id=chat_id, intern_name=intern_name, machine_id="debug-a", project=project)
        card = relay.emit_card_action(
            chat_id=chat_id,
            intern_name=intern_name,
            form_value={"q_0_input": "continue", "submit": "1"},
            question_keys=[questions[0]["question"]],
            question_id=question_id,
            sender_open_id="ou_ci_user",
            question_title=questions[0]["question"],
        )
        ctx.assertion.action_ok(card, "mock Feishu card callback routes through real relay handler")
        routed = card["sent"][0]["payload"]
        ctx.assertion.equals(routed["question_id"], question_id, "relay forwards question_id to daemon")
        ctx.assertion.equals(routed["project"], project, "relay forwards project to daemon")
        ctx.assertion.equals(routed["is_form"], True, "relay forwards card form submissions")
        ctx.assertion.equals(routed["form_value"]["q_0_input"], "continue", "relay forwards free-text form value")

        rc = daemon.RelayClient("ws://ci.invalid", "token", "machine-ci", registry=None, ws_server=None)
        rc._handle_relay_message(routed)
        with daemon._pq_lock:
            entry = daemon._pending_questions[pending_key]
        ctx.assertion.equals(entry["answer"], {questions[0]["question"]: "continue"}, "daemon stores answer on TUI-owned pending")
        ctx.assertion.require(entry["event"].is_set(), "daemon wakes the TUI owner waiter")

        poll = daemon._poll_pending_question(intern_name, project, question_id)
        ctx.assertion.equals(poll["status"], "answered", "poll reports answered")
        ctx.assertion.equals(poll["owner"], "codex_tui", "poll reports TUI owner")
        with daemon._pq_lock:
            ctx.assertion.require(pending_key in daemon._pending_questions, "poll does not pop TUI-owned pending")

        with mock.patch.object(daemon, "_send_codex_tui_answer", return_value=(True, None)) as send_tui:
            daemon._await_codex_tui_question_answer(intern_name, project, call_id)
        send_tui.assert_called_once()
        with daemon._pq_lock:
            ctx.assertion.equals(daemon._pending_questions.get(pending_key), None, "TUI owner cleanup removes active pending")

        return {
            "schema": "intern-agents.ci-codex-rui-owner-report.v1",
            "case_id": case_id,
            "ok": True,
            "status": "passed",
            "question_id": question_id,
            "pre_tool_cards": len(feishu_api.sent_cards),
            "relay_payload": routed,
            "poll": poll,
        }
    finally:
        daemon._api = old_api
        daemon._registry = old_registry
        with daemon._pq_lock:
            daemon._pending_questions.clear()
        with daemon._codex_rui_lock:
            daemon._codex_rui_seen_calls.clear()


def run_self_test(*, case_id: str, repo_root: Path, work_root: Path, artifact_dir: Path) -> dict[str, Any]:
    if case_id == "c_0011_codex_rui_card_owner":
        return _codex_rui_owner_self_test(
            case_id=case_id,
            repo_root=repo_root,
            work_root=work_root,
            artifact_dir=artifact_dir,
        )

    from CI.actions.context import CaseContext

    ctx = CaseContext.for_case_id(
        case_id,
        repo_root=repo_root,
        work_root=work_root,
        artifact_dir=artifact_dir,
        machine={"id": "debug-a"},
    )
    text = ctx.action.feishu_mock.send_text("CI mock text route")
    card = ctx.action.feishu_mock.click_card(answer="approve")
    relay = ctx.action.feishu_mock._relay()
    ctx.assertion.action_ok(text, "mock text event routed to relay ws")
    ctx.assertion.action_ok(card, "mock card callback routed to relay ws")
    ctx.assertion.equals(text["counts"]["feishu_im_message_count"], 1, "mock text increments relay im count")
    ctx.assertion.equals(card["counts"]["feishu_card_action_count"], 1, "mock card increments relay card count")
    ctx.assertion.equals(len(relay.api.visible_messages), 2, "mock actions emit visible Feishu messages")
    return {
        "schema": "intern-agents.ci-feishu-mock-report.v1",
        "case_id": case_id,
        "ok": True,
        "status": "passed",
        "actions": {
            "text": text,
            "card": card,
        },
        "visible_messages": relay.api.visible_messages,
        "sent_payloads": relay.relay_ws_server.sent_payloads,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run CI Feishu mock action self-test.")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--case-id", default="F_0041_real_feishu_ingress_handler_contract")
    parser.add_argument("--repo-root", default=str(CLI_ROOT.parent))
    parser.add_argument("--work-root", default="")
    parser.add_argument("--artifact-dir", default="")
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    if not args.self_test:
        parser.error("--self-test is required")
    report_path = Path(args.report)
    artifact_dir = Path(args.artifact_dir or report_path.parent)
    try:
        report = run_self_test(
            case_id=args.case_id,
            repo_root=Path(args.repo_root),
            work_root=Path(args.work_root or artifact_dir.parent),
            artifact_dir=artifact_dir,
        )
    except Exception as exc:  # noqa: BLE001
        report = {
            "schema": "intern-agents.ci-feishu-mock-report.v1",
            "case_id": args.case_id,
            "ok": False,
            "status": "failed",
            "failure_reason": str(exc),
        }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
