import uuid
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus, AttemptQuestionOrder, ParticipantAnswer
from app.services.scoring_service import ScoringAndRankingService, ResultsNotPublishedError


@pytest.mark.asyncio
async def test_scoring_and_deterministic_ranking(db_session: AsyncSession):
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Mathematics Championship",
        status=CompetitionStatus.CLOSED,
        opens_at=now - timedelta(hours=3),
        closes_at=now - timedelta(minutes=10),
        duration_minutes=60,
        question_count=3,
    )
    db_session.add(comp)
    await db_session.flush()

    # 3 Questions
    q1 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Q1",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Q2",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="B",
        order_index=2,
    )
    q3 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Q3",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="C",
        order_index=3,
    )
    db_session.add_all([q1, q2, q3])

    # 4 Participants
    p1 = Participant(telegram_user_id=101, membership_id="EMYC/1000101/2026")
    p2 = Participant(telegram_user_id=102, membership_id="EMYC/1000102/2026")
    p3 = Participant(telegram_user_id=103, membership_id="EMYC/1000103/2026")
    p4 = Participant(telegram_user_id=104, membership_id="EMYC/1000104/2026")
    db_session.add_all([p1, p2, p3, p4])
    await db_session.flush()

    # Create attempts with controlled scores and times:
    # Att 1: Score 3, Time 100s
    att1 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p1.id,
        status=AttemptStatus.SUBMITTED,
        started_at=now - timedelta(minutes=50),
        deadline_at=now + timedelta(minutes=10),
        submitted_at=now - timedelta(minutes=48, seconds=20),
        completion_seconds=100.0,
    )
    # Att 2: Score 2, Time 50s
    att2 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p2.id,
        status=AttemptStatus.SUBMITTED,
        started_at=now - timedelta(minutes=50),
        deadline_at=now + timedelta(minutes=10),
        submitted_at=now - timedelta(minutes=49, seconds=10),
        completion_seconds=50.0,
    )
    # Att 3: Score 2, Time 80s
    att3 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p3.id,
        status=AttemptStatus.SUBMITTED,
        started_at=now - timedelta(minutes=50),
        deadline_at=now + timedelta(minutes=10),
        submitted_at=now - timedelta(minutes=48, seconds=40),
        completion_seconds=80.0,
    )
    # Att 4: Score 2, Time 80s (Exact tie in score and time with Att 3, but submitted earlier!)
    att4 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p4.id,
        status=AttemptStatus.SUBMITTED,
        started_at=now - timedelta(minutes=50),
        deadline_at=now + timedelta(minutes=10),
        submitted_at=now - timedelta(minutes=48, seconds=50),  # 50s ago is earlier than 40s ago
        completion_seconds=80.0,
    )
    db_session.add_all([att1, att2, att3, att4])
    await db_session.flush()

    # Add Answers:
    # Att 1: 3 correct (q1, q2, q3)
    db_session.add_all([
        ParticipantAnswer(attempt_id=att1.id, question_id=q1.id, selected_display_option="A", resolved_canonical_option="A", is_correct=True),
        ParticipantAnswer(attempt_id=att1.id, question_id=q2.id, selected_display_option="B", resolved_canonical_option="B", is_correct=True),
        ParticipantAnswer(attempt_id=att1.id, question_id=q3.id, selected_display_option="C", resolved_canonical_option="C", is_correct=True),
    ])
    # Att 2: 2 correct (q1, q2), 1 wrong (q3)
    db_session.add_all([
        ParticipantAnswer(attempt_id=att2.id, question_id=q1.id, selected_display_option="A", resolved_canonical_option="A", is_correct=True),
        ParticipantAnswer(attempt_id=att2.id, question_id=q2.id, selected_display_option="B", resolved_canonical_option="B", is_correct=True),
        ParticipantAnswer(attempt_id=att2.id, question_id=q3.id, selected_display_option="A", resolved_canonical_option="A", is_correct=False),
    ])
    # Att 3: 2 correct (q1, q3), 1 wrong (q2)
    db_session.add_all([
        ParticipantAnswer(attempt_id=att3.id, question_id=q1.id, selected_display_option="A", resolved_canonical_option="A", is_correct=True),
        ParticipantAnswer(attempt_id=att3.id, question_id=q2.id, selected_display_option="A", resolved_canonical_option="A", is_correct=False),
        ParticipantAnswer(attempt_id=att3.id, question_id=q3.id, selected_display_option="C", resolved_canonical_option="C", is_correct=True),
    ])
    # Att 4: 2 correct (q1, q3), 1 wrong (q2)
    db_session.add_all([
        ParticipantAnswer(attempt_id=att4.id, question_id=q1.id, selected_display_option="A", resolved_canonical_option="A", is_correct=True),
        ParticipantAnswer(attempt_id=att4.id, question_id=q2.id, selected_display_option="A", resolved_canonical_option="A", is_correct=False),
        ParticipantAnswer(attempt_id=att4.id, question_id=q3.id, selected_display_option="C", resolved_canonical_option="C", is_correct=True),
    ])
    await db_session.commit()

    # 1. Finalize results
    finalized = await ScoringAndRankingService.finalize_competition_results(db_session, comp.id)
    assert len(finalized) == 4

    # 2. Check Rank #1: att1 (Score 3)
    await db_session.refresh(att1)
    assert att1.score == 3
    assert att1.rank == 1

    # 3. Check Rank #2: att2 (Score 2, 50s faster than att3 & att4)
    await db_session.refresh(att2)
    assert att2.score == 2
    assert att2.rank == 2

    # 4. Check Rank #3 vs #4 (Score 2, Time 80s for both):
    # att4 submitted earlier than att3, so att4 ranks #3, att3 ranks #4
    await db_session.refresh(att4)
    await db_session.refresh(att3)
    assert att4.rank == 3
    assert att3.rank == 4

    # 5. Result shielding: Attempting to fetch result before publication must raise ResultsNotPublishedError
    with pytest.raises(ResultsNotPublishedError):
        await ScoringAndRankingService.get_participant_result(db_session, comp.id, p1.id)

    with pytest.raises(ResultsNotPublishedError):
        await ScoringAndRankingService.get_answer_review(db_session, comp.id, p1.id, review_type="all")

    # 6. Admin publishes results
    await ScoringAndRankingService.publish_results(db_session, comp.id)

    # 7. Results and reviews now accessible!
    res_p1 = await ScoringAndRankingService.get_participant_result(db_session, comp.id, p1.id)
    assert res_p1["score"] == 3
    assert res_p1["rank"] == 1
    assert res_p1["correct_count"] == 3
    assert res_p1["incorrect_count"] == 0

    # 8. Check review for participant 2 (1 incorrect answer)
    incorrect_p2 = await ScoringAndRankingService.get_answer_review(db_session, comp.id, p2.id, review_type="incorrect")
    assert len(incorrect_p2) == 1
    assert incorrect_p2[0]["is_correct"] is False
    assert incorrect_p2[0]["question_text"] == "Q3"
