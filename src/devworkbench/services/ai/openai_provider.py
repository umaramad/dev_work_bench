"""OpenAI provider — OpenAI and OpenAI-compatible endpoints
(``/chat/completions`` with a Bearer token)."""

from __future__ import annotations

from typing import Any

from devworkbench.services.ai.base import (
    AIConfigurationError,
    AIProvider,
    AIProviderConfig,
    AIResult,
    AIUsage,
    ChatMessage,
)


class OpenAIProvider(AIProvider):
    name = "openai"
    display_name = "OpenAI"
    requires_api_key = True

    def _url(self, model: str) -> str:
        return f"{self.config.openai_base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        api_key = self.config.openai_api_key
        if not api_key:
            raise AIConfigurationError(
                "OpenAI API key is not configured — set it in Settings → AI",
                provider=self.name,
            )
        return {"Authorization": f"Bearer {api_key}"}

    def _payload(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        model: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        return {
            "model": model,
            "messages": [{"role": message.role, "content": message.content} for message in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }

    def _parse(self, body: dict[str, Any]) -> AIResult:
        choice = (body.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        usage = body.get("usage") or {}
        return AIResult(
            text=message.get("content") or "",
            usage=AIUsage(
                prompt_tokens=int(usage.get("prompt_tokens") or 0),
                completion_tokens=int(usage.get("completion_tokens") or 0),
            ),
            model=body.get("model") or "",
            finish_reason=choice.get("finish_reason") or "",
        )
