"""MavenPomWorker — scan local pom.xml files for declared dependencies.

Reads the working tree only (current checkout). No network, no ``mvn`` CLI.
Resolves ``${property}`` placeholders from local ``<properties>`` and parent poms
in the reactor (e.g. ``${flyway.version}`` → ``9.22.3``).
"""

from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET

from devworkbench.workers.base import Worker

_SKIP_DIRS = frozenset(
    {
        ".git",
        "target",
        "node_modules",
        ".idea",
        ".vscode",
        "__pycache__",
        ".venv",
        "venv",
        "build",
        "dist",
        "out",
    }
)
_MAX_POMS = 500
_PROP_RE = re.compile(r"\$\{([^}]+)\}")


def _tag_name(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _child_text(parent: ET.Element, name: str, default: str = "") -> str:
    for child in list(parent):
        if _tag_name(child.tag) == name:
            return (child.text or "").strip() or default
    return default


def _find_child(parent: ET.Element, name: str) -> ET.Element | None:
    for child in list(parent):
        if _tag_name(child.tag) == name:
            return child
    return None


def _parse_properties(root: ET.Element) -> dict[str, str]:
    props: dict[str, str] = {}
    el = _find_child(root, "properties")
    if el is None:
        return props
    for child in list(el):
        name = _tag_name(child.tag)
        if name:
            props[name] = (child.text or "").strip()
    return props


def _parse_dependency(elem: ET.Element, *, managed: bool) -> dict | None:
    group_id = _child_text(elem, "groupId")
    artifact_id = _child_text(elem, "artifactId")
    if not group_id or not artifact_id:
        return None
    return {
        "group_id": group_id,
        "artifact_id": artifact_id,
        "version": _child_text(elem, "version"),
        "scope": _child_text(elem, "scope", "compile") or "compile",
        "type": _child_text(elem, "type", "jar") or "jar",
        "managed": managed,
    }


def _parse_dependencies_block(root: ET.Element, *, managed: bool) -> list[dict]:
    deps: list[dict] = []
    if managed:
        dep_mgmt = _find_child(root, "dependencyManagement")
        container = _find_child(dep_mgmt, "dependencies") if dep_mgmt is not None else None
    else:
        container = _find_child(root, "dependencies")
    if container is None:
        return deps
    for child in list(container):
        if _tag_name(child.tag) != "dependency":
            continue
        row = _parse_dependency(child, managed=managed)
        if row:
            deps.append(row)
    return deps


def _parse_profile_dependencies(root: ET.Element) -> list[dict]:
    """Collect dependencies declared inside ``<profiles>/<profile>/<dependencies>``.`

    Profile dependencies are conditional in Maven (only active when the profile
    is activated), but they are still *declared* in the pom.xml and should be
    visible in a dependency scan so the report is complete.
    """
    deps: list[dict] = []
    profiles = _find_child(root, "profiles")
    if profiles is None:
        return deps
    for profile in list(profiles):
        if _tag_name(profile.tag) != "profile":
            continue
        profile_id = _child_text(profile, "id", "")
        container = _find_child(profile, "dependencies")
        if container is None:
            continue
        for child in list(container):
            if _tag_name(child.tag) != "dependency":
                continue
            row = _parse_dependency(child, managed=False)
            if row:
                row["profile"] = profile_id
                deps.append(row)
    return deps


def _parse_profile_managed_dependencies(root: ET.Element) -> list[dict]:
    """Collect ``<dependencyManagement>`` entries from inside ``<profiles>``.

    Managed versions declared in profiles (e.g. a ``dev`` profile that pins
    extra test-library versions) need to be available for version resolution
    just like root-level ``<dependencyManagement>``.
    """
    deps: list[dict] = []
    profiles = _find_child(root, "profiles")
    if profiles is None:
        return deps
    for profile in list(profiles):
        if _tag_name(profile.tag) != "profile":
            continue
        profile_id = _child_text(profile, "id", "")
        dep_mgmt = _find_child(profile, "dependencyManagement")
        container = _find_child(dep_mgmt, "dependencies") if dep_mgmt is not None else None
        if container is None:
            continue
        for child in list(container):
            if _tag_name(child.tag) != "dependency":
                continue
            row = _parse_dependency(child, managed=True)
            if row:
                row["profile"] = profile_id
                deps.append(row)
    return deps


def parse_pom_file(pom_path: str) -> dict:
    """Parse one pom.xml → module metadata, properties, and declared deps."""
    tree = ET.parse(pom_path)
    root = tree.getroot()
    parent = _find_child(root, "parent")
    parent_info: dict | None = None
    if parent is not None:
        relative = _child_text(parent, "relativePath", "../pom.xml")
        parent_info = {
            "group_id": _child_text(parent, "groupId"),
            "artifact_id": _child_text(parent, "artifactId"),
            "version": _child_text(parent, "version"),
            "relative_path": relative,
        }

    group_id = _child_text(root, "groupId")
    if not group_id and parent_info is not None:
        group_id = parent_info["group_id"]
    artifact_id = _child_text(root, "artifactId") or os.path.basename(os.path.dirname(pom_path))
    version = _child_text(root, "version")
    if not version and parent_info is not None:
        version = parent_info["version"]
    packaging = _child_text(root, "packaging", "jar") or "jar"

    module_dirs: list[str] = []
    modules_el = _find_child(root, "modules")
    if modules_el is not None:
        for child in list(modules_el):
            if _tag_name(child.tag) == "module":
                name = (child.text or "").strip()
                if name:
                    module_dirs.append(name)

    return {
        "pom_path": os.path.abspath(pom_path),
        "module_id": artifact_id,
        "group_id": group_id,
        "version": version,
        "packaging": packaging,
        "module_dirs": module_dirs,
        "properties": _parse_properties(root),
        "parent": parent_info,
        "dependencies": _parse_dependencies_block(root, managed=False),
        "managed_dependencies": _parse_dependencies_block(root, managed=True),
        "profile_dependencies": _parse_profile_dependencies(root),
        "profile_managed_dependencies": _parse_profile_managed_dependencies(root),
    }


def resolve_properties(text: str, props: dict[str, str], *, max_passes: int = 8) -> str:
    """Replace ``${name}`` using ``props``; leave unknown placeholders intact."""
    if not text or "${" not in text:
        return text
    value = text
    for _ in range(max_passes):
        if "${" not in value:
            break

        def repl(match: re.Match) -> str:
            key = match.group(1).strip()
            if key in props and props[key] is not None:
                return str(props[key])
            return match.group(0)

        nxt = _PROP_RE.sub(repl, value)
        if nxt == value:
            break
        value = nxt
    return value


def _resolve_parent_pom(meta: dict, by_path: dict[str, dict], by_ga: dict[tuple, str]) -> str | None:
    """Locate parent pom path inside the scanned reactor (local only)."""
    parent = meta.get("parent")
    if not parent:
        return None
    pom_path = meta["pom_path"]
    base = os.path.dirname(pom_path)
    rel = (parent.get("relative_path") or "../pom.xml").strip()
    if rel:
        candidate = os.path.abspath(os.path.join(base, rel))
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, "pom.xml")
        if candidate in by_path:
            return candidate
        if os.path.isfile(candidate) and candidate not in by_path:
            # Parent outside collected set — try parse on demand later via by_path only
            pass
    ga = (parent.get("group_id") or "", parent.get("artifact_id") or "")
    if ga[0] and ga[1] and ga in by_ga:
        return by_ga[ga]
    # Default Maven relativePath
    fallback = os.path.abspath(os.path.join(base, "..", "pom.xml"))
    if fallback in by_path:
        return fallback
    return None


