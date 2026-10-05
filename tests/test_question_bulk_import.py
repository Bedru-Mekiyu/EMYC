"""Tests for bulk question import utility, name resolution, and Supabase integration."""
import os
import uuid
import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import AttemptStatus
from app.scripts.import_questions_csv import (
    parse_csv_records,
    resolve_competition,
    bulk_import_questions,
)
from app.services.competition_service import CompetitionService
from app.services.scoring_service import ScoringAndRankingService


@pytest.mark.asyncio
async def test_parse_questions_template_csv():
    """1 & 2 & 3: Verify questions_template.csv exists, parses cleanly, and contains 20 valid questions."""
    file_path = "questions_template.csv"
    assert os.path.exists(file_path), "questions_template.csv should exist"
    dummy_comp_id = uuid.uuid4()
    questions, errors = parse_csv_records(file_path, default_competition_id=dummy_comp_id)
    assert not errors, f"Should have zero parsing errors: {errors}"
    assert len(questions) == 20
    for q in questions:
        assert set(q["options"].keys()) == {"A", "B", "C", "D"}
        assert q["correct_option"] in {"A", "B", "C", "D"}
        assert q["order_index"] >= 1
        assert len(q["question_text"]) > 5
        assert all(len(v) > 0 for v in q["options"].values())


@pytest.mark.asyncio
async def test_validation_invalid_correct_option(tmp_path):
    """4: Reject invalid correct_option keys."""
    bad_csv = tmp_path / "bad_correct_option.csv"
    bad_csv.write_text(
        "order_index,question_text,option_a,option_b,option_c,option_d,correct_option\n"
        "1,What is 2+2?,1,2,3,4,Z\n"  # Invalid option Z
    )
    questions, errors = parse_csv_records(str(bad_csv), default_competition_id=uuid.uuid4())
    assert len(errors) == 1
    assert "must be one of 'A', 'B', 'C', 'D'" in errors[0]


@pytest.mark.asyncio
async def test_validation_missing_option(tmp_path):
    """5: Reject missing or empty option values."""
    bad_csv = tmp_path / "missing_option.csv"
    bad_csv.write_text(
        "order_index,question_text,option_a,option_b,option_c,option_d,correct_option\n"
        "1,What is 2+2?,1,,3,4,A\n"  # Option B is empty
    )
    questions, errors = parse_csv_records(str(bad_csv), default_competition_id=uuid.uuid4())
    assert len(errors) == 1
    assert "options A, B, C, and D must all be non-empty" in errors[0]


@pytest.mark.asyncio
async def test_validation_missing_question_text(tmp_path):
    """6: Reject empty question text."""
    bad_csv = tmp_path / "missing_text.csv"
    bad_csv.write_text(
        "order_index,question_text,option_a,option_b,option_c,option_d,correct_option\n"
        "1,,Opt A,Opt B,Opt C,Opt D,A\n"  # Empty question text
    )
    questions, errors = parse_csv_records(str(bad_csv), default_competition_id=uuid.uuid4())
    assert len(errors) == 1
    assert "question_text is empty" in errors[0]


@pytest.mark.asyncio
async def test_validation_duplicate_order_index(tmp_path):
    """7: Reject duplicate order indexes."""
    bad_csv = tmp_path / "dup_index.csv"
    bad_csv.write_text(
        "order_index,question_text,option_a,option_b,option_c,option_d,correct_option\n"
        "1,Question One,A,B,C,D,A\n"
        "1,Question Two,A,B,C,D,B\n"  # Duplicate index 1
    )
    questions, errors = parse_csv_records(str(bad_csv), default_competition_id=uuid.uuid4())
    assert len(errors) == 1
    assert "duplicate order_index 1 detected" in errors[0]


@pytest.mark.asyncio
async def test_invalid_competition_resolution(db_session: AsyncSession):
    """8: Detect and report invalid competition name and UUID."""
    # Test invalid UUID
    non_existent_uuid = uuid.uuid4()
    comp, err = await resolve_competition(db_session, competition_id=non_existent_uuid)
    assert comp is None
    assert f"Competition with ID '{non_existent_uuid}' not found." in err

    # Test non-existent name
    comp, err = await resolve_competition(db_session, competition_name="Non Existent Contest")
    assert comp is None
    assert "No competition found with title matching 'Non Existent Contest'" in err


