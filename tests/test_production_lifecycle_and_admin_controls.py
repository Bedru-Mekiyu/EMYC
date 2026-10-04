"""Production lifecycle, administrative control panel, and participant resumption tests."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from telegram import Update, User
from telegram.ext import ContextTypes

from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus
from app.bot.handlers.admin import (
    cmd_admin,
    cb_admin_lang,
    cb_admin_set_lang,
    cb_admin_competition,
    cb_admin_set_status,
    cb_admin_attach_standard_questions,
    cb_admin_results,
    cb_admin_participants,
    cb_admin_rankings,
    cb_admin_sys_status,
    cb_admin_create_comp_start,
    cb_admin_create_duration,
    cb_admin_create_schedule,
    cb_admin_create_questions,
    cb_admin_add_question_prompt,
    cb_admin_q_wiz_choice,
    cb_admin_q_wiz_save,
    cb_admin_q_list,
    cb_admin_q_del,
    cb_admin_archive,
)
from app.bot.handlers.participant import (
    cb_start_flow,
    cb_exam_start,
    cb_answer,
    handle_text_message,
)
from app.models.attempt import ExamAttempt, AttemptStatus, AttemptQuestionOrder, ParticipantAnswer
from app.services.competition_service import CompetitionService
from app.services.membership_service import ParticipantService, MockMembershipVerificationService
from app.services.scoring_service import ScoringAndRankingService, ResultsNotPublishedError
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.time_utils import now_utc


@pytest.mark.asyncio
async def test_admin_participants_analytics_dashboard(db_session: AsyncSession):
    """Verifies that the dedicated participants analytics dashboard queries real DB stats."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    # Seed 3 participants
    for i in range(1, 4):
        p = Participant(
            telegram_user_id=1000 + i,
            telegram_username=f"user_{i}",
            membership_id=f"EMYC/405582{i}/2026",
            language_code="en",
        )
        db_session.add(p)

    # Seed a competition with 2 attempts
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Analytics Test Cup",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(hours=24),
        duration_minutes=30,
        question_count=10,
    )
    db_session.add(comp)
    await db_session.flush()

    att1 = ExamAttempt(
        competition_id=comp.id,
        participant_id=(await db_session.execute(select(Participant.id))).scalars().first(),
        status=AttemptStatus.SUBMITTED,
        started_at=now - timedelta(minutes=20),
        deadline_at=now + timedelta(minutes=10),
        score=8,
    )
    db_session.add(att1)
    await db_session.commit()

    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_admin_participants(update, context)

    query.edit_message_text.assert_called_once()
    rendered_text = query.edit_message_text.call_args[0][0]
    assert "EMYC Participant Analytics Dashboard" in rendered_text
    assert "Total Registered Members: *3*" in rendered_text
    assert "Attempts Started: *1*" in rendered_text
    assert "Average Score: *8.0*" in rendered_text
    assert "Top Score: *8*" in rendered_text


@pytest.mark.asyncio
async def test_admin_rankings_leaderboard_screen(db_session: AsyncSession):
    """Verifies that the admin can view the full leaderboard ranking."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Leaderboard Challenge",
        status=CompetitionStatus.RESULTS_FINALIZED,
        opens_at=now - timedelta(hours=2),
        closes_at=now - timedelta(hours=1),
        duration_minutes=30,
        question_count=20,
    )
    db_session.add(comp)
    await db_session.flush()

    # Create participant and finalized attempt
    p = Participant(
        telegram_user_id=55555,
        telegram_username="champion_user",
        membership_id="EMYC/4055829/2026",
    )
    db_session.add(p)
    await db_session.flush()

    att = ExamAttempt(
        competition_id=comp.id,
        participant_id=p.id,
        status=AttemptStatus.FINALIZED,
        score=19,
        completion_seconds=645.0,
        rank=1,
        started_at=now - timedelta(hours=2),
        deadline_at=now - timedelta(hours=1, minutes=30),
    )
    db_session.add(att)
    await db_session.commit()

    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_admin_rankings(update, context)

    query.edit_message_text.assert_called_once()
    rendered_text = query.edit_message_text.call_args[0][0]
    assert "EMYC Competition Leaderboard" in rendered_text
    assert "#1" in rendered_text
    assert "champion_user" in rendered_text
    assert "19/20" in rendered_text
    assert "10:45" in rendered_text


@pytest.mark.asyncio
async def test_admin_sys_status_screen(db_session: AsyncSession):
    """Verifies that the system status screen reports environment, DB, sweeper, and webhook status."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_admin_sys_status(update, context)

    query.edit_message_text.assert_called_once()
    rendered_text = query.edit_message_text.call_args[0][0]
    assert "EMYC Platform System Operational Status" in rendered_text
    assert "Runtime Environment:" in rendered_text
    assert "PostgreSQL / Supabase:" in rendered_text
    assert "Healthy" in rendered_text
    assert "Background Deadline Sweeper:" in rendered_text


@pytest.mark.asyncio
async def test_admin_create_competition_interactive_wizard(db_session: AsyncSession):
    """Verifies the multi-step Telegram competition creation wizard."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    # 1. Start wizard
    query_start = MagicMock()
    query_start.answer = AsyncMock()
    query_start.edit_message_text = AsyncMock()
    update_start = MagicMock(spec=Update)
    update_start.effective_user = admin_user
    update_start.callback_query = query_start
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cb_admin_create_comp_start(update_start, context)
    assert context.user_data["create_comp"]["step"] == "title"

    # 2. Input Title
    update_title = MagicMock(spec=Update)
    update_title.effective_user = admin_user
    msg_title = MagicMock()
    msg_title.text = "Grand Ramadan Olympiad 2026"
    msg_title.reply_text = AsyncMock()
    update_title.message = msg_title

    await handle_text_message(update_title, context)
    assert context.user_data["create_comp"]["title"] == "Grand Ramadan Olympiad 2026"
    assert context.user_data["create_comp"]["step"] == "description"

    # 3. Input Description
    update_desc = MagicMock(spec=Update)
    update_desc.effective_user = admin_user
    msg_desc = MagicMock()
    msg_desc.text = "Annual Nationwide Youth Examination"
    msg_desc.reply_text = AsyncMock()
    update_desc.message = msg_desc

    await handle_text_message(update_desc, context)
    assert context.user_data["create_comp"]["description"] == "Annual Nationwide Youth Examination"
    assert context.user_data["create_comp"]["step"] == "duration"

    # 4. Select Duration (60 min)
    query_dur = MagicMock()
    query_dur.data = "admin:create_dur:60"
    query_dur.answer = AsyncMock()
    query_dur.edit_message_text = AsyncMock()
    update_dur = MagicMock(spec=Update)
    update_dur.effective_user = admin_user
    update_dur.callback_query = query_dur

    await cb_admin_create_duration(update_dur, context)
    assert context.user_data["create_comp"]["duration"] == 60
    assert context.user_data["create_comp"]["step"] == "schedule"

    # 5. Select Schedule (7 days)
    query_sched = MagicMock()
    query_sched.data = "admin:create_sched:7d"
    query_sched.answer = AsyncMock()
    query_sched.edit_message_text = AsyncMock()
    update_sched = MagicMock(spec=Update)
    update_sched.effective_user = admin_user
    update_sched.callback_query = query_sched

    await cb_admin_create_schedule(update_sched, context)
    assert context.user_data["create_comp"]["step"] == "questions"

    # 6. Attach Standard Questions
    query_q = MagicMock()
    query_q.data = "admin:create_q:standard"
    query_q.answer = AsyncMock()
    query_q.edit_message_text = AsyncMock()
    update_q = MagicMock(spec=Update)
    update_q.effective_user = admin_user
    update_q.callback_query = query_q

    await cb_admin_create_questions(update_q, context)

    # Verify competition in DB
    stmt = select(Competition).where(Competition.title == "Grand Ramadan Olympiad 2026")
    comp = (await db_session.execute(stmt)).scalar_one_or_none()
    assert comp is not None
    assert comp.duration_minutes == 60
    assert comp.status == CompetitionStatus.DRAFT
    assert comp.question_count == 20

    # Verify questions in DB
    q_count = (await db_session.execute(
        select(func.count(CompetitionQuestion.id)).where(CompetitionQuestion.competition_id == comp.id)
    )).scalar()
    assert q_count == 20


@pytest.mark.asyncio
async def test_admin_add_question_interactive(db_session: AsyncSession):
    """Verifies that an admin can add a custom question with choices via Telegram."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Custom Question Comp",
        status=CompetitionStatus.DRAFT,
        opens_at=now,
        closes_at=now + timedelta(days=2),
        duration_minutes=45,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.commit()

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {"awaiting_question_comp_id": comp.id}

    # Admin sends question with options
    update_q = MagicMock(spec=Update)
    update_q.effective_user = admin_user
    msg_q = MagicMock()
    msg_q.text = "In what year was the first Hijrah? | 615 CE | 622 CE | 630 CE | 610 CE | A"
    msg_q.reply_text = AsyncMock()
    update_q.message = msg_q

    await handle_text_message(update_q, context)

    msg_q.reply_text.assert_called_once()
    reply_text = msg_q.reply_text.call_args[0][0]
    assert "Question #1 Added Successfully!" in reply_text

    # Verify question in DB
    stmt = select(CompetitionQuestion).where(CompetitionQuestion.competition_id == comp.id)
    q = (await db_session.execute(stmt)).scalar_one_or_none()
    assert q is not None
    assert q.question_text == "In what year was the first Hijrah?"
    assert q.correct_option == "A"
    assert q.options["A"] == "615 CE"
    assert q.options["B"] == "622 CE"


