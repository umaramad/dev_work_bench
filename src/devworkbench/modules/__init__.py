"""Built-in modules — each is a self-contained screen plugin (UI scaffold).

The full plugin contract (manifest, lifecycle, DI scope) lands with the core
framework; for now a module is metadata + a view factory.
"""

from devworkbench.modules.base import Module
from devworkbench.modules.ai import ai_module
from devworkbench.modules.compare import compare_module
from devworkbench.modules.git import git_module
from devworkbench.modules.home import home_module
from devworkbench.modules.loganalyzer import loganalyzer_module
from devworkbench.modules.plugins import plugins_module
from devworkbench.modules.settings import settings_module
from devworkbench.modules.ssh import ssh_module

MODULES: list[Module] = [
    home_module,
    compare_module,
    git_module,
    ai_module,
    ssh_module,
    loganalyzer_module,
    settings_module,
    plugins_module,
]

__all__ = ["MODULES", "Module"]
