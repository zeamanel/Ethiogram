# app/core/config.py
from functools import lru_cache
from typing import Literal, Optional
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ENVIRONMENT
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False
    app_name: str = "Ethiogram"
    app_version: str = "1.0.0"
    api_prefix: str = "/api/v1"
    allowed_hosts: list[str] = ["*"]
    cors_origins: list[str] = ["*"]

    # DATABASE
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/ethiogram"
    database_pool_size: int = 20
    database_max_overflow: int = 40
    database_pool_timeout: int = 30
    database_echo: bool = False

    # REDIS
    redis_url: str = "redis://localhost:6379/0"
    # Dev-only: use an in-memory fakeredis instead of a real Redis server.
    use_fake_redis: bool = False
    redis_cache_ttl: int = 300

    # SECURITY
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30
    encryption_key: str = "change-me-in-production"
    webhook_secret_salt: str = "change-me-in-production"

    # GOOGLE CLOUD
    gcp_project_id: str = "ethiogram"
    gcp_region: str = "us-central1"
    gcp_service_account_key: Optional[str] = None
    gcs_bucket_name: str = "ethiogram-uploads"
    gcs_bucket_public: str = "ethiogram-public"
    secret_manager_prefix: str = "ethiogram"

    # MASTER BOT
    master_bot_token: str = ""
    master_bot_username: str = "ethiogramchat_bot"
    master_bot_webhook_url: str = ""

    # Telegram HTTP client timeouts (seconds). Raise connect for local dev
    # over a slow VPN: TELEGRAM_CONNECT_TIMEOUT / TELEGRAM_READ_TIMEOUT.
    telegram_connect_timeout: float = 5.0
    telegram_read_timeout: float = 10.0

    # AI MODEL DEFAULTS
    default_model_id: str = "gemini-2.0-flash-001"
    fallback_model_id: str = "gpt-4o-mini"
    emergency_model_id: str = "llama-3.1-8b-instruct"
    embedding_model_id: str = "text-embedding-004"
    # Amharic-speaker routing: Gemini handles Ge'ez/Amharic best. Flash is the
    # primary (fast + cheap, great Amharic); Pro is the fallback for when Flash
    # is unavailable — routing every Amharic reply to Pro is ~10x the cost and
    # drains wallets fast, so keep Flash first.
    amharic_primary_model_id: str = "google/gemini-2.5-flash"
    amharic_secondary_model_id: str = "google/gemini-2.5-pro"

    # VERTEX AI
    vertex_ai_location: str = "us-central1"
    vertex_ai_project: Optional[str] = None

    # OPENAI
    openai_api_key: Optional[str] = None
    openai_org_id: Optional[str] = None
    # Override the API base URL to use an OpenAI-compatible provider
    # (e.g. OpenRouter: https://openrouter.ai/api/v1). Reads OPENAI_BASE_URL.
    openai_base_url: Optional[str] = None

    # ANTHROPIC
    anthropic_api_key: Optional[str] = None

    # MISTRAL
    mistral_api_key: Optional[str] = None

    # ETG TOKEN ECONOMY
    etg_usd_rate: float = 0.01
    etg_etb_rate: float = 0.56
    etg_new_user_bonus: int = 100
    etg_referral_bonus: int = 200

    # PAYMENT PROVIDERS
    # Accepts CHAPA_SECRET_KEY *or* CHAPA_PAYMENT_TOKEN (old env-var name still
    # in some Cloud Run deployments). Either name in the environment works.
    chapa_secret_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("CHAPA_SECRET_KEY", "CHAPA_PAYMENT_TOKEN"),
    )
    chapa_public_key: Optional[str] = None
    chapa_internal_key: str = ""   # shared secret for service-to-service calls (Odaflux)
    chapa_base_url: str = "https://api.chapa.co/v1"
    chapa_webhook_secret: Optional[str] = None
    lulit_internal_url: str = ""
    stripe_secret_key: Optional[str] = None
    stripe_publishable_key: Optional[str] = None
    stripe_webhook_secret: Optional[str] = None
    paypal_client_id: Optional[str] = None
    paypal_client_secret: Optional[str] = None
    paypal_base_url: str = "https://api-m.paypal.com"
    nowpayments_api_key: Optional[str] = None
    nowpayments_ipn_secret: Optional[str] = None
    lemonsqueezy_api_key: Optional[str] = None
    lemonsqueezy_webhook_secret: Optional[str] = None

    # GOOGLE CALENDAR
    google_calendar_credentials: Optional[str] = None
    google_calendar_scopes: list[str] = ["https://www.googleapis.com/auth/calendar"]

    # FIREBASE (Live Commerce)
    firebase_project_id: Optional[str] = None
    firebase_service_account_key: Optional[str] = None
    firebase_database_url: Optional[str] = None

    # PLATFORM LIMITS
    max_file_upload_mb: int = 50
    max_knowledge_chunks_per_business: int = 10_000
    max_bots_per_business: int = 5
    max_child_agents_per_bot: int = 10
    trial_duration_days: int = 15
    escrow_release_days: int = 7
    grace_period_hours: int = 24
    rag_default_top_k: int = 5
    # text-embedding-004 cosine similarity for genuinely relevant chunks sits
    # around 0.4-0.65, so 0.75 filtered everything out. 0.3 retrieves real
    # matches while the LLM ignores anything off-topic.
    rag_default_similarity_threshold: float = 0.3
    max_conversation_history: int = 20

    # RATE LIMITING
    rate_limit_webhook_per_second: int = 100
    rate_limit_api_per_minute: int = 60
    rate_limit_auth_per_minute: int = 10

    # ADMIN
    # Stored as a raw string (read from ADMIN_TELEGRAM_IDS) so pydantic-settings
    # never JSON-decodes it — a bare int like "959519454" can't crash startup.
    # The parsed list[int] is exposed via the admin_telegram_ids property below,
    # which tolerates "[959519454]", "959519454", "959519454,123", or "".
    admin_telegram_ids_raw: str = Field(default="", validation_alias="ADMIN_TELEGRAM_IDS")
    admin_email: str = "admin@ethiogram.com"
    # Name of the HTTP header that carries the admin shared secret.
    admin_secret_header: str = "X-Ethiogram-Admin"
    # The actual shared-secret VALUE expected in that header. Unset by default
    # so admin endpoints stay locked until ADMIN_SECRET_VALUE is configured.
    admin_secret_value: Optional[str] = None

    # PLATFORM URLS
    base_url: str = "https://api.ethiogram.com"
    dashboard_url: str = "https://ethiogram.com/dashboard"
    mini_app_url: str = "https://ethiogram.com/app"
    biz_page_url: str = "https://ethiogram.com/biz"

    # CUSTOM DOMAINS — owners point their .com here. The CNAME target is the Cloud
    # Run domain-mapping endpoint (Google provisions the TLS cert); the gcloud
    # command below is the per-domain edge step the platform operator runs.
    custom_domain_target: str = "ghs.googlehosted.com"
    cloud_run_service: str = "ethiogram-api"
    cloud_run_region: str = "us-central1"
    wide_overlay_url: str = "https://wide.ethiogram.com"
    studio_url: str = "https://studio.ethiogram.com"
    admin_url: str = "https://admin.ethiogram.com"
    docs_url: str = "https://docs.ethiogram.com"

    # LOGGING
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    sentry_dsn: Optional[str] = None

    @property
    def admin_telegram_ids(self) -> list[int]:
        """Parsed platform-admin Telegram IDs. Accepts a JSON list, a bare int,
        or a comma-separated string (brackets/whitespace tolerated)."""
        inner = (self.admin_telegram_ids_raw or "").strip().lstrip("[").rstrip("]")
        return [int(p.strip()) for p in inner.split(",") if p.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def webhook_base_url(self) -> str:
        return f"{self.base_url}/webhook"

    @property
    def vertex_project(self) -> str:
        return self.vertex_ai_project or self.gcp_project_id


@lru_cache()
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