@pytest.mark.asyncio
async def test_participant_interruption_resumes_at_unanswered_question(db_session: AsyncSession):
    """Verifies that a participant who answered Q1 and Q2 resumes directly at Q3 on restart."""
    now = datetime.now(timezone.utc)
    comp = await CompetitionService.create_competition(
        db=db_session,
        title="Resume Test Exam",
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(hours=2),
        duration_minutes=30,
        question_count=3,
        status=CompetitionStatus.LIVE,
    )

    for i in range(1, 4):
        q = CompetitionQuestion(
            competition_id=comp.id,
            question_text=f"Question number {i}",
            options={"A": f"Ans A {i}", "B": f"Ans B {i}", "C": f"Ans C {i}", "D": f"Ans D {i}"},
            correct_option="A",
            order_index=i,
        )
        db_session.add(q)
    await db_session.commit()

    participant = await ParticipantService.register_or_bind_participant(
        db=db_session,
        telegram_user_id=77777,
        membership_id="EMYC/4055830/2026",
        verifier=MockMembershipVerificationService(),
    )

    user = User(id=77777, first_name="Tester", is_bot=False)

    # 1. Start exam
    attempt = await CompetitionService.start_attempt(db_session, comp.id, participant.id)

    # 2. Answer question 1 and question 2
    q1_data = await CompetitionService.get_question_for_attempt(db_session, attempt.id, display_order=1, participant_id=participant.id)
    await CompetitionService.submit_answer(db_session, attempt.id, q1_data["question_id"], "A", participant_id=participant.id)

    q2_data = await CompetitionService.get_question_for_attempt(db_session, attempt.id, display_order=2, participant_id=participant.id)
    await CompetitionService.submit_answer(db_session, attempt.id, q2_data["question_id"], "B", participant_id=participant.id)

    # 3. User disconnects, reopens Telegram, clicks Start Competition flow
    query_flow = MagicMock()
    query_flow.answer = AsyncMock()
    query_flow.edit_message_text = AsyncMock()
    update_flow = MagicMock(spec=Update)
    update_flow.effective_user = user
    update_flow.callback_query = query_flow
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cb_start_flow(update_flow, context)

    # Must render question screen for question 3 (first unanswered question)
    query_flow.edit_message_text.assert_called_once()
    screen_text = query_flow.edit_message_text.call_args[0][0]
    assert "Question 3 / 3" in screen_text
    assert "Time remaining:" in screen_text


