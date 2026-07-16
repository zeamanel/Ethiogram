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
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Global end-user wallet (reserved for the end-user recharge phase; per-business
    # billing currently uses Conversation.etg_balance).
    etg_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
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

    # Billing policy — who pays per message, free-tier cap, and user-pays pricing.
    billing_policy: Mapped[str] = mapped_column(String(16), default="business_pays", nullable=False)
    per_user_monthly_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    per_user_limit_action: Mapped[str] = mapped_column(String(16), default="block", nullable=False)
    service_price: Mapped[int] = mapped_column(Integer, default=0, nullable=False)   # ETG charged to the end-user
    business_markup: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # ETG profit credited to the business

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
    bookings: Mapped[list["Booking"]] = relationship("Booking", back_populates="business")
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
    rag_similarity_threshold: Mapped[float] = mapped_column(Float, default=0.3, nullable=False)
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
    # Per-business end-user billing ledger (user_pays / free-tier cap).
    etg_balance: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    monthly_etg_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    monthly_reset_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
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


# ---------------------------------------------------------------------------
# System 4 — AI Models Registry
# ---------------------------------------------------------------------------

class AiModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "ai_models"

    model_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[ModelProvider] = mapped_column(Enum(ModelProvider), nullable=False)
    tier: Mapped[ModelTier] = mapped_column(Enum(ModelTier), default=ModelTier.standard, nullable=False)
    etg_cost_per_1k_input: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    etg_cost_per_1k_output: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    context_window: Mapped[int] = mapped_column(Integer, default=128_000, nullable=False)
    supports_vision: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    supports_function_calling: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_emergency: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class UsagePricing(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usage_pricing"

    action_type: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    etg_cost: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


# ---------------------------------------------------------------------------
# System 5 — Agent Marketplace
# ---------------------------------------------------------------------------

class Agent(Base, UUIDMixin, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "agents"

    creator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("creator_profiles.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    tagline: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    tags: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    capabilities: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    encrypted_system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_ref: Mapped[str] = mapped_column(String(128), default="shared-key-v1", nullable=False)
    child_schema: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    setup_guide: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_etg: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    preferred_model_id: Mapped[Optional[str]] = mapped_column(String(128), ForeignKey("ai_models.model_id"), nullable=True)
    status: Mapped[AgentStatus] = mapped_column(Enum(AgentStatus), default=AgentStatus.draft, nullable=False)
    is_featured: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_staff_pick: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cover_image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    demo_video_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    total_unlocks: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_active_trials: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    average_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    review_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    reviewed_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    creator: Mapped["CreatorProfile"] = relationship("CreatorProfile", back_populates="agents")
    child_agents: Mapped[list["ChildAgent"]] = relationship("ChildAgent", back_populates="agent")
    trials: Mapped[list["AgentTrial"]] = relationship("AgentTrial", back_populates="agent")
    unlocks: Mapped[list["AgentUnlock"]] = relationship("AgentUnlock", back_populates="agent")
    reviews: Mapped[list["AgentReview"]] = relationship("AgentReview", back_populates="agent")
    reports: Mapped[list["AgentReport"]] = relationship("AgentReport", back_populates="agent")


class ChildAgent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "child_agents"

    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    display_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Non-sensitive, owner-filled config (products, hours, rules, tone). Rendered
    # into the system prompt.
    child_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    # Sensitive config (calendar/API credentials). Fernet-encrypted JSON blob —
    # NEVER stored plaintext and NEVER rendered into the prompt; decrypted only
    # in code and exposed to the agent under the "_secrets" key.
    child_secrets: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_to_bot_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("bots.id"), nullable=True)

    agent: Mapped["Agent"] = relationship("Agent", back_populates="child_agents")
    business: Mapped["Business"] = relationship("Business", back_populates="child_agents")

    __table_args__ = (
        UniqueConstraint("agent_id", "business_id", name="uq_child_agent_business"),
    )


class AgentTrial(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_trials"

    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    child_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("child_agents.id"), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_converted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    converted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    warning_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    critical_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    expired_notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    agent: Mapped["Agent"] = relationship("Agent", back_populates="trials")

    __table_args__ = (
        UniqueConstraint("agent_id", "business_id", name="uq_trial_agent_business"),
    )


class AgentUnlock(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_unlocks"

    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    child_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("child_agents.id"), nullable=True)
    etg_paid: Mapped[int] = mapped_column(Integer, nullable=False)
    escrow_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("token_escrow.id"), nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_refunded: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    refunded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    refund_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    agent: Mapped["Agent"] = relationship("Agent", back_populates="unlocks")


class AgentReview(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_reviews"

    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    unlock_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agent_unlocks.id"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    review_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_visible: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    agent: Mapped["Agent"] = relationship("Agent", back_populates="reviews")

    __table_args__ = (
        UniqueConstraint("agent_id", "business_id", name="uq_review_agent_business"),
    )


class AgentReport(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_reports"

    agent_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True)
    reported_by_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    resolved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    agent: Mapped["Agent"] = relationship("Agent", back_populates="reports")


# ---------------------------------------------------------------------------
# System 6 — ETG Token Economy
# ---------------------------------------------------------------------------

class TokenWallet(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "token_wallets"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, unique=True)
    balance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    escrow_balance: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    lifetime_recharged: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    lifetime_spent: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    auto_recharge_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    auto_recharge_threshold: Mapped[int] = mapped_column(Integer, default=500, nullable=False)
    auto_recharge_amount: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)
    auto_recharge_provider: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    monthly_spend_limit: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    current_month_spend: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spend_limit_reset_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    subscription_plan: Mapped[SubscriptionPlan] = mapped_column(Enum(SubscriptionPlan), default=SubscriptionPlan.free, nullable=False)

    business: Mapped["Business"] = relationship("Business", back_populates="wallet")
    transactions: Mapped[list["EtgTransaction"]] = relationship("EtgTransaction", back_populates="wallet")
    alerts: Mapped[list["WalletAlert"]] = relationship("WalletAlert", back_populates="wallet")


class EtgTransaction(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "etg_transactions"

    wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("token_wallets.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    balance_before: Mapped[int] = mapped_column(BigInteger, nullable=False)
    balance_after: Mapped[int] = mapped_column(BigInteger, nullable=False)
    transaction_type: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    reference_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reference_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    admin_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    wallet: Mapped["TokenWallet"] = relationship("TokenWallet", back_populates="transactions")


class RechargeOrder(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "recharge_orders"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    etg_package_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("etg_packages.id"), nullable=True)
    etg_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    fiat_amount: Mapped[float] = mapped_column(Float, nullable=False)
    fiat_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    payment_provider: Mapped[PaymentProvider] = mapped_column(Enum(PaymentProvider), nullable=False)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.pending, nullable=False)
    webhook_payload: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    bonus_etg: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TokenEscrow(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "token_escrow"

    wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("token_wallets.id", ondelete="CASCADE"), nullable=False, index=True)
    amount: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    release_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[EscrowStatus] = mapped_column(Enum(EscrowStatus), default=EscrowStatus.holding, nullable=False)
    released_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    dispute_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dispute_opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


class UsageEvent(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usage_events"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    bot_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("bots.id"), nullable=True)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    model_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    model_tier: Mapped[Optional[ModelTier]] = mapped_column(Enum(ModelTier), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    etg_charged: Mapped[int] = mapped_column(Integer, nullable=False)
    payer: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)   # business | user
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    __table_args__ = (
        Index("idx_usage_events_business_created", "business_id", "created_at"),
    )


class UsageDailyAggregate(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "usage_daily_aggregates"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    total_events: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_etg: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    __table_args__ = (
        UniqueConstraint("business_id", "date", "action_type", name="uq_daily_agg"),
    )


class WalletAlert(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "wallet_alerts"

    wallet_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("token_wallets.id", ondelete="CASCADE"), nullable=False, index=True)
    alert_type: Mapped[AlertType] = mapped_column(Enum(AlertType), nullable=False)
    balance_at_alert: Mapped[int] = mapped_column(Integer, nullable=False)
    sent_via: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    acknowledged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    wallet: Mapped["TokenWallet"] = relationship("TokenWallet", back_populates="alerts")


class Subscription(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "subscriptions"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    plan: Mapped[SubscriptionPlan] = mapped_column(Enum(SubscriptionPlan), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    payment_provider: Mapped[Optional[PaymentProvider]] = mapped_column(Enum(PaymentProvider), nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class EtgPackage(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "etg_packages"

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    etg_amount: Mapped[int] = mapped_column(Integer, nullable=False)
    bonus_etg: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    price_usd: Mapped[float] = mapped_column(Float, nullable=False)
    price_etb: Mapped[float] = mapped_column(Float, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


# ---------------------------------------------------------------------------
# System 7 — Commerce
# ---------------------------------------------------------------------------

class PaymentIntegration(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "payment_integrations"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    provider: Mapped[PaymentProvider] = mapped_column(Enum(PaymentProvider), nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_secret_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    encrypted_webhook_secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    public_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    extra_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    business: Mapped["Business"] = relationship("Business", back_populates="payment_integrations")

    __table_args__ = (
        UniqueConstraint("business_id", "provider", name="uq_payment_integration_business_provider"),
    )


class Order(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "orders"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    order_number: Mapped[str] = mapped_column(String(16), unique=True, nullable=False, index=True)
    customer_platform_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    items: Mapped[list] = mapped_column(JSONB, nullable=False)
    subtotal: Mapped[float] = mapped_column(Float, nullable=False)
    platform_fee: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    total: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="ETB", nullable=False)
    payment_provider: Mapped[Optional[PaymentProvider]] = mapped_column(Enum(PaymentProvider), nullable=True)
    payment_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    payment_status: Mapped[PaymentStatus] = mapped_column(Enum(PaymentStatus), default=PaymentStatus.pending, nullable=False)
    order_status: Mapped[OrderStatus] = mapped_column(Enum(OrderStatus), default=OrderStatus.pending, nullable=False)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    fulfilled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delivery_address: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True)

    business: Mapped["Business"] = relationship("Business", back_populates="orders")


# Booking lifecycle (stored as a plain string like billing_policy — avoids a
# Postgres enum type and the SQLite enum quirks in tests).
BOOKING_STATUSES = ("confirmed", "cancelled", "completed", "no_show")


class Booking(Base, UUIDMixin, TimestampMixin):
    """A native appointment record. Owned by Ethiogram (not just a calendar
    event) so the owner dashboard, reminders, and reschedule/cancel can work
    without the business wiring Google Calendar. ``calendar_event_id`` is set
    only when the booking is also mirrored to an external calendar."""
    __tablename__ = "bookings"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True)
    customer_platform_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    customer_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    customer_phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    service_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="confirmed", nullable=False, index=True)
    price: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="telegram", nullable=False)
    calendar_event_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reminder_24h_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    reminder_1h_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    business: Mapped["Business"] = relationship("Business", back_populates="bookings")

    __table_args__ = (
        Index("idx_bookings_business_starts", "business_id", "starts_at"),
        Index("idx_bookings_status_starts", "status", "starts_at"),
    )


# How an owner names the recipient of a business transfer, and the lifecycle.
TRANSFER_KINDS = ("telegram_username", "telegram_id", "email")
TRANSFER_STATUSES = ("pending", "accepted", "declined", "cancelled", "expired")


class BusinessTransfer(Base, UUIDMixin, TimestampMixin):
    """A pending hand-off of a business to another person (by Telegram or email).
    Ownership only moves when the recipient accepts — so a mistaken target, or a
    recipient who hasn't signed up yet, never loses or strands the business."""
    __tablename__ = "business_transfers"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    from_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False)
    to_kind: Mapped[str] = mapped_column(String(16), nullable=False)        # telegram_username | telegram_id | email
    to_value: Mapped[str] = mapped_column(String(255), nullable=False, index=True)  # normalized identifier
    to_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False, index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)


# ---------------------------------------------------------------------------
# System 8 — Presence
# ---------------------------------------------------------------------------

class MiniAppConfig(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "mini_app_configs"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, unique=True)
    theme_primary: Mapped[str] = mapped_column(String(7), default="#1a73e8", nullable=False)
    theme_secondary: Mapped[str] = mapped_column(String(7), default="#ffffff", nullable=False)
    theme_accent: Mapped[str] = mapped_column(String(7), default="#fbbc04", nullable=False)
    font_family: Mapped[str] = mapped_column(String(64), default="Inter", nullable=False)
    font_heading: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    font_body: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    hero_image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    layout_config: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    ui_child_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    show_categories: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_search: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    show_cart: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    custom_sections: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    business: Mapped["Business"] = relationship("Business", back_populates="mini_app_config")


class LandingPage(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "landing_pages"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, unique=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    meta_description: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    hero_headline: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    hero_subheadline: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    sections: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    seo_keywords: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    og_image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_generated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_views: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    total_clicks: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    business: Mapped["Business"] = relationship("Business", back_populates="landing_page")


class CustomDomain(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "custom_domains"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    domain_type: Mapped[str] = mapped_column(String(16), default="subdomain", nullable=False)
    dns_verification_token: Mapped[str] = mapped_column(String(64), nullable=False)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ssl_issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ssl_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class McpListing(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "mcp_listings"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, unique=True)
    structured_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    search_keywords: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    ai_search_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_indexed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_fetches: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)

    business: Mapped["Business"] = relationship("Business", back_populates="mcp_listing")
    fetch_logs: Mapped[list["McpFetchLog"]] = relationship("McpFetchLog", back_populates="listing")


class McpFetchLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "mcp_fetch_logs"

    listing_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("mcp_listings.id", ondelete="CASCADE"), nullable=False, index=True)
    fetcher_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fetcher_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    endpoint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    listing: Mapped["McpListing"] = relationship("McpListing", back_populates="fetch_logs")


# ---------------------------------------------------------------------------
# System 9 — Live Commerce
# ---------------------------------------------------------------------------

class LiveSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "live_sessions"

    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), nullable=False, index=True)
    platform: Mapped[LivePlatform] = mapped_column(Enum(LivePlatform), nullable=False)
    firebase_room_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    overlay_url: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    peak_viewers: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_orders: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_revenue: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    live_products: Mapped[list["LiveProduct"]] = relationship("LiveProduct", back_populates="session")


class LiveProduct(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "live_products"

    session_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("live_sessions.id", ondelete="CASCADE"), nullable=False, index=True)
    knowledge_item_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("knowledge_items.id"), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    price: Mapped[float] = mapped_column(Float, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    promo_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    promo_discount_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    deactivated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    views_while_active: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    orders_while_active: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    session: Mapped["LiveSession"] = relationship("LiveSession", back_populates="live_products")


# ---------------------------------------------------------------------------
# System 10 — Support & Admin
# ---------------------------------------------------------------------------

class SupportTicket(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "support_tickets"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    business_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id"), nullable=True)
    related_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("agents.id"), nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(Enum(TicketStatus), default=TicketStatus.open, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    assigned_to_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    ai_suggested_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    messages: Mapped[list["TicketMessage"]] = relationship("TicketMessage", back_populates="ticket", cascade="all, delete-orphan")


class TicketMessage(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "ticket_messages"

    ticket_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    ticket: Mapped["SupportTicket"] = relationship("SupportTicket", back_populates="messages")


class Notification(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "notifications"

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    notification_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    sent_via: Mapped[list] = mapped_column(JSONB, default=[], nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    read_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Set once every intended delivery channel has been handled. The dispatch
    # worker selects on `dispatched_at IS NULL`, so this is the terminal flag
    # that stops a row being re-processed (distinct from is_read, the user's
    # dashboard read flag).
    dispatched_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)


class AdminAuditLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "admin_audit_log"

    admin_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    old_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)


class PlatformSetting(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "platform_settings"

    key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    value_type: Mapped[str] = mapped_column(String(16), default="string", nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    updated_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
