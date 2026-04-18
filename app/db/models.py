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


# ---------------------------------------------------------------------------
# System 2 — Businesses & Bots
# ---------------------------------------------------------------------------

class Business(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "businesses"

    owner_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    category: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    logo_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    brand_primary_color: Mapped[str] = mapped_column(String(7), default="#1a73e8", nullable=False)
    brand_secondary_color: Mapped[str] = mapped_column(String(7), default="#ffffff", nullable=False)
    country_code: Mapped[str] = mapped_column(String(2), default="ET", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Africa/Addis_Ababa", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="ETB", nullable=False)
    languages: Mapped[list] = mapped_column(JSONB, default=["am", "en"], nullable=False)
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    address: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    website_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    mcp_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suspended_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    owner: Mapped["User"] = relationship("User", back_populates="businesses")
    bots: Mapped[list["Bot"]] = relationship("Bot", back_populates="business")
    brain_config: Mapped[Optional["BusinessBrainConfig"]] = relationship("BusinessBrainConfig", back_populates="business", uselist=False)
    knowledge_documents: Mapped[list["KnowledgeDocument"]] = relationship("KnowledgeDocument", back_populates="business")
    knowledge_items: Mapped[list["KnowledgeItem"]] = relationship("KnowledgeItem", back_populates="business")
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="business")
    child_agents: Mapped[list["ChildAgent"]] = relationship("ChildAgent", back_populates="business")
    wallet: Mapped[Optional["TokenWallet"]] = relationship("TokenWallet", back_populates="business", uselist=False)
    payment_integrations: Mapped[list["PaymentIntegration"]] = relationship("PaymentIntegration", back_populates="business")
    orders: Mapped[list["Order"]] = relationship("Order", back_populates="business")
    mini_app_config: Mapped[Optional["MiniAppConfig"]] = relationship("MiniAppConfig", back_populates="business", uselist=False)
    landing_page: Mapped[Optional["LandingPage"]] = relationship("LandingPage", back_populates="business", uselist=False)
    mcp_listing: Mapped[Optional["McpListing"]] = relationship("McpListing", back_populates="business", uselist=False)


class Bot(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "bots"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    bot_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    bot_display_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    encrypted_token: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    webhook_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    webhook_secret: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform), default=Platform.telegram, nullable=False)
    status: Mapped[BotStatus] = mapped_column(Enum(BotStatus), default=BotStatus.active, nullable=False)
    grace_period_started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    suspended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    suspension_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_messages_processed: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_etg_consumed: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    business: Mapped["Business"] = relationship("Business", back_populates="bots")
    conversations: Mapped[list["Conversation"]] = relationship("Conversation", back_populates="bot")


# ---------------------------------------------------------------------------
# System 3 — Business Brain (RAG)
# ---------------------------------------------------------------------------

class BusinessBrainConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "business_brain_configs"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, unique=True)
    persona_name: Mapped[str] = mapped_column(String(128), default="Assistant", nullable=False)
    persona_tone: Mapped[str] = mapped_column(String(64), default="friendly", nullable=False)
    system_prompt_extra: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    rag_top_k: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    rag_similarity_threshold: Mapped[float] = mapped_column(Float, default=0.75, nullable=False)
    max_history_messages: Mapped[int] = mapped_column(Integer, default=20, nullable=False)
    fallback_message: Mapped[str] = mapped_column(Text, default="I don't have information about that. Please contact us directly.", nullable=False)
    handoff_message: Mapped[str] = mapped_column(Text, default="Let me connect you with a human agent.", nullable=False)
    out_of_hours_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    collect_customer_name: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    collect_customer_phone: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    business: Mapped["Business"] = relationship("Business", back_populates="brain_config")


class KnowledgeDocument(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "knowledge_documents"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(16), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    gcs_path: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(Enum(DocumentStatus), default=DocumentStatus.pending, nullable=False)
    extracted_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)

    business: Mapped["Business"] = relationship("Business", back_populates="knowledge_documents")
    chunks: Mapped[list["KnowledgeChunk"]] = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "knowledge_chunks"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=True, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(64), default="text-embedding-004", nullable=False)
    # embedding column (Vector(768)) is added via raw SQL migration — pgvector type not mapped here
    metadata_: Mapped[Optional[dict]] = mapped_column("metadata", JSONB, nullable=True)

    document: Mapped[Optional["KnowledgeDocument"]] = relationship("KnowledgeDocument", back_populates="chunks")

    __table_args__ = (
        Index("idx_knowledge_chunks_business", "business_id"),
    )


class KnowledgeItem(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "knowledge_items"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    item_type: Mapped[KnowledgeItemType] = mapped_column(Enum(KnowledgeItemType), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source_document_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_documents.id"), nullable=True)

    business: Mapped["Business"] = relationship("Business", back_populates="knowledge_items")


class Conversation(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "conversations"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    bot_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("bots.id", ondelete="CASCADE"), nullable=False, index=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform), default=Platform.telegram, nullable=False)
    customer_platform_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_username: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    customer_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    detected_language: Mapped[str] = mapped_column(String(8), default="en", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_message_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_messages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_etg_spent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    child_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("child_agents.id"), nullable=True)
    context_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    business: Mapped["Business"] = relationship("Business", back_populates="conversations")
    bot: Mapped["Bot"] = relationship("Bot", back_populates="conversations")
    messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("bot_id", "customer_platform_id", name="uq_conversation_bot_customer"),
    )


class ChatMessage(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "chat_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[MessageRole] = mapped_column(Enum(MessageRole), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    model_used: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    etg_charged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    chunks_retrieved: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    media_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    media_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
