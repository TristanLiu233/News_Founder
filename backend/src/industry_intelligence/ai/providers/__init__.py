"""Explicit registry of AI provider adapters; selected by the ``AI_PROVIDER`` setting."""

from __future__ import annotations

from industry_intelligence.ai.provider import AIProviderNotConfigured


def _anthropic(settings):
    from industry_intelligence.ai.providers.anthropic import AnthropicProvider

    return AnthropicProvider(settings)


PROVIDERS = {"anthropic": _anthropic}


def build_provider(settings):
    """The configured provider, or ``AIProviderNotConfigured`` (never a silent default)."""

    if not settings.ai_configured:
        raise AIProviderNotConfigured("AI_PROVIDER, AI_MODEL, and AI_API_KEY must all be set")
    factory = PROVIDERS.get(settings.ai_provider.lower())
    if factory is None:
        raise AIProviderNotConfigured(
            f"Unknown AI_PROVIDER {settings.ai_provider!r}; registered: {', '.join(PROVIDERS)}"
        )
    return factory(settings)
