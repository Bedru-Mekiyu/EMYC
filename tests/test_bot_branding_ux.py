import uuid
from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import Update, User, Message, CallbackQuery
from telegram.ext import ContextTypes

from app.core.config import get_settings
from app.locales.translator import get_text
from app.bot.keyboards import (
    get_main_menu_keyboard,
    get_language_keyboard,
    get_start_exam_keyboard,
    get_question_keyboard,
    get_results_keyboard,
    get_admin_keyboard,
    get_admin_confirm_announcement_keyboard,
)
from app.bot.handlers.participant import (
    cmd_start,
    cmd_help,
    handle_text_message,
    cb_start_flow,
    render_question_screen,
)
from app.bot.handlers.admin import (
    cmd_admin,
    cb_admin_announce,
    cb_admin_announce_confirm,
)
from app.models.participant import Participant
from app.models.competition import Competition, CompetitionStatus
from datetime import datetime, timezone, timedelta

settings = get_settings()


def test_multilingual_welcome_branding_and_greeting():
    """Verifies that all 4 supported languages have proper EMYC greeting and branding."""
    languages = [
        ("en", "Assalamu Alaikum, Bilal! 👋", "EMYC Competition"),
        ("am", "አሰላሙ ዐለይኩም Bilal! 👋", "የኢ.ሙ.ወ.ም ውድድር"),
        ("om", "Assalaamu Aleykum, Bilal! 👋", "Dorgommii EMYC"),
        ("ar", "السلام عليكم ورحمة الله، Bilal! 👋", "مسابقة المجلس الإثيوبي للشباب المسلم"),
    ]

    for lang, expected_greeting, expected_brand in languages:
        text = get_text("welcome", lang, name="Bilal")
        assert expected_greeting in text
        assert expected_brand in text

        # Verify main keyboard buttons: strictly 2 buttons: [Change Language], [Competition]
        kb = get_main_menu_keyboard(lang)
        assert len(kb.inline_keyboard) == 2
        # Row 1: Change Language
        assert "🌐" in kb.inline_keyboard[0][0].text
        # Row 2: Competition
        assert "🏆" in kb.inline_keyboard[1][0].text


def test_keyboard_layouts():
    """Verifies exact keyboard layouts for questions, results, and admin broadcast."""
    comp_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    q_id = uuid.uuid4()

    # 1. Question keyboard with 4 choices + navigation and Review/Finish Examination
    q_kb = get_question_keyboard(attempt_id, q_id, 1, 5, selected_opt="B", lang="en")
    assert len(q_kb.inline_keyboard) == 3
    opts_row = q_kb.inline_keyboard[0]
    assert len(opts_row) == 4
    assert opts_row[0].text == "A"
    assert opts_row[1].text == "✅ B"
    assert opts_row[2].text == "C"
    assert opts_row[3].text == "D"
    assert "Next Question" in q_kb.inline_keyboard[1][0].text
    assert "Review All Answers" in q_kb.inline_keyboard[2][0].text
    assert "Finish Examination" in q_kb.inline_keyboard[2][1].text

    # 2. Final question shows Finish Examination button
    q_final_kb = get_question_keyboard(attempt_id, q_id, 5, 5, selected_opt="D", lang="en")
    assert any("Finish Examination" in btn.text for row in q_final_kb.inline_keyboard for btn in row)

    # 3. Results keyboard segregated buttons
    res_kb = get_results_keyboard(comp_id, correct_count=8, incorrect_count=2, lang="en")
    assert len(res_kb.inline_keyboard) == 3
    assert "Correct Answers (8)" in res_kb.inline_keyboard[0][0].text
    assert "Incorrect Answers (2)" in res_kb.inline_keyboard[1][0].text
    assert "Main Menu" in res_kb.inline_keyboard[2][0].text

    # 4. Admin confirmation keyboard
    confirm_kb = get_admin_confirm_announcement_keyboard("en")
    assert "Confirm Broadcast" in confirm_kb.inline_keyboard[0][0].text
    assert "Cancel" in confirm_kb.inline_keyboard[0][1].text


