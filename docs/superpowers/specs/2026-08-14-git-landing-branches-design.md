# Git landing — shared branch list + group checkout/fetch

**Date:** 2026-08-14  
**Status:** Approved for implementation  
**Scope:** PySide6 Git landing (`modules/git/view.py`, config schema, `GitWorker` as needed)

## Goal

Let users configure a shared list of branch names, pick one under the group view, and checkout+fetch that branch across every repo in the selected group. Also enlarge card typography.

## Decisions

| Topic | Choice |
|-------|--------|
| Branch list scope | Global (common for all repos/groups) |
| Missing local branch | Skip that repo; log to console |
| Op sequence | Checkout selected branch, then fetch |
| Config UI | Landing branch bar + Edit branches… dialog |
| Flet | Unchanged |

## Layout

Under group title, above bulk bar:

1. Branch dropdown (configured names)  
2. **Fetch branch** — checkout then fetch for each group repo  
3. **Edit branches…** — multiline editor for the shared list  

## Persistence

- `git.home.branches` — JSON array of branch name strings  
- `git.home.branch` — currently selected branch  
- Defaults if empty: `main`, `master`, `develop`

## Fetch branch behavior

For each favorite in the selected group (sequential, same busy pattern as bulk ops):

1. If local branch exists → `git checkout <branch>`  
2. On success → `git fetch`  
3. If branch missing → skip; console + card toast  

No create-from-remote; no hard reset.

## Typography

Increase font size on landing cards for:

- Repo name  
- Path  
- Status toast / branch pill  

## Done when

Branch bar works; Edit branches persists; Fetch branch checkouts+fetches (skips missing); card fonts are larger; other screens untouched.
