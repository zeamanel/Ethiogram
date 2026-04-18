# app/services/embedding_service.py
import asyncio
from typing import Optional

from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)

_EMBEDDING_DIM = 768
_BATCH_SIZE = 100  # Vertex AI text-embedding-004 max per request


class EmbeddingService:
    """
    Generates 768-dimensional text embeddings using Google text-embedding-004
    via Vertex AI. Falls back to a zero vector on hard failure so document
    ingestion never crashes the entire pipeline.
    """

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google.cloud import aiplatform
                aiplatform.init(
                    project=settings.vertex_project,
                    location=settings.vertex_ai_location,
                )
                from vertexai.language_models import TextEmbeddingModel
                self._client = TextEmbeddingModel.from_pretrained(settings.embedding_model_id)
            except Exception as exc:
                logger.error("Failed to init Vertex AI embedding client", error=str(exc))
                raise ExternalServiceError("Vertex AI", str(exc))
        return self._client

    async def embed_text(self, text: str) -> list[float]:
        """Embed a single string. Returns 768-dim float list."""
        if not text or not text.strip():
            return [0.0] * _EMBEDDING_DIM
        results = await self.embed_batch([text])
        return results[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of strings in batches of up to _BATCH_SIZE.
        Returns a list of 768-dim float vectors in the same order.
        """
        if not texts:
            return []

        all_vectors: list[list[float]] = []

        for i in range(0, len(texts), _BATCH_SIZE):
            chunk = texts[i : i + _BATCH_SIZE]
            vectors = await asyncio.get_event_loop().run_in_executor(
                None, self._embed_batch_sync, chunk
            )
            all_vectors.extend(vectors)

        return all_vectors

    def _embed_batch_sync(self, texts: list[str]) -> list[list[float]]:
        """Blocking Vertex AI call — run inside executor."""
        try:
            client = self._get_client()
            embeddings = client.get_embeddings(texts)
            return [e.values for e in embeddings]
        except Exception as exc:
            logger.error(
                "Embedding batch failed, returning zero vectors",
                batch_size=len(texts),
                error=str(exc),
            )
            # Zero fallback keeps ingestion alive; chunks won't be retrieved
            # via similarity search but document still processes
            return [[0.0] * _EMBEDDING_DIM for _ in texts]

    # ------------------------------------------------------------------
    # Chunking utilities (used by document ingestion pipeline)
    # ------------------------------------------------------------------

    def chunk_text(
        self,
        text: str,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> list[str]:
        """
        Split text into token-approximate chunks with overlap.
        Uses whitespace-word splitting as a fast proxy for tokens
        (1 word ≈ 1.3 tokens for multilingual text).
        """
        words = text.split()
        if not words:
            return []

        chunks: list[str] = []
        start = 0
        while start < len(words):
            end = start + chunk_size
            chunk_words = words[start:end]
            chunks.append(" ".join(chunk_words))
            if end >= len(words):
                break
            start = end - overlap  # slide back by overlap

        return [c for c in chunks if c.strip()]

    def estimate_tokens(self, text: str) -> int:
        """Fast token count estimate: words * 1.3, rounded up."""
        return int(len(text.split()) * 1.3) + 1


embedding_service = EmbeddingService()
