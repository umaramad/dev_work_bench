"""AI-module models."""

from __future__ import annotations

from dataclasses import dataclass, field

from devworkbench.models.base import Model


@dataclass
class AiProviderConfig(Model):
    provider: str = "openai"
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4.1"
    # api_key is stored in the macOS Keychain, never here.


@dataclass
class AiMessage(Model):
    role: str = "user"          # user | assistant | system
    content: str = ""
    created_at: str = ""


@dataclass
class AiSession(Model):
    id: str = ""
    title: str = "New chat"
    model: str = "gpt-4.1"
    messages: list[AiMessage] = field(default_factory=list)