@pytest.mark.asyncio
async def test_competition_name_resolution(db_session: AsyncSession):
    """14: Verify case-insensitive exact and substring name resolution, plus disambiguation."""
    now = datetime.now(timezone.utc)
    comp1 = Competition(
        title="EMYC October Competition",
        status=CompetitionStatus.DRAFT,
        opens_at=now + timedelta(days=1),
        closes_at=now + timedelta(days=2),
        duration_minutes=60,
        question_count=0,
    )
    comp2 = Competition(
        title="EMYC November Competition",
        status=CompetitionStatus.DRAFT,
        opens_at=now + timedelta(days=30),
        closes_at=now + timedelta(days=31),
        duration_minutes=60,
        question_count=0,
    )
    db_session.add_all([comp1, comp2])
    await db_session.commit()

    # Exact case-insensitive match
    found, err = await resolve_competition(db_session, competition_name="emyc october competition")
    assert err is None
    assert found.id == comp1.id

    # Substring match
    found, err = await resolve_competition(db_session, competition_name="November")
    assert err is None
    assert found.id == comp2.id

    # Ambiguous match (matches both)
    found, err = await resolve_competition(db_session, competition_name="EMYC")
    assert found is None
    assert "Multiple competitions found matching 'EMYC'" in err


@pytest.mark.asyncio
async def test_dry_run_does_not_modify_database(db_session: AsyncSession):
    """9: Dry-run verifies CSV and competition eligibility without modifying database."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Dry Run Test Contest",
        status=CompetitionStatus.DRAFT,
        opens_at=now + timedelta(hours=1),
        closes_at=now + timedelta(hours=3),
        duration_minutes=30,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.commit()

    # Run dry-run
    success, msg, count = await bulk_import_questions(
        file_path="questions_template.csv",
        competition_name="Dry Run Test Contest",
        dry_run=True,
        session=db_session,
    )
    assert success is True
    assert count == 20
    assert "Dry run complete." in msg
    assert "No database changes made." in msg
    assert "✓ 20 questions validated" in msg
    assert "✓ Competition is eligible for import" in msg

    # Verify zero rows in database
    count_stmt = select(func.count()).select_from(CompetitionQuestion).where(
        CompetitionQuestion.competition_id == comp.id
    )
    row_count = (await db_session.execute(count_stmt)).scalar()
    assert row_count == 0

    # Verify comp.question_count remains 0
    await db_session.refresh(comp)
    assert comp.question_count == 0


@pytest.mark.asyncio
async def test_successful_import_and_question_count_sync(db_session: AsyncSession):
    """1 & 3 & 11: 20-question bulk import updates competition.question_count atomically."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="National Qur'an Championship 2026",
        status=CompetitionStatus.DRAFT,
        opens_at=now + timedelta(hours=1),
        closes_at=now + timedelta(hours=3),
        duration_minutes=30,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.commit()

    # Real import by competition name
    success, msg, count = await bulk_import_questions(
        file_path="questions_template.csv",
        competition_name="National Qur'an Championship 2026",
        dry_run=False,
        session=db_session,
    )
    assert success is True
    assert count == 20
    assert "Questions imported: 20" in msg
    assert "✓ Database transaction committed" in msg

    # Verify 20 rows in DB
    stmt = (
        select(CompetitionQuestion)
        .where(CompetitionQuestion.competition_id == comp.id)
        .order_by(CompetitionQuestion.order_index)
    )
    questions = (await db_session.execute(stmt)).scalars().all()
    assert len(questions) == 20
    assert questions[0].order_index == 1
    assert questions[0].question_text == "What is the capital of Ethiopia?"
    assert questions[0].options["A"] == "Addis Ababa"
    assert questions[0].correct_option == "A"
    assert questions[19].order_index == 20

    # Verify question_count synced
    await db_session.refresh(comp)
    assert comp.question_count == 20


@pytest.mark.asyncio
async def test_uuid_based_import_and_replace_mode(db_session: AsyncSession):
    """15 & 10: UUID-based import with --replace overwrites existing questions safely."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="UUID Replace Contest",
        status=CompetitionStatus.DRAFT,
        opens_at=now + timedelta(hours=1),
        closes_at=now + timedelta(hours=3),
        duration_minutes=30,
        question_count=1,
    )
    db_session.add(comp)
    await db_session.flush()

    old_q = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Old Question Prompt",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    db_session.add(old_q)
    await db_session.commit()

    # Import without replace should be rejected
    success, msg, count = await bulk_import_questions(
        file_path="questions_template.csv",
        competition_id=comp.id,
        replace_existing=False,
        session=db_session,
    )
    assert success is False
    assert "already has 1 questions. Use --replace to overwrite them." in msg

    # Import with replace=True should succeed
    success, msg, count = await bulk_import_questions(
        file_path="questions_template.csv",
        competition_id=comp.id,
        replace_existing=True,
        session=db_session,
    )
    assert success is True
    assert count == 20

    # Verify exactly 20 questions exist
    q_stmt = select(CompetitionQuestion).where(CompetitionQuestion.competition_id == comp.id)
    all_q = (await db_session.execute(q_stmt)).scalars().all()
    assert len(all_q) == 20


@pytest.mark.asyncio
async def test_protected_live_competition_blocked(db_session: AsyncSession):
    """13: Live competition cannot be accidentally overwritten."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Live Protected Contest",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=10),
        closes_at=now + timedelta(hours=1),
        duration_minutes=30,
        question_count=5,
    )
    db_session.add(comp)
    await db_session.commit()

    # Attempt to import should fail
    success, msg, count = await bulk_import_questions(
        file_path="questions_template.csv",
        competition_name="Live Protected Contest",
        session=db_session,
    )
    assert success is False
    assert "Competition is currently LIVE" in msg
    assert "Modifying questions for active or finalized competitions is prohibited." in msg


