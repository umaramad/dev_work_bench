# Git landing UX — PySide6 parity with Flet

**Date:** 2026-08-14  
**Status:** Approved for implementation  
**Scope:** PySide6 Git landing only (`modules/git/view.py` + minimal styles if needed)  
**Companion:** `2026-08-14-git-landing-ux-design.md` (Flet)

## Goal

Bring the Folders (landing) tab in line with the Flet Git landing: split groups/repos, group bulk ops, and a collapsible console — without removing per-repo Git tabs.

## Decisions

| Topic | Choice |
|-------|--------|
| Landing layout | Split: left groups (~240px), right repos for selected group |
| Drill-down | Remove stack/back; selecting a group updates the right pane in place |
| Group filter combo | Remove (“All groups”); left list is the selector |
| Bulk ops | Fetch all / Status all / Reset all on selected group |
| Console | Collapsed strip by default; expand ~200px monospace log |
| Open repo | Keep existing behavior — Open still opens a dedicated Git tab |
| Flet / other modules | Unchanged |

## Layout (Folders tab)

1. Header actions (Open / Scan / Add / Manage groups)  
2. Split body (`QSplitter`):
   - Left: group list (name + count), selection highlight  
   - Right: group title + bulk bar + searchable repo cards  
3. Console strip at the bottom of the **landing** only (not inside repo tabs)

## Bulk operations

- Sequential `GitWorker` calls over favorites in the selected group  
- Disable bulk buttons while a bulk run is in progress  
- Log each command/result to the console  
- Per-card actions remain  

## Console

- Collapsed: `Console ▸` + short status (`idle` / `fetch 2/5…`)  
- Expanded: scrollable monospace `QPlainTextEdit` + Clear; auto-scroll on append  
- Card-level and bulk ops both append lines  

## Persistence

- Selected group via `git.home.group` (same key as Flet)  
- Search via existing `git.home.search` if already used  

## Constraints

- Reuse existing theme / `button` / `styled_label` patterns  
- Demo mode: single “Demo folders” group on the left  
- Closing / cancelling workers for open tabs stays as today  

## Done when

Split landing matches Flet structure; bulk Fetch/Status/Reset log to console; console starts collapsed; Open still opens a Git tab; Flet and other screens untouched.
