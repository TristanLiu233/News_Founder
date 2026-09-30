"""AI provider configuration, the Anthropic adapter (mock transport), and bounded retries.

No test calls a real AI service: the adapter talks to an ``httpx2.MockTransport``.
"""

import asyncio
import json

import httpx2
import pytest
from anthropic import AsyncAnthropic

from industry_intelligence.ai.provider import (
    AIMalformedOutput,
    AIProviderNotConfigured,
    AIRateLimited,
    AIRefused,
    AIRequestRejected,
    AIServerError,
    AITimeout,
    generate_with_retry,
)
from industry_intelligence.ai.providers import build_provider
from industry_intelligence.ai.providers.anthropic import AnthropicProvider
from industry_intelligence.ai.providers.fixture import FixtureProvider
from industry_intelligence.config import Settings

KEY = "test-key-not-real"


def ai_settings(**overrides):
    values = {
        "_env_file": None,
        "database_url": "postgresql+psycopg://localhost/x",
        "ai_provider": "anthropic",
        "ai_model": "test-model",
        "ai_api_key": KEY,
        **overrides,
    }
    return Settings(**values)


def message(text, stop_reason="end_turn", model="test-model-served"):
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 11, "output_tokens": 7},
    }


def adapter(handler, **overrides):
    settings = ai_settings(**overrides)
    client = AsyncAnthropic(
        api_key=settings.ai_api_key,
        base_url="https://api.anthropic.com",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return AnthropicProvider(settings, client=client)


def call(provider):
    return asyncio.run(
        provider.generate_structured(
            system="system", user="user", schema={"type": "object"}, schema_name="x"
        )
    )


# --- configuration -------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["", "  ", "your-api-key", "your_key", "<key>", "changeme"])
def test_blank_and_placeholder_ai_settings_count_as_unset(value):
    settings = ai_settings(ai_api_key=value)
    assert settings.ai_api_key is None
    assert not settings.ai_configured


def test_ai_key_is_hidden_from_settings_repr():
    assert KEY not in repr(ai_settings())


def test_missing_configuration_never_builds_a_provider():
    with pytest.raises(AIProviderNotConfigured):
        build_provider(ai_settings(ai_api_key=None))
    with pytest.raises(AIProviderNotConfigured):
        build_provider(ai_settings(ai_model=None))
    with pytest.raises(AIProviderNotConfigured, match="Unknown AI_PROVIDER"):
        build_provider(ai_settings(ai_provider="unknown"))


def test_configured_anthropic_provider_uses_only_project_settings(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://ambient.invalid")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ambient-key")
    provider = build_provider(ai_settings())
    assert (provider.name, provider.model) == ("anthropic", "test-model")
    assert str(provider._client.base_url).startswith("https://api.anthropic.com")
    assert provider._client.api_key == KEY
    assert provider._client.max_retries == 0


# --- Anthropic adapter ---------------------------------------------------------------------


def test_structured_response_is_parsed_with_served_model_and_usage():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(200, json=message('{"points": []}'))

    response = call(adapter(handler))
    body = json.loads(seen[0].content)
    assert seen[0].headers["x-api-key"] == KEY
    assert body["model"] == "test-model"
    assert body["system"] == "system"
    assert body["output_config"] == {
        "format": {"type": "json_schema", "schema": {"type": "object"}}
    }
    assert response.data == {"points": []}
    assert (response.provider, response.model) == ("anthropic", "test-model-served")
    assert response.usage == {"input_tokens": 11, "output_tokens": 7}


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (500, AIServerError),
        (503, AIServerError),
        (400, AIRequestRejected),
        (401, AIRequestRejected),
    ],
)
def test_http_errors_map_to_retryable_or_final_errors(status, error):
    def handler(request):
        return httpx2.Response(
            status, json={"type": "error", "error": {"type": "x", "message": "m"}}
        )

    with pytest.raises(error):
        call(adapter(handler))


def test_rate_limit_carries_retry_after():
    def handler(request):
        return httpx2.Response(
            429,
            headers={"retry-after": "7"},
            json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow"}},
        )

    with pytest.raises(AIRateLimited) as caught:
        call(adapter(handler))
    assert caught.value.retry_after == 7.0
    assert caught.value.retryable


