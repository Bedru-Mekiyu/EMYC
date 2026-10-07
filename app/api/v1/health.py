from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.core.database import get_db
from app.core.config import get_settings

router = APIRouter()
settings = get_settings()


@router.get("")
async def health_check():
    """Basic liveness probe."""
    return {
        "status": "healthy",
        "project": settings.PROJECT_NAME,
        "environment": settings.ENVIRONMENT,
    }


@router.get("/ready")
async def readiness_check(db: AsyncSession = Depends(get_db)):
    """Readiness probe checking database connectivity."""
    db_status = "unhealthy"
    try:
        result = await db.execute(text("SELECT 1"))
        if result.scalar() == 1:
            db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return {
        "status": "ready" if db_status == "healthy" else "degraded",
        "database": db_status,
        "bot_mode": settings.BOT_MODE,
    }


@router.get("/bot-status")
async def bot_status_check(db: AsyncSession = Depends(get_db)):
    """Operational inspection endpoint revealing bot mode, webhook status, active competitions, and participant counts."""
    from sqlalchemy import select, func
    from app.models.competition import Competition
    from app.models.participant import Participant
    from app.models.attempt import ExamAttempt

    # Active or latest competition
    stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
    comp = (await db.execute(stmt)).scalar_one_or_none()

    comp_info = None
    if comp:
        comp_info = {
            "id": str(comp.id),
            "title": comp.title,
            "status": str(comp.status.value if hasattr(comp.status, 'value') else comp.status),
            "question_count": comp.question_count,
            "opens_at": comp.opens_at.isoformat() if comp.opens_at else None,
            "closes_at": comp.closes_at.isoformat() if comp.closes_at else None,
        }

    from app.services.membership_service import ParticipantService
    total_p = await ParticipantService.get_registered_participants_count(db, exclude_admins=True)
    total_att = (await db.execute(select(func.count(ExamAttempt.id)))).scalar() or 0

    webhook_info_dict = None
    if settings.BOT_MODE == "webhook":
        try:
            from app.api.v1.telegram_webhook import get_telegram_application
            t_app = get_telegram_application()
            info = await t_app.bot.get_webhook_info()
            webhook_info_dict = {
                "url": info.url,
                "has_custom_certificate": info.has_custom_certificate,
                "pending_update_count": info.pending_update_count,
                "last_error_date": info.last_error_date.isoformat() if info.last_error_date else None,
                "last_error_message": info.last_error_message,
                "ip_address": info.ip_address,
            }
        except Exception as e:
            webhook_info_dict = {"error": str(e)}

    return {
        "bot_mode": settings.BOT_MODE,
        "environment": settings.ENVIRONMENT,
        "webhook_url_configured": bool(settings.WEBHOOK_URL),
        "webhook_secret_configured": bool(settings.WEBHOOK_SECRET),
        "admin_count": len(settings.admin_ids),
        "active_competition": comp_info,
        "total_registered_participants": total_p,
        "total_exam_attempts": total_att,
        "telegram_webhook_info": webhook_info_dict,
    }


@router.get("/recent-errors")
async def recent_errors_check():
    """Returns in-memory capture of recent server and bot errors for instant diagnostics."""
    from app.core.logging import RECENT_ERRORS
    return {
        "count": len(RECENT_ERRORS),
        "errors": list(RECENT_ERRORS),
    }

