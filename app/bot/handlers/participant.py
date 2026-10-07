import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import select, and_, func

from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.locales.translator import get_text
from app.models.attempt import (
    ExamAttempt,
    AttemptStatus,
    AttemptQuestionOrder,
    ParticipantAnswer,
)
from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.services.membership_service import (
    ParticipantService,
    InvalidMembershipFormatError,
    MembershipNotFoundError,
    MembershipAlreadyBoundError,
    TelegramAccountAlreadyBoundError,
)
from app.services.competition_service import (
    CompetitionService,
    CompetitionError,
    CompetitionNotOpenError,
    AttemptExpiredError,
    DuplicateAttemptError,
    UnauthorizedAttemptAccessError,
)
from app.services.scoring_service import (
    ScoringAndRankingService,
    ResultsNotPublishedError,
)
from app.core.time_utils import (
    now_utc,
    ensure_utc,
    format_schedule_window,
    format_meta_line,
)
from app.bot.keyboards import (
    get_main_menu_keyboard,
    get_membership_prompt_keyboard,
    get_share_phone_keyboard,
    get_language_keyboard,
    get_start_exam_keyboard,
    get_question_keyboard,
    get_exam_review_keyboard,
    get_results_keyboard,
    get_answer_review_nav_keyboard,
    get_admin_confirm_announcement_keyboard,
    get_admin_create_comp_duration_keyboard,
    get_admin_create_comp_schedule_keyboard,
)


async def get_user_lang(user_id: int, context: Optional[ContextTypes.DEFAULT_TYPE] = None) -> str:
    """Helper to retrieve saved language for user or fallback to 'en'.
    Checks session context first for instant pre-registration responsiveness.
    """
    if context and hasattr(context, "user_data") and context.user_data and context.user_data.get("user_lang"):
        return context.user_data["user_lang"]
    try:
        async with AsyncSessionLocal() as db:
            p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
            if p and p.language_code:
                if context and hasattr(context, "user_data") and context.user_data is not None:
                    context.user_data["user_lang"] = p.language_code
                return p.language_code
    except Exception as e:
        logger.warning(f"Error retrieving language for user {user_id}: {e}")
    return "en"


def get_participant_display_name(update: Update) -> str:
    """Extracts first name or username for greeting, sanitized for safe Markdown rendering."""
    user = update.effective_user
    if not user:
        return "Participant"
    raw_name = user.first_name or user.username or "Participant"
    return raw_name.replace("_", " ").replace("*", "").replace("`", "").replace("[", "").replace("]", "")


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command. Automatically routes admins directly to Admin Dashboard."""
    user = update.effective_user
    if not user:
        return

    from app.core.config import get_settings
    settings = get_settings()

    # If user is an administrator and sent /start command, open Admin Dashboard directly
    if update.message and settings.is_admin(user.id):
        from app.bot.handlers.admin import cmd_admin
        await cmd_admin(update, context)
        return

    lang = "en"
    try:
        lang = await get_user_lang(user.id, context=context)
    except Exception as e:
        logger.warning(f"Failed to fetch user language in cmd_start: {e}")

    published_comp_id = None
    try:
        async with AsyncSessionLocal() as db:
            participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
            if not participant:
                reg_data = context.user_data.get("registration")
                if reg_data:
                    step = reg_data.get("step")
                    if step == "phone":
                        prompt = get_text("reg_share_phone_prompt", lang)
                        kb = get_share_phone_keyboard(lang)
                        if update.message:
                            await update.message.reply_text(prompt, reply_markup=kb)
                        elif update.callback_query:
                            await update.callback_query.answer()
                            if update.callback_query.message:
                                await update.callback_query.message.reply_text(prompt, reply_markup=kb)
                        return
                    elif step == "full_name":
                        prompt = get_text("reg_membership_verified_prompt_name", lang)
                        if update.message:
                            await update.message.reply_text(prompt)
                        elif update.callback_query:
                            await update.callback_query.answer()
                            await update.callback_query.edit_message_text(prompt)
                        return

            if participant:
                pub_res = await db.execute(
                    select(ExamAttempt.competition_id)
                    .join(Competition, ExamAttempt.competition_id == Competition.id)
                    .where(
                        and_(
                            ExamAttempt.participant_id == participant.id,
                            Competition.status.in_([CompetitionStatus.PUBLISHED, CompetitionStatus.ARCHIVED]),
                        )
                    )
                    .order_by(ExamAttempt.started_at.desc())
                    .limit(1)
                )
                published_comp_id = pub_res.scalar()

    except Exception as e:
        logger.warning(f"Database error in cmd_start for user {user.id}: {e}")

    name = get_participant_display_name(update)
    text = get_text("welcome", lang, name=name)
    is_admin = settings.is_admin(user.id)
    keyboard = get_main_menu_keyboard(lang, is_admin=is_admin, published_comp_id=published_comp_id)

    try:
        if update.message:
            await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
        elif update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Error sending welcome message with markdown: {e}")
        plain = text.replace("*", "").replace("_", "").replace("`", "")
        try:
            if update.message:
                await update.message.reply_text(plain, reply_markup=keyboard)
            elif update.callback_query:
                await update.callback_query.edit_message_text(plain, reply_markup=keyboard)
        except Exception as e2:
            logger.error(f"Critical error delivering fallback welcome message: {e2}")



async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /help command or help menu button."""
    user = update.effective_user
    lang = await get_user_lang(user.id)
    text = get_text("help_text", lang)
    keyboard = get_main_menu_keyboard(lang)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