@pytest.mark.asyncio
async def test_membership_zero_leakage_and_already_bound_handling(db_session: AsyncSession):
    """Verifies that format errors and account-already-bound collisions return clean, non-leaking messages."""
    user = User(id=88888, first_name="MemberUser", is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {"awaiting_membership": True}

    # 1. Invalid format -> returns localized clean error without leaking regex rules
    update_invalid = MagicMock(spec=Update)
    update_invalid.effective_user = user
    msg_invalid = MagicMock()
    msg_invalid.text = "NOT_A_VALID_FORMAT"
    msg_invalid.reply_text = AsyncMock()
    update_invalid.message = msg_invalid

    await handle_text_message(update_invalid, context)
    msg_invalid.reply_text.assert_called_once()
    err_text = msg_invalid.reply_text.call_args[0][0]
    assert "Invalid Membership ID" in err_text
    assert "EMYC" not in err_text or "format" not in err_text.lower()  # zero regex leaks

    # 2. Bind legitimate membership
    context.user_data = {"awaiting_membership": True}
    update_valid = MagicMock(spec=Update)
    update_valid.effective_user = user
    msg_valid = MagicMock()
    msg_valid.text = "EMYC/4055831/2026"
    msg_valid.reply_text = AsyncMock()
    update_valid.message = msg_valid

    await handle_text_message(update_valid, context)
    assert context.user_data.get("awaiting_membership") is False

    # 3. Same Telegram user tries to enter a different membership ID
    context.user_data = {"awaiting_membership": True}
    update_diff = MagicMock(spec=Update)
    update_diff.effective_user = user
    msg_diff = MagicMock()
    msg_diff.text = "EMYC/4055832/2026"
    msg_diff.reply_text = AsyncMock()
    update_diff.message = msg_diff

    await handle_text_message(update_diff, context)
    msg_diff.reply_text.assert_called_once()
    bound_text = msg_diff.reply_text.call_args[0][0]
    assert "already linked" in bound_text.lower()


@pytest.mark.asyncio
async def test_competition_archived_lifecycle(db_session: AsyncSession):
    """Verifies ARCHIVED competition status transitions and Telegram archive handler."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Archival Test Cup",
        status=CompetitionStatus.DRAFT,
        opens_at=now,
        closes_at=now + timedelta(days=1),
        duration_minutes=30,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.commit()
    await db_session.refresh(comp)

    # DRAFT -> ARCHIVED is valid
    comp = await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.ARCHIVED)
    assert comp.status == CompetitionStatus.ARCHIVED

    # ARCHIVED -> cannot transition to any other status
    with pytest.raises(Exception):
        await CompetitionService.update_status(db_session, comp.id, CompetitionStatus.LIVE)

    # Test via Telegram callback handler
    comp2 = Competition(
        title="Archival Via Bot Cup",
        status=CompetitionStatus.PUBLISHED,
        opens_at=now - timedelta(days=2),
        closes_at=now - timedelta(days=1),
        duration_minutes=30,
        question_count=0,
    )
    db_session.add(comp2)
    await db_session.commit()

    query = MagicMock()
    query.data = f"admin:archive:{comp2.id}"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_admin_archive(update, context)
    query.edit_message_text.assert_called_once()
    rendered = query.edit_message_text.call_args[0][0]
    assert "Competition Archived Successfully" in rendered

    await db_session.refresh(comp2)
    assert comp2.status == CompetitionStatus.ARCHIVED


@pytest.mark.asyncio
async def test_step_by_step_question_authoring_wizard(db_session: AsyncSession):
    """Verifies that the 5-step conversational question authoring wizard works end-to-end."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Wizard Question Cup",
        status=CompetitionStatus.DRAFT,
        opens_at=now,
        closes_at=now + timedelta(days=2),
        duration_minutes=30,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.commit()
    await db_session.refresh(comp)

    # 1. Admin taps "Add Question"
    query_start = MagicMock()
    query_start.data = f"admin:add_q:{comp.id}"
    query_start.answer = AsyncMock()
    query_start.edit_message_text = AsyncMock()
    update_start = MagicMock(spec=Update)
    update_start.effective_user = admin_user
    update_start.callback_query = query_start
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cb_admin_add_question_prompt(update_start, context)
    assert "q_wizard" in context.user_data
    assert context.user_data["q_wizard"]["step"] == "text"

    # Helper function to simulate user text responses
    async def send_text(text: str):
        update = MagicMock(spec=Update)
        update.effective_user = admin_user
        msg = MagicMock()
        msg.text = text
        msg.reply_text = AsyncMock()
        update.message = msg
        await handle_text_message(update, context)
        return msg.reply_text

    # Step 1 -> Step 2
    r1 = await send_text("What is the first pillar of Islam?")
    assert context.user_data["q_wizard"]["step"] == "opt_a"
    assert context.user_data["q_wizard"]["text"] == "What is the first pillar of Islam?"

    # Step 2 -> Step 3
    r2 = await send_text("Shahada (Declaration of Faith)")
    assert context.user_data["q_wizard"]["step"] == "opt_b"
    assert context.user_data["q_wizard"]["opt_a"] == "Shahada (Declaration of Faith)"

    # Step 3 -> Step 4
    r3 = await send_text("Salah (Prayer)")
    assert context.user_data["q_wizard"]["step"] == "opt_c"
    assert context.user_data["q_wizard"]["opt_b"] == "Salah (Prayer)"

    # Step 4 -> Step 5
    r4 = await send_text("Zakat (Charity)")
    assert context.user_data["q_wizard"]["step"] == "opt_d"
    assert context.user_data["q_wizard"]["opt_c"] == "Zakat (Charity)"

    # Step 5 -> Correct choice selection
    r5 = await send_text("Sawm (Fasting)")
    assert context.user_data["q_wizard"]["step"] == "choice"
    assert context.user_data["q_wizard"]["opt_d"] == "Sawm (Fasting)"

    # Admin taps choice "A"
    query_choice = MagicMock()
    query_choice.data = "admin:q_wiz_choice:A"
    query_choice.answer = AsyncMock()
    query_choice.edit_message_text = AsyncMock()
    update_choice = MagicMock(spec=Update)
    update_choice.effective_user = admin_user
    update_choice.callback_query = query_choice

    await cb_admin_q_wiz_choice(update_choice, context)
    assert context.user_data["q_wizard"]["step"] == "preview"
    assert context.user_data["q_wizard"]["correct"] == "A"
    query_choice.edit_message_text.assert_called_once()
    preview = query_choice.edit_message_text.call_args[0][0]
    assert "What is the first pillar of Islam?" in preview
    assert "A) Shahada (Declaration of Faith)" in preview

    # Admin confirms save
    query_save = MagicMock()
    query_save.data = f"admin:q_wiz_save:{comp.id}"
    query_save.answer = AsyncMock()
    query_save.edit_message_text = AsyncMock()
    update_save = MagicMock(spec=Update)
    update_save.effective_user = admin_user
    update_save.callback_query = query_save

    await cb_admin_q_wiz_save(update_save, context)
    query_save.edit_message_text.assert_called_once()
    saved_text = query_save.edit_message_text.call_args[0][0]
    assert "Saved Successfully" in saved_text

    # Verify persisted in database
    q_stmt = select(CompetitionQuestion).where(CompetitionQuestion.competition_id == comp.id)
    questions = list((await db_session.execute(q_stmt)).scalars().all())
    assert len(questions) == 1
    assert questions[0].question_text == "What is the first pillar of Islam?"
    assert questions[0].correct_option == "A"
    assert questions[0].options["A"] == "Shahada (Declaration of Faith)"
    assert questions[0].order_index == 1

    await db_session.refresh(comp)
    assert comp.question_count == 1


@pytest.mark.asyncio
async def test_question_list_and_deletion_reindexing(db_session: AsyncSession):
    """Verifies that listing questions and deleting questions automatically re-indexes order_index."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Reindex Test Cup",
        status=CompetitionStatus.DRAFT,
        opens_at=now,
        closes_at=now + timedelta(days=2),
        duration_minutes=30,
        question_count=3,
    )
    db_session.add(comp)
    await db_session.flush()

    q1 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Question 1",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="A",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Question 2 (To Delete)",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="B",
        order_index=2,
    )
    q3 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Question 3",
        options={"A": "1", "B": "2", "C": "3", "D": "4"},
        correct_option="C",
        order_index=3,
    )
    db_session.add_all([q1, q2, q3])
    await db_session.commit()

    # 1. Admin views question list
    query_list = MagicMock()
    query_list.data = f"admin:q_list:{comp.id}:1"
    query_list.answer = AsyncMock()
    query_list.edit_message_text = AsyncMock()
    update_list = MagicMock(spec=Update)
    update_list.effective_user = admin_user
    update_list.callback_query = query_list
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_admin_q_list(update_list, context)
    query_list.edit_message_text.assert_called_once()
    list_text = query_list.edit_message_text.call_args[0][0]
    assert "Total Questions: *3*" in list_text
    assert "Question 1" in list_text
    assert "Question 2 (To Delete)" in list_text

    target_comp_id = comp.id

    # 2. Admin deletes question 2
    query_del = MagicMock()
    query_del.data = f"admin:q_del:{comp.id}:{q2.id}"
    query_del.answer = AsyncMock()
    query_del.edit_message_text = AsyncMock()
    update_del = MagicMock(spec=Update)
    update_del.effective_user = admin_user
    update_del.callback_query = query_del

    await cb_admin_q_del(update_del, context)

    # Verify database state after deletion using a clean session
    async with AsyncSessionLocal() as verify_db:
        q_stmt = select(CompetitionQuestion).where(CompetitionQuestion.competition_id == target_comp_id).order_by(CompetitionQuestion.order_index)
        remaining = list((await verify_db.execute(q_stmt)).scalars().all())
        assert len(remaining) == 2
        assert remaining[0].question_text == "Question 1"
        assert remaining[0].order_index == 1
        assert remaining[1].question_text == "Question 3"
        assert remaining[1].order_index == 2  # re-indexed from 3 to 2!

        comp_fresh = await verify_db.get(Competition, target_comp_id)
        assert comp_fresh.question_count == 2


@pytest.mark.asyncio
async def test_paginated_rankings(db_session: AsyncSession):
    """Verifies that leaderboard rankings are properly paginated to avoid message limits."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Pagination Cup",
        status=CompetitionStatus.RESULTS_FINALIZED,
        opens_at=now - timedelta(days=2),
        closes_at=now - timedelta(days=1),
        duration_minutes=30,
        question_count=20,
    )
    db_session.add(comp)
    await db_session.flush()

    # Seed 15 participants and attempts
    for i in range(1, 16):
        p = Participant(
            telegram_user_id=5000 + i,
            telegram_username=f"member_{i:02d}",
            membership_id=f"EMYC/40559{i:02d}/2026",
            language_code="en",
        )
        db_session.add(p)
        await db_session.flush()

        att = ExamAttempt(
            competition_id=comp.id,
            participant_id=p.id,
            status=AttemptStatus.FINALIZED,
            started_at=now - timedelta(hours=2),
            deadline_at=now - timedelta(hours=1),
            score=20 - i,
            rank=i,
            completion_seconds=600 + i * 10,
        )
        db_session.add(att)
    await db_session.commit()

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    # Page 1 (Top 10)
    query_p1 = MagicMock()
    query_p1.data = "admin:rankings:1"
    query_p1.answer = AsyncMock()
    query_p1.edit_message_text = AsyncMock()
    update_p1 = MagicMock(spec=Update)
    update_p1.effective_user = admin_user
    update_p1.callback_query = query_p1

    await cb_admin_rankings(update_p1, context)
    text_p1 = query_p1.edit_message_text.call_args[0][0]
    assert "15 | Page 1 of 2" in text_p1
    assert "#1" in text_p1
    assert "member_01" in text_p1
    assert "#10" in text_p1
    assert "member_11" not in text_p1

    # Page 2 (Remaining 5)
    query_p2 = MagicMock()
    query_p2.data = "admin:rankings:2"
    query_p2.answer = AsyncMock()
    query_p2.edit_message_text = AsyncMock()
    update_p2 = MagicMock(spec=Update)
    update_p2.effective_user = admin_user
    update_p2.callback_query = query_p2

    await cb_admin_rankings(update_p2, context)
    text_p2 = query_p2.edit_message_text.call_args[0][0]
    assert "15 | Page 2 of 2" in text_p2
    assert "#11" in text_p2
    assert "member_11" in text_p2
    assert "#15" in text_p2
    assert "member_15" in text_p2


