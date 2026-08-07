"""AIProviderFactory — turns ConfigurationService settings into a provider.

This is the single place that maps settings keys to a concrete provider, so
swapping providers never touches the UI: change ``ai.provider`` (and the
relevant credentials) in Settings and the next ``create()`` returns the new
provider with the same four operations.
"""

from __future__ import annotations

from devworkbench.services.ai.anthropic_provider import AnthropicProvider
from devworkbench.services.ai.azure_provider import AzureOpenAIProvider
from devworkbench.services.ai.base import (
    AIConfigurationError,
    AIProvider,
    AIProviderConfig,
)
from devworkbench.services.ai.gemini_provider import GeminiProvider
from devworkbench.services.ai.ollama_provider import OllamaProvider
from devworkbench.services.ai.openai_provider import OpenAIProvider

PROVIDER_CLASSES: dict[str, type[AIProvider]] = {
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "anthropic": AnthropicProvider,
    "ollama": OllamaProvider,
    "azure": AzureOpenAIProvider,
}


class AIProviderFactory:
    """Builds :class:`AIProvider` instances from a ``ConfigurationService``."""

    def __init__(self, configuration) -> None:
        self._configuration = configuration

    # -- public ---------------------------------------------------------------

    def create(self, provider: str | None = None, transport=None) -> AIProvider:
        """Build the configured (or named) provider.

        ``transport`` is injectable for tests; production uses the stdlib one.
        """
        name = (provider or self._configuration.get("ai.provider") or "openai").strip().lower()
        provider_cls = PROVIDER_CLASSES.get(name)
        if provider_cls is None:
            raise AIConfigurationError(
                f"Unknown AI provider {name!r} — available: {', '.join(sorted(PROVIDER_CLASSES))}"
            )
        return provider_cls(self._config(), transport)

    def list_providers(self) -> dict[str, type[AIProvider]]:
        """Provider id → class (used by UIs/tests)."""
        return dict(PROVIDER_CLASSES)

    # -- internals ---------------------------------------------------------------

    def _config(self) -> AIProviderConfig:
        get = self._configuration.get
        secret = self._configuration.get_secret
        # ``or default`` would treat 0.0 as falsy — temperature 0 (deterministic
        # output) must survive, so resolve None explicitly.
        temperature = get("ai.temperature")
        return AIProviderConfig(
            provider=str(get("ai.provider") or "openai"),
            model=str(get("ai.model") or "gpt-4.1"),
            temperature=float(temperature) if temperature is not None else 0.7,
            timeout=int(get("ai.timeout") or 60),
            max_tokens=int(get("ai.max_tokens") or 2048),
            openai_base_url=str(get("ai.openai_base_url") or "https://api.openai.com/v1"),
            openai_api_key=secret("ai.api_key"),
            gemini_base_url=str(get("ai.gemini_base_url") or "https://generativelanguage.googleapis.com"),
            gemini_api_key=secret("ai.gemini_api_key"),
            anthropic_base_url=str(get("ai.anthropic_base_url") or "https://api.anthropic.com"),
            anthropic_api_key=secret("ai.anthropic_api_key"),
            ollama_base_url=str(get("ai.ollama_base_url") or "http://localhost:11434"),
            azure_endpoint=str(get("ai.azure_endpoint") or ""),
            azure_deployment=str(get("ai.azure_deployment") or ""),
            azure_api_version=str(get("ai.azure_api_version") or "2024-06-01"),
            azure_api_key=secret("ai.azure_api_key"),
        )
