"""AI provider framework — the contract every provider implements.

The public surface is intentionally small and provider-agnostic:

- :class:`AIProvider.chat` — a raw conversation
- :class:`AIProvider.explain_diff` — explain a code diff
- :class:`AIProvider.generate_commit` — draft a commit message
- :class:`AIProvider.analyze_logs` — summarize/root-cause log lines

The three task methods are template methods: they build a prompt and delegate
to ``chat``, which is the only provider-specific method (``_complete``).
Providers are swappable — the UI calls the same four methods regardless of
which provider is configured; settings only change *which* instance the
factory returns.

Configuration is injected as a plain :class:`AIProviderConfig` (built from
``ConfigurationService`` by :class:`AIProviderFactory`) — providers never
reach into settings themselves (Dependency Injection).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Errors — typed so callers can react per failure class
# ---------------------------------------------------------------------------


class AIError(Exception):
    """Base class for every provider failure."""

    def __init__(self, message: str, *, provider: str = "", detail: Any = None) -> None:
        super().__init__(message)
        self.provider = provider
        self.detail = detail


class AIConfigurationError(AIError):
    """Missing or invalid configuration (e.g. no API key)."""


class AIAuthError(AIError):
    """The endpoint rejected the credentials (401/403)."""


class AIRateLimitError(AIError):
    """The endpoint rate-limited the request (429)."""


class AITimeoutError(AIError):
    """The request did not complete within the configured timeout."""


class AINetworkError(AIError):
    """Transport-level failure (DNS, refused connection, …)."""


class AIHttpError(AIError):
    """An unexpected HTTP error (4xx/5xx not covered above)."""

    def __init__(self, message: str, *, status: int, provider: str = "", detail: Any = None) -> None:
        super().__init__(message, provider=provider, detail=detail)
        self.status = status


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChatMessage:
    """One message in a conversation. ``role`` is ``user`` | ``assistant`` | ``system``."""

    role: str
    content: str

    @classmethod
    def from_any(cls, value: Any) -> "ChatMessage":
        """Normalize a (role, content) tuple, dict, or ChatMessage."""
        if isinstance(value, ChatMessage):
            return value
        if isinstance(value, (tuple, list)) and len(value) == 2:
            return cls(str(value[0]), str(value[1]))
        if isinstance(value, dict):
            return cls(str(value.get("role", "user")), str(value.get("content", "")))
        raise TypeError(f"cannot build ChatMessage from {value!r}")


@dataclass(frozen=True)
class AIUsage:
    """Token accounting reported by the endpoint (0 when unknown)."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class AIResult:
    """A normalized completion — the same shape for every provider."""

    text: str
    usage: AIUsage = field(default_factory=AIUsage)
    model: str = ""
    finish_reason: str = ""

    def __bool__(self) -> bool:
        return bool(self.text.strip())


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AIProviderConfig:
    """Typed, provider-agnostic configuration (built from settings by the factory)."""

    provider: str = "openai"
    model: str = "gpt-4.1"
    temperature: float = 0.7
    timeout: int = 60
    max_tokens: int = 2048

    # OpenAI / compatible
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: str | None = None
    # Gemini
    gemini_base_url: str = "https://generativelanguage.googleapis.com"
    gemini_api_key: str | None = None
    # Anthropic
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_api_key: str | None = None
    # Ollama
    ollama_base_url: str = "http://localhost:11434"
    # Azure OpenAI
    azure_endpoint: str = ""
    azure_deployment: str = ""
    azure_api_version: str = "2024-06-01"
    azure_api_key: str | None = None


# ---------------------------------------------------------------------------
# The contract
# ---------------------------------------------------------------------------