def _ancestor_chain(pom_path: str, by_path: dict[str, dict], by_ga: dict[tuple, str]) -> list[str]:
    """Root-first list of pom paths from oldest parent to ``pom_path``."""
    chain: list[str] = []
    seen: set[str] = set()
    current: str | None = pom_path
    while current and current not in seen:
        seen.add(current)
        chain.append(current)
        meta = by_path.get(current)
        if meta is None:
            break
        current = _resolve_parent_pom(meta, by_path, by_ga)
    chain.reverse()
    return chain


def _effective_properties(pom_path: str, by_path: dict[str, dict], by_ga: dict[tuple, str]) -> dict[str, str]:
    """Merge properties root→child, then add project.* for this pom."""
    props: dict[str, str] = {}
    chain = _ancestor_chain(pom_path, by_path, by_ga)
    for path in chain:
        meta = by_path.get(path) or {}
        for key, value in (meta.get("properties") or {}).items():
            props[key] = value
    # Resolve property values that themselves reference other properties.
    for _ in range(6):
        changed = False
        for key, value in list(props.items()):
            resolved = resolve_properties(value, props)
            if resolved != value:
                props[key] = resolved
                changed = True
        if not changed:
            break

    meta = by_path.get(pom_path) or {}
    # project.* after custom props so they win for standard Maven interpolation.
    group_id = resolve_properties(meta.get("group_id") or "", props)
    artifact_id = resolve_properties(meta.get("module_id") or "", props)
    version = resolve_properties(meta.get("version") or "", props)
    props.setdefault("project.groupId", group_id)
    props.setdefault("project.artifactId", artifact_id)
    props.setdefault("project.version", version)
    props.setdefault("groupId", group_id)
    props.setdefault("artifactId", artifact_id)
    props.setdefault("version", version)
    props.setdefault("pom.groupId", group_id)
    props.setdefault("pom.artifactId", artifact_id)
    props.setdefault("pom.version", version)
    return props


def _effective_managed_versions(
    pom_path: str, by_path: dict[str, dict], by_ga: dict[tuple, str], props: dict[str, str]
) -> dict[tuple[str, str], str]:
    """GAV version map from dependencyManagement along the parent chain.

    Covers both root-level ``<dependencyManagement>`` and profile-scoped
    ``<dependencyManagement>`` entries from ancestor poms.
    """
    managed: dict[tuple[str, str], str] = {}
    for path in _ancestor_chain(pom_path, by_path, by_ga):
        meta = by_path.get(path) or {}
        path_props = _effective_properties(path, by_path, by_ga) if path != pom_path else props
        for dep in meta.get("managed_dependencies") or []:
            gid = resolve_properties(dep.get("group_id") or "", path_props)
            aid = resolve_properties(dep.get("artifact_id") or "", path_props)
            ver = resolve_properties(dep.get("version") or "", path_props)
            if gid and aid and ver:
                managed[(gid, aid)] = ver
        for dep in meta.get("profile_managed_dependencies") or []:
            gid = resolve_properties(dep.get("group_id") or "", path_props)
            aid = resolve_properties(dep.get("artifact_id") or "", path_props)
            ver = resolve_properties(dep.get("version") or "", path_props)
            if gid and aid and ver:
                managed[(gid, aid)] = ver
    return managed


def _inherited_dependencies(
    pom_path: str, by_path: dict[str, dict], by_ga: dict[tuple, str]
) -> list[dict]:
    """Collect direct ``<dependencies>`` inherited from ancestor poms.

    In Maven, every dependency declared in a parent's ``<dependencies>`` block
    is automatically inherited by all child modules (unless the child
    re-declares the same groupId:artifactId, which overrides the parent's
    version/scope/type).  This function walks the ancestor chain root-first
    and returns the de-duplicated set of inherited dependencies.
    """
    chain = _ancestor_chain(pom_path, by_path, by_ga)
    # Root-first iteration so closer-parent wins over grandparent.
    inherited: dict[tuple[str, str], dict] = {}
    for path in chain:
        if path == pom_path:
            continue  # skip self — own deps handled separately
        meta = by_path.get(path) or {}
        for dep in meta.get("dependencies") or []:
            key = (dep.get("group_id") or "", dep.get("artifact_id") or "")
            if key[0] and key[1]:
                inherited[key] = dep
    return list(inherited.values())


