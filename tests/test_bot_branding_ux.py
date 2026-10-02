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

        # Verify main keyboard buttons
        kb = get_main_menu_keyboard(lang)
        assert len(kb.inline_keyboard) == 2
        # Row 1: Start button
        assert "▶️" in kb.inline_keyboard[0][0].text
        # Row 2: Language & Help
        assert "🌐" in kb.inline_keyboard[1][0].text
        assert "❓" in kb.inline_keyboard[1][1].text


def test_keyboard_layouts():
    """Verifies exact keyboard layouts for questions, results, and admin broadcast."""
    comp_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    q_id = uuid.uuid4()

    # 1. Question keyboard with 4 choices + navigation
    q_kb = get_question_keyboard(attempt_id, q_id, 1, 5, selected_opt="B", lang="en")
    assert len(q_kb.inline_keyboard) == 2
    opts_row = q_kb.inline_keyboard[0]
    assert len(opts_row) == 4
    assert opts_row[0].text == "A"
    assert opts_row[1].text == "🔘 B"
    assert opts_row[2].text == "C"
    assert opts_row[3].text == "D"
    assert "Next Question" in q_kb.inline_keyboard[1][0].text

    # 2. Final question shows Submit button
    q_final_kb = get_question_keyboard(attempt_id, q_id, 5, 5, selected_opt="D", lang="en")
    assert "Submit Exam" in q_final_kb.inline_keyboard[1][0].text

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
    assert "EMYC/4055828/2026" in prompt_text

    # 2. User submits valid membership ID
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
