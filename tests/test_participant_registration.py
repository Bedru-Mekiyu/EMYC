import pytest
from unittest.mock import AsyncMock, MagicMock
from telegram import Update, User, Message, Contact, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.participant import Participant
from app.services.membership_service import (
    ParticipantService,
    MockMembershipVerificationService,
    MembershipAlreadyBoundError,
)
from app.bot.handlers.participant import (
    cmd_start,
    cb_start_flow,
    handle_text_message,
    handle_contact_message,
)
from app.locales.translator import get_text


@pytest.mark.asyncio
async def test_successful_membership_verification_starts_registration_and_prompts_name(db_session: AsyncSession):
    """Verifies that valid membership ID verification initiates registration state and prompts for full name."""
    user = User(id=500001, first_name="Bilal", username="bilal_m", is_bot=False)
    update = MagicMock(spec=Update)
    update.effective_user = user
    msg = MagicMock(spec=Message)
    msg.text = "EMYC/500001/2026"
    msg.reply_text = AsyncMock()
    update.message = msg

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {"awaiting_membership": True}

    await handle_text_message(update, context)

    # 1. State updated
    assert context.user_data.get("awaiting_membership") is False
    reg_data = context.user_data.get("registration")
    assert reg_data is not None
    assert reg_data["membership_id"] == "EMYC/500001/2026"
    assert reg_data["step"] == "full_name"

    # 2. Name prompt sent
    msg.reply_text.assert_called_once()
    prompt_text = msg.reply_text.call_args[0][0]
    assert "Membership verified" in prompt_text
    assert "Please enter your full name" in prompt_text

    # 3. Participant row is bound to membership but registration profile (name & phone) is not yet set
    p = await ParticipantService.get_participant_by_telegram_id(db_session, user.id)
    assert p is not None
    assert p.membership_id == "EMYC/500001/2026"
    assert p.full_name is None
    assert p.phone_number is None


