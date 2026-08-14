# Git landing — per-group custom Actions menu

**Date:** 2026-08-14  
**Status:** Approved for implementation  
**Scope:** PySide6 Git landing (`modules/git/view.py`, `git_worker.py`, config schema). Flet unchanged.

## Goal

Let each repository group define an arbitrary list of git commands (Add, Commit with message, Push, and user-defined extras). Run the chosen action across every repo in the selected group from an **Actions** dropdown next to **Checkout & reset**. Support copying actions into other groups.

## Decisions

| Topic | Choice |
|-------|--------|
| Command model | Fully custom: label + command string |
| Storage | Per-group lists; copy snapshot into other groups |
| Placeholders | Optional `{{name}}`; one dialog per run, same values for all repos |
| Commit message | Always prompted at run time via `{{message}}` — never hardcoded |
| UI | **Actions ▾** dropdown (scales to N); Edit / Copy at menu bottom |
| Bulk Fetch/Status/Reset | Keep existing bulk bar for v1 |
| Flet | Unchanged |

## Layout

Branch / ops row under group title:

| Branch `[combo ▾]` | **Checkout & reset** | **Actions ▾** | Edit branches… |

**Actions** menu contents:

1. One menu item per configured action (label)  
2. Separator  
3. **Edit actions…**  
4. **Copy actions to groups…**

Disable **Actions** (and its run items) when no real group is selected, demo mode, or a bulk/branch op is already running.

## Data model

Config key: `git.home.group_actions` (JSON string).

```json
{
  "MyApps": [
    { "id": "uuid", "label": "Add", "command": "git add ." },
    { "id": "uuid", "label": "Commit", "command": "git commit -m \"{{message}}\"" },
    { "id": "uuid", "label": "Push", "command": "git push" }
  ],
  "": []
}
```

- Key = group name; `""` for Ungrouped.  
- `id` = stable string (uuid) for edit/reorder/copy selection.  
- `label` = menu text (non-empty).  
- `command` = free text executed in each repo’s working tree.

### Starter seed

When the selected group has no entry (or empty list) the first time the Actions menu is opened / group is shown:

- Add → `git add .`  
- Commit → `git commit -m "{{message}}"`  
- Push → `git push`  

Persist the seeded list immediately.

### Placeholders

- Pattern: `{{identifier}}` where `identifier` is `[A-Za-z_][A-Za-z0-9_]*`.  
- Before run: collect unique placeholders in order of first appearance; dialog with one field each.  
- Substitute into the command string for every repo in the run.  
- Empty required field → cancel the run (do not start the queue).

### Commit message (required UX)

Selecting **Commit** (seeded as `git commit -m "{{message}}"`) **must** open a prompt for the commit message every time — it is dynamic and must not be stored as a fixed string in the command.

- Dialog title: **Commit message**  
- Field: multiline text (preferred for longer messages); label `message`  
- Confirm disabled until the field is non-empty (after strip)  
- The entered text is substituted for `{{message}}` and used for **all** repos in the group for that run only  
- The message is **not** persisted as a default for the next run  

Any custom action that includes `{{message}}` (or other `{{…}}` tokens) uses the same prompt flow.

## Edit actions… (current group)

Dialog:

- List of rows: Label | Command  
- Buttons: Add row, Remove selected, Move up / Move down  
- Hint: `Use {{name}} for prompts (same value for every repo).`  
- Save → write this group’s array into `git.home.group_actions`.  
- Reject empty labels or empty commands on save.

## Copy actions to groups…

1. Multi-select which actions from the **current** group to copy (default: all).  
2. Multi-select **target** groups (other known groups only; not current).  
3. Append a **snapshot** (new ids) to each target.  
4. If a target already has an action with the **same label**, skip that item for that target and note skips in a short summary.  
5. Persist and refresh.

## Run behavior

Reuse the sequential bulk queue pattern (Checkout & reset / Fetch all):

1. Resolve action + substitute placeholders.  
2. For each favorite path in the group: run the command in that cwd via `GitWorker`.  
3. Card toast + console line per repo; failures do not stop the queue.  
4. Finish summary: `N/M ok`.

### Worker

Add a generic op (e.g. `run_argv` / `shell_git`) that:

- Parses the command string into argv (respect quoted segments, e.g. `-m "my message"`).  
- Runs via the existing subprocess helper with the configured git executable **only if** the first token is `git` (replace with configured path); otherwise reject with a clear error (v1: git-only for safety).  
- Returns `{ ok, output }` like other ops.

Timeout: generous (e.g. 120s) to cover `push`.

### Errors

- Missing remote / nothing to commit / push rejected → show first meaningful stderr line on the card.  
- Parse / non-git command → fail that repo (or fail before queue if the template itself is invalid).

## Out of scope (v1)

- Per-action “confirm before run” flag  
- Non-git shell commands  
- Flet parity  
- Folding Fetch all / Status all / soft Reset into Actions  
- Live sync of edited actions across groups (copy is snapshot only)

## Done when

- Actions menu shows per-group commands; seed works.  
- Edit + Copy dialogs persist correctly.  
- Placeholder dialog works for **Commit**: each run asks for a commit message; that value is applied to every repo in the group.  
- Add / Commit / Push (and custom commands) run sequentially with card + console feedback.  
- Branch Checkout & reset and existing bulk bar still work.
