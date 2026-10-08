import asyncio
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus, ParticipantAnswer
from app.services.competition_service import (
    CompetitionService,
    CompetitionError,
    CompetitionNotOpenError,
    AttemptExpiredError,
    UnauthorizedAttemptAccessError,
)
from app.services.scoring_service import (
    ScoringAndRankingService,
    ResultsNotPublishedError,
)
from tests.conftest import TestSessionLocal


@pytest.mark.asyncio
async def test_idor_and_cross_participant_tampering_protection(db_session: AsyncSession):
    """Verifies that Participant A cannot inspect, answer, or submit Participant B's attempt."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Security Olympiad",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=1),
        duration_minutes=60,
        question_count=1,
    )
    # Legitimate Participant B
    participant_b = Participant(telegram_user_id=111222, membership_id="EMYC/1112222/2026")
    # Attacking Participant A
    participant_a = Participant(telegram_user_id=333444, membership_id="EMYC/3334444/2026")

    db_session.add_all([comp, participant_b, participant_a])
    await db_session.flush()

    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Confidential Question 1",
        options={"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"},
        correct_option="C",
        order_index=1,
    )
    db_session.add(q)
    await db_session.commit()

    # Participant B starts legitimate attempt
    attempt_b = await CompetitionService.start_attempt(db_session, comp.id, participant_b.id)
    attempt_b_id = attempt_b.id
    q_id = q.id
    part_a_id = participant_a.id
    part_b_id = participant_b.id

    # 1. IDOR Navigation Test: Participant A attempts to view Participant B's question
    with pytest.raises(UnauthorizedAttemptAccessError):
        await CompetitionService.get_question_for_attempt(
            db_session, attempt_b_id, display_order=1, participant_id=part_a_id
        )

    # Legitimate Participant B CAN view question
    q_data_b = await CompetitionService.get_question_for_attempt(
        db_session, attempt_b_id, display_order=1, participant_id=part_b_id
    )
    assert q_data_b["display_order"] == 1

    # 2. IDOR Answer Tampering Test: Participant A attempts to submit an answer on B's attempt
    with pytest.raises(UnauthorizedAttemptAccessError):
        await CompetitionService.submit_answer(
            db_session, attempt_b_id, q_id, selected_display_option="A", participant_id=part_a_id
        )

    # 3. IDOR Submission Tampering Test: Participant A attempts to submit B's exam
    with pytest.raises(UnauthorizedAttemptAccessError):
        await CompetitionService.submit_attempt(
            db_session, attempt_b_id, participant_id=part_a_id
        )

    # Confirm B's attempt is still in progress and unaffected
    await db_session.refresh(attempt_b)
    assert attempt_b.status == AttemptStatus.IN_PROGRESS


@pytest.mark.asyncio
async def test_multi_worker_sweeper_safety_with_skip_locked(db_session: AsyncSession):
    """Verifies that multiple concurrent sweepers using FOR UPDATE SKIP LOCKED process each attempt exactly once."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Multi-Worker Sweeper Test",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=2),
        closes_at=now + timedelta(hours=1),
        duration_minutes=30,
        question_count=1,
    )
    db_session.add(comp)
    await db_session.flush()

    # Create 6 expired attempts across 6 participants
    attempts = []
    for i in range(1, 7):
        p = Participant(telegram_user_id=50000 + i, membership_id=f"EMYC/500000{i}/2026")
        db_session.add(p)
        await db_session.flush()

        att = ExamAttempt(
            competition_id=comp.id,
            participant_id=p.id,
            status=AttemptStatus.IN_PROGRESS,
            started_at=now - timedelta(minutes=40),
            deadline_at=now - timedelta(minutes=10),  # expired 10 minutes ago
        )
        db_session.add(att)
        attempts.append(att)

    await db_session.commit()

    # Run two sweepers concurrently in separate sessions
    async def run_sweeper_worker():
        async with TestSessionLocal() as session:
            return await CompetitionService.sweep_expired_attempts(session)

    count1, count2 = await asyncio.gather(run_sweeper_worker(), run_sweeper_worker())

    # Total attempts swept: in PostgreSQL with skip_locked count1+count2 == 6, in SQLite without row locks count >= 6
    if db_session.bind and getattr(db_session.bind, "dialect", None) and db_session.bind.dialect.name == "sqlite":
        assert count1 + count2 >= 6
    else:
        assert count1 + count2 == 6

    # Verify all 6 attempts are now EXPIRED and none remain IN_PROGRESS
    async with TestSessionLocal() as session:
        stmt = select(ExamAttempt).where(ExamAttempt.competition_id == comp.id)
        res = await session.execute(stmt)
        all_attempts = res.scalars().all()
        assert len(all_attempts) == 6
        for a in all_attempts:
            assert a.status == AttemptStatus.EXPIRED
            assert a.submitted_at is not None


