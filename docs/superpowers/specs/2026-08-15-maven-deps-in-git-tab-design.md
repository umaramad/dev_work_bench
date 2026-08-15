# Git repo tab — Maven declared dependencies (groupId / artifactId / version)

**Date:** 2026-08-15  
**Status:** Approved for implementation  
**Scope:** PySide6 Git module — per opened repository tab. Flet unchanged.

## Goal

When a repository is opened in Git, show a **Dependencies** view of every **declared** third-party dependency across all Maven submodules. Primary information: **groupId (family)**, **artifactId (jar)**, and **version**, with filter/sort/navigation suitable for **200+** rows.

## Decisions

| Topic | Choice |
|-------|--------|
| Placement | Inside Git **repo tab**: inner tabs `Git` \| `Dependencies` |
| Scan depth | Declared only (`<dependencies>` in each pom); no transitive tree |
| Multi-module | Discover via root/`pom.xml` + `<modules>` (nested) |
| Primary columns | **groupId**, **artifactId**, **version** |
| Group rollup | Out of scope v1 (per-repo only) |
| Maven CLI / Central | Not required for v1 |

## Layout

Opened repo tab:

```
[path] [branch]
[ Git | Dependencies ]
```

**Git** — existing Fetch / Pull / Status / Commits / output (unchanged).

**Dependencies:**

```
┌──────────────────┬─────────────────────────────────────────────────────┐
│ Modules          │ Search (groupId / artifactId / version)             │
│ • All            │ Scope filter · View: Per-module | Unique GAV        │
│ • billing-api    ├─────────────────────────────────────────────────────┤
│ • ledger-core    │ groupId (Family) │ artifactId │ Version │ Scope │ … │
│ • …              │ (sortable columns; virtualized / fast filter)       │
└──────────────────┴─────────────────────────────────────────────────────┘
Footer: N dependencies · M modules · Refresh
```

- Left **Modules** list filters the table; **All** shows the whole reactor.  
- Search is instant (debounce ~150ms).  
- Sort by groupId / artifactId / version / scope / module.  
- **Unique GAV** collapses same groupId:artifactId:version across modules (show module count); **Per-module** is one row per declaration.

## Scan behavior

Worker (off UI thread), triggered on first open of Dependencies and on **Refresh**:

1. Resolve repo root path (opened folder).  
2. Collect `pom.xml` files: follow `<modules>` from parent poms; also accept nested poms under the tree, skipping `target/`, `.git`, `node_modules`.  
3. Parse each pom (XML): module coordinates if present; each `<dependency>` → groupId, artifactId, version, scope (default `compile`), type (default `jar`).  
4. Optionally note `<dependencyManagement>` entries as **managed** (version may come from BOM/parent; show version string or property placeholder as written).  
5. Return list of rows to the UI.

**Property placeholders** (e.g. `${jackson.version}`): show the literal property text in Version for v1 (no full property resolution required). Later enhancement: resolve from same pom / parent.

## Data model (in-memory per tab)

```text
DependencyRow:
  module_id      # artifactId or path-derived label of the submodule
  group_id       # family
  artifact_id    # jar
  version        # declared string (may be ${property})
  scope          # compile|test|provided|runtime|…
  type           # usually jar
  pom_path       # absolute path for tooltip / future “reveal”
  managed        # bool if from dependencyManagement only (if we include those)
```

No persistence for v1.

## UX for 200+ rows

- Filter + sort without rebuilding module list.  
- Prefer `QTableView` + model (or equivalent) so filtering stays snappy.  
- Empty states: “No pom.xml found”, “No dependencies match filters”, parse error on a module (others still shown).  
- Tooltip: full GAV + pom path.

## Out of scope (v1)

- Transitive / `mvn dependency:tree`  
- “Latest version” / outdated badges (Maven Central)  
- Group-level inventory across many repos  
- Editing pom.xml  
- Flet parity  

## Done when

- Open a multi-module Maven repo → **Dependencies** lists declared deps with **groupId, artifactId, version**.  
- Module filter, search, and column sort work with 200+ rows without UI freeze.  
- Refresh re-scans; Git ops tab unchanged.
