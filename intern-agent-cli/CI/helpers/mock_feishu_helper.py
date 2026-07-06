from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

from CI.helpers.native_error import NativeCaseError
from CI.helpers.source_evidence import handler_source_evidence


VISIBLE_PREFIX = "[CI模拟]"


class RelayDriverObj:
    def __init__(self, **kwargs: Any):
        self.__dict__.update(kwargs)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class RelayDriverMemoryChatConfig:
    def __init__(self):
        self._trigger_modes: dict[str, str] = {}

    def valid_modes(self) -> tuple[str, ...]:
        return ("all", "at_only")

    def get_trigger_mode(self, chat_id: str) -> str:
        return self._trigger_modes.get(chat_id, "all")

    def set_trigger_mode(self, chat_id: str, mode: str) -> bool:
        old = self.get_trigger_mode(chat_id)
        self._trigger_modes[chat_id] = mode
        return old != mode


class RelayDriverFakeAPI:
    def __init__(self):
        self.replies: list[dict[str, Any]] = []
        self.messages: list[dict[str, Any]] = []
        self.cards: list[dict[str, Any]] = []
        self.card_updates: list[dict[str, Any]] = []
        self.chat_info: dict[str, dict[str, Any]] = {}
        self.created_chats: list[dict[str, Any]] = []
        self.members: dict[str, list[str]] = {}

    def reply_message(self, message_id: str, text: str):
        self.replies.append({"message_id": message_id, "text": text})
        return None

    def send_message(self, chat_id: str, text: str):
        message_id = f"ci_msg_{len(self.messages) + 1}"
        self.messages.append({"chat_id": chat_id, "message_id": message_id, "text": text})
        return message_id, None

    def send_interactive_card(self, chat_id: str, card_json: dict[str, Any]):
        message_id = f"ci_card_{len(self.cards) + 1}"
        self.cards.append({"chat_id": chat_id, "message_id": message_id, "card": card_json})
        return message_id, None

    def update_interactive_card(self, message_id: str, card_json: dict[str, Any]):
        self.card_updates.append({"message_id": message_id, "card": card_json})
        return None

    def get_user_info(self, open_id: str):
        return {"open_id": open_id, "name": f"user_{open_id[-6:]}"}, None

    def get_bot_open_id(self, chat_id: str):
        return "ou_ci_bot", None

    def get_chat_info(self, chat_id: str):
        return self.chat_info.setdefault(chat_id, {"chat_id": chat_id, "description": ""}), None

    def update_chat(self, chat_id: str, **kwargs: Any):
        info = self.chat_info.setdefault(chat_id, {"chat_id": chat_id, "description": ""})
        info.update(kwargs)
        return None

    def create_chat(self, name: str, description: str, owner_open_id: str):
        chat_id = f"oc_ci_helper_{len(self.created_chats) + 1}"
        self.created_chats.append({
            "chat_id": chat_id,
            "name": name,
            "description": description,
            "owner_open_id": owner_open_id,
        })
        self.members.setdefault(chat_id, [owner_open_id])
        return chat_id, None

    def get_chat_members(self, chat_id: str):
        return list(self.members.get(chat_id, [])), None

    def add_chat_members(self, chat_id: str, open_ids: list[str]):
        members = self.members.setdefault(chat_id, [])
        for open_id in open_ids:
            if open_id not in members:
                members.append(open_id)
        return None