def test_render_production_guard(monkeypatch):
    """Verifies that RENDER=true automatically enforces production environment and webhook bot mode."""
    from app.core.config import Settings

    # With RENDER=true
    monkeypatch.setenv("RENDER", "true")
    s = Settings(
        TELEGRAM_BOT_TOKEN="token",
        ADMIN_TELEGRAM_IDS="123",
    )
    assert s.ENVIRONMENT == "production"
    assert s.BOT_MODE == "webhook"

    # Explicit override allowed
    monkeypatch.setenv("RENDER", "true")
    s_custom = Settings(
        TELEGRAM_BOT_TOKEN="token",
        ENVIRONMENT="staging",
        BOT_MODE="disabled",
    )
    assert s_custom.ENVIRONMENT == "staging"
    assert s_custom.BOT_MODE == "disabled"


@pytest.mark.asyncio
async def test_admin_main_menu_simplified_layout(db_session: AsyncSession):
    """Verifies that the admin main menu strictly exposes exactly 3 primary categories."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)

    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    msg = MagicMock()
    msg.reply_text = AsyncMock()
    update.message = msg
    update.callback_query = None
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cmd_admin(update, context)

    msg.reply_text.assert_called_once()
    rendered_kb = msg.reply_text.call_args[1]["reply_markup"]
    buttons = [b.text for row in rendered_kb.inline_keyboard for b in row]
    callbacks = [b.callback_data for row in rendered_kb.inline_keyboard for b in row]

    assert len(buttons) == 3
    assert "🌐 Change Language" in buttons
    assert "🏆 Manage Competition" in buttons
    assert "📊 Competition Results" in buttons

    assert callbacks == ["admin:lang", "admin:competition", "admin:results"]


@pytest.mark.asyncio
async def test_admin_custom_duration_flow(db_session: AsyncSession):
    """Verifies that an admin can select custom duration, receives input validation, and creates competition with custom duration."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    # 1. Start wizard
    query_start = MagicMock()
    query_start.answer = AsyncMock()
    query_start.edit_message_text = AsyncMock()
    update_start = MagicMock(spec=Update)
    update_start.effective_user = admin_user
    update_start.callback_query = query_start
    await cb_admin_create_comp_start(update_start, context)

    # 2. Enter Title
    update_title = MagicMock(spec=Update)
    update_title.effective_user = admin_user
    msg_title = MagicMock()
    msg_title.text = "Custom Duration Challenge 2026"
    msg_title.reply_text = AsyncMock()
    update_title.message = msg_title
    await handle_text_message(update_title, context)

    # 3. Enter Description
    update_desc = MagicMock(spec=Update)
    update_desc.effective_user = admin_user
    msg_desc = MagicMock()
    msg_desc.text = "Testing Custom Durations"
    msg_desc.reply_text = AsyncMock()
    update_desc.message = msg_desc
    await handle_text_message(update_desc, context)

    # 4. Click [Custom Duration]
    query_dur = MagicMock()
    query_dur.data = "admin:create_dur:custom"
    query_dur.answer = AsyncMock()
    query_dur.edit_message_text = AsyncMock()
    update_dur = MagicMock(spec=Update)
    update_dur.effective_user = admin_user
    update_dur.callback_query = query_dur
    await cb_admin_create_duration(update_dur, context)

    assert context.user_data["create_comp"]["step"] == "custom_duration"

    # 5. Invalid duration input: "abc"
    update_invalid = MagicMock(spec=Update)
    update_invalid.effective_user = admin_user
    msg_invalid = MagicMock()
    msg_invalid.text = "invalid_number"
    msg_invalid.reply_text = AsyncMock()
    update_invalid.message = msg_invalid
    await handle_text_message(update_invalid, context)
    msg_invalid.reply_text.assert_called_once()
    assert "Please enter a valid duration" in msg_invalid.reply_text.call_args[0][0]
    assert context.user_data["create_comp"]["step"] == "custom_duration"

    # 6. Valid custom duration input: "75"
    update_valid = MagicMock(spec=Update)
    update_valid.effective_user = admin_user
    msg_valid = MagicMock()
    msg_valid.text = "75"
    msg_valid.reply_text = AsyncMock()
    update_valid.message = msg_valid
    await handle_text_message(update_valid, context)

    assert context.user_data["create_comp"]["duration"] == 75
    assert context.user_data["create_comp"]["step"] == "schedule"

    # 7. Select Schedule (3 days)
    query_sched = MagicMock()
    query_sched.data = "admin:create_sched:3d"
    query_sched.answer = AsyncMock()
    query_sched.edit_message_text = AsyncMock()
    update_sched = MagicMock(spec=Update)
    update_sched.effective_user = admin_user
    update_sched.callback_query = query_sched
    await cb_admin_create_schedule(update_sched, context)

    # 8. Create with standard questions
    query_q = MagicMock()
    query_q.data = "admin:create_q:standard"
    query_q.answer = AsyncMock()
    query_q.edit_message_text = AsyncMock()
    update_q = MagicMock(spec=Update)
    update_q.effective_user = admin_user
    update_q.callback_query = query_q
    await cb_admin_create_questions(update_q, context)

    # Verify persisted competition
    stmt = select(Competition).where(Competition.title == "Custom Duration Challenge 2026")
    comp = (await db_session.execute(stmt)).scalar_one_or_none()
    assert comp is not None
    assert comp.duration_minutes == 75