async def cb_language_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays language selection menu."""
    query = update.callback_query
    await query.answer()
    lang = await get_user_lang(update.effective_user.id)
    text = get_text("select_language", lang)
    keyboard = get_language_keyboard(lang)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


async def cb_select_language(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles language selection click."""
    query = update.callback_query
    await query.answer()
    chosen_lang = query.data.split(":")[1]
    if context and hasattr(context, "user_data") and context.user_data is not None:
        context.user_data["user_lang"] = chosen_lang

    async with AsyncSessionLocal() as db:
        await ParticipantService.update_language(db, update.effective_user.id, chosen_lang)

    confirm_text = get_text("language_updated", chosen_lang)
    name = get_participant_display_name(update)
    menu_text = f"{confirm_text}\n\n{get_text('welcome', chosen_lang, name=name)}"
    keyboard = get_main_menu_keyboard(chosen_lang)
    await query.edit_message_text(menu_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


async def _send_or_edit(
    target: Any,
    text: str,
    reply_markup: Optional[Any] = None,
    parse_mode: Optional[str] = ParseMode.MARKDOWN,
) -> None:
    """Helper to cleanly edit an existing message or reply with a new message."""
    if hasattr(target, "edit_message_text"):
        try:
            await target.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            return
        except Exception as e:
            if "message is not modified" in str(e).lower():
                return
            plain = text.replace("*", "").replace("_", "").replace("`", "")
            try:
                await target.edit_message_text(plain, reply_markup=reply_markup)
            except Exception as e2:
                if "message is not modified" in str(e2).lower():
                    return
                logger.warning(f"Failed to edit message in _send_or_edit: {e2}")
    elif hasattr(target, "reply_text"):
        try:
            await target.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            plain = text.replace("*", "").replace("_", "").replace("`", "")
            try:
                await target.reply_text(plain, reply_markup=reply_markup)
            except Exception as e2:
                logger.warning(f"Failed to send reply in _send_or_edit: {e2}")


async def render_competition_state_for_participant(
    target: Any,
    participant: Participant,
    user: Any,
    lang: str,
    context: Optional[ContextTypes.DEFAULT_TYPE] = None,
) -> None:
    """Renders the appropriate contextual competition view for a verified, registered participant.
    
    Cases handled contextually:
    - Attempt in progress -> Resumes first unanswered question immediately
    - Results published -> Shows score, rank, correct/incorrect, and Review Answers button
    - Results pending -> Shows clean results pending notice
    - LIVE competition -> Shows competition details and [Start/Continue Competition]
    - Scheduled/Open competition -> Shows human-friendly schedule and details (no raw enums or UUIDs)
    - Closed competition -> Shows competition closed notice
    """
    async with AsyncSessionLocal() as db:
        # Check active competition (LIVE or OPEN)
        comp = await CompetitionService.get_active_competition(db)
        if not comp:
            # Check if participant has an attempt for the latest completed/published competition
            latest_attempt_stmt = (
                select(ExamAttempt, Competition)
                .join(Competition, ExamAttempt.competition_id == Competition.id)
                .where(ExamAttempt.participant_id == participant.id)
                .order_by(ExamAttempt.started_at.desc())
                .limit(1)
            )
            latest_res = await db.execute(latest_attempt_stmt)
            latest_row = latest_res.first()

            if latest_row:
                prev_attempt, prev_comp = latest_row
                name = participant.full_name or (user.first_name if user else "Participant")
                if prev_comp.status in [CompetitionStatus.PUBLISHED, CompetitionStatus.ARCHIVED]:
                    await render_participant_result_screen(target, prev_comp.id, participant.id, lang, name)
                    return
                elif prev_attempt.status in [AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED]:
                    text = get_text("results_pending_notice", lang)
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
                    await _send_or_edit(target, text, reply_markup=kb)
                    return

            from app.core.config import get_settings
            if user and get_settings().is_admin(user.id):
                admin_hint = (
                    "⏳ *No competition is currently LIVE.*\n\n"
                    "👑 As an administrator, you can configure and open the competition via the Admin Dashboard."
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="admin:home")],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="menu:home")],
                ])
                await _send_or_edit(target, admin_hint, reply_markup=kb)
                return

            latest_comp = await CompetitionService.get_open_or_scheduled_competition(db)
            if not latest_comp:
                latest_comp = (await db.execute(select(Competition).order_by(Competition.created_at.desc()).limit(1))).scalar_one_or_none()

            if latest_comp and latest_comp.status in [CompetitionStatus.DRAFT, CompetitionStatus.SCHEDULED, CompetitionStatus.OPEN]:
                sched = format_schedule_window(latest_comp.opens_at, latest_comp.closes_at)
                meta = format_meta_line(latest_comp.duration_minutes, latest_comp.question_count)
                safe_title = latest_comp.title.replace("*", "").replace("_", " ").replace("`", "")
                text = (
                    f"🏆 *EMYC Competition*\n"
                    f"*{safe_title}*\n\n"
                    f"{sched}\n"
                    f"{meta}\n\n"
                    f"✅ *You are eligible to participate.*\n\n"
                    f"⏳ *The examination session has not started yet.*\n"
                    f"The competition will open automatically at the scheduled time."
                )
            elif latest_comp and latest_comp.status in [CompetitionStatus.CLOSED, CompetitionStatus.RESULTS_FINALIZED, CompetitionStatus.PUBLISHED, CompetitionStatus.ARCHIVED]:
                text = get_text("competition_closed", lang)
            else:
                text = get_text("competition_not_open", lang, title="EMYC Competition", schedule="Schedule TBA", details="", opens_at="Soon", closes_at="TBA")
            await _send_or_edit(target, text, reply_markup=get_main_menu_keyboard(lang))
            return

        # Check existing attempt for this competition
        stmt = (
            select(ExamAttempt)
            .where(
                and_(
                    ExamAttempt.competition_id == comp.id,
                    ExamAttempt.participant_id == participant.id,
                )
            )
            .order_by(ExamAttempt.started_at.desc())
            .limit(1)
        )
        attempt = (await db.execute(stmt)).scalars().first()

        if attempt:
            # If in progress, check deadline and resume in Mini App
            if attempt.status == AttemptStatus.IN_PROGRESS:
                if now_utc() > ensure_utc(attempt.deadline_at):
                    await CompetitionService.auto_submit_expired_attempt(db, attempt)
                    text = get_text("time_up_auto_submit", lang)
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
                    await _send_or_edit(target, text, reply_markup=kb)
                    return
                from app.core.config import get_settings
                from telegram import WebAppInfo
                webapp_url = getattr(get_settings(), "WEBAPP_URL", "https://emyc-exam.pages.dev")
                launch_url = f"{webapp_url}?comp_id={comp.id}"
                text = (
                    "🚀 *Examination in Progress*\n\n"
                    f"• *Competition:* {comp.title}\n"
                    f"• *Status:* Active Exam Session\n\n"
                    "Tap below to continue taking your examination in the Mini App:"
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🚀 Continue Exam (Mini App)", web_app=WebAppInfo(url=launch_url))],
                    [InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")],
                ])
                await _send_or_edit(target, text, reply_markup=kb)
                return

            # If already submitted / finished:
            name = participant.full_name or (user.first_name if user else "Participant")
            if comp.status in [CompetitionStatus.PUBLISHED, CompetitionStatus.ARCHIVED]:
                await render_participant_result_screen(target, comp.id, participant.id, lang, name)
                return
            else:
                # Results pending
                text = get_text("results_pending_notice", lang)
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
                await _send_or_edit(target, text, reply_markup=kb)
                return

        # No attempt yet -> Show competition summary and Start Competition button
        sched_str = format_schedule_window(comp.opens_at, comp.closes_at)
        meta_str = format_meta_line(comp.duration_minutes, comp.question_count)
        exam_info_text = get_text(
            "exam_info",
            lang,
            title=comp.title,
            schedule=sched_str,
            details=meta_str,
            questions=comp.question_count,
            duration=comp.duration_minutes,
            opens_at=sched_str,
            closes_at=meta_str,
        )
        await _send_or_edit(
            target,
            exam_info_text,
            reply_markup=get_start_exam_keyboard(comp.id, lang=lang),
        )


