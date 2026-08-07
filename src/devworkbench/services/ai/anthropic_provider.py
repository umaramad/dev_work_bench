"""Anthropic Claude provider — the ``/v1/messages`` Messages API.

The system prompt is a top-level ``system`` field (not a message role), and
requests require an ``anthropic-version`` header.
"""

from __future__ import annotations

from typing import Any

from devworkbench.services.ai.base import (
    AIConfigurationError,
    AIProvider,
    AIResult,
    AIUsage,
    ChatMessage,
)


class AnthropicProvider(AIProvider):
    name = "anthropic"
    display_name = "Anthropic"
    requires_api_key = True

    _API_VERSION = "2023-06-01"

    def _url(self, model: str) -> str:
        return f"{self.config.anthropic_base_url.rstrip('/')}/v1/messages"

    def _headers(self) -> dict[str, str]:
        api_key = self.config.anthropic_api_key
        if not api_key:
            raise AIConfigurationError(
                "Anthropic API key is not configured — set it in Settings → AI",
                provider=self.name,
            )
        return {
            "x-api-key": api_key,
            "anthropic-version": self._API_VERSION,
        }

    def _payload(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        model: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        system_text = "\n\n".join(message.content for message in messages if message.role == "system")
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
                if message.role != "system"
            ],
        }
        if system_text:
            payload["system"] = system_text
        return payload

    def _parse(self, body: dict[str, Any]) -> AIResult:
        text = "".join(
            block.get("text") or ""
            for block in body.get("content") or []
            if isinstance(block, dict) and block.get("type") == "text"
        )
        usage = body.get("usage") or {}
        return AIResult(
            text=text,
            usage=AIUsage(
                prompt_tokens=int(usage.get("input_tokens") or 0),
                completion_tokens=int(usage.get("output_tokens") or 0),
            ),
            model=body.get("model") or "",
            finish_reason=body.get("stop_reason") or "",
        )
