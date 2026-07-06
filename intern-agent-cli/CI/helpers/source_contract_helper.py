from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import re
from typing import Any


class SourceContractHelper:
    def __init__(self, *, repo_root: Path, work_root: Path):
        self.repo_root = repo_root
        self.work_root = work_root

    def extension_source_candidates(self, rel_path: str) -> list[Path]:
        candidates = [
            self.work_root / "extension" / rel_path,
            self.work_root / "extension" / "dist" / rel_path,
            self.repo_root.parent / "vscode-extension" / rel_path,
            self.repo_root / rel_path,
        ]
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path)
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def product_source_evidence(self, rel_path: str, markers: list[str]) -> dict[str, Any]:
        checked = []
        for path in self.extension_source_candidates(rel_path):
            checked.append(str(path))
            if not path.is_file():
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            hits = []
            for marker in markers:
                line_no = next((idx for idx, line in enumerate(lines, start=1) if marker in line), 0)
                hits.append({"marker": marker, "line": line_no})
            return {
                "source_path": str(path),
                "markers": hits,
                "all_markers_found": all(item["line"] for item in hits),
            }
        return {
            "source_path": "",
            "checked_paths": checked,
            "markers": [{"marker": item, "line": 0} for item in markers],
            "all_markers_found": False,
        }

    def extension_bundle_path(self) -> Path:
        return self.work_root / "extension" / "dist" / "extension.js"

    def deployed_extension_dist(self) -> str:
        path = self.extension_bundle_path()
        if not path.is_file():
            raise FileNotFoundError(f"deployed extension bundle missing: {path}")
        return path.read_text(encoding="utf-8", errors="replace")

    @staticmethod
    def compact_js(text: str) -> str:
        return re.sub(r"\s+", "", text).replace("'", '"')

    def require_dist_contract(
        self,
        checks: list[tuple[str, bool]],
        require: Callable[[str, bool, dict[str, Any]], None],
    ) -> dict[str, Any]:
        results = []
        for name, ok in checks:
            require(name, ok, {})
            results.append({"name": name, "ok": ok})
        return {"checks": results, "bundle": str(self.extension_bundle_path())}

    def dist_contract_results(self, checks: list[tuple[str, bool]]) -> dict[str, Any]:
        results = [{"name": name, "ok": bool(ok)} for name, ok in checks]
        return {
            "checks": results,
            "ok": all(item["ok"] for item in results),
            "failed": [item["name"] for item in results if not item["ok"]],
            "bundle": str(self.extension_bundle_path()),
        }

    @staticmethod
    def dist_command_block(dist: str, command: str, *, window: int = 5000) -> str:
        start = dist.find(command)
        if start < 0:
            return ""
        next_match = re.search(r"registerCommand\(\s*['\"]intern\.", dist[start + len(command):])
        if next_match:
            end = start + len(command) + next_match.start()
            return dist[start:end]
        return dist[start:start + window]

    def deployed_cli_source_text(self, rel_path: str) -> dict[str, Any]:
        path = self.repo_root / rel_path
        if not path.is_file():
            return {"path": str(path), "text": "", "exists": False}
        return {"path": str(path), "text": path.read_text(encoding="utf-8", errors="replace"), "exists": True}