@pytest.mark.asyncio
async def test_full_name_validation_and_username_extraction(db_session: AsyncSession):
    """Verifies that full name validation rejects invalid names, stores valid name, extracts @username, and advances to phone step."""
    user = User(id=500002, first_name="Amina", username="amina_dev", is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {
        "registration": {
            "membership_id": "EMYC/500002/2026",
            "step": "full_name",
        }
    }

    # 1. Invalid name: too short
    update_short = MagicMock(spec=Update)
    update_short.effective_user = user
    msg_short = MagicMock(spec=Message)
    msg_short.text = "A"
    msg_short.reply_text = AsyncMock()
    update_short.message = msg_short

    await handle_text_message(update_short, context)
    assert context.user_data["registration"]["step"] == "full_name"
    assert "Please enter a valid full name" in msg_short.reply_text.call_args[0][0]

    # 2. Invalid name: bot command
    msg_cmd = MagicMock(spec=Message)
    msg_cmd.text = "/cancel"
    msg_cmd.reply_text = AsyncMock()
    update_short.message = msg_cmd

    await handle_text_message(update_short, context)
    assert context.user_data["registration"]["step"] == "full_name"
    assert "Please enter a valid full name" in msg_cmd.reply_text.call_args[0][0]

    # 3. Valid name: advances to phone step and extracts @username
    msg_valid = MagicMock(spec=Message)
    msg_valid.text = "Amina Mohammed"
    msg_valid.reply_text = AsyncMock()
    update_short.message = msg_valid

    await handle_text_message(update_short, context)
    reg_data = context.user_data["registration"]
    assert reg_data["step"] == "phone"
    assert reg_data["full_name"] == "Amina Mohammed"
    assert reg_data["telegram_username"] == "@amina_dev"

    # Contact keyboard sent
    msg_valid.reply_text.assert_called_once()
    reply_args = msg_valid.reply_text.call_args[1]
    assert "reply_markup" in reply_args
    assert isinstance(reply_args["reply_markup"], ReplyKeyboardMarkup)
    button = reply_args["reply_markup"].keyboard[0][0]
    assert button.request_contact is True
    assert "Share Phone Number" in button.text


@pytest.mark.asyncio
async def test_user_without_username_handled_gracefully(db_session: AsyncSession):
    """Verifies that a Telegram user without a username has telegram_username stored as None without inventing one."""
    user = User(id=500003, first_name="Zubair", username=None, is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {
        "registration": {
            "membership_id": "EMYC/500003/2026",
            "step": "full_name",
        }
    }

    update = MagicMock(spec=Update)
    update.effective_user = user
    msg = MagicMock(spec=Message)
    msg.text = "Zubair Ali"
    msg.reply_text = AsyncMock()
    update.message = msg

    await handle_text_message(update, context)
    reg_data = context.user_data["registration"]
    assert reg_data["step"] == "phone"
    assert reg_data["telegram_username"] is None


@pytest.mark.asyncio
async def test_third_party_contact_rejected_and_own_contact_accepted(db_session: AsyncSession):
    """Verifies that contacts with mismatched user_id are rejected, and legitimate contacts are saved in DB."""
    user = User(id=500004, first_name="Dawud", username="dawud_k", is_bot=False)
    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {
        "registration": {
            "membership_id": "EMYC/500004/2026",
            "full_name": "Dawud Kassim",
            "telegram_username": "@dawud_k",
            "step": "phone",
        }
    }

    # 1. Contact belonging to someone else (contact.user_id != user.id)
    spoofed_contact = Contact(
        phone_number="+251911999999",
        first_name="Imposter",
        user_id=999999,  # Mismatched ID!
    )
    update_spoofed = MagicMock(spec=Update)
    update_spoofed.effective_user = user
    msg_spoofed = MagicMock(spec=Message)
    msg_spoofed.contact = spoofed_contact
    msg_spoofed.reply_text = AsyncMock()
    update_spoofed.message = msg_spoofed

    await handle_contact_message(update_spoofed, context)

    # Must be rejected!
    msg_spoofed.reply_text.assert_called_once()
    assert "Please share your own phone number" in msg_spoofed.reply_text.call_args[0][0]
    # Registration still at phone step
    assert context.user_data["registration"]["step"] == "phone"
    # No DB record created
    p_none = await ParticipantService.get_participant_by_telegram_id(db_session, user.id)
    assert p_none is None

    # 2. Legitimate contact belonging to current user
    legit_contact = Contact(
        phone_number="0911 22 33 44",  # Raw format to test normalization
        first_name="Dawud",
        user_id=user.id,  # Matching owner!
    )
    update_legit = MagicMock(spec=Update)
    update_legit.effective_user = user
    msg_legit = MagicMock(spec=Message)
    msg_legit.contact = legit_contact
    msg_legit.reply_text = AsyncMock()
    update_legit.message = msg_legit

    await handle_contact_message(update_legit, context)

    # 3. Verified DB record persisted
    p = await ParticipantService.get_participant_by_telegram_id(db_session, user.id)
    assert p is not None
    assert p.telegram_user_id == user.id
    assert p.membership_id == "EMYC/500004/2026"
    assert p.full_name == "Dawud Kassim"
    assert p.telegram_username == "@dawud_k"
    assert p.phone_number == "+0911223344"
    assert p.is_active is True

    # 4. Registration state cleared
    assert "registration" not in context.user_data

    # 5. Dismiss reply keyboard & render minimal main menu
    assert msg_legit.reply_text.call_count == 2
    # First message: Registration complete + ReplyKeyboardRemove
    call1 = msg_legit.reply_text.call_args_list[0]
    assert "Registration complete" in call1[0][0]
    assert isinstance(call1[1].get("reply_markup"), ReplyKeyboardRemove)

    # Second message: Direct transition to competition state
    call2 = msg_legit.reply_text.call_args_list[1]
    assert "competition" in call2[0][0].lower()
    kb = call2[1].get("reply_markup")
    assert kb is not None
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert "🏆 Competition" in buttons
    assert "🌐 Change Language" in buttons
    assert len(buttons) == 2


@pytest.mark.asyncio
async def test_already_registered_participant_skips_registration(db_session: AsyncSession):
    """Verifies that an already registered participant submitting membership ID skips registration and sees main menu."""
    user = User(id=500005, first_name="Khadija", username="khadija_p", is_bot=False)

    # Pre-register participant in DB
    existing_p = Participant(
        telegram_user_id=user.id,
        membership_id="EMYC/500005/2026",
        full_name="Khadija Omar",
        phone_number="+251912345678",
        telegram_username="@khadija_p",
    )
    db_session.add(existing_p)
    await db_session.commit()

    update = MagicMock(spec=Update)
    update.effective_user = user
    msg = MagicMock(spec=Message)
    msg.text = "EMYC/500005/2026"
    msg.reply_text = AsyncMock()
    update.message = msg

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context.user_data = {"awaiting_membership": True}

    await handle_text_message(update, context)

    # Does not start registration
    assert "registration" not in context.user_data
    assert context.user_data.get("awaiting_membership") is None

    # Shows already registered confirmation and main menu
    msg.reply_text.assert_called_once()
    sent_text = msg.reply_text.call_args[0][0]
    assert "already registered" in sent_text.lower()
    kb = msg.reply_text.call_args[1]["reply_markup"]
    buttons = [b.text for row in kb.inline_keyboard for b in row]
    assert "🏆 Competition" in buttons
    assert "🌐 Change Language" in buttons


@pytest.mark.asyncio
async def test_registration_interruption_and_resumption(db_session: AsyncSession):
    """Verifies that if a user leaves and returns via /start or [🏆 Competition], registration resumes at the missing field."""
    user = User(id=500006, first_name="Farhan", username="farhan_t", is_bot=False)

    # Scenario A: Interrupted at full_name step
    context_name = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context_name.user_data = {
        "registration": {
            "membership_id": "EMYC/500006/2026",
            "step": "full_name",
        }
    }
    update_start_name = MagicMock(spec=Update)
    update_start_name.effective_user = user
    update_start_name.callback_query = None
    msg_name = MagicMock(spec=Message)
    msg_name.reply_text = AsyncMock()
    update_start_name.message = msg_name

    await cmd_start(update_start_name, context_name)
    msg_name.reply_text.assert_called_once()
    assert "Please enter your full name" in msg_name.reply_text.call_args[0][0]

    # Scenario B: Interrupted at phone step
    context_phone = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    context_phone.user_data = {
        "registration": {
            "membership_id": "EMYC/500006/2026",
            "full_name": "Farhan Ahmed",
            "telegram_username": "@farhan_t",
            "step": "phone",
        }
    }
    update_start_phone = MagicMock(spec=Update)
    update_start_phone.effective_user = user
    update_start_phone.callback_query = None
    msg_phone = MagicMock(spec=Message)
    msg_phone.reply_text = AsyncMock()
    update_start_phone.message = msg_phone

    await cmd_start(update_start_phone, context_phone)
    msg_phone.reply_text.assert_called_once()
    assert "Please share your phone number" in msg_phone.reply_text.call_args[0][0]
    assert isinstance(msg_phone.reply_text.call_args[1].get("reply_markup"), ReplyKeyboardMarkup)


@pytest.mark.asyncio
async def test_multilingual_registration_prompts():
    """Verifies that all 4 supported languages have clean registration translations."""
    for lang in ("en", "am", "om", "ar"):
        p_name = get_text("reg_membership_verified_prompt_name", lang, membership_id="EMYC/1234567/2026")
        assert len(p_name) > 10
        assert "EMYC/1234567/2026" in p_name

        p_phone = get_text("reg_share_phone_prompt", lang)
        assert len(p_phone) > 5

        btn_phone = get_text("reg_share_phone_btn", lang)
        assert "📱" in btn_phone

        p_done = get_text("reg_complete", lang)
        assert len(p_done) > 5

        p_already = get_text("reg_already_registered", lang, membership_id="EMYC/1234567/2026")
        assert "EMYC/1234567/2026" in p_already