async def cb_start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Start Competition' button from main menu. Guides registration or active exam."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id, context=context)

    try:
        async with AsyncSessionLocal() as db:
            participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)

            # 1. Unregistered -> Check if admin testing, registration in progress, or prompt for Membership ID
            if not participant:
                from app.core.config import get_settings
                if get_settings().is_admin(user.id):
                    participant = Participant(
                        telegram_user_id=user.id,
                        telegram_username=user.username or "admin_tester",
                        full_name=user.full_name or "Admin Test Candidate",
                        phone_number="+251900000000",
                        membership_id=f"ADMIN-{user.id}",
                        is_active=True,
                    )
                    db.add(participant)
                    await db.commit()
                    await db.refresh(participant)
                else:
                    reg_data = context.user_data.get("registration")
                if reg_data:
                    step = reg_data.get("step")
                    if step == "phone":
                        prompt = get_text("reg_share_phone_prompt", lang)
                        kb = get_share_phone_keyboard(lang)
                        if query.message:
                            await query.message.reply_text(prompt, reply_markup=kb)
                        return
                    elif step == "full_name":
                        prompt = get_text("reg_membership_verified_prompt_name", lang)
                        await query.edit_message_text(prompt)
                        return

                context.user_data["awaiting_membership"] = True
                prompt = get_text("membership_prompt", lang)
                await query.edit_message_text(
                    prompt,
                    reply_markup=get_membership_prompt_keyboard(lang),
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

        # 2. Registered -> Contextually route into the competition state
        await render_competition_state_for_participant(
            target=query,
            participant=participant,
            user=user,
            lang=lang,
            context=context,
        )
    except Exception as e:
        logger.error(f"Error in cb_start_flow for user {user.id}: {e}", exc_info=True)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
        fallback_msg = get_text("error_generic_retry", lang)
        try:
            await query.edit_message_text(fallback_msg, reply_markup=kb)
        except Exception:
            if query.message:
                await query.message.reply_text(fallback_msg, reply_markup=kb)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles text messages (Membership ID input & Admin announcements)."""
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return

    lang = await get_user_lang(user.id)
    text = update.message.text.strip()

    # Case A: Admin broadcast announcement flow
    if context.user_data.get("awaiting_announcement"):
        from app.core.config import get_settings
        if get_settings().is_admin(user.id):
            context.user_data["awaiting_announcement"] = False
            context.user_data["pending_announcement"] = text

            async with AsyncSessionLocal() as db:
                p_count = await ParticipantService.get_registered_participants_count(db, exclude_admins=True)

            preview_text = get_text(
                "admin_confirm_broadcast",
                lang,
                text=text,
                count=p_count,
            )
            await update.message.reply_text(
                preview_text,
                reply_markup=get_admin_confirm_announcement_keyboard(lang),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    # Case B: Admin interactive competition creation wizard
    if context.user_data.get("create_comp"):
        from app.core.config import get_settings
        if get_settings().is_admin(user.id):
            wizard = context.user_data["create_comp"]
            step = wizard.get("step")

            if step == "title":
                wizard["title"] = text
                wizard["step"] = "description"
                prompt = (
                    f"📝 *Create New EMYC Competition (Step 2/4)*\n\n"
                    f"*Title:* {text}\n\n"
                    f"Please type a brief *Description* (or send /skip):"
                )
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:competition")]])
                await update.message.reply_text(prompt, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
                return

            if step == "description":
                wizard["description"] = None if text.lower() == "/skip" else text
                wizard["step"] = "duration"
                prompt = (
                    f"⏱ *Create New EMYC Competition (Step 3/4)*\n\n"
                    f"*Title:* {wizard['title']}\n\n"
                    f"Select the allowed *Attempt Duration* per participant:"
                )
                await update.message.reply_text(
                    prompt,
                    reply_markup=get_admin_create_comp_duration_keyboard(lang),
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            if step == "custom_duration":
                clean_val = text.strip()
                if clean_val.isdigit():
                    dur_val = int(clean_val)
                    if 1 <= dur_val <= 1440:
                        wizard["duration"] = dur_val
                        wizard["step"] = "schedule"
                        prompt = (
                            f"📅 *Create New EMYC Competition (Step 4/4)*\n\n"
                            f"*Title:* {wizard.get('title', 'Competition')}\n"
                            f"*Duration:* {dur_val} minutes\n\n"
                            f"Select the competition open window:"
                        )
                        await update.message.reply_text(
                            prompt,
                            reply_markup=get_admin_create_comp_schedule_keyboard(lang=lang),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        return

                err_text = get_text("admin_custom_dur_invalid", lang)
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:competition")]])
                await update.message.reply_text(err_text, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
                return

            if step == "custom_schedule":
                from app.core.time_utils import parse_window_string, format_timedelta_friendly
                td = parse_window_string(text)
                if td is not None:
                    now = datetime.now(timezone.utc)
                    wizard["opens_at"] = now
                    wizard["closes_at"] = now + td
                    wizard["step"] = "questions"
                    sched_label = format_timedelta_friendly(td)
                    prompt = (
                        f"📝 *Create New EMYC Competition: Question Setup*\n\n"
                        f"*Title:* {wizard.get('title', 'Competition')}\n"
                        f"*Duration:* {wizard.get('duration', 30)} minutes\n"
                        f"*Closes in:* {sched_label}\n\n"
                        f"How would you like to configure questions for this competition?"
                    )
                    from app.bot.keyboards import get_admin_create_comp_questions_keyboard
                    await update.message.reply_text(
                        prompt,
                        reply_markup=get_admin_create_comp_questions_keyboard(),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    return

                err_text = get_text("admin_custom_sched_invalid", lang)
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:competition")]])
                await update.message.reply_text(err_text, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
                return

    # Case B.2: Admin editing existing competition schedule window
    if context.user_data.get("editing_sched_comp_id"):
        from app.core.config import get_settings
        if get_settings().is_admin(user.id):
            comp_id_str = context.user_data["editing_sched_comp_id"]
            from app.core.time_utils import parse_window_string, format_timedelta_friendly
            td = parse_window_string(text)
            lang = context.user_data.get("admin_lang", "en")
            if td is not None:
                context.user_data.pop("editing_sched_comp_id", None)
                dur_minutes = max(1, int(td.total_seconds() / 60))
                now = datetime.now(timezone.utc)
                async with AsyncSessionLocal() as db:
                    comp = await db.get(Competition, uuid.UUID(comp_id_str))
                    if comp:
                        comp.closes_at = comp.opens_at + td
                        if comp.status == CompetitionStatus.LIVE and comp.actual_exam_started_at:
                            await CompetitionService.extend_live_exam_duration(db, comp.id, dur_minutes)
                        else:
                            await db.commit()
                sched_label = format_timedelta_friendly(td)
                await update.message.reply_text(
                    f"✅ *Schedule Updated!*\n\nCompetition schedule / duration is now set to *{sched_label}*.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition")]]),
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

            err_text = get_text("admin_custom_sched_invalid", lang)
            cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:competition")]])
            await update.message.reply_text(err_text, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
            return

    # Case C: Admin interactive question authoring wizard (5 steps)
    if context.user_data.get("q_wizard") or context.user_data.get("awaiting_question_comp_id"):
        from app.core.config import get_settings
        if get_settings().is_admin(user.id):
            if "q_wizard" not in context.user_data:
                comp_id = context.user_data["awaiting_question_comp_id"]
                context.user_data["q_wizard"] = {"comp_id": comp_id, "step": "text"}
            wizard = context.user_data["q_wizard"]
            step = wizard.get("step")
            comp_id = wizard.get("comp_id")

            # Check if admin provided all-in-one shortcut format
            if "|" in text:
                parts = [p.strip() for p in text.split("|")]
                if len(parts) >= 6:
                    q_text = parts[0]
                    opt_a = parts[1].removeprefix("A)").removeprefix("A.").strip()
                    opt_b = parts[2].removeprefix("B)").removeprefix("B.").strip()
                    opt_c = parts[3].removeprefix("C)").removeprefix("C.").strip()
                    opt_d = parts[4].removeprefix("D)").removeprefix("D.").strip()
                    correct = parts[5].strip().upper()
                    if correct in ["A", "B", "C", "D"]:
                        context.user_data.pop("q_wizard", None)
                        context.user_data.pop("awaiting_question_comp_id", None)
                        options = {"A": opt_a, "B": opt_b, "C": opt_c, "D": opt_d}
                        async with AsyncSessionLocal() as db:
                            comp = await db.get(Competition, comp_id)
                            if comp and comp.status in [CompetitionStatus.DRAFT, CompetitionStatus.SCHEDULED]:
                                cnt_stmt = select(func.count(CompetitionQuestion.id)).where(CompetitionQuestion.competition_id == comp.id)
                                current_q_count = (await db.execute(cnt_stmt)).scalar() or 0
                                next_order = current_q_count + 1
                                q = CompetitionQuestion(
                                    competition_id=comp.id,
                                    question_text=q_text,
                                    options=options,
                                    correct_option=correct,
                                    order_index=next_order,
                                )
                                db.add(q)
                                comp.question_count = next_order
                                await db.commit()

                                kb = InlineKeyboardMarkup([
                                    [InlineKeyboardButton("➕ Add Another Question", callback_data=f"admin:add_q:{comp.id}")],
                                    [InlineKeyboardButton(f"📋 Manage Questions ({next_order})", callback_data=f"admin:q_list:{comp.id}:1")],
                                    [InlineKeyboardButton("⚙️ Back to Competition", callback_data="admin:competition")],
                                ])
                                await update.message.reply_text(
                                    f"✅ *Question #{next_order} Added Successfully!*\n\n"
                                    f"*{q_text}*\n"
                                    f"A) {options['A']}\nB) {options['B']}\nC) {options['C']}\nD) {options['D']}\n"
                                    f"Correct: *Option {correct}*",
                                    reply_markup=kb,
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                                return

            if step == "text":
                wizard["text"] = text
                wizard["step"] = "opt_a"
                prompt = (
                    "📝 *Add Question (Step 2/5: Option A)*\n\n"
                    f"*Question:* {text}\n\n"
                    "Please enter the text for *Option A*:"
                )
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Discard", callback_data="admin:q_wiz_cancel")]])
                await update.message.reply_text(prompt, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
                return

            if step == "opt_a":
                wizard["opt_a"] = text
                wizard["step"] = "opt_b"
                prompt = (
                    "📝 *Add Question (Step 3/5: Option B)*\n\n"
                    f"*A)* {wizard.get('opt_a')}\n\n"
                    "Please enter the text for *Option B*:"
                )
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Discard", callback_data="admin:q_wiz_cancel")]])
                await update.message.reply_text(prompt, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
                return

            if step == "opt_b":
                wizard["opt_b"] = text
                wizard["step"] = "opt_c"
                prompt = (
                    "📝 *Add Question (Step 4/5: Option C)*\n\n"
                    f"*A)* {wizard.get('opt_a')}\n"
                    f"*B)* {wizard.get('opt_b')}\n\n"
                    "Please enter the text for *Option C*:"
                )
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Discard", callback_data="admin:q_wiz_cancel")]])
                await update.message.reply_text(prompt, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
                return

            if step == "opt_c":
                wizard["opt_c"] = text
                wizard["step"] = "opt_d"
                prompt = (
                    "📝 *Add Question (Step 5/5: Option D)*\n\n"
                    f"*A)* {wizard.get('opt_a')}\n"
                    f"*B)* {wizard.get('opt_b')}\n"
                    f"*C)* {wizard.get('opt_c')}\n\n"
                    "Please enter the text for *Option D*:"
                )
                cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Discard", callback_data="admin:q_wiz_cancel")]])
                await update.message.reply_text(prompt, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
                return

            if step == "opt_d":
                wizard["opt_d"] = text
                wizard["step"] = "choice"
                prompt = (
                    "🎯 *Select Correct Answer*\n\n"
                    f"*{wizard.get('text')}*\n\n"
                    f"A) {wizard.get('opt_a')}\n"
                    f"B) {wizard.get('opt_b')}\n"
                    f"C) {wizard.get('opt_c')}\n"
                    f"D) {wizard.get('opt_d')}\n\n"
                    "Which option is the correct answer? Tap below:"
                )
                from app.bot.keyboards import get_admin_question_correct_choice_keyboard
                await update.message.reply_text(
                    prompt,
                    reply_markup=get_admin_question_correct_choice_keyboard(),
                    parse_mode=ParseMode.MARKDOWN,
                )
                return

    # Case D.1: Participant Registration state machine (Full Name & Phone text fallback)
    reg_data = context.user_data.get("registration")
    if reg_data:
        step = reg_data.get("step")
        if step == "full_name":
            clean_name = text.strip()
            # Validate full name: not empty, min 2 chars, max 100 chars, not a bot command
            if not clean_name or len(clean_name) < 2 or len(clean_name) > 100 or clean_name.startswith("/"):
                err_prompt = get_text("reg_enter_name_invalid", lang)
                await update.message.reply_text(err_prompt)
                return

            reg_data["full_name"] = clean_name
            # Authoritative username from Telegram user object
            username_str = f"@{user.username.lstrip('@')}" if user.username else None
            reg_data["telegram_username"] = username_str
            reg_data["step"] = "phone"

            phone_prompt = get_text("reg_share_phone_prompt", lang)
            await update.message.reply_text(
                phone_prompt,
                reply_markup=get_share_phone_keyboard(lang),
            )
            return

        elif step == "phone":
            # Participant typed text instead of tapping the native contact button
            reminder = get_text("reg_phone_rejected_not_owner", lang)
            await update.message.reply_text(
                reminder,
                reply_markup=get_share_phone_keyboard(lang),
            )
            return

    # Case D.2: Participant Membership ID input
    is_awaiting = context.user_data.get("awaiting_membership")
    looks_like_id = text.upper().startswith("EMYC") or "/" in text

    if is_awaiting or looks_like_id:
        async with AsyncSessionLocal() as db:
            try:
                # Bind / verify membership for this Telegram user
                participant = await ParticipantService.register_or_bind_participant(
                    db=db,
                    telegram_user_id=user.id,
                    membership_id=text,
                    telegram_username=user.username,
                    language_code=lang,
                )
                context.user_data["awaiting_membership"] = False

                # If already fully registered (has full name and phone number):
                if participant.full_name and participant.phone_number:
                    context.user_data.pop("registration", None)
                    context.user_data.pop("awaiting_membership", None)
                    already_msg = get_text("reg_already_registered", lang, membership_id=participant.membership_id)
                    from app.core.config import get_settings
                    menu_kb = get_main_menu_keyboard(lang, is_admin=get_settings().is_admin(user.id))
                    await update.message.reply_text(already_msg, reply_markup=menu_kb, parse_mode=ParseMode.MARKDOWN)
                    return

                # Not yet registered: start the registration flow!
                context.user_data["registration"] = {
                    "membership_id": participant.membership_id,
                    "step": "full_name",
                }

                prompt_name = get_text("reg_membership_verified_prompt_name", lang, membership_id=participant.membership_id)
                await update.message.reply_text(prompt_name, parse_mode=ParseMode.MARKDOWN)
                return

            except InvalidMembershipFormatError:
                err = get_text("membership_invalid_format", lang)
                kb = get_membership_prompt_keyboard(lang)
                try:
                    await update.message.reply_text(err, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    await update.message.reply_text(err.replace("*", "").replace("_", ""), reply_markup=kb)
                return
            except MembershipNotFoundError:
                err = get_text("membership_not_found", lang)
                kb = get_membership_prompt_keyboard(lang)
                try:
                    await update.message.reply_text(err, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    await update.message.reply_text(err.replace("*", "").replace("_", ""), reply_markup=kb)
                return
            except MembershipAlreadyBoundError:
                await update.message.reply_text(get_text("membership_already_bound", lang))
                return
            except TelegramAccountAlreadyBoundError:
                err = get_text("membership_account_already_bound", lang)
                kb = get_main_menu_keyboard(lang)
                try:
                    await update.message.reply_text(err, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    await update.message.reply_text(err.replace("*", "").replace("_", ""), reply_markup=kb)
                return
            except Exception as e:
                logger.error(f"Error during membership verification for user {user.id}: {e}", exc_info=True)
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
                await update.message.reply_text("❌ Verification failed. Please check your ID and try again.", reply_markup=kb)
                return

    # Case E: General fallback for messages from participants
    async with AsyncSessionLocal() as db:
        p = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not p:
            # Unregistered user sending general text -> guide them to start
            context.user_data["awaiting_membership"] = True
            prompt = get_text("membership_prompt", lang)
            kb = get_membership_prompt_keyboard(lang)
            try:
                await update.message.reply_text(prompt, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await update.message.reply_text(prompt.replace("*", "").replace("_", ""), reply_markup=kb)
        else:
            # Registered participant sending text -> show main menu
            welcome_t = get_text("welcome", lang, name=get_participant_display_name(update))
            kb = get_main_menu_keyboard(lang)
            try:
                await update.message.reply_text(welcome_t, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                await update.message.reply_text(welcome_t.replace("*", "").replace("_", ""), reply_markup=kb)


async def handle_contact_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles native Telegram contact sharing for participant registration."""
    user = update.effective_user
    if not user or not update.message or not update.message.contact:
        return

    lang = await get_user_lang(user.id)
    reg_data = context.user_data.get("registration")

    # If user sends contact but is not in registration, or not at phone step
    if not reg_data or reg_data.get("step") != "phone":
        return

    contact = update.message.contact

    # 1. Verify contact belongs to the current Telegram user
    if contact.user_id != user.id:
        reject_msg = get_text("reg_phone_rejected_not_owner", lang)
        await update.message.reply_text(
            reject_msg,
            reply_markup=get_share_phone_keyboard(lang),
        )
        return

    # 2. Extract and normalize phone number
    raw_phone = contact.phone_number.strip()
    import re
    clean_phone = re.sub(r"[^\d+]", "", raw_phone)
    if not clean_phone.startswith("+") and clean_phone.isdigit():
        clean_phone = f"+{clean_phone}"

    membership_id = reg_data["membership_id"]
    full_name = reg_data.get("full_name") or get_participant_display_name(update)
    telegram_username = reg_data.get("telegram_username") or (f"@{user.username.lstrip('@')}" if user.username else None)

    # 3. Single atomic database transaction to persist/update participant
    try:
        async with AsyncSessionLocal() as db:
            participant = await ParticipantService.register_or_bind_participant(
                db=db,
                telegram_user_id=user.id,
                membership_id=membership_id,
                telegram_username=telegram_username,
                language_code=lang,
                full_name=full_name,
                phone_number=clean_phone,
            )

        # 4. Clear registration state
        context.user_data.pop("registration", None)
        context.user_data.pop("awaiting_membership", None)

        # 5. Dismiss reply keyboard and confirm registration
        complete_text = get_text("reg_complete", lang)
        await update.message.reply_text(complete_text, reply_markup=ReplyKeyboardRemove())

        # 6. Render contextual competition state directly
        await render_competition_state_for_participant(
            target=update.message,
            participant=participant,
            user=user,
            lang=lang,
            context=context,
        )

    except Exception as e:
        logger.error(f"Failed to persist participant registration for user {user.id}: {e}", exc_info=True)
        await update.message.reply_text(
            "⚠️ A temporary connection issue occurred while completing registration. Please tap /start to try again.",
            reply_markup=ReplyKeyboardRemove(),
        )


async def cb_exam_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Redirects participant to the high-throughput Mini App."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)

    from app.core.config import get_settings
    from telegram import WebAppInfo
    webapp_url = getattr(get_settings(), "WEBAPP_URL", "https://emyc-exam.pages.dev")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🚀 {get_text('start_exam_btn', lang)} (Mini App)", web_app=WebAppInfo(url=webapp_url))],
        [InlineKeyboardButton(get_text("back_btn", lang), callback_data="menu:home")],
    ])
    await query.edit_message_text(
        "🚀 *EMYC Competitive Examination*\n\n"
        "Please tap the button below to launch the Mini App. The examination features real-time countdown timers, "
        "smooth question navigation, and offline progress protection.",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )


