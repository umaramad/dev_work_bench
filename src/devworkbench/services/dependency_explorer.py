"""Group-level Dependency Explorer services (pure Python — no Qt).

Reuses the Maven scan produced by ``devworkbench.workers.maven_worker``
(no POM is re-parsed here) to provide:

* **J2EE classification** — explainable, evidence-based scoring from Maven
  packaging (war/ear), Java EE / Jakarta EE API dependencies, deployment
  descriptors (web.xml, application.xml, WebSphere files), webapp layout and
  EAR/WAR module structure. Spring Boot markers are recorded as notes and are
  *never* counted as J2EE evidence. Verdicts: ``j2ee`` / ``non_j2ee`` /
  ``unknown`` with a confidence and the full evidence trail; manual
  overrides (per repository path) replace the verdict and survive rescans.
* **ArtifactIndex** — an immutable-by-convention, in-memory index over every
  dependency row of every scanned repository. Search matches JAR filenames,
  groupId/artifactId, full coordinates, versions, modules and scopes with a
  precomputed haystack, so keystrokes never touch the filesystem. Duplicate
  detection uses normalized Maven coordinates (never JAR filenames) and
  versions are ordered with a Maven-style comparator.
* **Usage rows** — one record per (repository, module, coordinate) carrying
  declared vs effective version, version provenance (explicit / managed /
  unresolved / none), scope, type, classifier, optional flag, declaration
  origin (direct / inherited / profile / managed), the source pom and
  whether the artifact exists in the module's build output.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable

# ------------------------------------------------------------------ constants

VERDICT_J2EE = "j2ee"
VERDICT_NON_J2EE = "non_j2ee"
VERDICT_UNKNOWN = "unknown"

CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_MANUAL = "manual"

OVERRIDE_J2EE = "j2ee"
OVERRIDE_NON_J2EE = "non_j2ee"
OVERRIDE_AUTO = "auto"

VERDICT_LABELS = {
    VERDICT_J2EE: "J2EE",
    VERDICT_NON_J2EE: "Non-J2EE",
    VERDICT_UNKNOWN: "Unknown",
}

# A module/repo scores J2EE at >= this total and Non-J2EE at <= its negative.
J2EE_SCORE_THRESHOLD = 2.0

# Probe dirs skipped while walking a module tree for deployment descriptors.
_PROBE_SKIP_DIRS = frozenset(
    {".git", "target", "node_modules", "__pycache__", ".venv", "venv", ".idea", ".vscode"}
)

# groupId → J2EE/Jakarta EE API family. Presence of a *used* dependency from
# one of these groups is positive evidence (managed-only entries count less —
# dependencyManagement alone does not prove usage).
_EE_API_GROUPS = frozenset(
    {
        "javax.servlet",
        "javax.servlet.jsp",
        "javax.ejb",
        "javax.persistence",
        "javax.ws.rs",
        "javax.xml.ws",
        "javax.jws",
        "javax.annotation",
        "javax.enterprise",
        "javax.decorator",
        "javax.el",
        "javax.faces",
        "javax.mail",
        "javax.jms",
        "javax.activation",
        "javax.resource",
        "javax.security.enterprise",
        "jakarta.servlet",
        "jakarta.servlet.jsp",
        "jakarta.ejb",
        "jakarta.persistence",
        "jakarta.ws.rs",
        "jakarta.xml.ws",
        "jakarta.jws",
        "jakarta.annotation",
        "jakarta.enterprise",
        "jakarta.decorator",
        "jakarta.el",
        "jakarta.faces",
        "jakarta.mail",
        "jakarta.jms",
        "jakarta.activation",
        "jakarta.resource",
        "jakarta.platform",
    }
)

# artifactId → EE API family (covers artifacts whose groupId alone is generic,
# e.g. ``org.jboss.spec``:…-spec jars are handled by their artifact names).
_EE_API_ARTIFACTS = frozenset(
    {
        "javaee-api",
        "javaee-web-api",
        "javaee-specified-api",
        "jakartaee-api",
        "jakartaee-web-api",
        "servlet-api",
        "jsp-api",
        "el-api",
        "ejb-api",
        "jta",
        "jakarta.transaction-api",
        "javaee-jaxrs-api",
        "jaxrs-api",
        "jaxws-api",
        "jsr311-api",
        "jsr250-api",
        "cdi-api",
        "javax.inject",
        "javaee-security-api",
    }
)

# Spring Boot markers — recorded as evidence *notes* with zero weight.
_SPRING_BOOT_GROUPS = frozenset({"org.springframework.boot"})
_SPRING_BOOT_PARENTS = frozenset({"spring-boot-starter-parent", "spring-boot-dependencies"})
_SPRING_BOOT_PLUGINS = frozenset({"spring-boot-maven-plugin"})

_DESCRIPTOR_WEIGHT = 2.0
_WEBAPP_DIR_WEIGHT = 1.0
_EE_API_WEIGHT = 1.5
_MANAGED_EE_API_WEIGHT = 0.5
_PACKAGING_WAR_WEIGHT = 3.0
_PACKAGING_EAR_WEIGHT = 3.0
_PACKAGING_JAR_WEIGHT = -2.0
_EAR_STRUCTURE_WEIGHT = 2.0


# ------------------------------------------------------------------ evidence


@dataclass(frozen=True)
class Evidence:
    """One explainable reason behind a classification verdict."""

    kind: str
    weight: float
    description: str
    detail: str = ""


@dataclass
class Classification:
    """Verdict + confidence + evidence for a module or repository."""

    verdict: str = VERDICT_UNKNOWN
    confidence: str = CONFIDENCE_LOW
    score: float = 0.0
    evidence: list[Evidence] = field(default_factory=list)
    base_verdict: str = ""      # verdict computed from evidence (pre-override)
    base_confidence: str = ""
    overridden: bool = False
    override: str = ""          # "" | "j2ee" | "non_j2ee" (manual override applied)
    scope: str = "repository"   # "repository" | "module"
    module_id: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "Classification":
        if isinstance(data, cls):
            return data
        if not isinstance(data, dict):
            return cls()
        raw = dict(data)
        raw["evidence"] = [
            e if isinstance(e, Evidence) else Evidence(**e)
            for e in raw.get("evidence") or []
        ]
        known = {f for f in cls.__dataclass_fields__}  # noqa: SIM118 — dataclass fields
        return cls(**{k: v for k, v in raw.items() if k in known})

    def summary(self) -> str:
        """One-line human summary with the strongest evidence."""
        top = sorted(self.evidence, key=lambda e: -abs(e.weight))[:3]
        parts = [e.description for e in top if e.description]
        if self.overridden:
            parts.insert(0, f"manual override ({self.override})")
        return " · ".join(parts) if parts else "no evidence found"


def _confidence_for(score: float, verdict: str) -> str:
    magnitude = abs(score)
    if verdict == VERDICT_UNKNOWN:
        return CONFIDENCE_LOW
    if magnitude >= 4.0:
        return CONFIDENCE_HIGH
    return CONFIDENCE_MEDIUM


# ------------------------------------------------------------------- probing


def probe_module_dir(module_dir: str, *, max_entries: int = 4000) -> dict:
    """Bounded filesystem probe for J2EE evidence under one directory.

    Looks for web.xml, META-INF/application.xml, WebSphere descriptors
    (``ibm-*.xml`` / ``ibm-*.xmi``) and ``webapp`` directories. Walks are
    capped so a pathological tree can never stall a scan.
    """
    found: dict[str, list[str]] = {
        "web_xml": [],
        "application_xml": [],
        "websphere": [],
        "webapp_dirs": [],
    }
    if not module_dir or not os.path.isdir(module_dir):
        return found
    entries = 0
    for dirpath, dirnames, filenames in os.walk(module_dir):
        dirnames[:] = [
            d for d in dirnames if d not in _PROBE_SKIP_DIRS and not d.startswith(".")
        ]
        if os.path.basename(dirpath) == "webapp":
            found["webapp_dirs"].append(dirpath)
        for name in filenames:
            lower = name.lower()
            full = os.path.join(dirpath, name)
            if lower == "web.xml":
                found["web_xml"].append(full)
            elif lower == "application.xml" and os.path.basename(dirpath).lower() == "meta-inf":
                found["application_xml"].append(full)
            elif lower.startswith("ibm-") or lower in ("websphere.xml", "was-manifest.mf"):
                found["websphere"].append(full)
        entries += 1
        if entries >= max_entries:
            break
    return found


# ------------------------------------------------------------- classification


def _module_coordinates(rows: Iterable[dict]) -> list[tuple[str, str, bool]]:
    """(groupId, artifactId, managed-only) triples for a module's rows."""
    managed_only: dict[tuple[str, str], bool] = {}
    for row in rows:
        key = (str(row.get("group_id") or "").strip(), str(row.get("artifact_id") or "").strip())
        if not key[0] or not key[1]:
            continue
        is_managed = bool(row.get("managed")) or str(row.get("origin") or "") == "managed"
        managed_only[key] = managed_only.get(key, True) and is_managed
    return [(g, a, m) for (g, a), m in managed_only.items()]


