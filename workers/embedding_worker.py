# workers/embedding_worker.py
"""
Async document embedding worker.

Polls the knowledge_documents table for rows with status='pending',
processes each one (OCR → chunk → embed → insert chunks), and marks
the document completed or failed.

Run as a standalone process:
    python -m workers.embedding_worker

In production this is a Cloud Run Job triggered by Cloud Scheduler
every 60 seconds, or by a Pub/Sub message from the upload endpoint.
"""
import asyncio
import sys
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_):
        pass


def _start_health_server(port: int = 8080) -> None:
    """Start a minimal HTTP health check server in a background daemon thread."""
    server = HTTPServer(("", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.models import DocumentStatus, KnowledgeDocument
from app.db.session import connect_db, connect_redis, disconnect_db, disconnect_redis, get_db_context
from app.services.ocr_service import ocr_service
from app.services.rag_service import rag_service
from app.services.storage_service import storage_service

configure_logging()
logger = get_logger(__name__)

_BATCH_SIZE = 5          # documents processed per run
_POLL_INTERVAL = 30      # seconds between polls in continuous mode


async def process_pending_documents() -> int:
    """
    Fetch up to _BATCH_SIZE pending documents, process each one.
    Returns number of documents successfully processed.
    """
    processed = 0

    async with get_db_context() as db:
        result = await db.execute(
            select(KnowledgeDocument)
            .where(KnowledgeDocument.status == DocumentStatus.pending)
            .order_by(KnowledgeDocument.created_at.asc())
            .limit(_BATCH_SIZE)
            .with_for_update(skip_locked=True)  # safe for concurrent workers
        )
        docs = result.scalars().all()

        if not docs:
            logger.info("No pending documents found")
            return 0

        logger.info(f"Processing {len(docs)} pending documents")

        for doc in docs:
            try:
                await _process_document(doc, db)
                processed += 1
            except Exception as exc:
                logger.error(
                    "Document processing failed",
                    document_id=str(doc.id),
                    filename=doc.filename,
                    error=str(exc),
                )
                doc.status = DocumentStatus.failed
                doc.error_message = str(exc)

    return processed


async def _process_document(doc: KnowledgeDocument, db) -> None:
    logger.info(
        "Processing document",
        document_id=str(doc.id),
        filename=doc.filename,
        file_type=doc.file_type,
        business_id=str(doc.business_id),
    )

    doc.status = DocumentStatus.processing
    await db.flush()

    # 1. Download from GCS
    file_bytes = await storage_service.download_file(doc.gcs_path)

    # 2. Extract text via OCR/parsing
    extracted_text = await ocr_service.extract_text(file_bytes, doc.filename)

    if not extracted_text.strip():
        raise ValueError("No text content could be extracted from document")

    doc.extracted_text = extracted_text[:50_000]  # cap stored text at 50k chars

    # 3. Chunk + embed + persist
    chunk_count = await rag_service.ingest_document(
        document_id=doc.id,
        business_id=doc.business_id,
        text=extracted_text,
        db=db,
    )

    logger.info(
        "Document processed",
        document_id=str(doc.id),
        filename=doc.filename,
        chunks=chunk_count,
        text_chars=len(extracted_text),
    )


async def run_once() -> None:
    _start_health_server()
    await connect_db()
    await connect_redis()
    try:
        count = await process_pending_documents()
        logger.info(f"Embedding worker run complete", documents_processed=count)
    finally:
        await disconnect_db()
        await disconnect_redis()


async def run_continuous() -> None:
    """Run in a poll loop (for local dev or single-container deployments)."""
    _start_health_server()
    await connect_db()
    await connect_redis()
    logger.info(f"Embedding worker started (poll interval: {_POLL_INTERVAL}s)")
    try:
        while True:
            try:
                await process_pending_documents()
            except Exception as exc:
                logger.error("Worker loop error", error=str(exc))
            await asyncio.sleep(_POLL_INTERVAL)
    finally:
        await disconnect_db()
        await disconnect_redis()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "once"
    if mode == "continuous":
        asyncio.run(run_continuous())
    else:
        asyncio.run(run_once())
