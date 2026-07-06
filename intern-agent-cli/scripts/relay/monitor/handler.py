"""HTTP route dispatch for the monitor dashboard.

Delegated from the relay's ``MonitorHandler.do_GET`` so new paths can grow here
without expanding the main file. All routes are read-only.

Served paths:

* ``GET /monitor`` → dashboard HTML
* ``GET /monitor/`` → dashboard HTML
* ``GET /monitor/static/...`` → static asset
* ``GET /api/snapshot`` → aggregated JSON (contract V1 from intern-monitor-web)
"""
from __future__ import annotations

import json
import mimetypes
import os
from pathlib import Path

from .aggregator import build_snapshot

_STATIC_ROOT = Path(__file__).resolve().parent / "static"
_DASHBOARD_HTML = _STATIC_ROOT / "dashboard.html"


def try_handle_get(http_handler, registry, feishu_api=None):
    """Return True when the request was handled by the monitor module."""
    path = http_handler.path
    # Strip query string for routing
    route = path.split("?", 1)[0]

    if route == "/api/snapshot":
        _send_json(http_handler, 200, build_snapshot(registry, feishu_api=feishu_api))
        return True
    if route in ("/monitor", "/monitor/"):
        _send_file(http_handler, _DASHBOARD_HTML, "text/html; charset=utf-8")
        return True
    if route.startswith("/monitor/static/"):
        rel = route[len("/monitor/static/"):]
        target = (_STATIC_ROOT / rel).resolve()
        if not str(target).startswith(str(_STATIC_ROOT)) or not target.is_file():
            _send_json(http_handler, 404, {"error": "not found"})
            return True
        ctype, _ = mimetypes.guess_type(str(target))
        _send_file(http_handler, target, ctype or "application/octet-stream")
        return True
    return False


def _send_json(http_handler, code, payload):
    body = json.dumps(payload, ensure_ascii=False).encode()
    http_handler.send_response(code)
    http_handler.send_header("Content-Type", "application/json; charset=utf-8")
    http_handler.send_header("Content-Length", str(len(body)))
    http_handler.end_headers()
    http_handler.wfile.write(body)


def _send_file(http_handler, path, content_type):
    try:
        data = Path(path).read_bytes()
    except OSError:
        _send_json(http_handler, 404, {"error": "not found"})
        return
    http_handler.send_response(200)
    http_handler.send_header("Content-Type", content_type)
    http_handler.send_header("Content-Length", str(len(data)))
    http_handler.end_headers()
    http_handler.wfile.write(data)
