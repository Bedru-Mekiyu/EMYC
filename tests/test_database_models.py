import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import (
    ExamAttempt,
    AttemptStatus,
    AttemptQuestionOrder,
    ParticipantAnswer,
)


@pytest.mark.asyncio
async def test_create_competition_and_questions(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Test National Championship",
        description="Exam testing",
        status=CompetitionStatus.LIVE,
        opens_at=now,
        closes_at=now + timedelta(hours=2),
        duration_minutes=60,
        question_count=2,
    )
    db_session.add(comp)
    await db_session.flush()

    q1 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="What is 2+2?",
        options={"A": "3", "B": "4", "C": "5", "D": "6"},
        correct_option="B",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Capital of France?",
        options={"A": "London", "B": "Rome", "C": "Paris", "D": "Berlin"},
        correct_option="C",
        order_index=2,
    )
    db_session.add_all([q1, q2])
    await db_session.commit()

    # Query back
    stmt = select(Competition).where(Competition.id == comp.id)
    result = await db_session.execute(stmt)
    saved_comp = result.scalar_one()
    assert saved_comp.title == "Test National Championship"
    assert saved_comp.status == CompetitionStatus.LIVE


@pytest.mark.asyncio
async def test_participant_unique_constraints(db_session: AsyncSession):
    # Register participant 1
    p1 = Participant(
        telegram_user_id=111111,
        telegram_username="user1",
        membership_id="EMYC/1000001/2026",
        language_code="en",
    )
    db_session.add(p1)
    await db_session.commit()

    # Test 1: Duplicate Telegram User ID must fail
    p_dup_tg = Participant(
        telegram_user_id=111111,
        telegram_username="user1_alt",
        membership_id="EMYC/1000002/2026",
        language_code="en",
    )
    db_session.add(p_dup_tg)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Test 2: Duplicate Membership ID must fail (same membership bound to second telegram user)
    p_dup_mem = Participant(
        telegram_user_id=222222,
        telegram_username="user2",
        membership_id="EMYC/1000001/2026",
        language_code="en",
    )
    db_session.add(p_dup_mem)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_attempt_and_answer_unique_constraints(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Timed Competition",
        status=CompetitionStatus.LIVE,
        opens_at=now,
        closes_at=now + timedelta(hours=3),
        duration_minutes=60,
        question_count=1,
    )
    db_session.add(comp)
    await db_session.flush()

    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Q1?",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    p = Participant(
        telegram_user_id=999999,
        membership_id="EMYC/9999999/2026",
    )
    db_session.add_all([q, p])
    await db_session.flush()

    # Create first attempt
    attempt1 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now,
        deadline_at=now + timedelta(minutes=60),
    )
    db_session.add(attempt1)
    await db_session.commit()
    attempt1_id = attempt1.id
    q_id = q.id

    # Attempt 2 for the same participant on the same competition must fail!
    attempt2 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now,
        deadline_at=now + timedelta(minutes=60),
    )
    db_session.add(attempt2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()

    # Answer constraints
    ans1 = ParticipantAnswer(
        attempt_id=attempt1_id,
        question_id=q_id,
        selected_display_option="A",
        resolved_canonical_option="A",
        is_correct=True,
    )
    db_session.add(ans1)
    await db_session.commit()

    # Duplicate answer on same attempt for same question must fail
    ans2 = ParticipantAnswer(
        attempt_id=attempt1_id,
        question_id=q_id,
        selected_display_option="B",
        resolved_canonical_option="B",
        is_correct=False,
    )
    db_session.add(ans2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()
