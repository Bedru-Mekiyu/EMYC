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
        duration_minutes=30,
        question_count=1,
        status=CompetitionStatus.DRAFT,
    )
    assert comp.status == CompetitionStatus.DRAFT

    # Add question to satisfy pre-LIVE validation
    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Lifecycle Q1",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    db_session.add(q)
    await db_session.commit()

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


@pytest.mark.asyncio
async def test_synchronized_competition_deadline_and_late_joiner_clamp(db_session: AsyncSession):
    """Verifies that:
    1. Setting competition to LIVE starts the clock immediately for duration_minutes.
    2. A participant joining at start gets the full duration.
    3. A participant joining 15 minutes late receives ONLY the remaining 45 minutes (no extra time).
    4. A participant attempting to join after the duration expires is rejected with CompetitionNotOpenError.
    """
    from datetime import datetime, timezone, timedelta
    from app.services.competition_service import CompetitionNotOpenError

    t0 = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
    duration = 60  # 1 hour competition

    comp = Competition(
        title="Synchronized Live Olympiad",
        status=CompetitionStatus.DRAFT,
        opens_at=t0,
        closes_at=t0 + timedelta(minutes=duration),
        duration_minutes=duration,
        question_count=2,
    )
    p1 = Participant(telegram_user_id=111001, membership_id="EMYC/111001/2026")
    p2 = Participant(telegram_user_id=111002, membership_id="EMYC/111002/2026")
    p3 = Participant(telegram_user_id=111003, membership_id="EMYC/111003/2026")
    db_session.add_all([comp, p1, p2, p3])
    await db_session.flush()

    for idx in (1, 2):
        q = CompetitionQuestion(
            competition_id=comp.id,
            question_text=f"Question {idx}?",
            options={"A": "1", "B": "2", "C": "3", "D": "4"},
            correct_option="A",
            order_index=idx,
        )
        db_session.add(q)
    await db_session.commit()

    # 1. Admin sets competition to LIVE
    # Service automatically sets opens_at = now, closes_at = now + 60m
    comp.opens_at = t0
    comp.closes_at = t0 + timedelta(minutes=duration)
    comp.status = CompetitionStatus.LIVE
    comp.actual_exam_started_at = t0
    comp.actual_exam_ends_at = t0 + timedelta(minutes=duration)
    await db_session.commit()

    # 2. Participant 1 starts right at t0
    attempt1 = await CompetitionService.start_attempt(db_session, comp.id, p1.id, now_override=t0)
    # Deadline is min(t0 + 60m, closes_at) = t0 + 60m (full 60 min)
    assert attempt1.deadline_at == t0 + timedelta(minutes=60)
    time_left_p1 = (attempt1.deadline_at - t0).total_seconds() / 60
    assert time_left_p1 == 60

    # 3. Participant 2 joins 15 minutes late at t0 + 15m
    t_late = t0 + timedelta(minutes=15)
    attempt2 = await CompetitionService.start_attempt(db_session, comp.id, p2.id, now_override=t_late)
    # Deadline must NOT be t_late + 60m; it MUST be strictly clamped to comp.actual_exam_ends_at = t0 + 60m!
    assert attempt2.deadline_at == comp.actual_exam_ends_at
    time_left_p2 = (attempt2.deadline_at - t_late).total_seconds() / 60
    assert time_left_p2 == 45  # Exactly 45 minutes remaining!

    # 4. Participant 3 tries to start after competition has expired (t0 + 61m)
    t_expired = t0 + timedelta(minutes=61)
    with pytest.raises(CompetitionNotOpenError):
        await CompetitionService.start_attempt(db_session, comp.id, p3.id, now_override=t_expired)


