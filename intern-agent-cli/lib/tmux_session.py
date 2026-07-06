"""Helpers for scoped intern tmux session names.

Intern names are only unique inside an enterprise workspace/project.  Tmux
session names are machine-global, so they need an additional stable scope.
"""

from __future__ import annotations

import hashlib
import os
import re

_SAFE_PART_RE = re.compile(r"[^A-Za-z0-9_-]+")
_MAX_SAFE_INTERN_PART = 48


def safe_tmux_part(value: str, *, default: str = "intern", max_len: int = _MAX_SAFE_INTERN_PART) -> str:
    safe = _SAFE_PART_RE.sub("_", str(value or "")).strip("_-")
    if not safe:
        safe = default
    return safe[:max_len].rstrip("_-") or default


def scoped_tmux_session_name(
    intern_name: str,
    *,
    project: str = "",
    workspace_id: str = "",
    intern_dir: str = "",
) -> str:
    """Return the deterministic tmux session name for one runtime identity."""
    intern_part = safe_tmux_part(intern_name)
    canonical_dir = os.path.abspath(intern_dir) if intern_dir else ""
    scope = "\0".join([
        str(workspace_id or ""),
        str(project or ""),
        canonical_dir,
        str(intern_name or ""),
    ])
    digest = hashlib.sha1(scope.encode("utf-8", errors="surrogateescape")).hexdigest()[:12]
    return f"ia_{intern_part}_{digest}"


def tmux_ready_channel(session_name: str) -> str:
    return f"session_ready_{safe_tmux_part(session_name, default='session', max_len=80)}"
