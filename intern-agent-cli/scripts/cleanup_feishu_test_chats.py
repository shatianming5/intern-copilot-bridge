#!/usr/bin/env python3
"""Clean visible Feishu chats for a dedicated CI test app."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


BASE_URL = "https://open.feishu.cn/open-apis"
SCHEMA = "intern-agents.feishu-test-chat-cleanup.v1"


class FeishuAPIError(RuntimeError):
    pass


class FeishuAPI:
    def __init__(self, app_id: str, app_secret: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self._tenant_token = ""

    def tenant_token(self) -> str:
        if self._tenant_token:
            return self._tenant_token
        body = json.dumps({"app_id": self.app_id, "app_secret": self.app_secret}).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE_URL}/auth/v3/tenant_access_token/internal",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8") or "{}")
        if data.get("code") not in (0, None):
            raise FeishuAPIError(f"tenant token failed: {data}")
        token = str(data.get("tenant_access_token") or "")
        if not token:
            raise FeishuAPIError("tenant token response missing tenant_access_token")
        self._tenant_token = token
        return token

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE_URL}{path}",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.tenant_token()}",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            raise FeishuAPIError(f"{method} {path} HTTP {exc.code}: {raw[:500]}") from exc
        if data.get("code") not in (0, None):
            raise FeishuAPIError(f"{method} {path} failed: {data}")
        value = data.get("data")
        return value if isinstance(value, dict) else data

    def list_chats(self) -> list[dict[str, Any]]:
        chats: list[dict[str, Any]] = []
        page_token = ""
        while True:
            path = "/im/v1/chats?page_size=100"
            if page_token:
                path += f"&page_token={urllib.parse.quote(page_token, safe='')}"
            data = self.request("GET", path)
            for item in data.get("items") or []:
                if isinstance(item, dict) and item.get("chat_id"):
                    chats.append(dict(item))
            if not data.get("has_more"):
                return chats
            page_token = str(data.get("page_token") or "")
            if not page_token:
                return chats

    def delete_chat(self, chat_id: str) -> None:
        self.request("DELETE", f"/im/v1/chats/{urllib.parse.quote(chat_id, safe='')}")


def run(args: argparse.Namespace) -> dict[str, Any]:
    app_id = args.app_id.strip()
    app_secret = args.app_secret.strip()
    confirm_app_id = args.confirm_app_id.strip()
    if not app_id or not app_secret:
        raise ValueError("--app-id and --app-secret are required")
    if not confirm_app_id:
        raise ValueError("--confirm-app-id is required")
    if confirm_app_id != app_id:
        raise ValueError(f"refusing to clean app {app_id}: confirm app id is {confirm_app_id}")

    api = FeishuAPI(app_id, app_secret)
    chats = api.list_chats()
    deleted = []
    errors = []
    for chat in chats:
        chat_id = str(chat.get("chat_id") or "")
        if not chat_id:
            continue
        item = {"chat_id": chat_id, "name": str(chat.get("name") or "")}
        if args.apply:
            try:
                api.delete_chat(chat_id)
                item["ok"] = True
            except Exception as exc:  # noqa: BLE001
                item["ok"] = False
                item["error"] = str(exc)
                errors.append(item)
            else:
                deleted.append(item)
        else:
            item["ok"] = True
            deleted.append(item)
    return {
        "schema": SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app_id": app_id,
        "apply": bool(args.apply),
        "visible_before": len(chats),
        "deleted": len(deleted) if args.apply else 0,
        "would_delete": len(deleted) if not args.apply else 0,
        "errors": errors,
        "items": deleted + errors,
        "ok": not errors,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--app-secret", required=True)
    parser.add_argument("--confirm-app-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Actually delete chats. Without this, only reports what would be deleted.")
    parser.add_argument("--report", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = run(args)
    if args.report:
        path = Path(args.report).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.json or not report.get("ok"):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        verb = "deleted" if args.apply else "would delete"
        print(f"Feishu test chat cleanup ok: {verb} {report['deleted'] or report['would_delete']} chats")
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
