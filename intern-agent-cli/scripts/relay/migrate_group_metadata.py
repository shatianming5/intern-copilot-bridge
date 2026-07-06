#!/usr/bin/env python3
"""task204 批量脚本：刷新飞书群名（带类型 emoji）+ 重置群头像到飞书默认。

用法示例：
  # dry-run：只打印将要做的改动
  python3 migrate_group_metadata.py --root /work-agents \\
      --refresh-names --reset-avatar --dry-run

  # 实跑：刷群名 + 重置头像
  python3 migrate_group_metadata.py --root /work-agents \\
      --refresh-names --reset-avatar --apply

  # 只刷群名（比如 task204 部署后同步 emoji），不动头像
  python3 migrate_group_metadata.py --root /work-agents \\
      --refresh-names --apply

  # 只重置头像，群名让 relay 自己刷
  python3 migrate_group_metadata.py --root /work-agents \\
      --reset-avatar --apply

  # 过滤
  --only-type claude|codex|copilot   只处理指定类型
  --only-project <project>           只处理指定项目

根目录要求：
  <root>/enterprise_policy/relay/policy.json   Feishu app_id
  <root>/enterprise_policy/relay/secrets.json  Feishu app_secret
  <root>/llm_intern_logs/_daemon/relay_registry.json  入群清单

群名格式（与 feishu_relay.py:_build_group_name 同源）：
  `{🟢/🔴} {[🤖/🚀/]}{stripped}/{project}`
  - 🟢/🔴 online 状态（在线信息从 relay /api/online 拉；无法连 relay 时保守 🔴）
  - 🤖 claude / 🚀 codex / (空) copilot
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
from lib.enterprise_paths import relay_policy_path, relay_secrets_path
from lib.enterprise_policy import load_enterprise_policy, load_enterprise_secrets, resolve_secret_value

BASE_URL = "https://open.feishu.cn/open-apis"
SUPPORTED_TYPES = ("claude", "codex", "copilot")

# 与 feishu_relay.py / feishu_daemon.py 同源
_TYPE_EMOJI = {"claude": "🤖 ", "codex": "🚀 ", "copilot": ""}


class FeishuAPI:
    def __init__(self, app_id, app_secret):
        self.app_id = app_id
        self.app_secret = app_secret
        self._token = None
        self._token_expires = 0

    def _get_token(self):
        now = time.time()
        if self._token and now < self._token_expires - 300:
            return self._token
        payload = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode()
        req = urllib.request.Request(
            f"{BASE_URL}/auth/v3/tenant_access_token/internal",
            data=payload, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read())
        if result.get("code") != 0:
            raise RuntimeError(f"get_token failed: {result}")
        self._token = result["tenant_access_token"]
        self._token_expires = now + result.get("expire", 7200)
        return self._token

    def _request(self, method, path, body=None):
        token = self._get_token()
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            f"{BASE_URL}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=15)
            result = json.loads(resp.read())
            if result.get("code") == 0:
                return result.get("data"), None
            return None, f"code={result.get('code')}, msg={result.get('msg')}"
        except urllib.error.HTTPError as e:
            return None, f"HTTP {e.code}: {e.read().decode()[:200]}"
        except Exception as e:
            return None, str(e)

    def update_chat(self, chat_id, name=None, avatar=None):
        body = {}
        if name is not None:
            body["name"] = name
        if avatar is not None:
            body["avatar"] = avatar
        if not body:
            return None
        _, err = self._request("PUT", f"/im/v1/chats/{chat_id}", body)
        return err


def strip_prefix(name):
    return name[len("intern_"):] if name.startswith("intern_") else name


def new_group_name(intern_name, intern_type, project, is_online):
    prefix = "🟢" if is_online else "🔴"
    badge = _TYPE_EMOJI.get(intern_type or "copilot", "")
    return f"{prefix} {badge}{strip_prefix(intern_name)}/{project}"


def load_feishu_credentials(root):
    policy_result = load_enterprise_policy(relay_policy_path(root))
    if not policy_result.ok:
        raise RuntimeError(policy_result.error or f"读取 relay policy 失败: {policy_result.path}")
    secrets_result = load_enterprise_secrets(relay_secrets_path(root), required=True)
    if not secrets_result.ok:
        raise RuntimeError(secrets_result.error or f"读取 relay secrets 失败: {secrets_result.path}")
    feishu = policy_result.data.get("feishu") if isinstance(policy_result.data.get("feishu"), dict) else {}
    app_id = str(feishu.get("app_id") or "").strip()
    app_secret = resolve_secret_value((secrets_result.data.get("secrets") or {}).get("feishu.app_secret") or {})
    if not app_id or not app_secret:
        raise RuntimeError("relay policy/secrets 缺少 feishu.app_id 或 feishu.app_secret")
    return app_id, app_secret


def load_registry(root):
    path = os.path.join(root, "llm_intern_logs", "_daemon", "relay_registry.json")
    with open(path) as f:
        return json.load(f)


def fetch_online_from_relay(relay_url):
    """Query relay /api/online to get currently-online composite keys.

    Returns set of (project, intern_name) tuples, or None on error.
    """
    try:
        with urllib.request.urlopen(f"{relay_url}/api/online", timeout=5) as r:
            data = json.loads(r.read())
        online = set()
        for k in data.keys():
            if ":" in k:
                project, name = k.split(":", 1)
                online.add((project, name))
        return online
    except Exception as e:
        print(f"[WARN] fetch_online_from_relay failed: {e} (defaulting to all 🔴)")
        return None


def main():
    p = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter,
                                description=__doc__)
    p.add_argument("--root", required=True, help="WORK_AGENTS_ROOT")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="仅打印，不改动")
    mode.add_argument("--apply", action="store_true", help="实际调用 PUT chats")
    # 操作开关（可组合；必须至少选一个）
    p.add_argument("--refresh-names", action="store_true",
                   help="按新格式刷群名（带类型 emoji + 实时 online 状态）")
    p.add_argument("--reset-avatar", action="store_true",
                   help="PUT avatar='' 重置群头像为飞书默认")
    # 过滤
    p.add_argument("--only-type", choices=list(SUPPORTED_TYPES), help="仅处理此类型")
    p.add_argument("--only-project", help="仅处理此 project")
    p.add_argument("--only-intern", action="append", default=[],
                   help="仅处理指定 intern_name（可多次传入）")
    p.add_argument("--sleep-ms", type=int, default=300,
                   help="每次 PUT 之间 sleep 毫秒数，默认 300")
    p.add_argument("--relay-url", default="http://localhost:28080",
                   help="relay HTTP url，用于查询实时 online 状态（refresh-names 用）")
    args = p.parse_args()

    # 至少选一个操作
    if not (args.refresh_names or args.reset_avatar):
        p.error("必须至少选一个操作：--refresh-names / --reset-avatar")

    app_id, app_secret = load_feishu_credentials(args.root)
    api = FeishuAPI(app_id, app_secret)
    mode_label = "dry-run" if args.dry_run else "apply"
    ops = []
    if args.refresh_names:
        ops.append("refresh-names")
    if args.reset_avatar:
        ops.append("reset-avatar")
    print(f"[INFO] app_id={app_id[:8]}...  root={args.root}  mode={mode_label}  ops={'+'.join(ops)}")

    # 查 online 状态（refresh-names 才需要）
    online_set = None
    if args.refresh_names:
        online_set = fetch_online_from_relay(args.relay_url)
        print(f"[ONLINE] from relay: {len(online_set) if online_set is not None else 'N/A'} entries")

    registry = load_registry(args.root)
    print(f"[INFO] 总 entry 数：{len(registry)}")

    # Enterprise registry should not have duplicate chat_id entries.
    from collections import defaultdict
    by_chat = defaultdict(list)
    for k, v in registry.items():
        cid = v.get("chat_id")
        if cid:
            by_chat[cid].append((k, v))
    dups = [(cid, entries) for cid, entries in by_chat.items() if len(entries) > 1]
    if dups:
        print("[ERROR] registry 有重复 chat_id entry：")
        for cid, entries in dups:
            print(f"  chat={cid}")
            for k, v in entries:
                print(f"    {k!r} project={v.get('project')}")
        raise SystemExit(
            "registry 存在 stale 重复 entry，先跑 cleanup_stale_registry_entries.py --apply 再来"
        )

    stats = {"total": 0, "skipped": 0, "updated": 0, "failed": 0, "filtered": 0}
    for key, entry in sorted(registry.items()):
        intern_name = entry.get("name") or key.split(":", 1)[-1]
        project = entry.get("project", "")
        intern_type = entry.get("type", "copilot")
        chat_id = entry.get("chat_id")
        if not chat_id:
            stats["skipped"] += 1
            continue
        if args.only_type and intern_type != args.only_type:
            stats["filtered"] += 1
            continue
        if args.only_project and project != args.only_project:
            stats["filtered"] += 1
            continue
        if args.only_intern and intern_name not in args.only_intern:
            stats["filtered"] += 1
            continue

        is_online = False
        if online_set is not None:
            if (project, intern_name) in online_set:
                is_online = True

        # 构造 PUT 负载
        put_name = None
        put_avatar = None
        if args.refresh_names:
            put_name = new_group_name(intern_name, intern_type, project, is_online)
        if args.reset_avatar:
            put_avatar = ""  # 飞书接受空字符串重置为默认

        stats["total"] += 1
        light = "🟢" if is_online else "🔴"
        avatar_note = ""
        if args.reset_avatar:
            avatar_note = "  avatar→<default>"
        name_note = f"  name→{put_name}" if put_name else ""
        print(f"[PLAN] {chat_id}  {light} type={intern_type:8s}{name_note}{avatar_note}")
        if args.apply:
            err = api.update_chat(chat_id, name=put_name, avatar=put_avatar)
            if err:
                print(f"        ✗ failed: {err}")
                stats["failed"] += 1
            else:
                print(f"        ✓ updated")
                stats["updated"] += 1
            time.sleep(args.sleep_ms / 1000.0)

    print()
    print(f"[SUMMARY] total_planned={stats['total']} "
          f"updated={stats['updated']} failed={stats['failed']} "
          f"skipped={stats['skipped']} filtered_out={stats['filtered']}")
    return 0 if stats["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
