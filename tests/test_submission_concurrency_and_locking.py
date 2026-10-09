"""Targeted concurrency, locking, and atomicity tests for the exam submission pipeline.

Verifies:
1. Same-attempt concurrent duplicate submission race conditions (idempotent winner, zero duplicate scores).
2. Same-competition multi-attempt concurrency (row-level isolation, zero competition lock contention).
3. Cross-competition multi-attempt concurrency (complete inter-competition isolation).
4. Idempotent retries after timeout/network failure (identical score, immutable submission timestamp).
5. Transaction rollback and partial-failure recovery (clean state preservation, subsequent submission succeeds).
"""

import asyncio
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.question import CompetitionQuestion
from app.models.attempt import ExamAttempt, AttemptStatus
from app.services.webapp_exam_service import WebAppExamService
from app.services.competition_service import CompetitionService, clear_competition_questions_cache
from app.core.time_utils import now_utc


async def seed_test_competition(
    db: AsyncSession,
    title: str = "Concurrency Test Exam",
    num_questions: int = 10,
) -> tuple[Competition, List[CompetitionQuestion]]:
    """Helper to seed a test competition with questions."""
    now = now_utc()
    comp_id = uuid.uuid4()
    comp = Competition(
        id=comp_id,
        title=title,
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(hours=2),
        duration_minutes=60,
        question_count=num_questions,
    )
    db.add(comp)

    questions = []
    for i in range(1, num_questions + 1):
        q = CompetitionQuestion(
            id=uuid.uuid4(),
            competition_id=comp_id,
            order_index=i,
            question_text=f"Question {i} text?",
            options={"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"},
            correct_option="A",
        )
        db.add(q)
        questions.append(q)

    await db.commit()
    await db.refresh(comp)
    clear_competition_questions_cache(comp_id)
    return comp, questions


async def seed_participant_and_attempt(
    db: AsyncSession,
    comp: Competition,
    questions: List[CompetitionQuestion],
    user_idx: int = 1,
) -> tuple[Participant, ExamAttempt]:
    """Helper to seed a participant and in-progress attempt."""
    now = now_utc()
    p = Participant(
        id=uuid.uuid4(),
        telegram_user_id=850000000 + user_idx,
        telegram_username=f"concurrent_user_{user_idx}",
        full_name=f"Concurrent Examinee {user_idx}",
        membership_id=f"CONC/{user_idx:05d}/2026",
    )
    db.add(p)

    q_sequence = [
        {
            "question_id": str(q.id),
            "display_order": q.order_index,
            "option_mapping": {"A": "A", "B": "B", "C": "C", "D": "D"},
        }
        for q in questions
    ]

    attempt = ExamAttempt(
        id=uuid.uuid4(),
        competition_id=comp.id,
        participant_id=p.id,
        status=AttemptStatus.IN_PROGRESS,
        started_at=now - timedelta(minutes=10),
        deadline_at=now + timedelta(minutes=50),
        question_sequence=q_sequence,
    )
    db.add(attempt)
    await db.commit()
    await db.refresh(p)
    await db.refresh(attempt)
    return p, attempt


@pytest.mark.asyncio
async def test_concurrent_duplicate_submissions_same_attempt(db_session: AsyncSession):
    """Test 1: Multiple concurrent submissions for the EXACT same attempt.
    
    Verifies:
    - Row-level lock (FOR UPDATE OF exam_attempts) serializes concurrent requests.
    - Exactly 1 request successfully marks status=SUBMITTED.
    - Remaining requests receive status=already_submitted.
    - All return the EXACT same score and zero data corruption occurs.
    """
    comp, questions = await seed_test_competition(db_session, num_questions=10)
    p, attempt = await seed_participant_and_attempt(db_session, comp, questions, user_idx=1)

    answers_payload = [
        {"question_id": str(q.id), "display_order": q.order_index, "selected_option": "A"}
        for q in questions
    ]

    from tests.conftest import TestSessionLocal

    async def submit_worker():
        async with TestSessionLocal() as session:
            return await WebAppExamService.submit_batch_answers(
                db=session,
                telegram_user_id=p.telegram_user_id,
                attempt_id=attempt.id,
                answers=answers_payload,
            )

    # Launch 10 simultaneous submission requests for the exact same attempt
    results = await asyncio.gather(*[submit_worker() for _ in range(10)])

    # Analyze results
    success_count = sum(1 for r in results if r.get("status") == "success")
    already_submitted_count = sum(1 for r in results if r.get("status") == "already_submitted")

    assert success_count == 1, f"Expected exactly 1 successful submission, got {success_count}"
    assert already_submitted_count == 9, f"Expected 9 already_submitted responses, got {already_submitted_count}"

    # Verify score consistency across all responses
    for r in results:
        assert r["score"] == 10
        assert r["attempt_id"] == str(attempt.id)

    # Verify database state
    async with TestSessionLocal() as session:
        refreshed = (await session.execute(
            select(ExamAttempt).where(ExamAttempt.id == attempt.id)
        )).scalar_one()
        assert refreshed.status == AttemptStatus.SUBMITTED
        assert refreshed.score == 10
        assert refreshed.correct_count == 10
        assert len(refreshed.answers_summary) == 10


