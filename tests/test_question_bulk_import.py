"""Tests for bulk question import utility and Supabase CSV compatibility."""
import json
import os
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.scripts.import_questions_csv import parse_csv_records, bulk_import_questions
from app.services.competition_service import CompetitionService
from app.services.scoring_service import ScoringAndRankingService


@pytest.mark.asyncio
async def test_parse_supabase_template():
    """Verify questions_supabase_template.csv parses cleanly."""
    file_path = "questions_supabase_template.csv"
    assert os.path.exists(file_path), "Template file should exist"
    dummy_comp_id = uuid.uuid4()
    questions, errors = parse_csv_records(file_path, default_competition_id=dummy_comp_id)
    assert not errors, f"Should have zero parsing errors: {errors}"
    assert len(questions) == 10
    for q in questions:
        assert set(q["options"].keys()) == {"A", "B", "C", "D"}
        assert q["correct_option"] in {"A", "B", "C", "D"}
        assert q["order_index"] >= 1
        assert len(q["question_text"]) > 5


@pytest.mark.asyncio
async def test_parse_flat_template():
    """Verify questions_flat_template.csv parses cleanly."""
    file_path = "questions_flat_template.csv"
    assert os.path.exists(file_path), "Flat template file should exist"
    dummy_comp_id = uuid.uuid4()
    questions, errors = parse_csv_records(file_path, default_competition_id=dummy_comp_id)
    assert not errors, f"Should have zero parsing errors: {errors}"
    assert len(questions) == 10
    for q in questions:
        assert set(q["options"].keys()) == {"A", "B", "C", "D"}
        assert q["correct_option"] in {"A", "B", "C", "D"}
        assert q["order_index"] >= 1


@pytest.mark.asyncio
async def test_parse_validation_errors(tmp_path):
    """Test validation catches invalid CSV rows."""
    bad_csv = tmp_path / "bad_questions.csv"
    bad_csv.write_text(
        "order_index,question_text,option_a,option_b,option_c,option_d,correct_option\n"
        "1,,Opt A,Opt B,Opt C,Opt D,A\n"  # Empty question text
        "2,Valid Q,Opt A,Opt B,Opt C,,A\n"  # Empty option D
        "2,Duplicate Index,Opt A,Opt B,Opt C,Opt D,Z\n"  # Dup index & invalid correct_option
    )
    questions, errors = parse_csv_records(str(bad_csv), default_competition_id=uuid.uuid4())
    assert len(errors) >= 3
    assert any("question_text is empty" in e for e in errors)
    assert any("must all be non-empty" in e for e in errors)
    assert any("must be one of 'A', 'B', 'C', 'D'" in e for e in errors)


@pytest.mark.asyncio
async def test_bulk_import_flat_csv(db_session: AsyncSession):
    """Test importing flat CSV directly into a competition."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="National Qur'an Championship",
        description="Exam questions imported from CSV",
        status=CompetitionStatus.DRAFT,
        opens_at=now + timedelta(hours=1),
        closes_at=now + timedelta(hours=3),
        duration_minutes=30,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.flush()

    # Dry run
    success, msg, count = await bulk_import_questions(
        file_path="questions_flat_template.csv",
        competition_id=comp.id,
        dry_run=True,
        session=db_session,
    )
    assert success is True
    assert count == 10
    assert "[DRY-RUN SUCCESS]" in msg

    # Actual import
    success, msg, count = await bulk_import_questions(
        file_path="questions_flat_template.csv",
        competition_id=comp.id,
        dry_run=False,
        session=db_session,
    )
    assert success is True
    assert count == 10

    # Verify rows in DB
    order_index_stmt = (
        select(CompetitionQuestion)
        .where(CompetitionQuestion.competition_id == comp.id)
        .order_by(CompetitionQuestion.order_index)
    )
    result = await db_session.execute(order_index_stmt)
    db_questions = result.scalars().all()
    assert len(db_questions) == 10
    assert db_questions[0].order_index == 1
    assert db_questions[0].options["A"] == "Abyssinia (Ethiopia)"
    assert db_questions[0].correct_option == "A"
    assert db_questions[0].id is not None
    assert db_questions[0].created_at is not None

    # Check that comp.question_count was automatically synced
    await db_session.refresh(comp)
    assert comp.question_count == 10

    # Pre-live validation must pass with imported questions
    await CompetitionService.validate_competition_for_live(db_session, comp)


@pytest.mark.asyncio
async def test_bulk_import_replace(db_session: AsyncSession):
    """Test that --replace overwrites existing questions cleanly."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Replace Test Competition",
        status=CompetitionStatus.DRAFT,
        opens_at=now,
        closes_at=now + timedelta(hours=1),
        duration_minutes=15,
        question_count=1,
    )
    db_session.add(comp)
    await db_session.flush()

    old_q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Old Question 1",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    db_session.add(old_q)
    await db_session.commit()

    # Attempt import without replace should fail
    success, msg, count = await bulk_import_questions(
        file_path="questions_flat_template.csv",
        competition_id=comp.id,
        replace_existing=False,
        session=db_session,
    )
    assert success is False
    assert "already has 1 questions" in msg

    # Import with replace=True should succeed
    success, msg, count = await bulk_import_questions(
        file_path="questions_flat_template.csv",
        competition_id=comp.id,
        replace_existing=True,
        session=db_session,
    )
    assert success is True
    assert count == 10

    # Verify exactly 10 questions remain
    stmt = select(CompetitionQuestion).where(CompetitionQuestion.competition_id == comp.id)
    questions = (await db_session.execute(stmt)).scalars().all()
    assert len(questions) == 10


@pytest.mark.asyncio
async def test_imported_questions_exam_attempt_and_scoring(db_session: AsyncSession):
    """Verify that questions imported via CSV function end-to-end in real attempts."""
    from app.models.participant import Participant
    from app.models.attempt import AttemptStatus

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Live Exam Test",
        status=CompetitionStatus.DRAFT,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=2),
        duration_minutes=60,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.flush()

    # Import questions
    success, _, count = await bulk_import_questions(
        file_path="questions_flat_template.csv",
        competition_id=comp.id,
        session=db_session,
    )
    assert success is True
    assert count == 10

    # Set competition LIVE
    comp.status = CompetitionStatus.LIVE
    await db_session.commit()

    # Create participant
    part = Participant(
        telegram_user_id=987654321,
        telegram_username="amina_test",
        membership_id="EMYC/12345/2026",
        is_active=True,
    )
    db_session.add(part)
    await db_session.commit()

    # Start attempt
    attempt = await CompetitionService.start_attempt(db_session, comp.id, part.id)
    assert attempt.status == AttemptStatus.IN_PROGRESS

    # Answer all questions
    for q_idx in range(1, 11):
        q_data = await CompetitionService.get_question_for_attempt(db_session, attempt.id, q_idx)
        assert q_data is not None
        # Submit choice 'A'
        ans = await CompetitionService.submit_answer(
            db=db_session,
            attempt_id=attempt.id,
            question_id=q_data["question_id"],
            selected_display_option="A",
            participant_id=part.id,
            display_order=q_idx,
        )
        assert ans is not None

    # Submit attempt
    completed_attempt = await CompetitionService.submit_attempt(db_session, attempt.id, part.id)
    assert completed_attempt.status == AttemptStatus.SUBMITTED

    # Calculate score
    score, correct, incorrect = await ScoringAndRankingService.score_attempt(
        db_session, completed_attempt
    )
    assert score >= 0
    assert correct + incorrect == 10
    assert completed_attempt.score == score
