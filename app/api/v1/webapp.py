"""Telegram Mini App API endpoints for high-throughput examination."""

import uuid
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.telegram_auth import parse_and_validate_init_data, TelegramAuthError
from app.services.webapp_exam_service import WebAppExamService
from app.services.competition_service import (
    CompetitionError,
    CompetitionNotOpenError,
    DuplicateAttemptError,
    AttemptExpiredError,
    UnauthorizedAttemptAccessError,
)

router = APIRouter()


class AnswerItem(BaseModel):
    question_id: Optional[str] = None
    display_order: Optional[int] = None
    selected_option: str = Field(..., max_length=5)


class BatchSubmitRequest(BaseModel):
    attempt_id: str
    answers: List[AnswerItem]


async def get_current_telegram_user(
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
) -> Dict[str, Any]:
    """Dependency that authenticates the Telegram Mini App request using initData HMAC-SHA256."""
    if not x_telegram_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Telegram-Init-Data header",
        )
    try:
        auth_payload = parse_and_validate_init_data(x_telegram_init_data)
        return auth_payload["user"]
    except TelegramAuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Authentication failed: {str(e)}",
        )


@router.get("/health")
async def webapp_health():
    """Lightweight health probe for Mini App endpoints."""
    return {"status": "ok", "service": "emyc-webapp-api"}


@router.get("/session")
async def get_exam_session(
    user: Dict[str, Any] = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves or starts the examination session for the authenticated student.
    
    Returns all questions and countdown state in a single low-latency payload.
    """
    tg_user_id = user.get("id")
    if not tg_user_id:
        raise HTTPException(status_code=400, detail="Invalid user object in initData")

    try:
        session_data = await WebAppExamService.get_or_create_session(db, tg_user_id)
        return session_data
    except CompetitionNotOpenError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while loading the exam session.")


@router.post("/submit")
async def submit_exam_batch(
    payload: BatchSubmitRequest,
    user: Dict[str, Any] = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    """Atomically records all exam answers, computes scores, and finalizes the attempt."""
    tg_user_id = user.get("id")
    if not tg_user_id:
        raise HTTPException(status_code=400, detail="Invalid user object in initData")

    try:
        attempt_uuid = uuid.UUID(payload.attempt_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid attempt_id format")

    try:
        answers_dict_list = [a.model_dump() for a in payload.answers]
        result = await WebAppExamService.submit_batch_answers(
            db=db,
            telegram_user_id=tg_user_id,
            attempt_id=attempt_uuid,
            answers=answers_dict_list,
        )
        return result
    except UnauthorizedAttemptAccessError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except AttemptExpiredError as e:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail=str(e))
    except CompetitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="An error occurred while submitting your exam.")
