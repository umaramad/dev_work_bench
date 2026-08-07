"""Plugin-record model — installed plugin entry as seen by the manager UI."""

from __future__ import annotations

from dataclasses import dataclass, field

from devworkbench.models.base import Model


@dataclass
class PluginRecord(Model):
    id: str = ""
    name: str = ""
    version: str = ""
    api_version: str = "1"
    category: str = "module"
    builtin: bool = False
    source: str = "local"         # builtin | local | git
    enabled: bool = True
    state: str = "discovered"
    config: dict = field(default_factory=dict)
