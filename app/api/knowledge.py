# app/api/knowledge.py
import uuid

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.db.models import (
    Business,
    DocumentStatus,
    KnowledgeDocument,
    KnowledgeItem,
    KnowledgeItemType,
)
from app.db.session import get_db, get_redis
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


# ──────────────────────────────────────────────────────────────────────────────
# Structured catalog — knowledge_items (products, services, FAQs, policies).
# Unlike documents these are curated rows injected verbatim into the bot prompt
# (see BaseAgent.process), so a business can keep a precise, always-on catalog
# without uploading a file.
# ──────────────────────────────────────────────────────────────────────────────

_ITEM_TYPES = {t.value for t in KnowledgeItemType}

# Catalog images go to the PUBLIC bucket (the storefront <img> loads them directly).
_ALLOWED_IMG_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
_MAX_IMG_BYTES = 5 * 1024 * 1024


class ItemImageResponse(BaseModel):
    image_url: str


class ItemResponse(BaseModel):
    id: str
    item_type: str
    title: str
    body: str | None = None
    data: dict | None = None
    is_active: bool
    created_at: str


class ItemCreate(BaseModel):
    item_type: str = "general"
    title: str = Field(min_length=1, max_length=255)
    body: str | None = None
    data: dict | None = None
    is_active: bool = True

    @field_validator("item_type")
    @classmethod
    def _valid_type(cls, v: str) -> str:
        if v not in _ITEM_TYPES:
            raise ValueError(f"item_type must be one of {sorted(_ITEM_TYPES)}")
        return v


class ItemUpdate(BaseModel):
    item_type: str | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    body: str | None = None
    data: dict | None = None
    is_active: bool | None = None

    @field_validator("item_type")
    @classmethod
    def _valid_type(cls, v: str | None) -> str | None:
        if v is not None and v not in _ITEM_TYPES:
            raise ValueError(f"item_type must be one of {sorted(_ITEM_TYPES)}")
        return v


def _item_to_response(item: KnowledgeItem) -> ItemResponse:
    return ItemResponse(
        id=str(item.id),
        item_type=item.item_type.value if hasattr(item.item_type, "value") else str(item.item_type),
        title=item.title,
        body=item.body,
        data=item.data,
        is_active=item.is_active,
        created_at=item.created_at.isoformat() if item.created_at else "",
    )


async def _get_owned_item(
    business_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession
) -> KnowledgeItem:
    result = await db.execute(
        select(KnowledgeItem).where(
            KnowledgeItem.id == item_id,
            KnowledgeItem.business_id == business_id,
        )
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise NotFoundError("KnowledgeItem", str(item_id))
    return item


async def _bust_storefront(business_id: uuid.UUID, db: AsyncSession, redis) -> None:
    """Invalidate the public storefront cache after a catalog change."""
    from app.api.miniapp import bust_storefront_cache  # local import avoids any cycle
    await bust_storefront_cache(business_id, db, redis)


@router.post("/{business_id}/items", response_model=ItemResponse, status_code=201)
async def create_item(
    business_id: uuid.UUID,
    payload: ItemCreate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> ItemResponse:
    await _assert_owns_business(current_user.id, business_id, db)
    item = KnowledgeItem(
        business_id=business_id,
        item_type=KnowledgeItemType(payload.item_type),
        title=payload.title,
        body=payload.body,
        data=payload.data,
        is_active=payload.is_active,
    )
    db.add(item)
    await db.flush()
    await _bust_storefront(business_id, db, redis)
    logger.info("Knowledge item created", item_id=str(item.id),
                business_id=str(business_id), item_type=payload.item_type)
    return _item_to_response(item)


@router.get("/{business_id}/items", response_model=list[ItemResponse])
async def list_items(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
) -> list[ItemResponse]:
    await _assert_owns_business(current_user.id, business_id, db)
    result = await db.execute(
        select(KnowledgeItem)
        .where(KnowledgeItem.business_id == business_id)
        .order_by(KnowledgeItem.created_at.desc())
    )
    return [_item_to_response(i) for i in result.scalars().all()]


@router.post("/{business_id}/items/image", response_model=ItemImageResponse, status_code=201)
async def upload_item_image(
    business_id: uuid.UUID,
    current_user: CurrentUser,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> ItemImageResponse:
    """Upload a catalog image (e.g. a product photo) to the public bucket and
    return its URL. The owner stores that URL in the item's data.image_url; the
    storefront then renders it. Decoupled from item create so it works for both
    new and existing items."""
    await _assert_owns_business(current_user.id, business_id, db)

    filename = file.filename or "image"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in _ALLOWED_IMG_EXT:
        raise ValidationError(f"Unsupported image type '.{ext}'. Allowed: {sorted(_ALLOWED_IMG_EXT)}")

    data = await file.read()
    if not data:
        raise ValidationError("Empty file")
    if len(data) > _MAX_IMG_BYTES:
        raise ValidationError(f"Image exceeds {_MAX_IMG_BYTES // (1024*1024)} MB limit")

    path = f"items/{business_id}/{uuid.uuid4().hex}.{ext}"
    url = await storage_service.upload_public(data, path, content_type=file.content_type)
    logger.info("Catalog image uploaded", business_id=str(business_id), path=path, bytes=len(data))
    return ItemImageResponse(image_url=url)


@router.patch("/{business_id}/items/{item_id}", response_model=ItemResponse)
async def update_item(
    business_id: uuid.UUID,
    item_id: uuid.UUID,
    payload: ItemUpdate,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> ItemResponse:
    await _assert_owns_business(current_user.id, business_id, db)
    item = await _get_owned_item(business_id, item_id, db)

    fields = payload.model_dump(exclude_unset=True)
    if "item_type" in fields:
        item.item_type = KnowledgeItemType(fields.pop("item_type"))
    for key, value in fields.items():
        setattr(item, key, value)
    await db.flush()
    await _bust_storefront(business_id, db, redis)
    logger.info("Knowledge item updated", item_id=str(item.id), business_id=str(business_id))
    return _item_to_response(item)


@router.delete("/{business_id}/items/{item_id}", status_code=204)
async def delete_item(
    business_id: uuid.UUID,
    item_id: uuid.UUID,
    current_user: CurrentUser,
    db: AsyncSession = Depends(get_db),
    redis=Depends(get_redis),
) -> None:
    await _assert_owns_business(current_user.id, business_id, db)
    item = await _get_owned_item(business_id, item_id, db)
    await db.delete(item)
    await _bust_storefront(business_id, db, redis)
