"""HTTP transport for AI providers.

Providers depend on the :class:`JSONTransport` interface, not on any HTTP
library — tests inject a fake transport and assert on the exact request.
The default implementation uses only the standard library (``urllib``) so
the framework stays dependency-free.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Any

from devworkbench.services.ai.base import AINetworkError, AITimeoutError


def _redact_url(url: str) -> str:
    """Strip query string and fragment so embedded credentials never leak.

    Some providers (Gemini) place the API key in the URL query string
    (``?key=...``); error messages and logs must never carry it.
    """
    for separator in ("?", "#"):
        url = url.split(separator, 1)[0]
    return url


class JSONTransport(ABC):
    """POSTs a JSON body and returns ``(status, parsed_body)``.

    Transports are deliberately dumb: they never interpret status codes or
    bodies (that is the provider's job) — they only do I/O and surface
    network/timeout failures as typed errors.
    """

    @abstractmethod
    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int | None,
    ) -> tuple[int, dict[str, Any]]:
        """POST ``payload`` as JSON; return ``(http_status, parsed_json)``."""


class UrllibJSONTransport(JSONTransport):
    """Standard-library JSON POST via ``urllib.request``."""

    def post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout: int | None,
    ) -> tuple[int, dict[str, Any]]:
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, self._read_body(response)
        except urllib.error.HTTPError as exc:
            # Non-2xx: return the status + body so the provider can classify it.
            return exc.code, self._read_body(exc)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError):
                raise AITimeoutError(f"request timed out after {timeout}s") from exc
            # The URL is user-configurable and some providers (Gemini) put the
            # API key in the query string — never embed it in an error that may
            # surface in the UI or logs.
            raise AINetworkError(f"network error for {_redact_url(url)}: {reason}") from exc
        except TimeoutError as exc:
            raise AITimeoutError(f"request timed out after {timeout}s") from exc

    @staticmethod
    def _read_body(response) -> dict[str, Any]:
        raw = response.read()
        if not raw:
            return {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
            return parsed if isinstance(parsed, dict) else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
