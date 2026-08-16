# Maven dependencies HTML — interactive offline report

**Date:** 2026-08-16  
**Status:** Approved for implementation  
**Scope:** Git repo tab → Dependencies → Download HTML. Extends `dependencies_to_html` / `download_html`. CSV export unchanged.

## Goal

Make the downloaded `.html` useful for **you and teammates**: one self-contained offline file with search, scope filter, Conflicts toggle, sort, and a live shown/total count — without requiring the app.

## Decisions

| Topic | Choice |
|-------|--------|
| Approach | Self-contained HTML + inline CSS + inline JS (no CDN) |
| Rows embedded | **Full declared scan** for the repo (`state["rows"]` after enrich), not only the app’s visible slice |
| Initial filters | Seed from the app’s current controls (search text, scope, Conflicts on/off, selected module if not “All”) |
| Clearing filters | Browser “Clear” / emptying search restores the full embedded set |
| CSV Download | Unchanged — still exports **currently visible** rows only |
| Unique GAV / family chips / copy GAV | Out of scope v1 |
| Dark theme | Optional later via `prefers-color-scheme` only; v1 ships a single readable light-leaning stylesheet |

Supersedes the 2026-08-15 note that HTML export is “currently visible rows only” for this Download HTML action.

## Report chrome

```
[Title] Maven dependencies (declared)
[Meta]  Repo · Branch · Generated · Total rows embedded

[Sticky toolbar]
  Search… | Scope ▾ | [Conflicts] | N shown / M total | Clear

[Table]
  groupId | artifactId | Version | Scope | Module | Managed
  (sortable headers; conflict rows tinted soft red)
```

### Controls (parity with app v1)

- **Search** — case-insensitive match across groupId, artifactId, version (and module id for convenience).
- **Scope** — All scopes + compile / test / provided / runtime / system / import.
- **Conflicts** — toggle; when on, show only rows with `conflict` true (same meaning as in-app: same GA, different versions across modules).
- **Module** — if the app had a module selected (not All), seed that filter; HTML includes a module `<select>` built from distinct module ids in the embedded rows (plus All).
- **Clear** — reset search, scope=All, Conflicts off, module=All.
- **Sort** — click column header; toggle asc/desc; indicator on active column.
- **Count** — `N shown / M total` updates on every filter change.

### Row data

Each `<tr>` carries attributes used by the filter script, at least:

- `data-group`, `data-artifact`, `data-version`, `data-scope`, `data-module`
- `data-conflict="1"` when conflict
- `data-managed="1"` when managed

Conflict rows get a CSS class (soft red background). Managed stays a visible column value (`yes` / empty).

## App-side wiring

### `dependencies_to_html` (`maven_worker.py`)

Signature gains an optional `initial_filters` dict, e.g.:

```python
initial_filters = {
    "search": str,
    "scope": str,          # "" = all
    "conflicts": bool,
    "module": str,         # "" = all
}
```

- Escape all cell text and attribute values.
- Emit full row set + toolbar + script.
- Script reads `initial_filters` (JSON in a `<script type="application/json" id="initial-filters">` or data attributes on the toolbar) and applies once on `DOMContentLoaded`.

### `download_html` (`maven_deps.py`)

- Pass **`state["rows"]`** (full enriched list), not `visible_rows()`.
- Build `initial_filters` from current search, scope combo, Conflicts button, and modules list selection.
- Status message: e.g. `Saved M row(s) (interactive) → path` using embedded count.
- If scan not loaded / empty, keep current empty-table behavior.

## Constraints

- Must open via `file://` and as an email/Confluence attachment with no network.
- No external fonts, scripts, or images.
- Keep JS small and vanilla (no build step).
- Print should still show the (currently filtered) table reasonably.

## Out of scope (v1)

- Unique GAV collapse in HTML
- Family filter chips
- Copy GAV / Maven XML from a row
- Syncing filters back into the app
- Changing CSV semantics

## Success criteria

1. Downloading HTML with Conflicts on and a search string produces a file that opens already filtered that way, with total rows still available after Clear.
2. Teammate with no app can search / change scope / toggle Conflicts / sort offline.
3. Conflict rows are visibly tinted (not black / not unreadable).
4. File has no external network dependencies.