def _spring_boot_markers(module: dict) -> list[Evidence]:
    markers: list[Evidence] = []
    parent = module.get("parent") or {}
    parent_artifact = str(parent.get("artifact_id") or "")
    if parent_artifact in _SPRING_BOOT_PARENTS:
        markers.append(
            Evidence(
                kind="spring-boot-parent",
                weight=0.0,
                description="Spring Boot parent POM (not J2EE evidence)",
                detail=parent_artifact,
            )
        )
    for plugin in module.get("plugins") or []:
        if str(plugin) in _SPRING_BOOT_PLUGINS:
            markers.append(
                Evidence(
                    kind="spring-boot-plugin",
                    weight=0.0,
                    description="spring-boot-maven-plugin (not J2EE evidence)",
                    detail=str(plugin),
                )
            )
    return markers


def classify_module(
    module: dict,
    rows: list[dict],
    probe: dict,
) -> Classification:
    """Classify one Maven module from packaging, descriptors and APIs.

    ``module`` is a ``parse_pom_file`` meta dict (packaging, parent, plugins,
    module_dirs); ``rows`` are that module's dependency rows from the scan;
    ``probe`` is the result of :func:`probe_module_dir`.
    """
    evidence: list[Evidence] = []
    score = 0.0

    # ``parse_pom_file`` defaults missing <packaging> to "jar"; ""/"none" is
    # only used by synthetic entries (repos without a Maven reactor).
    packaging = str(module.get("packaging") or "").strip().lower()
    if packaging == "war":
        score += _PACKAGING_WAR_WEIGHT
        evidence.append(
            Evidence("packaging-war", _PACKAGING_WAR_WEIGHT, "Maven packaging is war", packaging)
        )
    elif packaging == "ear":
        score += _PACKAGING_EAR_WEIGHT
        evidence.append(
            Evidence("packaging-ear", _PACKAGING_EAR_WEIGHT, "Maven packaging is ear", packaging)
        )
    elif packaging == "bundle":
        evidence.append(
            Evidence("packaging-bundle", 0.0, "OSGi bundle packaging (neutral)", packaging)
        )
    elif packaging in ("", "none", "unknown"):
        evidence.append(
            Evidence("no-packaging", 0.0, "No Maven packaging information", "")
        )
    else:
        score += _PACKAGING_JAR_WEIGHT
        evidence.append(
            Evidence(
                "packaging-jar",
                _PACKAGING_JAR_WEIGHT,
                f"Maven packaging is {packaging or 'jar'} (no Java EE web profile)",
                packaging,
            )
        )

    for path in (probe.get("web_xml") or [])[:1]:
        score += _DESCRIPTOR_WEIGHT
        evidence.append(
            Evidence("descriptor-web-xml", _DESCRIPTOR_WEIGHT, "web.xml deployment descriptor", path)
        )
    for path in (probe.get("application_xml") or [])[:1]:
        score += _DESCRIPTOR_WEIGHT
        evidence.append(
            Evidence(
                "descriptor-application-xml",
                _DESCRIPTOR_WEIGHT,
                "META-INF/application.xml (EAR descriptor)",
                path,
            )
        )
    for path in (probe.get("websphere") or [])[:1]:
        score += _DESCRIPTOR_WEIGHT
        evidence.append(
            Evidence("descriptor-websphere", _DESCRIPTOR_WEIGHT, "WebSphere deployment descriptor", path)
        )
    for path in (probe.get("webapp_dirs") or [])[:1]:
        score += _WEBAPP_DIR_WEIGHT
        evidence.append(
            Evidence("webapp-directory", _WEBAPP_DIR_WEIGHT, "webapp directory (web assets)", path)
        )

    ee_api_weight = 0.0
    ee_details: list[str] = []
    for group_id, artifact_id, managed_only in _module_coordinates(rows):
        is_ee = group_id in _EE_API_GROUPS or artifact_id in _EE_API_ARTIFACTS
        if not is_ee:
            continue
        weight = _MANAGED_EE_API_WEIGHT if managed_only else _EE_API_WEIGHT
        if ee_api_weight + weight > 4.0:
            break  # cap — a pile of EE APIs is not stronger evidence than a few
        ee_api_weight += weight
        ee_details.append(f"{group_id}:{artifact_id}{' (managed)' if managed_only else ''}")
    if ee_api_weight:
        score += ee_api_weight
        evidence.append(
            Evidence(
                "ee-api-dependency",
                ee_api_weight,
                "Java EE / Jakarta EE API dependency",
                ", ".join(ee_details),
            )
        )

    evidence.extend(_spring_boot_markers(module))

    verdict = (
        VERDICT_J2EE
        if score >= J2EE_SCORE_THRESHOLD
        else VERDICT_NON_J2EE
        if score <= -J2EE_SCORE_THRESHOLD
        else VERDICT_UNKNOWN
    )
    return Classification(
        verdict=verdict,
        confidence=_confidence_for(score, verdict),
        score=score,
        evidence=evidence,
        base_verdict=verdict,
        base_confidence=_confidence_for(score, verdict),
        scope="module",
        module_id=str(module.get("module_id") or ""),
    )