@pytest.mark.asyncio
async def test_admin_competition_state_aware_controls(db_session: AsyncSession):
    """Verifies that Competition Management renders strictly state-aware actions for SCHEDULED, LIVE, and CLOSED states."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="State Test Competition",
        status=CompetitionStatus.SCHEDULED,
        opens_at=now + timedelta(hours=2),
        closes_at=now + timedelta(days=5),
        duration_minutes=60,
        question_count=10,
    )
    db_session.add(comp)
    await db_session.commit()

    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query

    # 1. State: SCHEDULED
    await cb_admin_competition(update, context)
    rendered_kb = query.edit_message_text.call_args[1]["reply_markup"]
    sched_btn_texts = [b.text for row in rendered_kb.inline_keyboard for b in row]
    assert "▶️ Start Competition (Set LIVE)" in sched_btn_texts
    assert "📦 Archive Competition" in sched_btn_texts
    assert not any("Close Competition" in t for t in sched_btn_texts)
    assert not any("Setup Sample" in t for t in sched_btn_texts)

    # 2. State: LIVE
    comp.status = CompetitionStatus.LIVE
    await db_session.commit()
    query.edit_message_text.reset_mock()

    await cb_admin_competition(update, context)
    rendered_kb = query.edit_message_text.call_args[1]["reply_markup"]
    live_btn_texts = [b.text for row in rendered_kb.inline_keyboard for b in row]
    assert "⏹ Close Competition" in live_btn_texts
    assert "📊 View Results" in live_btn_texts
    assert not any("Start Competition" in t for t in live_btn_texts)

    # 3. State: CLOSED
    comp.status = CompetitionStatus.CLOSED
    await db_session.commit()
    query.edit_message_text.reset_mock()

    await cb_admin_competition(update, context)
    rendered_kb = query.edit_message_text.call_args[1]["reply_markup"]
    closed_btn_texts = [b.text for row in rendered_kb.inline_keyboard for b in row]
    assert "📊 Finalize Scores & Rankings" in closed_btn_texts
    assert "📊 View Results" in closed_btn_texts


@pytest.mark.asyncio
async def test_admin_consolidated_results_and_rankings_navigation(db_session: AsyncSession):
    """Verifies that Competition Results consolidates all operational metrics and provides clean navigation to rankings."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Results Cup 2026",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(hours=5),
        duration_minutes=45,
        question_count=20,
    )
    db_session.add(comp)
    await db_session.flush()

    # Create participant & attempt
    p = Participant(telegram_user_id=881234, membership_id="EMYC/4055829/2026", language_code="en")
    db_session.add(p)
    await db_session.flush()

    att = ExamAttempt(
        competition_id=comp.id,
        participant_id=p.id,
        status=AttemptStatus.SUBMITTED,
        started_at=now - timedelta(minutes=30),
        deadline_at=now + timedelta(minutes=15),
        score=18,
        completion_seconds=800.0,
        rank=1,
    )
    db_session.add(att)
    await db_session.commit()

    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query

    # Call cb_admin_results
    await cb_admin_results(update, context)
    query.edit_message_text.assert_called_once()
    rendered_text = query.edit_message_text.call_args[0][0]

    assert "Competition Results" in rendered_text
    assert "Results Cup 2026" in rendered_text
    assert "Registered:" in rendered_text
    assert "Completed:" in rendered_text
    assert "Top Score:* 18/20" in rendered_text

    rendered_kb = query.edit_message_text.call_args[1]["reply_markup"]
    btn_texts = [b.text for row in rendered_kb.inline_keyboard for b in row]
    assert "🏅 View Rankings" in btn_texts


