# app/core/logging.py
import logging
import sys
import json
from typing import Any
from datetime import datetime, timezone
from app.core.config import settings

class CloudRunFormatter(logging.Formatter):
    SEVERITY_MAP = {logging.DEBUG: "DEBUG", logging.INFO: "INFO",
                    logging.WARNING: "WARNING", logging.ERROR: "ERROR", logging.CRITICAL: "CRITICAL"}

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "severity": self.SEVERITY_MAP.get(record.levelno, "DEFAULT"),
            "message": record.getMessage(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "logger": record.name, "module": record.module,
            "function": record.funcName, "line": record.lineno,
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in ("name","msg","args","levelname","levelno","pathname","filename",
                           "module","exc_info","exc_text","stack_info","lineno","funcName",
                           "created","msecs","relativeCreated","thread","threadName",
                           "processName","process","message","taskName"):
                log_entry[key] = value
        return json.dumps(log_entry, default=str)

def configure_logging() -> None:
    log_level = getattr(logging, settings.log_level.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudRunFormatter())
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

def get_logger(name: str) -> "EthiogramLogger":
    return EthiogramLogger(logging.getLogger(name))

class EthiogramLogger:
    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, message: str, **kwargs: Any) -> None:
        extra = {k: str(v) if hasattr(v, '__str__') else v for k, v in kwargs.items()}
        self._logger.log(level, message, extra=extra)

    def debug(self, message: str, **kwargs): self._log(logging.DEBUG, message, **kwargs)
    def info(self, message: str, **kwargs): self._log(logging.INFO, message, **kwargs)
    def warning(self, message: str, **kwargs): self._log(logging.WARNING, message, **kwargs)
    def error(self, message: str, **kwargs): self._log(logging.ERROR, message, **kwargs)
    def critical(self, message: str, **kwargs): self._log(logging.CRITICAL, message, **kwargs)

    def model_switched(self, from_model: str, to_model: str, reason: str, business_id=None):
        self.warning(f"AI model switched: {from_model} → {to_model}",
                     action="model_switch", from_model=from_model, to_model=to_model,
                     switch_reason=reason, business_id=business_id)

    def etg_charged(self, amount: int, action_type: str, business_id: str, balance_after: int):
        self.info(f"ETG charged: {amount} for {action_type}",
                  action="etg_charge", etg_amount=amount, action_type=action_type,
                  business_id=business_id, balance_after=balance_after)

    def admin_action(self, admin_id: str, action: str, target_type: str, target_id: str, details=None):
        self.info(f"Admin action: {action} on {target_type}/{target_id}",
                  action=f"admin.{action}", admin_id=admin_id,
                  target_type=target_type, target_id=target_id, **(details or {}))
