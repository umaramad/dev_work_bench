#!/usr/bin/env python3
"""Generate the macOS application icon (``resources/icons/DevWorkbench.icns``).

The icon is the programmatic "app" glyph from ``ui/icons.py`` drawn at
1024×1024, downscaled with ``sips`` to the iconset sizes macOS expects, then
packed into a single ``.icns`` with ``iconutil`` (an Xcode/macOS tool).

Usage:
    .venv/bin/python scripts/make_icon.py [--size 1024] [--out resources/icons]

The app glyph is theme-agnostic (uses the dark-theme token set) and matches
the in-app sidebar / dock icon drawn by ``IconProvider``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

_ICONSET_SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}


def render_base(out: Path, size: int) -> None:
    """Draw the app glyph into a square PNG at ``size`` pixels (device 1x)."""
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QImage, QPainter, QColor

    from devworkbench.ui.icons import _glyph_app
    from devworkbench.ui.theme import DARK

    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(DARK["bg"]))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    _glyph_app(painter, size, QColor(DARK["text"]))
    painter.end()
    image.save(str(out))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=1024)
    parser.add_argument("--out", type=Path, default=ROOT / "resources" / "icons")
    args = parser.parse_args()

    if shutil.which("iconutil") is None or shutil.which("sips") is None:
        print("error: iconutil and sips are required (macOS Xcode tools)", file=sys.stderr)
        return 1

    iconset = Path(args.out) / "DevWorkbench.iconset"
    iconset.mkdir(parents=True, exist_ok=True)

    base = args.out / ".icon-base.png"
    render_base(base, args.size)

    # sips downscales (it cannot upscale, so every target <= base size).
    for filename, px in _ICONSET_SIZES.items():
        target = iconset / filename
        if px > args.size:
            shutil.copy(base, target)
        else:
            subprocess.run(
                ["sips", "-z", str(px), str(px), str(base), "--out", str(target)],
                check=True,
                capture_output=True,
            )

    icns = args.out / "DevWorkbench.icns"
    subprocess.run(
        ["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
        check=True,
    )

    shutil.rmtree(iconset)
    base.unlink(missing_ok=True)
    print(f"wrote {icns} ({icns.stat().st_size / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
