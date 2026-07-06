"""Monitor subpackage for the relay-hosted HTML dashboard.

Data flows daemon → relay WS → registry → aggregator → HTTP JSON → static UI.
The aggregator produces the public ``/api/snapshot`` shape defined in
``intern-monitor-web/API_CONTRACT.md``.
"""

from .aggregator import build_snapshot
from .handler import try_handle_get

__all__ = ["build_snapshot", "try_handle_get"]
