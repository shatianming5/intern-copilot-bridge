#!/usr/bin/env python3
"""Monitor recovery progress for the 2026-05-31 Feishu group incident.

This is a read-only relay-side monitor. It compares the full incident allowlist
embedded in repair_feishu_daemon_registry.py with the current Feishu chat list
and relay registry, then reports how many affected groups are available again.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib.log_paths import system_log_dir


WORK_AGENTS_ROOT = Path("/work-agents")


def load_repair_module() -> Any:
    path = Path(__file__).resolve().with_name("repair_feishu_daemon_registry.py")
    spec = importlib.util.spec_from_file_location("repair_feishu_daemon_registry", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def list_visible_chats(api: Any) -> dict[str, dict[str, Any]]:
    chats: dict[str, dict[str, Any]] = {}
    page_token = ""
    while True:
        path = "/im/v1/chats?page_size=100"
        if page_token:
            path += f"&page_token={urllib.parse.quote(page_token, safe='')}"
        data = api.request("GET", path)
        for item in data.get("items") or []:
            chat_id = str(item.get("chat_id") or "")
            if chat_id:
                chats[chat_id] = dict(item)
        if not data.get("has_more"):
            return chats
        page_token = str(data.get("page_token") or "")
        if not page_token:
            return chats


def load_relay_registry(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "llm_intern_logs" / "_daemon" / "relay_registry.json"
    if not path.exists():
        return {}
    data = read_json(path)
    result: dict[str, dict[str, Any]] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            result[str(key)] = dict(value)
    return result


def split_values(value: Any) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value or "").replace(",", ";").split(";")
    return [str(item).strip() for item in raw if str(item).strip()]


def relay_keys_for(item: dict[str, Any]) -> list[str]:
    keys = split_values(item.get("relay_keys"))
    if keys:
        return keys
    project = str(item.get("project") or "").strip()
    names = split_values(item.get("intern_names"))
    if project:
        return [f"{project}:{name}" for name in names]
    return []


def classify_item(
    item: dict[str, Any],
    visible_chats: dict[str, dict[str, Any]],
    relay_registry: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    old_chat_id = str(item.get("old_chat_id") or "").strip()
    relay_keys = relay_keys_for(item)
    relay_candidates = []
    for key in relay_keys:
        entry = relay_registry.get(key)
        if not isinstance(entry, dict):
            continue
        chat_id = str(entry.get("chat_id") or "")
        if chat_id:
            relay_candidates.append({"key": key, "chat_id": chat_id, "entry": entry})

    new_chat_id = str(item.get("new_chat_id") or "").strip()
    visible_candidate = ""
    visible_source = ""
    if new_chat_id and new_chat_id in visible_chats:
        visible_candidate = new_chat_id
        visible_source = "incident_new_chat_id"
    for candidate in relay_candidates:
        chat_id = candidate["chat_id"]
        if chat_id != old_chat_id and chat_id in visible_chats:
            visible_candidate = chat_id
            visible_source = f"relay:{candidate['key']}"
            break

    old_visible = old_chat_id in visible_chats
    relay_points_old = any(candidate["chat_id"] == old_chat_id for candidate in relay_candidates)
    relay_points_new = any(candidate["chat_id"] != old_chat_id for candidate in relay_candidates)

    if visible_candidate:
        status = "restored"
    elif old_visible:
        status = "old_visible"
    elif relay_points_new:
        status = "new_not_visible"
    elif relay_points_old:
        status = "missing_relay_old"
    elif relay_candidates:
        status = "missing_relay_unknown"
    else:
        status = "missing_no_relay_entry"

    return {
        "status": status,
        "old_chat_id": old_chat_id,
        "new_chat_id": new_chat_id,
        "visible_chat_id": visible_candidate,
        "visible_source": visible_source,
        "relay_keys": relay_keys,
        "relay_candidates": relay_candidates,
        "project": item.get("project") or "",
        "type": item.get("type") or "",
        "intern_names": item.get("intern_names") or [],
        "name": item.get("name") or "",
    }


def build_report(root: Path) -> dict[str, Any]:
    repair = load_repair_module()
    allowlist, allowlist_name = repair.load_default_incident_allowlist()
    raw_items = repair.DEFAULT_INCIDENT_REPORT.get("items") or []
    app_id, app_secret = repair.read_key_txt(root)
    api = repair.FeishuAPI(app_id, app_secret)
    visible_chats = list_visible_chats(api)
    relay_registry = load_relay_registry(root)

    items = []
    counts: dict[str, int] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        old_chat_id = str(raw.get("old_chat_id") or raw.get("chat_id") or "").strip()
        item = dict(raw)
        item.update(allowlist.get(old_chat_id, {}))
        item["relay_keys"] = raw.get("relay_keys") or item.get("relay_keys") or ""
        result = classify_item(item, visible_chats, relay_registry)
        items.append(result)
        counts[result["status"]] = counts.get(result["status"], 0) + 1

    total = len(items)
    restored = counts.get("restored", 0)
    old_visible = counts.get("old_visible", 0)
    available = restored + old_visible
    remaining = total - available
    return {
        "generated_at": now_iso(),
        "root": str(root),
        "allowlist": allowlist_name,
        "total": total,
        "restored": restored,
        "old_visible": old_visible,
        "available": available,
        "remaining": remaining,
        "visible_chat_count": len(visible_chats),
        "relay_registry_count": len(relay_registry),
        "counts": dict(sorted(counts.items())),
        "items": sorted(items, key=lambda item: (item["status"], str(item.get("project")), ",".join(item.get("intern_names") or []), item["old_chat_id"])),
    }


def write_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    latest_json = output_dir / "latest.json"
    latest_md = output_dir / "latest.md"
    history_jsonl = output_dir / "history.jsonl"
    latest_json.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with history_jsonl.open("a", encoding="utf-8") as f:
        f.write(json.dumps({
            "generated_at": report["generated_at"],
            "total": report["total"],
            "restored": report["restored"],
            "old_visible": report["old_visible"],
            "available": report["available"],
            "remaining": report["remaining"],
            "counts": report["counts"],
        }, ensure_ascii=False) + "\n")
    latest_md.write_text(render_markdown(report), encoding="utf-8")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Feishu Group Recovery Monitor",
        "",
        f"- generated_at: `{report['generated_at']}`",
        f"- allowlist: `{report['allowlist']}`",
        f"- total affected: `{report['total']}`",
        f"- restored replacement groups: `{report['restored']}`",
        f"- old groups visible again: `{report['old_visible']}`",
        f"- available now: `{report['available']}`",
        f"- remaining: `{report['remaining']}`",
        "",
        "## Status Counts",
        "",
    ]
    for key, value in report["counts"].items():
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Remaining Samples", ""])
    remaining = [item for item in report["items"] if item["status"] not in {"restored", "old_visible"}]
    for item in remaining[:80]:
        names = ",".join(item.get("intern_names") or [])
        keys = ",".join(item.get("relay_keys") or [])
        lines.append(f"- `{item['status']}` `{item['project']}` `{names}` old=`{item['old_chat_id']}` relay=`{keys}`")
    if len(remaining) > 80:
        lines.append(f"- ... {len(remaining) - 80} more")
    lines.append("")
    return "\n".join(lines)


def print_summary(report: dict[str, Any], output_dir: Path) -> None:
    print(
        "Feishu group recovery: "
        f"available={report['available']}/{report['total']} "
        f"restored={report['restored']} old_visible={report['old_visible']} "
        f"remaining={report['remaining']} output={output_dir}"
    )
    print("counts:", json.dumps(report["counts"], ensure_ascii=False, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monitor Feishu group recovery progress.")
    parser.add_argument("--root", default=os.environ.get("WORK_AGENTS_ROOT", str(WORK_AGENTS_ROOT)))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--watch", action="store_true", help="Run periodically until interrupted.")
    parser.add_argument("--interval", type=int, default=300, help="Seconds between checks in --watch mode.")
    parser.add_argument("--iterations", type=int, default=0, help="Stop after N checks in --watch mode; 0 means forever.")
    args = parser.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    output_dir = (
        Path(args.output_dir).expanduser().resolve()
        if args.output_dir
        else system_log_dir(root, "relay", script_path=__file__, component_version="1.0.0") / "group_recovery_monitor"
    )
    count = 0
    while True:
        report = build_report(root)
        write_report(report, output_dir)
        print_summary(report, output_dir)
        count += 1
        if not args.watch or (args.iterations and count >= args.iterations):
            return 0
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
