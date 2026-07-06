#!/usr/bin/env python3
"""task204 一次性数据清理：去除 relay_registry.json 里由 DEFAULT_PROJECT fallback
导致的 52 个 stale 重复 entry。

背景：
  feishu_relay.py 的老代码在 `_make_composite_key(name, project)` 缺 project 时
  会 fallback 到 `axis_intern_agents:<name>`，于是"新 daemon 传真实 project" 和
  "旧 daemon 不传 project 走 fallback" 两条路径并存时，会出现同一 chat_id 挂两
  个 composite key：`axis_intern_agents:name` + `<real_project>:name`。

做法：
  扫 relay_registry.json，按 chat_id 聚合；一组里出现 >=2 个 entry 时：
    1) 只有一个 entry 的 project != axis_intern_agents → 保留它，删其他（stale）
    2) 多个 entry 都不是 axis_intern_agents（罕见）→ log 不动，人工处理
    3) 多个 entry 都是 axis_intern_agents（同 key 不该发生）→ log

使用：
  # 先 dry-run：打印 diff 不写盘
  python3 intern-cli/scripts/relay/cleanup_stale_registry_entries.py --dry-run

  # 实际写盘（先备份到 .bak.YYYYmmdd-HHMMSS）
  python3 intern-cli/scripts/relay/cleanup_stale_registry_entries.py --apply

⚠️ 注意：
  - 写入前 relay 必须停机，否则 relay 用内存态覆写文件 ← 人工保证
  - 写入后启动 relay 才会读到清理后的数据
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

REGISTRY_PATH = os.path.join(
    os.environ.get("WORK_AGENTS_ROOT", "/work-agents"),
    "llm_intern_logs",
    "_daemon",
    "relay_registry.json",
)


def main():
    p = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                description=__doc__)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    p.add_argument("--registry", default=REGISTRY_PATH, help=f"path to relay_registry.json (default {REGISTRY_PATH})")
    args = p.parse_args()

    with open(args.registry) as f:
        registry = json.load(f)

    print(f"[INFO] loaded {len(registry)} entries from {args.registry}")

    # 按 chat_id 分组
    by_chat = defaultdict(list)
    entries_without_chat = []
    for k, v in registry.items():
        cid = v.get("chat_id")
        if not cid:
            entries_without_chat.append(k)
            continue
        by_chat[cid].append((k, v))

    print(f"[INFO] {sum(1 for entries in by_chat.values() if len(entries)>=2)} chat_id 有多个 entry；"
          f"{len(entries_without_chat)} entries 无 chat_id")

    to_delete = []
    ambiguous = []
    for cid, entries in by_chat.items():
        if len(entries) < 2:
            continue
        non_axis = [(k, v) for (k, v) in entries if v.get("project") != "axis_intern_agents"]
        if len(non_axis) == 1:
            # 保留非 axis_intern_agents 的那个；其他都删
            keep_k = non_axis[0][0]
            for k, v in entries:
                if k != keep_k:
                    to_delete.append((k, v, keep_k))
        else:
            ambiguous.append((cid, entries))

    print()
    print(f"[PLAN] 将删除 {len(to_delete)} 个 stale entry (由 DEFAULT_PROJECT fallback 制造)")
    for k, v, keep_k in to_delete:
        print(f"  [DELETE] {k!r} (type={v.get('type')}, project={v.get('project')})  "
              f"→ 保留 {keep_k!r}")
    if ambiguous:
        print()
        print(f"[AMBIGUOUS] {len(ambiguous)} chat_id 有多条 entry 但无明显 stale，不动：")
        for cid, entries in ambiguous:
            print(f"  chat={cid}")
            for k, v in entries:
                print(f"    {k!r} type={v.get('type')} project={v.get('project')}")

    if not to_delete:
        print("[DONE] 没有要清理的 stale entry")
        return 0

    if args.dry_run:
        print()
        print("[DRY-RUN] 无变更。用 --apply 实际写盘。")
        return 0

    # apply: 备份 + 写入
    ts = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{args.registry}.bak.{ts}"
    os.rename(args.registry, backup)
    print(f"[BACKUP] 原文件 → {backup}")

    for k, _, _ in to_delete:
        del registry[k]
    tmp = args.registry + ".tmp"
    with open(tmp, "w") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)
    os.replace(tmp, args.registry)
    print(f"[APPLIED] 写入 {args.registry}，剩余 {len(registry)} 个 entry")
    return 0


if __name__ == "__main__":
    sys.exit(main())