@pytest.mark.asyncio
async def test_cmd_start_renders_emyc_welcome(db_session: AsyncSession):
    """Verifies /start command sends personalized EMYC welcome."""
    user = User(id=888123, first_name="Amina", is_bot=False)
    update = MagicMock(spec=Update)
    update.effective_user = user
    update.callback_query = None
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cmd_start(update, context)

    update.message.reply_text.assert_called_once()
    sent_text = update.message.reply_text.call_args[0][0]
    assert "Assalamu Alaikum, Amina! 👋" in sent_text
    assert "EMYC Competition" in sent_text
    assert "Ethiopian Muslim Youth Council" in sent_text


@pytest.mark.asyncio
async def test_membership_verification_flow(db_session: AsyncSession):
    """Verifies membership prompt and registration flow with EMYC format guidance."""
    user = User(id=777001, first_name="Dawud", username="dawud_test", is_bot=False)

    # 1. Start flow unverified -> prompts for membership
    update_cb = MagicMock(spec=Update)
    update_cb.effective_user = user
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update_cb.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cb_start_flow(update_cb, context)
    assert context.user_data.get("awaiting_membership") is True
    prompt_text = query.edit_message_text.call_args[0][0]
    assert "EMYC Membership Verification" in prompt_text
    assert "membership.emyc.et" in prompt_text
    # Ensure format example is confidential and NOT leaked to the user
    assert "4055828" not in prompt_text

    # 2. User submits invalid membership ID format -> gives helpful error without revealing format
    update_msg_invalid = MagicMock(spec=Update)
    update_msg_invalid.effective_user = user
    msg_inv = MagicMock()
    msg_inv.text = "INVALID_ID_123"
    msg_inv.reply_text = AsyncMock()
    update_msg_invalid.message = msg_inv

    await handle_text_message(update_msg_invalid, context)
    msg_inv.reply_text.assert_called_once()
    assert "Invalid Membership ID" in msg_inv.reply_text.call_args[0][0]
    assert "4055828" not in msg_inv.reply_text.call_args[0][0]

    # 3. User submits valid membership ID
    update_msg = MagicMock(spec=Update)
    update_msg.effective_user = user
    msg = MagicMock()
    msg.text = "EMYC/777001/2026"
    msg.reply_text = AsyncMock()
    update_msg.message = msg

    await handle_text_message(update_msg, context)
    assert context.user_data.get("awaiting_membership") is False
    assert msg.reply_text.call_count >= 1
    confirm_text = msg.reply_text.call_args_list[0][0][0]
    assert "Membership Verified!" in confirm_text
    assert "EMYC/777001/2026" in confirm_text


@pytest.mark.asyncio
async def test_admin_announcement_confirmation_flow(db_session: AsyncSession):
    """Verifies the two-step admin announcement confirmation modal and broadcast."""
    admin_id = 999111
    settings.ADMIN_TELEGRAM_IDS = str(admin_id)
    admin_user = User(id=admin_id, first_name="Admin", is_bot=False)

    # 1. Admin triggers announcement prompt
    update_cb = MagicMock(spec=Update)
    update_cb.effective_user = admin_user
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update_cb.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cb_admin_announce(update_cb, context)
    assert context.user_data.get("awaiting_announcement") is True
    assert "Send EMYC Operational Announcement" in query.edit_message_text.call_args[0][0]

    # 2. Admin submits announcement text -> Receives preview with confirmation keyboard
    update_msg = MagicMock(spec=Update)
    update_msg.effective_user = admin_user
    msg = MagicMock()
    msg.text = "Important: Competition opens at 14:00 UTC today."
    msg.reply_text = AsyncMock()
    update_msg.message = msg

    await handle_text_message(update_msg, context)
    assert context.user_data.get("awaiting_announcement") is False
    assert context.user_data.get("pending_announcement") == msg.text

    msg.reply_text.assert_called_once()
    preview_text = msg.reply_text.call_args[0][0]
    assert "Confirm Announcement Broadcast" in preview_text
    assert "Important: Competition opens at 14:00 UTC today." in preview_text

    # 3. Admin clicks [Confirm Broadcast]
    confirm_query = MagicMock()
    confirm_query.answer = AsyncMock()
    confirm_query.edit_message_text = AsyncMock()
    update_confirm = MagicMock(spec=Update)
    update_confirm.effective_user = admin_user
    update_confirm.callback_query = confirm_query

    mock_bot = MagicMock()
    mock_bot.send_message = AsyncMock()
    context.bot = mock_bot

    await cb_admin_announce_confirm(update_confirm, context)

    confirm_query.edit_message_text.assert_called_once()
    broadcast_result = confirm_query.edit_message_text.call_args[0][0]
    assert "Announcement Broadcast Completed!" in broadcast_result
    assert context.user_data.get("pending_announcement") is None


