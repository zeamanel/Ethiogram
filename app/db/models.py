# app/db/models.py
import uuid
from datetime import datetime
from typing import Optional
from enum import Enum as PyEnum

from sqlalchemy import (
    String, Boolean, Integer, BigInteger, Float, Text, DateTime,
    ForeignKey, UniqueConstraint, Index, Enum, JSON, func,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, UUIDMixin, TimestampMixin, SoftDeleteMixin


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserRole(str, PyEnum):
    owner = "owner"
    creator = "creator"
    admin = "admin"
    support = "support"

class BotStatus(str, PyEnum):
    active = "active"
    paused = "paused"
    grace = "grace"
    disconnected = "disconnected"
    suspended = "suspended"

class AgentStatus(str, PyEnum):
    draft = "draft"
    pending_review = "pending_review"
    live = "live"
    suspended = "suspended"
    rejected = "rejected"

class DocumentStatus(str, PyEnum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"

class OrderStatus(str, PyEnum):
    pending = "pending"
    paid = "paid"
    fulfilled = "fulfilled"
    cancelled = "cancelled"
    refunded = "refunded"

class PaymentStatus(str, PyEnum):
    pending = "pending"
    completed = "completed"
    failed = "failed"
    refunded = "refunded"

class SubscriptionPlan(str, PyEnum):
    free = "free"
    starter = "starter"
    growth = "growth"
    business = "business"
    enterprise = "enterprise"

class EscrowStatus(str, PyEnum):
    holding = "holding"
    released = "released"
    disputed = "disputed"
    refunded = "refunded"

class TicketStatus(str, PyEnum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    closed = "closed"

class MessageRole(str, PyEnum):
    user = "user"
    assistant = "assistant"
    system = "system"

class Platform(str, PyEnum):
    telegram = "telegram"
    whatsapp = "whatsapp"

class ModelProvider(str, PyEnum):
    google = "google"
    openai = "openai"
    anthropic = "anthropic"
    mistral = "mistral"
    meta = "meta"

class ModelTier(str, PyEnum):
    standard = "standard"
    premium = "premium"
    economy = "economy"

class KnowledgeItemType(str, PyEnum):
    faq = "faq"
    product = "product"
    service = "service"
    menu_item = "menu_item"
    policy = "policy"
    general = "general"

class AlertType(str, PyEnum):
    low = "low"
    critical = "critical"
    zero = "zero"
    paused = "paused"

class PaymentProvider(str, PyEnum):
    chapa = "chapa"
    stripe = "stripe"
    paypal = "paypal"
    crypto = "crypto"
    lemonsqueezy = "lemonsqueezy"
    telebirr = "telebirr"

class LivePlatform(str, PyEnum):
    tiktok = "tiktok"
    instagram = "instagram"
    youtube = "youtube"


# ---------------------------------------------------------------------------
# System 1 — Users & Auth
# ---------------------------------------------------------------------------

class User(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "users"

    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, nullable=True, index=True)
    username: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), unique=True, nullable=True, index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), unique=True, nullable=True)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.owner, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    language_code: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    referral_code: Mapped[Optional[str]] = mapped_column(String(16), unique=True, nullable=True, index=True)
    referred_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    suspended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    sessions: Mapped[list["UserSession"]] = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    creator_profile: Mapped[Optional["CreatorProfile"]] = relationship("CreatorProfile", back_populates="user", uselist=False)
    businesses: Mapped[list["Business"]] = relationship("Business", back_populates="owner")


class UserSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "user_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    access_token_jti: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    refresh_token: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)
    refresh_token_jti: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    platform: Mapped[str] = mapped_column(String(32), default="web", nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="sessions")

    @property
    def is_valid(self) -> bool:
        from datetime import timezone
        return self.revoked_at is None and self.expires_at > datetime.now(timezone.utc)


class CreatorProfile(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "creator_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    github_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_trusted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    total_agents_published: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_revenue_etg: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_unlocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payout_method: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payout_details_encrypted: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="creator_profile")
    agents: Mapped[list["Agent"]] = relationship("Agent", back_populates="creator")
