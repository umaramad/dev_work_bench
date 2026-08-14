# Git landing UX — split groups, bulk ops, console

**Date:** 2026-08-14  
**Status:** Approved for implementation  
**Scope:** Flet Git screen only (`flet_ui/screens/git.py`)

## Goal

Improve the Git landing experience without changing Compare, Settings, or the shell.

## Decisions

| Topic | Choice |
|-------|--------|
| Landing layout | Split: left groups, right repos for selected group |
| Bulk ops | Fetch all / Status all / Reset all on selected group |
| Console | Collapsed strip by default; expand to ~200px log |
| Other pages | Unchanged |

## Layout

1. Header actions (Add / Open / Scan / Manage groups)  
2. Split body — left group list (~240px), right group title + bulk bar + repo rows  
3. Console strip at bottom of the Git screen  

Selecting a group updates the right pane in place. Empty right pane when no group selected.

## Bulk operations

- Sequential `GitService` calls over favorites in the selected group  
- Disable bulk buttons + ProgressRing while running  
- Per-repo actions remain  

## Console

- Collapsed: `Console ▸` + short status (`idle` / `fetch 2/5…`)  
- Expanded: scrollable monospace log (`$ git … — path`, then ok/error)  
- Clear when expanded; auto-scroll on append  

## Constraints

- Prefer Column layouts (avoid nested ListView/GridView expand)  
- Persist selected group via `git.home.group`  
- Theme tokens from `flet_ui/theme.py`  

## Done when

Split landing works; bulk Fetch/Status/Reset log to console; console starts collapsed; other screens untouched.