class RelayDriverFakeRegistry:
    def __init__(
        self,
        *,
        project: str,
        intern: str,
        chat_id: str,
        owner_open_id: str,
        mapped: bool = True,
    ):
        self.project = project
        self.intern = intern
        self.chat_id = chat_id
        self.owner_open_id = owner_open_id
        self.entries_by_chat: dict[str, dict[str, Any]] = {}
        if mapped:
            self.entries_by_chat[chat_id] = {
                "name": intern,
                "intern_name": intern,
                "project": project,
                "machine_id": "debug-a",
                "type": "codex",
                "chat_id": chat_id,
            }
        self.helpers: dict[str, dict[str, Any]] = {}
        self.helper_audit: list[dict[str, Any]] = []
        self.machines: dict[str, dict[str, Any]] = {
            "debug-a": {
                "machine_id": "debug-a",
                "ws_connected": True,
                "owner_open_id": owner_open_id,
                "cli_versions": {"codex": "ci-source-driver"},
                "interns": [intern] if mapped else [],
                "interns_detail": [
                    {"name": intern, "project": project, "type": "codex", "online": True}
                ] if mapped else [],
                "workspaces": [{"display_name": project, "workspace_id": f"ws_{project}"}],
            },
            "debug-b": {
                "machine_id": "debug-b",
                "ws_connected": True,
                "owner_open_id": owner_open_id,
                "cli_versions": {"codex": "ci-source-driver"},
                "interns": [],
                "interns_detail": [],
                "workspaces": [{"display_name": f"{project}_secondary", "workspace_id": f"ws_{project}_b"}],
            },
        }

    def find_entry_by_chat(self, chat_id: str):
        return self.entries_by_chat.get(chat_id)

    def find_helper_by_chat(self, chat_id: str):
        for machine_id, helper in self.helpers.items():
            if helper.get("chat_id") == chat_id:
                return {"machine_id": machine_id, **helper}
        return None

    def find_intern_by_chat(self, chat_id: str):
        entry = self.find_entry_by_chat(chat_id) or {}
        return entry.get("name", "")

    def get_machines_summary(self):
        return self.machines

    def get_helpers_summary(self):
        return self.helpers

    def get_current_scene(self):
        return {"summary": {"active_groups": len(self.entries_by_chat), "active_red_groups": 0, "stale_persisted_groups": 0}}

    def get_connection(self, machine_id: str):
        return object() if (self.machines.get(machine_id) or {}).get("ws_connected") else None

    def has_capability(self, machine_id: str, capability: str) -> bool:
        return capability in {"detail_mode", "no_collapse_mode", "attachments"}

    def get_machine_helper(self, machine_id: str):
        return self.helpers.get(machine_id, {
            "machine_id": machine_id,
            "helper_id": f"machine_helper_{machine_id}",
            "runtime": "codex",
            "chat_id": "",
            "status": "stopped",
        })

    def register_machine_helper(self, machine_id: str, **kwargs: Any):
        helper = dict(self.get_machine_helper(machine_id))
        helper.update(kwargs)
        helper["machine_id"] = machine_id
        self.helpers[machine_id] = helper
        return helper

    def update_machine_helper_status(self, machine_id: str, status: str, **kwargs: Any):
        return self.register_machine_helper(machine_id, status=status, **kwargs)

    def append_machine_helper_audit(self, machine_id: str, action: str, *args: Any, detail: dict[str, Any] | None = None):
        entry = {"machine_id": machine_id, "action": action, "args": list(args), "detail": detail or {}}
        self.helper_audit.append(entry)
        return entry


class RelayDriverFakeRelayWS:
    def __init__(self, registry: RelayDriverFakeRegistry):
        self.registry = registry
        self.sent_payloads: list[dict[str, Any]] = []
        self.detail_modes: dict[str, str] = {}
        self.no_collapse_modes: dict[str, str] = {}

    def detail_mode_request(self, chat_id: str, op: str, mode: str | None = None, timeout: int = 10):
        return self._mode_request(self.detail_modes, "full", chat_id, op, mode)

    def no_collapse_mode_request(self, chat_id: str, op: str, mode: str | None = None, timeout: int = 10):
        return self._mode_request(self.no_collapse_modes, "on", chat_id, op, mode)

    def _mode_request(self, store: dict[str, str], default: str, chat_id: str, op: str, mode: str | None):
        if op == "get":
            return {"mode": store.get(chat_id, default), "changed": False}, None
        if op == "set" and mode:
            old = store.get(chat_id, default)
            store[chat_id] = mode
            return {"mode": mode, "changed": old != mode}, None
        return None, "invalid_mode"

    def send_to_machine(self, machine_id: str, data: dict[str, Any]):
        connected = bool((self.registry.machines.get(machine_id) or {}).get("ws_connected"))
        entry = {"machine_id": machine_id, "payload": dict(data), "connected": connected}
        self.sent_payloads.append(entry)
        if not connected:
            return False
        if data.get("type") == "helper_action":
            action = str(data.get("helper_action") or "")
            status = {"start": "running", "stop": "stopped", "invite_owner": "running"}.get(action, "updated")
            self.registry.register_machine_helper(
                machine_id,
                helper_id=str(data.get("helper_id") or f"machine_helper_{machine_id}"),
                runtime=str(data.get("runtime") or "codex"),
                chat_id=str(data.get("chat_id") or ""),
                status=status,
                last_operator_open_id=str(data.get("operator_open_id") or ""),
            )
        return True

    def send_to_machine_result(self, machine_id: str, data: dict[str, Any], payload_bytes: int | None = None):
        return self.send_to_machine(machine_id, data), ""


