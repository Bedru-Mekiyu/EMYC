"""Production lifecycle, administrative control panel, and participant resumption tests."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
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
    cb_admin_participants,
    cb_admin_rankings,
    cb_admin_sys_status,
    cb_admin_create_comp_start,
    cb_admin_create_duration,
    cb_admin_create_schedule,
    cb_admin_create_questions,
    cb_admin_add_question_prompt,
)
from app.bot.handlers.participant import (
    cb_start_flow,
    cb_exam_start,
    cb_answer,
    handle_text_message,
)
from app.services.competition_service import CompetitionService
from app.services.membership_service import ParticipantService, MockMembershipVerificationService
from app.core.config import settings


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
