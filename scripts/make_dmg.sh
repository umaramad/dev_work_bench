#!/usr/bin/env bash
# Package dist/DevWorkbench.app into a portable, internet-safe .dmg.
#
#   ./scripts/build.sh            # first: produce dist/DevWorkbench.app
#   ./scripts/make_dmg.sh         # -> dist/DevWorkbench-<version>.dmg
#
# The DMG gets a modern layout: the app plus a symlink to /Applications, so
# the user drags the icon into Applications and it "just works". Everything
# here uses hdiutil (built into macOS) — no create-dmg dependency.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-.venv/bin/python}"
APP="dist/DevWorkbench.app"
if [[ ! -d "$APP" ]]; then
    echo "error: $APP not found — run ./scripts/build.sh first" >&2
    exit 1
fi

VERSION="$("$PYTHON" scripts/version.py)"
DMG="dist/DevWorkbench-$VERSION.dmg"
STAGING="dist/.dmg-staging"            # folder copied into the image
MOUNT_POINT="dist/.dmg-mount"          # transient mount location
STAGING_IMAGE="dist/.staging-$VERSION.dmg"  # uncompressed UDRW image

# Tolerant cleanup: a busy mount (Spotlight/Finder) must not abort the build.
# The EXIT trap only removes transient artifacts — never the final DMG.
cleanup() {
    hdiutil detach "$MOUNT_POINT" >/dev/null 2>&1 || true
    rm -rf "$STAGING" "$MOUNT_POINT" "$STAGING_IMAGE" 2>/dev/null || true
}
# Pre-build: also drop any previous DMG for this version.
cleanup
rm -f "$DMG"
trap cleanup EXIT
mkdir -p "$STAGING" "$MOUNT_POINT"

# Copy the app and add a symlink to Applications.
cp -R "$APP" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

echo "==> creating $DMG"

# 1) Uncompressed staging image (UDRW) — sparse, fast to build.
hdiutil create -srcfolder "$STAGING" -volname "DevWorkbench" \
    -format UDRW -fs HFS+ -o "$STAGING_IMAGE" >/dev/null

# 2) Mount, arrange the window layout (best-effort), then detach.
hdiutil attach "$STAGING_IMAGE" -mountpoint "$MOUNT_POINT" -nobrowse -readwrite >/dev/null
if osascript <<'APPLESCRIPT' >/dev/null 2>&1; then
    tell application "Finder"
        tell disk "DevWorkbench"
            open
            set current view of container window to icon view
            set toolbar visible of container window to false
            set statusbar visible of container window to false
            set bounds of container window to {200, 160, 620, 460}
            set viewOptions to the icon view options of container window
            set arrangement of viewOptions to not arranged
            set icon size of viewOptions to 104
            set position of item "DevWorkbench.app" of container window to {120, 170}
            set position of item "Applications" of container window to {390, 170}
            close
        end tell
    end tell
APPLESCRIPT
    echo "   (window layout arranged)"
else
    echo "   (skipped window layout — no Finder session; DMG is still valid)"
fi
hdiutil detach "$MOUNT_POINT" >/dev/null

# 3) Compress to a read-only, internet-safe image.
hdiutil convert "$STAGING_IMAGE" -format UDZO -imagekey zlib-level=9 -o "$DMG" >/dev/null

echo "==> OK: $DMG ($(du -sh "$DMG" | cut -f1))"
