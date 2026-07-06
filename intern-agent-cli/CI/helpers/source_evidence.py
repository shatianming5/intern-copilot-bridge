from __future__ import annotations

import inspect
from pathlib import Path
from types import ModuleType
from typing import Any


def handler_source_evidence(
    module: ModuleType,
    references: list[dict[str, str]],
    *,
    source_path: str = "",
) -> dict[str, Any]:
    resolved_source_path = str(getattr(module, "__file__", "") or source_path)
    source_lines: list[str] = []
    if resolved_source_path:
        try:
            source_lines = Path(resolved_source_path).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            source_lines = []

    entries: list[dict[str, Any]] = []
    for ref in references:
        function_name = ref.get("function", "")
        marker = ref.get("marker", "")
        label = ref.get("label") or function_name or marker
        entry: dict[str, Any] = {"label": label}
        if function_name:
            entry["function"] = function_name
            obj = getattr(module, function_name, None)
            if obj is not None:
                try:
                    lines, start = inspect.getsourcelines(obj)
                    entry["line_start"] = start
                    entry["line_end"] = start + len(lines) - 1
                except (OSError, TypeError):
                    pass
        if marker:
            entry["marker"] = marker
            search_start = int(entry.get("line_start") or 1)
            search_end = int(entry.get("line_end") or len(source_lines))
            search_lines = source_lines[search_start - 1:search_end]
            for offset, line in enumerate(search_lines, start=search_start):
                if marker in line:
                    entry["line_hint"] = offset
                    break
        entries.append(entry)
    return {"deployed_source_path": resolved_source_path, "references": entries}
