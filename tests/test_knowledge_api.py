"""Tests for the knowledge document upload/list/delete API.

GCS is mocked (no real bucket); we assert the endpoint stores the file path and
creates a KnowledgeDocument in status=pending for the embedding worker to pick up.
"""
import uuid

import pytest
from sqlalchemy import select

import app.api.knowledge as knowledge_api
from app.db.models import Business, DocumentStatus, KnowledgeDocument, User


async def _seed_owner_and_business(db, user_id, business_id):
    db.add(User(id=user_id))  # all User fields default; is_active defaults True
    db.add(Business(id=business_id, owner_id=user_id, name="Test Biz", slug=f"b-{business_id.hex[:8]}"))
    await db.flush()


@pytest.fixture
def _no_gcs(monkeypatch):
    """Stub the GCS upload so tests don't hit a real bucket."""
    async def _fake_upload(data, path, content_type=None, bucket_name=None):
        return f"gs://fake/{path}"
    monkeypatch.setattr(knowledge_api.storage_service, "upload_file", _fake_upload)


@pytest.mark.asyncio
async def test_upload_creates_pending_document(client, db, _no_gcs, sample_user_id,
                                               sample_business_id, valid_access_token):
    await _seed_owner_and_business(db, sample_user_id, sample_business_id)

    resp = await client.post(
        f"/api/v1/knowledge/{sample_business_id}/documents",
        files={"file": ("menu.txt", b"We sell injera and coffee. Hours 9-5.", "text/plain")},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["filename"] == "menu.txt"
    assert body["file_type"] == "txt"
    assert body["status"] == "pending"        # the worker will process it
    assert body["chunk_count"] == 0

    # a KnowledgeDocument row exists, pending, with the object PATH (not gs:// URI)
    row = (await db.execute(select(KnowledgeDocument))).scalars().one()
    assert row.status == DocumentStatus.pending
    assert row.gcs_path == f"documents/{sample_business_id}/menu.txt"
    assert row.uploaded_by_id == sample_user_id


@pytest.mark.asyncio
async def test_list_documents(client, db, _no_gcs, sample_user_id,
                              sample_business_id, valid_access_token):
    await _seed_owner_and_business(db, sample_user_id, sample_business_id)
    hdr = {"Authorization": f"Bearer {valid_access_token}"}
    await client.post(f"/api/v1/knowledge/{sample_business_id}/documents",
                      files={"file": ("a.txt", b"hello", "text/plain")}, headers=hdr)

    resp = await client.get(f"/api/v1/knowledge/{sample_business_id}/documents", headers=hdr)
    assert resp.status_code == 200
    docs = resp.json()
    assert len(docs) == 1 and docs[0]["filename"] == "a.txt"


@pytest.mark.asyncio
async def test_unsupported_file_type_rejected(client, db, _no_gcs, sample_user_id,
                                              sample_business_id, valid_access_token):
    await _seed_owner_and_business(db, sample_user_id, sample_business_id)
    resp = await client.post(
        f"/api/v1/knowledge/{sample_business_id}/documents",
        files={"file": ("evil.exe", b"MZ", "application/octet-stream")},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code in (400, 422)
    # nothing persisted
    assert (await db.execute(select(KnowledgeDocument))).first() is None


@pytest.mark.asyncio
async def test_cannot_upload_to_unowned_business(client, db, _no_gcs, sample_user_id,
                                                 valid_access_token):
    # user exists, but the business belongs to someone else
    db.add(User(id=sample_user_id))
    other_biz = uuid.uuid4()
    db.add(Business(id=other_biz, owner_id=uuid.uuid4(), name="Theirs", slug="theirs"))
    await db.flush()

    resp = await client.post(
        f"/api/v1/knowledge/{other_biz}/documents",
        files={"file": ("a.txt", b"hi", "text/plain")},
        headers={"Authorization": f"Bearer {valid_access_token}"},
    )
    assert resp.status_code == 404   # ownership check -> not found (no info leak)
