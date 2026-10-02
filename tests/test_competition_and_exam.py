import uuid
from datetime import datetime, timedelta, timezone
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time_utils import ensure_utc, now_utc
from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import AttemptStatus
from app.services.competition_service import (
    CompetitionService,
    CompetitionError,
    CompetitionNotOpenError,
    AttemptExpiredError,
    DuplicateAttemptError,
)


@pytest_asyncio.fixture
async def setup_competition_and_participant(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="National Physics Olympiad",
        description="Official Physics Exam",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=10),
        closes_at=now + timedelta(hours=2),
        duration_minutes=30,
        question_count=3,
    )
    db_session.add(comp)
    await db_session.flush()

    q1 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Speed of light in vacuum?",
        options={"A": "3x10^8 m/s", "B": "3x10^6 m/s", "C": "1.5x10^8 m/s", "D": "Infinite"},
        correct_option="A",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Unit of electrical resistance?",
        options={"A": "Volt", "B": "Ampere", "C": "Ohm", "D": "Watt"},
        correct_option="C",
        order_index=2,
    )
    q3 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Newton's First Law is also known as?",
        options={"A": "Law of Action-Reaction", "B": "Law of Inertia", "C": "Law of Gravity", "D": "Law of Energy"},
        correct_option="B",
        order_index=3,
    )
    p = Participant(
        telegram_user_id=1234567,
        telegram_username="student1",
        membership_id="EMYC/7770001/2026",
        language_code="en",
    )
    db_session.add_all([q1, q2, q3, p])
    await db_session.commit()
    return comp, p, [q1, q2, q3]


@pytest.mark.asyncio
async def test_competition_lifecycle_transitions(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    comp = await CompetitionService.create_competition(
        db_session,
        title="Lifecycle Comp",
        opens_at=now,
        closes_at=now + timedelta(hours=1),
        status=CompetitionStatus.DRAFT,
    )
    assert comp.status == CompetitionStatus.DRAFT

    # Valid transitions: DRAFT -> SCHEDULED -> LIVE -> CLOSED -> RESULTS_FINALIZED -> PUBLISHED
    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.SCHEDULED)
    assert comp.status == CompetitionStatus.SCHEDULED

    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.LIVE)
    assert comp.status == CompetitionStatus.LIVE

    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.CLOSED)
    assert comp.status == CompetitionStatus.CLOSED

    # Invalid jump: CLOSED directly to PUBLISHED must fail
    with pytest.raises(CompetitionError):
        await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.PUBLISHED)

    # Valid transition to RESULTS_FINALIZED, then PUBLISHED
    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.RESULTS_FINALIZED)
    assert comp.status == CompetitionStatus.RESULTS_FINALIZED

    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.PUBLISHED)
    assert comp.status == CompetitionStatus.PUBLISHED


@pytest.mark.asyncio
async def test_attempt_start_deadline_and_randomization(
    db_session: AsyncSession, setup_competition_and_participant
):
    comp, p, questions = setup_competition_and_participant

    # 1. Start attempt
    attempt = await CompetitionService.start_attempt(db_session, comp.id, p.id)
    assert attempt.status == AttemptStatus.IN_PROGRESS

    # 2. Check individual deadline
    expected_deadline = attempt.started_at + timedelta(minutes=comp.duration_minutes)
    assert abs((attempt.deadline_at - expected_deadline).total_seconds()) < 2

    # 3. Duplicate attempt must fail
    with pytest.raises(DuplicateAttemptError):
        await CompetitionService.start_attempt(db_session, comp.id, p.id)

    # 4. Fetch Question 1 - options must be present, NO canonical answer leaked
    q_data = await CompetitionService.get_question_for_attempt(db_session, attempt.id, display_order=1)
    assert q_data["total_questions"] == 3
    assert "options" in q_data
    assert set(q_data["options"].keys()) == {"A", "B", "C", "D"}
    assert "correct_option" not in q_data
    assert "canonical_option" not in q_data

    # 5. Submit an answer for Question 1
    q1_id = q_data["question_id"]
    ans_res = await CompetitionService.submit_answer(db_session, attempt.id, q1_id, selected_display_option="A")
    assert ans_res["status"] == "recorded"
    # Live correctness must NOT be exposed
    assert "is_correct" not in ans_res

    # 6. Duplicate callback for the same question must be idempotent
    dup_res = await CompetitionService.submit_answer(db_session, attempt.id, q1_id, selected_display_option="A")
    assert dup_res["status"] == "already_recorded"


@pytest.mark.asyncio
async def test_deadline_capped_by_competition_close(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    # Competition closes in 15 minutes, but normal exam duration is 60 minutes
    comp = Competition(
        title="Closing Soon Comp",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(minutes=15),
        duration_minutes=60,
        question_count=1,
    )
    p = Participant(
        telegram_user_id=888888,
        membership_id="EMYC/8888888/2026",
    )
    db_session.add_all([comp, p])
    await db_session.flush()

    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Q?",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    db_session.add(q)
    await db_session.commit()

    attempt = await CompetitionService.start_attempt(db_session, comp.id, p.id)
    # Deadline must be capped at comp.closes_at (15 min), NOT 60 min
    assert ensure_utc(attempt.deadline_at) <= ensure_utc(comp.closes_at)


@pytest.mark.asyncio
async def test_expired_attempt_auto_submission(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Expired Comp",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=2),
        closes_at=now + timedelta(hours=2),
        duration_minutes=10,
        question_count=1,
    )
    p = Participant(
        telegram_user_id=777777,
        membership_id="EMYC/7777777/2026",
    )
    db_session.add_all([comp, p])
    await db_session.flush()

    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Q?",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    db_session.add(q)
    await db_session.commit()

    attempt = await CompetitionService.start_attempt(db_session, comp.id, p.id)

    # Artificially shift deadline into past to test timeout
    attempt.deadline_at = now - timedelta(seconds=10)
    await db_session.commit()

    # Attempting to fetch question or submit answer must raise AttemptExpiredError and mark EXPIRED
    with pytest.raises(AttemptExpiredError):
        await CompetitionService.get_question_for_attempt(db_session, attempt.id, display_order=1)

    await db_session.refresh(attempt)
    assert attempt.status == AttemptStatus.EXPIRED
    assert attempt.submitted_at is not None
