"""
Regression tests for app.services.rag_service.ingest_document.

Guards against the shadowing bug where the function parameter named ``text``
hid the module-level ``from sqlalchemy import text`` import, so the
``text(<INSERT sql>)`` call raised ``TypeError: 'str' object is not
callable`` and every ingestion was silently marked ``failed``.

These tests mock the embedding service and the DB session, so they need
neither Vertex AI nor a real Postgres/pgvector instance.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.sql.elements import TextClause

from app.db.models import DocumentStatus
from app.services.rag_service import rag_service


def _mock_db_with_document(doc):
    """An AsyncSession-like mock whose first execute() yields ``doc``.

    The first ``db.execute`` is the ``select(KnowledgeDocument)`` lookup; its
    result must expose ``.scalar_one_or_none() -> doc``. Every later
    ``db.execute`` is a raw INSERT whose result is irrelevant here.
    """
    select_result = MagicMock()
    select_result.scalar_one_or_none.return_value = doc

    db = AsyncMock()
    db.execute.return_value = select_result
    db.flush = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_ingest_document_uses_sqlalchemy_text(monkeypatch):
    """The INSERT must be built with sqlalchemy.text (not the str param)."""
    # Two chunks -> two INSERT executes, each with a 3-dim fake vector.
    monkeypatch.setattr(
        "app.services.rag_service.embedding_service.chunk_text",
        lambda txt, size, overlap: ["chunk one", "chunk two"],
    )
    monkeypatch.setattr(
        "app.services.rag_service.embedding_service.embed_batch",
        AsyncMock(return_value=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]),
    )
    monkeypatch.setattr(
        "app.services.rag_service.embedding_service.estimate_tokens",
        lambda content: 5,
    )

    doc = MagicMock()
    doc.status = None
    db = _mock_db_with_document(doc)

    count = await rag_service.ingest_document(
        document_id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        document_text="some extracted document text",
        db=db,
    )

    # Did not raise TypeError, processed both chunks.
    assert count == 2
    assert doc.status == DocumentStatus.completed

    # The INSERT statements were genuine SQLAlchemy TextClause objects.
    insert_calls = [
        c for c in db.execute.call_args_list
        if c.args and isinstance(c.args[0], TextClause)
    ]
    assert len(insert_calls) == 2, "expected two text()-built INSERTs"
    assert "INSERT INTO knowledge_chunks" in str(insert_calls[0].args[0])


@pytest.mark.asyncio
async def test_ingest_document_missing_document_returns_zero():
    """No matching KnowledgeDocument -> returns 0, no embedding work."""
    db = _mock_db_with_document(None)

    count = await rag_service.ingest_document(
        document_id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        document_text="irrelevant",
        db=db,
    )

    assert count == 0


@pytest.mark.asyncio
async def test_ingest_document_no_chunks_marks_failed(monkeypatch):
    """Empty extracted text -> status failed, returns 0."""
    monkeypatch.setattr(
        "app.services.rag_service.embedding_service.chunk_text",
        lambda txt, size, overlap: [],
    )

    doc = MagicMock()
    db = _mock_db_with_document(doc)

    count = await rag_service.ingest_document(
        document_id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        document_text="",
        db=db,
    )

    assert count == 0
    assert doc.status == DocumentStatus.failed
