# app/api/knowledge.py
import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import Business, DocumentStatus, KnowledgeDocument
from app.db.session import get_db
from app.services.rag_service import rag_service
from app.services.storage_service import storage_service

logger = get_logger(__name__)

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# Accepted upload types and the 50 MB cap (matches settings.max_file_upload_mb).
_MAX_BYTES = 50 * 1024 * 1024
_ALLOWED_EXT = {"txt", "md", "csv", "pdf", "docx", "xlsx"}


class DocumentResponse(BaseModel):
    id: str
    filename: str
    file_type: str
    file_size_bytes: int
    status: str
    chunk_count: int
    error_message: str | None = None
    created_at: str


async def _assert_owns_business(user_id: uuid.UUID, business_id: uuid.UUID, db: AsyncSession) -> None:
    result = await db.execute(
        select(Business.id).where(Business.id == business_id, Business.owner_id == user_id)
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Business", str(business_id))


def _doc_to_response(doc: KnowledgeDocument) -> DocumentResponse:
    return DocumentResponse(
        id=str(doc.id),
        filename=doc.filename,
        file_type=doc.file_type,
        file_size_bytes=doc.file_size_bytes,
        status=doc.status.value if hasattr(doc.status, "value") else str(doc.status),
        chunk_count=doc.chunk_count,
        error_message=doc.error_message,
        created_at=doc.created_at.isoformat() if doc.created_at else "",
    )


@router.post("/{business_id}/documents", response_model=DocumentResponse, status_code=201)
async def upload_document(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    """
    Upload a knowledge document. Stores the file in GCS and creates a
    KnowledgeDocument in status=pending; the embedding worker then extracts
    text, chunks, embeds, and persists searchable knowledge_chunks.
    """
    await _assert_owns_business(current_user.id, business_id, db)

    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_EXT:
        raise ValidationError(f"Unsupported file type '.{ext}'. Allowed: {sorted(_ALLOWED_EXT)}")

    data = await file.read()
    if not data:
        raise ValidationError("Empty file")
    if len(data) > _MAX_BYTES:
        raise ValidationError(f"File exceeds {_MAX_BYTES // (1024*1024)} MB limit")

    # gcs_path is the OBJECT PATH (not a gs:// URI) so the worker's
    # download_file(doc.gcs_path) resolves against the default bucket.
    gcs_path = storage_service.build_document_path(str(business_id), filename)
    await storage_service.upload_file(data, gcs_path, content_type=file.content_type)

    doc = KnowledgeDocument(
        business_id=business_id,
        filename=filename,
        file_type=ext,
        file_size_bytes=len(data),
        gcs_path=gcs_path,
        status=DocumentStatus.pending,
        uploaded_by_id=current_user.id,
    )
    db.add(doc)
    await db.flush()
    logger.info("Knowledge document uploaded", document_id=str(doc.id),
                business_id=str(business_id), filename=filename, bytes=len(data))
    return _doc_to_response(doc)


@router.get("/{business_id}/documents", response_model=list[DocumentResponse])
async def list_documents(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[DocumentResponse]:
    await _assert_owns_business(current_user.id, business_id, db)
    result = await db.execute(
        select(KnowledgeDocument)
        .where(KnowledgeDocument.business_id == business_id)
        .order_by(KnowledgeDocument.created_at.desc())
    )
    return [_doc_to_response(d) for d in result.scalars().all()]


@router.delete("/{business_id}/documents/{document_id}", status_code=204)
async def delete_document(
    business_id: uuid.UUID,
    document_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> None:
    await _assert_owns_business(current_user.id, business_id, db)
    result = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.id == document_id,
            KnowledgeDocument.business_id == business_id,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise NotFoundError("KnowledgeDocument", str(document_id))
    await rag_service.delete_document_chunks(document_id, db)
    await db.delete(doc)