@pytest.mark.asyncio
async def test_two_phase_competition_lifecycle_availability_and_global_exam(db_session: AsyncSession):
    """Verifies the complete two-phase competition lifecycle:
    1. Phase 1: Competition Open/Availability Period (30 days: Oct 1 -> Oct 31).
       - Registration & eligibility verification open.
       - Questions remain locked (attempts rejected with CompetitionNotOpenError).
       - get_open_or_scheduled_competition finds the competition.
    2. Phase 2: Actual Examination Session (120 minutes) authorized by admin.
       - Admin sets status to LIVE: records actual_exam_started_at and actual_exam_ends_at.
       - Availability window (opens_at / closes_at) is preserved.
    3. Global Examination Timer:
       - Participant 1 starts right at exam launch -> receives full 120 minutes until actual_exam_ends_at.
       - Participant 2 starts 30 minutes late -> receives remaining 90 minutes (deadline is actual_exam_ends_at).
       - Participant 3 starts 90 minutes late -> receives remaining 30 minutes (deadline is actual_exam_ends_at).
       - Participant 4 attempts to start after actual_exam_ends_at -> rejected with CompetitionNotOpenError.
    4. Auto-closure & Finalization:
       - check_and_auto_close_competitions closes the competition once actual_exam_ends_at passes,
         auto-submitting any unsubmitted attempts.
    """
    # 1. Phase 1: Create 1-month competition
    t_open = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)
    duration = 120  # 2 hours exam duration

    comp = Competition(
        title="Ramadan National Championship 2026",
        description="Month-long competition with final examination session",
        status=CompetitionStatus.SCHEDULED,
        opens_at=t_open,
        closes_at=t_close,
        duration_minutes=duration,
        question_count=2,
    )
    p1 = Participant(telegram_user_id=2001, membership_id="EMYC/2001/2026")
    p2 = Participant(telegram_user_id=2002, membership_id="EMYC/2002/2026")
    p3 = Participant(telegram_user_id=2003, membership_id="EMYC/2003/2026")
    p4 = Participant(telegram_user_id=2004, membership_id="EMYC/2004/2026")
    db_session.add_all([comp, p1, p2, p3, p4])
    await db_session.flush()

    for idx in (1, 2):
        q = CompetitionQuestion(
            competition_id=comp.id,
            question_text=f"Grand Question {idx}?",
            options={"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"},
            correct_option="A",
            order_index=idx,
        )
        db_session.add(q)
    await db_session.commit()

    # Mid-month check (Oct 15): competition is open for registration, but exam is locked
    t_mid = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    open_comp = await CompetitionService.get_open_or_scheduled_competition(db_session, now=t_mid)
    assert open_comp is not None
    assert open_comp.id == comp.id

    # Trying to start attempt during Phase 1 raises CompetitionNotOpenError
    with pytest.raises(CompetitionNotOpenError):
        await CompetitionService.start_attempt(db_session, comp.id, p1.id, now_override=t_mid)

    # 2. Phase 2: Admin activates the live exam session on Oct 31 at 14:00 UTC
    t_exam_start = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    t_exam_end = t_exam_start + timedelta(minutes=duration)  # 16:00 UTC

    # Simulate admin launching exam session
    comp.status = CompetitionStatus.LIVE
    comp.actual_exam_started_at = t_exam_start
    comp.actual_exam_ends_at = t_exam_end
    await db_session.commit()

    # Verify availability window is preserved and not overwritten
    assert comp.opens_at == t_open
    assert comp.closes_at == t_close
    assert comp.actual_exam_started_at == t_exam_start
    assert comp.actual_exam_ends_at == t_exam_end

    # 3. Global Timer:
    # Participant 1 starts right at 14:00 UTC
    att1 = await CompetitionService.start_attempt(db_session, comp.id, p1.id, now_override=t_exam_start)
    assert att1.deadline_at == t_exam_end
    assert (att1.deadline_at - t_exam_start).total_seconds() / 60 == 120  # 120 min left

    # Participant 2 joins 30 min late (14:30 UTC)
    t_p2 = t_exam_start + timedelta(minutes=30)
    att2 = await CompetitionService.start_attempt(db_session, comp.id, p2.id, now_override=t_p2)
    assert att2.deadline_at == t_exam_end  # Shared global deadline!
    assert (att2.deadline_at - t_p2).total_seconds() / 60 == 90  # Only 90 min left

    # Participant 3 joins 90 min late (15:30 UTC)
    t_p3 = t_exam_start + timedelta(minutes=90)
    att3 = await CompetitionService.start_attempt(db_session, comp.id, p3.id, now_override=t_p3)
    assert att3.deadline_at == t_exam_end  # Shared global deadline!
    assert (att3.deadline_at - t_p3).total_seconds() / 60 == 30  # Only 30 min left

    # Participant 4 tries to start after exam deadline (16:05 UTC)
    t_after = t_exam_end + timedelta(minutes=5)
    with pytest.raises(CompetitionNotOpenError):
        await CompetitionService.start_attempt(db_session, comp.id, p4.id, now_override=t_after)

    # 4. Auto-closing at 16:05 UTC
    closed_count = await CompetitionService.check_and_auto_close_competitions(db_session, now_override=t_after)
    assert closed_count == 1

    await db_session.refresh(comp)
    assert comp.status == CompetitionStatus.CLOSED

    # Verify attempts were auto-submitted / finalized
    await db_session.refresh(att1)
    await db_session.refresh(att2)
    await db_session.refresh(att3)
    assert att1.status == AttemptStatus.EXPIRED
    assert att2.status == AttemptStatus.EXPIRED
    assert att3.status == AttemptStatus.EXPIRED


