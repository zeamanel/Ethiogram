# app/services/rag_service.py
import uuid
from typing import Optional

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.db.models import (
    DocumentStatus,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeItem,
)
from app.services.embedding_service import embedding_service

logger = get_logger(__name__)

_DEFAULT_TOP_K = settings.rag_default_top_k
_DEFAULT_THRESHOLD = settings.rag_default_similarity_threshold


class RagService:
    """
    Retrieval-Augmented Generation engine backed by pgvector.

    Chunks are stored with a 768-dim embedding column (Vector(768)) created
    by the SQL migration.  This service queries via raw SQL using the
    pgvector <=> (cosine distance) operator since SQLAlchemy's ORM layer
    doesn't natively express vector ops.
    """

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        business_id: str | uuid.UUID,
        db: AsyncSession,
        top_k: int = _DEFAULT_TOP_K,
        similarity_threshold: float = _DEFAULT_THRESHOLD,
    ) -> list[dict]:
        """
        Embed query, run cosine similarity search, return ranked chunks.
        Each result dict: {id, content, similarity, chunk_index, document_id}
        """
        query_vector = await embedding_service.embed_text(query)
        if all(v == 0.0 for v in query_vector):
            logger.warning("Zero query vector — skipping RAG search", business_id=str(business_id))
            return []

        vector_literal = f"[{','.join(str(v) for v in query_vector)}]"

        sql = text("""
            SELECT
                id,
                content,
                chunk_index,
                document_id,
                1 - (embedding <=> :query_vec::vector) AS similarity
            FROM knowledge_chunks
            WHERE business_id = :business_id
              AND 1 - (embedding <=> :query_vec::vector) >= :threshold
            ORDER BY embedding <=> :query_vec::vector
            LIMIT :top_k
        """)

        result = await db.execute(
            sql,
            {
                "query_vec": vector_literal,
                "business_id": str(business_id),
                "threshold": similarity_threshold,
                "top_k": top_k,
            },
        )
        rows = result.fetchall()

        chunks = [
            {
                "id": str(row.id),
                "content": row.content,
                "chunk_index": row.chunk_index,
                "document_id": str(row.document_id) if row.document_id else None,
                "similarity": round(float(row.similarity), 4),
            }
            for row in rows
        ]

        logger.info(
            "RAG search complete",
            business_id=str(business_id),
            query_len=len(query),
            results=len(chunks),
            top_similarity=chunks[0]["similarity"] if chunks else 0,
        )
        return chunks

    def build_context_string(
        self, chunks: list[dict], max_chars: int = 4000
    ) -> str:
        """
        Format retrieved chunks into a context block for the LLM prompt.
        Respects a character budget to avoid blowing the context window.
        """
        if not chunks:
            return ""

        parts: list[str] = ["=== Relevant business information ==="]
        total = len(parts[0])

        for i, chunk in enumerate(chunks, start=1):
            snippet = chunk["content"].strip()
            entry = f"\n[{i}] {snippet}"
            if total + len(entry) > max_chars:
                break
            parts.append(entry)
            total += len(entry)

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingest_document(
        self,
        document_id: uuid.UUID,
        business_id: uuid.UUID,
        text: str,
        db: AsyncSession,
        chunk_size: int = 500,
        overlap: int = 50,
    ) -> int:
        """
        Chunk text, embed all chunks, write KnowledgeChunk rows.
        Returns the number of chunks created.
        Updates the KnowledgeDocument status to completed/failed.
        """
        doc_result = await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.id == document_id)
        )
        doc = doc_result.scalar_one_or_none()
        if doc is None:
            logger.error("Document not found for ingestion", document_id=str(document_id))
            return 0

        doc.status = DocumentStatus.processing
        await db.flush()

        try:
            raw_chunks = embedding_service.chunk_text(text, chunk_size, overlap)
            if not raw_chunks:
                doc.status = DocumentStatus.failed
                doc.error_message = "No text content extracted"
                return 0

            vectors = await embedding_service.embed_batch(raw_chunks)

            # Insert chunks via raw SQL to use the pgvector literal cast
            for idx, (content, vector) in enumerate(zip(raw_chunks, vectors)):
                token_count = embedding_service.estimate_tokens(content)
                vector_literal = f"[{','.join(str(v) for v in vector)}]"

                await db.execute(
                    text("""
                        INSERT INTO knowledge_chunks
                            (id, business_id, document_id, content, token_count,
                             chunk_index, embedding_model, embedding, created_at, updated_at)
                        VALUES
                            (gen_random_uuid(), :business_id, :document_id, :content,
                             :token_count, :chunk_index, :model,
                             :embedding::vector, NOW(), NOW())
                    """),
                    {
                        "business_id": str(business_id),
                        "document_id": str(document_id),
                        "content": content,
                        "token_count": token_count,
                        "chunk_index": idx,
                        "model": settings.embedding_model_id,
                        "embedding": vector_literal,
                    },
                )

            doc.status = DocumentStatus.completed
            doc.chunk_count = len(raw_chunks)

            from datetime import datetime, timezone
            doc.processed_at = datetime.now(timezone.utc)

            logger.info(
                "Document ingested",
                document_id=str(document_id),
                business_id=str(business_id),
                chunks=len(raw_chunks),
            )
            return len(raw_chunks)

        except Exception as exc:
            doc.status = DocumentStatus.failed
            doc.error_message = str(exc)
            logger.error(
                "Document ingestion failed",
                document_id=str(document_id),
                error=str(exc),
            )
            raise

    async def delete_document_chunks(
        self, document_id: uuid.UUID, db: AsyncSession
    ) -> int:
        """Delete all chunks for a document. Returns count deleted."""
        result = await db.execute(
            text("DELETE FROM knowledge_chunks WHERE document_id = :doc_id RETURNING id"),
            {"doc_id": str(document_id)},
        )
        count = len(result.fetchall())
        logger.info("Chunks deleted", document_id=str(document_id), count=count)
        return count

    async def delete_business_chunks(
        self, business_id: uuid.UUID, db: AsyncSession
    ) -> int:
        """Delete ALL chunks for a business (e.g. on account deletion)."""
        result = await db.execute(
            text("DELETE FROM knowledge_chunks WHERE business_id = :biz_id RETURNING id"),
            {"biz_id": str(business_id)},
        )
        count = len(result.fetchall())
        logger.info("All business chunks deleted", business_id=str(business_id), count=count)
        return count

    # ------------------------------------------------------------------
    # Knowledge Item helpers
    # ------------------------------------------------------------------

    async def get_knowledge_items(
        self,
        business_id: uuid.UUID,
        db: AsyncSession,
        item_type: Optional[str] = None,
        active_only: bool = True,
    ) -> list[KnowledgeItem]:
        stmt = select(KnowledgeItem).where(KnowledgeItem.business_id == business_id)
        if item_type:
            stmt = stmt.where(KnowledgeItem.item_type == item_type)
        if active_only:
            stmt = stmt.where(KnowledgeItem.is_active.is_(True))
        result = await db.execute(stmt)
        return list(result.scalars().all())

    def format_knowledge_items_for_prompt(self, items: list[KnowledgeItem]) -> str:
        """Render structured knowledge items as readable text for the system prompt."""
        if not items:
            return ""
        lines = ["=== Business catalog ==="]
        for item in items:
            line = f"• [{item.item_type.value.upper()}] {item.title}"
            if item.body:
                line += f": {item.body[:200]}"
            if item.data:
                price = item.data.get("price")
                if price:
                    line += f" — Price: {price}"
            lines.append(line)
        return "\n".join(lines)


rag_service = RagService()
