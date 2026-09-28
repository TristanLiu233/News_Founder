"""Deterministic provider for tests and offline previews; never calls a network service."""

from __future__ import annotations

from industry_intelligence.ai.provider import StructuredResponse


class FixtureProvider:
    """Answers with ``responder(system=, user=, schema=, schema_name=)``.

    The responder returns a dict (the structured output) or raises an ``AIProviderError``.
    Every request is recorded in ``calls`` so tests can assert request counts and content.
    """

    name = "fixture"

    def __init__(self, responder, model="fixture-model"):
        self.model = model
        self._responder = responder
        self.calls = []

    async def generate_structured(self, *, system, user, schema, schema_name):
        request = {"system": system, "user": user, "schema": schema, "schema_name": schema_name}
        self.calls.append(request)
        data = self._responder(**request)
        return StructuredResponse(data=data, provider=self.name, model=self.model)
