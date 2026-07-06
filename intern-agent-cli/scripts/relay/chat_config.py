"""Per-chat config for feishu_relay.

Stores {chat_id: {"trigger_mode": ...}} at ~/.feishu_relay/chat_config.json.
Truth source is the file on disk.

trigger_mode (task252):
- "all"     (default): every non-app message in the chat triggers the intern.
- "at_only" : only messages that @ the chat's bot are forwarded; others are
              silent dropped.

detail_mode is NOT stored here (task283). The truth source lives on the
daemon machine because the hook hot-path that reads it also runs there — see
`intern-cli/scripts/daemon/daemon_chat_config.py`. relay only proxies via
WS RPC (RelayWSServer.detail_mode_request). This module deliberately exposes
no detail_mode helpers so any new caller gets a clear failure instead of
silently writing the wrong place (project rule 6).

There is no in-memory cache: relay traffic is low enough that re-reading the
small JSON per message is fine and avoids staleness between the file edited
via slash commands and any future process that writes the file (e.g.
daemon-bridged VS Code menu).
"""

import json
import os
import threading

_PATH = os.path.expanduser("~/.feishu_relay/chat_config.json")
_LOCK = threading.RLock()

_DEFAULT_TRIGGER_MODE = "all"
_VALID_TRIGGER_MODES = ("all", "at_only")

# Back-compat aliases for trigger_mode-era callers.
_DEFAULT_MODE = _DEFAULT_TRIGGER_MODE
_VALID_MODES = _VALID_TRIGGER_MODES


def _read():
    if not os.path.exists(_PATH):
        return {}
    with open(_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(data):
    os.makedirs(os.path.dirname(_PATH), exist_ok=True)
    tmp = _PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, _PATH)


def _get_field(chat_id, field, default, valid):
    if not chat_id:
        return default
    with _LOCK:
        data = _read()
    entry = data.get(chat_id) or {}
    value = entry.get(field, default)
    if value not in valid:
        return default
    return value


def _set_field(chat_id, field, value, default, valid):
    """Persist `field=value` for chat_id. Returns True iff the value changed."""
    if not chat_id:
        raise ValueError("chat_id is required")
    if value not in valid:
        raise ValueError(f"invalid {field}: {value!r}, must be one of {valid}")
    with _LOCK:
        data = _read()
        entry = data.setdefault(chat_id, {})
        old = entry.get(field, default)
        if old not in valid:
            old = default
        if old == value:
            return False
        entry[field] = value
        _write(data)
        return True


# ── trigger_mode (task252) ────────────────────────────────────────────────

def get_trigger_mode(chat_id):
    """Return "all" | "at_only" for chat_id. Defaults to "all"."""
    return _get_field(chat_id, "trigger_mode",
                      _DEFAULT_TRIGGER_MODE, _VALID_TRIGGER_MODES)


def set_trigger_mode(chat_id, mode):
    """Persist trigger_mode for chat_id. Returns True iff the value changed.

    task259: caller uses the changed signal to skip side-effects (e.g. patching
    the Feishu chat description) on no-op writes. Raises ValueError for invalid
    chat_id/mode so callers know they wrote nothing.
    """
    return _set_field(chat_id, "trigger_mode", mode,
                      _DEFAULT_TRIGGER_MODE, _VALID_TRIGGER_MODES)


def valid_modes():
    return _VALID_TRIGGER_MODES
