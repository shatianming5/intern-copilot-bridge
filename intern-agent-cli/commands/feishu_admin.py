"""intern-adminctl feishu — administrator-only Feishu diagnostics."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

from lib.enterprise_paths import relay_policy_path, relay_secrets_path
from lib.enterprise_policy import load_enterprise_policy, load_enterprise_secrets, resolve_secret_value


REPORT_SCHEMA = "intern-agents.feishu-admin-doctor.v1"
TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
APPLICATION_INFO_URL = "https://open.feishu.cn/open-apis/application/v6/applications/{app_id}"
APP_INFO_SCOPES = ["admin:app.info:readonly", "application:application:self_manage"]
RUNTIME_EVENT_NAMES = ["im.message.receive_v1", "card.action.trigger"]


def _app_scope_grant_url(app_id: str) -> str:
    query = urllib.parse.urlencode({
        "q": ",".join(APP_INFO_SCOPES),
        "op_from": "openapi",
        "token_type": "tenant",
    })
    return f"https://open.feishu.cn/app/{urllib.parse.quote(app_id, safe='')}/auth?{query}"


def _app_event_config_url(app_id: str) -> str:
    return f"https://open.feishu.cn/app/{urllib.parse.quote(app_id, safe='')}/event"


def _app_console_url(app_id: str) -> str:
    return f"https://open.feishu.cn/app/{urllib.parse.quote(app_id, safe='')}" if app_id else ""


def setup_parser(subparsers):
    p = subparsers.add_parser("feishu", help="Diagnose administrator-managed Feishu app configuration")
    sub = p.add_subparsers(dest="feishu_command", help="Feishu sub-commands")

    doctor = sub.add_parser("doctor", help="Check Feishu app credentials and introspection permissions")
    doctor.add_argument("--json", action="store_true", help="Output JSON")
    doctor.set_defaults(func=run)

    p.set_defaults(func=run)


def _work_root() -> Path:
    return Path(os.environ.get("WORK_AGENTS_ROOT") or os.getcwd())


def _check(check_id, status, passed, code, message, hint="", details=None):
    return {
        "id": check_id,
        "status": status,
        "passed": bool(passed),
        "code": code,
        "message": message,
        "hint": hint,
        "details": details or {},
    }


def _post_json(url, payload, *, headers=None, timeout=10):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _get_json(url, *, headers=None, timeout=10):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _http_error_payload(exc: urllib.error.HTTPError) -> dict:
    try:
        return json.loads(exc.read().decode("utf-8") or "{}")
    except Exception:
        return {"code": getattr(exc, "code", ""), "msg": str(exc)}


def build_report(*, work_root: Path | None = None, urlopen=None) -> dict:
    if urlopen is not None:
        urllib.request.urlopen = urlopen
    root = work_root or _work_root()
    policy = load_enterprise_policy(relay_policy_path(root))
    secrets = load_enterprise_secrets(relay_secrets_path(root), required=True)
    checks = []

    app_id = ""
    if policy.ok:
        app_id = str((policy.data.get("feishu") or {}).get("app_id") or "")
    checks.append(_check(
        "policy.feishu_app_id",
        "ok" if app_id else "missing",
        bool(app_id),
        "OK" if app_id else "FEISHU_APP_ID_MISSING",
        "Feishu app_id is present in enterprise policy" if app_id else "Feishu app_id is missing from enterprise policy",
        "Set feishu.app_id in enterprise policy." if not app_id else "",
    ))

    secret = ""
    if secrets.ok:
        entry = (secrets.data.get("secrets") or {}).get("feishu.app_secret")
        if isinstance(entry, dict):
            secret = resolve_secret_value(entry)
    checks.append(_check(
        "secrets.feishu_app_secret",
        "ok" if secret else "missing",
        bool(secret),
        "OK" if secret else "FEISHU_APP_SECRET_MISSING",
        "Feishu app secret is present in enterprise secret bundle" if secret else "Feishu app secret is missing",
        "Set feishu.app_secret in enterprise secrets with 0600 permissions." if not secret else "",
        {"secret_key": "feishu.app_secret", "redacted": True},
    ))

    token = ""
    if app_id and secret:
        try:
            token_resp = _post_json(TOKEN_URL, {"app_id": app_id, "app_secret": secret})
            token = str(token_resp.get("tenant_access_token") or "")
            ok = int(token_resp.get("code") or 0) == 0 and bool(token)
            checks.append(_check(
                "feishu.tenant_access_token",
                "ok" if ok else "failed",
                ok,
                "OK" if ok else f"FEISHU_TOKEN_ERROR_{token_resp.get('code')}",
                "tenant_access_token acquired" if ok else str(token_resp.get("msg") or "failed to acquire token"),
                "Check app_id/app_secret." if not ok else "",
            ))
        except Exception as exc:
            checks.append(_check(
                "feishu.tenant_access_token",
                "failed",
                False,
                "FEISHU_TOKEN_REQUEST_FAILED",
                f"tenant token request failed: {exc}",
                "Check network and app credentials.",
            ))

    if token and app_id:
        url = APPLICATION_INFO_URL.format(app_id=urllib.parse.quote(app_id, safe=""))
        url += "?lang=zh_cn&user_id_type=open_id"
        try:
            data = _get_json(url, headers={"Authorization": f"Bearer {token}"})
            ok = int(data.get("code") or 0) == 0
            error = data.get("error") if isinstance(data.get("error"), dict) else {}
            violations = error.get("permission_violations") if isinstance(error, dict) else []
            checks.append(_check(
                "feishu.app_introspection",
                "ok" if ok else ("permission_denied" if data.get("code") == 99991672 else "failed"),
                ok,
                "OK" if ok else f"FEISHU_APP_INTROSPECTION_ERROR_{data.get('code')}",
                "application metadata is readable" if ok else str(data.get("msg") or "application metadata is not readable"),
                "" if ok else f"Grant one of: {', '.join(APP_INFO_SCOPES)}.",
                {
                    "required_any_scope": APP_INFO_SCOPES,
                    "permission_violations": violations or [],
                    "grant_url": _app_scope_grant_url(app_id),
                },
            ))
        except urllib.error.HTTPError as exc:
            payload = _http_error_payload(exc)
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            violations = error.get("permission_violations") if isinstance(error, dict) else []
            checks.append(_check(
                "feishu.app_introspection",
                "permission_denied" if payload.get("code") == 99991672 else "failed",
                False,
                f"FEISHU_APP_INTROSPECTION_ERROR_{payload.get('code')}",
                str(payload.get("msg") or exc),
                f"Grant one of: {', '.join(APP_INFO_SCOPES)}.",
                {
                    "required_any_scope": APP_INFO_SCOPES,
                    "permission_violations": violations or [],
                    "grant_url": _app_scope_grant_url(app_id),
                },
            ))
        except Exception as exc:
            checks.append(_check(
                "feishu.app_introspection",
                "failed",
                False,
                "FEISHU_APP_INTROSPECTION_REQUEST_FAILED",
                f"application metadata request failed: {exc}",
                "Check network and app credentials.",
                {"required_any_scope": APP_INFO_SCOPES, "grant_url": _app_scope_grant_url(app_id)},
            ))

    ready = all(check["passed"] for check in checks)
    return {
        "schema": REPORT_SCHEMA,
        "ready": ready,
        "work_root": str(root),
        "policy": {"state": policy.state, "path": policy.path, "error": policy.error},
        "secrets": {"state": secrets.state, "path": secrets.path, "error": secrets.error, "redacted": True},
        "app_id": app_id,
        "runtime_event_requirements": {
            "required_events": RUNTIME_EVENT_NAMES,
            "connection": "long_connection",
            "event_config_url": _app_event_config_url(app_id) if app_id else "",
            "developer_console_url": _app_console_url(app_id) if app_id else "",
            "requires_developer_console_access": True,
            "credentials_note": (
                "App ID/App Secret can authenticate runtime OpenAPI and WebSocket clients, "
                "but they cannot configure developer-console event subscriptions by themselves."
            ),
        },
        "checks": checks,
        "next_actions": _next_actions(checks, app_id),
    }


def _next_actions(checks, app_id: str = ""):
    actions = []
    by_id = {check["id"]: check for check in checks}
    if not by_id.get("feishu.tenant_access_token", {}).get("passed", False):
        actions.append("Verify enterprise policy app_id and secret bundle feishu.app_secret.")
    app_check = by_id.get("feishu.app_introspection")
    if app_check and not app_check.get("passed"):
        actions.append(
            "Grant admin:app.info:readonly or application:application:self_manage if you want admin CLI to verify app configuration."
        )
    event_url = _app_event_config_url(app_id) if app_id else "the Feishu Open Platform event subscription page"
    console_url = _app_console_url(app_id) if app_id else "the Feishu Open Platform developer console"
    actions.append(
        "For runtime inbound, configure long-connection events "
        f"{', '.join(RUNTIME_EVENT_NAMES)} at {event_url}, publish/approve the app, "
        "then send a real bot message and rerun setup doctor."
    )
    actions.append(
        f"Use a Feishu Open Platform account with developer access to this app ({console_url}); "
        "App ID/App Secret alone are not enough to change event subscriptions."
    )
    return actions


def _print_human(report):
    print(f"Feishu admin doctor: {'ready' if report['ready'] else 'not ready'}")
    for check in report["checks"]:
        marker = "OK" if check["passed"] else "FAIL"
        print(f"  [{marker}] {check['id']}: {check['code']} - {check['message']}")
        if check.get("hint"):
            print(f"        hint: {check['hint']}")
    if report.get("next_actions"):
        print("Next actions:")
        for action in report["next_actions"]:
            print(f"  - {action}")


def run(args: argparse.Namespace, **_kwargs) -> int:
    if getattr(args, "feishu_command", None) != "doctor":
        print("Usage: intern-adminctl feishu doctor [--json]")
        return 1
    report = build_report()
    if getattr(args, "json", False):
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    return 0 if report["ready"] else 1
