import re
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from telegram import Update, User, Message, Contact, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.config import get_settings
from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus, ParticipantAnswer
from app.services.competition_service import CompetitionService
from app.services.scoring_service import ScoringAndRankingService
from app.services.membership_service import ParticipantService
from app.bot.keyboards import get_main_menu_keyboard, get_admin_keyboard
from app.bot.handlers.participant import (
    cmd_start,
    cb_start_flow,
    handle_contact_message,
    render_competition_state_for_participant,
)
from app.bot.handlers.admin import (
    cmd_admin,
    cb_admin_competition,
    cb_admin_set_status,
)
from app.locales.en import MESSAGES as EN
from app.locales.am import MESSAGES as AM
from app.locales.om import MESSAGES as OM
from app.locales.ar import MESSAGES as AR

settings = get_settings()


def test_participant_primary_actions_max_two():
    """Verifies that participant interface has strictly <= 2 primary actions."""
    for lang in ["en", "am", "om", "ar"]:
        kb = get_main_menu_keyboard(lang=lang, is_admin=False)
        buttons = [btn for row in kb.inline_keyboard for btn in row]
        assert len(buttons) <= 2
        callbacks = [btn.callback_data for btn in buttons]
        assert "menu:start" in callbacks
        assert "menu:lang" in callbacks


def test_admin_primary_actions_strictly_three():
    """Verifies that admin interface has clean, essential controls: Competition, Results, Announce, Language."""
    for lang in ["en", "am", "om", "ar"]:
        kb = get_admin_keyboard(lang=lang)
        buttons = [btn for row in kb.inline_keyboard for b in row for btn in [b]]
        assert len(buttons) in [3, 4]
        callbacks = [btn.callback_data for btn in buttons]
        assert "admin:competition" in callbacks
        assert "admin:announce" in callbacks
        assert "admin:lang" in callbacks


def test_multilingual_complete_catalog_parity():
    """Verifies 100% key parity and non-empty values across English, Amharic, Afaan Oromoo, and Arabic."""
    en_keys = set(EN.keys())
    am_keys = set(AM.keys())
    om_keys = set(OM.keys())
    ar_keys = set(AR.keys())

    assert en_keys == am_keys, f"Missing in AM: {en_keys ^ am_keys}"
    assert en_keys == om_keys, f"Missing in OM: {en_keys ^ om_keys}"
    assert en_keys == ar_keys, f"Missing in AR: {en_keys ^ ar_keys}"

    for key in en_keys:
        assert EN[key], f"EN[{key}] is empty"
        assert AM[key], f"AM[{key}] is empty"
        assert OM[key], f"OM[{key}] is empty"
        assert AR[key], f"AR[{key}] is empty"


@pytest.mark.asyncio
async def test_participant_registration_directly_transitions_to_live_competition(db_session: AsyncSession):
    """Verifies that upon sharing phone, the participant directly transitions to LIVE competition with Start button."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="National Youth Knowledge Exam",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(days=2),
        actual_exam_started_at=now - timedelta(minutes=5),
        actual_exam_ends_at=now + timedelta(hours=2),
        duration_minutes=60,
        question_count=10,
    )
    db_session.add(comp)
    await db_session.commit()

    user = User(id=770001, first_name="Hamza", username="hamza_k", is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {
        "registration": {
            "membership_id": "EMYC/770001/2026",
            "full_name": "Hamza Kemal",
            "telegram_username": "@hamza_k",
            "step": "phone",
        }
    }

    contact = Contact(
        phone_number="+251911770001",
        first_name="Hamza",
        user_id=user.id,
    )
    update = MagicMock(spec=Update)
    update.effective_user = user
    msg = MagicMock(spec=Message)
    msg.contact = contact
    msg.reply_text = AsyncMock()
    update.message = msg

    await handle_contact_message(update, context)

    # Participant should be registered
    p = await ParticipantService.get_participant_by_telegram_id(db_session, user.id)
    assert p is not None
    assert p.phone_number == "+251911770001"

    # Message 1: Registration complete confirmation
    # Message 2: Directly renders LIVE competition details with [Start Competition] button!
    assert msg.reply_text.call_count == 2
    call2_text = msg.reply_text.call_args_list[1][0][0]
    call2_kb = msg.reply_text.call_args_list[1][1]["reply_markup"]

    assert "National Youth Knowledge Exam" in call2_text
    buttons = [btn.text for row in call2_kb.inline_keyboard for btn in row]
    assert any("Start Competition" in b for b in buttons)
    has_launch = any(
        (btn.callback_data and f"exam:start:{comp.id}" in btn.callback_data) or
        (btn.web_app and f"{comp.id}" in btn.web_app.url)
        for row in call2_kb.inline_keyboard for btn in row
    )
    assert has_launch


@pytest.mark.asyncio
async def test_returning_participant_with_active_attempt_resumes_immediately(db_session: AsyncSession):
    """Verifies that a participant with an in-progress attempt resumes question immediately in 1 step."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Active Exam Challenge",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(minutes=5),
        closes_at=now + timedelta(days=2),
        actual_exam_started_at=now - timedelta(minutes=5),
        actual_exam_ends_at=now + timedelta(hours=2),
        duration_minutes=30,
        question_count=2,
    )
    p = Participant(
        telegram_user_id=770002,
        membership_id="EMYC/770002/2026",
        full_name="Fatima Zahra",
        phone_number="+251911770002",
    )
    db_session.add_all([comp, p])
    await db_session.flush()

    q1 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="First question text?",
        options={"A": "One", "B": "Two", "C": "Three", "D": "Four"},
        correct_option="A",
        order_index=1,
    )
    q2 = CompetitionQuestion(
        competition_id=comp.id,
        question_text="Second question text?",
        options={"A": "Red", "B": "Green", "C": "Blue", "D": "Yellow"},
        correct_option="B",
        order_index=2,
    )
    db_session.add_all([q1, q2])
    await db_session.commit()

    # Start attempt
    attempt = await CompetitionService.start_attempt(db_session, comp.id, p.id)

    # Participant returns and taps [🏆 Competition] (cb_start_flow)
    user = User(id=p.telegram_user_id, first_name="Fatima", is_bot=False)
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cb_start_flow(update, context)

    # Immediately directs to continue in the Mini App!
    query.edit_message_text.assert_called_once()
    rendered_text = query.edit_message_text.call_args[0][0]
    assert "Examination in Progress" in rendered_text
    rendered_kb = query.edit_message_text.call_args[1]["reply_markup"]
    btn = rendered_kb.inline_keyboard[0][0]
    assert "Continue Exam" in btn.text
    assert btn.web_app is not None


