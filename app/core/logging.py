import logging
import sys
from typing import Any, Dict


import collections
from datetime import datetime, timezone

RECENT_ERRORS = collections.deque(maxlen=20)


class InMemoryErrorCaptureHandler(logging.Handler):
    """Ring buffer holding the most recent application errors for remote diagnostics."""
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.ERROR:
            try:
                entry = {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "logger": record.name,
                    "level": record.levelname,
                    "message": record.getMessage(),
                    "exc_info": self.format(record) if record.exc_info else None,
                }
                RECENT_ERRORS.append(entry)
            except Exception:
                pass


def setup_logging(debug: bool = False) -> logging.Logger:
    """Configures root and application loggers."""
    log_level = logging.DEBUG if debug else logging.INFO
    log_format = "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"

    memory_handler = InMemoryErrorCaptureHandler()
    memory_handler.setLevel(logging.ERROR)

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
            memory_handler,
        ],
    )

    logger = logging.getLogger("emyc_exam")
    logger.setLevel(log_level)
    if memory_handler not in logger.handlers:
        logger.addHandler(memory_handler)
    return logger


logger = logging.getLogger("emyc_exam")


def log_audit_event(action: str, actor_type: str, actor_id: str, payload: Dict[str, Any] | None = None) -> None:
    """Logs security and administrative audit events with structured context."""
    audit_logger = logging.getLogger("emyc_exam.audit")
    extra = {
        "action": action,
        "actor_type": actor_type,
        "actor_id": str(actor_id),
        "payload": payload or {},
    }
    audit_logger.info(f"AUDIT_EVENT: action={action} actor={actor_type}:{actor_id} payload={payload}", extra=extra)
