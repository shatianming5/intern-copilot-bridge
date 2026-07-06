from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from CI.helpers.mock_treeview_helper import MockTreeViewHelper


@dataclass
class TreeViewMockActions:
    ctx: Any
    helper: MockTreeViewHelper = field(default_factory=MockTreeViewHelper)

    def command_invocation(
        self,
        gui_command: str,
        *,
        args: dict[str, Any] | list[Any] | None = None,
        cli_args: list[str] | None = None,
        dry_run: bool = True,
        business_prompt_sent: bool = False,
    ) -> dict[str, Any]:
        return self.helper.command_invocation(
            gui_command,
            args=args,
            cli_args=cli_args,
            dry_run=dry_run,
            business_prompt_sent=business_prompt_sent,
        )

    def tree_item_click(
        self,
        gui_command: str,
        *,
        item: dict[str, Any],
        cli_args: list[str] | None = None,
        business_prompt_sent: bool = False,
    ) -> dict[str, Any]:
        return self.helper.tree_item_click(
            gui_command,
            item=item,
            cli_args=cli_args,
            business_prompt_sent=business_prompt_sent,
        )

    def context_menu_click(
        self,
        view_item: str,
        gui_command: str,
        *,
        item: dict[str, Any] | None = None,
        cli_args: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.helper.context_menu_click(view_item, gui_command, item=item, cli_args=cli_args)

    def quickpick_choice(
        self,
        gui_command: str,
        *,
        items: list[dict[str, Any]],
        selected_label: str,
        cli_args: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.helper.quickpick_choice(
            gui_command,
            items=items,
            selected_label=selected_label,
            cli_args=cli_args,
        )

    def input_box_submission(
        self,
        gui_command: str,
        *,
        prompt: str,
        value: str,
        accepted: bool,
        cli_args: list[str] | None = None,
    ) -> dict[str, Any]:
        return self.helper.input_box_submission(
            gui_command,
            prompt=prompt,
            value=value,
            accepted=accepted,
            cli_args=cli_args,
        )

    def cli_equivalence(
        self,
        gui_command: str,
        *,
        cli: str,
        actual_commands: list[list[str]],
    ) -> dict[str, Any]:
        return self.helper.cli_equivalence(gui_command, cli=cli, actual_commands=actual_commands)