def _repo_structure_evidence(modules: list[dict]) -> list[Evidence]:
    """EAR root + WAR child modules assembled from the reactor layout."""
    by_rel: dict[str, dict] = {}
    for module in modules:
        rel = str(module.get("rel_path") or ".")
        by_rel[rel] = module
    evidence: list[Evidence] = []
    for module in modules:
        if str(module.get("packaging") or "").lower() != "ear":
            continue
        base_rel = str(module.get("rel_path") or ".")
        wars: list[str] = []
        for rel in module.get("module_dirs") or []:
            child = by_rel.get(
                os.path.join(base_rel, rel) if base_rel != "." else rel
            ) or by_rel.get(rel)
            if child is not None and str(child.get("packaging") or "").lower() == "war":
                wars.append(str(child.get("module_id") or rel))
        if wars:
            evidence.append(
                Evidence(
                    "ear-structure",
                    _EAR_STRUCTURE_WEIGHT,
                    "EAR with WAR modules (multi-module EAR structure)",
                    f"{module.get('module_id')}: {', '.join(wars)}",
                )
            )
    return evidence


def apply_override(classification: Classification, override: str) -> Classification:
    """Apply a manual override (``j2ee`` / ``non_j2ee`` / ``auto``).

    The base verdict and evidence are preserved so the UI can always explain
    what the classifier originally concluded.
    """
    value = str(override or "").strip().lower()
    if value not in (OVERRIDE_J2EE, OVERRIDE_NON_J2EE):
        value = OVERRIDE_AUTO
    base = classification.base_verdict or classification.verdict
    if value == OVERRIDE_AUTO:
        return replace(
            classification,
            verdict=base,
            confidence=classification.base_confidence or classification.confidence,
            overridden=False,
            override="",
        )
    verdict = VERDICT_J2EE if value == OVERRIDE_J2EE else VERDICT_NON_J2EE
    label = VERDICT_LABELS[verdict]
    return replace(
        classification,
        verdict=verdict,
        confidence=CONFIDENCE_MANUAL,
        base_verdict=base,
        overridden=True,
        override=value,
        evidence=[
            *classification.evidence,
            Evidence("override", 0.0, f"Manual override: {label}"),
        ],
    )


