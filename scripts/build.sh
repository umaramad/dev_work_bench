#!/usr/bin/env bash
# Build DevWorkbench as a macOS .app bundle with PyInstaller.
#
#   ./scripts/build.sh            # release: windowed .app, stripped, slim
#   ./scripts/build.sh debug      # debug: console visible, unstripped
#   ./scripts/build.sh --clean    # (flag) purge dist/build before building
#
# Output: dist/DevWorkbench.app (release) or dist/DevWorkbench-dbg/DevWorkbench.app (debug)
# Version is read from src/devworkbench/__init__.py (see scripts/version.py).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
MODE="${1:-release}"
shift || true

if [[ "$MODE" == "debug" ]]; then
    CONSOLE_FLAG="-- --debug"
    DISTPATH="dist/DevWorkbench-dbg"   # keep debug output separate — never clobber the release .app
elif [[ "$MODE" == "release" ]]; then
    CONSOLE_FLAG=""
    DISTPATH="dist"
else
    echo "usage: $0 [release|debug] [--clean]" >&2
    exit 2
fi

# Ensure PyInstaller is available (declared in requirements.txt / [build] extras).
"$PYTHON" -c "import PyInstaller" 2>/dev/null || {
    echo "PyInstaller missing — run: .venv/bin/python -m pip install -r requirements.txt" >&2
    exit 1
}

VERSION="$("$PYTHON" scripts/version.py)"
echo "==> building $MODE build v$VERSION"

"$PYTHON" -m PyInstaller --noconfirm --clean --distpath "$DISTPATH" packaging/DevWorkbench.spec $CONSOLE_FLAG

APP="$DISTPATH/DevWorkbench.app"
if [[ ! -d "$APP" ]]; then
    echo "error: expected bundle at $APP" >&2
    exit 1
fi

echo "==> OK: $APP ($(du -sh "$APP" | cut -f1))"
