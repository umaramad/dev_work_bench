# Per-repo local changes & vs-remote file list (group landing cards)

**Date:** 2026-08-20  
**Status:** Approved for implementation  
**Scope:** PySide6 Git module — group landing repo cards only. Opened-repo tab Status unchanged.

## Goal

From each repo tile on the Git group landing, quickly inspect:

1. **Local changes** — working tree vs HEAD (`git status`), like porcelain M / A / D / ?  
2. **Diff vs remote** — files that differ from the upstream tracking branch (`origin/<branch>` or configured upstream)

Two **separate actions** (not one modal with a toggle). Shared modal shell, different title + data source.

## Entry points (hybrid)

| Affordance | Opens |
|------------|--------|
| Right-click card → **Local changes…** | Local status modal |
| Right-click card → **Diff vs remote…** | Vs-remote modal |
| Footer **Changes · N** chip (only when working tree dirty) | Local status modal |

Sync footer (**Clean / Ahead / Behind / Diverged**) stays as today — **not** the opener for local files (that means remote sync, not dirty tree).

No permanent dual buttons beside the branch pill in v1 (avoids card clutter).

## Card chrome

- Add `cardLocalChanges` pill/chip in the footer row (next to sync status), hidden when clean.  
- Label: `Changes · N` where N = count of porcelain paths (staged + unstaged + untracked; count unique paths).  
- Click → Local changes modal for that repo.  
- Populate from a lightweight status pass (see Workers); refresh with group **Refresh status** and after bulk ops that already refresh cards.  
- Optional: amber/warn token when N > 0; theme tokens only.

## Shared modal

Dialog title includes repo name + mode:

- `Local changes — {repo}`  
- `Diff vs remote — {repo}`  

Body:

```
[Refresh]  [Open in …]                    N files
─────────────────────────────────────────────────
Status | Path
  M    | src/foo/Bar.java
  A    | …
  D    | …
  ?    | …
```

- Table: **Status** (short code or label), **Path** (relative). Sortable by path.  
- Empty state: “Working tree clean” / “No differences vs remote”.  
- **Refresh** re-runs the worker for that mode only.  
- Double-click row or **Reveal** / **Open**: open file or reveal in Finder when path exists (nice-to-have v1 if cheap; else v1.1).  
- Close via standard dialog buttons.

Vs-remote modal subtitle line: `Comparing to {upstream}` (e.g. `origin/main`). If no upstream: clear error “No upstream configured — fetch/set upstream first.”

## Data sources

### A — Local changes

- `git status --porcelain=v1` (or `--short`) in the repo path.  
- Parse XY codes into display status: modified / added / deleted / renamed / untracked / conflicted (map common codes; unknown → show raw).  
- Local only; no network.  
- Fast enough to open modal immediately with spinner if > ~300ms.

### B — Diff vs remote

- Resolve upstream: `@{upstream}` or `origin/<current-branch>` when upstream missing but remote branch exists.  
- Prefer **no auto-fetch** on open (predictable, offline-friendly). Use current remote-tracking refs.  
- List: `git diff --name-status <upstream>...HEAD` and/or include uncommitted?  

**v1 decision:** vs-remote modal shows **committed differences vs upstream** (`git diff --name-status @{upstream}...HEAD`) **plus** a note if the working tree is also dirty (“Also N local uncommitted change(s) — use Local changes…”). Uncommitted files are **not** merged into the vs-remote list (keeps A and B distinct).

- If upstream ref missing: offer status text; do not silently fetch unless user already used group Fetch.

## Workers / wiring

- Extend `GitWorker` (or small helpers used by it) with ops, e.g.:  
  - `working_tree_status` → `{ ok, files: [{status, path, ...}], count }`  
  - `diff_vs_upstream` → `{ ok, upstream, files: [{status, path}], error? }`  
- Run on `QThreadPool`; modal shows busy state; cancel or ignore if card/dialog closed.  
- Card dirty count: reuse `working_tree_status` count (or parse from existing `status`/`remote_status` if extended) during **Refresh status** batch so chips stay in sync without a second full group scan later. Prefer **one porcelain parse per repo** during refresh that also feeds the Changes chip.

## Context menu placement

On card context menu (existing Actions / Packs menu):

- After built-in open-ish items (or before Packs):  
  - **Local changes…**  
  - **Diff vs remote…**  
- Keep custom group Actions / Packs as today.

## Out of scope (v1)

- Buttons permanently next to branch pill  
- Staging / commit / discard from the modal  
- File content diff viewer inside the modal  
- Auto-fetch on Diff vs remote open  
- Flet parity  
- Changing group **Status all** console behavior (may still dump raw status text)

## Success criteria

1. Dirty repo shows **Changes · N**; click opens Local changes modal with those files.  
2. Clean working tree hides the chip; sync Clean/Ahead/Behind unchanged.  
3. Right-click exposes both Local and vs-remote actions; vs-remote lists name-status vs upstream without mixing uncommitted paths.  
4. UI never blocks; errors surface in the modal (missing upstream, not a git repo).