def classify_repository(
    scan: dict,
    *,
    repo_path: str = "",
    overrides: dict[str, str] | None = None,
) -> Classification:
    """Aggregate module classifications (plus repo-wide probes) into a verdict.

    Semantics for mixed repositories: any J2EE module makes the repository
    J2EE; a repository whose modules are all Non-J2EE is Non-J2EE; anything
    else stays Unknown so the user can decide (overrides are honored).
    ``overrides`` maps absolute repo path → ``j2ee`` / ``non_j2ee`` / ``auto``.
    """
    modules = list(scan.get("modules") or [])
    rows_by_module: dict[str, list[dict]] = {}
    for row in scan.get("dependencies") or []:
        rows_by_module.setdefault(str(row.get("module_id") or ""), []).append(row)

    evidence: list[Evidence] = []
    module_classifications: list[Classification] = []
    repo_dir = os.path.abspath(repo_path or str(scan.get("repo_path") or ""))

    for module in modules:
        module_dir = os.path.dirname(str(module.get("pom_path") or repo_dir))
        probe = probe_module_dir(module_dir)
        module_classifications.append(
            classify_module(module, rows_by_module.get(str(module.get("module_id") or ""), []), probe)
        )

    if not modules:
        # Configuration-only repository (descriptors without a Maven reactor)
        # or a repo whose POMs are all malformed — fall back to a repo probe.
        probe = probe_module_dir(repo_dir)
        synthetic = classify_module({"module_id": "", "packaging": ""}, [], probe)
        evidence.extend(synthetic.evidence)
        score = synthetic.score
    else:
        for module_class in module_classifications:
            evidence.extend(
                replace(e, detail=f"[{module_class.module_id}] {e.detail}".strip())
                for e in module_class.evidence
            )
        score = max((m.score for m in module_classifications), default=0.0)
        if score < J2EE_SCORE_THRESHOLD:
            evidence.extend(_repo_structure_evidence(modules))
            score += sum(e.weight for e in evidence if e.kind == "ear-structure")

    if any(m.verdict == VERDICT_J2EE for m in module_classifications):
        verdict = VERDICT_J2EE
        confidence = max(
            (m.confidence for m in module_classifications if m.verdict == VERDICT_J2EE),
            key=lambda c: {CONFIDENCE_LOW: 0, CONFIDENCE_MEDIUM: 1, CONFIDENCE_HIGH: 2}.get(c, 0),
        )
    elif module_classifications and all(
        m.verdict == VERDICT_NON_J2EE for m in module_classifications
    ):
        verdict = VERDICT_NON_J2EE
        confidence = (
            CONFIDENCE_HIGH if all(m.confidence == CONFIDENCE_HIGH for m in module_classifications)
            else CONFIDENCE_MEDIUM
        )
    elif not module_classifications:
        verdict = (
            VERDICT_J2EE if score >= J2EE_SCORE_THRESHOLD
            else VERDICT_NON_J2EE if score <= -J2EE_SCORE_THRESHOLD
            else VERDICT_UNKNOWN
        )
        confidence = _confidence_for(score, verdict)
    else:
        verdict = VERDICT_UNKNOWN
        confidence = CONFIDENCE_LOW

    classification = Classification(
        verdict=verdict,
        confidence=confidence,
        score=score,
        evidence=evidence,
        base_verdict=verdict,
        base_confidence=confidence,
        scope="repository",
    )
    override = (overrides or {}).get(repo_path, "")
    return apply_override(classification, override)


def load_overrides(raw: Any) -> dict[str, str]:
    """Parse the persisted overrides blob (JSON string or dict) safely."""
    import json

    data = raw
    if isinstance(data, (bytes, bytearray)):
        try:
            data = data.decode("utf-8")
        except Exception:  # noqa: BLE001 — a corrupt blob must not crash the UI
            return {}
    if isinstance(data, str):
        try:
            data = json.loads(data or "{}")
        except (TypeError, ValueError):
            return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in data.items():
        text = str(value or "").strip().lower()
        if text in (OVERRIDE_J2EE, OVERRIDE_NON_J2EE, OVERRIDE_AUTO):
            out[str(key)] = text
    return out


def save_overrides(overrides: dict[str, str]) -> str:
    """Serialize overrides for the hidden configuration key."""
    import json

    clean = {
        str(k): str(v)
        for k, v in (overrides or {}).items()
        if str(v or "").strip().lower() in (OVERRIDE_J2EE, OVERRIDE_NON_J2EE, OVERRIDE_AUTO)
    }
    return json.dumps(clean, sort_keys=True)


def reclassify_entry(entry: dict, overrides: dict[str, str]) -> dict:
    """Re-apply an override to an already-scanned repo entry (no rescan)."""
    path = str(entry.get("path") or "")
    classification = Classification.from_dict(entry.get("classification"))
    entry["classification"] = apply_override(
        classification, (overrides or {}).get(path, "")
    ).to_dict()
    return entry


