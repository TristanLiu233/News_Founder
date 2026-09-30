"""Anthropic Messages API adapter (official ``anthropic`` SDK, optional ``ai`` extra).

Structured output uses ``output_config.format`` (``json_schema``); the returned text is parsed as
JSON here and validated by the caller. Credentials and endpoint come only from this project's
settings: the API key and base URL are always passed explicitly, so ambient ``ANTHROPIC_*``
environment variables of the host shell are never used. The SDK's own retries are disabled;
``generate_with_retry`` owns the bounded retry policy for every provider. Server-side model
fallbacks are not enabled, so the recorded model is the model that served the request.
"""

from __future__ import annotations

import json

from industry_intelligence.ai.provider import (
    AIAuthenticationFailed,
    AIMalformedOutput,
    AIProviderNotConfigured,
    AIRateLimited,
    AIRefused,
    AIRequestRejected,
    AIServerError,
    AITimeout,
    StructuredResponse,
)

DEFAULT_BASE_URL = "https://api.anthropic.com"


def _retry_after(response):
    try:
        return float(response.headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        return None


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, settings, client=None):
        if not (settings.ai_model and settings.ai_api_key):
            raise AIProviderNotConfigured("AI_MODEL and AI_API_KEY are required")
        self.model = settings.ai_model
        self.max_tokens = settings.ai_max_output_tokens
        if client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover - depends on the installed extra
                raise AIProviderNotConfigured(
                    "Install the optional dependency: pip install -e './backend[ai]'"
                ) from exc
            client = AsyncAnthropic(
                api_key=settings.ai_api_key,
                base_url=settings.ai_base_url or DEFAULT_BASE_URL,
                timeout=settings.ai_timeout,
                max_retries=0,
            )
        self._client = client

    async def preflight(self):
        """Models API lookup of the configured model: authenticates without generating."""

        import anthropic

        try:
            await self._client.models.retrieve(self.model)
        except anthropic.APITimeoutError as exc:
            raise AITimeout("request timed out") from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise AIAuthenticationFailed(f"HTTP {exc.status_code}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise AIServerError(f"HTTP {exc.status_code}") from exc
            raise AIRequestRejected(f"HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIServerError("connection error") from exc

    async def generate_structured(self, *, system, user, schema, schema_name):
        import anthropic

        try:
            response = await self._client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APITimeoutError as exc:
            raise AITimeout("request timed out") from exc
        except anthropic.RateLimitError as exc:
            raise AIRateLimited("HTTP 429", _retry_after(exc.response)) from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise AIAuthenticationFailed(f"HTTP {exc.status_code}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise AIServerError(f"HTTP {exc.status_code}") from exc
            raise AIRequestRejected(f"HTTP {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIServerError("connection error") from exc
        if response.stop_reason == "refusal":
            raise AIRefused("the model declined the request")
        if response.stop_reason == "max_tokens":
            raise AIMalformedOutput("output truncated at max_tokens")
        text = "".join(block.text for block in response.content if block.type == "text")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIMalformedOutput("response is not valid JSON") from exc
        if not isinstance(data, dict):
            raise AIMalformedOutput("response is not a JSON object")
        usage = getattr(response, "usage", None)
        return StructuredResponse(
            data=data,
            provider=self.name,
            model=response.model or self.model,
            usage={
                "input_tokens": getattr(usage, "input_tokens", None),
                "output_tokens": getattr(usage, "output_tokens", None),
            },
        )