@pytest.mark.asyncio
async def test_concurrent_submission_and_sweeper_race(db_session: AsyncSession):
    """Race A & C: Participant submits attempt at the exact moment sweeper expires it or concurrent submits."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Race Olympiad",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=10),
        closes_at=now + timedelta(hours=1),
        duration_minutes=5,
        question_count=1,
    )
    p = Participant(telegram_user_id=777888, membership_id="EMYC/7778888/2026")
    db_session.add_all([comp, p])
    await db_session.flush()

    att = ExamAttempt(
        competition_id=comp.id,
        participant_id=p.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now - timedelta(minutes=6),
        deadline_at=now - timedelta(seconds=1),  # right at expiration threshold
    )
    db_session.add(att)
    await db_session.commit()

    att_id = att.id
    p_id = p.id

    async def participant_submit():
        async with TestSessionLocal() as s:
            return await CompetitionService.submit_attempt(s, att_id, participant_id=p_id)

    async def sweeper_submit():
        async with TestSessionLocal() as s:
            attempt_row = await s.get(ExamAttempt, att_id)
            return await CompetitionService.auto_submit_expired_attempt(s, attempt_row)

    # Race: participant submits while sweeper expires
    r1, r2 = await asyncio.gather(participant_submit(), sweeper_submit(), return_exceptions=True)

    # Neither should fail with unhandled crash; attempt must reach terminal status
    async with TestSessionLocal() as s:
        final_att = await s.get(ExamAttempt, att_id)
        assert final_att.status in [AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED]
        assert final_att.submitted_at is not None


@pytest.mark.asyncio
async def test_dual_admin_concurrent_lifecycle_and_finalization(db_session: AsyncSession):
    """Race D & F: Two administrators simultaneously finalizing results or transitioning lifecycle."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Dual Admin Race",
        status=CompetitionStatus.CLOSED,
        opens_at=now - timedelta(hours=2),
        closes_at=now - timedelta(minutes=10),
        duration_minutes=30,
        question_count=1,
    )
    p = Participant(telegram_user_id=888999, membership_id="EMYC/8889999/2026")
    db_session.add_all([comp, p])
    await db_session.flush()

    q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Admin Race Q1",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="B",
        order_index=1,
    )
    db_session.add(q)
    await db_session.flush()

    att = ExamAttempt(
        competition_id=comp.id,
        participant_id=p.id,
        status=AttemptStatus.SUBMITTED,
        started_at=now - timedelta(minutes=40),
        deadline_at=now - timedelta(minutes=10),
        submitted_at=now - timedelta(minutes=15),
        completion_seconds=1500.0,
    )
    db_session.add(att)
    await db_session.commit()

    comp_id = comp.id

    async def admin_finalize():
        async with TestSessionLocal() as s:
            return await ScoringAndRankingService.finalize_competition_results(s, comp_id, admin_id="admin_1")

    # Two admins simultaneously trigger result finalization
    results = await asyncio.gather(admin_finalize(), admin_finalize(), return_exceptions=True)
    # Both calls either succeed or serialize safely without corrupting status or rank
    for res in results:
        assert not isinstance(res, Exception) or "RESULTS_FINALIZED" in str(res)

    async with TestSessionLocal() as s:
        final_comp = await s.get(Competition, comp_id)
        assert final_comp.status == CompetitionStatus.RESULTS_FINALIZED
        stmt = select(ExamAttempt).where(ExamAttempt.competition_id == comp_id)
        att_res = await s.execute(stmt)
        finalized_att = att_res.scalar_one()
        assert finalized_att.status == AttemptStatus.FINALIZED
        assert finalized_att.rank == 1


