"""Azure OpenAI provider.

Same ``/chat/completions`` wire format as OpenAI, addressed through a
deployment URL and authenticated with an ``api-key`` header.
"""

from __future__ import annotations

from devworkbench.services.ai.base import AIConfigurationError
from devworkbench.services.ai.openai_provider import OpenAIProvider


class AzureOpenAIProvider(OpenAIProvider):
    name = "azure"
    display_name = "Azure OpenAI"
    requires_api_key = True

    def _url(self, model: str) -> str:
        endpoint = (self.config.azure_endpoint or "").rstrip("/")
        deployment = self.config.azure_deployment
        if not endpoint or not deployment:
            raise AIConfigurationError(
                "Azure endpoint and deployment are not configured — set them in Settings → AI",
                provider=self.name,
            )
        return (
            f"{endpoint}/openai/deployments/{deployment}/chat/completions"
            f"?api-version={self.config.azure_api_version}"
        )

    def _headers(self) -> dict[str, str]:
        api_key = self.config.azure_api_key
        if not api_key:
            raise AIConfigurationError(
                "Azure API key is not configured — set it in Settings → AI",
                provider=self.name,
            )
        return {"api-key": api_key}