async def render_question_screen(
    query: Any,
    attempt_id: uuid.UUID,
    display_order: int,
    lang: str = "en",
    participant_id: Optional[uuid.UUID] = None,
) -> None:
    """Renders single question on existing message in-place, zero chat flood."""
    async with AsyncSessionLocal() as db:
        try:
            q_data = await CompetitionService.get_question_for_attempt(
                db, attempt_id, display_order, participant_id=participant_id
            )
        except AttemptExpiredError:
            await _send_or_edit(
                query,
                get_text("time_up_auto_submit", lang),
                reply_markup=get_main_menu_keyboard(lang),
            )
            return
        except UnauthorizedAttemptAccessError:
            await _send_or_edit(
                query,
                "❌ Unauthorized attempt access.",
                reply_markup=get_main_menu_keyboard(lang),
            )
            return
        except Exception as e:
            logger.error(f"Failed to fetch question for attempt: {e}", exc_info=True)
            await _send_or_edit(
                query,
                get_text("error_generic_retry", lang),
                reply_markup=get_main_menu_keyboard(lang),
            )
            return

    # Format time remaining
    secs = q_data["time_left_seconds"]
    mins, remaining_secs = divmod(secs, 60)
    time_str = f"{mins:02d}:{remaining_secs:02d}"

    header = get_text(
        "question_header",
        lang,
        current=q_data["display_order"],
        total=q_data["total_questions"],
        time_left=time_str,
    )

    options_text = ""
    for opt_letter, opt_val in q_data["options"].items():
        options_text += f"\n*{opt_letter})* {opt_val}"

    answered_opt = q_data.get("already_answered_option")
    selected_status = ""
    if answered_opt:
        selected_status = f"\n\n{get_text('selected_answer_text', lang, option=answered_opt)}"

    msg_body = f"{header}\n\n{q_data['question_text']}\n{options_text}{selected_status}"

    keyboard = get_question_keyboard(
        attempt_id=attempt_id,
        question_id=q_data["question_id"],
        display_order=display_order,
        total_questions=q_data["total_questions"],
        selected_opt=q_data["already_answered_option"],
        lang=lang,
    )

    await _send_or_edit(query, msg_body, reply_markup=keyboard)


