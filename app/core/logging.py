import logging
import sys
from typing import Any, Dict


def setup_logging(debug: bool = False) -> logging.Logger:
    """Configures root and application loggers."""
    log_level = logging.DEBUG if debug else logging.INFO
    log_format = "%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"

    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    logger = logging.getLogger("emyc_exam")
    logger.setLevel(log_level)
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
