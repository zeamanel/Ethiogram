# app/core/exceptions.py
from typing import Any, Optional

class EthiogramError(Exception):
    def __init__(self, message: str, code: str = "ETHIOGRAM_ERROR",
                 status_code: int = 500, details: Optional[dict[str, Any]] = None):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)

    def to_dict(self) -> dict[str, Any]:
        return {"error": self.code, "message": self.message, "details": self.details}

class AuthError(EthiogramError):
    def __init__(self, message: str = "Authentication required"):
        super().__init__(message, code="AUTH_ERROR", status_code=401)

class TokenExpiredError(AuthError):
    def __init__(self):
        super().__init__("Token has expired")
        self.code = "TOKEN_EXPIRED"

class TokenInvalidError(AuthError):
    def __init__(self):
        super().__init__("Invalid token")
        self.code = "TOKEN_INVALID"

class PermissionDeniedError(EthiogramError):
    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, code="PERMISSION_DENIED", status_code=403)

class AdminRequiredError(PermissionDeniedError):
    def __init__(self):
        super().__init__("Admin access required")
        self.code = "ADMIN_REQUIRED"

class SuperAdminRequiredError(PermissionDeniedError):
    def __init__(self):
        super().__init__("Super admin access required")
        self.code = "SUPER_ADMIN_REQUIRED"

class NotFoundError(EthiogramError):
    def __init__(self, resource: str, identifier: Any = None):
        msg = f"{resource} not found"
        if identifier:
            msg = f"{resource} '{identifier}' not found"
        super().__init__(msg, code="NOT_FOUND", status_code=404)

class AlreadyExistsError(EthiogramError):
    def __init__(self, resource: str, field: str = "id"):
        super().__init__(f"{resource} already exists", code="ALREADY_EXISTS", status_code=409)

class ValidationError(EthiogramError):
    def __init__(self, message: str, field: Optional[str] = None):
        super().__init__(message, code="VALIDATION_ERROR", status_code=422)

class BotTokenInvalidError(EthiogramError):
    def __init__(self):
        super().__init__("The bot token is invalid or has been revoked", code="BOT_TOKEN_INVALID", status_code=400)

class BotTokenAlreadyRegisteredError(EthiogramError):
    def __init__(self):
        super().__init__("This bot token is already registered", code="BOT_TOKEN_DUPLICATE", status_code=409)

class BotPausedError(EthiogramError):
    def __init__(self):
        super().__init__("Bot paused — insufficient ETG balance", code="BOT_PAUSED", status_code=402)

class BotSuspendedError(EthiogramError):
    def __init__(self, reason: str = ""):
        super().__init__(f"Bot suspended by admin. {reason}".strip(), code="BOT_SUSPENDED", status_code=403)

class InsufficientBalanceError(EthiogramError):
    def __init__(self, required: int, available: int):
        super().__init__(f"Insufficient ETG. Required: {required}, Available: {available}",
                         code="INSUFFICIENT_BALANCE", status_code=402)
        self.details = {"required_etg": required, "available_etg": available}

class ModelUnavailableError(EthiogramError):
    def __init__(self, model_id: str):
        super().__init__(f"AI model '{model_id}' unavailable", code="MODEL_UNAVAILABLE", status_code=503)

class AllModelsFailedError(EthiogramError):
    def __init__(self):
        super().__init__("All AI models unavailable", code="ALL_MODELS_FAILED", status_code=503)

class AgentNotLiveError(EthiogramError):
    def __init__(self, status: str):
        super().__init__(f"Agent not available (status: {status})", code="AGENT_NOT_LIVE", status_code=400)

class AgentTrialExpiredError(EthiogramError):
    def __init__(self):
        super().__init__("Trial expired. Please unlock to continue.", code="TRIAL_EXPIRED", status_code=402)

class DocumentProcessingError(EthiogramError):
    def __init__(self, filename: str, reason: str):
        super().__init__(f"Failed to process '{filename}': {reason}", code="DOCUMENT_PROCESSING_ERROR", status_code=422)

class RateLimitError(EthiogramError):
    def __init__(self, retry_after: int = 60):
        super().__init__("Too many requests", code="RATE_LIMITED", status_code=429)
        self.details = {"retry_after_seconds": retry_after}

class ExternalServiceError(EthiogramError):
    def __init__(self, service: str, reason: str = ""):
        super().__init__(f"External service error ({service}): {reason}", code="EXTERNAL_SERVICE_ERROR", status_code=502)

class TelegramAPIError(ExternalServiceError):
    def __init__(self, reason: str = ""):
        super().__init__("Telegram", reason)
        self.code = "TELEGRAM_API_ERROR"
