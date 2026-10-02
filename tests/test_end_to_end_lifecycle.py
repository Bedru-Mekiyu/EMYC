import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings
from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus
from app.services.competition_service import (
    CompetitionService,
    CompetitionError,
    CompetitionNotOpenError,
    AttemptExpiredError,
    DuplicateAttemptError,
)
from app.services.membership_service import (
    ParticipantService,
    MockMembershipVerificationService,
    MembershipAlreadyBoundError,
)
from app.services.scoring_service import (
    ScoringAndRankingService,
    ResultsNotPublishedError,
)

settings = get_settings()


@pytest.mark.asyncio
async def test_complete_end_to_end_competition_workflow(db_session: AsyncSession):
    """Executes the full 17-step end-to-end competition lifecycle scenario."""
    now = datetime.now(timezone.utc)
    verifier = MockMembershipVerificationService()

    # Step 1: Admin creates and configures competition
    comp = await CompetitionService.create_competition(
        db=db_session,
        title="2026 National STEM Championship",
        description="Comprehensive End-to-End Competition Test",
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=2),
        duration_minutes=30,
        question_count=3,
        status=CompetitionStatus.DRAFT,
    )
    assert comp.status == CompetitionStatus.DRAFT

    # Add 3 questions
    q1 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="What is the chemical formula for water?",
        options={"A": "CO2", "B": "H2O", "C": "NaCl", "D": "O2"},
        correct_option="B",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Which planet is known as the Red Planet?",
        options={"A": "Mars", "B": "Jupiter", "C": "Venus", "D": "Saturn"},
        correct_option="A",
        order_index=2,
    )
    q3 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="What is 7 multiplied by 8?",
        options={"A": "54", "B": "56", "C": "48", "D": "64"},
        correct_option="B",
        order_index=3,
    )
    db_session.add_all([q1, q2, q3])
    await db_session.commit()

    # Step 2: Competition becomes LIVE
    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.LIVE)
    assert comp.status == CompetitionStatus.LIVE

    # Step 3: Participant registers/binds membership
    tg_user_id = 987654321
    membership_id = "EMYC/4055828/2026"
    participant = await ParticipantService.register_or_bind_participant(
        db=db_session,
        telegram_user_id=tg_user_id,
        membership_id=membership_id,
        telegram_username="stem_champion",
        language_code="en",
        verifier=verifier,
    )
    assert participant.membership_id == membership_id
    assert participant.telegram_user_id == tg_user_id

    # Security check: Second telegram user trying to steal this membership ID must fail
    with pytest.raises(MembershipAlreadyBoundError):
        await ParticipantService.register_or_bind_participant(
            db=db_session,
            telegram_user_id=11223344,
            membership_id=membership_id,
            verifier=verifier,
        )

    # Step 4: Participant starts official attempt
    attempt = await CompetitionService.start_attempt(db_session, comp.id, participant.id)
    assert attempt.status == AttemptStatus.IN_PROGRESS
    assert attempt.started_at is not None
    assert attempt.deadline_at is not None

    # Security check: Duplicate attempt start must be strictly rejected
    with pytest.raises(DuplicateAttemptError):
        await CompetitionService.start_attempt(db_session, comp.id, participant.id)

    # Step 5 & 6: Questions and options are randomized and mappings persisted
    q_data_1 = await CompetitionService.get_question_for_attempt(db_session, attempt.id, display_order=1)
    assert q_data_1["total_questions"] == 3
    # Check that canonical correct answer is NOT leaked
    assert "correct_option" not in q_data_1
    assert "canonical_option" not in q_data_1

    # Step 7: Participant answers Question 1
    q1_id = q_data_1["question_id"]
    ans1_res = await CompetitionService.submit_answer(
        db_session, attempt.id, q1_id, selected_display_option="A"
    )
    assert ans1_res["status"] == "recorded"
    # Live correctness must not be revealed
    assert "is_correct" not in ans1_res

    # Security check: Duplicate callback doesn't corrupt data (idempotency)
    ans1_dup = await CompetitionService.submit_answer(
        db_session, attempt.id, q1_id, selected_display_option="A"
    )
    assert ans1_dup["status"] == "already_recorded"

    # Step 8: Participant leaves and returns -> timer continues on server
    q_data_2 = await CompetitionService.get_question_for_attempt(db_session, attempt.id, display_order=2)
    assert q_data_2["display_order"] == 2
    q2_id = q_data_2["question_id"]
    await CompetitionService.submit_answer(
        db_session, attempt.id, q2_id, selected_display_option="B"
    )

    # Question 3
    q_data_3 = await CompetitionService.get_question_for_attempt(db_session, attempt.id, display_order=3)
    q3_id = q_data_3["question_id"]
    await CompetitionService.submit_answer(
        db_session, attempt.id, q3_id, selected_display_option="C"
    )

    # Step 9: Participant completes and submits exam
    submitted_attempt = await CompetitionService.submit_attempt(db_session, attempt.id)
    assert submitted_attempt.status == AttemptStatus.SUBMITTED
    assert submitted_attempt.submitted_at is not None
    assert submitted_attempt.completion_seconds is not None

    # Step 10: Security check: Participant cannot access results prior to official publication
    with pytest.raises(ResultsNotPublishedError):
        await ScoringAndRankingService.get_participant_result(db_session, comp.id, participant.id)

    with pytest.raises(ResultsNotPublishedError):
        await ScoringAndRankingService.get_answer_review(db_session, comp.id, participant.id, review_type="all")

    # Step 11: Competition closes
    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.CLOSED)
    assert comp.status == CompetitionStatus.CLOSED

    # Step 12: Admin finalizes results (scores calculated, rankings assigned)
    finalized = await ScoringAndRankingService.finalize_competition_results(db_session, comp.id)
    assert len(finalized) == 1
    assert finalized[0].rank == 1
    assert finalized[0].score is not None

    await db_session.refresh(comp)
    assert comp.status == CompetitionStatus.RESULTS_FINALIZED

    # Step 13: Results still shielded until PUBLISHED
    with pytest.raises(ResultsNotPublishedError):
        await ScoringAndRankingService.get_participant_result(db_session, comp.id, participant.id)

    # Step 14: Admin publishes results
    comp = await ScoringAndRankingService.publish_results(db_session, comp.id)
    assert comp.status == CompetitionStatus.PUBLISHED

    # Step 15: Participant views official result
    result = await ScoringAndRankingService.get_participant_result(db_session, comp.id, participant.id)
    assert result["rank"] == 1
    assert result["total_questions"] == 3
    assert result["score"] >= 0
    assert "completion_time" in result

    # Step 16: Participant reviews correct answers
    correct_reviews = await ScoringAndRankingService.get_answer_review(
        db_session, comp.id, participant.id, review_type="correct"
    )
    for rev in correct_reviews:
        assert rev["is_correct"] is True
        assert "correct_answer_text" in rev

    # Step 17: Participant reviews incorrect answers
    incorrect_reviews = await ScoringAndRankingService.get_answer_review(
        db_session, comp.id, participant.id, review_type="incorrect"
    )
    for rev in incorrect_reviews:
        assert rev["is_correct"] is False
        assert "user_answer_text" in rev
        assert "correct_answer_text" in rev