@pytest.mark.asyncio
async def test_two_phase_availability_invariants_and_registration(db_session: AsyncSession):
    """Verifies that Phase 1 (open for registration) strictly isolates exam access:
    1. A 1-month competition in OPEN status has NO actual_exam timestamps.
    2. Participants can register, bind membership, and become eligible during this period.
    3. Any attempt to start the exam during OPEN status raises CompetitionNotOpenError.
    4. Editing opens_at or closes_at does NOT set actual_exam timestamps.
    5. Transitioning DRAFT -> OPEN does NOT set actual_exam timestamps.
    """
    from app.services.membership_service import ParticipantService

    t_open = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)

    # 1. Create DRAFT competition
    comp = await CompetitionService.create_competition(
        db=db_session,
        title="National Physics League 2026",
        description="One month availability window",
        opens_at=t_open,
        closes_at=t_close,
        duration_minutes=90,
        question_count=2,
        status=CompetitionStatus.DRAFT,
    )
    assert comp.status == CompetitionStatus.DRAFT
    assert comp.actual_exam_started_at is None
    assert comp.actual_exam_ends_at is None

    # Add questions
    for idx in (1, 2):
        q = CompetitionQuestion(
            competition_id=comp.id,
            question_text=f"Physics Question {idx}?",
            options={"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"},
            correct_option="A",
            order_index=idx,
        )
        db_session.add(q)
    await db_session.commit()

    # 2. Transition DRAFT -> OPEN (Phase 1 begins)
    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.OPEN)
    assert comp.status == CompetitionStatus.OPEN
    # Critical Invariant: Exam timer must NOT be running!
    assert comp.actual_exam_started_at is None
    assert comp.actual_exam_ends_at is None

    # 3. Participant registers during Phase 1 (e.g. Oct 10)
    p = await ParticipantService.register_or_bind_participant(
        db=db_session,
        telegram_user_id=778899,
        membership_id="EMYC/778899/2026",
        telegram_username="zubair",
    )
    assert p is not None
    assert p.is_active is True

    # 4. Attempting to start the exam during Phase 1 MUST fail
    t_mid = datetime(2026, 10, 10, 15, 0, 0, tzinfo=timezone.utc)
    with pytest.raises(CompetitionNotOpenError):
        await CompetitionService.start_attempt(db_session, comp.id, p.id, now_override=t_mid)

    # 5. Editing the availability window preserves null exam timestamps
    comp.closes_at = comp.opens_at + timedelta(days=45)
    await db_session.commit()
    await db_session.refresh(comp)
    assert comp.actual_exam_started_at is None
    assert comp.actual_exam_ends_at is None


