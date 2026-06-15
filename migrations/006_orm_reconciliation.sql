-- 006_orm_reconciliation.sql
-- Idempotent: creates every table/enum the ORM (app/db/models.py) expects.
-- Safe to re-run. Does NOT alter or drop existing tables.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Enum types ──────────────────────────────────────────────────────────────
DO $$ BEGIN
  CREATE TYPE modelprovider AS ENUM ('google', 'openai', 'anthropic', 'mistral', 'meta');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE modeltier AS ENUM ('standard', 'premium', 'economy');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE userrole AS ENUM ('owner', 'creator', 'admin', 'support');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE agentstatus AS ENUM ('draft', 'pending_review', 'live', 'suspended', 'rejected');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE platform AS ENUM ('telegram', 'whatsapp');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE botstatus AS ENUM ('active', 'paused', 'grace', 'disconnected', 'suspended');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE documentstatus AS ENUM ('pending', 'processing', 'completed', 'failed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE liveplatform AS ENUM ('tiktok', 'instagram', 'youtube');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE paymentprovider AS ENUM ('chapa', 'stripe', 'paypal', 'crypto', 'lemonsqueezy', 'telebirr');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE paymentstatus AS ENUM ('pending', 'completed', 'failed', 'refunded');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE subscriptionplan AS ENUM ('free', 'starter', 'growth', 'business', 'enterprise');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE knowledgeitemtype AS ENUM ('faq', 'product', 'service', 'menu_item', 'policy', 'general');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE ticketstatus AS ENUM ('open', 'in_progress', 'resolved', 'closed');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE escrowstatus AS ENUM ('holding', 'released', 'disputed', 'refunded');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE alerttype AS ENUM ('low', 'critical', 'zero', 'paused');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE messagerole AS ENUM ('user', 'assistant', 'system');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
DO $$ BEGIN
  CREATE TYPE orderstatus AS ENUM ('pending', 'paid', 'fulfilled', 'cancelled', 'refunded');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── Tables ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ai_models (
	model_id VARCHAR(128) NOT NULL, 
	display_name VARCHAR(128) NOT NULL, 
	provider modelprovider NOT NULL, 
	tier modeltier NOT NULL, 
	etg_cost_per_1k_input INTEGER NOT NULL, 
	etg_cost_per_1k_output INTEGER NOT NULL, 
	context_window INTEGER NOT NULL, 
	supports_vision BOOLEAN NOT NULL, 
	supports_function_calling BOOLEAN NOT NULL, 
	is_enabled BOOLEAN NOT NULL, 
	is_default BOOLEAN NOT NULL, 
	is_fallback BOOLEAN NOT NULL, 
	is_emergency BOOLEAN NOT NULL, 
	notes TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_ai_models_id ON ai_models (id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_ai_models_model_id ON ai_models (model_id);

CREATE TABLE IF NOT EXISTS etg_packages (
	name VARCHAR(64) NOT NULL, 
	etg_amount INTEGER NOT NULL, 
	bonus_etg INTEGER NOT NULL, 
	price_usd FLOAT NOT NULL, 
	price_etb FLOAT NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	display_order INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_etg_packages_id ON etg_packages (id);

CREATE TABLE IF NOT EXISTS usage_pricing (
	action_type VARCHAR(64) NOT NULL, 
	etg_cost INTEGER NOT NULL, 
	description VARCHAR(255), 
	is_active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_usage_pricing_id ON usage_pricing (id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_usage_pricing_action_type ON usage_pricing (action_type);

CREATE TABLE IF NOT EXISTS users (
	telegram_id BIGINT, 
	username VARCHAR(64), 
	email VARCHAR(255), 
	phone VARCHAR(32), 
	full_name VARCHAR(255), 
	hashed_password VARCHAR(255), 
	role userrole NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	is_verified BOOLEAN NOT NULL, 
	language_code VARCHAR(8) NOT NULL, 
	referral_code VARCHAR(16), 
	referred_by_id UUID, 
	avatar_url VARCHAR(512), 
	suspended_at TIMESTAMP WITH TIME ZONE, 
	suspension_reason TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	UNIQUE (phone), 
	FOREIGN KEY(referred_by_id) REFERENCES users (id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code ON users (referral_code);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_id ON users (id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_username ON users (username);
CREATE UNIQUE INDEX IF NOT EXISTS ix_users_telegram_id ON users (telegram_id);

CREATE TABLE IF NOT EXISTS admin_audit_log (
	admin_id UUID NOT NULL, 
	action VARCHAR(128) NOT NULL, 
	target_type VARCHAR(64) NOT NULL, 
	target_id VARCHAR(255), 
	old_value JSONB, 
	new_value JSONB, 
	reason TEXT, 
	ip_address VARCHAR(45), 
	user_agent VARCHAR(512), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(admin_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_admin_audit_log_admin_id ON admin_audit_log (admin_id);
CREATE INDEX IF NOT EXISTS ix_admin_audit_log_action ON admin_audit_log (action);
CREATE INDEX IF NOT EXISTS ix_admin_audit_log_id ON admin_audit_log (id);

CREATE TABLE IF NOT EXISTS businesses (
	owner_id UUID NOT NULL, 
	name VARCHAR(255) NOT NULL, 
	slug VARCHAR(128) NOT NULL, 
	description TEXT, 
	category VARCHAR(64), 
	logo_url VARCHAR(512), 
	brand_primary_color VARCHAR(7) NOT NULL, 
	brand_secondary_color VARCHAR(7) NOT NULL, 
	country_code VARCHAR(2) NOT NULL, 
	timezone VARCHAR(64) NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	languages JSONB NOT NULL, 
	latitude FLOAT, 
	longitude FLOAT, 
	address TEXT, 
	phone VARCHAR(32), 
	email VARCHAR(255), 
	website_url VARCHAR(512), 
	mcp_enabled BOOLEAN NOT NULL, 
	is_suspended BOOLEAN NOT NULL, 
	suspended_reason TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(owner_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_businesses_owner_id ON businesses (owner_id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_businesses_slug ON businesses (slug);
CREATE INDEX IF NOT EXISTS ix_businesses_id ON businesses (id);

CREATE TABLE IF NOT EXISTS creator_profiles (
	user_id UUID NOT NULL, 
	display_name VARCHAR(128) NOT NULL, 
	bio TEXT, 
	website_url VARCHAR(512), 
	github_url VARCHAR(512), 
	is_verified BOOLEAN NOT NULL, 
	is_trusted BOOLEAN NOT NULL, 
	total_agents_published INTEGER NOT NULL, 
	total_revenue_etg BIGINT NOT NULL, 
	total_unlocks INTEGER NOT NULL, 
	average_rating FLOAT, 
	payout_method VARCHAR(64), 
	payout_details_encrypted TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (user_id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_creator_profiles_id ON creator_profiles (id);

CREATE TABLE IF NOT EXISTS notifications (
	user_id UUID NOT NULL, 
	notification_type VARCHAR(64) NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	body TEXT NOT NULL, 
	data JSONB, 
	sent_via JSONB NOT NULL, 
	is_read BOOLEAN NOT NULL, 
	read_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_notifications_user_id ON notifications (user_id);
CREATE INDEX IF NOT EXISTS ix_notifications_id ON notifications (id);

CREATE TABLE IF NOT EXISTS platform_settings (
	key VARCHAR(128) NOT NULL, 
	value TEXT NOT NULL, 
	value_type VARCHAR(16) NOT NULL, 
	description VARCHAR(255), 
	is_public BOOLEAN NOT NULL, 
	updated_by_id UUID, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(updated_by_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_platform_settings_id ON platform_settings (id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_platform_settings_key ON platform_settings (key);

CREATE TABLE IF NOT EXISTS user_sessions (
	user_id UUID NOT NULL, 
	access_token_jti VARCHAR(64), 
	refresh_token VARCHAR(512) NOT NULL, 
	refresh_token_jti VARCHAR(64), 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	revoked_at TIMESTAMP WITH TIME ZONE, 
	ip_address VARCHAR(45), 
	user_agent VARCHAR(512), 
	platform VARCHAR(32) NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	UNIQUE (refresh_token)
);
CREATE INDEX IF NOT EXISTS ix_user_sessions_refresh_token_jti ON user_sessions (refresh_token_jti);
CREATE INDEX IF NOT EXISTS ix_user_sessions_access_token_jti ON user_sessions (access_token_jti);
CREATE INDEX IF NOT EXISTS ix_user_sessions_user_id ON user_sessions (user_id);
CREATE INDEX IF NOT EXISTS ix_user_sessions_id ON user_sessions (id);

CREATE TABLE IF NOT EXISTS agents (
	creator_id UUID NOT NULL, 
	name VARCHAR(128) NOT NULL, 
	tagline VARCHAR(255) NOT NULL, 
	description TEXT NOT NULL, 
	category VARCHAR(64) NOT NULL, 
	tags JSONB NOT NULL, 
	capabilities JSONB NOT NULL, 
	encrypted_system_prompt TEXT NOT NULL, 
	encryption_key_ref VARCHAR(128) NOT NULL, 
	child_schema JSONB, 
	setup_guide TEXT, 
	price_etg INTEGER NOT NULL, 
	preferred_model_id VARCHAR(128), 
	status agentstatus NOT NULL, 
	is_featured BOOLEAN NOT NULL, 
	is_staff_pick BOOLEAN NOT NULL, 
	cover_image_url VARCHAR(512), 
	demo_video_url VARCHAR(512), 
	total_unlocks INTEGER NOT NULL, 
	total_active_trials INTEGER NOT NULL, 
	average_rating FLOAT, 
	review_count INTEGER NOT NULL, 
	rejection_reason TEXT, 
	reviewed_by_id UUID, 
	reviewed_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	deleted_at TIMESTAMP WITH TIME ZONE, 
	PRIMARY KEY (id), 
	FOREIGN KEY(creator_id) REFERENCES creator_profiles (id) ON DELETE CASCADE, 
	FOREIGN KEY(preferred_model_id) REFERENCES ai_models (model_id), 
	FOREIGN KEY(reviewed_by_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_agents_creator_id ON agents (creator_id);
CREATE INDEX IF NOT EXISTS ix_agents_category ON agents (category);
CREATE INDEX IF NOT EXISTS ix_agents_id ON agents (id);

CREATE TABLE IF NOT EXISTS bots (
	business_id UUID NOT NULL, 
	bot_username VARCHAR(128), 
	bot_display_name VARCHAR(255), 
	encrypted_token TEXT NOT NULL, 
	token_hash VARCHAR(64) NOT NULL, 
	webhook_url VARCHAR(512), 
	webhook_secret VARCHAR(128), 
	platform platform NOT NULL, 
	status botstatus NOT NULL, 
	grace_period_started_at TIMESTAMP WITH TIME ZONE, 
	suspended_at TIMESTAMP WITH TIME ZONE, 
	suspension_reason TEXT, 
	total_messages_processed BIGINT NOT NULL, 
	total_etg_consumed BIGINT NOT NULL, 
	last_message_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_bots_id ON bots (id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_bots_token_hash ON bots (token_hash);
CREATE INDEX IF NOT EXISTS ix_bots_business_id ON bots (business_id);

CREATE TABLE IF NOT EXISTS business_brain_configs (
	business_id UUID NOT NULL, 
	persona_name VARCHAR(128) NOT NULL, 
	persona_tone VARCHAR(64) NOT NULL, 
	system_prompt_extra TEXT, 
	rag_top_k INTEGER NOT NULL, 
	rag_similarity_threshold FLOAT NOT NULL, 
	max_history_messages INTEGER NOT NULL, 
	fallback_message TEXT NOT NULL, 
	handoff_message TEXT NOT NULL, 
	out_of_hours_message TEXT, 
	collect_customer_name BOOLEAN NOT NULL, 
	collect_customer_phone BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (business_id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_business_brain_configs_id ON business_brain_configs (id);

CREATE TABLE IF NOT EXISTS custom_domains (
	business_id UUID NOT NULL, 
	domain VARCHAR(255) NOT NULL, 
	domain_type VARCHAR(16) NOT NULL, 
	dns_verification_token VARCHAR(64) NOT NULL, 
	is_verified BOOLEAN NOT NULL, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	ssl_issued_at TIMESTAMP WITH TIME ZONE, 
	ssl_expires_at TIMESTAMP WITH TIME ZONE, 
	paid_until TIMESTAMP WITH TIME ZONE, 
	is_active BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_custom_domains_id ON custom_domains (id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_custom_domains_domain ON custom_domains (domain);
CREATE INDEX IF NOT EXISTS ix_custom_domains_business_id ON custom_domains (business_id);

CREATE TABLE IF NOT EXISTS knowledge_documents (
	business_id UUID NOT NULL, 
	filename VARCHAR(255) NOT NULL, 
	file_type VARCHAR(16) NOT NULL, 
	file_size_bytes INTEGER NOT NULL, 
	gcs_path VARCHAR(512) NOT NULL, 
	status documentstatus NOT NULL, 
	extracted_text TEXT, 
	chunk_count INTEGER NOT NULL, 
	error_message TEXT, 
	processed_at TIMESTAMP WITH TIME ZONE, 
	uploaded_by_id UUID NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(uploaded_by_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_knowledge_documents_id ON knowledge_documents (id);
CREATE INDEX IF NOT EXISTS ix_knowledge_documents_business_id ON knowledge_documents (business_id);

CREATE TABLE IF NOT EXISTS landing_pages (
	business_id UUID NOT NULL, 
	title VARCHAR(255), 
	meta_description VARCHAR(512), 
	hero_headline VARCHAR(255), 
	hero_subheadline VARCHAR(512), 
	sections JSONB, 
	seo_keywords JSONB, 
	og_image_url VARCHAR(512), 
	is_published BOOLEAN NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	last_generated_at TIMESTAMP WITH TIME ZONE, 
	total_views BIGINT NOT NULL, 
	total_clicks BIGINT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (business_id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_landing_pages_id ON landing_pages (id);

CREATE TABLE IF NOT EXISTS live_sessions (
	business_id UUID NOT NULL, 
	platform liveplatform NOT NULL, 
	firebase_room_id VARCHAR(128) NOT NULL, 
	overlay_url VARCHAR(512) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE, 
	ended_at TIMESTAMP WITH TIME ZONE, 
	peak_viewers INTEGER NOT NULL, 
	total_orders INTEGER NOT NULL, 
	total_revenue FLOAT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	UNIQUE (firebase_room_id)
);
CREATE INDEX IF NOT EXISTS ix_live_sessions_id ON live_sessions (id);
CREATE INDEX IF NOT EXISTS ix_live_sessions_business_id ON live_sessions (business_id);

CREATE TABLE IF NOT EXISTS mcp_listings (
	business_id UUID NOT NULL, 
	structured_data JSONB, 
	search_keywords JSONB, 
	ai_search_score FLOAT, 
	is_published BOOLEAN NOT NULL, 
	last_indexed_at TIMESTAMP WITH TIME ZONE, 
	total_fetches BIGINT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (business_id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_mcp_listings_id ON mcp_listings (id);

CREATE TABLE IF NOT EXISTS mini_app_configs (
	business_id UUID NOT NULL, 
	theme_primary VARCHAR(7) NOT NULL, 
	theme_secondary VARCHAR(7) NOT NULL, 
	theme_accent VARCHAR(7) NOT NULL, 
	font_family VARCHAR(64) NOT NULL, 
	hero_image_url VARCHAR(512), 
	layout_config JSONB, 
	ui_child_prompt TEXT, 
	show_categories BOOLEAN NOT NULL, 
	show_search BOOLEAN NOT NULL, 
	show_cart BOOLEAN NOT NULL, 
	custom_sections JSONB, 
	is_published BOOLEAN NOT NULL, 
	published_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (business_id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_mini_app_configs_id ON mini_app_configs (id);

CREATE TABLE IF NOT EXISTS payment_integrations (
	business_id UUID NOT NULL, 
	provider paymentprovider NOT NULL, 
	encrypted_api_key TEXT NOT NULL, 
	encrypted_secret_key TEXT, 
	encrypted_webhook_secret TEXT, 
	public_key VARCHAR(255), 
	is_active BOOLEAN NOT NULL, 
	is_verified BOOLEAN NOT NULL, 
	verified_at TIMESTAMP WITH TIME ZONE, 
	extra_config JSONB, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_payment_integration_business_provider UNIQUE (business_id, provider), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_payment_integrations_id ON payment_integrations (id);
CREATE INDEX IF NOT EXISTS ix_payment_integrations_business_id ON payment_integrations (business_id);

CREATE TABLE IF NOT EXISTS recharge_orders (
	business_id UUID NOT NULL, 
	etg_package_id UUID, 
	etg_amount INTEGER NOT NULL, 
	fiat_amount FLOAT NOT NULL, 
	fiat_currency VARCHAR(3) NOT NULL, 
	payment_provider paymentprovider NOT NULL, 
	payment_reference VARCHAR(255), 
	status paymentstatus NOT NULL, 
	webhook_payload JSONB, 
	completed_at TIMESTAMP WITH TIME ZONE, 
	bonus_etg INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(etg_package_id) REFERENCES etg_packages (id)
);
CREATE INDEX IF NOT EXISTS ix_recharge_orders_payment_reference ON recharge_orders (payment_reference);
CREATE INDEX IF NOT EXISTS ix_recharge_orders_id ON recharge_orders (id);
CREATE INDEX IF NOT EXISTS ix_recharge_orders_business_id ON recharge_orders (business_id);

CREATE TABLE IF NOT EXISTS subscriptions (
	business_id UUID NOT NULL, 
	plan subscriptionplan NOT NULL, 
	started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	expires_at TIMESTAMP WITH TIME ZONE, 
	is_active BOOLEAN NOT NULL, 
	payment_provider paymentprovider, 
	payment_reference VARCHAR(255), 
	cancelled_at TIMESTAMP WITH TIME ZONE, 
	cancel_reason TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_subscriptions_business_id ON subscriptions (business_id);
CREATE INDEX IF NOT EXISTS ix_subscriptions_id ON subscriptions (id);

CREATE TABLE IF NOT EXISTS token_wallets (
	business_id UUID NOT NULL, 
	balance BIGINT NOT NULL, 
	escrow_balance BIGINT NOT NULL, 
	lifetime_recharged BIGINT NOT NULL, 
	lifetime_spent BIGINT NOT NULL, 
	auto_recharge_enabled BOOLEAN NOT NULL, 
	auto_recharge_threshold INTEGER NOT NULL, 
	auto_recharge_amount INTEGER NOT NULL, 
	auto_recharge_provider VARCHAR(32), 
	monthly_spend_limit INTEGER, 
	current_month_spend INTEGER NOT NULL, 
	spend_limit_reset_at TIMESTAMP WITH TIME ZONE, 
	subscription_plan subscriptionplan NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (business_id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_token_wallets_id ON token_wallets (id);

CREATE TABLE IF NOT EXISTS usage_daily_aggregates (
	business_id UUID NOT NULL, 
	date TIMESTAMP WITH TIME ZONE NOT NULL, 
	action_type VARCHAR(64) NOT NULL, 
	total_events INTEGER NOT NULL, 
	total_etg INTEGER NOT NULL, 
	total_input_tokens BIGINT NOT NULL, 
	total_output_tokens BIGINT NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_daily_agg UNIQUE (business_id, date, action_type), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_usage_daily_aggregates_business_id ON usage_daily_aggregates (business_id);
CREATE INDEX IF NOT EXISTS ix_usage_daily_aggregates_id ON usage_daily_aggregates (id);

CREATE TABLE IF NOT EXISTS agent_reports (
	agent_id UUID NOT NULL, 
	reported_by_id UUID NOT NULL, 
	reason VARCHAR(64) NOT NULL, 
	details TEXT, 
	is_resolved BOOLEAN NOT NULL, 
	resolved_by_id UUID, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	resolution_notes TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE, 
	FOREIGN KEY(reported_by_id) REFERENCES users (id), 
	FOREIGN KEY(resolved_by_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_agent_reports_agent_id ON agent_reports (agent_id);
CREATE INDEX IF NOT EXISTS ix_agent_reports_id ON agent_reports (id);

CREATE TABLE IF NOT EXISTS child_agents (
	agent_id UUID NOT NULL, 
	business_id UUID NOT NULL, 
	display_name VARCHAR(128), 
	child_data JSONB, 
	is_active BOOLEAN NOT NULL, 
	assigned_to_bot_id UUID, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_child_agent_business UNIQUE (agent_id, business_id), 
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE, 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(assigned_to_bot_id) REFERENCES bots (id)
);
CREATE INDEX IF NOT EXISTS ix_child_agents_agent_id ON child_agents (agent_id);
CREATE INDEX IF NOT EXISTS ix_child_agents_id ON child_agents (id);
CREATE INDEX IF NOT EXISTS ix_child_agents_business_id ON child_agents (business_id);

CREATE TABLE IF NOT EXISTS etg_transactions (
	wallet_id UUID NOT NULL, 
	amount INTEGER NOT NULL, 
	balance_before BIGINT NOT NULL, 
	balance_after BIGINT NOT NULL, 
	transaction_type VARCHAR(32) NOT NULL, 
	description VARCHAR(255) NOT NULL, 
	reference_id VARCHAR(255), 
	reference_type VARCHAR(64), 
	admin_id UUID, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(wallet_id) REFERENCES token_wallets (id) ON DELETE CASCADE, 
	FOREIGN KEY(admin_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_etg_transactions_wallet_id ON etg_transactions (wallet_id);
CREATE INDEX IF NOT EXISTS ix_etg_transactions_id ON etg_transactions (id);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
	business_id UUID NOT NULL, 
	document_id UUID, 
	content TEXT NOT NULL, 
	token_count INTEGER NOT NULL, 
	chunk_index INTEGER NOT NULL, 
	embedding_model VARCHAR(64) NOT NULL, 
	metadata JSONB, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(document_id) REFERENCES knowledge_documents (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_business_id ON knowledge_chunks (business_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_id ON knowledge_chunks (id);
CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_business ON knowledge_chunks (business_id);
CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_document_id ON knowledge_chunks (document_id);

CREATE TABLE IF NOT EXISTS knowledge_items (
	business_id UUID NOT NULL, 
	item_type knowledgeitemtype NOT NULL, 
	title VARCHAR(255) NOT NULL, 
	body TEXT, 
	data JSONB, 
	is_active BOOLEAN NOT NULL, 
	source_document_id UUID, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(source_document_id) REFERENCES knowledge_documents (id)
);
CREATE INDEX IF NOT EXISTS ix_knowledge_items_id ON knowledge_items (id);
CREATE INDEX IF NOT EXISTS ix_knowledge_items_business_id ON knowledge_items (business_id);

CREATE TABLE IF NOT EXISTS mcp_fetch_logs (
	listing_id UUID NOT NULL, 
	fetcher_name VARCHAR(64), 
	fetcher_ip VARCHAR(45), 
	user_agent VARCHAR(512), 
	endpoint VARCHAR(64), 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(listing_id) REFERENCES mcp_listings (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_mcp_fetch_logs_listing_id ON mcp_fetch_logs (listing_id);
CREATE INDEX IF NOT EXISTS ix_mcp_fetch_logs_id ON mcp_fetch_logs (id);

CREATE TABLE IF NOT EXISTS support_tickets (
	user_id UUID NOT NULL, 
	business_id UUID, 
	related_agent_id UUID, 
	category VARCHAR(64) NOT NULL, 
	subject VARCHAR(255) NOT NULL, 
	status ticketstatus NOT NULL, 
	priority VARCHAR(16) NOT NULL, 
	assigned_to_id UUID, 
	ai_suggested_answer TEXT, 
	resolved_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE CASCADE, 
	FOREIGN KEY(business_id) REFERENCES businesses (id), 
	FOREIGN KEY(related_agent_id) REFERENCES agents (id), 
	FOREIGN KEY(assigned_to_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_support_tickets_id ON support_tickets (id);
CREATE INDEX IF NOT EXISTS ix_support_tickets_user_id ON support_tickets (user_id);

CREATE TABLE IF NOT EXISTS token_escrow (
	wallet_id UUID NOT NULL, 
	amount INTEGER NOT NULL, 
	reason VARCHAR(255) NOT NULL, 
	release_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	status escrowstatus NOT NULL, 
	released_at TIMESTAMP WITH TIME ZONE, 
	dispute_reason TEXT, 
	dispute_opened_at TIMESTAMP WITH TIME ZONE, 
	resolved_by_id UUID, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(wallet_id) REFERENCES token_wallets (id) ON DELETE CASCADE, 
	FOREIGN KEY(resolved_by_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_token_escrow_wallet_id ON token_escrow (wallet_id);
CREATE INDEX IF NOT EXISTS ix_token_escrow_id ON token_escrow (id);

CREATE TABLE IF NOT EXISTS wallet_alerts (
	wallet_id UUID NOT NULL, 
	alert_type alerttype NOT NULL, 
	balance_at_alert INTEGER NOT NULL, 
	sent_via JSONB NOT NULL, 
	acknowledged_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(wallet_id) REFERENCES token_wallets (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_wallet_alerts_id ON wallet_alerts (id);
CREATE INDEX IF NOT EXISTS ix_wallet_alerts_wallet_id ON wallet_alerts (wallet_id);

CREATE TABLE IF NOT EXISTS agent_trials (
	agent_id UUID NOT NULL, 
	business_id UUID NOT NULL, 
	child_agent_id UUID, 
	expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
	is_converted BOOLEAN NOT NULL, 
	converted_at TIMESTAMP WITH TIME ZONE, 
	warning_sent_at TIMESTAMP WITH TIME ZONE, 
	critical_sent_at TIMESTAMP WITH TIME ZONE, 
	expired_notified_at TIMESTAMP WITH TIME ZONE, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_trial_agent_business UNIQUE (agent_id, business_id), 
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE, 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(child_agent_id) REFERENCES child_agents (id)
);
CREATE INDEX IF NOT EXISTS ix_agent_trials_business_id ON agent_trials (business_id);
CREATE INDEX IF NOT EXISTS ix_agent_trials_id ON agent_trials (id);
CREATE INDEX IF NOT EXISTS ix_agent_trials_agent_id ON agent_trials (agent_id);

CREATE TABLE IF NOT EXISTS agent_unlocks (
	agent_id UUID NOT NULL, 
	business_id UUID NOT NULL, 
	child_agent_id UUID, 
	etg_paid INTEGER NOT NULL, 
	escrow_id UUID, 
	payment_reference VARCHAR(255), 
	is_refunded BOOLEAN NOT NULL, 
	refunded_at TIMESTAMP WITH TIME ZONE, 
	refund_reason TEXT, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE, 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(child_agent_id) REFERENCES child_agents (id), 
	FOREIGN KEY(escrow_id) REFERENCES token_escrow (id)
);
CREATE INDEX IF NOT EXISTS ix_agent_unlocks_id ON agent_unlocks (id);
CREATE INDEX IF NOT EXISTS ix_agent_unlocks_agent_id ON agent_unlocks (agent_id);
CREATE INDEX IF NOT EXISTS ix_agent_unlocks_business_id ON agent_unlocks (business_id);

CREATE TABLE IF NOT EXISTS conversations (
	business_id UUID NOT NULL, 
	bot_id UUID NOT NULL, 
	platform platform NOT NULL, 
	customer_platform_id VARCHAR(128) NOT NULL, 
	customer_name VARCHAR(255), 
	customer_username VARCHAR(128), 
	customer_phone VARCHAR(32), 
	detected_language VARCHAR(8) NOT NULL, 
	is_active BOOLEAN NOT NULL, 
	last_message_at TIMESTAMP WITH TIME ZONE, 
	total_messages INTEGER NOT NULL, 
	total_etg_spent INTEGER NOT NULL, 
	child_agent_id UUID, 
	context_data JSONB, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_conversation_bot_customer UNIQUE (bot_id, customer_platform_id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(bot_id) REFERENCES bots (id) ON DELETE CASCADE, 
	FOREIGN KEY(child_agent_id) REFERENCES child_agents (id)
);
CREATE INDEX IF NOT EXISTS ix_conversations_business_id ON conversations (business_id);
CREATE INDEX IF NOT EXISTS ix_conversations_customer_platform_id ON conversations (customer_platform_id);
CREATE INDEX IF NOT EXISTS ix_conversations_bot_id ON conversations (bot_id);
CREATE INDEX IF NOT EXISTS ix_conversations_id ON conversations (id);

CREATE TABLE IF NOT EXISTS live_products (
	session_id UUID NOT NULL, 
	knowledge_item_id UUID, 
	name VARCHAR(255) NOT NULL, 
	price FLOAT NOT NULL, 
	image_url VARCHAR(512), 
	promo_code VARCHAR(32), 
	promo_discount_pct FLOAT, 
	is_current BOOLEAN NOT NULL, 
	activated_at TIMESTAMP WITH TIME ZONE, 
	deactivated_at TIMESTAMP WITH TIME ZONE, 
	views_while_active INTEGER NOT NULL, 
	orders_while_active INTEGER NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(session_id) REFERENCES live_sessions (id) ON DELETE CASCADE, 
	FOREIGN KEY(knowledge_item_id) REFERENCES knowledge_items (id)
);
CREATE INDEX IF NOT EXISTS ix_live_products_session_id ON live_products (session_id);
CREATE INDEX IF NOT EXISTS ix_live_products_id ON live_products (id);

CREATE TABLE IF NOT EXISTS ticket_messages (
	ticket_id UUID NOT NULL, 
	sender_id UUID, 
	role VARCHAR(16) NOT NULL, 
	content TEXT NOT NULL, 
	attachments JSONB, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(ticket_id) REFERENCES support_tickets (id) ON DELETE CASCADE, 
	FOREIGN KEY(sender_id) REFERENCES users (id)
);
CREATE INDEX IF NOT EXISTS ix_ticket_messages_id ON ticket_messages (id);
CREATE INDEX IF NOT EXISTS ix_ticket_messages_ticket_id ON ticket_messages (ticket_id);

CREATE TABLE IF NOT EXISTS agent_reviews (
	agent_id UUID NOT NULL, 
	business_id UUID NOT NULL, 
	unlock_id UUID NOT NULL, 
	rating INTEGER NOT NULL, 
	review_text TEXT, 
	is_visible BOOLEAN NOT NULL, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_review_agent_business UNIQUE (agent_id, business_id), 
	FOREIGN KEY(agent_id) REFERENCES agents (id) ON DELETE CASCADE, 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(unlock_id) REFERENCES agent_unlocks (id)
);
CREATE INDEX IF NOT EXISTS ix_agent_reviews_business_id ON agent_reviews (business_id);
CREATE INDEX IF NOT EXISTS ix_agent_reviews_id ON agent_reviews (id);
CREATE INDEX IF NOT EXISTS ix_agent_reviews_agent_id ON agent_reviews (agent_id);

CREATE TABLE IF NOT EXISTS chat_messages (
	conversation_id UUID NOT NULL, 
	role messagerole NOT NULL, 
	content TEXT NOT NULL, 
	model_used VARCHAR(64), 
	input_tokens INTEGER NOT NULL, 
	output_tokens INTEGER NOT NULL, 
	etg_charged INTEGER NOT NULL, 
	chunks_retrieved JSONB, 
	media_type VARCHAR(32), 
	media_url VARCHAR(512), 
	telegram_message_id BIGINT, 
	latency_ms INTEGER, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation_id ON chat_messages (conversation_id);
CREATE INDEX IF NOT EXISTS ix_chat_messages_id ON chat_messages (id);

CREATE TABLE IF NOT EXISTS orders (
	business_id UUID NOT NULL, 
	order_number VARCHAR(16) NOT NULL, 
	customer_platform_id VARCHAR(128) NOT NULL, 
	customer_name VARCHAR(255), 
	items JSONB NOT NULL, 
	subtotal FLOAT NOT NULL, 
	platform_fee FLOAT NOT NULL, 
	total FLOAT NOT NULL, 
	currency VARCHAR(3) NOT NULL, 
	payment_provider paymentprovider, 
	payment_reference VARCHAR(255), 
	payment_status paymentstatus NOT NULL, 
	order_status orderstatus NOT NULL, 
	paid_at TIMESTAMP WITH TIME ZONE, 
	fulfilled_at TIMESTAMP WITH TIME ZONE, 
	notes TEXT, 
	delivery_address JSONB, 
	conversation_id UUID, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id)
);
CREATE INDEX IF NOT EXISTS ix_orders_payment_reference ON orders (payment_reference);
CREATE INDEX IF NOT EXISTS ix_orders_business_id ON orders (business_id);
CREATE INDEX IF NOT EXISTS ix_orders_id ON orders (id);
CREATE UNIQUE INDEX IF NOT EXISTS ix_orders_order_number ON orders (order_number);
CREATE INDEX IF NOT EXISTS ix_orders_customer_platform_id ON orders (customer_platform_id);

CREATE TABLE IF NOT EXISTS usage_events (
	business_id UUID NOT NULL, 
	bot_id UUID, 
	conversation_id UUID, 
	action_type VARCHAR(64) NOT NULL, 
	model_id VARCHAR(128), 
	model_tier modeltier, 
	input_tokens INTEGER NOT NULL, 
	output_tokens INTEGER NOT NULL, 
	etg_charged INTEGER NOT NULL, 
	latency_ms INTEGER, 
	id UUID NOT NULL, 
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(business_id) REFERENCES businesses (id) ON DELETE CASCADE, 
	FOREIGN KEY(bot_id) REFERENCES bots (id), 
	FOREIGN KEY(conversation_id) REFERENCES conversations (id)
);
CREATE INDEX IF NOT EXISTS ix_usage_events_action_type ON usage_events (action_type);
CREATE INDEX IF NOT EXISTS ix_usage_events_id ON usage_events (id);
CREATE INDEX IF NOT EXISTS idx_usage_events_business_created ON usage_events (business_id, created_at);
CREATE INDEX IF NOT EXISTS ix_usage_events_business_id ON usage_events (business_id);