@pytest.mark.asyncio
async def test_admin_direct_start_and_competition_setup(db_session: AsyncSession):
    """Verifies that admins automatically receive Admin Dashboard on /start,
    can setup sample competitions with 1 click, and can switch to participant view."""
    from app.bot.handlers.participant import cmd_start
    from app.bot.handlers.admin import cb_admin_competition, cb_admin_setup_sample, cb_admin_to_participant
    from app.core.config import settings
    from telegram import User

    admin_id = 99912345
    settings.ADMIN_TELEGRAM_IDS = f"{admin_id}"
    admin_user = User(id=admin_id, first_name="AdminUser", is_bot=False)

    # 1. Admin sends /start -> automatically opens Admin Dashboard
    update_start = MagicMock(spec=Update)
    update_start.effective_user = admin_user
    msg_start = MagicMock()
    msg_start.reply_text = AsyncMock()
    update_start.message = msg_start
    update_start.callback_query = None
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {}

    await cmd_start(update_start, context)
    msg_start.reply_text.assert_called_once()
    start_text = msg_start.reply_text.call_args[0][0]
    assert "EMYC Competition Admin" in start_text
    start_kb = msg_start.reply_text.call_args[1]["reply_markup"]
    btn_texts = [b.text for row in start_kb.inline_keyboard for b in row]
    assert len(btn_texts) == 3
    assert "🏆 Competition" in btn_texts
    assert "📢 Announcement" in btn_texts
    assert "🌐 Language" in btn_texts

    # 2. Admin clicks Competition on empty DB -> shows Create Competition button (no sample/dev buttons)
    query_comp = MagicMock()
    query_comp.answer = AsyncMock()
    query_comp.edit_message_text = AsyncMock()
    update_comp = MagicMock(spec=Update)
    update_comp.effective_user = admin_user
    update_comp.callback_query = query_comp

    await cb_admin_competition(update_comp, context)
    query_comp.edit_message_text.assert_called_once()
    comp_text = query_comp.edit_message_text.call_args[0][0]
    assert "No competition is currently configured" in comp_text
    comp_kb = query_comp.edit_message_text.call_args[1]["reply_markup"]
    comp_btn_texts = [b.text for row in comp_kb.inline_keyboard for b in row]
    assert "➕ Create Competition" in comp_btn_texts
    assert not any("Setup Sample" in t for t in comp_btn_texts)

    # 3. Admin clicks [Setup Sample Competition (Live)] -> creates competition and sets it to LIVE
    query_setup = MagicMock()
    query_setup.data = "admin:setup_sample:live"
    query_setup.answer = AsyncMock()
    query_setup.edit_message_text = AsyncMock()
    update_setup = MagicMock(spec=Update)
    update_setup.effective_user = admin_user
    update_setup.callback_query = query_setup

    await cb_admin_setup_sample(update_setup, context)
    query_setup.edit_message_text.assert_called_once()
    setup_text = query_setup.edit_message_text.call_args[0][0]
    assert "Sample Competition Created!" in setup_text
    assert "LIVE" in setup_text

    # 4. Admin clicks [Switch to Participant View] -> renders participant view with Admin Dashboard button
    query_to_p = MagicMock()
    query_to_p.answer = AsyncMock()
    query_to_p.edit_message_text = AsyncMock()
    update_to_p = MagicMock(spec=Update)
    update_to_p.effective_user = admin_user
    update_to_p.callback_query = query_to_p

    await cb_admin_to_participant(update_to_p, context)
    query_to_p.edit_message_text.assert_called_once()
    p_text = query_to_p.edit_message_text.call_args[0][0]
    assert "EMYC Competition" in p_text
    p_kb = query_to_p.edit_message_text.call_args[1]["reply_markup"]
    p_btn_texts = [b.text for row in p_kb.inline_keyboard for b in row]
    assert "⚙️ Admin Dashboard" in p_btn_texts


