"""Telegram Mini App API endpoints for high-throughput examination."""

import hmac
import hashlib
import time
import uuid
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings
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
from app.models.participant import Participant

router = APIRouter()


def create_web_auth_token(user_id: int, username: str = "", display_name: str = "") -> str:
    """Signs a secure web session token for browser-based access outside Telegram."""
    settings = get_settings()
    expiry = int(time.time()) + 86400 * 7  # 7-day session validity
    safe_uname = username.replace(":", "_") if username else "user"
    safe_dname = display_name.replace(":", "_") if display_name else "Candidate"
    payload = f"{user_id}:{expiry}:{safe_uname}:{safe_dname}"
    secret = (settings.TELEGRAM_BOT_TOKEN or "default_secret").encode()
    sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
    return f"web:{payload}:{sig}"


def verify_web_auth_token(token: str) -> Optional[Dict[str, Any]]:
    """Validates signature and expiry of a web session token."""
    if not token or not token.startswith("web:"):
        return None
    try:
        parts = token.split(":")
        if len(parts) != 6:
            return None
        _, uid_str, exp_str, uname, dname, sig = parts
        payload = f"{uid_str}:{exp_str}:{uname}:{dname}"
        settings = get_settings()
        secret = (settings.TELEGRAM_BOT_TOKEN or "default_secret").encode()
        expected_sig = hmac.new(secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
        if int(exp_str) < time.time():
            return None
        return {
            "id": int(uid_str),
            "username": uname,
            "first_name": dname or uname,
        }
    except Exception:
        return None


class AnswerItem(BaseModel):
    question_id: Optional[str] = None
    display_order: Optional[int] = None
    selected_option: str = Field(..., max_length=5)


class BatchSubmitRequest(BaseModel):
    attempt_id: str
    answers: List[AnswerItem]


class WebLoginRequest(BaseModel):
    telegram_user_id: Optional[int] = None
    membership_id: Optional[str] = None
    admin_test: Optional[bool] = False


async def get_current_telegram_user(
    x_telegram_init_data: Optional[str] = Header(None, alias="X-Telegram-Init-Data"),
    x_web_auth_token: Optional[str] = Header(None, alias="X-Web-Auth-Token"),
) -> Dict[str, Any]:
    """Dependency that authenticates the examination request.
    
    Supports:
    1. Direct Web Session Token (for browsers without Telegram client).
    2. Native Telegram Mini App HMAC-SHA256 signature (X-Telegram-Init-Data).
    """
    # 1. Try Web Session Token
    token = x_web_auth_token or (x_telegram_init_data if x_telegram_init_data and x_telegram_init_data.startswith("web:") else None)
    if token:
        user = verify_web_auth_token(token)
        if user:
            return user
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired web auth token",
        )

    # 2. Try Telegram Mini App initData
    if not x_telegram_init_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication credentials",
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


@router.post("/auth/web-login")
async def web_login(
    payload: WebLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Allows candidates and administrators to authenticate in a standalone browser
    without opening the Telegram application.
    """
    settings = get_settings()
    target_user_id = payload.telegram_user_id
    participant = None

    if (target_user_id and settings.is_admin(target_user_id)) or payload.admin_test:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is authorized for administration and cannot participate in this competition.",
        )

    if target_user_id:
        part_stmt = select(Participant).where(Participant.telegram_user_id == target_user_id)
        participant = (await db.execute(part_stmt)).scalar_one_or_none()
        if not participant:
            raise HTTPException(status_code=404, detail="No registered participant found with this Telegram ID. Please register first.")

    elif payload.membership_id:
        clean_mid = payload.membership_id.strip()
        part_stmt = select(Participant).where(Participant.membership_id == clean_mid)
        participant = (await db.execute(part_stmt)).scalar_one_or_none()
        if not participant:
            raise HTTPException(status_code=404, detail="No registered participant found with this Membership ID.")
        target_user_id = participant.telegram_user_id
    else:
        raise HTTPException(status_code=400, detail="Please provide a Telegram User ID or Membership ID.")

    token = create_web_auth_token(
        user_id=target_user_id,
        username=participant.telegram_username or "",
        display_name=participant.full_name or "",
    )
    return {
        "status": "success",
        "token": token,
        "user": {
            "id": target_user_id,
            "full_name": participant.full_name,
            "username": participant.telegram_username,
            "membership_id": participant.membership_id,
            "is_admin": settings.is_admin(target_user_id),
        },
    }


@router.get("/session")
async def get_exam_session(
    comp_id: Optional[str] = Query(None),
    user: Dict[str, Any] = Depends(get_current_telegram_user),
    db: AsyncSession = Depends(get_db),
):
    """Retrieves or starts the examination session for the authenticated student.
    
    Returns all questions and countdown state in a single low-latency payload.
    """
    tg_user_id = user.get("id")
    if not tg_user_id:
        raise HTTPException(status_code=400, detail="Invalid user object in session credentials")

    try:
        session_data = await WebAppExamService.get_or_create_session(db, tg_user_id, comp_id=comp_id)
        return session_data
    except CompetitionNotOpenError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except CompetitionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
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
        raise HTTPException(status_code=400, detail="Invalid user object in session credentials")

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
