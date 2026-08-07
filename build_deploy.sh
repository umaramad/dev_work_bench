#!/usr/bin/env bash
# DevWorkbench — build & deploy menu.
#
#   ./build_deploy.sh              # interactive menu
#   ./build_deploy.sh local        # run from source (dev mode)
#   ./build_deploy.sh build        # build release .app only
#   ./build_deploy.sh dmg          # build release .app + portable .dmg
#   ./build_deploy.sh debug        # build debug .app (console visible)
#   ./build_deploy.sh tests        # run the test suite
#   ./build_deploy.sh bump [patch|minor|major]   # bump version
#   ./build_deploy.sh version      # print current version
#   ./build_deploy.sh clean        # purge dist/ and build/
#   ./build_deploy.sh doctor       # check prerequisites
#
# With no argument the script prompts for an option — choose "1. Run locally"
# to start the app from source, or "3. Build + portable DMG" to produce
# dist/DevWorkbench-<version>.dmg ready to distribute.
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-.venv/bin/python}"
VERSION=$("$PYTHON" scripts/version.py 2>/dev/null || echo "?")
ACTION="${1:-menu}"

# --- colors (only when on a TTY) -------------------------------------------
if [[ -t 1 ]]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
    C_GRN=$'\033[32m'; C_YEL=$'\033[33m'; C_CYN=$'\033[36m'; C_RED=$'\033[31m'
else
    C_RESET=""; C_BOLD=""; C_DIM=""; C_GRN=""; C_YEL=""; C_CYN=""; C_RED=""
fi

banner() {
    echo "${C_BOLD}${C_CYN}┌─────────────────────────────────────────────┐${C_RESET}"
    echo "${C_BOLD}${C_CYN}│  DevWorkbench  ·  build & deploy             │${C_RESET}"
    echo "${C_BOLD}${C_CYN}└─────────────────────────────────────────────┘${C_RESET}"
    echo "${C_DIM}version ${VERSION}   platform $(uname -sm)${C_RESET}"
    echo
}

die() { echo "${C_RED}error: $*${C_RESET}" >&2; exit 1; }
ok()   { echo "${C_GRN}==> $*${C_RESET}"; }
note() { echo "${C_DIM}$*${C_RESET}"; }

# --- prerequisites ----------------------------------------------------------
require_venv() {
    [[ -x .venv/bin/python ]] || die "virtualenv missing — run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
}

require_pyinstaller() {
    "$PYTHON" -c "import PyInstaller" 2>/dev/null || die "PyInstaller missing — run: .venv/bin/python -m pip install -r requirements.txt"
}

# --- actions ----------------------------------------------------------------
run_local() {
    require_venv
    ok "running DevWorkbench from source (Ctrl-C to quit)"
    exec scripts/dev.sh
}

build_release() {
    require_venv; require_pyinstaller
    ok "building release .app v$VERSION"
    scripts/build.sh release
    ok "done: dist/DevWorkbench.app"
}

build_debug() {
    require_venv; require_pyinstaller
    ok "building debug .app v$VERSION (console visible)"
    scripts/build.sh debug
    ok "done: dist/DevWorkbench-dbg/DevWorkbench.app"
}

build_dmg() {
    build_release
    [[ -d dist/DevWorkbench.app ]] || die "dist/DevWorkbench.app not found — run 'build' first"
    ok "packaging portable DMG"
    scripts/make_dmg.sh
    ok "done: dist/DevWorkbench-$VERSION.dmg  (drag into /Applications)"
    note "verify: hdiutil verify dist/DevWorkbench-$VERSION.dmg"
}

run_tests() {
    require_venv
    ok "running test suite"
    QT_QPA_PLATFORM=offscreen "$PYTHON" -m pytest tests/ -q
}

bump_version() {
    require_venv
    local part="${1:-patch}"
    "$PYTHON" scripts/version.py bump "$part"
}

clean_all() {
    ok "purging dist/ and build/"
    # macOS rm -rf can transiently fail on a busy dir (Spotlight/.DS_Store);
    # retry once, then verify — never claim success if anything survives.
    rm -rf dist build 2>/dev/null || true
    if [[ -e dist || -e build ]]; then
        note "retrying — something still held a file…"
        rm -rf dist build 2>/dev/null || true
    fi
    if [[ -e dist || -e build ]]; then
        die "could not fully remove dist/ or build/ — quit any app launched from dist/ and retry"
    fi
    ok "cleaned"
}

doctor() {
    require_venv
    echo "${C_BOLD}prerequisites:${C_RESET}"
    note "  python : $($PYTHON --version 2>&1)"
    if "$PYTHON" -c "import PySide6" 2>/dev/null; then note "  PySide6: present"; else note "  PySide6: ${C_RED}missing${C_RESET}"; fi
    if "$PYTHON" -c "import PyInstaller" 2>/dev/null; then note "  PyInstaller: present"; else note "  PyInstaller: ${C_RED}missing${C_RESET} (needed for build/dmg)"; fi
    echo
    ok "done"
}

# --- interactive menu --------------------------------------------------------
menu() {
    banner
    echo "${C_BOLD}Choose an option:${C_RESET}"
    echo "  ${C_CYN}1${C_RESET}) Run locally (from source)"
    echo "  ${C_CYN}2${C_RESET}) Build release .app"
    echo "  ${C_CYN}3${C_RESET}) Build release .app + portable DMG"
    echo "  ${C_CYN}4${C_RESET}) Build debug .app (console visible)"
    echo "  ${C_CYN}5${C_RESET}) Run tests"
    echo "  ${C_CYN}6${C_RESET}) Bump version (patch)"
    echo "  ${C_CYN}7${C_RESET}) Version bump helper — pick patch/minor/major"
    echo "  ${C_CYN}8${C_RESET}) Clean build artifacts (dist/ build/)"
    echo "  ${C_CYN}9${C_RESET}) Doctor — check prerequisites"
    echo "  ${C_CYN}0${C_RESET}) Quit"
    echo
    read -r -p "Select [1-9, 0]: " choice || { echo; ok "bye"; exit 0; }
    while true; do
        case "$choice" in
            1) run_local ;;  # execs — terminates the script by design
            2) build_release ;;
            3) build_dmg ;;
            4) build_debug ;;
            5) run_tests ;;
            6) bump_version patch ;;
            7)
                read -r -p "Bump part [patch|minor|major] (default patch): " part
                bump_version "${part:-patch}"
                ;;
            8) clean_all ;;
            9) doctor ;;
            0) ok "bye"; exit 0 ;;
            *) die "invalid choice '$choice'" ;;
        esac
        # Menu loops so quick actions (bump, clean, tests) can chain into a
        # build; refresh the version stamp in case it was bumped above.
        VERSION=$("$PYTHON" scripts/version.py 2>/dev/null || echo "?")
        echo
        read -r -p "Anything else? [1-9, 0 to quit]: " choice || { echo; ok "bye"; exit 0; }
    done
}

# --- dispatch ----------------------------------------------------------------
case "$ACTION" in
    local|run|dev)   run_local ;;
    build|app)       build_release ;;
    dmg|deploy)      build_dmg ;;
    debug)           build_debug ;;
    tests|test)      run_tests ;;
    bump)            shift; bump_version "${1:-patch}" ;;
    version)         echo "$VERSION" ;;
    clean)           clean_all ;;
    doctor)          doctor ;;
    menu|"")         menu ;;
    -h|--help)       grep -E '^# ' "$0" | grep -v '^# ---' | sed 's/^# \{0,1\}//' ;;
    *)               die "unknown action '$ACTION' — run ./build_deploy.sh for the menu" ;;
esac
