# app/db/base.py
import uuid
from datetime import datetime, timezone
from typing import Any
from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase):
    type_annotation_map = {datetime: DateTime(timezone=True)}

    def to_dict(self) -> dict[str, Any]:
        return {col.name: getattr(self, col.name) for col in self.__table__.columns}

class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True,
                                           default=uuid.uuid4, index=True)

class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                   server_default=func.now(),
                                                   onupdate=lambda: datetime.now(timezone.utc),
                                                   nullable=False)

class SoftDeleteMixin:
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True),
                                                          nullable=True, default=None)
    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None