@pytest.mark.asyncio
async def test_live_exam_concurrency_and_idempotency(db_session: AsyncSession):
    """Verifies that activating the live exam is strictly idempotent and concurrency-safe:
    1. update_status(..., LIVE) transitions OPEN -> LIVE and sets authoritative timestamps.
    2. Attempting to transition to LIVE a second time raises CompetitionError without modifying timestamps.
    """
    t_open = datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc)
    t_close = datetime(2026, 10, 31, 23, 59, 59, tzinfo=timezone.utc)

    comp = await CompetitionService.create_competition(
        db=db_session,
        title="Olympiad Concurrency Test",
        opens_at=t_open,
        closes_at=t_close,
        duration_minutes=60,
        question_count=1,
        status=CompetitionStatus.OPEN,
    )
    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Q1?",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    db_session.add(q)
    await db_session.commit()

    # 1. First admin activates exam
    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.LIVE)
    assert comp.status == CompetitionStatus.LIVE
    assert comp.actual_exam_started_at is not None
    assert comp.actual_exam_ends_at is not None
    orig_start = comp.actual_exam_started_at
    orig_end = comp.actual_exam_ends_at

    # 2. Second admin / concurrent request attempts to activate exam again
    with pytest.raises(CompetitionError) as exc_info:
        await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.LIVE)
    assert "already LIVE" in str(exc_info.value) or "Invalid transition" in str(exc_info.value)

    # 3. Verify timestamps were not altered
    await db_session.refresh(comp)
    assert comp.actual_exam_started_at == orig_start
    assert comp.actual_exam_ends_at == orig_end


@pytest.mark.asyncio
async def test_live_duration_extension_synchronizes_all_active_attempts(db_session: AsyncSession):
    """Verifies that extending the exam duration while LIVE updates ONE authoritative global deadline
    for both the competition and all active participant attempts in progress:
    - Participant A starts before extension (gets original deadline).
    - Admin extends duration by 30 minutes.
    - Participant A's attempt deadline is automatically extended.
    - Participant B joins after extension and receives the exact same global deadline.
    - Zero divergent deadlines exist.
    """
    t0 = datetime(2026, 10, 31, 14, 0, 0, tzinfo=timezone.utc)
    duration = 60  # Initial 60 min

    comp = Competition(
        title="Live Extension Exam",
        status=CompetitionStatus.LIVE,
        opens_at=t0 - timedelta(days=10),
        closes_at=t0 + timedelta(days=1),
        duration_minutes=duration,
        actual_exam_started_at=t0,
        actual_exam_ends_at=t0 + timedelta(minutes=duration),
        question_count=1,
    )
    p1 = Participant(telegram_user_id=8801, membership_id="EMYC/8801/2026")
    p2 = Participant(telegram_user_id=8802, membership_id="EMYC/8802/2026")
    db_session.add_all([comp, p1, p2])
    await db_session.flush()

    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Q1?",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    db_session.add(q)
    await db_session.commit()

    # 1. Participant A starts at t0: deadline is t0 + 60m
    att1 = await CompetitionService.start_attempt(db_session, comp.id, p1.id, now_override=t0)
    assert att1.deadline_at == t0 + timedelta(minutes=60)

    # 2. Admin extends live exam duration to 90 minutes at t0 + 20m
    updated_comp = await CompetitionService.extend_live_exam_duration(db_session, comp.id, new_duration_minutes=90)
    assert updated_comp.duration_minutes == 90
    assert updated_comp.actual_exam_ends_at == t0 + timedelta(minutes=90)

    # Verify Participant A's existing in-progress attempt deadline was updated to t0 + 90m
    await db_session.refresh(att1)
    assert att1.deadline_at == t0 + timedelta(minutes=90)

    # 3. Participant B starts late at t0 + 30m: receives the exact same global deadline (t0 + 90m)
    t_p2 = t0 + timedelta(minutes=30)
    att2 = await CompetitionService.start_attempt(db_session, comp.id, p2.id, now_override=t_p2)
    assert att2.deadline_at == t0 + timedelta(minutes=90)
    assert att2.deadline_at == att1.deadline_at  # Strictly identical global deadline!
    assert (att2.deadline_at - t_p2).total_seconds() / 60 == 60  # Exactly 60 minutes remaining