class AIProvider(ABC):
    """Base class for all AI providers.

    Subclasses implement the provider-specific parts — ``_url``, ``_headers``,
    ``_payload`` and ``_parse`` — plus ``name``/``display_name``. The four
    public operations and HTTP plumbing live here.
    """

    name: str = "base"
    display_name: str = "Base provider"
    requires_api_key: bool = False

    def __init__(self, config: AIProviderConfig, transport=None) -> None:
        self.config = config
        # Imported lazily to avoid a module cycle (transport imports AIError).
        self._transport = transport or self._default_transport()

    # -- the four operations -------------------------------------------------

    def chat(
        self,
        messages,
        system: str | None = None,
        *,
        temperature: float | None = None,
        model: str | None = None,
        timeout: int | None = None,
        max_tokens: int | None = None,
    ) -> AIResult:
        """Send a conversation; returns the assistant reply as an AIResult."""
        prepared = self._prepare_messages(messages, system)
        return self._complete(
            prepared,
            temperature=temperature if temperature is not None else self.config.temperature,
            model=model or self.config.model,
            timeout=timeout,
            max_tokens=max_tokens if max_tokens is not None else self.config.max_tokens,
        )

    def explain_diff(
        self,
        diff_text: str,
        context: str | None = None,
        *,
        temperature: float | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> AIResult:
        """Explain a unified diff: what changed, why, and any risks."""
        from devworkbench.services.ai.prompts import explain_diff_messages

        return self.chat(
            explain_diff_messages(diff_text, context),
            temperature=temperature,
            model=model,
            timeout=timeout,
        )

    def generate_commit(
        self,
        changes: str,
        style: str = "conventional",
        context: str | None = None,
        *,
        temperature: float | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> AIResult:
        """Draft a commit message for the given changes (git status/diff)."""
        from devworkbench.services.ai.prompts import commit_messages

        return self.chat(
            commit_messages(changes, style, context),
            temperature=temperature,
            model=model,
            timeout=timeout,
        )

    def analyze_logs(
        self,
        log_text: str,
        level: str = "ERROR",
        focus: str | None = None,
        *,
        temperature: float | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> AIResult:
        """Analyze log lines: summarize, root-cause, suggest next steps."""
        from devworkbench.services.ai.prompts import log_analysis_messages

        return self.chat(
            log_analysis_messages(log_text, level, focus),
            temperature=temperature,
            model=model,
            timeout=timeout,
        )

    # -- provider-specific surface -------------------------------------------

    @abstractmethod
    def _url(self, model: str) -> str:
        """The endpoint URL for a completion request."""

    def _headers(self) -> dict[str, str]:
        """HTTP headers (auth, content-type is added by the transport)."""
        return {}

    @abstractmethod
    def _payload(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        model: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        """The JSON body for this provider's API."""

    @abstractmethod
    def _parse(self, body: dict[str, Any]) -> AIResult:
        """Normalize a provider response into an AIResult."""

    def _complete(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        model: str,
        timeout: int | None,
        max_tokens: int,
    ) -> AIResult:
        payload = self._payload(messages, temperature=temperature, model=model, max_tokens=max_tokens)
        body = self._post(self._url(model), payload, timeout)
        return self._parse(body)

    # -- transport plumbing ----------------------------------------------------

    def _post(self, url: str, payload: dict[str, Any], timeout: int | None) -> dict[str, Any]:
        status, body = self._transport.post_json(url, self._headers(), payload, timeout or self.config.timeout)
        return self._check_response(status, body)

    def _check_response(self, status: int, body: dict[str, Any]) -> dict[str, Any]:
        if 200 <= status < 300:
            return body
        message = self._error_message(body) or f"HTTP {status}"
        if status in (401, 403):
            raise AIAuthError(message, provider=self.name)
        if status == 429:
            raise AIRateLimitError(message, provider=self.name)
        raise AIHttpError(message, status=status, provider=self.name)

    @staticmethod
    def _error_message(body: dict[str, Any]) -> str | None:
        error = body.get("error")
        if isinstance(error, dict):
            if isinstance(error.get("message"), str):
                return error["message"]
            for key in ("code", "type", "status"):
                if isinstance(error.get(key), str):
                    return error[key]
        if isinstance(error, str):
            return error
        return None

    @staticmethod
    def _prepare_messages(messages, system: str | None) -> list[ChatMessage]:
        prepared = [ChatMessage.from_any(message) for message in messages]
        prepared = [message for message in prepared if message.content.strip()]
        if system:
            prepared.insert(0, ChatMessage("system", system))
        return prepared

    @staticmethod
    def _default_transport():
        from devworkbench.services.ai.transport import UrllibJSONTransport

        return UrllibJSONTransport()