def test_timeout_maps_to_retryable_timeout():
    def handler(request):
        raise httpx2.ReadTimeout("slow", request=request)

    with pytest.raises(AITimeout):
        call(adapter(handler))


@pytest.mark.parametrize(
    ("text", "stop_reason", "error"),
    [
        ("not json", "end_turn", AIMalformedOutput),
        ('["a list"]', "end_turn", AIMalformedOutput),
        ('{"points": [', "max_tokens", AIMalformedOutput),
        ("", "refusal", AIRefused),
    ],
)
def test_malformed_truncated_and_refused_output(text, stop_reason, error):
    def handler(request):
        return httpx2.Response(200, json=message(text, stop_reason))

    with pytest.raises(error):
        call(adapter(handler))


# --- bounded retry -------------------------------------------------------------------------


def run_retry(responder, max_retries=2):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    provider = FixtureProvider(responder)
    try:
        result = asyncio.run(
            generate_with_retry(
                provider,
                max_retries=max_retries,
                sleep=sleep,
                system="s",
                user="u",
                schema={},
                schema_name="x",
            )
        )
    except Exception as exc:  # noqa: BLE001 - the test inspects the raised error
        result = exc
    return result, delays, provider.calls


def test_transient_errors_are_retried_and_retry_after_is_honoured():
    outcomes = iter([AIRateLimited(retry_after=5), AIServerError(), {"ok": True}])

    def responder(**_):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    (response, attempts), delays, calls = run_retry(responder)
    assert response.data == {"ok": True}
    assert attempts == 3 and len(calls) == 3
    assert delays[0] == 5


def test_retries_are_bounded():
    def responder(**_):
        raise AITimeout()

    error, delays, calls = run_retry(responder, max_retries=2)
    assert isinstance(error, AITimeout)
    assert len(calls) == 3 and len(delays) == 2
    assert error.attempts == 3


def test_retry_after_is_capped():
    def responder(**_):
        raise AIRateLimited(retry_after=3600)

    _, delays, _ = run_retry(responder, max_retries=1)
    assert delays == [60.0]


@pytest.mark.parametrize("error", [AIRequestRejected, AIMalformedOutput, AIRefused])
def test_non_transient_errors_are_not_retried(error):
    def responder(**_):
        raise error()

    raised, delays, calls = run_retry(responder)
    assert isinstance(raised, error)
    assert len(calls) == 1 and delays == []


# --- Product-1: refused credentials and the zero-generation preflight ------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_refused_credentials_are_authentication_failures_and_never_retried(status):
    from industry_intelligence.ai.provider import AIAuthenticationFailed

    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx2.Response(
            status, json={"type": "error", "error": {"type": "auth", "message": "m"}}
        )

    provider = adapter(handler)
    with pytest.raises(AIAuthenticationFailed) as caught:
        asyncio.run(
            generate_with_retry(
                provider,
                max_retries=3,
                sleep=lambda _s: asyncio.sleep(0),
                system="s",
                user="u",
                schema={"type": "object"},
                schema_name="x",
            )
        )
    # Stored like any rejection, but flagged so a batch stops; one request only.
    assert caught.value.code == "request_rejected" and caught.value.authentication
    assert calls == ["/v1/messages"]


def test_preflight_checks_the_model_without_a_messages_request():
    from industry_intelligence.ai.provider import AIAuthenticationFailed, preflight

    seen = []

    def ok(request):
        seen.append((request.method, request.url.path))
        return httpx2.Response(
            200,
            json={
                "type": "model",
                "id": "test-model",
                "display_name": "Test",
                "created_at": "2026-01-01T00:00:00Z",
            },
        )

    asyncio.run(preflight(adapter(ok)))
    assert seen == [("GET", "/v1/models/test-model")]

    def refused(request):
        seen.append((request.method, request.url.path))
        return httpx2.Response(
            401, json={"type": "error", "error": {"type": "authentication_error", "message": "m"}}
        )

    with pytest.raises(AIAuthenticationFailed):
        asyncio.run(preflight(adapter(refused)))
    assert all(path != "/v1/messages" for _, path in seen)


def test_providers_without_a_preflight_pass_it():
    from industry_intelligence.ai.provider import preflight

    asyncio.run(preflight(FixtureProvider(lambda **_: {})))
