# app/main.py
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
import jwt

from app.core.config import settings
from app.core.exceptions import EthiogramError
from app.core.logging import configure_logging, get_logger
from app.db.session import connect_db, connect_redis, disconnect_db, disconnect_redis

configure_logging()
logger = get_logger(__name__)


_BUILD_MARKER = "trace-v4-botlookup-print"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Plain stdout banner so it's trivial to confirm WHICH code revision is
    # actually serving traffic in Cloud Run (independent of the log config).
    print(f"[BOOT] Ethiogram starting — marker={_BUILD_MARKER} env={settings.environment}", flush=True)
    logger.info(f"Starting {settings.app_name} v{settings.app_version}", build_marker=_BUILD_MARKER)
    await connect_db()
    await connect_redis()
    logger.info("All services connected — platform ready")
    yield
    logger.info("Shutting down")
    await disconnect_db()
    await disconnect_redis()


app = FastAPI(
    title="Ethiogram API",
    description="AI-Powered Business Agent Platform",
    version=settings.app_version,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
    openapi_url="/openapi.json" if not settings.is_production else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.is_production:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


@app.exception_handler(EthiogramError)
async def ethiogram_error_handler(request: Request, exc: EthiogramError):
    return JSONResponse(status_code=exc.status_code, content=exc.to_dict())


@app.exception_handler(jwt.ExpiredSignatureError)
async def jwt_expired_handler(request: Request, exc):
    return JSONResponse(status_code=401, content={"error": "TOKEN_EXPIRED", "message": "Token expired"})


@app.exception_handler(Exception)
async def generic_error_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled: {type(exc).__name__}: {exc}")
    return JSONResponse(status_code=500, content={"error": "INTERNAL_ERROR", "message": "Unexpected error"})


from app.api import auth, bots, webhooks, admin, billing, agents, dashboard, knowledge, businesses

app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(bots.router, prefix=settings.api_prefix)
app.include_router(webhooks.router)
app.include_router(admin.router, prefix=settings.api_prefix)
app.include_router(billing.router, prefix=settings.api_prefix)
app.include_router(agents.router, prefix=settings.api_prefix)
app.include_router(dashboard.router, prefix=settings.api_prefix)
app.include_router(knowledge.router, prefix=settings.api_prefix)
app.include_router(businesses.router, prefix=settings.api_prefix)

# Telegram Mini Apps (static, same-origin). Owner console at /app/owner/.
import os as _os
from fastapi.staticfiles import StaticFiles

_static_dir = _os.path.join(_os.path.dirname(__file__), "static")
app.mount("/app", StaticFiles(directory=_static_dir, html=True), name="miniapp")


@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": settings.app_version}


if __name__ == "__main__":
    import os

    import uvicorn

    # Cloud Run injects PORT (default 8080) and does not allow overriding it.
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port)
