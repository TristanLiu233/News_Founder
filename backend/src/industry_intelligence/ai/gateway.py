"""Provider-independent AI gateway protocol; no provider SDK is used here."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, TypeVar, runtime_checkable

StructuredOutput = TypeVar("StructuredOutput")


@runtime_checkable
class AIGateway(Protocol):
    """The sole future boundary between domain code and AI providers."""

    async def generate(self, *, prompt: str, system_prompt: str | None = None) -> str:
        """Generate unstructured text."""

    async def structured_generate(
        self,
        *,
        prompt: str,
        schema: type[StructuredOutput],
        system_prompt: str | None = None,
    ) -> StructuredOutput:
        """Generate an instance matching a caller-owned structured schema."""

    async def embed(self, *, texts: Sequence[str]) -> list[list[float]]:
        """Create embeddings when semantic features are introduced in a later phase."""

    async def classify(self, *, text: str, labels: Sequence[str]) -> str:
        """Classify text into one of the supplied labels."""
