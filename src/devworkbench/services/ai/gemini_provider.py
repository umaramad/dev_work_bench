"""Google Gemini provider — ``models/{model}:generateContent`` REST API.

The API key travels as a query parameter; ``assistant`` roles are renamed to
``model`` and the system prompt is passed as ``systemInstruction``.
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


class GeminiProvider(AIProvider):
    name = "gemini"
    display_name = "Google Gemini"
    requires_api_key = True

    def _url(self, model: str) -> str:
        api_key = self.config.gemini_api_key
        if not api_key:
            raise AIConfigurationError(
                "Gemini API key is not configured — set it in Settings → AI",
                provider=self.name,
            )
        return (
            f"{self.config.gemini_base_url.rstrip('/')}/v1beta/models/"
            f"{model}:generateContent?key={api_key}"
        )

    def _headers(self) -> dict[str, str]:
        return {}

    def _payload(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float,
        model: str,
        max_tokens: int,
    ) -> dict[str, Any]:
        system_text = "\n\n".join(message.content for message in messages if message.role == "system")
        contents = [
            {
                "role": "user" if message.role == "user" else "model",
                "parts": [{"text": message.content}],
            }
            for message in messages
            if message.role != "system"
        ]
        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_text:
            payload["systemInstruction"] = {"parts": [{"text": system_text}]}
        return payload

    def _parse(self, body: dict[str, Any]) -> AIResult:
        candidate = (body.get("candidates") or [{}])[0]
        content = candidate.get("content") or {}
        parts = content.get("parts") or []
        text = "".join(part.get("text") or "" for part in parts if isinstance(part, dict))
        metadata = body.get("usageMetadata") or {}
        return AIResult(
            text=text,
            usage=AIUsage(
                prompt_tokens=int(metadata.get("promptTokenCount") or 0),
                completion_tokens=int(
                    metadata.get("candidatesTokenCount") or metadata.get("completionTokenCount") or 0
                ),
            ),
            model=body.get("model") or "",
            finish_reason=candidate.get("finishReason") or "",
        )
