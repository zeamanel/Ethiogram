# Ethiogram — Testing Phases

A staged plan from pure unit tests up to production smoke tests. Each phase
gates the next; do not advance with failures.

---

## Phase 1 — Unit tests (no infrastructure)

Pure-logic tests: utils, security primitives, metering math. Run anywhere in
seconds — these are the CI fast path.

```bash
pip install -r requirements.txt
cp .env.example .env
pytest tests/test_utils.py tests/test_security.py tests/test_metering.py -v
```

**Covers:** Amharic/English detection, Ethiopian phone normalisation,
encrypt/decrypt roundtrips, bot-token hashing, JWT lifecycle (expiry,
tampering, refresh-vs-access), password hashing, ETG balance cache logic,
pre-flight checks, pricing fallbacks.

**Status: ✅ passing (47 tests).**

---

## Phase 2 — API tests (in-memory SQLite + mock Redis)

FastAPI endpoints exercised through `httpx.AsyncClient` with dependency
overrides (`get_db` → SQLite, `get_redis` → AsyncMock). `tests/conftest.py`
shims `JSONB` → `JSON` so the ORM schema creates on SQLite.

```bash
pytest tests/ -v          # full suite
pytest tests/ --cov=app --cov-report=term-missing
```

**Covers:** register/login/refresh, duplicate-email rejection, weak-password
422, `/me` end-to-end, 401 for ghost users (no existence leak), webhook
silent-200 on unknown token hash, malformed JSON resilience, business
creation + unique-slug collision handling, bot onboarding with welcome-bonus
crediting (Telegram mocked).

**Status: ✅ passing (77 tests total).**

**Known limitation:** pgvector similarity search cannot run on SQLite. RAG
search paths are covered in Phase 3.

---

## Phase 3 — Integration tests (Docker: real Postgres + Redis)

Real `pgvector/pgvector:pg16` and Redis 7 via docker-compose. This is where
migrations, vector search, and `FOR UPDATE SKIP LOCKED` worker logic get
verified.

```bash
make dev            # api + workers + postgres + redis
make migrate        # apply migrations/000_run_all.sql
make test           # suite inside the api container against real services
```

Manual checks to run once:

| Check | Command / Expectation |
|---|---|
| Migrations idempotent | run `make migrate` twice — second run must not error |
| pgvector live | `make psql` → `SELECT '[1,2,3]'::vector;` |
| Health | `curl localhost:8000/health` → `{"status":"healthy"}` |
| OpenAPI loads | `curl localhost:8000/openapi.json` — catches router/schema errors |
| Ledger immutability | `UPDATE etg_transactions SET amount=0;` → 0 rows (rule blocks it) |
| Embedding worker | insert a `pending` knowledge doc → worker picks it up, chunks appear |

---

## Phase 4 — End-to-end Telegram flow (staging)

Requires a real bot token from @BotFather and a public URL (Cloud Run staging
service, or `ngrok http 8000` locally with `BASE_URL` set to the tunnel).

1. **Create business:** `POST /api/v1/dashboard/businesses` with `{"name": ...}`
   → returns a `business_id` (a business is required before onboarding a bot).
2. **Onboard:** `POST /api/v1/bots` with the token + `business_id` → expect
   webhook registered, wallet created with 100 ETG bonus, welcome message
   arrives in Telegram.
3. **Chat:** message the bot → typing indicator, AI reply, conversation row
   created, ETG charged (check `GET /api/v1/billing/transactions`).
4. **Amharic:** send "ሰላም" → `detected_language` becomes `am`.
5. **RAG:** upload a doc, wait for embedding worker, ask a question answered
   only by the doc → reply must use it.
6. **Balance exhaustion:** drain wallet (admin can set balance) → bot enters
   grace, low/critical alerts fire exactly once each (Redis dedup).
7. **Offboard:** `DELETE /api/v1/bots/{id}` → webhook removed, status
   `disconnected`.

---

## Phase 5 — Load & security checks (pre-launch)

- **Webhook throughput:** `hey -n 2000 -c 50 -m POST -D update.json
  https://staging.../webhook/{hash}` — p95 < 2s, zero 5xx. (Telegram retries
  on non-200, so errors compound.)
- **Concurrency safety:** run two escrow-release workers simultaneously
  against due escrows — each escrow released exactly once (`FOR UPDATE`).
- **Secrets audit:** `git log -p | grep -iE 'sk-|CHASECK|token'` — nothing real
  committed; all prod secrets in Secret Manager only.
- **Authz matrix:** non-owner cannot read another business's dashboard (404),
  non-admin hits admin routes → 403, admin without `X-Ethiogram-Admin`
  header → 403.

---

## CI

`.github/workflows/ci.yml` runs Phases 1–2 plus a production Docker build on
every push/PR, and deploys via Cloud Build on merge to `main`. See
`docs/DEPLOYMENT.md`.
