"""Provider-independent structured-generation boundary (Phase 7).

Domain services depend only on ``AIProvider``: they pass a system prompt, a user message, and a
plain JSON schema, and receive parsed JSON plus the provider/model that produced it. Concrete
adapters (``ai/providers``) translate provider errors into the exceptions below, so retry and
failure handling are the same for every provider. Nothing here knows about Events or databases.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


class AIProviderError(Exception):
    """Base class; ``code`` is a short, non-sensitive label stored with failed artifacts."""

    code = "provider_error"
    retryable = False


class AIProviderNotConfigured(AIProviderError):
    code = "not_configured"


class AITimeout(AIProviderError):
    code = "timeout"
    retryable = True


class AIRateLimited(AIProviderError):
    code = "rate_limited"
    retryable = True

    def __init__(self, message="rate limited", retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


class AIServerError(AIProviderError):
    """5xx or a connection failure: transient."""

    code = "server_error"
    retryable = True


class AIRequestRejected(AIProviderError):
    """A non-retryable 4xx (bad request, authentication, permission, unknown model)."""

    code = "request_rejected"


class AIAuthenticationFailed(AIRequestRejected):
    """401/403: the credential was refused. Stored like any rejection (``request_rejected``), but
    a batch stops at the first one instead of sending more requests with the same credential."""

    authentication = True


class AIRefused(AIProviderError):
    """The model declined the request."""

    code = "refused"


class AIMalformedOutput(AIProviderError):
    """The response was not a complete JSON object (truncated, non-JSON, or empty)."""

    code = "malformed_output"


@dataclass(frozen=True)
class StructuredResponse:
    data: dict[str, Any]
    provider: str
    # The model the provider reports having served the request (provenance), not the request.
    model: str
    usage: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class AIProvider(Protocol):
    name: str
    model: str

    async def generate_structured(
        self, *, system: str, user: str, schema: dict[str, Any], schema_name: str
    ) -> StructuredResponse:
        """One request constrained to ``schema``; raises an ``AIProviderError`` on failure."""


MAX_RETRY_DELAY = 60.0


async def preflight(provider):
    """Zero-generation check that the credential and model are accepted, before a batch.

    Adapters may define ``preflight()`` (Anthropic: a Models API lookup, no Messages request);
    a provider without one passes. Raises an ``AIProviderError`` (``AIAuthenticationFailed`` for a
    refused credential); never retried here.
    """

    check = getattr(provider, "preflight", None)
    if check is not None:
        await check()


async def generate_with_retry(provider, *, max_retries, base_delay=2.0, sleep=None, **request):
    """Call ``provider`` with bounded retries of transient failures only.

    Timeouts, 429, and 5xx are retried at most ``max_retries`` times with exponential backoff;
    a provider ``retry_after`` is honoured (capped at ``MAX_RETRY_DELAY``). Everything else is
    raised immediately. Returns ``(response, attempts)``; a final failure carries ``attempts``.
    """

    sleep = sleep or asyncio.sleep
    attempt = 0
    while True:
        attempt += 1
        try:
            return await provider.generate_structured(**request), attempt
        except AIProviderError as exc:
            exc.attempts = attempt
            if not exc.retryable or attempt > max_retries:
                raise
            delay = getattr(exc, "retry_after", None)
            if delay is None:
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.5)
            await sleep(min(float(delay), MAX_RETRY_DELAY))
