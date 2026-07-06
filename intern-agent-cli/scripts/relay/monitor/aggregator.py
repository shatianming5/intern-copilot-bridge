"""Registry → snapshot aggregator.

Converts the relay's in-memory registry view into the ``/api/snapshot`` shape
consumed by the dashboard frontend. Handles per-field degradation for old
daemons that do not report ``turn_active`` / ``last_active``.
"""
from __future__ import annotations

from datetime import datetime, timezone

SCHEMA_VERSION = 1
DEAD_AFTER_SECONDS = 120

_STATUS_WORKING = "working"
_STATUS_IDLE = "idle"
_STATUS_DEAD = "dead"


def _parse_iso_to_utc(ts):
    """Return aware datetime in UTC, or None if unparseable.

    Daemon emits naive local ISO from ``datetime.fromtimestamp(...).isoformat()``.
    Treat naive as local and convert; accept trailing Z / offsets otherwise.
    """
    if not ts:
        return None
    try:
        s = ts.rstrip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.astimezone(timezone.utc)


def _iso_utc(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _mask_mobile(mobile):
    """Return mobile masked as ``****XXXX`` (last 4 digits). Empty in → empty out."""
    if not mobile:
        return ""
    return "****" + mobile[-4:]


def _intern_status(detail):
    """Map registry intern detail → public ``status`` value.

    Degrades per-field: if ``turn_active`` is missing, use ``online``; if both
    are missing, use ``dead``. Capability-limited daemons still surface some
    liveness signal, so do not mark them dead wholesale.

    Liveness is the daemon's ``online`` signal (tmux process / WS active set).
    ``last_active`` (status.md mtime) is data freshness, not liveness — a
    stale status.md must not flip a running intern to ``dead``.
    """
    online = bool(detail.get("online"))
    if not online:
        return _STATUS_DEAD
    turn_active = detail.get("turn_active")
    if turn_active is True:
        return _STATUS_WORKING
    if turn_active is False:
        return _STATUS_IDLE
    # Old daemon: no turn_active reported. Online → idle is safe default.
    return _STATUS_IDLE


def _build_intern_entry(detail, now_utc):
    last_active_dt = _parse_iso_to_utc(detail.get("last_active"))
    age_seconds = (
        int((now_utc - last_active_dt).total_seconds())
        if last_active_dt
        else None
    )
    last_seen_at = _iso_utc(last_active_dt) if last_active_dt else None
    return {
        "name": detail.get("name", ""),
        "status": _intern_status(detail),
        "task": detail.get("current_task", "") or "",
        "last_seen_at": last_seen_at,
        "heartbeat_age_seconds": age_seconds,
        "runtime_alive": bool(detail.get("online")),
        "skin": "default",
        # Extensions beyond the v1 contract — safe additions consumed by the
        # relay-hosted dashboard, ignored by stricter contract-only clients.
        "type": detail.get("type", "copilot"),
    }


def _group_interns_by_project(interns_detail, now_utc):
    projects = {}
    for detail in interns_detail:
        project = detail.get("project") or None
        project_key = project if project else "__null__"
        bucket = projects.setdefault(
            project_key,
            {"project": project, "path": None, "interns": []},
        )
        bucket["interns"].append(_build_intern_entry(detail, now_utc))
    return list(projects.values())


def build_snapshot(registry, feishu_api=None):
    """Return the ``/api/snapshot`` payload computed from ``registry``.

    The relay is a single node that already aggregates all daemons, so
    ``machines[].reachable`` mirrors the ws_connected flag reported by each
    daemon. Ports/schemes that existed in the old exporter+host model are
    preserved as null-ish placeholders so the frontend contract is unchanged.

    ``feishu_api`` (optional): a ``FeishuAPI`` instance. When provided and the
    machine has an ``owner_open_id``, ``get_user_info`` (24h cached) resolves
    owner name / avatar / mobile so the dashboard can render the full admin
    header. Missing api → owner_name / owner_avatar fall back to empty strings.
    """
    now_utc = datetime.now(timezone.utc)
    summary = registry.get_machines_summary()
    machines = []
    for machine_id, info in summary.items():
        owner_open_id = info.get("owner_open_id", "") or ""
        owner_mobile = info.get("owner_mobile", "") or ""
        owner_name = ""
        owner_avatar = ""
        if feishu_api and owner_open_id:
            user_info, _err = feishu_api.get_user_info(owner_open_id)
            if user_info:
                owner_name = user_info.get("name", "") or ""
                owner_avatar = user_info.get("avatar_url", "") or ""
                owner_mobile = user_info.get("mobile", "") or owner_mobile

        machines.append({
            "label": machine_id,
            "ip": info.get("ip", "") or None,
            "port": info.get("ssh_port") or None,
            "enabled": True,
            "scheme": "relay",
            "reachable": bool(info.get("ws_connected")),
            "projects": _group_interns_by_project(
                info.get("interns_detail") or [], now_utc
            ),
            # Extensions carried through for the dashboard — ignored by the
            # old exporter/host frontend fields.
            "owner_name": owner_name,
            "owner_mobile": _mask_mobile(owner_mobile),
            "owner_open_id": owner_open_id,
            "owner_avatar": owner_avatar,
            "connected_at": info.get("connected_at", "") or "",
            "daemon_hash": info.get("daemon_hash", "") or "",
            "cli_versions": dict(info.get("cli_versions") or {}),
            "resources": dict(info.get("resources") or {}),
            "warnings": list(info.get("warnings") or []),
            "extension_version": info.get("extension_version", "") or "",
            "hooks_version": info.get("hooks_version", "") or "",
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso_utc(now_utc),
        "dead_after_seconds": DEAD_AFTER_SECONDS,
        "machines": machines,
    }
