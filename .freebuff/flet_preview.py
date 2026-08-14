"""Run the DevWorkbench Flet shell in web mode (for previewing)."""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

import flet as ft  # noqa: E402

from main import main  # noqa: E402

if __name__ == "__main__":
    ft.run(main, view=ft.AppView.FLET_APP_WEB, host="127.0.0.1", port=8565)
