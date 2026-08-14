"""ConfigurationService — the single access point for application settings.

No module talks to SQLite or the Keychain directly: everything flows through
this service, which owns the typed schema, validation, persistence (settings
repository for normal values, Keychain for secrets) and change notifications
(EventBus).

The schema is a flat list of :class:`SettingDef` — every setting is declared
once, with a default, a kind, and an optional validator — and pages, workers
and tests all derive from it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from devworkbench.core.settings import SettingsManager
from devworkbench.core.events import EventBus

logger = logging.getLogger("devworkbench.services.configuration")

# -- event topics -----------------------------------------------------------

TOPIC_SETTINGS_CHANGED = "settings.changed"      # payload: key, value
TOPIC_SETTINGS_APPLIED = "settings.applied"      # payload: keys (list)
TOPIC_NAVIGATION_REQUEST = "navigation.request"  # payload: module_id

# -- schema -----------------------------------------------------------------


class SettingKind(str, Enum):
    STRING = "string"
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    ENUM = "enum"
    SECRET = "secret"
    PATH = "path"  # a filesystem location (file or directory)


# NOTE: module-level alias — evaluated at import time, so it must use
# Optional[...] (not `str | None`, which is 3.10+ syntax at runtime).
Validator = Callable[[Any], Optional[str]]


@dataclass(frozen=True)
class SettingDef:
    """Declarative description of a single setting."""

    key: str
    label: str
    kind: SettingKind
    default: Any
    category: str = ""
    choices: tuple[str, ...] = ()
    hint: str = ""
    min: int | None = None
    max: int | None = None
    step: int | None = None
    browse: str | None = None  # "file" | "dir" → show a Browse… button
    validator: Validator | None = None


# -- validators ---------------------------------------------------------------


def _url(value: Any) -> str | None:
    if not str(value).startswith(("http://", "https://")):
        return "Must be a valid http(s) URL"
    return None


def _non_empty(value: Any) -> str | None:
    if not str(value or "").strip():
        return "This field is required"
    return None


def _executable_exists(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return "This field is required"
    if Path(text).is_file():
        return None
    import shutil

    if shutil.which(text):
        return None
    return "Executable not found on this system"


def _folder_exists(value: Any) -> str | None:
    """Optional folder: empty means 'use the app default'."""
    text = str(value or "").strip()
    if not text:
        return None
    return None if Path(text).expanduser().is_dir() else "Folder does not exist"


# -- schema: 10 categories -------------------------------------------------------


def _enum_validator(choices: tuple[str, ...]) -> Validator:
    def _check(value: Any) -> str | None:
        return None if value in choices else f"Choose one of: {', '.join(choices)}"

    return _check


def _defs(category: str, *entries: list) -> list[SettingDef]:
    return [SettingDef(category=category, **entry) for entry in entries]


SCHEMA: list[SettingDef] = [
    # ---------------------------------------------------------------- General
    *_defs(
        "General",
        dict(key="startup.module", label="Startup module", kind=SettingKind.ENUM, default="compare",
             choices=("compare", "git", "ai", "ssh", "loganalyzer", "settings", "plugins"),
             hint="Module shown when DevWorkbench launches"),
        dict(key="startup.restore_workspace", label="Restore last workspace on launch", kind=SettingKind.BOOL, default=True),
        dict(key="startup.confirm_quit", label="Confirm before quitting", kind=SettingKind.BOOL, default=True),
        dict(key="startup.single_instance", label="Allow only a single running instance", kind=SettingKind.BOOL, default=True),
        dict(key="general.autosave_interval", label="Auto-save interval", kind=SettingKind.INT, default=60, min=10, max=600, step=10,
             hint="Seconds between automatic saves"),
        dict(key="general.show_hidden_files", label="Show hidden files in navigator", kind=SettingKind.BOOL, default=False),
        dict(key="general.open_files_in_tabs", label="Open files in new tabs", kind=SettingKind.BOOL, default=True),
        dict(key="updates.check", label="Check for updates automatically", kind=SettingKind.BOOL, default=True),
        dict(key="updates.channel", label="Update channel", kind=SettingKind.ENUM, default="stable",
             choices=("stable", "beta", "nightly")),
    ),
    # ------------------------------------------------------------------ Menus
    # Menu manager: which menu-bar menus exist and which module screens are
    # visible. Every toggle is applied live (same session) by MainWindow via
    # the settings.changed bus — no restart needed. "Settings" itself has no
    # toggle: it must always stay reachable or the user could never re-enable
    # the rest.
    #
    # Defaults are *compare-only*: on a fresh install nothing is shown except
    # the Compare screen, so the app starts focused; the user enables the
    # menus and modules they need from Settings > Menus. Toggles are persisted
    # to SQLite (via the settings repository) the moment they are applied, so
    # a user's choices survive restarts while untouched keys keep their
    # defaults.
    *_defs(
        "Menus",
        dict(key="ui.menu_file", label="File", kind=SettingKind.BOOL, default=False,
             hint="Show the File menu in the menu bar"),
        dict(key="ui.menu_edit", label="Edit", kind=SettingKind.BOOL, default=False,
             hint="Show the Edit menu in the menu bar"),
        dict(key="ui.menu_view", label="View", kind=SettingKind.BOOL, default=False,
             hint="Show the View menu in the menu bar"),
        dict(key="ui.menu_module", label="Module", kind=SettingKind.BOOL, default=False,
             hint="Show the Module menu in the menu bar"),
        dict(key="ui.menu_help", label="Help", kind=SettingKind.BOOL, default=False,
             hint="Show the Help menu in the menu bar"),
        dict(key="ui.show_compare", label="Compare", kind=SettingKind.BOOL, default=True,
             hint="Shown in the sidebar, Module menu and tab bar"),
        dict(key="ui.show_git", label="Git", kind=SettingKind.BOOL, default=False,
             hint="Shown in the sidebar, Module menu and tab bar"),
        dict(key="ui.show_ai", label="AI", kind=SettingKind.BOOL, default=False,
             hint="Shown in the sidebar, Module menu and tab bar"),
        dict(key="ui.show_ssh", label="SSH", kind=SettingKind.BOOL, default=False,
             hint="Shown in the sidebar, Module menu and tab bar"),
        dict(key="ui.show_loganalyzer", label="Log Analyzer", kind=SettingKind.BOOL, default=False,
             hint="Shown in the sidebar, Module menu and tab bar"),
        dict(key="ui.show_plugins", label="Plugin Manager", kind=SettingKind.BOOL, default=False,
             hint="Shown in the sidebar, Module menu and tab bar"),
    ),
    # ------------------------------------------------------------- Appearance
    *_defs(
        "Appearance",
        dict(key="appearance.theme", label="Theme", kind=SettingKind.ENUM, default="dark",
             choices=("dark", "light", "system"), hint="'System' follows the macOS appearance"),
        dict(key="appearance.accent", label="Accent color", kind=SettingKind.STRING, default="#5b8def",
             hint="Used for highlights, buttons and focus states"),
        dict(key="appearance.font_size", label="Font size", kind=SettingKind.INT, default=13, min=11, max=17,
             hint="Base UI font size in points"),
        dict(key="appearance.mono_diffs", label="Use monospaced font in diffs", kind=SettingKind.BOOL, default=True),
        dict(key="appearance.antialias", label="Enable text antialiasing", kind=SettingKind.BOOL, default=True),
        dict(key="appearance.reduce_transparency", label="Reduce transparency", kind=SettingKind.BOOL, default=False),
    ),
    # ------------------------------------------------------------------- Git
    *_defs(
        "Git",
        dict(key="git.executable", label="Git executable", kind=SettingKind.STRING, default="/usr/bin/git",
             hint="Path to the git binary", validator=_executable_exists),
        dict(key="git.default_branch", label="Default branch name", kind=SettingKind.STRING, default="main",
             validator=_non_empty),
        dict(key="git.autofetch", label="Fetch remotes automatically", kind=SettingKind.BOOL, default=True),
        dict(key="git.fetch_interval", label="Auto-fetch interval", kind=SettingKind.INT, default=15, min=1, max=120,
             hint="Minutes between background fetches"),
        dict(key="git.diff_tool", label="External diff tool", kind=SettingKind.STRING, default="", hint="Optional"),
        dict(key="git.merge_tool", label="External merge tool", kind=SettingKind.STRING, default="", hint="Optional"),
        dict(key="git.sign_commits", label="Sign commits by default", kind=SettingKind.BOOL, default=False),
        dict(key="git.signing_key", label="Signing key", kind=SettingKind.STRING, default="",
             hint="GPG key id (optional)"),
    ),
    # Internal UI state — view preferences persisted through the service but
    # *never rendered* in Settings (the category is not part of CATEGORIES):
    # the Git home page filter/search text and the open repository tabs,
    # restored on the next launch.
    *_defs(
        "internal",
        dict(key="git.home.search", label="Git home search", kind=SettingKind.STRING, default=""),
        dict(key="git.home.group", label="Git home group filter", kind=SettingKind.STRING, default=""),
        dict(key="git.home.tabs", label="Git home open tabs", kind=SettingKind.STRING, default="[]"),
        dict(key="git.home.active", label="Git home active tab", kind=SettingKind.STRING, default=""),
        dict(key="git.home.branches", label="Git home branch list", kind=SettingKind.STRING,
             default='["main","master","develop"]'),
        dict(key="git.home.branch", label="Git home selected branch", kind=SettingKind.STRING, default="main"),
        dict(key="git.home.group_actions", label="Git home per-group actions", kind=SettingKind.STRING,
             default="{}"),
    ),
    # -------------------------------------------------------------------- AI
    *_defs(
        "AI",
        dict(key="ai.provider", label="Provider", kind=SettingKind.ENUM, default="openai",
             choices=("openai", "gemini", "anthropic", "ollama", "azure"),
             hint="Active provider — the AI module follows this switch automatically"),
        dict(key="ai.model", label="Model", kind=SettingKind.STRING, default="gpt-4.1", validator=_non_empty),
        dict(key="ai.temperature", label="Temperature", kind=SettingKind.FLOAT, default=0.7, min=0, max=2, step=1,
             hint="Sampling temperature, 0–2"),
        dict(key="ai.timeout", label="Request timeout", kind=SettingKind.INT, default=60, min=1, max=300,
             hint="Seconds before a request is aborted"),
        dict(key="ai.max_tokens", label="Max output tokens", kind=SettingKind.INT, default=2048,
             min=128, max=32768, step=256),
        # OpenAI — key in the Keychain, never in SQLite
        dict(key="ai.openai_base_url", label="OpenAI base URL", kind=SettingKind.STRING,
             default="https://api.openai.com/v1", validator=_url,
             hint="Also covers OpenAI-compatible endpoints"),
        dict(key="ai.api_key", label="OpenAI API key", kind=SettingKind.SECRET, default="",
             hint="Stored in the macOS Keychain — never in the database"),
        # Google Gemini
        dict(key="ai.gemini_base_url", label="Gemini base URL", kind=SettingKind.STRING,
             default="https://generativelanguage.googleapis.com", validator=_url),
        dict(key="ai.gemini_api_key", label="Gemini API key", kind=SettingKind.SECRET, default="",
             hint="Stored in the macOS Keychain — never in the database"),
        # Anthropic
        dict(key="ai.anthropic_base_url", label="Anthropic base URL", kind=SettingKind.STRING,
             default="https://api.anthropic.com", validator=_url),
        dict(key="ai.anthropic_api_key", label="Anthropic API key", kind=SettingKind.SECRET, default="",
             hint="Stored in the macOS Keychain — never in the database"),
        # Ollama — local, no key required
        dict(key="ai.ollama_base_url", label="Ollama base URL", kind=SettingKind.STRING,
             default="http://localhost:11434",
             hint="Local models — no API key required"),
        # Azure OpenAI
        dict(key="ai.azure_endpoint", label="Azure endpoint", kind=SettingKind.STRING, default="",
             hint="e.g. https://my-resource.openai.azure.com"),
        dict(key="ai.azure_deployment", label="Azure deployment", kind=SettingKind.STRING, default=""),
        dict(key="ai.azure_api_version", label="Azure API version", kind=SettingKind.STRING,
             default="2024-06-01", validator=_non_empty),
        dict(key="ai.azure_api_key", label="Azure API key", kind=SettingKind.SECRET, default="",
             hint="Stored in the macOS Keychain — never in the database"),
    ),
    # ------------------------------------------------------------------- SSH
    *_defs(
        "SSH",
        dict(key="ssh.default_user", label="Default user", kind=SettingKind.STRING, default="dev",
             validator=_non_empty),
        dict(key="ssh.default_port", label="Default port", kind=SettingKind.INT, default=22, min=1, max=65535),
        dict(key="ssh.timeout", label="Connection timeout", kind=SettingKind.INT, default=15, min=1, max=120,
             hint="Seconds"),
        dict(key="ssh.keepalive", label="Keep-alive interval", kind=SettingKind.INT, default=0, min=0, max=300,
             hint="Seconds between probes; 0 disables"),
        dict(key="ssh.compression", label="Enable compression", kind=SettingKind.BOOL, default=True),
        dict(key="ssh.default_key", label="Default private key", kind=SettingKind.PATH, default="", browse="file",
             hint="Optional identity file"),
        dict(key="ssh.passphrase", label="Key passphrase", kind=SettingKind.SECRET, default="",
             hint="Stored in the macOS Keychain — never in the database"),
    ),
    # ---------------------------------------------------------------- Compare
    *_defs(
        "Compare",
        dict(key="compare.engine", label="Diff engine", kind=SettingKind.ENUM, default="auto",
             choices=("auto", "myers", "difflib"),
             hint="Myers O(ND) with an automatic difflib fallback"),
        dict(key="compare.context_lines", label="Context lines", kind=SettingKind.INT, default=3, min=0, max=50),
        dict(key="compare.ignore_whitespace", label="Ignore whitespace", kind=SettingKind.BOOL, default=False),
        dict(key="compare.ignore_case", label="Ignore case", kind=SettingKind.BOOL, default=False),
        dict(key="compare.ignore_comments", label="Ignore comments", kind=SettingKind.BOOL, default=False),
        dict(key="compare.ignore_blank_lines", label="Ignore blank lines", kind=SettingKind.BOOL, default=False),
        dict(key="compare.show_whitespace", label="Show whitespace", kind=SettingKind.BOOL, default=True),
        dict(key="compare.follow_symlinks", label="Follow symbolic links", kind=SettingKind.BOOL, default=True),
        dict(key="compare.detect_moves", label="Detect moved / renamed files", kind=SettingKind.BOOL, default=True,
             hint="Pair same-content files at different paths in folder comparisons"),
        dict(key="compare.ignore_dirs", label="Ignored folders", kind=SettingKind.STRING,
             default=".git,.idea,target,build,node_modules",
             hint="Comma-separated directory names pruned from folder comparisons"),
    ),
    # ------------------------------------------------------------------- Logs
    *_defs(
        "Logs",
        dict(key="logs.log_level", label="Log level", kind=SettingKind.ENUM, default="INFO",
             choices=("DEBUG", "INFO", "WARN", "ERROR")),
        dict(key="logs.timestamps", label="Prefix log lines with timestamps", kind=SettingKind.BOOL, default=True),
        dict(key="logs.max_bytes", label="Max log file size", kind=SettingKind.INT, default=1_000_000,
             min=100_000, max=100_000_000, step=100_000, hint="Bytes before rotation"),
        dict(key="logs.backup_count", label="Keep log backups", kind=SettingKind.INT, default=3, min=0, max=20),
    ),
    # ---------------------------------------------------------------- Plugins
    *_defs(
        "Plugins",
        dict(key="plugins.auto_discover", label="Auto-discover plugins", kind=SettingKind.BOOL, default=True),
        dict(key="plugins.scan_on_start", label="Scan for updates on startup", kind=SettingKind.BOOL, default=True),
        dict(key="plugins.allow_community", label="Allow community plugins", kind=SettingKind.BOOL, default=True),
        dict(key="plugins.trusted_sources", label="Trusted plugin sources", kind=SettingKind.STRING, default="github.com",
             hint="Comma-separated hostnames"),
        dict(key="plugins.strict_validation", label="Strict manifest validation", kind=SettingKind.BOOL, default=False),
    ),
    # ---------------------------------------------------------------- Advanced
    *_defs(
        "Advanced",
        dict(key="advanced.data_folder", label="Data folder", kind=SettingKind.PATH, default="", browse="dir",
             validator=_folder_exists, hint="Where databases and plugins live"),
        dict(key="advanced.log_folder", label="Log folder", kind=SettingKind.PATH, default="", browse="dir",
             validator=_folder_exists),
        dict(key="advanced.telemetry", label="Send anonymous usage telemetry", kind=SettingKind.BOOL, default=False),
        dict(key="advanced.crash_reports", label="Submit crash reports automatically", kind=SettingKind.BOOL, default=False),
        dict(key="advanced.developer_mode", label="Developer mode", kind=SettingKind.BOOL, default=False,
             hint="Exposes extra diagnostics"),
    ),
]

BY_KEY: dict[str, SettingDef] = {definition.key: definition for definition in SCHEMA}
CATEGORIES: list[str] = [
    "General", "Menus", "Appearance", "Git", "AI", "SSH", "Compare", "Logs", "Plugins", "Advanced",
]
DEFAULTS: dict[str, Any] = {definition.key: definition.default for definition in SCHEMA}


class ValidationError(ValueError):
    """Raised for programmatic writes that fail validation."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{key}: {message}" for key, message in errors.items()))


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


