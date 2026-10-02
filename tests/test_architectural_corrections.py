import asyncio
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus
from app.services.competition_service import (
    CompetitionService,
    CompetitionValidationError,
    CompetitionError,
    CompetitionNotOpenError,
    DuplicateAttemptError,
)
from app.tasks.deadline_sweeper import sweep_expired_attempts_job


@pytest.mark.asyncio
async def test_validation_before_live(db_session: AsyncSession):
    now = datetime.now(timezone.utc)

    # 1. Create a competition with question_count=2
    comp = Competition(
        title="Validation Test Championship",
        status=CompetitionStatus.DRAFT,
        opens_at=now,
        closes_at=now + timedelta(hours=2),
        duration_minutes=60,
        question_count=2,
    )
    db_session.add(comp)
    await db_session.commit()
    comp_id = comp.id

    # Case A: Try going LIVE with 0 questions in DB -> must fail validation!
    with pytest.raises(CompetitionValidationError) as exc_info:
        await CompetitionService.update_status(db_session, comp_id, CompetitionStatus.LIVE)
    assert "Question count mismatch" in str(exc_info.value)
    await db_session.rollback()

    # Case B: Add questions with duplicate order_index
    q1 = CompetitionQuestion(
        competition_id=comp_id,
        question_text="Q1 text",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp_id,
        question_text="Q2 text",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="B",
        order_index=1,  # Duplicate order_index!
    )
    db_session.add_all([q1, q2])
    await db_session.commit()
    q2_id = q2.id

    with pytest.raises(CompetitionValidationError) as exc_info:
        await CompetitionService.update_status(db_session, comp_id, CompetitionStatus.LIVE)
    assert "Duplicate question order_index" in str(exc_info.value)
    await db_session.rollback()

    # Case C: Invalid option key / count
    q2 = await db_session.get(CompetitionQuestion, q2_id)
    q2.order_index = 2
    q2.options = {"A": "1", "B": "2", "C": "3"}  # Missing D!
    await db_session.commit()

    with pytest.raises(CompetitionValidationError) as exc_info:
        await CompetitionService.update_status(db_session, comp_id, CompetitionStatus.LIVE)
    assert "must have exactly options A, B, C, D" in str(exc_info.value)
    await db_session.rollback()

    # Case D: Invalid correct option
    q2 = await db_session.get(CompetitionQuestion, q2_id)
    q2.options = {"A": "1", "B": "2", "C": "3", "D": "4"}
    q2.correct_option = "E"  # Invalid!
    await db_session.commit()

    with pytest.raises(CompetitionValidationError) as exc_info:
        await CompetitionService.update_status(db_session, comp_id, CompetitionStatus.LIVE)
    assert "correct_option 'E' is invalid" in str(exc_info.value)
    await db_session.rollback()

    # Case E: Valid configuration -> successfully transitions to LIVE
    q2 = await db_session.get(CompetitionQuestion, q2_id)
    q2.correct_option = "D"
    await db_session.commit()

    live_comp = await CompetitionService.update_status(db_session, comp_id, CompetitionStatus.LIVE)
    assert live_comp.status == CompetitionStatus.LIVE


@pytest.mark.asyncio
async def test_automatic_competition_closure(db_session: AsyncSession):
    now = datetime.now(timezone.utc)

    # Competition whose closes_at has just passed
    comp = Competition(
        title="Auto-Close Comp",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=2),
        closes_at=now - timedelta(seconds=1),  # already in the past!
        duration_minutes=30,
        question_count=1,
    )
    p = Participant(telegram_user_id=121212, membership_id="EMYC/1212121/2026")
    db_session.add_all([comp, p])
    await db_session.flush()

    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Q?",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    # Active attempt
    att = ExamAttempt(
        competition_id=comp.id,
        participant_id=p.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now - timedelta(minutes=10),
        deadline_at=now + timedelta(minutes=20),
    )
    db_session.add_all([q, att])
    await db_session.commit()

    # Trigger sweeper pass
    closed_count = await CompetitionService.check_and_auto_close_competitions(db_session)
    assert closed_count == 1

    await db_session.refresh(comp)
    await db_session.refresh(att)

    assert comp.status == CompetitionStatus.CLOSED
    assert att.status == AttemptStatus.EXPIRED
    assert att.submitted_at is not None

    # New attempts must be rejected
    with pytest.raises(CompetitionNotOpenError):
        await CompetitionService.start_attempt(db_session, comp.id, p.id)


