import io
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update, User, Message, CallbackQuery, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus
from app.services.scoring_service import ScoringAndRankingService
from app.bot.handlers.admin import cb_admin_export_results, cb_admin_rankings
from app.bot.handlers.participant import get_user_lang, cb_select_language, _send_or_edit
from app.bot.keyboards import get_admin_results_keyboard, get_admin_rankings_keyboard
from app.core.config import get_settings

settings = get_settings()


@pytest.mark.asyncio
async def test_generate_results_csv_structure_and_formatting(db_session: AsyncSession):
    """Verifies that generate_results_csv builds valid CSV with headers, ranks, and participant details."""
    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Youth Leadership Challenge 2026",
        status=CompetitionStatus.PUBLISHED,
        opens_at=now - timedelta(days=2),
        closes_at=now - timedelta(days=1),
        duration_minutes=30,
        question_count=10,
    )
    p1 = Participant(
        telegram_user_id=880011,
        membership_id="EMYC/880011/2026",
        full_name="Dawud Ali",
        telegram_username="@dawud_ali",
        phone_number="+251911880011",
        language_code="en",
    )
    p2 = Participant(
        telegram_user_id=880022,
        membership_id="EMYC/880022/2026",
        full_name="Amina Omar",
        telegram_username="@amina_o",
        phone_number="+251922880022",
        language_code="am",
    )
    db_session.add_all([comp, p1, p2])
    await db_session.flush()

    att1 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p1.id,
        status=AttemptStatus.FINALIZED,
        score=9,
        correct_count=9,
        incorrect_count=1,
        completion_seconds=420.5,
        rank=1,
        started_at=now - timedelta(days=1, hours=2),
        deadline_at=now - timedelta(days=1, hours=1, minutes=30),
        submitted_at=now - timedelta(days=1, hours=1, minutes=53),
    )
    att2 = ExamAttempt(
        competition_id=comp.id,
        participant_id=p2.id,
        status=AttemptStatus.FINALIZED,
        score=7,
        correct_count=7,
        incorrect_count=3,
        completion_seconds=530.0,
        rank=2,
        started_at=now - timedelta(days=1, hours=2),
        deadline_at=now - timedelta(days=1, hours=1, minutes=30),
        submitted_at=now - timedelta(days=1, hours=1, minutes=51),
    )
    db_session.add_all([att1, att2])
    await db_session.commit()

    csv_content, filename = await ScoringAndRankingService.generate_results_csv(db_session, comp.id)

    assert "Youth_Leadership_Challenge_2026" in filename
    assert filename.endswith(".csv")

    lines = csv_content.strip().splitlines()
    header = lines[0]
    assert "Rank" in header
    assert "Full Name" in header
    assert "Membership ID" in header
    assert "Telegram Username" in header
    assert "Phone Number" in header
    assert "Score" in header

    # Row 1 (Rank 1: Dawud Ali)
    row1 = lines[1]
    assert "1,Dawud Ali,EMYC/880011/2026,@dawud_ali,+251911880011,9,10,9,1,420.5,FINALIZED" in row1

    # Row 2 (Rank 2: Amina Omar)
    row2 = lines[2]
    assert "2,Amina Omar,EMYC/880022/2026,@amina_o,+251922880022,7,10,7,3,530.0,FINALIZED" in row2


@pytest.mark.asyncio
async def test_cb_admin_export_results_delivers_document(db_session: AsyncSession):
    """Verifies that tapping [📥 Export Results CSV] delivers a document reply to the admin."""
    admin_id = 999888
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="SuperAdmin", is_bot=False, username="superadmin")

    now = datetime.now(timezone.utc)
    comp = Competition(
        title="Admin Export Test",
        status=CompetitionStatus.RESULTS_FINALIZED,
        opens_at=now - timedelta(days=1),
        closes_at=now - timedelta(hours=2),
        duration_minutes=20,
        question_count=5,
    )
    db_session.add(comp)
    await db_session.commit()

    query = MagicMock(spec=CallbackQuery)
    query.data = f"admin:export_results:{comp.id}"
    query.answer = AsyncMock()
    message = MagicMock(spec=Message)
    message.reply_document = AsyncMock()
    query.message = message

    update = MagicMock(spec=Update)
    update.effective_user = admin_user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_admin_export_results(update, context)

    query.answer.assert_called_once_with("Generating CSV export...", show_alert=False)
    message.reply_document.assert_called_once()
    kwargs = message.reply_document.call_args[1]
    assert "document" in kwargs
    assert kwargs["filename"].startswith("EMYC_Results_Admin_Export_Test_")
    assert kwargs["filename"].endswith(".csv")
    assert "Official Results Export" in kwargs["caption"]


def test_export_buttons_present_on_admin_keyboards():
    """Verifies that Export CSV button is present on Results and Rankings keyboards."""
    test_id = uuid.uuid4()
    # Results keyboard in finalized status
    kb_res = get_admin_results_keyboard(comp_id=test_id, status="RESULTS_FINALIZED", lang="en")
    cb_data = [btn.callback_data for row in kb_res.inline_keyboard for btn in row]
    assert f"admin:export_results:{test_id}" in cb_data

    # Rankings keyboard with comp_id
    kb_rank = get_admin_rankings_keyboard(lang="en", comp_id=test_id)
    rank_cb_data = [btn.callback_data for row in kb_rank.inline_keyboard for btn in row]
    assert f"admin:export_results:{test_id}" in rank_cb_data


@pytest.mark.asyncio
async def test_pre_registration_language_selection_persists_in_context():
    """Verifies that selecting a language before registration stores it in session context and get_user_lang reads it."""
    user = User(id=991122, first_name="Kadir", is_bot=False)
    query = MagicMock(spec=CallbackQuery)
    query.data = "lang:am"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock(spec=Update)
    update.effective_user = user
    update.callback_query = query

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cb_select_language(update, context)

    # Session context now retains "am"
    assert context.user_data.get("user_lang") == "am"

    # get_user_lang returns "am" for unregistered user
    lang = await get_user_lang(user.id, context=context)
    assert lang == "am"


@pytest.mark.asyncio
async def test_send_or_edit_cleanly_suppresses_message_not_modified():
    """Verifies that _send_or_edit silently ignores 'Message is not modified' error without re-raising or crashing."""
    target = MagicMock()
    target.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified: specified new message content is the same"))

    # Should exit cleanly without throwing exception
    await _send_or_edit(target, "Identical content")
    target.edit_message_text.assert_called_once()
