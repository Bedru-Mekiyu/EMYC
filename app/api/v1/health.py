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

    total_p = (await db.execute(select(func.count(Participant.id)))).scalar() or 0
    total_att = (await db.execute(select(func.count(ExamAttempt.id)))).scalar() or 0

    return {
        "bot_mode": settings.BOT_MODE,
        "environment": settings.ENVIRONMENT,
        "webhook_url_configured": bool(settings.WEBHOOK_URL),
        "webhook_secret_configured": bool(settings.WEBHOOK_SECRET),
        "admin_count": len(settings.admin_ids),
        "active_competition": comp_info,
        "total_registered_participants": total_p,
        "total_exam_attempts": total_att,
    }