def _inherited_profile_dependencies(
    pom_path: str, by_path: dict[str, dict], by_ga: dict[tuple, str]
) -> list[dict]:
    """Collect profile ``<dependencies>`` inherited from ancestor poms.

    Same inheritance rules as :func:`_inherited_dependencies` but for
    dependencies declared inside ``<profiles>/<profile>/<dependencies>``.
    """
    chain = _ancestor_chain(pom_path, by_path, by_ga)
    inherited: dict[tuple[str, str], dict] = {}
    for path in chain:
        if path == pom_path:
            continue
        meta = by_path.get(path) or {}
        for dep in meta.get("profile_dependencies") or []:
            key = (dep.get("group_id") or "", dep.get("artifact_id") or "")
            if key[0] and key[1]:
                inherited[key] = dep
    return list(inherited.values())


def _collect_poms_via_modules(root_pom: str) -> list[str]:
    """BFS follow ``<modules>`` from ``root_pom``."""
    found: list[str] = []
    seen: set[str] = set()
    queue = [os.path.abspath(root_pom)]
    while queue and len(found) < _MAX_POMS:
        pom = queue.pop(0)
        if pom in seen or not os.path.isfile(pom):
            continue
        seen.add(pom)
        found.append(pom)
        try:
            meta = parse_pom_file(pom)
        except Exception:  # noqa: BLE001 — skip broken poms during discovery
            continue
        base = os.path.dirname(pom)
        for rel in meta.get("module_dirs") or []:
            child = os.path.abspath(os.path.join(base, rel, "pom.xml"))
            if child not in seen:
                queue.append(child)
    return found


def _collect_poms_walk(root: str) -> list[str]:
    found: list[str] = []
    root = os.path.abspath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        if "pom.xml" in filenames:
            found.append(os.path.join(dirpath, "pom.xml"))
            if len(found) >= _MAX_POMS:
                break
    return found


def collect_pom_paths(repo_path: str) -> list[str]:
    """Prefer reactor via root pom modules; fall back to a directory walk."""
    root_pom = os.path.join(os.path.abspath(repo_path), "pom.xml")
    if os.path.isfile(root_pom):
        via_modules = _collect_poms_via_modules(root_pom)
        if via_modules:
            return via_modules
    return _collect_poms_walk(repo_path)


def scan_declared_dependencies(repo_path: str) -> dict:
    """Scan ``repo_path`` and return modules + dependency rows with resolved versions."""
    repo_path = os.path.abspath(repo_path)
    pom_paths = collect_pom_paths(repo_path)
    by_path: dict[str, dict] = {}
    errors: list[dict] = []

    for pom_path in pom_paths:
        try:
            meta = parse_pom_file(pom_path)
            by_path[meta["pom_path"]] = meta
        except Exception as exc:  # noqa: BLE001
            errors.append({"pom_path": pom_path, "error": str(exc)})

    # Also load relative parents that sit just outside the module BFS (same repo).
    extra: list[str] = []
    for meta in list(by_path.values()):
        parent = meta.get("parent")
        if not parent:
            continue
        rel = (parent.get("relative_path") or "../pom.xml").strip()
        if not rel:
            continue
        candidate = os.path.abspath(os.path.join(os.path.dirname(meta["pom_path"]), rel))
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, "pom.xml")
        if (
            candidate not in by_path
            and os.path.isfile(candidate)
            and candidate.startswith(repo_path + os.sep)
        ):
            extra.append(candidate)
    for pom_path in extra:
        try:
            meta = parse_pom_file(pom_path)
            by_path[meta["pom_path"]] = meta
        except Exception as exc:  # noqa: BLE001
            errors.append({"pom_path": pom_path, "error": str(exc)})

    by_ga: dict[tuple, str] = {}
    for path, meta in by_path.items():
        ga = (meta.get("group_id") or "", meta.get("module_id") or "")
        if ga[0] and ga[1]:
            by_ga[ga] = path

    modules: list[dict] = []
    rows: list[dict] = []

    for pom_path in pom_paths:
        abs_pom = os.path.abspath(pom_path)
        meta = by_path.get(abs_pom)
        if meta is None:
            continue
        props = _effective_properties(abs_pom, by_path, by_ga)
        managed_versions = _effective_managed_versions(abs_pom, by_path, by_ga, props)
        module_id = resolve_properties(meta["module_id"], props)
        rel = os.path.relpath(os.path.dirname(abs_pom), repo_path)
        modules.append(
            {
                "module_id": module_id,
                "pom_path": abs_pom,
                "rel_path": "." if rel == "." else rel,
                "group_id": resolve_properties(meta.get("group_id") or "", props),
                "version": resolve_properties(meta.get("version") or "", props),
            }
        )

        # --- Merge own + inherited dependencies -----------------------------------
        # In Maven, parent <dependencies> are automatically inherited by every
        # child module.  A child re-declaring the same GAV overrides the parent.
        own_deps = meta.get("dependencies") or []
        own_profile_deps = meta.get("profile_dependencies") or []
        inherited = _inherited_dependencies(abs_pom, by_path, by_ga)
        inherited_profile = _inherited_profile_dependencies(abs_pom, by_path, by_ga)

        # Build merged map: inherited first, own overrides, profile fills gaps.
        dep_map: dict[tuple[str, str], dict] = {}
        for dep in inherited:
            key = (dep.get("group_id") or "", dep.get("artifact_id") or "")
            if key[0] and key[1]:
                dep_map[key] = dep
        for dep in own_deps:
            key = (dep.get("group_id") or "", dep.get("artifact_id") or "")
            if key[0] and key[1]:
                dep_map[key] = dep  # child overrides parent
        for dep in inherited_profile:
            key = (dep.get("group_id") or "", dep.get("artifact_id") or "")
            if key[0] and key[1] and key not in dep_map:
                dep_map[key] = dep  # profile fills gaps only
        for dep in own_profile_deps:
            key = (dep.get("group_id") or "", dep.get("artifact_id") or "")
            if key[0] and key[1] and key not in dep_map:
                dep_map[key] = dep

        declared: list[dict] = list(dep_map.values())
        # Include managed-only entries from this pom as managed rows.
        declared.extend(meta.get("managed_dependencies") or [])

        for dep in declared:
            group_id = resolve_properties(dep.get("group_id") or "", props)
            artifact_id = resolve_properties(dep.get("artifact_id") or "", props)
            version = resolve_properties(dep.get("version") or "", props)
            if not version:
                version = managed_versions.get((group_id, artifact_id), "")
            scope = resolve_properties(dep.get("scope") or "compile", props) or "compile"
            dep_type = resolve_properties(dep.get("type") or "jar", props) or "jar"
            rows.append(
                {
                    "module_id": module_id,
                    "group_id": group_id,
                    "artifact_id": artifact_id,
                    "version": version,
                    "scope": scope,
                    "type": dep_type,
                    "pom_path": abs_pom,
                    "managed": bool(dep.get("managed")),
                    "profile": dep.get("profile") or "",
                }
            )

    modules.sort(key=lambda m: (m["rel_path"].casefold(), m["module_id"].casefold()))
    rows.sort(
        key=lambda r: (
            r["group_id"].casefold(),
            r["artifact_id"].casefold(),
            r["module_id"].casefold(),
        )
    )
    return {
        "repo_path": repo_path,
        "modules": modules,
        "dependencies": rows,
        "errors": errors,
        "pom_count": len(pom_paths),
    }


