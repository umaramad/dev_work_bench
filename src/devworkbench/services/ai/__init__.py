"""AI provider framework.

Five swappable providers — OpenAI, Google Gemini, Anthropic, Ollama and
Azure OpenAI — behind a single :class:`AIProvider` contract with four
operations (``chat``, ``explain_diff``, ``generate_commit``,
``analyze_logs``). Configuration is read from ``ConfigurationService`` by
:class:`AIProviderFactory`; the UI never knows which provider it talks to.

Usage::

    provider = AIProviderFactory(configuration).create()   # reads ai.provider
    result = provider.explain_diff("@@ -1,3 +1,4 @@ ...")
"""

from devworkbench.services.ai.base import (
    AIAuthError,
    AIConfigurationError,
    AIError,
    AIHttpError,
    AINetworkError,
    AIProvider,
    AIProviderConfig,
    AIRateLimitError,
    AIResult,
    AITimeoutError,
    AIUsage,
    ChatMessage,
)
from devworkbench.services.ai.factory import AIProviderFactory, PROVIDER_CLASSES
from devworkbench.services.ai.transport import JSONTransport, UrllibJSONTransport

__all__ = [
    "AIError",
    "AIConfigurationError",
    "AIAuthError",
    "AIRateLimitError",
    "AITimeoutError",
    "AINetworkError",
    "AIHttpError",
    "ChatMessage",
    "AIUsage",
    "AIResult",
    "AIProviderConfig",
    "AIProvider",
    "AIProviderFactory",
    "PROVIDER_CLASSES",
    "JSONTransport",
    "UrllibJSONTransport",
]