@pytest.mark.asyncio
async def test_concurrent_submissions_same_competition_different_attempts(db_session: AsyncSession):
    """Test 2: Multiple concurrent submissions for DIFFERENT attempts in the SAME competition.
    
    Verifies:
    - with_for_update(of=ExamAttempt) locks ONLY the attempt row.
    - The shared competition row is NOT locked.
    - All 20 independent submissions succeed concurrently without deadlocks or blocking.
    """
    comp, questions = await seed_test_competition(db_session, num_questions=5)
    
    participants_attempts = []
    for i in range(20):
        pa = await seed_participant_and_attempt(db_session, comp, questions, user_idx=100 + i)
        participants_attempts.append(pa)

    from tests.conftest import TestSessionLocal

    async def submit_candidate(p: Participant, att: ExamAttempt, ans_opt: str):
        answers_payload = [
            {"question_id": str(q.id), "display_order": q.order_index, "selected_option": ans_opt}
            for q in questions
        ]
        async with TestSessionLocal() as session:
            return await WebAppExamService.submit_batch_answers(
                db=session,
                telegram_user_id=p.telegram_user_id,
                attempt_id=att.id,
                answers=answers_payload,
            )

    # Launch 20 simultaneous submissions across 20 distinct examinees
    tasks = [
        submit_candidate(p, att, "A" if i % 2 == 0 else "B")
        for i, (p, att) in enumerate(participants_attempts)
    ]
    results = await asyncio.gather(*tasks)

    # All 20 must succeed with zero deadlocks
    assert len(results) == 20
    for i, r in enumerate(results):
        assert r["status"] == "success"
        expected_score = 5 if i % 2 == 0 else 0
        assert r["score"] == expected_score


@pytest.mark.asyncio
async def test_concurrent_submissions_different_competitions(db_session: AsyncSession):
    """Test 3: Concurrent submissions across DIFFERENT competitions.
    
    Verifies:
    - Independent competitions do not interfere with each other.
    - Simultaneous submissions across separate competition scopes execute in parallel.
    """
    comp1, questions1 = await seed_test_competition(db_session, title="Competition Alpha", num_questions=5)
    comp2, questions2 = await seed_test_competition(db_session, title="Competition Beta", num_questions=5)

    pa1 = [await seed_participant_and_attempt(db_session, comp1, questions1, user_idx=200 + i) for i in range(5)]
    pa2 = [await seed_participant_and_attempt(db_session, comp2, questions2, user_idx=300 + i) for i in range(5)]

    from tests.conftest import TestSessionLocal

    async def submit_candidate(p: Participant, att: ExamAttempt, qs: List[CompetitionQuestion]):
        answers_payload = [
            {"question_id": str(q.id), "display_order": q.order_index, "selected_option": "A"}
            for q in qs
        ]
        async with TestSessionLocal() as session:
            return await WebAppExamService.submit_batch_answers(
                db=session,
                telegram_user_id=p.telegram_user_id,
                attempt_id=att.id,
                answers=answers_payload,
            )

    tasks = [submit_candidate(p, att, questions1) for p, att in pa1] + \
            [submit_candidate(p, att, questions2) for p, att in pa2]

    results = await asyncio.gather(*tasks)
    assert len(results) == 10
    for r in results:
        assert r["status"] == "success"
        assert r["score"] == 5


