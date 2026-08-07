"""SshServerRepository — SSH connection profiles in the ``ssh_servers`` table.

Passphrases never live here; they are stored in the macOS Keychain and joined
by profile id in the SSH service.
"""

from __future__ import annotations

import datetime

from devworkbench.database.orm import CrudRepository
from devworkbench.models.persistence import SshServerRecord


class SshServerRepository(CrudRepository[SshServerRecord]):
    """CRUD plus lookups over saved SSH profiles."""

    model = SshServerRecord

    def by_name(self, name: str) -> SshServerRecord | None:
        row = self._fetch_one("SELECT * FROM ssh_servers WHERE name = ?", (name,))
        return self.model.from_row(row)

    def insert(self, model: SshServerRecord) -> int:
        model.created_at = model.updated_at = _now()
        return super().insert(model)

    def update(self, model: SshServerRecord) -> bool:
        model.updated_at = _now()
        return super().update(model)


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")
