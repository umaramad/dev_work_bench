"""Mock data used to populate the UI screens.

Presentation-only: these stand in for what services will produce later.
Each value is intentionally simple — no logic, just display content.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Compare
# ---------------------------------------------------------------------------

DIFF_LEFT = [
    "import json",
    "from pathlib import Path",
    "",
    "def load_config(path: Path) -> dict:",
    "    with open(path, encoding='utf-8') as fh:",
    "        data = json.load(fh)",
    "    return data",
    "",
    "def save_config(path: Path, data: dict) -> None:",
    "    path.write_text(json.dumps(data, indent=2))",
    "",
    "if __name__ == '__main__':",
    "    cfg = load_config(Path('config.json'))",
    "    print(cfg)",
]

DIFF_RIGHT = [
    "import json",
    "from pathlib import Path",
    "from typing import Any",
    "",
    "def load_config(path: Path) -> dict[str, Any]:",
    "    if not path.exists():",
    "        raise FileNotFoundError(path)",
    "    with open(path, encoding='utf-8') as fh:",
    "        data = json.load(fh)",
    "    return data",
    "",
    "def save_config(path: Path, data: dict) -> None:",
    "    path.parent.mkdir(parents=True, exist_ok=True)",
    "    path.write_text(json.dumps(data, indent=2))",
    "",
    "if __name__ == '__main__':",
    "    cfg = load_config(Path('config.json'))",
    "    print(cfg)",
]

# One state string per line: "", "added", "removed", "changed"
DIFF_STATES_A = [
    "", "", "", "", "", "", "", "", "",
    "changed", "", "", "",
]
DIFF_STATES_B = [
    "", "", "added", "", "added", "added", "", "", "",
    "changed", "changed", "", "",
]

DIFF_HEADER = [
    "@@ -12,14 +12,16 @@",
    " import json",
    " from pathlib import Path",
    "+from typing import Any",
    " ",
    " def load_config(path: Path) -> dict:",
    "-    with open(path, encoding='utf-8') as fh:",
    "+    if not path.exists():",
    "+        raise FileNotFoundError(path)",
    "+    with open(path, encoding='utf-8') as fh:",
    "         data = json.load(fh)",
]
DIFF_HEADER_STATES = ["header", "", "", "added", "", "", "removed", "added", "added", ""]

# ---------------------------------------------------------------------------
# Git
# ---------------------------------------------------------------------------

GIT_REPOS = [
    "dev_work_bench",
    "freebuff-desktop",
    "scripts",
    "dotfiles",
]

GIT_STATUS = [
    ("Staged changes", [
        ("A", "src/devworkbench/ui/theme.py", "new file"),
        ("M", "src/devworkbench/ui/icons.py", "recolor glyphs"),
        ("A", "docs/ui/gallery.html", "new file"),
    ]),
    ("Unstaged changes", [
        ("M", "src/devworkbench/ui/main_window.py", "dock tweaks"),
        ("M", "pyproject.toml", "pin PySide6"),
        ("D", "scripts/old_build.sh", "deleted"),
        ("??", "data/devworkbench.db", "untracked"),
        ("??", "notes.md", "untracked"),
    ]),
]

GIT_COMMITS = [
    ("c4f1a2e", "Buffy", "2h ago", "Polish dock styling and status bar"),
    ("9b3d7c0", "Buffy", "5h ago", "Add compare view diff states"),
    ("77a1e9f", "Buffy", "1d ago", "Wire theme manager + icon provider"),
    ("3ef2b8d", "Buffy", "2d ago", "Scaffold module view shells"),
    ("a1c9d02", "Buffy", "3d ago", "Initial Qt application bootstrap"),
]

GIT_BRANCHES = [
    ("main", "active"),
    ("feature/compare-engine", ""),
    ("feature/ai-provider-ollama", ""),
    ("release/0.1.0", ""),
]

# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------

AI_MODELS = ["gpt-4.1", "claude-sonnet-4", "ollama/qwen3:14b", "deepseek-v3"]

AI_SESSIONS = [
    ("Refactor config loader", "2h ago"),
    ("Explain WAL pragmas", "yesterday"),
    ("Review migration 0004", "yesterday"),
    ("Draft release notes", "3d ago"),
    ("Debug FTS5 ranking", "4d ago"),
]

AI_CHAT = [
    ("user", "Can you explain why the compare view needs diff line states separate from the line text?"),
    ("assistant", "Yes — separating *content* from *annotation* keeps the model dumb and the view smart.\n\n**Why separate arrays:**\n\n- The view renders text and coloring independently, so you can re-diff without rebuilding the pane.\n- States are presentation metadata (added/removed/changed), not data — mixing them couples parsing to rendering.\n- Engines can emit `(lines, states)` and stay UI-agnostic, which is exactly what the `IDiffEngine` interface wants.\n\nWant me to sketch the model shape?"),
    ("user", "Yes, sketch it. Keep it minimal."),
    ("assistant", "Here's the minimal shape:\n\n```python\n@dataclass\nclass DiffLine:\n    text: str\n    state: Literal[\"added\", \"removed\", \"changed\", \"context\"]\n\n@dataclass\nclass DiffHunk:\n    lines: list[DiffLine]\n\n@dataclass\nclass DiffResult:\n    left: Path\n    right: Path\n    hunks: list[DiffHunk]\n```\n\nThe view renders `DiffResult` directly — no re-parsing anywhere."),
]

# ---------------------------------------------------------------------------
# SSH
# ---------------------------------------------------------------------------

SSH_HOSTS = [
    ("dev@10.0.1.14", "online"),
    ("pi@raspberrypi.local", "offline"),
    ("staging@tools.example.com", "online"),
    ("prod@db01.internal", "offline"),
]

SSH_BROWSER = [
    ("folder", "api", ["__init__.py", "app.py", "config.py"]),
    ("folder", "deploy", ["Dockerfile", "entrypoint.sh"]),
    ("folder", "logs", ["access.log", "worker.log"]),
    ("file", "README.md", []),
]

SSH_TRANSFERS = [
    ("deploy/Dockerfile", "Upload", "done", 100, "1.2 MB"),
    ("logs/access.log", "Download", "running", 64, "48 MB"),
    ("api/app.py", "Upload", "queued", 0, "12 KB"),
]

# ---------------------------------------------------------------------------
# Log Analyzer
# ---------------------------------------------------------------------------

LOG_FILE = "server.log · 1.2 MB · 18,420 lines"

LOG_ROWS = [
    ("10:42:01.214", "INFO", "http", "GET /api/projects 200 38ms"),
    ("10:42:01.301", "DEBUG", "db", "query projects WHERE owner = ? [4 rows]"),
    ("10:42:01.405", "INFO", "http", "GET /api/tasks 200 12ms"),
    ("10:42:02.118", "WARN", "cache", "redis connection degraded, retry 1/3"),
    ("10:42:02.220", "INFO", "worker", "job #44821 started (type=index)"),
    ("10:42:03.004", "ERROR", "worker", "job #44821 failed: timeout after 30s"),
    ("10:42:03.105", "INFO", "http", "GET /api/health 200 4ms"),
    ("10:42:03.310", "DEBUG", "db", "vacuum checkpoint (WAL) triggered"),
    ("10:42:04.019", "INFO", "worker", "job #44822 started (type=purge)"),
    ("10:42:04.512", "WARN", "http", "slow request: POST /api/export 2124ms"),
    ("10:42:05.003", "INFO", "http", "GET /api/logs?level=ERROR 200 9ms"),
    ("10:42:05.318", "ERROR", "db", "disk I/O error on checkpoint: 28"),
    ("10:42:06.000", "INFO", "worker", "job #44822 finished in 0.98s"),
    ("10:42:06.455", "DEBUG", "http", "session renewed for user 42"),
    ("10:42:07.102", "INFO", "http", "GET /api/projects 200 31ms"),
]

LOG_HISTOGRAM = {"TRACE": 12, "DEBUG": 1040, "INFO": 15820, "WARN": 1184, "ERROR": 264}

LOG_DETAIL = (
    "Traceback (most recent call last):\n"
    "  File \"/srv/api/worker.py\", line 88, in run\n"
    "    result = engine.index(file, incremental=True)\n"
    "  File \"/srv/api/indexer.py\", line 143, in index\n"
    "    conn.execute(\"INSERT INTO log_index ROWS(...)\")\n"
    "sqlite3.OperationalError: database is locked (5)\n"
)

# ---------------------------------------------------------------------------
# Settings / Plugins
# ---------------------------------------------------------------------------

KEYMAP = [
    ("⌘⇧P", "Command palette"),
    ("⌘1 … ⌘7", "Switch module"),
    ("⌘,", "Open settings"),
    ("⌘⇧B", "Toggle output panel"),
    ("⌘⇧A", "Toggle navigator"),
    ("⌘D", "Toggle dark / light theme"),
]

PLUGINS = [
    ("Compare", "1.0.0", "Built-in", "Enabled", True),
    ("Git", "1.0.0", "Built-in", "Enabled", True),
    ("AI Assistant", "1.0.0", "Built-in", "Enabled", True),
    ("SSH", "1.0.0", "Built-in", "Enabled", True),
    ("Log Analyzer", "1.0.0", "Built-in", "Enabled", True),
    ("Settings", "1.0.0", "Built-in", "Enabled", True),
    ("Plugin Manager", "1.0.0", "Built-in", "Enabled", True),
    ("format-json", "0.3.1", "Community", "Disabled", False),
    ("git-flow-helper", "1.2.0", "Community", "Enabled", False),
    ("log-hilite", "0.9.2", "Community", "Disabled", False),
]
