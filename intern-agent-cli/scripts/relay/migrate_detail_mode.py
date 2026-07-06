#!/usr/bin/env python3
"""Migrate per-chat detail_mode from relay-local chat_config.json → daemon-local store.

task283 Session 5: detail_mode used to live in `~/.feishu_relay/chat_config.json`
on the relay host. From task283 onward the truth source moved to each owning
daemon's `$WORK_AGENTS_ROOT/enterprise_policy/daemon/chat_config.json` because the
hook hot-path that reads it also runs there. This script reads any remaining
detail_mode entries out of the relay-local file and pushes them to the right
daemon via the relay's POST /api/chat/detail_mode endpoint (which now proxies
through the relay→daemon WS RPC internally).

Usage:
    python3 migrate_detail_mode.py [--relay-base URL] [--config PATH] [--dry-run]

The script is idempotent — re-running after a partial migration leaves
already-migrated chats alone (their detail_mode field was removed from the
relay file on first success) and only retries the ones that previously failed.
Supervisor can keep running it until skipped=0.

Behavior on per-chat failure:
  - chat_id not in relay /api/chat/list  → log warn, leave field in place
  - daemon offline / outdated            → log warn, leave field in place
  - any other HTTP error                 → log warn, leave field in place
Successful migrations clear the detail_mode field from the relay file (and
delete the whole chat_id entry if no other fields remain) so the relay file
converges to a trigger_mode-only schema.
"""

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request


def _http_get(base, path, timeout=15):
    req = urllib.request.Request(base.rstrip("/") + path)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read())


def _http_post(base, path, body, timeout=15):
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace") if hasattr(e, "read") else str(e)
        try:
            body_json = json.loads(raw)
        except ValueError:
            body_json = {"error": raw}
        return e.code, body_json


def _read_chat_config(path):
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_chat_config_atomic(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _build_chat_reverse_index(base, log):
    """Build {chat_id: (intern_name, project)} from relay /api/chat/list.

    Cross-project name collision quirk: the list endpoint is keyed by name
    (back-compat), so if `intern_test` exists in two projects only the last
    one wins under that key. Since we reverse by chat_id (always unique per
    Feishu group) the resulting map is still 1-to-1 for any chat the list
    endpoint exposed — but a chat owned by an intern hidden by name collision
    won't appear and that chat will be skipped + reported.
    """
    code, data = _http_get(base, "/api/chat/list")
    if code != 200 or not isinstance(data, dict):
        raise RuntimeError(f"/api/chat/list returned HTTP {code}: {data!r}")
    reverse = {}
    for name, info in data.items():
        chat_id = info.get("chat_id") or ""
        project = info.get("project") or ""
        if chat_id and project:
            reverse[chat_id] = (name, project)
    log.info(f"loaded {len(reverse)} chat→(intern, project) mappings from relay")
    return reverse


def _collect_pending(config):
    """Return list of (chat_id, mode) entries that still carry detail_mode."""
    pending = []
    for chat_id, entry in config.items():
        if not isinstance(entry, dict):
            continue
        mode = entry.get("detail_mode")
        if mode in ("full", "summary"):
            pending.append((chat_id, mode))
    return pending


def _drop_detail_mode(config, chat_id):
    """Remove detail_mode field after successful migration. Drop the chat
    entry entirely if no other fields remain so the file converges to a
    trigger_mode-only schema."""
    entry = config.get(chat_id)
    if not isinstance(entry, dict):
        return
    entry.pop("detail_mode", None)
    if not entry:
        del config[chat_id]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Migrate detail_mode from relay chat_config.json to daemon-local "
                    "via relay HTTP API (task283).")
    parser.add_argument("--relay-base", default="http://localhost:28080",
                        help="Relay HTTP base URL (default: %(default)s)")
    parser.add_argument("--config",
                        default=os.path.expanduser("~/.feishu_relay/chat_config.json"),
                        help="Path to relay chat_config.json (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print what would happen without modifying anything")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("migrate_detail_mode")

    config = _read_chat_config(args.config)
    if not isinstance(config, dict):
        log.error(f"unexpected schema in {args.config}: expected dict, got "
                  f"{type(config).__name__}")
        return 1

    pending = _collect_pending(config)
    if not pending:
        log.info(f"no detail_mode entries to migrate in {args.config}; nothing to do")
        return 0

    log.info(f"found {len(pending)} chat(s) with detail_mode in {args.config}")

    try:
        reverse = _build_chat_reverse_index(args.relay_base, log)
    except Exception as e:
        log.error(f"failed to load /api/chat/list from {args.relay_base}: {e}")
        return 1

    migrated, skipped = 0, 0
    for chat_id, mode in pending:
        target = reverse.get(chat_id)
        if not target:
            log.warning(f"chat={chat_id} mode={mode}: no matching intern in relay "
                        f"registry; skipping (run again after the daemon registers)")
            skipped += 1
            continue
        intern_name, project = target

        if args.dry_run:
            log.info(f"[DRY] would push chat={chat_id} → "
                     f"intern={intern_name} project={project} mode={mode}")
            continue

        log.info(f"pushing chat={chat_id} → intern={intern_name} project={project} mode={mode}")
        try:
            code, body = _http_post(args.relay_base, "/api/chat/detail_mode",
                                    {"intern_name": intern_name,
                                     "project": project, "mode": mode})
        except Exception as e:
            log.warning(f"chat={chat_id}: POST failed: {e}; keeping field for retry")
            skipped += 1
            continue

        if code != 200:
            err = body.get("error", body) if isinstance(body, dict) else body
            log.warning(f"chat={chat_id}: relay returned HTTP {code} {err}; "
                        f"keeping field for retry")
            skipped += 1
            continue

        log.info(f"chat={chat_id}: migrated (relay changed={body.get('changed')!r})")
        _drop_detail_mode(config, chat_id)
        migrated += 1

    if args.dry_run:
        log.info(f"[DRY] {len(pending) - skipped} would be pushed, "
                 f"{skipped} would be skipped; no files modified")
        return 0

    if migrated:
        _write_chat_config_atomic(args.config, config)
        log.info(f"wrote updated {args.config}: migrated={migrated}, skipped={skipped}")
    else:
        log.info(f"no successful migrations; nothing written. skipped={skipped}")

    return 0 if skipped == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