@pytest.mark.asyncio
async def test_idempotent_retry_after_submission(db_session: AsyncSession):
    """Test 4: Client retries after initial successful submission (network drop/timeout simulation).
    
    Verifies:
    - First submission returns status=success with computed score.
    - Subsequent retries return status=already_submitted with IDENTICAL score.
    - submitted_at timestamp is immutable across retries.
    """
    comp, questions = await seed_test_competition(db_session, num_questions=10)
    p, attempt = await seed_participant_and_attempt(db_session, comp, questions, user_idx=400)

    answers_payload = [
        {"question_id": str(q.id), "display_order": q.order_index, "selected_option": "A"}
        for q in questions
    ]

    from tests.conftest import TestSessionLocal

    # 1. Initial submission
    async with TestSessionLocal() as session:
        r1 = await WebAppExamService.submit_batch_answers(
            db=session,
            telegram_user_id=p.telegram_user_id,
            attempt_id=attempt.id,
            answers=answers_payload,
        )
    assert r1["status"] == "success"
    assert r1["score"] == 10
    original_submitted_at = r1["submitted_at"]

    # 2. Immediate retry
    async with TestSessionLocal() as session:
        r2 = await WebAppExamService.submit_batch_answers(
            db=session,
            telegram_user_id=p.telegram_user_id,
            attempt_id=attempt.id,
            answers=answers_payload,
        )
    assert r2["status"] == "already_submitted"
    assert r2["score"] == 10

    # 3. Third retry with different answers payload (client tampering or re-answering attempt)
    tampered_payload = [
        {"question_id": str(q.id), "display_order": q.order_index, "selected_option": "B"}
        for q in questions
    ]
    async with TestSessionLocal() as session:
        r3 = await WebAppExamService.submit_batch_answers(
            db=session,
            telegram_user_id=p.telegram_user_id,
            attempt_id=attempt.id,
            answers=tampered_payload,
        )
    assert r3["status"] == "already_submitted"
    assert r3["score"] == 10  # Score remains 10 (unaltered)

    # 4. Verify database attempt is unchanged
    async with TestSessionLocal() as session:
        refreshed = (await session.execute(
            select(ExamAttempt).where(ExamAttempt.id == attempt.id)
        )).scalar_one()
        assert refreshed.status == AttemptStatus.SUBMITTED
        assert refreshed.score == 10
        assert refreshed.submitted_at.isoformat() == original_submitted_at


@pytest.mark.asyncio
async def test_submission_transaction_rollback_on_partial_failure(db_session: AsyncSession):
    """Test 5: Transaction rollback and partial-failure handling.
    
    Verifies:
    - If a failure occurs before commit, the attempt remains in IN_PROGRESS.
    - No partial score or incorrect submitted status is persisted.
    - A subsequent valid submission succeeds normally.
    """
    comp, questions = await seed_test_competition(db_session, num_questions=5)
    p, attempt = await seed_participant_and_attempt(db_session, comp, questions, user_idx=500)

    from tests.conftest import TestSessionLocal
    from unittest.mock import patch

    # 1. Simulate a database failure during db.commit()
    with patch("sqlalchemy.ext.asyncio.AsyncSession.commit", side_effect=RuntimeError("Simulated database failure")):
        async with TestSessionLocal() as session:
            with pytest.raises(RuntimeError, match="Simulated database failure"):
                await WebAppExamService.submit_batch_answers(
                    db=session,
                    telegram_user_id=p.telegram_user_id,
                    attempt_id=attempt.id,
                    answers=[{"question_id": str(q.id), "display_order": q.order_index, "selected_option": "A"} for q in questions],
                )

    # 2. Verify state rolled back cleanly to IN_PROGRESS
    async with TestSessionLocal() as session:
        check = (await session.execute(
            select(ExamAttempt).where(ExamAttempt.id == attempt.id)
        )).scalar_one()
        assert check.status == AttemptStatus.IN_PROGRESS
        assert check.score is None
        assert check.submitted_at is None

    # 3. Subsequent valid submission must succeed
    async with TestSessionLocal() as session:
        r = await WebAppExamService.submit_batch_answers(
            db=session,
            telegram_user_id=p.telegram_user_id,
            attempt_id=attempt.id,
            answers=[{"question_id": str(q.id), "display_order": q.order_index, "selected_option": "A"} for q in questions],
        )
    assert r["status"] == "success"
    assert r["score"] == 5

    # 4. Final verification in database
    async with TestSessionLocal() as session:
        final_attempt = (await session.execute(
            select(ExamAttempt).where(ExamAttempt.id == attempt.id)
        )).scalar_one()
        assert final_attempt.status == AttemptStatus.SUBMITTED
        assert final_attempt.score == 5