# ------------------------------------------------------------------ versions

# Maven-style qualifier ranks (subset of ComparableVersion). The empty
# qualifier — an absent qualifier — ranks as "release", which is why
# 1.0-RC1 < 1.0 and 1.0-SNAPSHOT < 1.0.
_QUALIFIER_RANK = {
    "alpha": 1,
    "a": 1,
    "beta": 2,
    "b": 2,
    "milestone": 3,
    "m": 3,
    "rc": 4,
    "cr": 4,
    "snapshot": 5,
    "": 6,
    "ga": 7,
    "final": 7,
    "release": 7,
    "sp": 8,
}
_TOKEN_RE = re.compile(r"\d+|[A-Za-z]+")


def parse_version(version: str) -> list[tuple[int, int, str]]:
    """Tokenize a Maven version into comparable (kind, number, text) tuples."""
    text = str(version or "").strip()
    tokens: list[tuple[int, int, str]] = []
    for part in re.split(r"[.\-_+]", text):
        if not part:
            continue
        for chunk in _TOKEN_RE.findall(part):
            if chunk.isdigit():
                tokens.append((0, int(chunk), ""))
            else:
                lowered = chunk.lower()
                rank = _QUALIFIER_RANK.get(lowered)
                tokens.append((1, rank, "") if rank is not None else (1, 100, lowered))
    return tokens


def compare_versions(a: str, b: str) -> int:
    """Maven-style version comparison: -1 / 0 / 1 (empty sorts first)."""
    ta, tb = parse_version(a), parse_version(b)
    if not ta and not tb:
        return 0
    length = max(len(ta), len(tb))
    for index in range(length):
        xa = ta[index] if index < len(ta) else None
        xb = tb[index] if index < len(tb) else None
        if xa is None:
            # Missing token: pad with the null qualifier if the other side
            # continues with a qualifier, else with zero.
            pad = (1, 6, "") if xb is not None and xb[0] == 1 else (0, 0, "")
            xa = pad
        if xb is None:
            pad = (1, 6, "") if xa[0] == 1 else (0, 0, "")
            xb = pad
        if xa != xb:
            return -1 if xa < xb else 1
    return 0


def version_sort_key(version: str) -> Any:
    """Sort key implementing full Maven ordering.

    Built on :func:`compare_versions` (via ``cmp_to_key``) so trailing
    zeros (``1.0`` vs ``1.0.0``) and qualifier padding (``1.0-rc1`` <
    ``1.0``) sort correctly — a plain token-list key gets both wrong.
    """
    from functools import cmp_to_key

    return cmp_to_key(compare_versions)(str(version or ""))


def conflict_key(usage: Usage) -> tuple[str, str, str, str]:
    """Conflict identity: normalized coordinates plus type and classifier.

    Two usages are the same artifact only when groupId, artifactId, type
    (default ``jar``) and classifier all match — a ``sources`` or ``tests``
    artifact must never merge with the main artifact.
    """
    return (
        str(usage.group_id or "").strip(),
        str(usage.artifact_id or "").strip(),
        (str(usage.type or "").strip() or "jar"),
        str(usage.classifier or "").strip(),
    )


def conflict_report_text(conflicts: Iterable[dict], *, fmt: str = "tsv") -> str:
    """Readable conflict report for the clipboard (one row per usage).

    Follows the ``dependencies_table_text`` conventions: ``tsv``
    (tab-separated, Excel-friendly) or ``markdown``; the ``note`` column
    carries ``unresolved`` for usages whose version expression could not be
    resolved — never a fabricated version.
    """
    headers = (
        "groupId",
        "artifactId",
        "type",
        "classifier",
        "version",
        "repository",
        "module",
        "scope",
        "origin",
        "pom",
        "note",
    )
    rows: list[tuple[str, ...]] = []
    for group in conflicts:
        for usage in group.get("usages") or []:
            unresolved = usage.version_source == "unresolved" or not usage.version
            rows.append(
                (
                    str(group.get("group_id") or usage.group_id or ""),
                    str(group.get("artifact_id") or usage.artifact_id or ""),
                    str(usage.type or group.get("type") or "jar"),
                    str(usage.classifier or group.get("classifier") or ""),
                    usage.version or (usage.declared_version if unresolved else ""),
                    usage.repo_name,
                    usage.module_id,
                    usage.scope,
                    usage.origin,
                    usage.pom_path,
                    "unresolved" if unresolved else "",
                )
            )

    if fmt == "markdown":
        lines = [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
        ]
        for row in rows:
            safe = [c.replace("|", "\\|") for c in row]
            lines.append("| " + " | ".join(safe) + " |")
        return "\n".join(lines) + ("\n" if lines else "")

    lines = ["\t".join(headers)]
    lines.extend("\t".join(row) for row in rows)
    return "\n".join(lines) + ("\n" if lines else "")


def normalize_coordinate(group_id: Any, artifact_id: Any) -> tuple[str, str]:
    """Normalized (groupId, artifactId) for duplicate detection.

    Maven coordinates are case-sensitive, so normalization only trims
    whitespace — duplicates are never detected from JAR filenames.
    """
    return (str(group_id or "").strip(), str(artifact_id or "").strip())


