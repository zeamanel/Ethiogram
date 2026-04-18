# app/services/storage_service.py
import asyncio
import mimetypes
import os
from datetime import timedelta
from typing import Optional

from app.core.config import settings
from app.core.exceptions import ExternalServiceError
from app.core.logging import get_logger

logger = get_logger(__name__)


class StorageService:
    """
    Async wrapper around Google Cloud Storage.
    All blocking GCS SDK calls are dispatched to a thread executor.
    """

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                from google.cloud import storage
                self._client = storage.Client(project=settings.gcp_project_id)
            except Exception as exc:
                raise ExternalServiceError("GCS", str(exc))
        return self._client

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    async def upload_file(
        self,
        file_bytes: bytes,
        destination_path: str,
        bucket_name: Optional[str] = None,
        content_type: Optional[str] = None,
    ) -> str:
        """
        Upload raw bytes to GCS.
        Returns the gs:// URI of the uploaded object.
        """
        bucket = bucket_name or settings.gcs_bucket_name
        if content_type is None:
            content_type, _ = mimetypes.guess_type(destination_path)
            content_type = content_type or "application/octet-stream"

        await asyncio.get_event_loop().run_in_executor(
            None,
            self._upload_sync,
            file_bytes,
            destination_path,
            bucket,
            content_type,
        )
        uri = f"gs://{bucket}/{destination_path}"
        logger.info("File uploaded", path=destination_path, bucket=bucket, size=len(file_bytes))
        return uri

    def _upload_sync(
        self, file_bytes: bytes, path: str, bucket_name: str, content_type: str
    ) -> None:
        client = self._get_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(path)
        blob.upload_from_string(file_bytes, content_type=content_type)

    async def upload_public(
        self,
        file_bytes: bytes,
        destination_path: str,
        content_type: Optional[str] = None,
    ) -> str:
        """Upload to the public bucket. Returns a public HTTPS URL."""
        await asyncio.get_event_loop().run_in_executor(
            None,
            self._upload_public_sync,
            file_bytes,
            destination_path,
            content_type or "application/octet-stream",
        )
        return f"https://storage.googleapis.com/{settings.gcs_bucket_public}/{destination_path}"

    def _upload_public_sync(
        self, file_bytes: bytes, path: str, content_type: str
    ) -> None:
        client = self._get_client()
        bucket = client.bucket(settings.gcs_bucket_public)
        blob = bucket.blob(path)
        blob.upload_from_string(file_bytes, content_type=content_type)
        blob.make_public()

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def download_file(
        self,
        source_path: str,
        bucket_name: Optional[str] = None,
    ) -> bytes:
        bucket = bucket_name or settings.gcs_bucket_name
        return await asyncio.get_event_loop().run_in_executor(
            None, self._download_sync, source_path, bucket
        )

    def _download_sync(self, path: str, bucket_name: str) -> bytes:
        client = self._get_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(path)
        return blob.download_as_bytes()

    # ------------------------------------------------------------------
    # Signed URLs
    # ------------------------------------------------------------------

    async def get_signed_url(
        self,
        path: str,
        expiry_minutes: int = 60,
        bucket_name: Optional[str] = None,
    ) -> str:
        """Generate a time-limited signed URL for private objects."""
        bucket = bucket_name or settings.gcs_bucket_name
        return await asyncio.get_event_loop().run_in_executor(
            None, self._signed_url_sync, path, bucket, expiry_minutes
        )

    def _signed_url_sync(self, path: str, bucket_name: str, expiry_minutes: int) -> str:
        client = self._get_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(path)
        return blob.generate_signed_url(
            expiration=timedelta(minutes=expiry_minutes),
            method="GET",
        )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_file(
        self,
        path: str,
        bucket_name: Optional[str] = None,
    ) -> None:
        bucket = bucket_name or settings.gcs_bucket_name
        await asyncio.get_event_loop().run_in_executor(
            None, self._delete_sync, path, bucket
        )
        logger.info("File deleted", path=path, bucket=bucket)

    def _delete_sync(self, path: str, bucket_name: str) -> None:
        client = self._get_client()
        bucket = client.bucket(bucket_name)
        blob = bucket.blob(path)
        blob.delete()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def build_document_path(self, business_id: str, filename: str) -> str:
        """Deterministic GCS path for a knowledge document."""
        safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in filename)
        return f"documents/{business_id}/{safe_name}"

    def build_avatar_path(self, user_id: str, ext: str = "jpg") -> str:
        return f"avatars/{user_id}.{ext}"

    def build_logo_path(self, business_id: str, ext: str = "png") -> str:
        return f"logos/{business_id}.{ext}"


storage_service = StorageService()