def dependencies_to_html(
    *,
    repo_path: str,
    branch: str,
    rows: list[dict],
    title: str = "Maven dependencies",
    initial_filters: dict | None = None,
) -> str:
    """Self-contained interactive HTML report (full scan + client-side filters).

    Embeds every given row. Optional ``initial_filters`` seeds the toolbar
    (search / scope / conflicts / module) so the export mirrors the app view
    while Clear still reveals the full set. No CDN — works offline / file://.
    """
    import html as html_mod
    import json
    from datetime import datetime, timezone

    def esc(value: object) -> str:
        return html_mod.escape(str(value or ""), quote=True)

    filters = {
        "search": str((initial_filters or {}).get("search") or ""),
        "scope": str((initial_filters or {}).get("scope") or ""),
        "conflicts": bool((initial_filters or {}).get("conflicts")),
        "module": str((initial_filters or {}).get("module") or ""),
        "profile": str((initial_filters or {}).get("profile") or ""),
    }
    filters_json = json.dumps(filters, ensure_ascii=False)

    modules = sorted(
        {
            str(row.get("module_id") or "").strip()
            for row in rows
            if str(row.get("module_id") or "").strip()
        }
    )
    module_options = ['<option value="">All modules</option>']
    for module in modules:
        selected = " selected" if module == filters["module"] else ""
        module_options.append(f'<option value="{esc(module)}"{selected}>{esc(module)}</option>')

    scope_choices = ("", "compile", "test", "provided", "runtime", "system", "import")
    scope_options = []
    for scope in scope_choices:
        label = "All scopes" if not scope else scope
        selected = " selected" if scope == filters["scope"] else ""
        scope_options.append(f'<option value="{esc(scope)}"{selected}>{esc(label)}</option>')

    profile_values = sorted(
        {str(row.get("profile") or "").strip() for row in rows}
    )
    profile_options = ['<option value="">All profiles</option>', '<option value="__direct__">direct</option>']
    for prof in profile_values:
        if not prof:
            continue
        selected = " selected" if prof == filters["profile"] else ""
        profile_options.append(f'<option value="{esc(prof)}"{selected}>{esc(prof)}</option>')

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    body_rows = []
    for row in rows:
        conflict = bool(row.get("conflict"))
        managed = bool(row.get("managed"))
        group = str(row.get("group_id") or "")
        artifact = str(row.get("artifact_id") or "")
        version = str(row.get("version") or "")
        scope = str(row.get("scope") or "")
        module = str(row.get("module_id") or "")
        profile = str(row.get("profile") or "")
        cls = ' class="conflict"' if conflict else ""
        body_rows.append(
            f"<tr{cls}"
            f' data-group="{esc(group)}"'
            f' data-artifact="{esc(artifact)}"'
            f' data-version="{esc(version)}"'
            f' data-scope="{esc(scope or "compile")}"'
            f' data-module="{esc(module)}"'
            f' data-profile="{esc(profile)}"'
            f' data-conflict="{"1" if conflict else "0"}"'
            f' data-managed="{"1" if managed else "0"}">'
            f"<td>{esc(group)}</td>"
            f"<td>{esc(artifact)}</td>"
            f"<td>{esc(version)}</td>"
            f"<td>{esc(scope)}</td>"
            f"<td>{esc(module)}</td>"
            f"<td>{esc(profile)}</td>"
            f"<td>{'yes' if managed else ''}</td>"
            "</tr>"
        )
    table_body = "\n".join(body_rows) or (
        '<tr data-empty="1"><td colspan="7">No dependencies in this scan.</td></tr>'
    )
    conflict_checked = " checked" if filters["conflicts"] else ""
    search_value = esc(filters["search"])

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>{esc(title)}</title>
  <style>
    :root {{
      --bg: #f6f7f5; --surface: #fff; --text: #1a1f1c; --muted: #5c6b62;
      --border: #d5ddd7; --head: #eef3ef; --conflict: rgba(196, 69, 69, 0.12);
      --accent: #2f6fdd;
    }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
           margin: 0; padding: 24px; color: var(--text); background: var(--bg); }}
    h1 {{ font-size: 1.4rem; margin: 0 0 6px; }}
    .meta {{ color: var(--muted); font-size: 0.9rem; margin-bottom: 14px; }}
    .toolbar {{
      position: sticky; top: 0; z-index: 2;
      display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
      padding: 10px 0 12px; margin-bottom: 8px;
      background: linear-gradient(var(--bg) 70%, transparent);
    }}
    .toolbar input[type="search"], .toolbar select {{
      font: inherit; padding: 6px 10px; border: 1px solid var(--border);
      border-radius: 6px; background: var(--surface); min-width: 140px;
    }}
    .toolbar input[type="search"] {{ flex: 1 1 220px; min-width: 180px; }}
    .toolbar button, .toolbar label.toggle {{
      font: inherit; padding: 6px 12px; border: 1px solid var(--border);
      border-radius: 6px; background: var(--surface); cursor: pointer;
    }}
    .toolbar label.toggle {{
      display: inline-flex; align-items: center; gap: 6px; user-select: none;
    }}
    .toolbar label.toggle:has(input:checked) {{
      border-color: #c44545; background: var(--conflict); color: #8b2e2e;
    }}
    .toolbar .count {{ color: var(--muted); font-size: 0.88rem; margin-left: auto; }}
    table {{ border-collapse: collapse; width: 100%; background: var(--surface);
             border: 1px solid var(--border); }}
    th, td {{ text-align: left; padding: 8px 10px; border-bottom: 1px solid #e4ebe6;
              font-size: 0.88rem; vertical-align: top; }}
    th {{ background: var(--head); font-size: 0.72rem; text-transform: uppercase;
         letter-spacing: 0.04em; color: var(--muted); cursor: pointer;
         user-select: none; white-space: nowrap; }}
    th:hover {{ color: var(--text); }}
    th[data-sort-dir="asc"]::after {{ content: " \\25B2"; font-size: 0.65em; }}
    th[data-sort-dir="desc"]::after {{ content: " \\25BC"; font-size: 0.65em; }}
    tbody tr {{ cursor: pointer; user-select: none; }}
    tbody tr:hover {{ background: rgba(47, 111, 221, 0.06); }}
    tbody tr.conflict {{ background: var(--conflict); }}
    tbody tr.conflict:hover {{ background: rgba(196, 69, 69, 0.18); }}
    tbody tr.selected {{ background: rgba(47, 111, 221, 0.18); outline: 1px solid var(--accent); }}
    tbody tr.conflict.selected {{ background: rgba(47, 111, 221, 0.22); }}
    tr[hidden] {{ display: none; }}
    code {{ font-family: ui-monospace, Menlo, monospace; font-size: 0.84rem; }}
    @media print {{
      .toolbar {{ position: static; background: none; }}
      .toolbar button, .toolbar label.toggle {{ display: none; }}
      tbody tr.selected {{ outline: none; }}
    }}
  </style>