@pytest.mark.asyncio
async def test_exam_engine_retrieves_imported_questions(db_session: AsyncSession):
    """12: Examination engine starts attempt, shuffles options, records answer, and scores."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Exam Engine Retrieval Contest",
        status=CompetitionStatus.DRAFT,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=2),
        duration_minutes=60,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.flush()

    # Import 20 questions
    success, _, count = await bulk_import_questions(
        file_path="questions_template.csv",
        competition_id=comp.id,
        session=db_session,
    )
    assert success is True

    # Transition to LIVE
    comp.status = CompetitionStatus.LIVE
    await db_session.commit()

    # Create participant
    part = Participant(
        telegram_user_id=1122334455,
        telegram_username="student_test",
        membership_id="EMYC/99887/2026",
        is_active=True,
    )
    db_session.add(part)
    await db_session.commit()

    # Start attempt
    attempt = await CompetitionService.start_attempt(db_session, comp.id, part.id)
    assert attempt.status == AttemptStatus.IN_PROGRESS

    # Retrieve first 5 questions and answer them
    for q_idx in range(1, 6):
        q_data = await CompetitionService.get_question_for_attempt(db_session, attempt.id, q_idx)
        assert q_data is not None
        assert "question_text" in q_data
        assert set(q_data["options"].keys()) == {"A", "B", "C", "D"}

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

    # Score attempt
    score, correct, incorrect = await ScoringAndRankingService.score_attempt(
        db_session, completed_attempt
    )
    assert score >= 0
    assert correct + incorrect == 5
    assert completed_attempt.score == score


@pytest.mark.asyncio
async def test_ten_question_bulk_import(tmp_path, db_session: AsyncSession):
    """2: Explicitly verify 10-question bulk import."""
    ten_csv = tmp_path / "ten_questions.csv"
    lines = ["order_index,question_text,option_a,option_b,option_c,option_d,correct_option"]
    for i in range(1, 11):
        lines.append(f'{i},"Question {i} prompt?","Choice A","Choice B","Choice C","Choice D",A')
    ten_csv.write_text("\n".join(lines))

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Ten Question Test Competition",
        status=CompetitionStatus.DRAFT,
        opens_at=now + timedelta(hours=1),
        closes_at=now + timedelta(hours=3),
        duration_minutes=20,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.commit()

    success, msg, count = await bulk_import_questions(
        file_path=str(ten_csv),
        competition_name="Ten Question Test Competition",
        session=db_session,
    )
    assert success is True
    assert count == 10
    await db_session.refresh(comp)
    assert comp.question_count == 10

    # Verify rows in database
    q_count = (
        await db_session.execute(
            select(func.count()).select_from(CompetitionQuestion).where(
                CompetitionQuestion.competition_id == comp.id
            )
        )
    ).scalar()
    assert q_count == 10


@pytest.mark.asyncio
async def test_failed_import_atomic_rollback(tmp_path, db_session: AsyncSession, monkeypatch):
    """10: Failed import rolls back completely inside atomic transaction."""
    test_csv = tmp_path / "rollback_test.csv"
    test_csv.write_text(
        "order_index,question_text,option_a,option_b,option_c,option_d,correct_option\n"
        "1,Question One,A,B,C,D,A\n"
        "2,Question Two,A,B,C,D,B\n"
    )

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Rollback Test Contest",
        status=CompetitionStatus.DRAFT,
        opens_at=now + timedelta(hours=1),
        closes_at=now + timedelta(hours=3),
        duration_minutes=20,
        question_count=0,
    )
    comp_id = comp.id
    db_session.add(comp)
    await db_session.commit()

    # Monkeypatch commit to raise an exception simulating a database write crash
    async def mock_fail_commit():
        raise RuntimeError("Simulated database failure during commit")

    monkeypatch.setattr(db_session, "commit", mock_fail_commit)

    success, msg, count = await bulk_import_questions(
        file_path=str(test_csv),
        competition_name="Rollback Test Contest",
        session=db_session,
    )
    assert success is False
    assert "Transaction Error:" in msg
    assert "Import aborted. All changes rolled back." in msg

    # Unpatch commit to query database state
    monkeypatch.undo()
    q_count = (
        await db_session.execute(
            select(func.count()).select_from(CompetitionQuestion).where(
                CompetitionQuestion.competition_id == comp_id
            )
        )
    ).scalar()
    assert q_count == 0
