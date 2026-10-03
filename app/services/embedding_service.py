"""
Embedding generation service.

Wraps ``langchain_openai.OpenAIEmbeddings`` so the rest of the application
depends on a stable interface rather than a specific SDK. Embeddings are
generated using OpenAI's ``text-embedding-3-small`` model by default, which
offers a strong accuracy/cost trade-off for enterprise RAG workloads.
"""

from __future__ import annotations

import logging

from langchain_openai import OpenAIEmbeddings

from app.config import Settings, get_settings
from app.core.exceptions import MissingAPIKeyError

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Produces vector embeddings for text using OpenAI's embedding models."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._embeddings: OpenAIEmbeddings | None = None

    @property
    def client(self) -> OpenAIEmbeddings:
        """Lazily instantiate the LangChain embeddings client."""
        if self._embeddings is None:
            api_key = self._settings.embedding_api_key or self._settings.openai_api_key
            api_base = self._settings.embedding_api_base or self._settings.openai_api_base
            if not api_key:
                raise MissingAPIKeyError()
            self._embeddings = OpenAIEmbeddings(
                model=self._settings.openai_embedding_model,
                api_key=api_key,  # type: ignore[arg-type]
                base_url=api_base,
                timeout=self._settings.openai_request_timeout,
            )
            logger.info(
                "Initialized OpenAIEmbeddings (%s, base=%s)",
                self._settings.openai_embedding_model,
                api_base,
            )
        return self._embeddings

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (used when indexing document chunks)."""
        if not texts:
            return []
        logger.info("Embedding %d text chunks", len(texts))
        return self.client.embed_documents(texts)

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string (used at retrieval time)."""
        return self.client.embed_query(text)
