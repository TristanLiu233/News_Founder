"""Replaceable embedding capability; no provider dependency in retrieval logic."""

import math
from typing import Protocol

import httpx


class EmbeddingProvider(Protocol):
    model: str
    dimension: int

    def embed(self, text: str) -> list[float]: ...


def validate_vector(vector, dimension):
    if len(vector) != dimension or not all(math.isfinite(x) for x in vector):
        raise ValueError("Invalid embedding dimension or non-finite values")
    if not any(vector):
        raise ValueError("Cosine distance requires a nonzero embedding")
    return vector


class CompatibleEmbeddingProvider:
    def __init__(self, settings):
        self.settings = settings
        self.model = settings.embedding_model
        self.dimension = settings.embedding_dimension

    def embed(self, text):
        headers = {}
        if self.settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {self.settings.embedding_api_key}"
        response = httpx.post(
            self.settings.embedding_url,
            headers=headers,
            json={"model": self.model, "input": text},
            timeout=self.settings.embedding_timeout,
        )
        response.raise_for_status()
        vector = response.json()["data"][0]["embedding"]
        return validate_vector(vector, self.dimension)


def embedding_provider(settings):
    if not settings.embedding_url or not settings.embedding_model:
        return None
    return CompatibleEmbeddingProvider(settings)
