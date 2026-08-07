"""Ollama provider — local models via ``/api/chat``.

No authentication; sampling options are passed under ``options``.
"""

from __future__ import annotations

from typing import Any

from devworkbench.services.ai.base import AIProvider, AIResult, AIUsage, ChatMessage


class OllamaProvider(AIProvider):
    name = "ollama"
    display_name = "Ollama (local)"
    requires_api_key = False

    def _url(self, model: str) -> str:
        return f"{self.config.ollama_base_url.rstrip('/')}/api/chat"

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
        return {
            "model": model,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
        }

    def _parse(self, body: dict[str, Any]) -> AIResult:
        message = body.get("message") or {}
        return AIResult(
            text=message.get("content") or "",
            usage=AIUsage(
                prompt_tokens=int(body.get("prompt_eval_count") or 0),
                completion_tokens=int(body.get("eval_count") or 0),
            ),
            model=body.get("model") or "",
            finish_reason="stop" if body.get("done") else "",
        )