class MockFeishuHelper:
    def __init__(self, *, prefix: str = VISIBLE_PREFIX):
        self.prefix = prefix

    def visible_text(self, action: str) -> str:
        return f"{self.prefix} {action}"

    def retained_scene_policy(self, chat_id: str) -> dict[str, Any]:
        return {
            "chat_id": chat_id,
            "retained_scene": True,
            "cleanup_policy": "start_only",
            "end_cleanup": False,
        }

    def snapshot(self, api: Any, relay_ws: Any) -> dict[str, int]:
        return {
            "replies": len(getattr(api, "replies", [])),
            "messages": len(getattr(api, "messages", [])),
            "cards": len(getattr(api, "cards", [])),
            "card_updates": len(getattr(api, "card_updates", [])),
            "payloads": len(getattr(relay_ws, "sent_payloads", [])),
        }

    def send_visible(self, api: Any, *, chat_id: str, action: str) -> dict[str, Any]:
        text = self.visible_text(action)
        message_id, error = api.send_message(chat_id, text)
        evidence = {
            "chat_id": chat_id,
            "text": text,
            "message_id": message_id,
            "error": error,
            **self.retained_scene_policy(chat_id),
        }
        return evidence

    def surface_delta(self, api: Any, relay_ws: Any, before: dict[str, int]) -> dict[str, Any]:
        return {
            "new_replies": getattr(api, "replies", [])[before["replies"]:],
            "new_messages": getattr(api, "messages", [])[before["messages"]:],
            "new_cards": getattr(api, "cards", [])[before["cards"]:],
            "new_card_updates": getattr(api, "card_updates", [])[before["card_updates"]:],
            "new_payloads": getattr(relay_ws, "sent_payloads", [])[before["payloads"]:],
        }

    @staticmethod
    def missing_handler_entrypoints(
        module: Any,
        handler_names: tuple[str, ...] = ("create_message_handler", "create_card_callback_handler"),
    ) -> list[str]:
        return [name for name in handler_names if not hasattr(module, name)]

    @staticmethod
    def source_driver_metadata(
        *,
        namespace: str,
        deployed_source: str,
        forbidden_endpoints: tuple[str, ...] = ("/api/ci/feishu_message", "/api/ci/card_callback"),
    ) -> dict[str, Any]:
        return {
            "driver_kind": "deployed_feishu_relay_source_driver",
            "source_driver_only": True,
            "namespace": namespace,
            "deployed_source": deployed_source,
            "forbidden_endpoints": forbidden_endpoints,
        }

    @staticmethod
    def load_relay_source_driver_module(*, repo_root: Path, artifacts: dict[str, Any]):
        source = repo_root / "scripts" / "relay" / "feishu_relay.py"
        if not source.is_file():
            raise NativeCaseError(f"environment_missing: deployed relay source not found: {source}")
        module_name = f"_intern_ci_deployed_feishu_relay_{os.getpid()}_{time.time_ns()}"
        spec = importlib.util.spec_from_file_location(module_name, source)
        if spec is None or spec.loader is None:
            raise NativeCaseError(f"unable to load deployed relay source spec: {source}")
        module = importlib.util.module_from_spec(spec)
        for import_root in (source.parent, repo_root / "scripts", repo_root):
            import_root_text = str(import_root)
            if import_root_text not in sys.path:
                sys.path.insert(0, import_root_text)
        spec.loader.exec_module(module)
        module.chat_config = RelayDriverMemoryChatConfig()
        artifacts.setdefault("relay_source_driver", {})["source_path"] = str(source)
        return module

    @staticmethod
    def machine_config_schema() -> dict[str, Any]:
        return {
            "schema": "intern-agents.machine-config.v1",
            "groups": [
                {
                    "key": "ci_f_0036_policy",
                    "title": "CI F 0036 Policy",
                    "fields": [
                        {
                            "key": "codex_lb_mode",
                            "title": "Codex LB Mode",
                            "type": "select",
                            "default": "enabled",
                            "options": [
                                {
                                    "value": "enabled",
                                    "label": "enabled",
                                    "description": "CI policy sync enabled branch.",
                                    "policy_patch": {"ci_f_0036": {"codex_lb_mode": "enabled"}},
                                },
                                {
                                    "value": "disabled",
                                    "label": "disabled",
                                    "description": "CI policy sync disabled branch.",
                                    "policy_patch": {"ci_f_0036": {"codex_lb_mode": "disabled"}},
                                },
                            ],
                        }
                    ],
                }
            ],
        }

    @staticmethod
    def prepare_machine_config_root(
        *,
        artifact_dir: Path,
        case_no: str,
        run_token: str,
        resource_namespace: str,
        schema: dict[str, Any],
        artifacts: dict[str, Any],
    ) -> Path:
        root = artifact_dir / f"machine_config_root_{case_no}_{run_token}"
        relay_dir = root / "enterprise_policy" / "relay"
        relay_dir.mkdir(parents=True, exist_ok=True)
        policy = {
            "schema": "intern-agents.enterprise-policy.v1",
            "deployment_id": f"{resource_namespace}_deployment",
            "capabilities": {},
            "machine_config": schema,
        }
        policy_path = relay_dir / "policy.json"
        policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            policy_path.chmod(0o600)
        except OSError:
            pass
        artifacts.setdefault("machine_config_policy", {})["root"] = str(root)
        artifacts.setdefault("machine_config_policy", {})["policy_path"] = str(policy_path)
        artifacts.setdefault("machine_config_policy", {})["schema"] = schema
        return root

    def relay_driver_context(
        self,
        *,
        module: Any,
        namespace: str,
        run_token: str,
        case_label: str,
        mapped: bool = True,
        machine_config_schema: dict[str, Any] | None = None,
        machine_config_root: Path | None = None,
        project: str = "",
        intern: str = "",
        chat_id: str = "",
        owner_open_id: str = "",
        non_owner_open_id: str = "",
    ) -> dict[str, Any]:
        if machine_config_root is not None and hasattr(module, "_root_dir"):
            module._root_dir = str(machine_config_root)
        project = project or f"{namespace}_{case_label.lower()}"
        intern = intern or f"intern_{namespace}_{case_label.lower()}"
        chat_id = chat_id or f"oc_{namespace}_{case_label.lower()}_{run_token}"
        owner_open_id = owner_open_id or f"ou_{namespace}_owner"
        non_owner_open_id = non_owner_open_id or f"ou_{namespace}_non_owner"
        api = RelayDriverFakeAPI()
        registry = RelayDriverFakeRegistry(
            project=project,
            intern=intern,
            chat_id=chat_id,
            owner_open_id=owner_open_id,
            mapped=mapped,
        )
        relay_ws = RelayDriverFakeRelayWS(registry)
        if hasattr(module, "_set_api_for_callback"):
            module._set_api_for_callback(api)
        helper_policy = {
            "default_visibility": "all",
            "app_owner_open_id": owner_open_id,
            "admins": [owner_open_id],
            "machine_grants": {
                "debug-a": {"view": [non_owner_open_id], "helper_ops": [owner_open_id]},
                "debug-b": {"view": [non_owner_open_id], "helper_ops": [owner_open_id]},
            },
        }
        return {
            "module": module,
            "api": api,
            "registry": registry,
            "relay_ws": relay_ws,
            "helper_policy": helper_policy,
            "project": project,
            "intern": intern,
            "chat_id": chat_id,
            "owner_open_id": owner_open_id,
            "non_owner_open_id": non_owner_open_id,
            "machine_config_schema": machine_config_schema,
            "machine_config_root": machine_config_root,
        }

    def relay_driver_context_for_remote(self, remote: Any, **kwargs: Any) -> dict[str, Any]:
        module = self.load_relay_source_driver_module(repo_root=remote.repo_root, artifacts=remote.artifacts)
        machine_config_schema = kwargs.get("machine_config_schema")
        if machine_config_schema is not None and kwargs.get("machine_config_root") is None:
            kwargs["machine_config_root"] = self.prepare_machine_config_root(
                artifact_dir=remote.artifact_dir,
                case_no=remote.case_no,
                run_token=remote.run_token,
                resource_namespace=remote.resource_namespace,
                schema=machine_config_schema,
                artifacts=remote.artifacts,
            )
        ctx = self.relay_driver_context(
            module=module,
            namespace=remote.resource_namespace,
            run_token=remote.run_token,
            **kwargs,
        )
        ctx["source_path"] = str(remote.artifacts.get("relay_source_driver", {}).get("source_path", ""))
        return ctx

    def relay_driver_message_for_remote(self, remote: Any, ctx: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self.relay_driver_message(
            ctx,
            namespace=remote.resource_namespace,
            case_id=remote.case_id,
            **kwargs,
        )

    def relay_driver_card_action_for_remote(self, remote: Any, ctx: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return self.relay_driver_card_action(
            ctx,
            case_id=remote.case_id,
            **kwargs,
        )

    @staticmethod
    def relay_driver_handler_evidence(ctx: dict[str, Any], references: list[dict[str, str]], *, source_path: str = "") -> dict[str, Any]:
        return handler_source_evidence(
            ctx["module"],
            references,
            source_path=source_path or str(ctx.get("source_path") or ""),
        )

    def relay_driver_message(
        self,
        ctx: dict[str, Any],
        *,
        text: str,
        namespace: str,
        case_id: str,
        chat_id: str = "",
        sender_open_id: str = "",
        chat_type: str = "group",
    ) -> dict[str, Any]:
        api: RelayDriverFakeAPI = ctx["api"]
        relay_ws: RelayDriverFakeRelayWS = ctx["relay_ws"]
        module = ctx["module"]
        target_chat_id = chat_id or ctx["chat_id"]
        before = self.snapshot(api, relay_ws)
        visible_message = self.send_visible(
            api,
            chat_id=target_chat_id,
            action=self.message_action(text),
        )
        message_id = f"om_{namespace}_{len(api.replies) + len(api.cards) + len(relay_ws.sent_payloads) + 1}"
        event_id = f"ev_{namespace}_{case_id}_{message_id}_{time.time_ns()}"
        data = RelayDriverObj(
            header=RelayDriverObj(event_id=event_id),
            event=RelayDriverObj(
                message=RelayDriverObj(
                    chat_id=target_chat_id,
                    message_id=message_id,
                    message_type="text",
                    content=json.dumps({"text": text}, ensure_ascii=False),
                    mentions=[],
                    create_time="",
                    chat_type=chat_type,
                ),
                sender=RelayDriverObj(
                    sender_type="user",
                    sender_id=RelayDriverObj(open_id=sender_open_id or ctx["owner_open_id"]),
                ),
            ),
        )
        handler = module.create_message_handler(
            api,
            ctx["registry"],
            relay_ws,
            helper_policy=ctx["helper_policy"],
            machine_config_schema=ctx.get("machine_config_schema"),
        )
        handler(data)
        surface = self.surface_delta(api, relay_ws, before)
        return self.message_ingress_result(
            text=text,
            chat_id=target_chat_id,
            chat_type=chat_type,
            message_id=message_id,
            event_id=event_id,
            visible_message=visible_message,
            surface=surface,
        )

    def relay_driver_card_action(
        self,
        ctx: dict[str, Any],
        *,
        value: dict[str, Any],
        case_id: str,
        form_value: dict[str, Any] | None = None,
        operator_open_id: str = "",
        chat_id: str = "",
    ) -> dict[str, Any]:
        module = ctx["module"]
        target_chat_id = chat_id or ctx["chat_id"]
        before = self.snapshot(ctx["api"], ctx["relay_ws"])
        visible_message = self.send_visible(
            ctx["api"],
            chat_id=target_chat_id,
            action=self.card_action(value, form_value=form_value),
        )
        data = RelayDriverObj(
            header=RelayDriverObj(event_id=f"ev_card_{case_id}_{time.time_ns()}"),
            event=RelayDriverObj(
                action=RelayDriverObj(value=value, form_value=form_value),
                context=RelayDriverObj(open_chat_id=target_chat_id),
                operator=RelayDriverObj(open_id=operator_open_id or ctx["owner_open_id"]),
                operator_id=RelayDriverObj(open_id=operator_open_id or ctx["owner_open_id"]),
            ),
        )
        handler = module.create_card_callback_handler(
            ctx["api"],
            ctx["registry"],
            ctx["relay_ws"],
            helper_policy=ctx["helper_policy"],
            machine_config_schema=ctx.get("machine_config_schema"),
        )
        response = handler(data)
        return self.card_ingress_result(
            chat_id=target_chat_id,
            value=value,
            form_value=form_value,
            response=response,
            visible_message=visible_message,
            surface=self.surface_delta(ctx["api"], ctx["relay_ws"], before),
        )

    @staticmethod
    def response_summary(response: Any) -> dict[str, Any]:
        toast = getattr(response, "toast", None)
        card = getattr(response, "card", None)
        return {
            "toast_type": getattr(toast, "type", ""),
            "toast_i18n": getattr(toast, "i18n", {}),
            "card_type": getattr(card, "type", ""),
            "card_data": getattr(card, "data", None),
        }

    @staticmethod
    def message_ingress_result(
        *,
        text: str,
        chat_id: str,
        chat_type: str,
        message_id: str,
        event_id: str,
        visible_message: dict[str, Any],
        surface: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "text": text,
            "chat_id": chat_id,
            "chat_type": chat_type,
            "message_id": message_id,
            "event_id": event_id,
            "visible_message": visible_message,
            "visible_message_id": visible_message.get("message_id"),
            "retained_scene": True,
            **surface,
        }

    def card_ingress_result(
        self,
        *,
        chat_id: str,
        value: dict[str, Any],
        form_value: dict[str, Any] | None,
        response: Any,
        visible_message: dict[str, Any],
        surface: dict[str, Any],
    ) -> dict[str, Any]:
        summary = self.response_summary(response)
        summary.update({
            "chat_id": chat_id,
            "callback_payload": {"value": value, "form_value": form_value},
            "visible_message": visible_message,
            "visible_message_id": visible_message.get("message_id"),
            "retained_scene": True,
            **surface,
        })
        return summary

    @staticmethod
    def card_values(card: Any) -> list[dict[str, Any]]:
        values: list[dict[str, Any]] = []

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                value = node.get("value")
                if isinstance(value, dict):
                    values.append(value)
                for item in node.values():
                    visit(item)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(card)
        return values

    def card_buttons(self, card: Any) -> list[dict[str, Any]]:
        buttons: list[dict[str, Any]] = []

        def text_content(node: Any) -> str:
            if isinstance(node, dict):
                parts: list[str] = []
                content = node.get("content")
                if isinstance(content, str):
                    parts.append(content)
                for item in node.values():
                    value = text_content(item)
                    if value:
                        parts.append(value)
                return " ".join(parts)
            if isinstance(node, list):
                return " ".join(text_content(item) for item in node if text_content(item))
            return ""

        def semantic_actions(button: dict[str, Any]) -> list[str]:
            value = button.get("value") if isinstance(button.get("value"), dict) else {}
            text = str(button.get("text") or "").lower()
            name = str(button.get("name") or "").lower()
            raw_action = " ".join(
                str(value.get(key) or "").lower()
                for key in ("config_action", "machine_config_action", "helper_action", "action")
            )
            haystack = " ".join((text, name, raw_action))
            if "cancel" in haystack or "取消" in haystack:
                return ["cancel"]
            if (
                "save" in haystack
                or "保存" in haystack
                or name == "submit"
                or str(button.get("action_type") or "") == "form_submit"
            ):
                return ["save"]
            return [item for item in (raw_action.split() or []) if item]

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                if node.get("tag") == "button":
                    button = {
                        "name": node.get("name", ""),
                        "text": text_content(node.get("text")),
                        "type": node.get("type", ""),
                        "action_type": node.get("action_type", ""),
                        "value": node.get("value") if isinstance(node.get("value"), dict) else {},
                    }
                    button["semantic_actions"] = semantic_actions(button)
                    buttons.append(button)
                for item in node.values():
                    visit(item)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(card)
        return buttons

    def card_action_summary(self, card: Any) -> dict[str, Any]:
        buttons = self.card_buttons(card)
        actions = sorted({action for button in buttons for action in button.get("semantic_actions", [])})
        return {"actions": actions, "buttons": buttons}

    def find_card_action_value(self, card: Any, semantic_action: str) -> dict[str, Any]:
        for button in self.card_buttons(card):
            if semantic_action in (button.get("semantic_actions") or []):
                value = button.get("value")
                return dict(value) if isinstance(value, dict) else {}
        return {}

    @staticmethod
    def form_current_values(card: Any) -> dict[str, Any]:
        fields: dict[str, Any] = {}

        def visit(node: Any) -> None:
            if isinstance(node, dict):
                name = node.get("name")
                if name in {"trigger_mode", "detail_mode", "no_collapse_mode"}:
                    fields[str(name)] = node.get("initial_option")
                for item in node.values():
                    visit(item)
            elif isinstance(node, list):
                for item in node:
                    visit(item)

        visit(card)
        return fields

    @staticmethod
    def card_text(card: Any) -> str:
        return json.dumps(card, ensure_ascii=False, sort_keys=True)

    def first_card_value_with_key(self, card: Any, key: str) -> dict[str, Any]:
        for value in self.card_values(card):
            if value.get(key):
                return value
        return {}

    def config_value(self, card: dict[str, Any]) -> dict[str, Any]:
        value = self.first_card_value_with_key(card, "config_action")
        if value:
            return value
        raise NativeCaseError("config card submit value not found")

    def machine_config_value(self, card: dict[str, Any]) -> dict[str, Any]:
        value = self.first_card_value_with_key(card, "machine_config_action")
        if value:
            return value
        raise NativeCaseError("machine_config card submit value not found")

    @staticmethod
    def config_snapshot(ctx: dict[str, Any]) -> dict[str, Any]:
        chat_id = ctx["chat_id"]
        return {
            "trigger_mode": ctx["module"].chat_config.get_trigger_mode(chat_id),
            "detail_mode": ctx["relay_ws"].detail_modes.get(chat_id, "full"),
            "no_collapse_mode": ctx["relay_ws"].no_collapse_modes.get(chat_id, "on"),
            "sent_payload_count": len(ctx["relay_ws"].sent_payloads),
            "card_update_count": len(ctx["api"].card_updates),
        }

    @staticmethod
    def machine_config_state(ctx: dict[str, Any]) -> dict[str, Any]:
        root = ctx.get("machine_config_root")
        if not root:
            return {"schema": "intern-agents.machine-config-state.v1", "machines": {}}
        path = Path(root) / "enterprise_policy" / "relay" / "machine_config_state.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            data = {"schema": "intern-agents.machine-config-state.v1", "machines": {}}
        return {"path": str(path), **data}

    @staticmethod
    def message_action(text: str) -> str:
        return f"用户发送 {text}"

    @staticmethod
    def card_action(value: dict[str, Any], *, form_value: dict[str, Any] | None = None) -> str:
        action = ""
        for key in ("config_action", "machine_config_action", "helper_action", "action"):
            if value.get(key):
                action = str(value[key])
                break
        label = action or "card callback"
        if form_value:
            return f"用户点击 {label} 并提交表单"
        return f"用户点击 {label}"