</head>
<body>
  <h1>{esc(title)}</h1>
  <div class="meta">
    <div><strong>Repo:</strong> <code>{esc(repo_path)}</code></div>
    <div><strong>Branch:</strong> {esc(branch or "—")}</div>
    <div><strong>Generated:</strong> {esc(now)}</div>
    <div><strong>Embedded rows:</strong> {len(rows)}</div>
  </div>
  <div class="toolbar" id="toolbar">
    <input type="search" id="search" placeholder="Search groupId, artifactId, version, module…"
           value="{search_value}" autocomplete="off"/>
    <select id="scope" aria-label="Scope">{"".join(scope_options)}</select>
    <select id="profile" aria-label="Profile">{"".join(profile_options)}</select>
    <label class="toggle"><input type="checkbox" id="conflicts"{conflict_checked}/> Conflicts</label>
    <select id="module" aria-label="Module">{"".join(module_options)}</select>
    <button type="button" id="clear">Clear</button>
    <button type="button" id="copy-tsv" title="Copy selected rows, or all filtered if none selected (TSV)">Copy TSV</button>
    <button type="button" id="copy-md" title="Copy selected rows, or all filtered if none selected (Markdown)">Copy Markdown</button>
    <span class="count" id="count">{len(rows)} shown / {len(rows)} total</span>
  </div>
  <table id="deps">
    <thead>
      <tr>
        <th data-col="group">groupId (family)</th>
        <th data-col="artifact">artifactId</th>
        <th data-col="version">Version</th>
        <th data-col="scope">Scope</th>
        <th data-col="module">Module</th>
        <th data-col="profile">Profile</th>
        <th data-col="managed">Managed</th>
      </tr>
    </thead>
    <tbody>
{table_body}
    </tbody>
  </table>
  <script type="application/json" id="initial-filters">{filters_json}</script>
  <script>