class ConfigurationService:
    """Owns the schema; validates, persists and publishes settings changes.

    ``repository`` may be None (memory-only mode for headless/tests) and
    ``keychain`` may be None (secrets become no-ops); the UI layer always
    passes real instances.
    """

    def __init__(
        self,
        settings: SettingsManager,
        repository=None,
        keychain=None,
        events: EventBus | None = None,
        service_name: str = "DevWorkbench",
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._keychain = keychain
        self._events = events
        self._service_name = service_name
        self._settings.set_defaults(DEFAULTS)

    # -- schema ------------------------------------------------------------

    @staticmethod
    def definitions(category: str | None = None) -> list[SettingDef]:
        if category is None:
            return list(SCHEMA)
        return [definition for definition in SCHEMA if definition.category == category]

    @staticmethod
    def definition(key: str) -> SettingDef | None:
        return BY_KEY.get(key)

    # -- reads -------------------------------------------------------------

    def get(self, key: str) -> Any:
        definition = BY_KEY.get(key)
        if definition is None:
            raise KeyError(f"unknown setting {key!r}")
        if definition.kind is SettingKind.SECRET:
            return self.get_secret(key)
        value = self._settings.get(key, definition.default)
        return self._coerce(value, definition)

    def get_secret(self, key: str) -> str | None:
        if self._keychain is None:
            return None
        try:
            return self._keychain.get(self._service_name, key)
        except Exception:  # noqa: BLE001 — a Keychain hiccup must not crash the UI
            logger.exception("failed to read secret %r", key)
            return None

    def has_secret(self, key: str) -> bool:
        return self.get_secret(key) is not None

    def snapshot(self, include_secrets: bool = False) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for definition in SCHEMA:
            if definition.kind is SettingKind.SECRET:
                if include_secrets:
                    values[definition.key] = self.get_secret(definition.key)
            else:
                values[definition.key] = self.get(definition.key)
        return values

    # -- writes ---------------------------------------------------------------

    def set(self, key: str, value: Any) -> None:
        """Validate and persist a single setting (raises ValidationError)."""
        definition = BY_KEY.get(key)
        if definition is None:
            raise KeyError(f"unknown setting {key!r}")
        errors = self.validate({key: value})
        if errors:
            raise ValidationError(errors)
        self._persist(definition, value)
        self._notify(definition.key, self.get(definition.key) if definition.kind is not SettingKind.SECRET else value)

    def apply(self, values: dict[str, Any]) -> dict[str, str]:
        """Validate a batch; on success persist everything and return {}.

        Returns ``{key: error}`` on failure — nothing is persisted.
        """
        errors = self.validate(values)
        if errors:
            return errors
        applied: list[str] = []
        for key, value in values.items():
            definition = BY_KEY.get(key)
            if definition is None:
                continue
            self._persist(definition, value)
            current = self.get(key) if definition.kind is not SettingKind.SECRET else value
            self._notify(definition.key, current)
            applied.append(definition.key)
        if applied and self._events is not None:
            self._events.publish(TOPIC_SETTINGS_APPLIED, keys=applied)
        return {}

    def reset(self, key: str) -> None:
        """Delete a setting, falling back to its default."""
        definition = BY_KEY.get(key)
        if definition is None:
            raise KeyError(f"unknown setting {key!r}")
        if definition.kind is SettingKind.SECRET:
            if self._keychain is not None:
                try:
                    self._keychain.delete(self._service_name, key)
                except Exception:  # noqa: BLE001
                    logger.exception("failed to delete secret %r", key)
        else:
            if self._repository is not None:
                try:
                    self._repository.delete(key)
                except Exception:  # noqa: BLE001
                    logger.exception("failed to delete setting %r", key)
        self._settings.reset(key)
        self._notify(key, definition.default)

    def reset_all(self) -> None:
        for definition in SCHEMA:
            self.reset(definition.key)

    # -- validation -------------------------------------------------------------

    def validate(self, values: dict[str, Any]) -> dict[str, str]:
        """Validate ``values`` against the schema; returns {key: error}."""
        errors: dict[str, str] = {}
        for key, value in values.items():
            definition = BY_KEY.get(key)
            if definition is None:
                continue
            message = self._check(definition, value)
            if message:
                errors[key] = message
        return errors

    # -- persistence ------------------------------------------------------------

    def load(self) -> None:
        """Merge persisted (non-secret) values into the in-memory store."""
        if self._repository is None:
            return
        for definition in SCHEMA:
            if definition.kind is SettingKind.SECRET:
                continue
            try:
                # Fallback is the in-memory default (config file first, then
                # the schema default) so file-level overrides survive.
                fallback = self._settings.get(definition.key, definition.default)
                stored = self._repository.get(definition.key, fallback)
            except Exception:  # noqa: BLE001
                logger.exception("failed to load setting %r", definition.key)
                continue
            self._settings.set(definition.key, self._coerce(stored, definition))
        logger.info("loaded %d settings from %s", len(SCHEMA), getattr(self._repository, "_manager", "memory"))

    # -- internals ---------------------------------------------------------------

    @staticmethod
    def _check(definition: SettingDef, value: Any) -> str | None:
        kind = definition.kind
        if kind is SettingKind.ENUM and value not in definition.choices:
            return f"Choose one of: {', '.join(definition.choices)}"
        if kind is SettingKind.INT:
            try:
                number = int(value)
            except (TypeError, ValueError):
                return "Must be a whole number"
            if definition.min is not None and number < definition.min:
                return f"Minimum is {definition.min}"
            if definition.max is not None and number > definition.max:
                return f"Maximum is {definition.max}"
        elif kind is SettingKind.FLOAT:
            try:
                number = float(value)
            except (TypeError, ValueError):
                return "Must be a number"
            if definition.min is not None and number < definition.min:
                return f"Minimum is {definition.min}"
            if definition.max is not None and number > definition.max:
                return f"Maximum is {definition.max}"
        if definition.validator is not None:
            return definition.validator(value)
        return None

    def _persist(self, definition: SettingDef, value: Any) -> None:
        if definition.kind is SettingKind.SECRET:
            if self._keychain is not None:
                try:
                    self._keychain.set(self._service_name, definition.key, str(value))
                except Exception:  # noqa: BLE001
                    logger.exception("failed to store secret %r", definition.key)
            return
        coerced = self._coerce(value, definition)
        if self._repository is not None:
            self._repository.set(definition.key, coerced)
        self._settings.set(definition.key, coerced)

    def _notify(self, key: str, value: Any) -> None:
        if self._events is not None:
            # Secrets never transit the bus. Subscribers react to the key; if
            # they need the value they call get_secret() explicitly — this
            # keeps API keys out of any future logging/tracing of events.
            definition = BY_KEY.get(key)
            if definition is not None and definition.kind is SettingKind.SECRET:
                value = None
            self._events.publish(TOPIC_SETTINGS_CHANGED, key=key, value=value)

    @staticmethod
    def _coerce(value: Any, definition: SettingDef) -> Any:
        kind = definition.kind
        if kind is SettingKind.BOOL:
            if isinstance(value, bool):
                return value
            return str(value).strip().lower() in ("1", "true", "yes", "on")
        if kind is SettingKind.INT:
            try:
                return int(value)
            except (TypeError, ValueError):
                return definition.default
        if kind is SettingKind.FLOAT:
            try:
                return float(value)
            except (TypeError, ValueError):
                return definition.default
        if kind is SettingKind.ENUM:
            return value if value in definition.choices else definition.default
        return "" if value is None else str(value)
