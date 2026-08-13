#!/usr/bin/env bash
# Build DevWorkbench as a macOS .app bundle.
#
# Default (release/debug) packages the **Flet** UI via ``flet pack`` — this is
# what the portable DMG ships. The legacy PySide6 PyInstaller spec remains
# available as ``./scripts/build.sh qt``.
#
#   ./scripts/build.sh            # release: Flet .app → dist/DevWorkbench.app
#   ./scripts/build.sh debug      # debug: Flet .app with console
#   ./scripts/build.sh qt         # legacy PySide6 .app (packaging/DevWorkbench.spec)
#
# Version is read from src/devworkbench/__init__.py (see scripts/version.py).
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
FLET="${FLET:-.venv/bin/flet}"
MODE="${1:-release}"

VERSION="$("$PYTHON" scripts/version.py)"
ICON="resources/icons/DevWorkbench.icns"
BUNDLE_ID="com.devworkbench.app"

die() { echo "error: $*" >&2; exit 1; }

require_flet_pack() {
    [[ -x "$FLET" ]] || die "flet CLI missing — run: .venv/bin/python -m pip install -e '.[flet]'"
    "$PYTHON" -c "import flet" 2>/dev/null || die "Flet missing — run: .venv/bin/python -m pip install -e '.[flet]'"
    "$PYTHON" -c "import PyInstaller" 2>/dev/null || die "PyInstaller missing — run: .venv/bin/python -m pip install -r requirements.txt"
    [[ -f main.py ]] || die "main.py not found (Flet entry point)"
    [[ -f "$ICON" ]] || die "app icon missing: $ICON"
}

build_flet() {
    local debug_flag=()
    local distpath="dist"
    if [[ "$MODE" == "debug" ]]; then
        debug_flag=(--debug-console)
        distpath="dist/DevWorkbench-dbg"
        mkdir -p "$distpath"
    fi

    require_flet_pack
    echo "==> building Flet $MODE .app v$VERSION (portable UI)"

    # Ensure analysis can see the src-layout package.
    export PYTHONPATH="${PWD}/src${PYTHONPATH:+:$PYTHONPATH}"

    # Clean prior bundle so a stale Qt .app is never left behind.
    rm -rf "$distpath/DevWorkbench.app" "$distpath/DevWorkbench"

    "$FLET" pack main.py \
        --name DevWorkbench \
        --icon "$ICON" \
        --distpath "$distpath" \
        --product-name DevWorkbench \
        --product-version "$VERSION" \
        --bundle-id "$BUNDLE_ID" \
        --copyright "Copyright © 2026 DevWorkbench" \
        --add-data "resources:resources" \
        --hidden-import devworkbench \
        --hidden-import devworkbench.flet_ui \
        --hidden-import devworkbench.flet_ui.shell \
        --hidden-import devworkbench.flet_ui.theme \
        --hidden-import devworkbench.flet_ui.screens \
        --hidden-import devworkbench.flet_ui.screens.git \
        --hidden-import devworkbench.flet_ui.screens.settings \
        --hidden-import devworkbench.flet_ui.screens.compare \
        --hidden-import devworkbench.services.git \
        --hidden-import devworkbench.services.compare_service \
        --hidden-import devworkbench.services.configuration_service \
        --hidden-import devworkbench.services.keychain_service \
        --hidden-import devworkbench.services.compare.engine \
        --hidden-import devworkbench.services.compare.folder_sync \
        --hidden-import devworkbench.services.compare.models \
        --hidden-import devworkbench.services.compare.encoding \
        --hidden-import devworkbench.database.connection \
        --hidden-import devworkbench.database.migrations \
        --hidden-import devworkbench.database.repositories.favorite_repository \
        --hidden-import devworkbench.database.repositories.history_repository \
        --hidden-import devworkbench.database.repositories.settings_repository \
        --hidden-import devworkbench.core.paths \
        --hidden-import devworkbench.core.settings \
        --hidden-import devworkbench.core.events \
        --hidden-import devworkbench.models.persistence \
        --pyinstaller-build-args "--paths=src" "--exclude-module=PySide6" "--exclude-module=PySide6.QtCore" "--exclude-module=PySide6.QtWidgets" \
        "${debug_flag[@]}" \
        -y

    APP="$distpath/DevWorkbench.app"
    [[ -d "$APP" ]] || die "expected bundle at $APP"
    echo "==> OK: $APP ($(du -sh "$APP" | cut -f1))  [Flet UI]"
}

build_qt() {
    "$PYTHON" -c "import PyInstaller" 2>/dev/null || die "PyInstaller missing — run: .venv/bin/python -m pip install -r requirements.txt"
    echo "==> building legacy PySide6 release .app v$VERSION"
    "$PYTHON" -m PyInstaller --noconfirm --clean --distpath dist packaging/DevWorkbench.spec
    APP="dist/DevWorkbench.app"
    [[ -d "$APP" ]] || die "expected bundle at $APP"
    echo "==> OK: $APP ($(du -sh "$APP" | cut -f1))  [PySide6 UI]"
}

case "$MODE" in
    release|debug) build_flet ;;
    qt|pyside|pyside6) build_qt ;;
    *)
        echo "usage: $0 [release|debug|qt]" >&2
        exit 2
        ;;
esac
