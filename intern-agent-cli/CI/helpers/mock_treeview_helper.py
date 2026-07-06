from __future__ import annotations

from typing import Any


class MockTreeViewHelper:
    @staticmethod
    def command_invocation(
        gui_command: str,
        *,
        args: dict[str, Any] | list[Any] | None = None,
        cli_args: list[str] | None = None,
        dry_run: bool = True,
        business_prompt_sent: bool = False,
    ) -> dict[str, Any]:
        evidence: dict[str, Any] = {
            "event": "command_invocation",
            "gui_command": gui_command,
            "args": args if args is not None else {},
            "dry_run": dry_run,
            "business_prompt_sent": business_prompt_sent,
        }
        if cli_args is not None:
            evidence["cli_args"] = list(cli_args)
        return evidence

    def tree_item_click(
        self,
        gui_command: str,
        *,
        item: dict[str, Any],
        cli_args: list[str] | None = None,
        business_prompt_sent: bool = False,
    ) -> dict[str, Any]:
        return {
            **self.command_invocation(
                gui_command,
                args=item.get("command_args") if isinstance(item.get("command_args"), dict) else item,
                cli_args=cli_args,
                business_prompt_sent=business_prompt_sent,
            ),
            "event": "tree_item_click",
            "tree_item": item,
        }

    def context_menu_click(
        self,
        view_item: str,
        gui_command: str,
        *,
        item: dict[str, Any] | None = None,
        cli_args: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            **self.command_invocation(gui_command, args=item or {}, cli_args=cli_args),
            "event": "context_menu_click",
            "view_item": view_item,
        }

    def quickpick_choice(
        self,
        gui_command: str,
        *,
        items: list[dict[str, Any]],
        selected_label: str,
        cli_args: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            **self.command_invocation(gui_command, args={"selected_label": selected_label}, cli_args=cli_args),
            "event": "quickpick_choice",
            "items": items,
            "selected_label": selected_label,
        }

    def input_box_submission(
        self,
        gui_command: str,
        *,
        prompt: str,
        value: str,
        accepted: bool,
        cli_args: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            **self.command_invocation(gui_command, args={"value": value}, cli_args=cli_args),
            "event": "input_box_submission",
            "prompt": prompt,
            "value": value,
            "accepted": accepted,
        }

    @staticmethod
    def cli_equivalence(gui_command: str, *, cli: str, actual_commands: list[list[str]]) -> dict[str, Any]:
        actual = [" ".join(command) for command in actual_commands]
        return {
            "gui_command": gui_command,
            "expected_cli": cli,
            "actual_commands": actual,
            "equivalent": all(cli_part in " ".join(actual) for cli_part in cli.split(" ") if cli_part),
        }

    @staticmethod
    def context_menu_commands(package_json: dict[str, Any], view_item: str) -> list[str]:
        commands: list[str] = []
        menus = ((package_json.get("contributes") or {}).get("menus") or {}).get("view/item/context") or []
        for entry in menus:
            if not isinstance(entry, dict):
                continue
            when = str(entry.get("when") or "")
            command = str(entry.get("command") or "")
            if command and f"viewItem == {view_item}" in when:
                commands.append(command)
        return commands