(function () {{
  var search = document.getElementById("search");
  var scope = document.getElementById("scope");
  var profileSel = document.getElementById("profile");
  var conflicts = document.getElementById("conflicts");
  var moduleSel = document.getElementById("module");
  var clearBtn = document.getElementById("clear");
  var copyTsvBtn = document.getElementById("copy-tsv");
  var copyMdBtn = document.getElementById("copy-md");
  var countEl = document.getElementById("count");
  var tbody = document.querySelector("#deps tbody");
  var headers = document.querySelectorAll("#deps thead th[data-col]");
  var sortCol = null;
  var sortDir = "asc";
  var lastAnchor = null;

  function rows() {{
    return Array.prototype.slice.call(tbody.querySelectorAll("tr:not([data-empty])"));
  }}

  function visibleRows() {{
    return rows().filter(function (tr) {{ return !tr.hidden; }});
  }}

  function selectedRows() {{
    return visibleRows().filter(function (tr) {{ return tr.classList.contains("selected"); }});
  }}

  function clearSelection() {{
    rows().forEach(function (tr) {{ tr.classList.remove("selected"); }});
    lastAnchor = null;
    updateCount();
  }}

  function updateCount() {{
    var shown = visibleRows().length;
    var total = rows().length;
    var sel = selectedRows().length;
    countEl.textContent = shown + " shown / " + total + " total"
      + (sel ? (" · " + sel + " selected") : "");
  }}

  function cellText(tr, key) {{
    if (key === "managed") return tr.getAttribute("data-managed") === "1" ? "yes" : "";
    return tr.getAttribute("data-" + key) || "";
  }}

  function rowsForCopy() {{
    var selected = selectedRows();
    return selected.length ? selected : visibleRows();
  }}

  function tableText(fmt) {{
    var cols = ["group", "artifact", "version", "scope", "module", "profile", "managed"];
    var labels = ["groupId", "artifactId", "version", "scope", "module", "profile", "managed"];
    var list = rowsForCopy();
    if (fmt === "markdown") {{
      var lines = [
        "| " + labels.join(" | ") + " |",
        "| " + labels.map(function () {{ return "---"; }}).join(" | ") + " |"
      ];
      list.forEach(function (tr) {{
        var vals = cols.map(function (c) {{
          return cellText(tr, c).replace(/\\|/g, "\\\\|");
        }});
        lines.push("| " + vals.join(" | ") + " |");
      }});
      return lines.join("\\n") + "\\n";
    }}
    var out = [labels.join("\\t")];
    list.forEach(function (tr) {{
      out.push(cols.map(function (c) {{ return cellText(tr, c); }}).join("\\t"));
    }});
    return out.join("\\n") + "\\n";
  }}

  function flashCopy(btn, n) {{
    var prev = btn.textContent;
    btn.textContent = "Copied " + n;
    setTimeout(function () {{ btn.textContent = prev; }}, 1400);
  }}

  function copyTable(fmt) {{
    var list = rowsForCopy();
    var text = tableText(fmt);
    var btn = fmt === "markdown" ? copyMdBtn : copyTsvBtn;
    if (navigator.clipboard && navigator.clipboard.writeText) {{
      navigator.clipboard.writeText(text).then(function () {{
        flashCopy(btn, list.length);
      }}).catch(function () {{
        window.prompt("Copy this table:", text);
      }});
    }} else {{
      window.prompt("Copy this table:", text);
    }}
  }}

  function applyFilters() {{
    var q = (search.value || "").trim().toLowerCase();
    var sc = scope.value || "";
    var prof = profileSel.value || "";
    var mod = moduleSel.value || "";
    var onlyConflict = conflicts.checked;
    rows().forEach(function (tr) {{
      var hay = (
        (tr.getAttribute("data-group") || "") + " " +
        (tr.getAttribute("data-artifact") || "") + " " +
        (tr.getAttribute("data-version") || "") + " " +
        (tr.getAttribute("data-module") || "")
      ).toLowerCase();
      var ok = true;
      if (q && hay.indexOf(q) === -1) ok = false;
      if (ok && sc && (tr.getAttribute("data-scope") || "compile") !== sc) ok = false;
      if (ok && prof) {{
        var rowProf = tr.getAttribute("data-profile") || "";
        if (prof === "__direct__") {{ if (rowProf !== "") ok = false; }}
        else {{ if (rowProf !== prof) ok = false; }}
      }}
      if (ok && mod && (tr.getAttribute("data-module") || "") !== mod) ok = false;
      if (ok && onlyConflict && tr.getAttribute("data-conflict") !== "1") ok = false;
      tr.hidden = !ok;
      if (!ok) tr.classList.remove("selected");
    }});
    updateCount();
  }}

  function onRowClick(ev) {{
    var tr = ev.target.closest("tr");
    if (!tr || !tbody.contains(tr) || tr.hidden || tr.getAttribute("data-empty")) return;
    var visible = visibleRows();
    var idx = visible.indexOf(tr);
    if (idx < 0) return;
    var multi = ev.metaKey || ev.ctrlKey;
    var range = ev.shiftKey && lastAnchor != null;
    if (range) {{
      var a = visible.indexOf(lastAnchor);
      if (a < 0) a = idx;
      var lo = Math.min(a, idx), hi = Math.max(a, idx);
      if (!multi) {{
        visible.forEach(function (r) {{ r.classList.remove("selected"); }});
      }}
      for (var i = lo; i <= hi; i++) visible[i].classList.add("selected");
    }} else if (multi) {{
      tr.classList.toggle("selected");
      lastAnchor = tr;
    }} else {{
      visible.forEach(function (r) {{ r.classList.remove("selected"); }});
      tr.classList.add("selected");
      lastAnchor = tr;
    }}
    updateCount();
  }}

  function sortBy(col) {{
    if (sortCol === col) {{
      sortDir = sortDir === "asc" ? "desc" : "asc";
    }} else {{
      sortCol = col;
      sortDir = "asc";
    }}
    headers.forEach(function (th) {{
      if (th.getAttribute("data-col") === col) th.setAttribute("data-sort-dir", sortDir);
      else th.removeAttribute("data-sort-dir");
    }});
    var list = rows();
    list.sort(function (a, b) {{
      var av, bv;
      if (col === "managed") {{
        av = a.getAttribute("data-managed") || "0";
        bv = b.getAttribute("data-managed") || "0";
      }} else {{
        av = (a.getAttribute("data-" + col) || "").toLowerCase();
        bv = (b.getAttribute("data-" + col) || "").toLowerCase();
      }}
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    }});
    list.forEach(function (tr) {{ tbody.appendChild(tr); }});
  }}

  search.addEventListener("input", applyFilters);
  scope.addEventListener("change", applyFilters);
  profileSel.addEventListener("change", applyFilters);
  conflicts.addEventListener("change", applyFilters);
  moduleSel.addEventListener("change", applyFilters);
  clearBtn.addEventListener("click", function () {{
    search.value = "";
    scope.value = "";
    profileSel.value = "";
    conflicts.checked = false;
    moduleSel.value = "";
    clearSelection();
    applyFilters();
  }});
  copyTsvBtn.addEventListener("click", function () {{ copyTable("tsv"); }});
  copyMdBtn.addEventListener("click", function () {{ copyTable("markdown"); }});
  tbody.addEventListener("click", onRowClick);
  headers.forEach(function (th) {{
    th.addEventListener("click", function () {{
      sortBy(th.getAttribute("data-col"));
    }});
  }});

  try {{
    var raw = document.getElementById("initial-filters");
    var init = raw ? JSON.parse(raw.textContent || "{{}}") : {{}};
    if (init.search != null) search.value = init.search;
    if (init.scope != null) scope.value = init.scope;
    if (init.profile != null) profileSel.value = init.profile;
    if (init.module != null) moduleSel.value = init.module;
    conflicts.checked = !!init.conflicts;
  }} catch (e) {{}}
  applyFilters();
}})();
  </script>
</body>
</html>
"""


class MavenPomWorker(Worker):
    """Scan a local repo path for declared Maven dependencies."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path

    def work(self):
        return scan_declared_dependencies(self._path)