async def cb_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles participant selecting an answer option. Confirms choice with toast and highlights choice."""
    query = update.callback_query
    user = update.effective_user
    lang = await get_user_lang(user.id)
    parts = query.data.split(":")
    if len(parts) >= 5:
        # Legacy format: ans:attempt_id:question_id:opt:display_order
        attempt_id = uuid.UUID(parts[1])
        question_id = uuid.UUID(parts[2])
        selected_opt = parts[3]
        display_order = int(parts[4])
    else:
        # Compact format (< 64 bytes): ans:attempt_id:display_order:opt
        attempt_id = uuid.UUID(parts[1])
        display_order = int(parts[2])
        selected_opt = parts[3]
        question_id = None

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not participant:
            await query.answer()
            await query.edit_message_text(get_text("membership_prompt", lang), parse_mode=ParseMode.MARKDOWN)
            return
        p_id = participant.id

        try:
            await CompetitionService.submit_answer(
                db, attempt_id, question_id=question_id, selected_display_option=selected_opt, participant_id=p_id, display_order=display_order
            )
        except AttemptExpiredError:
            await query.answer()
            await query.edit_message_text(
                get_text("time_up_auto_submit", lang),
                reply_markup=get_main_menu_keyboard(lang),
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except UnauthorizedAttemptAccessError:
            await query.answer()
            await query.edit_message_text(
                "❌ Unauthorized attempt access.",
                reply_markup=get_main_menu_keyboard(lang),
            )
            return

    # Instant confirmation feedback via Telegram native toast notification
    toast_msg = get_text("answer_selected_toast", lang, option=selected_opt)
    await query.answer(toast_msg)

    # Re-render current question screen with the green right icon on their selected choice
    await render_question_screen(query, attempt_id, display_order, lang=lang, participant_id=p_id)


async def cb_question_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Navigates to specific question display order."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    parts = query.data.split(":")
    attempt_id = uuid.UUID(parts[2])
    display_order = int(parts[3])

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not participant:
            await query.edit_message_text(get_text("membership_prompt", lang), parse_mode=ParseMode.MARKDOWN)
            return
        p_id = participant.id

    await render_question_screen(query, attempt_id, display_order, lang=lang, participant_id=p_id)


async def cb_exam_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Renders full exam progress and answer review screen before submission."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    parts = query.data.split(":")
    attempt_id = uuid.UUID(parts[2])
    current_order = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not participant:
            await query.edit_message_text(get_text("membership_prompt", lang), parse_mode=ParseMode.MARKDOWN)
            return

        attempt = await db.get(ExamAttempt, attempt_id)
        if not attempt or attempt.participant_id != participant.id:
            await query.edit_message_text("❌ Unauthorized attempt access.", reply_markup=get_main_menu_keyboard(lang))
            return

        now = now_utc()
        deadline = ensure_utc(attempt.deadline_at)
        if now > deadline:
            await CompetitionService.auto_submit_expired_attempt(db, attempt)
            await query.edit_message_text(
                get_text("time_up_auto_submit", lang),
                reply_markup=get_main_menu_keyboard(lang),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Fetch questions and answers
        order_stmt = (
            select(AttemptQuestionOrder.display_order, AttemptQuestionOrder.question_id)
            .where(AttemptQuestionOrder.attempt_id == attempt_id)
            .order_by(AttemptQuestionOrder.display_order.asc())
        )
        orders = (await db.execute(order_stmt)).all()
        total_questions = len(orders)

        ans_stmt = (
            select(ParticipantAnswer.question_id, ParticipantAnswer.selected_display_option)
            .where(ParticipantAnswer.attempt_id == attempt_id)
        )
        answers_map = dict((await db.execute(ans_stmt)).all())

    answered_orders = set()
    summary_lines = []
    for disp_order, q_id in orders:
        if q_id in answers_map:
            answered_orders.add(disp_order)
            summary_lines.append(f"`Q{disp_order:02d}:` Option *{answers_map[q_id]}* ✅")
        else:
            summary_lines.append(f"`Q{disp_order:02d}:` _Unanswered_ ⚠️")

    answered_count = len(answered_orders)
    unanswered_count = total_questions - answered_count

    time_left = max(0, int((deadline - now).total_seconds()))
    mins, secs = divmod(time_left, 60)
    time_str = f"{mins:02d}:{secs:02d}"

    body = (
        f"{get_text('review_title', lang)}\n\n"
        f"{get_text('review_time', lang, time_left=time_str)}\n"
        f"{get_text('review_progress', lang, answered=answered_count, total=total_questions, unanswered=unanswered_count)}\n\n"
        f"*Quick Jump to Question:*\n"
        f"✅ = Answered | ⚠️ = Unanswered\n\n"
    )

    body += "\n".join(summary_lines[:25])

    keyboard = get_exam_review_keyboard(
        attempt_id=attempt_id,
        total_questions=total_questions,
        answered_orders=answered_orders,
        current_display_order=current_order,
        lang=lang,
    )

    try:
        await query.edit_message_text(body, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Error rendering review screen with markdown: {e}")
        plain = body.replace("*", "").replace("_", "").replace("`", "")
        await query.edit_message_text(plain, reply_markup=keyboard)


async def cb_submit_exam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles explicit exam submission."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    attempt_id = uuid.UUID(query.data.split(":")[2])

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not participant:
            await query.edit_message_text(get_text("membership_prompt", lang), parse_mode=ParseMode.MARKDOWN)
            return

        try:
            await CompetitionService.submit_attempt(db, attempt_id, participant_id=participant.id)
        except UnauthorizedAttemptAccessError:
            await query.edit_message_text(
                "❌ Unauthorized attempt access.",
                reply_markup=get_main_menu_keyboard(lang),
            )
            return

    confirm_msg = get_text("exam_submitted", lang)
    await query.edit_message_text(
        confirm_msg,
        reply_markup=get_main_menu_keyboard(lang),
        parse_mode=ParseMode.MARKDOWN,
    )


async def cb_review_answers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles post-publication answer reviews (correct / incorrect)."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    parts = query.data.split(":")
    review_type = parts[1]  # "correct" or "incorrect"
    comp_id = uuid.UUID(parts[2])

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not participant:
            return

        try:
            reviews = await ScoringAndRankingService.get_answer_review(
                db, comp_id, participant.id, review_type=review_type
            )
        except ResultsNotPublishedError:
            await query.edit_message_text(
                get_text("results_pending", lang),
                reply_markup=get_main_menu_keyboard(lang),
            )
            return

    if not reviews:
        if review_type == "incorrect":
            await query.edit_message_text(
                get_text("no_incorrect", lang),
                reply_markup=get_main_menu_keyboard(lang),
            )
        else:
            await query.edit_message_text("No answers to display.", reply_markup=get_main_menu_keyboard(lang))
        return

    # Format review items
    review_title = "✅ Correct Answers Review" if review_type == "correct" else "❌ Incorrect Answers Review"
    review_lines = [f"📋 *{review_title}*\n"]
    for idx, item in enumerate(reviews, start=1):
        if review_type == "incorrect":
            line = (
                f"*{idx}. {item['question_text']}*\n"
                f"❌ Your Answer: *{item['selected_display_option']})* {item['user_answer_text']}\n"
                f"✅ Correct: *{item['correct_display_option']})* {item['correct_answer_text']}\n"
            )
        else:
            line = (
                f"*{idx}. {item['question_text']}*\n"
                f"✅ Answer: *{item['correct_display_option']})* {item['correct_answer_text']}\n"
            )
        review_lines.append(line)

    text = "\n".join(review_lines)
    if len(text) > 3800:
        text = text[:3800] + "\n\n... (truncated)"

    kb = get_main_menu_keyboard(lang)
    try:
        await query.edit_message_text(
            text,
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(f"Error in cb_review_answers with markdown: {e}")
        plain = text.replace("*", "").replace("_", "").replace("`", "")
        try:
            await query.edit_message_text(plain, reply_markup=kb)
        except Exception:
            if query.message:
                await query.message.reply_text(plain, reply_markup=kb)



async def render_participant_result_screen(
    target: Any,
    competition_id: uuid.UUID,
    participant_id: uuid.UUID,
    lang: str,
    user_display_name: str,
) -> None:
    """Renders the official personal result screen for a participant strictly guarded by publication status."""
    kb_back = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])

    async with AsyncSessionLocal() as db:
        comp = await db.get(Competition, competition_id)
        if not comp or comp.status not in [CompetitionStatus.PUBLISHED, CompetitionStatus.ARCHIVED]:
            text = get_text("results_pending_notice", lang)
            try:
                if hasattr(target, "edit_message_text"):
                    await target.edit_message_text(text, reply_markup=kb_back, parse_mode=ParseMode.MARKDOWN)
                else:
                    await target.reply_text(text, reply_markup=kb_back, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                plain = text.replace("*", "").replace("_", "").replace("`", "")
                if hasattr(target, "edit_message_text"):
                    await target.edit_message_text(plain, reply_markup=kb_back)
                else:
                    await target.reply_text(plain, reply_markup=kb_back)
            return

        result_data = None
        try:
            result_data = await ScoringAndRankingService.get_participant_result(db, competition_id, participant_id)
        except Exception as e:
            logger.warning(f"Could not retrieve participant result (comp={competition_id}, participant={participant_id}): {e}")

        if not result_data:
            text = get_text("results_pending_notice", lang)
            try:
                if hasattr(target, "edit_message_text"):
                    await target.edit_message_text(text, reply_markup=kb_back, parse_mode=ParseMode.MARKDOWN)
                else:
                    await target.reply_text(text, reply_markup=kb_back, parse_mode=ParseMode.MARKDOWN)
            except Exception:
                plain = text.replace("*", "").replace("_", "").replace("`", "")
                if hasattr(target, "edit_message_text"):
                    await target.edit_message_text(plain, reply_markup=kb_back)
                else:
                    await target.reply_text(plain, reply_markup=kb_back)
            return

        total_q = result_data.get("total_questions") or comp.question_count or 0
        raw_score = result_data.get("score")
        score = raw_score if raw_score is not None else 0
        percent = round((score / total_q) * 100, 1) if (total_q and total_q > 0) else 0
        total_participants = result_data.get("total_participants") or 1
        rank = result_data.get("rank") if result_data.get("rank") is not None else "-"
        time_taken = result_data.get("completion_time") or "00:00"
        correct = score
        incorrect = max(0, total_q - score)

        safe_title = comp.title.replace("*", "").replace("_", " ").replace("`", "")
        safe_name = user_display_name.replace("*", "").replace("_", " ").replace("`", "")

        res_text = get_text(
            "results_title",
            lang,
            title=safe_title,
            full_name=safe_name,
            score=score,
            total=total_q,
            percent=percent,
            correct=correct,
            incorrect=incorrect,
            rank=rank,
            total_participants=total_participants,
            time=time_taken,
        )
        kb = get_results_keyboard(comp.id, lang=lang)
        try:
            if hasattr(target, "edit_message_text"):
                await target.edit_message_text(res_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            else:
                await target.reply_text(res_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.warning(f"Error rendering result screen with markdown: {e}")
            plain = res_text.replace("*", "").replace("_", "").replace("`", "")
            try:
                if hasattr(target, "edit_message_text"):
                    await target.edit_message_text(plain, reply_markup=kb)
                else:
                    await target.reply_text(plain, reply_markup=kb)
            except Exception as e2:
                logger.error(f"Fallback result editing failed: {e2}")
                if hasattr(target, "message") and target.message:
                    await target.message.reply_text(plain, reply_markup=kb)


async def cb_participant_my_result(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'My Result' action or 'Back to My Result' navigation."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    comp_id = uuid.UUID(query.data.split(":")[2])
    name = get_participant_display_name(update)

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not participant:
            await query.edit_message_text(get_text("membership_prompt", lang), parse_mode=ParseMode.MARKDOWN)
            return
        p_id = participant.id

    try:
        await render_participant_result_screen(query, comp_id, p_id, lang, name)
    except Exception as e:
        logger.error(f"Error in cb_participant_my_result for user {user.id}: {e}", exc_info=True)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
        text = get_text("results_pending_notice", lang)
        try:
            await query.edit_message_text(text, reply_markup=kb)
        except Exception:
            if query.message:
                await query.message.reply_text(text, reply_markup=kb)



async def cb_participant_answer_review(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 1-question-at-a-time post-exam answer review screen."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    parts = query.data.split(":")
    comp_id = uuid.UUID(parts[2])
    display_order = int(parts[3])

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not participant:
            await query.edit_message_text(get_text("membership_prompt", lang), parse_mode=ParseMode.MARKDOWN)
            return

        try:
            data = await ScoringAndRankingService.get_answer_review_question(
                db, comp_id, participant.id, display_order
            )
        except ResultsNotPublishedError:
            notice_text = get_text("results_pending_notice", lang)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
            await query.edit_message_text(notice_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            return
        except CompetitionError as e:
            logger.warning(f"CompetitionError in cb_participant_answer_review: {e}")
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
            await query.edit_message_text(get_text("error_generic_retry", lang), reply_markup=kb)
            return

    # Build 1-question-at-a-time review presentation
    total_q = data["total_questions"]
    q_text = data["question_text"]
    options = data["options"]  # {"A": text, "B": text, ...}

    opt_lines = []
    for letter in sorted(options.keys()):
        opt_lines.append(f"*{letter}.* {options[letter]}")
    options_formatted = "\n".join(opt_lines)

    if data["unanswered"] or not data["user_selected_display"]:
        user_ans_str = get_text("review_not_answered", lang)
        result_str = get_text("review_not_answered", lang)
    else:
        user_letter = data["user_selected_display"]
        user_text = data["user_selected_text"]
        user_ans_str = f"*{user_letter}.* {user_text}"
        if data["is_correct"]:
            result_str = get_text("review_correct", lang, points=1)
        else:
            result_str = get_text("review_incorrect", lang)

    correct_letter = data["correct_display_option"]
    correct_text = data["correct_answer_text"]
    correct_ans_str = f"*{correct_letter}.* {correct_text}"

    review_body = (
        f"📖 *Question {data['display_order']} of {total_q}*\n\n"
        f"*{q_text}*\n\n"
        f"{options_formatted}\n\n"
        f"{get_text('review_your_answer', lang)} {user_ans_str}\n"
        f"{get_text('review_correct_answer', lang)} {correct_ans_str}\n"
        f"{get_text('review_result_label', lang)} {result_str}"
    )

    kb = get_answer_review_nav_keyboard(comp_id, display_order, total_q, lang=lang)
    try:
        await query.edit_message_text(review_body, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.warning(f"Error rendering answer review markdown: {e}")
        plain = review_body.replace("*", "").replace("_", "").replace("`", "")
        await query.edit_message_text(plain, reply_markup=kb)

