# Flet parity for Git, Settings, and Compare

**Date:** 2026-08-13  
**Status:** Approved for implementation planning  
**Scope phase:** Git gaps → Settings → Compare  

## 1. Goal

Make the Flet UI end-to-end functional for the modules that are fully working in PySide6 today—**Git** (close gaps), **Settings**, and **Compare**—while keeping the existing Flet shell/theme/layout language. PySide6 remains fully runnable side-by-side. AI Assistant, SSH, Log Analyzer, and Plugins stay Flet placeholders for a later phase.

A later UI/UX enhancement pass on Git operations is expected after this parity work; this phase prioritizes behavior parity over redesign.

## 2. Constraints and decisions

| Decision | Choice |
|----------|--------|
| Module scope | Git gaps, Settings, Compare only |
| AI / SSH / Logs / Plugins | Defer (placeholders remain) |
| Dual UI | Keep both runnable (`python main.py` = Flet, `python -m devworkbench` = PySide6) |
| Visual language | Keep existing Flet shell/theme; do not mirror Qt layouts |
| Architecture approach | Expand Flet `build_backend()` and port screens against shared services (no unified Application core yet) |
| Packaging / DMG | Stay on PySide6; out of scope |

## 3. Architecture

### 3.1 Composition roots

- **PySide6:** unchanged — `devworkbench.bootstrap` → DI container → `MainWindow`.
- **Flet:** `main.py` → `build_backend()` → `AppShell(page, backend=…).mount()`.

### 3.2 Expanded Flet backend

`build_backend()` wires shared data/services used by the three screens:

| Key | Component | Purpose |
|-----|-----------|---------|
| `paths` | `Paths` | App support / data locations |
| `database` | `ConnectionManager` | SQLite (already) |
| `favorites` | `FavoriteRepository` | Git favorites (already) |
| `history` | `HistoryRepository` | Git view-state where Qt persists it |
| `config` | `ConfigurationService` | Settings + git executable + compare options |
| `keychain` | `KeychainService` | Settings secrets |
| `git` | `GitService(executable from config)` | Async git ops |
| `compare` | New Qt-free async wrapper around `CompareEngine` + folder sync | Compare off the UI loop |

If the database or keychain cannot open, the shell still starts; screens degrade with clear messaging / demo behavior (same spirit as current Git demo mode).

### 3.3 Screen registry

`flet_ui/screens/build_screen` dispatches:

- `git` → existing `build_git_screen` (extended)
- `settings` → new `build_settings_screen`
- `compare` → new `build_compare_screen`
- `ai`, `ssh`, `loganalyzer`, `plugins` → placeholders

Screens receive `shell` (and thus `backend`); long work uses `page.run_task` with SnackBar / ProgressRing busy patterns already used by Flet Git.

### 3.4 Non-goals (this phase)

- Unified Application/DI core for both UIs
- Command palette, docks, menu manager, status bar parity with Qt shell
- Live QSS ↔ Flet theme bridge
- Packaging switch to Flet
- Porting AI chat screen
- Full Qt `CodeView` feature set (syntax highlighter fidelity, folds, minimap)

## 4. Git gaps

Close behavioral gaps inside the **current** Flet Git UX (group tiles → repo cards → actions). No Qt-style multi-tab chrome required unless a small detail surface is already natural in Flet.

### 4.1 Add

- **Open folder** — operate on a path without requiring a favorite first
- **Scan for repos** — `GitService.find_repos`; multi-select add to favorites
- **Manage groups** — rename / merge / delete group names on favorites
- **Edit favorite** — name, path, group
- **Ops parity** — Fetch, Fetch-all (nested repos), recent commits (`log`), remote status badges
- **Config** — `GitService` uses `git.executable` from `ConfigurationService` (fallback `"git"`)
- **Persistence** — favorites + history/view-state where Qt already persists filters/open state

### 4.2 Keep

Add / Remove, status, pull, branches, reset, demo mode if DB fails.

### 4.3 Follow-up (out of scope)

User-led UI/UX enhancement of Git operations after functional parity.

## 5. Settings

Flet Settings screen bound to the same `ConfigurationService` schema as Qt.

### 5.1 Layout

- Left category list + right form content
- Footer: Apply / Save / Reset / Cancel
- Dirty tracking and inline validation errors (same keys as Qt)
- Secrets via `KeychainService`

### 5.2 Categories

Wire all schema-backed pages: General, Menus, Appearance, Git, AI, SSH, Compare, Logs, Plugins, Advanced.

Special cases:

- **Menus:** persist visibility flags only (no live Qt menu manager rebuild)
- **Appearance:** persist theme/accent/font; Flet live apply is best-effort against existing dark tokens
- **Advanced:** backup database, open data folder, reset all (same behaviors as Qt)

### 5.3 Deferred

Live menubar rebuild, QSS theme bridge, AI chat consuming saved keys (AI screen later).

## 6. Compare

Port Compare **workflows** into Flet using `CompareEngine` via a small async service. Keep Flet layout/theme; do not clone Qt `CodeView`.

### 6.1 UI

- Header: left/right path fields + browse + swap; mode Files/Folders; engine combo (auto/Myers/difflib from settings); Compare
- **Files:** side-by-side (default) or inline line list with add/delete/change coloring; status line (counts, truncated)
- **Folders:** entry tree + filters; Copy L→R / R→L / Delete; open an entry into a file-diff subview
- Busy: disable Compare + ProgressRing; errors via SnackBar
- Options (ignore whitespace, etc.) from Settings / `CompareOptions` — same source as Qt

### 6.2 Engine layer

New Qt-free `CompareService` (or equivalent module helpers):

- Wrap `CompareEngine` and folder sync in `asyncio.to_thread` / executor
- Support the same modes as `CompareWorker`: `files`, `folders`, `texts`, `texts_with_file`

### 6.3 In scope

Path pickers, swap, mode, engine, compare, render text/folder results, folder sync, open nested file diff, settings-driven options.

### 6.4 Deferred polish

Full syntax highlighting / folds / minimap / find-replace bar fidelity; multi-tab diff sessions (start with one file view + one folder view).

## 7. Delivery order

1. Expand Flet `build_backend()` (config, keychain, history, git exe, compare service)
2. Git gaps
3. Settings
4. Compare
5. Leave AI / SSH / Logs / Plugins as placeholders

## 8. Success criteria

Phase is complete when:

1. `python main.py` runs Flet with working Git, Settings, and Compare against real DB/services.
2. `python -m devworkbench` still runs PySide6 with unchanged behavior.
3. **Git:** open / scan / groups / edit + fetch / fetch-all / log / remote status + config executable.
4. **Settings:** load / edit / validate / Apply / Save / Reset; secrets via keychain; Advanced backup / open / reset.
5. **Compare:** file + folder compare + folder sync + open entry diff; options from settings.

## 9. Error handling

- DB/keychain failures: log + degrade; shell still starts.
- Long git/compare ops: clear busy state on failure; cancel where practical.
- Primary actions surface errors via SnackBar; no silent failures.

## 10. Testing

- Extend existing service/unit tests where logic moves into Qt-free services.
- Light smoke checks for Flet screen builders where feasible.
- Manual checklist: Git CRUD/ops, Settings save round-trip, Compare file/folder/sync.

## 11. Out of scope summary

- AI Assistant Flet screen
- SSH / Log Analyzer / Plugins functional ports
- Replacing or removing PySide6
- Flet packaging / DMG switch
- Qt shell chrome (palette, docks, menus)
- Git ops visual redesign (planned after parity)
- Full CodeView parity features