def enrich_dependency_rows(rows: list[dict]) -> list[dict]:
    """Add used_in_n, used_in_modules, conflict flags to each row (by groupId+artifactId)."""
    by_ga: dict[tuple[str, str], list[dict]] = {}
    for row in rows:
        key = (str(row.get("group_id") or ""), str(row.get("artifact_id") or ""))
        by_ga.setdefault(key, []).append(row)

    enriched: list[dict] = []
    for row in rows:
        key = (str(row.get("group_id") or ""), str(row.get("artifact_id") or ""))
        peers = by_ga.get(key) or [row]
        modules = sorted({str(r.get("module_id") or "") for r in peers if r.get("module_id")})
        versions = sorted({str(r.get("version") or "") for r in peers if str(r.get("version") or "").strip()})
        item = dict(row)
        item["used_in_n"] = len(modules)
        item["used_in_modules"] = modules
        item["conflict"] = len(versions) >= 2
        item["conflict_versions"] = versions
        enriched.append(item)
    return enriched


def family_prefix(group_id: str, segments: int = 2) -> str:
    parts = [p for p in str(group_id or "").split(".") if p]
    if len(parts) <= segments:
        return str(group_id or "")
    return ".".join(parts[:segments])


def top_families(rows: list[dict], *, limit: int = 10) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        fam = family_prefix(str(row.get("group_id") or ""))
        if not fam:
            continue
        counts[fam] = counts.get(fam, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    return ranked[:limit]


def dependencies_table_text(rows: list[dict], *, fmt: str = "tsv") -> str:
    """Plain table of filtered deps for clipboard paste (email / chat).

    ``fmt`` is ``tsv`` (tab-separated, Excel-friendly) or ``markdown``.
    Columns match the interactive HTML report.
    """
    headers = ("groupId", "artifactId", "version", "scope", "module", "profile", "managed")

    def cells(row: dict) -> tuple[str, ...]:
        return (
            str(row.get("group_id") or ""),
            str(row.get("artifact_id") or ""),
            str(row.get("version") or ""),
            str(row.get("scope") or ""),
            str(row.get("module_id") or ""),
            str(row.get("profile") or ""),
            "yes" if row.get("managed") else "",
        )

    if fmt == "markdown":
        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        for row in rows:
            safe = [c.replace("|", "\\|") for c in cells(row)]
            lines.append("| " + " | ".join(safe) + " |")
        return "\n".join(lines) + ("\n" if lines else "")

    # Default: TSV
    lines = ["\t".join(headers)]
    for row in rows:
        lines.append("\t".join(cells(row)))
    return "\n".join(lines) + ("\n" if lines else "")


def dependencies_to_csv(rows: list[dict]) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["groupId", "artifactId", "version", "scope", "module", "profile", "managed", "used_in_n", "conflict"]
    )
    for row in rows:
        writer.writerow(
            [
                row.get("group_id") or "",
                row.get("artifact_id") or "",
                row.get("version") or "",
                row.get("scope") or "",
                row.get("module_id") or "",
                row.get("profile") or "",
                "yes" if row.get("managed") else "",
                row.get("used_in_n") if row.get("used_in_n") is not None else "",
                "yes" if row.get("conflict") else "",
            ]
        )
    return buf.getvalue()


def gav_string(row: dict) -> str:
    return (
        f"{row.get('group_id') or ''}:"
        f"{row.get('artifact_id') or ''}:"
        f"{row.get('version') or ''}"
    )


def maven_xml_snippet(row: dict) -> str:
    lines = [
        "<dependency>",
        f"  <groupId>{row.get('group_id') or ''}</groupId>",
        f"  <artifactId>{row.get('artifact_id') or ''}</artifactId>",
    ]
    if row.get("version"):
        lines.append(f"  <version>{row.get('version')}</version>")
    scope = row.get("scope") or "compile"
    if scope and scope != "compile":
        lines.append(f"  <scope>{scope}</scope>")
    lines.append("</dependency>")
    return "\n".join(lines)


