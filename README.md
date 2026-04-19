# Ethiogram

AI-powered business infrastructure for Ethiopian SMBs. Businesses deploy Telegram bots backed by Vertex AI (Gemini), a knowledge-base RAG pipeline, and a marketplace of specialist Father Agents — all metered through ETG tokens.

## Architecture

```
Telegram Bot ──► /webhook/{token_hash}
                      │
                 Intent Router
                      │
          ┌───────────┼───────────┐
     BaseAgent   AccountantAgent  ConciergeAgent  …
          │
     ModelRouter (7-level priority chain)
     Vertex AI / OpenAI / Anthropic
          │
     RAG (pgvector cosine search)
     MeteringService (ETG charge)
```

**Stack:** FastAPI · SQLAlchemy 2.0 async · PostgreSQL 16 + pgvector · Redis 7 · Google Cloud Run / Vertex AI · Docker

---

## Quickstart

```bash
cp .env.example .env          # fill in real keys
make dev                      # starts api + workers + postgres + redis
make migrate                  # apply all SQL migrations
```

API docs (dev only): http://localhost:8000/docs

---

## Development

```bash
make test          # full test suite
make test-unit     # fast unit tests only (no DB)
make lint          # ruff linter
make fmt           # ruff formatter
make typecheck     # mypy
make logs          # tail api + worker logs
make shell         # shell inside api container
make psql          # interactive psql
```

---

## Project Structure

```
app/
├── core/           config · security · logging · metering · exceptions
├── db/             SQLAlchemy models · session · base
├── api/            auth · bots · webhooks · billing · agents · dashboard · admin
├── agents/         base · router · accountant · concierge
├── services/       telegram · bot_onboarding · model_router · rag · embedding
│                   vertex_ai · openai · anthropic · storage · ocr
└── utils/          language · phone · text

workers/
├── embedding_worker.py     document → OCR → chunk → embed → pgvector
├── trial_monitor.py        agent trial expiry lifecycle
├── notification_worker.py  dispatch Notification rows via Telegram
└── escrow_release.py       70/30 creator/platform split after 7-day hold

migrations/
├── 000_run_all.sql          run all in order
├── 001_users_auth.sql       users · sessions · businesses · bots · conversations
├── 002_knowledge_base.sql   knowledge docs · chunks (pgvector) · orders
├── 003_agents_marketplace.sql  father/child agents · unlocks · trials · reviews
├── 004_token_economy.sql    wallets · ledger · usage events · escrow · pricing
└── 005_mini_app_landing_mcp.sql  mini-app · landing pages · live commerce · MCP
```

---

## ETG Token Economy

| Action | Cost (ETG) |
|---|---|
| AI reply (Gemini Flash) | 2 |
| AI reply (GPT-4o / Claude) | 5 |
| AI reply (o3 reasoning) | 10 |
| RAG knowledge search | 1 |
| OCR extraction | 3 |
| Order intake | 1 |
| Calendar booking | 2 |

New users receive **100 ETG** on signup. Referrals earn **200 ETG** each.

Agent marketplace uses a **70/30 creator/platform** revenue split held in a 7-day escrow.

---

## Environment Variables

See `.env.example` for all required variables.  Key groups:

- **`SECRET_KEY`** / **`ENCRYPTION_KEY`** — generate with `python -c "import secrets; print(secrets.token_hex(32))"`
- **`MASTER_BOT_TOKEN`** — @ethiogram_bot token from @BotFather
- **`DATABASE_URL`** — PostgreSQL async URL (`postgresql+asyncpg://...`)
- **`VERTEX_AI_LOCATION`** / **`GCP_PROJECT_ID`** — Google Cloud config
- **`CHAPA_SECRET_KEY`** — Ethiopian payment gateway

---

## Deployment (Google Cloud Run)

```bash
gcloud builds submit --tag gcr.io/$PROJECT_ID/ethiogram-api
gcloud run deploy ethiogram-api \
  --image gcr.io/$PROJECT_ID/ethiogram-api \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --set-env-vars "ENVIRONMENT=production"
```

Workers deploy as separate Cloud Run Jobs or Cloud Run services with `--command`.
