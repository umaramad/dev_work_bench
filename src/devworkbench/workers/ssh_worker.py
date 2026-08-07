"""SshWorker — connection, SFTP transfer, and command sessions."""

from __future__ import annotations

import socket

from devworkbench.workers.base import Worker


class SshWorker(Worker):
    """Runs SSH/SFTP work off the UI thread.

    ``probe`` is wired today: a real TCP reachability check for ``host:port``
    (no authentication). The authenticated operations (connect/list/transfer/
    exec) land with the SshService (paramiko) in the services milestone.
    """

    def __init__(self, operation: str, **payload) -> None:
        super().__init__()
        self._operation = operation  # probe | connect | list | transfer | exec
        self._payload = payload

    def work(self):
        """Perform the requested SSH operation."""
        if self._operation == "probe":
            host = str(self._payload.get("host", ""))
            port = int(self._payload.get("port", 22))
            timeout = float(self._payload.get("timeout", 5.0))
            with socket.create_connection((host, port), timeout=timeout):
                pass  # reachable — the connection closes on block exit
            return {"reachable": True, "host": host, "port": port}
        # Delegates to SshService (paramiko) — wired in the services milestone.
        raise NotImplementedError(f"SshWorker operation {self._operation!r} will be wired to SshService")
