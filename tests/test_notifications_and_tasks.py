from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus
from app.models.announcement import Announcement
from app.services.notification_service import NotificationService
from app.services.competition_service import CompetitionService
from app.tasks.deadline_sweeper import sweep_expired_attempts_job


@pytest.mark.asyncio
async def test_send_result_notifications(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Physics Trophy 2026",
        status=CompetitionStatus.PUBLISHED,
        opens_at=now - timedelta(hours=2),
        closes_at=now - timedelta(minutes=10),
        duration_minutes=30,
        question_count=10,
    )
    p1 = Participant(telegram_user_id=111222, membership_id="EMYC/1112222/2026", language_code="en")
    p2 = Participant(telegram_user_id=333444, membership_id="EMYC/3334444/2026", language_code="am")
    db_session.add_all([comp, p1, p2])
    await db_session.flush()

    # Both participants attempted
    att1 = ExamAttempt(competition_id=comp.id, participant_id=p1.id, status=AttemptStatus.FINALIZED, started_at=now, deadline_at=now)
    att2 = ExamAttempt(competition_id=comp.id, participant_id=p2.id, status=AttemptStatus.FINALIZED, started_at=now, deadline_at=now)
    db_session.add_all([att1, att2])
    await db_session.commit()

    # Mock Telegram Bot App
    mock_bot_app = MagicMock()
    mock_bot_app.bot.send_message = AsyncMock(return_value=True)

    sent = await NotificationService.send_result_notifications(
        db=db_session,
        bot_app=mock_bot_app,
        competition=comp,
        admin_telegram_id=999999,
    )

    assert sent == 2
    assert mock_bot_app.bot.send_message.call_count == 2

    # Check Announcement record was created in database
    stmt = select(Announcement).where(Announcement.competition_id == comp.id)
    res = await db_session.execute(stmt)
    ann = res.scalar_one_or_none()
    assert ann is not None
    assert ann.sent_count == 2


@pytest.mark.asyncio
async def test_deadline_sweeper_task(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Sweeper Test Comp",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(hours=1),
        duration_minutes=30,
        question_count=5,
    )
    p1 = Participant(telegram_user_id=555666, membership_id="EMYC/5556666/2026")
    p2 = Participant(telegram_user_id=777888, membership_id="EMYC/7778888/2026")
    db_session.add_all([comp, p1, p2])
    await db_session.flush()

    # Attempt 1: Expired (deadline was 5 minutes ago)
    att_expired = ExamAttempt(
        competition_id=comp.id,
        participant_id=p1.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now - timedelta(minutes=35),
        deadline_at=now - timedelta(minutes=5),
    )
    # Attempt 2: Still active (deadline in 20 minutes)
    att_active = ExamAttempt(
        competition_id=comp.id,
        participant_id=p2.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now - timedelta(minutes=10),
        deadline_at=now + timedelta(minutes=20),
    )
    db_session.add_all([att_expired, att_active])
    await db_session.commit()

    # Run sweeper pass
    swept_count = await CompetitionService.sweep_expired_attempts(db_session)
    assert swept_count == 1

    await db_session.refresh(att_expired)
    await db_session.refresh(att_active)

    assert att_expired.status == AttemptStatus.EXPIRED
    assert att_expired.submitted_at is not None
    assert att_active.status == AttemptStatus.IN_PROGRESS