def list_local_branches(repo_path: str, executable: str = "git") -> list[str]:
    import subprocess

    try:
        proc = subprocess.run(
            [executable, "branch", "--format=%(refname:short)"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def current_branch_name(repo_path: str, executable: str = "git") -> str:
    import subprocess

    try:
        proc = subprocess.run(
            [executable, "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    name = (proc.stdout or "").strip()
    return "" if name == "HEAD" else name


def scan_repo_at_ref(repo_path: str, ref: str, executable: str = "git") -> dict:
    """Scan declared deps for ``ref`` via a temporary detached worktree (local only)."""
    import shutil
    import subprocess
    import tempfile

    repo_path = os.path.abspath(repo_path)
    tmp = tempfile.mkdtemp(prefix="dwb-maven-wt-")
    try:
        add = subprocess.run(
            [executable, "worktree", "add", "--detach", tmp, ref],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if add.returncode != 0:
            raise RuntimeError((add.stderr or add.stdout or "worktree add failed").strip())
        result = scan_declared_dependencies(tmp)
        result["ref"] = ref
        return result
    finally:
        subprocess.run(
            [executable, "worktree", "remove", "--force", tmp],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        shutil.rmtree(tmp, ignore_errors=True)


def diff_dependency_scans(scan_a: dict, scan_b: dict) -> list[dict]:
    """Compare two scans by groupId+artifactId (versions aggregated across modules)."""

    def index(scan: dict) -> dict[tuple[str, str], set[str]]:
        out: dict[tuple[str, str], set[str]] = {}
        for row in scan.get("dependencies") or []:
            key = (str(row.get("group_id") or ""), str(row.get("artifact_id") or ""))
            if not key[0] or not key[1]:
                continue
            out.setdefault(key, set())
            ver = str(row.get("version") or "").strip()
            if ver:
                out[key].add(ver)
        return out

    a = index(scan_a)
    b = index(scan_b)
    keys = sorted(set(a) | set(b), key=lambda k: (k[0].casefold(), k[1].casefold()))
    rows: list[dict] = []
    for key in keys:
        va = sorted(a.get(key) or [])
        vb = sorted(b.get(key) or [])
        if key not in a:
            change = "added"
        elif key not in b:
            change = "removed"
        elif va != vb:
            change = "version_changed"
        else:
            continue
        rows.append(
            {
                "group_id": key[0],
                "artifact_id": key[1],
                "version_a": ", ".join(va) if va else "—",
                "version_b": ", ".join(vb) if vb else "—",
                "change": change,
            }
        )
    return rows


class MavenCompareWorker(Worker):
    """Compare declared dependencies between two local git refs via worktrees."""

    def __init__(
        self,
        path: str,
        branch_a: str,
        branch_b: str,
        executable: str = "git",
    ) -> None:
        super().__init__()
        self._path = path
        self._branch_a = branch_a
        self._branch_b = branch_b
        self._executable = executable

    def work(self):
        scan_a = scan_repo_at_ref(self._path, self._branch_a, self._executable)
        scan_b = scan_repo_at_ref(self._path, self._branch_b, self._executable)
        return {
            "branch_a": self._branch_a,
            "branch_b": self._branch_b,
            "diff": diff_dependency_scans(scan_a, scan_b),
            "count_a": len(scan_a.get("dependencies") or []),
            "count_b": len(scan_b.get("dependencies") or []),
        }


# ---------------------------------------------------------------------------
# Maven dependency tree (transitive)
# ---------------------------------------------------------------------------

# Matches lines like:  +- org.springframework.boot:spring-boot:jar:3.2.1:compile
# or:                 |  \- org.slf4j:slf4j-api:jar:2.0.9:compile
# or:                 (omitted for conflict with 3.2.1)
_TREE_DEP_RE = re.compile(
    r"^\[INFO\]\s+([|\\s+\-]+)\s+"
    r"(\S+?):(\S+?):(\S+?):(\S+?)(?::(\S+))?"
    r"(?:\s+(\(omitted.*\)))?"
)
_TREE_OMITTED_RE = re.compile(
    r"^\[INFO\]\s+([|\\s+\-]+)\s+\(omitted(.*)\)"
)


def _parse_tree_output(output: str) -> list[dict]:
    """Parse ``mvn dependency:tree`` text output into a nested node list.

    Returns a list of root-level tree nodes.  Each node is::

        {
            "group_id": str,
            "artifact_id": str,
            "version": str,
            "scope": str,        # compile, test, provided, …
            "omitted": bool,
            "omitted_reason": str,
            "children": [...],
        }
    """
    root_nodes: list[dict] = []
    # Stack tracks (indent_level, parent_list) for building the hierarchy.
    stack: list[tuple[int, list[dict]]] = [(-1, root_nodes)]
    last_node: dict | None = None

    for line in output.splitlines():
        if not line.startswith("[INFO]"):
            continue

        # Check for omitted-only line (no GAV, just reason)
        omit_match = _TREE_OMITTED_RE.match(line)
        if omit_match and last_node is not None:
            reason = (omit_match.group(2) or "").strip().rstrip(")")
            last_node["omitted"] = True
            last_node["omitted_reason"] = f"omitted{reason}"
            continue

        dep_match = _TREE_DEP_RE.match(line)
        if not dep_match:
            continue

        prefix = dep_match.group(1)
        group_id = dep_match.group(2)
        artifact_id = dep_match.group(3)
        _packaging = dep_match.group(4)  # jar, pom, etc. — not displayed
        version = dep_match.group(5)
        scope = dep_match.group(6) or "compile"
        omitted_hint = (dep_match.group(7) or "").strip()

        # Compute depth from prefix: each "|  " or "   " segment = +1,
        # each "+- " or "\- " = +1.  Simplified: count non-space, non-| chars.
        depth = 0
        for ch in prefix:
            if ch in ("+", "\\"):
                depth += 1

        node: dict = {
            "group_id": group_id,
            "artifact_id": artifact_id,
            "version": version or "",
            "scope": scope,
            "omitted": bool(omitted_hint),
            "omitted_reason": omitted_hint.rstrip(")") if omitted_hint else "",
            "children": [],
        }
        last_node = node

        # Walk stack to find the right parent.
        while len(stack) > 1 and stack[-1][0] >= depth:
            stack.pop()
        stack[-1][1].append(node)
        stack.append((depth, node["children"]))

    return root_nodes


def resolve_maven_tree(
    repo_path: str,
    *,
    verbose: bool = False,
    executable: str = "mvn",
    extra_args: str = "",
) -> dict:
    """Run ``mvn dependency:tree`` and return structured output."""
    import shlex

    exe_parts = shlex.split(executable)
    cmd: list[str] = exe_parts + ["dependency:tree", "-DoutputType=text"]
    if extra_args:
        cmd.extend(shlex.split(extra_args))
    if verbose:
        cmd.append("-Dverbose")
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        return {
            "tree": [],
            "total_deps": 0,
            "omitted_deps": 0,
            "mvn_error": "Maven not found. Install Maven and ensure it's on PATH.",
        }
    except subprocess.TimeoutExpired:
        return {
            "tree": [],
            "total_deps": 0,
            "omitted_deps": 0,
            "mvn_error": "Maven command timed out after 120s.",
        }

    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "Maven failed").strip()
        # Truncate very long errors
        if len(err) > 500:
            err = err[:500] + "..."
        return {
            "tree": [],
            "total_deps": 0,
            "omitted_deps": 0,
            "mvn_error": err,
        }

    tree = _parse_tree_output(proc.stdout or "")

    def _count(nodes: list[dict]) -> tuple[int, int]:
        total = 0
        omitted = 0
        for n in nodes:
            total += 1
            if n.get("omitted"):
                omitted += 1
            t, o = _count(n.get("children") or [])
            total += t
            omitted += o
        return total, omitted

    total, omitted = _count(tree)
    return {
        "tree": tree,
        "total_deps": total,
        "omitted_deps": omitted,
        "mvn_error": "",
    }


class MavenTreeWorker(Worker):
    """Run ``mvn dependency:tree`` for a local repo."""

    def __init__(
        self,
        path: str,
        *,
        verbose: bool = False,
        executable: str = "mvn",
        extra_args: str = "",
    ) -> None:
        super().__init__()
        self._path = path
        self._verbose = verbose
        self._executable = executable
        self._extra_args = extra_args

    def work(self):
        return resolve_maven_tree(
            self._path,
            verbose=self._verbose,
            executable=self._executable,
            extra_args=self._extra_args,
        )
