#!/usr/bin/env python3
"""fix_chat_persist_last_group_name.py — task242 一次性部署 fix。

新代码版 feishu_relay 在 _chat_persist[ckey] 上新增 last_group_name 字段做 dedupe：
_update_group_light_for_chat 调 api.update_chat 前比较 desired new_name 与
last_group_name，相等则跳过。relay 重启首轮所有 sync_online 上报的 intern desired
都是 GREEN，没有 last_group_name 字段时无法 dedupe → 重启写风暴 → 飞书 request
trigger frequency limit。

本脚本在 relay 停止状态下跑一次：调飞书 list_chats 拿当前所有群名快照，按 chat_id
匹配 _chat_persist 条目，把飞书侧当前真名写进 last_group_name 字段。脚本只读
（list_chats GET 请求 + 单文件 JSON 重写），不调 update_chat，不会触发限频。

用法：
    python3 fix_chat_persist_last_group_name.py --root /path/to/work-agents [--dry-run]

precondition：feishu_relay 已停（否则 relay 可能并发改写 relay_registry.json）。
postcondition：relay_registry.json 中每个 chat_id 在飞书侧存在的条目都补上
last_group_name，下一次 relay 起来后首轮 sync_online 命中 dedupe，零写请求。
"""
import argparse
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fix_last_group_name")


def _load_feishu_api():
    """Dynamically import FeishuAPI from sibling feishu_relay.py (script lives in
    scripts/relay/, not a package)."""
    relay_path = Path(__file__).resolve().parent / "feishu_relay.py"
    spec = importlib.util.spec_from_file_location("feishu_relay", relay_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["feishu_relay"] = module
    spec.loader.exec_module(module)
    return module.FeishuAPI, module.load_config


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", required=True,
                        help="WORK_AGENTS_ROOT (含 enterprise_policy/relay/_owner.json)")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印拟更新的条目，不写盘")
    args = parser.parse_args()

    registry_path = os.path.join(args.root, "llm_intern_logs", "_daemon", "relay_registry.json")
    if not os.path.exists(registry_path):
        log.error(f"registry not found: {registry_path}")
        sys.exit(1)

    FeishuAPI, load_config = _load_feishu_api()
    cfg = load_config(args.root)
    api = FeishuAPI(cfg["app_id"], cfg["app_secret"])

    log.info(f"loading registry from {registry_path}")
    with open(registry_path) as f:
        registry = json.load(f)
    log.info(f"loaded {len(registry)} chat_persist entries")

    log.info("calling feishu list_chats to fetch authoritative names ...")
    chats = api.list_chats()
    chat_by_id = {c["chat_id"]: c["name"] for c in chats if c.get("chat_id")}
    log.info(f"feishu returned {len(chat_by_id)} chats")

    updated = 0
    unchanged = 0
    no_match = 0
    no_chat_id = 0
    for ckey, entry in registry.items():
        if not isinstance(entry, dict):
            continue
        chat_id = entry.get("chat_id")
        if not chat_id:
            no_chat_id += 1
            continue
        feishu_name = chat_by_id.get(chat_id)
        if not feishu_name:
            # 飞书侧已删 / list_chats 漏了（理论不会）→ 不动该条目，下次 _update_group_light
            # 自然会调一次 update_chat 重建 last_group_name（或失败暴露 chat 不存在）。
            no_match += 1
            log.warning(f"  {ckey}: chat_id={chat_id} not in feishu list_chats; "
                        "leaving last_group_name unset (will be set on first authoritative update)")
            continue
        old_last = entry.get("last_group_name")
        if old_last == feishu_name:
            unchanged += 1
            continue
        log.info(f"  {ckey}: {old_last!r} → {feishu_name!r}")
        entry["last_group_name"] = feishu_name
        updated += 1

    log.info(f"summary: updated={updated} unchanged={unchanged} "
             f"no_match_in_feishu={no_match} no_chat_id={no_chat_id} "
             f"total={len(registry)}")

    if args.dry_run:
        log.info("dry-run: not writing to disk")
        return

    if updated == 0:
        log.info("nothing to update; skipping write")
        return

    tmp = registry_path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    os.replace(tmp, registry_path)
    log.info(f"wrote {registry_path}")


if __name__ == "__main__":
    main()