# -------------------------------------------------------------------- usage


@dataclass
class Usage:
    """One (repository, module, coordinate) dependency usage record."""

    repo_name: str
    repo_path: str
    module_id: str
    group_id: str
    artifact_id: str
    version: str               # effective version ("" when unresolved)
    declared_version: str      # raw declared text (may contain ${...})
    version_source: str        # explicit | managed | unresolved | none
    scope: str
    type: str
    classifier: str
    optional: str
    origin: str                # direct | inherited | profile | managed
    profile: str
    packaging: str
    pom_path: str
    filename: str = ""         # build-output JAR name when present
    present_in_build_output: bool = False
    search_blob: str = ""

    @property
    def coordinates(self) -> str:
        return f"{self.group_id}:{self.artifact_id}"

    def gav(self) -> str:
        version = self.version or ("?" if self.declared_version else "")
        text = f"{self.group_id}:{self.artifact_id}"
        return f"{text}:{version}" if version else text

    def make_blob(self) -> str:
        parts = [
            self.group_id,
            self.artifact_id,
            self.version,
            self.declared_version,
            self.scope,
            self.type,
            self.classifier,
            self.origin,
            self.module_id,
            self.repo_name,
            self.filename,
            self.coordinates,
            f"{self.coordinates}:{self.version}" if self.version else "",
            os.path.basename(self.pom_path or ""),
        ]
        return " ".join(p for p in parts if p).lower()


def _find_build_jar(module_dir: str, artifact_id: str, version: str) -> str:
    """JAR name from ``<module>/target`` when the artifact was built locally."""
    if not module_dir or not artifact_id:
        return ""
    target = os.path.join(module_dir, "target")
    try:
        names = os.listdir(target)
    except OSError:
        return ""
    prefixes = (
        [f"{artifact_id}-{version}.jar", f"{artifact_id}-{version}-sources.jar"]
        if version
        else []
    )
    for name in prefixes:
        if name in names:
            return name
    loose = sorted(
        n for n in names if n.startswith(f"{artifact_id}-") and n.endswith(".jar")
    )
    return loose[0] if loose else ""


def _usage_from_row(
    repo_name: str,
    repo_path: str,
    row: dict,
    modules_meta: dict[str, dict],
) -> Usage:
    group_id, artifact_id = normalize_coordinate(row.get("group_id"), row.get("artifact_id"))
    declared = str(row.get("declared_version") or row.get("version") or "")
    effective = str(row.get("version") or "")
    source = str(row.get("version_source") or "")
    if not source:
        if "${" in effective:
            source = "unresolved"
        elif effective and str(row.get("origin") or "") == "managed":
            source = "managed"
        elif effective:
            source = "explicit"
        else:
            source = "none"
    if source == "unresolved":
        effective = ""  # never invent a version we could not resolve
    module_id = str(row.get("module_id") or "")
    module_meta = modules_meta.get(module_id) or {}
    pom_path = str(row.get("pom_path") or module_meta.get("pom_path") or "")
    filename = _find_build_jar(os.path.dirname(pom_path), artifact_id, effective)
    usage = Usage(
        repo_name=repo_name,
        repo_path=repo_path,
        module_id=module_id,
        group_id=group_id,
        artifact_id=artifact_id,
        version=effective,
        declared_version=declared,
        version_source=source,
        scope=str(row.get("scope") or "compile"),
        type=str(row.get("type") or "jar"),
        classifier=str(row.get("classifier") or ""),
        optional=str(row.get("optional") or "false"),
        origin=str(row.get("origin") or "direct"),
        profile=str(row.get("profile") or ""),
        packaging=str(module_meta.get("packaging") or "jar"),
        pom_path=pom_path,
        filename=filename,
        present_in_build_output=bool(filename),
    )
    usage.search_blob = usage.make_blob()
    return usage


def build_usages(repo_entries: list[dict]) -> list[Usage]:
    """Flatten scanned repo entries into usage records."""
    usages: list[Usage] = []
    for entry in repo_entries or []:
        scan = entry.get("scan") or {}
        modules_meta = {
            str(m.get("module_id") or ""): m for m in scan.get("modules") or []
        }
        for row in scan.get("dependencies") or []:
            usages.append(
                _usage_from_row(
                    str(entry.get("name") or ""),
                    str(entry.get("path") or ""),
                    row,
                    modules_meta,
                )
            )
    return usages


# --------------------------------------------------------------------- index


def _query_tokens(query: str) -> list[str]:
    tokens: list[str] = []
    for raw in str(query or "").split():
        token = raw.lower().strip()
        if token.endswith(".jar"):
            token = token[:-4]
        if token:
            tokens.append(token)
    return tokens


