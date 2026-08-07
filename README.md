<div align="center">

# 🧰 DevWorkbench

**A lightweight, native developer toolbox for macOS — built with Python 3.12 + PySide6 (Qt Widgets). No Electron, no Flutter.**

`v0.1.0` · macOS · Python ≥ 3.12 · PySide6 ≥ 6.8 · SQLite

</div>

DevWorkbench is a fast-starting, low-memory desktop workbench that brings **file/folder comparison**, **AI-assisted workflows**, **Git**, **SSH**, **log analysis** and **plugin management** into one clean, dark-theme shell — packaged as a portable `.dmg` for distribution.

> **Status:** the shell, theme system, widgets, Compare engine + viewer, AI provider framework, Settings (10 categories, incl. a live menu manager) and Plugin framework are fully functional. Git, SSH and Log Analyzer ship as polished UI screens with their service wiring on the roadmap.

[📸 UI gallery](#screenshots) · [🚀 Quick start](#quick-start) · [📦 Building the DMG](#building-the-dmg) · [🏗 Architecture](#architecture) · [📚 Documentation](#documentation)

---

## ✨ Features

### 🔍 Compare — text, folders, structured files
- **Engine:** Myers O(ND) diff with automatic `difflib` fallback
- **Formats:** text, **JSON**, **XML**, **YAML**, **Properties**, **SQL**, **Java**, **Python**, **Dart**, **Markdown** (semantic comparison for structured files, with graceful text fallback)
- **Rules:** ignore whitespace / case / comments / blank lines, configurable context lines, follow-symlink toggle
- **Folder compare:** recursive walk, added / deleted / modified / **moved / renamed** detection, timestamps + hashes + sizes, ignored folders (`.git`, `.idea`, `target`, `build`, `node_modules`), live statistics
- **Synchronization:** Copy → right, Copy ← left, Delete, Refresh (with confirmation and per-file overwrite awareness)
- **Thread safety:** comparisons run in worker threads — the UI never freezes, even on 100k+ line files

### 👁 Compare viewer
- Line numbers, syntax highlighting, word-level & character-level change highlights
- **Side-by-side** and **inline (unified)** modes
- Collapsible sections (folds), diff-hunk navigation (Ctrl+[ / Ctrl+])
- Live search with match counts (⌘F), in-bar replace with replace-all, case toggle
- Minimap, scrollbar difference markers, zoom (Ctrl+= / Ctrl+− / Ctrl+0)
- Virtualized rendering — smooth scrolling for 100,000+ line files
- Dark **and** light themes, full Retina support

### 🤖 AI — swappable providers
- **OpenAI · Gemini · Anthropic · Ollama · Azure OpenAI** behind one interface
- Capabilities: `chat()`, `explain_diff()`, `generate_commit()`, `analyze_logs()`
- Providers are swappable from Settings without touching the UI
- Typed error hierarchy (auth, rate-limit, HTTP, timeout) — and **API keys never leak**: secrets live in the macOS Keychain, URLs are redacted in errors

### 🗂 Settings — 10 categories
General · **Menus** · Appearance · Git · AI · SSH · Compare · Logs · Plugins · Advanced

- Per-field validation, Apply / Save / Reset / Cancel flow, dirty tracking
- Normal values persist to **SQLite**; secrets go to the **macOS Keychain** (never the database)
- **Menu Manager:** enable/disable menu-bar menus (File/Edit/View/Module/Help) and whole module screens (sidebar, tabs, Module menu, command palette) — applied **live in the same session**, no restart; Settings itself is pinned so you can never lock yourself out

### 🚀 Performance by design
- Lazy module views — only the active tab is built at startup
- Background workers for everything heavy; bounded memory (1 GB files stream at ~0 MB delta)
- Measured: window up in ~0.2 s, ~120 MB RSS (see `docs/performance.md`)

### 🔌 Modular & developer-friendly
- MVC / service-layer architecture with **dependency injection** (single composition root)
- Plugin framework: manifest parsing, loader, manager with enable/disable state
- SQLite via a **Repository pattern** — no module touches the database directly
- SQLite migrations, WAL mode, foreign keys, busy timeouts

### 🛡 Crash handling & logging
- Unhandled exceptions → timestamped `crash-<ts>.log`; native segfaults → `faulthandler` dump (all threads); Qt messages routed into the rotating app log

---

## Screenshots

The full UI gallery (all modules, dark + light themes, real rendered screenshots) is generated offscreen and lives in the repo:

```bash
.venv/bin/python scripts/screenshots.py   # → docs/ui/gallery.html
```

Open **`docs/ui/gallery.html`** in a browser to browse all screens.

---

## Quick start

### Prerequisites

- **macOS** (arm64 or x86_64)
- **Python 3.12+** ([python.org](https://www.python.org/downloads/) or `brew install python@3.12`)

### 1. Set up the virtual environment

```bash
git clone <your-repo-url> DevWorkbench
cd DevWorkbench

python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
```

> `requirements.txt` is complete: runtime (`PySide6-Essentials`, optional `PyYAML`), build tooling (`pyinstaller`) **and** development / QA tools (`pytest`, `ruff`, `mypy`) — so the same command works on a fresh machine, whether you run, test, or package. Installing editable extras works too:
>
> ```bash
> .venv/bin/python -m pip install -e ".[dev,yaml,build]"
> ```

### 2. Run the app (development)

```bash
# from the project root — the package lives under src/
PYTHONPATH=src .venv/bin/python -m devworkbench
```

or install it as a command:

```bash
.venv/bin/python -m pip install -e .
devworkbench        # the console script is now on PATH
```

In development, state is stored under `./data/`; a packaged app uses `~/Library/Application Support/DevWorkbench`.

### 3. Run the tests

```bash
.venv/bin/python -m pytest          # 175 tests (1 skipped)
.venv/bin/python -m pytest -q tests/test_menu_manager.py   # or a single file
```

---

## 📦 Building the DMG

Everything is scripted — from a clean checkout you get a portable `.dmg` in two commands:

```bash
./scripts/build.sh          # PyInstaller → dist/DevWorkbench.app
./scripts/make_dmg.sh       # hdiutil → dist/DevWorkbench-0.1.0.dmg
```

The full pipeline:

```
scripts/version.py        single source of truth: __version__ in src/devworkbench/__init__.py
      │
      ▼
scripts/make_icon.py      (optional) renders the app glyph → resources/icons/DevWorkbench.icns
      ▼
scripts/build.sh          PyInstaller onedir .app — release or debug
      ▼
scripts/make_dmg.sh       hdiutil (built into macOS) → dist/DevWorkbench-<version>.dmg
```

### Step by step

```bash
# 1. Environment (first time only)
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt        # runtime + build + dev deps

# 2. (Optional) regenerate the multi-resolution app icon
.venv/bin/python scripts/make_icon.py

# 3. Check the version (reads src/devworkbench/__init__.py)
.venv/bin/python scripts/version.py                        # prints 0.1.0

# 4. Release build → dist/DevWorkbench.app
./scripts/build.sh

# 5. Package the DMG → dist/DevWorkbench-0.1.0.dmg
./scripts/make_dmg.sh
```

### Versioning (auto)

The version lives in exactly **one** place — `__version__ = "0.1.0"` in `src/devworkbench/__init__.py`. The spec stamps it into `Info.plist`, and the DMG filename reads it:

```bash
.venv/bin/python scripts/version.py bump patch   # 0.1.0 → 0.1.1
.venv/bin/python scripts/version.py bump minor   # 0.1.1 → 0.2.0
.venv/bin/python scripts/version.py bump major   # 0.2.0 → 1.0.0
.venv/bin/python scripts/version.py bump patch --tag   # ...also `git tag v0.1.1`
```

Writes are atomic and validated — a malformed version can never ship.

### Build modes

| | Release | Debug |
| --- | --- | --- |
| Command | `./scripts/build.sh` | `./scripts/build.sh debug` |
| Console | hidden | attached (for crash triage) |
| Output | `dist/DevWorkbench.app` | `dist/DevWorkbench-dbg/DevWorkbench.app` *(isolated — never clobbers release)* |

### Verify the artifacts

```bash
# Bundle structure & version
find dist/DevWorkbench.app -maxdepth 3 -name "*.dylib" | head
plutil -p dist/DevWorkbench.app/Contents/Info.plist | grep -E "CFBundle(S|Version)"

# Launch it — the window should open
open dist/DevWorkbench.app

# DMG integrity (should print: ... VALID)
hdiutil verify dist/DevWorkbench-0.1.0.dmg

# Inspect the mounted image (app + Applications symlink)
hdiutil attach dist/DevWorkbench-0.1.0.dmg -mountpoint /tmp/dwb -nobrowse && ls /tmp/dwb
hdiutil detach /tmp/dwb
```

The DMG is built with `hdiutil` (UDRW → UDZO) — **no `create-dmg` dependency** — and contains the app plus a symlink to `/Applications`, so users just drag the icon to install.

### Known packaging limits (roadmap)

- **Code signing:** the bundle carries an ad-hoc signature; shipping to the public requires a Developer ID certificate + hardened runtime + notarization. The scripts are structured so signing slots in between build and DMG without rework.
- **Universal binary:** builds for the host architecture (arm64 here). A universal build needs a two-arch build + `lipo`, or a CI matrix.
- **Auto-update:** the in-app `UpdateService` exists; wiring it to a release feed is a future step.
- **First launch:** an unsigned app requires right-click → Open on macOS. Notarization removes this.

Full details: **`docs/packaging.md`**.

---

## 🏗 Architecture

**Stack:** Python 3.12 · PySide6 (Qt **Widgets** only — no QML) · SQLite · PyInstaller · `hdiutil`

**Patterns:** MVC / service layer, dependency injection (composition root in `bootstrap.py`), Repository pattern, worker-thread background tasks, event-bus decoupling (`settings.changed`, `navigation.request`, …).

```
src/devworkbench/
├── bootstrap.py            # composition root — wires paths, logging, DI, DB, services, UI
├── app.py                  # QApplication subclass (Fusion style, high-DPI policy)
├── core/                   # config loader, events bus, settings store, DI container,
│                           # logging, crash handling (excepthook + faulthandler + Qt handler), paths
├── database/               # SQLite connection (WAL), migrations, ORM helpers, repositories
├── services/               # configuration, keychain, AI providers (5 backends), compare engine,
│                           #   folder diff, encoding detection, system service
├── modules/                # the 7 screens — Compare, Git, AI, SSH, Log Analyzer, Settings, Plugins
├── plugins/                # plugin manifest, loader, manager
├── ui/                     # theme system, programmatic icon set, main window, docks/sidebar,
│                           #   command palette, status bar, code viewer, common widgets
├── workers/                # off-thread workers: compare, folder sync, git, log, ssh (QRunnable contract)
└── utils/                  # shared helpers (filesystem, etc.)
```

**The seven modules:**

| Module | Status |
| --- | --- |
| **Compare** | ✅ Fully working — engine + viewer + folder sync |
| **AI** | ✅ Fully working — 5 providers, chat & diff helpers |
| **Settings** | ✅ Fully working — 10 categories, Keychain, live Menu Manager |
| **Plugin Manager** | 🟡 UI + framework (manifest/loader/manager); discovery wiring on the roadmap |
| **Git** | 🟡 Polished UI screen; service wiring on the roadmap |
| **SSH** | 🟡 Polished UI screen; service wiring on the roadmap |
| **Log Analyzer** | 🟡 Polished UI screen; service wiring on the roadmap |

---

## 📚 Documentation

| Doc | Contents |
| --- | --- |
| `docs/architecture/01-folder-structure.md` | Folder structure & package responsibilities |
| `docs/architecture/02-class-catalog.md` | Every class and its responsibilities |
| `docs/architecture/03-plugin-system.md` | Plugin contract, lifecycle, DI scopes |
| `docs/architecture/04-data-and-threading.md` | Data flow, SQLite, worker threads |
| `docs/architecture/05-packaging-and-distribution.md` | Packaging & distribution design |
| `docs/architecture/06-code-quality-review.md` | As-built audit across 10 dimensions + prioritized roadmap |
| `docs/performance.md` | Measured startup / memory / large-file numbers and every optimization |
| `docs/database-schema.md` | SQLite schema: settings, projects, repos, SSH servers, history, favorites, plugins |
| `docs/packaging.md` | The full release pipeline, crash handling, frozen-path behavior |
| `docs/ui/gallery.html` | Rendered screenshot gallery of every screen |

---

## 🧪 Quality

- **175 tests** (pytest, headless Qt via `QT_QPA_PLATFORM=offscreen`) covering the diff engine, folder sync, AI providers & key hygiene, configuration/keychain, database migrations, lazy tabs, theme, crash handling, version tooling — and the menu manager.
- Typed code with `py.typed`; ruff + mypy configs in `pyproject.toml`.
- Every broad `except` is deliberate and annotated (`# noqa: BLE001`) — boundaries fail loudly with typed errors, never silently.

## License

Proprietary. © DevWorkbench.