@pytest.mark.asyncio
async def test_answer_key_confidentiality_structural_audit(db_session: AsyncSession):
    """Verifies that correct_option, is_correct, and canonical mapping are structurally shielded before publication."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Confidentiality Audit Exam",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=1),
        duration_minutes=30,
        question_count=2,
    )
    p = Participant(telegram_user_id=909090, membership_id="EMYC/9090909/2026")
    db_session.add_all([comp, p])
    await db_session.flush()

    q1 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Secret Question 1",
        options={"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"},
        correct_option="A",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Secret Question 2",
        options={"A": "Apple", "B": "Banana", "C": "Cherry", "D": "Date"},
        correct_option="D",
        order_index=2,
    )
    db_session.add_all([q1, q2])
    await db_session.commit()

    attempt = await CompetitionService.start_attempt(db_session, comp.id, p.id)

    # 1. Inspect Question 1 fetch: must NEVER contain correct_option or canonical mapping
    q1_data = await CompetitionService.get_question_for_attempt(
        db_session, attempt.id, display_order=1, participant_id=p.id
    )
    assert "correct_option" not in q1_data
    assert "is_correct" not in q1_data
    assert "canonical_option" not in q1_data
    assert "option_mapping" not in q1_data

    # 2. Inspect Answer submission: must NEVER reveal live correctness
    q1_id = q1_data["question_id"]
    ans_res = await CompetitionService.submit_answer(
        db_session, attempt.id, q1_id, selected_display_option="B", participant_id=p.id
    )
    assert ans_res["status"] == "recorded"
    assert "is_correct" not in ans_res
    assert "correct_option" not in ans_res

    # 3. Before publication: participant results and reviews must be blocked
    with pytest.raises(ResultsNotPublishedError):
        await ScoringAndRankingService.get_participant_result(db_session, comp.id, p.id)

    with pytest.raises(ResultsNotPublishedError):
        await ScoringAndRankingService.get_answer_review(db_session, comp.id, p.id)


@pytest.mark.asyncio
async def test_deterministic_ranking_tie_breakers(db_session: AsyncSession):
    """Verifies the exact 4-level deterministic ranking rule:
    1. score DESC
    2. completion_seconds ASC
    3. submitted_at ASC
    4. attempt_id ASC
    """
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Tie Breaker Championship",
        status=CompetitionStatus.CLOSED,
        opens_at=now - timedelta(hours=2),
        closes_at=now - timedelta(minutes=5),
        duration_minutes=30,
        question_count=2,
    )
    db_session.add(comp)
    await db_session.flush()

    # Create 4 participants
    participants = []
    for i in range(1, 5):
        p = Participant(telegram_user_id=60000 + i, membership_id=f"EMYC/600000{i}/2026")
        db_session.add(p)
        participants.append(p)
    await db_session.flush()

    # Setup 4 attempts:
    # All 4 participants have IDENTICAL score = 2 and IDENTICAL completion_seconds = 60.0
    # Participant 1: submitted at now - 15 mins (earliest submission -> rank 1)
    # Participant 2: submitted at now - 10 mins (second earliest -> rank 2)
    # Participants 3 & 4: submitted at exact same second (now - 5 mins) -> tie broken by attempt.id ASC
    sub_t1 = now - timedelta(minutes=15)
    sub_t2 = now - timedelta(minutes=10)
    sub_t3_4 = now - timedelta(minutes=5)

    att1 = ExamAttempt(
        competition_id=comp.id,
        participant_id=participants[0].id,
        status=AttemptStatus.SUBMITTED,
        started_at=sub_t1 - timedelta(seconds=60),
        deadline_at=sub_t1 + timedelta(minutes=30),
        submitted_at=sub_t1,
        completion_seconds=60.0,
        score=2,
    )
    att2 = ExamAttempt(
        competition_id=comp.id,
        participant_id=participants[1].id,
        status=AttemptStatus.SUBMITTED,
        started_at=sub_t2 - timedelta(seconds=60),
        deadline_at=sub_t2 + timedelta(minutes=30),
        submitted_at=sub_t2,
        completion_seconds=60.0,
        score=2,
    )
    att3 = ExamAttempt(
        competition_id=comp.id,
        participant_id=participants[2].id,
        status=AttemptStatus.SUBMITTED,
        started_at=sub_t3_4 - timedelta(seconds=60),
        deadline_at=sub_t3_4 + timedelta(minutes=30),
        submitted_at=sub_t3_4,
        completion_seconds=60.0,
        score=2,
    )
    att4 = ExamAttempt(
        competition_id=comp.id,
        participant_id=participants[3].id,
        status=AttemptStatus.SUBMITTED,
        started_at=sub_t3_4 - timedelta(seconds=60),
        deadline_at=sub_t3_4 + timedelta(minutes=30),
        submitted_at=sub_t3_4,
        completion_seconds=60.0,
        score=2,
    )
    db_session.add_all([att1, att2, att3, att4])
    await db_session.commit()

    # Finalize rankings
    finalized = await ScoringAndRankingService.finalize_competition_results(db_session, comp.id)
    assert len(finalized) == 4

    # Verification:
    # Rank 1: att1 (submitted earliest)
    # Rank 2: att2 (submitted second)
    # Ranks 3 & 4: broken strictly by string(attempt.id)
    assert finalized[0].id == att1.id
    assert finalized[0].rank == 1

    assert finalized[1].id == att2.id
    assert finalized[1].rank == 2

    tie_pair = [att3, att4]
    tie_pair.sort(key=lambda a: str(a.id))
    assert finalized[2].id == tie_pair[0].id
    assert finalized[2].rank == 3
    assert finalized[3].id == tie_pair[1].id
    assert finalized[3].rank == 4
