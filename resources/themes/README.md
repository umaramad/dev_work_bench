# resources/themes/

QSS exports of the built-in themes (`dark.qss`, `light.qss`).

These files are **generated** from `src/devworkbench/ui/theme.py` — the
single source of truth — by:

```bash
.venv/bin/python scripts/export_themes.py
```

They exist so external tools, docs, and future user-overridable themes can
consume the QSS without importing Python.