@pytest.mark.asyncio
async def test_admin_multilingual_language_switching(db_session: AsyncSession):
    """Verifies that an admin can change language and the admin panel re-renders in the selected language."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    # Admin opens language menu
    query_lang = MagicMock()
    query_lang.answer = AsyncMock()
    query_lang.edit_message_text = AsyncMock()
    update_lang = MagicMock(spec=Update)
    update_lang.effective_user = admin_user
    update_lang.callback_query = query_lang

    await cb_admin_lang(update_lang, context)
    query_lang.edit_message_text.assert_called_once()
    lang_kb = query_lang.edit_message_text.call_args[1]["reply_markup"]
    btn_texts = [b.text for row in lang_kb.inline_keyboard for b in row]
    assert "English 🇬🇧" in btn_texts
    assert "አማርኛ 🇪🇹" in btn_texts

    # Admin selects Amharic (admin:set_lang:am)
    query_set = MagicMock()
    query_set.data = "admin:set_lang:am"
    query_set.answer = AsyncMock()
    query_set.edit_message_text = AsyncMock()
    update_set = MagicMock(spec=Update)
    update_set.effective_user = admin_user
    update_set.callback_query = query_set
    update_set.message = None

    await cb_admin_set_lang(update_set, context)
    query_set.edit_message_text.assert_called_once()
    admin_kb = query_set.edit_message_text.call_args[1]["reply_markup"]
    admin_btn_texts = [b.text for row in admin_kb.inline_keyboard for b in row]

    assert "🌐 ቋንቋ ቀይር" in admin_btn_texts
    assert "🏆 ውድድር አስተዳድር" in admin_btn_texts
    assert "📊 የውድድር ውጤቶች" in admin_btn_texts


@pytest.mark.asyncio
async def test_admin_set_live_and_attach_standard_questions(db_session: AsyncSession):
    """Verifies that an admin can attach standard questions and activate competition to LIVE without transition errors."""
    admin_id = 998877
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Zero Questions Comp",
        status=CompetitionStatus.DRAFT,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(days=5),
        duration_minutes=30,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.commit()

    # 1. View competition controls with 0 questions -> offers 1-click attach standard questions
    query_view = MagicMock()
    query_view.answer = AsyncMock()
    query_view.edit_message_text = AsyncMock()
    update_view = MagicMock(spec=Update)
    update_view.effective_user = admin_user
    update_view.callback_query = query_view

    await cb_admin_competition(update_view, context)
    rendered_kb = query_view.edit_message_text.call_args[1]["reply_markup"]
    btn_texts = [b.text for row in rendered_kb.inline_keyboard for b in row]
    assert "⚡ Attach 20 EMYC Standard Questions" in btn_texts

    # 2. Attach standard questions
    query_attach = MagicMock()
    query_attach.data = f"admin:attach_std:{comp.id}"
    query_attach.answer = AsyncMock()
    query_attach.edit_message_text = AsyncMock()
    update_attach = MagicMock(spec=Update)
    update_attach.effective_user = admin_user
    update_attach.callback_query = query_attach

    await cb_admin_attach_standard_questions(update_attach, context)
    await db_session.refresh(comp)
    assert comp.question_count == 20

    # 3. Transition to LIVE via admin:set_live:<id>
    query_live = MagicMock()
    query_live.data = f"admin:set_live:{comp.id}"
    query_live.answer = AsyncMock()
    query_live.edit_message_text = AsyncMock()
    update_live = MagicMock(spec=Update)
    update_live.effective_user = admin_user
    update_live.callback_query = query_live

    await cb_admin_set_status(update_live, context)
    query_live.edit_message_text.assert_called_once()
    live_text = query_live.edit_message_text.call_args[0][0]
    assert "Competition is now LIVE" in live_text

    await db_session.refresh(comp)
    assert comp.status == CompetitionStatus.LIVE

    # 4. Transition to CLOSED via admin:set_closed:<id>
    query_close = MagicMock()
    query_close.data = f"admin:set_closed:{comp.id}"
    query_close.answer = AsyncMock()
    query_close.edit_message_text = AsyncMock()
    update_close = MagicMock(spec=Update)
    update_close.effective_user = admin_user
    update_close.callback_query = query_close

    await cb_admin_set_status(update_close, context)
    query_close.edit_message_text.assert_called_once()
    close_text = query_close.edit_message_text.call_args[0][0]
    assert "Competition is now CLOSED" in close_text

    await db_session.refresh(comp)
    assert comp.status == CompetitionStatus.CLOSED


@pytest.mark.asyncio
async def test_callback_data_length_under_telegram_64_byte_limit_and_compact_answer(db_session: AsyncSession):
    """Verifies that all question option callbacks are <= 64 bytes (preventing BUTTON_DATA_INVALID)
    and that compact answer submissions are correctly processed.
    """
    from app.bot.keyboards import get_question_keyboard, get_admin_question_list_keyboard
    from app.models.attempt import ParticipantAnswer

    attempt_id = uuid.uuid4()
    q_id = uuid.uuid4()

    # 1. Verify get_question_keyboard callback data lengths are <= 64 bytes
    kb = get_question_keyboard(attempt_id, q_id, display_order=1, total_questions=20, selected_opt="A")
    for row in kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode("utf-8")) <= 64, f"Callback {btn.callback_data} exceeds 64 bytes"
            assert len(btn.callback_data) <= 64

    # 2. Verify get_admin_question_list_keyboard delete button length is <= 64 bytes
    mock_q = MagicMock(id=uuid.uuid4(), order_index=1, question_text="What is the capital of Ethiopia?")
    admin_kb = get_admin_question_list_keyboard(comp_id=uuid.uuid4(), page=1, total_pages=1, questions=[mock_q])
    for row in admin_kb.inline_keyboard:
        for btn in row:
            assert len(btn.callback_data.encode("utf-8")) <= 64, f"Admin callback {btn.callback_data} exceeds 64 bytes"

    # 3. Verify compact answer submission end-to-end via cb_answer
    participant_user = User(id=884422, first_name="Amina", is_bot=False)
    p = await ParticipantService.register_or_bind_participant(
        db=db_session,
        telegram_user_id=884422,
        membership_id="EMYC/8844220/2026",
        telegram_username="amina_test",
        verifier=MockMembershipVerificationService(),
    )

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Compact Answer Exam",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=10),
        closes_at=now + timedelta(days=2),
        duration_minutes=30,
        question_count=1,
    )
    db_session.add(comp)
    await db_session.flush()

    q = CompetitionQuestion(
        competition_id=comp.id,
        order_index=1,
        question_text="Sample question text?",
        options={"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"},
        correct_option="B",
    )
    db_session.add(q)
    await db_session.commit()

    # Start attempt
    attempt = await CompetitionService.start_attempt(db_session, comp.id, p.id)

    # Participant clicks option "A" using the compact callback: ans:<attempt_id>:<display_order>:<opt>
    compact_cb_data = f"ans:{attempt.id}:1:A"
    assert len(compact_cb_data.encode("utf-8")) <= 64

    query = MagicMock()
    query.data = compact_cb_data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_user = participant_user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_answer(update, context)

    # Verify answer recorded in DB
    ans_stmt = select(ParticipantAnswer).where(
        ParticipantAnswer.attempt_id == attempt.id,
        ParticipantAnswer.question_id == q.id,
    )
    ans = (await db_session.execute(ans_stmt)).scalar_one_or_none()
    assert ans is not None
    assert ans.selected_display_option == "A"


@pytest.mark.asyncio
async def test_participant_review_answers_and_bidirectional_navigation(db_session: AsyncSession):
    """Verifies:
    1. Bidirectional question navigation (Prev and Next buttons).
    2. Changing an answer during the exam updates the stored answer.
    3. In-exam review screen (cb_exam_review) displays answered/unanswered counts and jump buttons.
    """
    from app.bot.keyboards import get_question_keyboard
    from app.bot.handlers.participant import cb_exam_review
    from app.models.attempt import ParticipantAnswer

    participant_user = User(id=992211, first_name="Bilal", is_bot=False)
    p = await ParticipantService.register_or_bind_participant(
        db=db_session,
        telegram_user_id=992211,
        membership_id="EMYC/9922110/2026",
        telegram_username="bilal_test",
        verifier=MockMembershipVerificationService(),
    )

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Navigation & Review Exam",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(days=1),
        duration_minutes=20,
        question_count=3,
    )
    db_session.add(comp)
    await db_session.flush()

    for idx in range(1, 4):
        q = CompetitionQuestion(
            competition_id=comp.id,
            order_index=idx,
            question_text=f"Question number {idx}?",
            options={"A": "Option A", "B": "Option B", "C": "Option C", "D": "Option D"},
            correct_option="A",
        )
        db_session.add(q)
    await db_session.commit()

    attempt = await CompetitionService.start_attempt(db_session, comp.id, p.id)

    # 1. Verify Keyboard Navigation Buttons:
    # Q1: Has Next, No Prev
    kb_q1 = get_question_keyboard(attempt.id, display_order=1, total_questions=3)
    q1_callbacks = [btn.callback_data for row in kb_q1.inline_keyboard for btn in row]
    assert any(cb.startswith(f"q:nav:{attempt.id}:2") for cb in q1_callbacks)
    assert not any(cb.startswith(f"q:nav:{attempt.id}:0") for cb in q1_callbacks)
    assert any(cb.startswith(f"q:rev_all:{attempt.id}") for cb in q1_callbacks)

    # Q2: Has both Prev (1) and Next (3)
    kb_q2 = get_question_keyboard(attempt.id, display_order=2, total_questions=3)
    q2_callbacks = [btn.callback_data for row in kb_q2.inline_keyboard for btn in row]
    assert any(cb.startswith(f"q:nav:{attempt.id}:1") for cb in q2_callbacks)
    assert any(cb.startswith(f"q:nav:{attempt.id}:3") for cb in q2_callbacks)

    # Q3: Has Prev (2), No Next
    kb_q3 = get_question_keyboard(attempt.id, display_order=3, total_questions=3)
    q3_callbacks = [btn.callback_data for row in kb_q3.inline_keyboard for btn in row]
    assert any(cb.startswith(f"q:nav:{attempt.id}:2") for cb in q3_callbacks)
    assert not any(cb.startswith(f"q:nav:{attempt.id}:4") for cb in q3_callbacks)

    # 2. Test Answering and Updating Answer:
    # Answer Q1 with 'A'
    res1 = await CompetitionService.submit_answer(db_session, attempt.id, display_order=1, selected_display_option="A")
    assert res1["status"] == "recorded"

    # Idempotent select 'A' again
    res1_dup = await CompetitionService.submit_answer(db_session, attempt.id, display_order=1, selected_display_option="A")
    assert res1_dup["status"] == "already_recorded"

    # Update Q1 answer to 'C'
    res1_update = await CompetitionService.submit_answer(db_session, attempt.id, display_order=1, selected_display_option="C")
    assert res1_update["status"] == "updated"

    # Verify updated in database
    ans_q1_stmt = (
        select(ParticipantAnswer.selected_display_option)
        .where(ParticipantAnswer.attempt_id == attempt.id)
    )
    saved_opt = (await db_session.execute(ans_q1_stmt)).scalar()
    assert saved_opt == "C"

    # 3. Test In-Exam Review Screen via cb_exam_review
    query_rev = MagicMock()
    query_rev.data = f"q:rev_all:{attempt.id}:1"
    query_rev.answer = AsyncMock()
    query_rev.edit_message_text = AsyncMock()

    update_rev = MagicMock(spec=Update)
    update_rev.effective_user = participant_user
    update_rev.callback_query = query_rev
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_exam_review(update_rev, context)

    query_rev.edit_message_text.assert_called_once()
    rev_text = query_rev.edit_message_text.call_args[0][0]
    assert "Exam Progress & Answer Review" in rev_text
    assert "1 / 3 answered" in rev_text
    assert "Option *C* ✅" in rev_text
    assert "_Unanswered_ ⚠️" in rev_text

    # Verify review keyboard contains quick jump buttons for all 3 questions
    rev_kb = query_rev.edit_message_text.call_args[1]["reply_markup"]
    jump_callbacks = [b.callback_data for row in rev_kb.inline_keyboard for b in row]
    assert f"q:nav:{attempt.id}:1" in jump_callbacks
    assert f"q:nav:{attempt.id}:2" in jump_callbacks
    assert f"q:nav:{attempt.id}:3" in jump_callbacks
    assert f"exam:submit:{attempt.id}" in jump_callbacks


@pytest.mark.asyncio
async def test_admin_results_db_level_aggregation(db_session: AsyncSession):
    """Verifies that cb_admin_results executes DB-level aggregation without fetching all attempt models."""
    from app.bot.handlers.admin import cb_admin_results

    admin_user = MagicMock(spec=User)
    admin_user.id = 999999
    admin_user.first_name = "Admin"

    # Setup competition
    now = now_utc()
    comp = Competition(
        title="Aggregate Statistics Comp",
        status=CompetitionStatus.CLOSED,
        opens_at=now - timedelta(hours=3),
        closes_at=now - timedelta(hours=1),
        duration_minutes=60,
        question_count=10,
    )
    db_session.add(comp)
    await db_session.commit()
    await db_session.refresh(comp)

    # Add 4 participants
    p_ids = []
    for i in range(4):
        p = Participant(
            telegram_user_id=88000 + i,
            membership_id=f"EMYC-AGG-{i}",
            telegram_username=f"participant_{i}",
            language_code="en",
        )
        db_session.add(p)
        await db_session.commit()
        await db_session.refresh(p)
        p_ids.append(p.id)

    # 1 Submitted (score 90)
    att1 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p_ids[0],
        status=AttemptStatus.SUBMITTED,
        score=90,
        started_at=now - timedelta(hours=2),
        submitted_at=now - timedelta(hours=1, minutes=30),
        deadline_at=now - timedelta(hours=1),
    )
    # 1 Submitted (score 70)
    att2 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p_ids[1],
        status=AttemptStatus.SUBMITTED,
        score=70,
        started_at=now - timedelta(hours=2),
        submitted_at=now - timedelta(hours=1, minutes=40),
        deadline_at=now - timedelta(hours=1),
    )
    # 1 In Progress
    att3 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p_ids[2],
        status=AttemptStatus.IN_PROGRESS,
        score=0,
        started_at=now - timedelta(minutes=30),
        deadline_at=now + timedelta(minutes=30),
    )
    # 1 Expired (score 40)
    att4 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p_ids[3],
        status=AttemptStatus.EXPIRED,
        score=40,
        started_at=now - timedelta(hours=2),
        deadline_at=now - timedelta(hours=1),
    )
    db_session.add_all([att1, att2, att3, att4])
    await db_session.commit()

    query = MagicMock()
    query.data = "admin:results"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query
    settings.ADMIN_TELEGRAM_IDS = f"{admin_user.id}"
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    await cb_admin_results(update, context)

    query.edit_message_text.assert_called_once()
    dash_text = query.edit_message_text.call_args[0][0]

    assert "Competition Results" in dash_text
    assert "Registered:* 4" in dash_text
    assert "Started:* 4" in dash_text
    assert "In Progress:* 1" in dash_text
    assert "Completed:* 2" in dash_text
    assert "Expired:* 1" in dash_text
    assert "Completion Rate:* 50.0%" in dash_text
    assert "Top Score:* 90" in dash_text


@pytest.mark.asyncio
async def test_strict_publication_gate_and_1_question_review_flow(db_session: AsyncSession):
    """Verifies server-side publication gate and 1-question-at-a-time answer review navigation."""
    from app.bot.handlers.participant import (
        cb_participant_answer_review,
        cb_participant_my_result,
    )

    now = now_utc()
    comp = Competition(
        title="Publication Gate Review Comp",
        status=CompetitionStatus.CLOSED,  # CLOSED, not yet PUBLISHED
        opens_at=now - timedelta(hours=2),
        closes_at=now - timedelta(hours=1),
        duration_minutes=60,
        question_count=2,
    )
    db_session.add(comp)
    await db_session.commit()
    await db_session.refresh(comp)

    # Questions
    q1 = CompetitionQuestion(
        competition_id=comp.id,
        order_index=1,
        question_text="First question text?",
        options={"A": "Alpha", "B": "Beta", "C": "Gamma", "D": "Delta"},
        correct_option="B",
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        order_index=2,
        question_text="Second question text?",
        options={"A": "One", "B": "Two", "C": "Three", "D": "Four"},
        correct_option="A",
    )
    db_session.add_all([q1, q2])
    await db_session.commit()
    await db_session.refresh(q1)
    await db_session.refresh(q2)

    # Participant & Attempt
    p_user = MagicMock(spec=User)
    p_user.id = 771122
    p_user.first_name = "Khadija"

    p = Participant(
        telegram_user_id=p_user.id,
        membership_id="EMYC-REV-01",
        telegram_username="khadija_a",
        language_code="en",
    )
    db_session.add(p)
    await db_session.commit()
    await db_session.refresh(p)

    attempt = ExamAttempt(
        competition_id=comp.id,
        participant_id=p.id,
        status=AttemptStatus.SUBMITTED,
        score=1,
        rank=1,
        started_at=now - timedelta(minutes=50),
        submitted_at=now - timedelta(minutes=20),
        deadline_at=now + timedelta(minutes=10),
    )
    db_session.add(attempt)
    await db_session.commit()
    await db_session.refresh(attempt)

    # Question order entries
    ord1 = AttemptQuestionOrder(
        attempt_id=attempt.id,
        question_id=q1.id,
        display_order=1,
        option_mapping={"A": "A", "B": "B", "C": "C", "D": "D"},
    )
    ord2 = AttemptQuestionOrder(
        attempt_id=attempt.id,
        question_id=q2.id,
        display_order=2,
        option_mapping={"A": "A", "B": "B", "C": "C", "D": "D"},
    )
    db_session.add_all([ord1, ord2])

    # Participant answered Q1 correctly (B) and left Q2 unanswered
    ans1 = ParticipantAnswer(
        attempt_id=attempt.id,
        question_id=q1.id,
        selected_display_option="B",
        resolved_canonical_option="B",
        is_correct=True,
    )
    db_session.add(ans1)
    await db_session.commit()

    # 1. Verification of Publication Gate while CLOSED -> must raise ResultsNotPublishedError
    with pytest.raises(ResultsNotPublishedError):
        await ScoringAndRankingService.get_answer_review_question(db_session, comp.id, p.id, display_order=1)

    # Handler call while CLOSED should show results_pending_notice
    query = MagicMock()
    query.data = f"rev:q:{comp.id}:1"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_user = p_user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_participant_answer_review(update, context)
    query.edit_message_text.assert_called_once()
    pending_notice = query.edit_message_text.call_args[0][0]
    assert "Results Pending" in pending_notice

    # 2. Now transition competition to PUBLISHED
    comp.status = CompetitionStatus.PUBLISHED
    await db_session.commit()

    # 3. Test Question 1 Review (Answered Correctly)
    query_q1 = MagicMock()
    query_q1.data = f"rev:q:{comp.id}:1"
    query_q1.answer = AsyncMock()
    query_q1.edit_message_text = AsyncMock()
    update_q1 = MagicMock(spec=Update)
    update_q1.effective_user = p_user
    update_q1.callback_query = query_q1

    await cb_participant_answer_review(update_q1, context)
    query_q1.edit_message_text.assert_called_once()
    q1_text = query_q1.edit_message_text.call_args[0][0]
    assert "Question 1 of 2" in q1_text
    assert "First question text?" in q1_text
    assert "Your Answer:* *B.* Beta" in q1_text
    assert "Correct Answer:* *B.* Beta" in q1_text
    assert "Result:* ✅ Correct (+1 pts)" in q1_text

    # Verify navigation keyboard on Question 1 (no prev button on Q1, has Next button)
    kb1 = query_q1.edit_message_text.call_args[1]["reply_markup"]
    nav_callbacks = [b.callback_data for row in kb1.inline_keyboard for b in row]
    assert f"rev:q:{comp.id}:2" in nav_callbacks
    assert f"rev:my_result:{comp.id}" in nav_callbacks
    assert "menu:home" in nav_callbacks

    # 4. Test Question 2 Review (Unanswered)
    query_q2 = MagicMock()
    query_q2.data = f"rev:q:{comp.id}:2"
    query_q2.answer = AsyncMock()
    query_q2.edit_message_text = AsyncMock()
    update_q2 = MagicMock(spec=Update)
    update_q2.effective_user = p_user
    update_q2.callback_query = query_q2

    await cb_participant_answer_review(update_q2, context)
    query_q2.edit_message_text.assert_called_once()
    q2_text = query_q2.edit_message_text.call_args[0][0]
    assert "Question 2 of 2" in q2_text
    assert "Second question text?" in q2_text
    assert "Your Answer:* ⚪ Not answered" in q2_text
    assert "Correct Answer:* *A.* One" in q2_text
    assert "Result:* ⚪ Not answered (0 pts)" in q2_text

    # Verify navigation keyboard on Question 2 (has Prev button, no Next button on last Q)
    kb2 = query_q2.edit_message_text.call_args[1]["reply_markup"]
    nav2_callbacks = [b.callback_data for row in kb2.inline_keyboard for b in row]
    assert f"rev:q:{comp.id}:1" in nav2_callbacks
    assert f"rev:my_result:{comp.id}" in nav2_callbacks
    assert "menu:home" in nav2_callbacks

    # 5. Test Back to My Result via cb_participant_my_result
    query_res = MagicMock()
    query_res.data = f"rev:my_result:{comp.id}"
    query_res.answer = AsyncMock()
    query_res.edit_message_text = AsyncMock()
    update_res = MagicMock(spec=Update)
    update_res.effective_user = p_user
    update_res.callback_query = query_res

    await cb_participant_my_result(update_res, context)
    query_res.edit_message_text.assert_called_once()
    my_res_text = query_res.edit_message_text.call_args[0][0]
    assert "EMYC Competition Results" in my_res_text
    assert "Score:* 1 / 2 (50.0%)" in my_res_text
    assert "Rank:* #1 of 1" in my_res_text
    res_kb = query_res.edit_message_text.call_args[1]["reply_markup"]
    res_callbacks = [b.callback_data for row in res_kb.inline_keyboard for b in row]
    assert f"rev:q:{comp.id}:1" in res_callbacks
    assert "menu:home" in res_callbacks


@pytest.mark.asyncio
async def test_render_participant_result_screen_graceful_fallbacks(db_session: AsyncSession):
    """Verifies that render_participant_result_screen handles null scores and Telegram Markdown failures cleanly."""
    from app.bot.handlers.participant import render_participant_result_screen

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Competition_With_Underscores_And*Stars",
        status=CompetitionStatus.PUBLISHED,
        opens_at=now - timedelta(days=2),
        closes_at=now - timedelta(days=1),
        duration_minutes=30,
        question_count=10,
    )
    part = Participant(
        telegram_user_id=889988,
        membership_id="EMYC/889988/2026",
        language_code="en",
    )

    db_session.add_all([comp, part])
    await db_session.flush()

    att = ExamAttempt(
        competition_id=comp.id,
        participant_id=part.id,
        status=AttemptStatus.FINALIZED,
        started_at=now - timedelta(hours=3),
        deadline_at=now - timedelta(hours=2),
        score=None,  # Null score to test division guard
        rank=None,   # Null rank
        completion_seconds=None,
    )
    db_session.add(att)
    await db_session.commit()

    # Mock query where first edit_message_text with MARKDOWN raises Telegram error
    mock_query = MagicMock()
    mock_query.edit_message_text = AsyncMock(side_effect=[
        Exception("BadRequest: Can't parse entities"),
        None,  # Second call (plain text fallback) succeeds
    ])

    await render_participant_result_screen(
        target=mock_query,
        competition_id=comp.id,
        participant_id=part.id,
        lang="en",
        user_display_name="User_With_Special*Chars",
    )

    # edit_message_text should be called twice: 1st failed, 2nd succeeded with plain text
    assert mock_query.edit_message_text.call_count == 2
    plain_call = mock_query.edit_message_text.call_args_list[1]
    plain_text = plain_call[0][0]
    assert "Score: 0 / 10" in plain_text
    assert "Rank: #-" in plain_text