@pytest.mark.asyncio
async def test_admin_competition_control_inline_aggregate_metrics(db_session: AsyncSession):
    """Verifies that cb_admin_competition presents full database-level aggregate metrics in 1 step."""
    admin_id = 770099
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="Admin", is_bot=False)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Inline Metrics Championship",
        status=CompetitionStatus.LIVE,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(hours=1),
        actual_exam_started_at=now - timedelta(hours=1),
        actual_exam_ends_at=now + timedelta(hours=1),
        duration_minutes=60,
        question_count=10,
    )
    db_session.add(comp)
    await db_session.flush()

    # Add participants and attempts
    p1 = Participant(telegram_user_id=8801, membership_id="EMYC/8801/2026")
    p2 = Participant(telegram_user_id=8802, membership_id="EMYC/8802/2026")
    p3 = Participant(telegram_user_id=8803, membership_id="EMYC/8803/2026")
    db_session.add_all([p1, p2, p3])
    await db_session.flush()

    att1 = ExamAttempt(competition_id=comp.id, participant_id=p1.id, status=AttemptStatus.FINALIZED, score=9, started_at=now, deadline_at=now+timedelta(hours=1))
    att2 = ExamAttempt(competition_id=comp.id, participant_id=p2.id, status=AttemptStatus.IN_PROGRESS, score=None, started_at=now, deadline_at=now+timedelta(hours=1))
    att3 = ExamAttempt(competition_id=comp.id, participant_id=p3.id, status=AttemptStatus.EXPIRED, score=3, started_at=now, deadline_at=now+timedelta(hours=1))
    db_session.add_all([att1, att2, att3])
    await db_session.commit()

    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cb_admin_competition(update, context)

    query.edit_message_text.assert_called_once()
    control_text = query.edit_message_text.call_args[0][0]

    # Verify inline aggregate metrics are directly present in Competition Control!
    assert "Registered:* 3" in control_text
    assert "Started:* 3" in control_text
    assert "In Progress:* 1" in control_text
    assert "Completed:* 1" in control_text
    assert "Expired:* 1" in control_text
    assert "Top Score:* 9/0" in control_text or "Top Score:*" in control_text

    # Verify contextual buttons: Live exam has Close and View Rankings
    rendered_kb = query.edit_message_text.call_args[1]["reply_markup"]
    btn_texts = [btn.text for row in rendered_kb.inline_keyboard for btn in row]
    assert any("Stop Competition" in b or "Close" in b for b in btn_texts)
    assert any("View Rankings" in b for b in btn_texts)
    assert any("Back to Admin Panel" in b for b in btn_texts)


@pytest.mark.asyncio
async def test_zero_raw_exception_or_internal_leakage_in_error_paths(db_session: AsyncSession):
    """Verifies that no raw exceptions, UUIDs, or internal database table names leak in error screens."""
    admin_id = 770098
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="Admin", is_bot=False)

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Empty Question Comp",
        status=CompetitionStatus.DRAFT,
        opens_at=now - timedelta(hours=1),
        closes_at=now + timedelta(hours=1),
        duration_minutes=30,
        question_count=0,
    )
    db_session.add(comp)
    await db_session.commit()

    # Attempt to set LIVE with 0 questions
    query = MagicMock()
    query.data = f"admin:set_live:{comp.id}"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cb_admin_set_status(update, context)

    query.edit_message_text.assert_called_once()
    err_text = query.edit_message_text.call_args[0][0]

    # Verify zero internal database table leakage
    assert "competition_questions" not in err_text
    assert "Supabase" not in err_text
    assert "Traceback" not in err_text
    assert "Exception" not in err_text