@pytest.mark.asyncio
async def test_manual_early_closure_policies(db_session: AsyncSession):
    now = datetime.now(timezone.utc)

    # Competition open with 2 hours remaining
    comp = Competition(
        title="Early Closure Comp",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=10),
        closes_at=now + timedelta(hours=2),
        duration_minutes=30,
        question_count=1,
    )
    p1 = Participant(telegram_user_id=313131, membership_id="EMYC/3131313/2026")
    p2 = Participant(telegram_user_id=414141, membership_id="EMYC/4141414/2026")
    db_session.add_all([comp, p1, p2])
    await db_session.flush()

    att1 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p1.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now - timedelta(minutes=5),
        deadline_at=now + timedelta(minutes=25),
    )
    db_session.add(att1)
    await db_session.commit()

    # Policy 1: truncate_to_close_time
    closed_comp = await CompetitionService.update_status(
        db_session,
        comp.id,
        CompetitionStatus.CLOSED,
        admin_id="superadmin",
        early_closure_policy="truncate_to_close_time",
    )
    assert closed_comp.status == CompetitionStatus.CLOSED

    await db_session.refresh(att1)
    assert att1.status == AttemptStatus.EXPIRED
    assert att1.deadline_at <= now + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_postgresql_concurrency_race_conditions(db_session: AsyncSession):
    """Simulates concurrent operations running against PostgreSQL 17."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Concurrent Olympiad",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=1),
        duration_minutes=30,
        question_count=1,
    )
    p = Participant(telegram_user_id=898989, membership_id="EMYC/8989898/2026")
    db_session.add_all([comp, p])
    await db_session.flush()

    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Concurrency Test Q",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    db_session.add(q)
    await db_session.commit()

    # 1. Start attempt
    attempt = await CompetitionService.start_attempt(db_session, comp.id, p.id)
    attempt_id = attempt.id
    q_id = q.id
    comp_id = comp.id
    await db_session.commit()

    # 2. Race: Two rapid concurrent answer submissions on same question using separate sessions
    async def submit_concurrently():
        from tests.conftest import TestSessionLocal
        async with TestSessionLocal() as s:
            return await CompetitionService.submit_answer(s, attempt_id, q_id, "A")

    res1, res2 = await asyncio.gather(submit_concurrently(), submit_concurrently())

    statuses = {res1["status"], res2["status"]}
    # One was recorded, the other was identified as already_recorded via PostgreSQL unique constraint
    assert "recorded" in statuses
    assert "already_recorded" in statuses

    # 3. Race: Two rapid concurrent attempt starts for same participant
    p_concurrent = Participant(telegram_user_id=767676, membership_id="EMYC/7676767/2026")
    db_session.add(p_concurrent)
    await db_session.commit()
    p_conc_id = p_concurrent.id

    async def start_concurrently():
        from tests.conftest import TestSessionLocal
        async with TestSessionLocal() as s:
            return await CompetitionService.start_attempt(s, comp_id, p_conc_id)

    results = await asyncio.gather(start_concurrently(), start_concurrently(), return_exceptions=True)
    successes = [r for r in results if not isinstance(r, Exception)]
    failures = [r for r in results if isinstance(r, Exception)]

    # Exactly 1 succeeds, 1 fails with DuplicateAttemptError due to PostgreSQL unique constraint
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], DuplicateAttemptError)
