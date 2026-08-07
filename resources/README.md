# resources/

Build-time assets for DevWorkbench.

| Directory | Purpose |
|---|---|
| `icons/` | Branding assets (app icon exports). Runtime icons are **programmatic** — see `src/devworkbench/ui/icons.py`, so they render pixel-perfect on Retina displays. |
| `themes/` | QSS theme files exported from `src/devworkbench/ui/theme.py` (the single source of truth). Regenerate with `scripts/export_themes.py`. |

The PyInstaller spec bundles these into the `.app` bundle.
