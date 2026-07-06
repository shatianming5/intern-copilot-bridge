from __future__ import annotations

import os
from dataclasses import dataclass


DEFAULT_DEBUG_WORK_ROOT = "/root/axis_enterprise_ci"


@dataclass(frozen=True)
class Machine:
    id: str
    host: str
    port: int
    role: str
    work_root: str = DEFAULT_DEBUG_WORK_ROOT
    index: int = 0

    def to_full_ci_machine(self) -> dict:
        data = {
            "id": self.id,
            "index": self.index,
            "host": self.host,
            "port": self.port,
            "ssh_port": self.port,
            "role": self.role,
            "work_root": self.work_root,
            "ssh": f"ssh -p {self.port} root@{self.host}",
        }
        if self.role == "relay":
            data["relay_host"] = self.host
            data["container_host"] = self.host
        return data


DEBUG_MACHINES = [
    Machine(
        id="debug-a",
        index=0,
        host=os.environ.get("CI_DEBUG_HOST_A", ""),
        port=20780,
        role="relay",
    ),
    Machine(
        id="debug-b",
        index=1,
        host=os.environ.get("CI_DEBUG_HOST_B", ""),
        port=19052,
        role="worker",
    ),
]


def debug_machines() -> list[dict]:
    # TODO(machine-pool): replace this hard-coded list when the machine pool
    # service exists. This CI assumes the operator guarantees a single active
    # debug full-CI run at a time.
    return [machine.to_full_ci_machine() for machine in DEBUG_MACHINES]