class ArtifactIndex:
    """In-memory index over every scanned repository's dependency rows.

    Built once per group scan (in a worker thread); every keystroke only
    filters this structure — no POM is ever re-parsed. Classifications can be
    updated in place (manual overrides) without touching the index rows.
    """

    def __init__(self, repo_entries: list[dict] | None = None) -> None:
        self._classifications: dict[str, dict] = {}
        self._usages: list[Usage] = []
        self._repos: dict[str, dict] = {}
        self._rebuild(list(repo_entries or []))

    # -- construction -----------------------------------------------------

    def _rebuild(self, repo_entries: list[dict]) -> None:
        self._repos = {
            str(e.get("path") or ""): {
                "name": str(e.get("name") or ""),
                "path": str(e.get("path") or ""),
                "error": str(e.get("error") or ""),
                "scan": e.get("scan") or {},
                "classification": e.get("classification") or {},
            }
            for e in repo_entries
        }
        self._classifications = {
            path: data.get("classification") or {} for path, data in self._repos.items()
        }
        self._usages = build_usages(repo_entries)

    def update_entries(self, repo_entries: list[dict]) -> None:
        self._rebuild(list(repo_entries or []))

    # -- classifications -------------------------------------------------

    def classification_for(self, repo_path: str) -> dict:
        return self._classifications.get(str(repo_path)) or {}

    def set_classification(self, repo_path: str, classification: dict) -> None:
        """Update one repo's classification in place (after an override)."""
        key = str(repo_path)
        self._classifications[key] = dict(classification or {})
        if key in self._repos:
            self._repos[key]["classification"] = dict(classification or {})

    def eligible_repo_paths(self, verdict: str = VERDICT_J2EE) -> list[str]:
        return sorted(
            path
            for path, data in self._classifications.items()
            if str(data.get("verdict") or "") == verdict
        )

    def repo_info(self, repo_path: str) -> dict:
        return self._repos.get(str(repo_path)) or {}

    # -- querying ---------------------------------------------------------

    @property
    def usages(self) -> list[Usage]:
        return list(self._usages)

    def search(
        self,
        query: str = "",
        *,
        j2ee_only: bool = True,
        scopes: Iterable[str] = (),
        origins: Iterable[str] = (),
        repos: Iterable[str] = (),
        version_sources: Iterable[str] = (),
        types: Iterable[str] = (),
    ) -> list[Usage]:
        """Filter the index; every whitespace-separated token must match the
        precomputed haystack (filename, coordinates, versions, module, repo…).
        """
        tokens = _query_tokens(query)
        scope_set = {s for s in scopes if s}
        origin_set = {o for o in origins if o}
        repo_set = {r for r in repos if r}
        source_set = {s for s in version_sources if s}
        type_set = {t for t in types if t}
        eligible = (
            set(self.eligible_repo_paths(VERDICT_J2EE)) if j2ee_only else None
        )
        out: list[Usage] = []
        for usage in self._usages:
            if eligible is not None and usage.repo_path not in eligible:
                continue
            if scope_set and usage.scope not in scope_set:
                continue
            if origin_set and usage.origin not in origin_set:
                continue
            if repo_set and usage.repo_path not in repo_set:
                continue
            if source_set and usage.version_source not in source_set:
                continue
            if type_set and usage.type not in type_set:
                continue
            if tokens and not all(token in usage.search_blob for token in tokens):
                continue
            out.append(usage)
        return out

    # -- grouping / duplicates ----------------------------------------------

    def artifact_groups(self, usages: Iterable[Usage] | None = None) -> list[dict]:
        """Group usages by normalized coordinates, worst-version-aware."""
        rows = list(usages) if usages is not None else list(self._usages)
        grouped: dict[tuple[str, str], dict] = {}
        for usage in rows:
            key = normalize_coordinate(usage.group_id, usage.artifact_id)
            group = grouped.get(key)
            if group is None:
                group = {
                    "group_id": key[0],
                    "artifact_id": key[1],
                    "coordinates": f"{key[0]}:{key[1]}",
                    "usages": [],
                    "versions": {},
                    "repos": set(),
                    "repo_names": set(),
                    "scopes": set(),
                    "origins": set(),
                    "filenames": set(),
                    "in_build_output": False,
                }
                grouped[key] = group
            group["usages"].append(usage)
            if usage.version:
                group["versions"][usage.version] = None
            group["repos"].add(usage.repo_path)
            group["repo_names"].add(usage.repo_name)
            group["scopes"].add(usage.scope)
            group["origins"].add(usage.origin)
            if usage.filename:
                group["filenames"].add(usage.filename)
            group["in_build_output"] = group["in_build_output"] or usage.present_in_build_output

        out: list[dict] = []
        for group in grouped.values():
            versions = sorted(group.pop("versions"), key=version_sort_key)
            group["versions"] = versions
            group["cross_repo"] = len(group["repos"]) > 1
            group["version_divergence"] = len(versions) > 1
            out.append(group)
        out.sort(
            key=lambda g: (
                str(min((u.repo_name for u in g["usages"]), default="")).casefold(),
                str(g["group_id"]).casefold(),
                str(g["artifact_id"]).casefold(),
            )
        )
        return out

    def repository_groups(self, usages: Iterable[Usage] | None = None) -> list[dict]:
        """Group usages by repository (with its current classification)."""
        rows = list(usages) if usages is not None else list(self._usages)
        grouped: dict[str, dict] = {}
        for usage in rows:
            group = grouped.get(usage.repo_path)
            if group is None:
                info = self.repo_info(usage.repo_path)
                scan = info.get("scan") or {}
                group = {
                    "repo_name": usage.repo_name,
                    "repo_path": usage.repo_path,
                    "classification": self.classification_for(usage.repo_path),
                    "errors": list(scan.get("errors") or []),
                    "error": str(info.get("error") or ""),
                    "artifacts": {},
                }
                grouped[usage.repo_path] = group
            key = normalize_coordinate(usage.group_id, usage.artifact_id)
            artifact = group["artifacts"].get(key)
            if artifact is None:
                artifact = {
                    "group_id": key[0],
                    "artifact_id": key[1],
                    "coordinates": f"{key[0]}:{key[1]}",
                    "usages": [],
                    "versions": set(),
                }
                group["artifacts"][key] = artifact
            artifact["usages"].append(usage)
            if usage.version:
                artifact["versions"].add(usage.version)
        out = list(grouped.values())
        for group in out:
            artifacts = list(group["artifacts"].values())
            for artifact in artifacts:
                artifact["versions"] = sorted(artifact.pop("versions"), key=version_sort_key)
            artifacts.sort(
                key=lambda a: (str(a["group_id"]).casefold(), str(a["artifact_id"]).casefold())
            )
            group["artifacts"] = artifacts
        out.sort(key=lambda g: str(g["repo_name"]).casefold())
        return out

    def duplicates(self, usages: Iterable[Usage] | None = None) -> list[dict]:
        """Coordinates used in ≥2 repositories or with ≥2 distinct versions."""
        return [
            group
            for group in self.artifact_groups(usages)
            if group["cross_repo"] or group["version_divergence"]
        ]

    def conflicts(self, usages: Iterable[Usage] | None = None) -> list[dict]:
        """Cross-repository Maven *version* conflicts, strictly defined.

        An artifact conflicts when the same ``(groupId, artifactId, type,
        classifier)`` carries **two or more semantically distinct, known**
        versions across **two or more distinct repositories**:

        * identities include type/classifier so e.g. a ``sources`` or
          ``tests`` artifact is never merged with the main artifact;
        * versions are compared with :func:`parse_version` (Maven order),
          and ``1.0`` vs ``1.0.0`` are the same version;
        * ``dependencyManagement``-only rows (``origin == "managed"``) are
          excluded — a managed version is not proof the dependency is used;
        * unresolved ``${…}`` versions never invent a version: they ride
          along in ``unresolved_usages`` for reporting only.

        Different versions *may* resolve differently per repository — that
        is not a claim of a runtime conflict.
        """
        rows = [
            u
            for u in (usages if usages is not None else self._usages)
            if u.origin != "managed"
        ]
        grouped: dict[tuple[str, str, str, str], dict] = {}
        for usage in rows:
            key = conflict_key(usage)
            group = grouped.get(key)
            if group is None:
                group = {
                    "group_id": key[0],
                    "artifact_id": key[1],
                    "type": key[2],
                    "classifier": key[3],
                    "coordinates": usage.coordinates,
                    "usages": [],
                    "versions_seen": [],
                    "unresolved_usages": [],
                    "known_repos": set(),
                    "known_repo_names": set(),
                    "repos": set(),
                    "repo_names": set(),
                    "scopes": set(),
                    "origins": set(),
                    "pom_paths": set(),
                }
                grouped[key] = group
            group["usages"].append(usage)
            group["repos"].add(usage.repo_path)
            group["repo_names"].add(usage.repo_name)
            group["scopes"].add(usage.scope)
            group["origins"].add(usage.origin)
            if usage.pom_path:
                group["pom_paths"].add(usage.pom_path)
            if usage.version and usage.version_source != "unresolved":
                # Semantic dedupe: "1.0" and "1.0.0" are one Maven version.
                known = group["versions_seen"]
                if not any(compare_versions(usage.version, seen) == 0 for seen in known):
                    known.append(usage.version)
                group["known_repos"].add(usage.repo_path)
                group["known_repo_names"].add(usage.repo_name)
            else:
                group["unresolved_usages"].append(usage)

        out: list[dict] = []
        for group in grouped.values():
            versions = sorted(group.pop("versions_seen"), key=version_sort_key)
            if len(versions) < 2:
                continue  # one known version (or none) is not a conflict
            if len(group["known_repos"]) < 2:
                continue  # cross-*repository* conflict analysis only
            group["versions"] = versions
            group["unresolved"] = bool(group["unresolved_usages"])
            group["conflict"] = True
            group["usages"].sort(
                key=lambda u: (version_sort_key(u.version), u.repo_name.casefold(), u.module_id)
            )
            out.append(group)
        out.sort(
            key=lambda g: (
                str(g["group_id"]).casefold(),
                str(g["artifact_id"]).casefold(),
                str(g["type"]).casefold(),
                str(g["classifier"]).casefold(),
            )
        )
        return out

    def summary(self, usages: Iterable[Usage] | None = None) -> dict:
        rows = list(usages) if usages is not None else list(self._usages)
        groups = self.artifact_groups(rows)
        scan_errors = sum(
            len((data.get("scan") or {}).get("errors") or []) for data in self._repos.values()
        )
        failed = sum(1 for data in self._repos.values() if data.get("error"))
        return {
            "repos_total": len(self._repos),
            "repos_j2ee": len(self.eligible_repo_paths(VERDICT_J2EE)),
            "repos_unknown": sum(
                1
                for data in self._classifications.values()
                if str(data.get("verdict") or "") == VERDICT_UNKNOWN
            ),
            "repos_failed": failed,
            "pom_errors": scan_errors,
            "artifacts": len(groups),
            "usages": len(rows),
            "duplicates": sum(1 for g in groups if g["cross_repo"] or g["version_divergence"]),
        }