def test_human_readable_schedule_and_duration_formatters():
    """Verifies that format_schedule_window and format_meta_line output human-friendly strings."""
    from app.core.time_utils import (
        format_schedule_window,
        format_meta_line,
        format_friendly_duration,
        format_friendly_questions,
    )

    # 1. Test duration formatting
    assert format_friendly_duration(120) == "2 hours"
    assert format_friendly_duration(60) == "1 hour"
    assert format_friendly_duration(30) == "30 minutes"
    assert format_friendly_duration(90) == "1 hr 30 mins"
    assert format_friendly_duration(0) == "Untimed"

    # 2. Test question count
    assert format_friendly_questions(20) == "20 questions"
    assert format_friendly_questions(1) == "1 question"

    # 3. Test meta line: duration · questions
    assert format_meta_line(120, 20) == "2 hours · 20 questions"
    assert format_meta_line(30, 5) == "30 minutes · 5 questions"

    # 4. Test schedule window
    now = datetime(2026, 10, 4, 11, 0, tzinfo=timezone.utc)  # 2:00 PM EAT (UTC+3)
    tomorrow = now + timedelta(days=1)
    window_multiday = format_schedule_window(now, tomorrow, now=now)
    assert window_multiday == "Today, 2:00 PM → Tomorrow, 2:00 PM"

    same_day_end = now + timedelta(hours=2)
    window_sameday = format_schedule_window(now, same_day_end, now=now)
    assert window_sameday == "Today, 2:00 PM → 4:00 PM"

    # 5. Test parse_window_string & format_timedelta_friendly
    from app.core.time_utils import parse_window_string, format_timedelta_friendly
    assert parse_window_string("2h") == timedelta(hours=2)
    assert parse_window_string("4 hours") == timedelta(hours=4)
    assert parse_window_string("3d") == timedelta(days=3)
    assert parse_window_string("5 days") == timedelta(days=5)
    assert parse_window_string("1d 12h") == timedelta(days=1, hours=12)
    assert parse_window_string("2 weeks") == timedelta(weeks=2)
    assert parse_window_string("30 mins") == timedelta(minutes=30)
    assert parse_window_string("6") == timedelta(hours=6)
    assert parse_window_string("invalid_input") is None
    assert parse_window_string("0") is None

    assert format_timedelta_friendly(timedelta(hours=4)) == "4 hours"
    assert format_timedelta_friendly(timedelta(days=2)) == "2 days"
    assert format_timedelta_friendly(timedelta(days=1, hours=12)) == "1d 12h"
    assert format_timedelta_friendly(timedelta(minutes=30)) == "30 minutes"


@pytest.mark.asyncio
async def test_participant_exam_info_screen_human_readable(db_session: AsyncSession):
    """Verifies that the participant exam info screen renders clean human-readable schedule and format."""
    now = datetime(2026, 10, 4, 11, 0, tzinfo=timezone.utc)
    comp = Competition(
        title="Ramadan Cup 2026",
        status=CompetitionStatus.LIVE,
        opens_at=now,
        closes_at=now + timedelta(days=1),
        duration_minutes=120,
        question_count=20,
    )
    db_session.add(comp)
    await db_session.flush()

    p = Participant(
        telegram_user_id=888777,
        telegram_username="bilal_tester",
        membership_id="EMYC/888777/2026",
        language_code="en",
    )
    db_session.add(p)
    await db_session.commit()

    user = User(id=888777, first_name="Bilal", is_bot=False)
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock(spec=Update)
    update.effective_user = user
    update.callback_query = query
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)

    await cb_start_flow(update, context)

    query.edit_message_text.assert_called_once()
    rendered_text = query.edit_message_text.call_args[0][0]
    assert "🏆 *EMYC Competition*" in rendered_text
    assert "*Ramadan Cup 2026*" in rendered_text
    assert "2 hours · 20 questions" in rendered_text
    assert "→" in rendered_text
    assert "2026-10-04" not in rendered_text
    assert "UTC" not in rendered_text

