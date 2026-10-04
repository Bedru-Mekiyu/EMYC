import uuid
from typing import Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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
from app.core.time_utils import now_utc, ensure_utc
from app.bot.keyboards import (
    get_main_menu_keyboard,
    get_membership_prompt_keyboard,
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


async def get_user_lang(user_id: int) -> str:
    """Helper to retrieve saved language for user or fallback to 'en'."""
    try:
        async with AsyncSessionLocal() as db:
            p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
            if p and p.language_code:
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
        lang = await get_user_lang(user.id)
    except Exception as e:
        logger.warning(f"Failed to fetch user language in cmd_start: {e}")

    published_comp_id = None
    try:
        async with AsyncSessionLocal() as db:
            participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
            if participant:
                pub_res = await db.execute(
                    select(ExamAttempt.competition_id)
                    .join(Competition, ExamAttempt.competition_id == Competition.id)
                    .where(
                        and_(
                            ExamAttempt.participant_id == participant.id,
                            Competition.status == CompetitionStatus.PUBLISHED,
                        )
                    )
                    .order_by(ExamAttempt.created_at.desc())
                    .limit(1)
                )
                published_comp_id = pub_res.scalar_one_or_none()
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

    async with AsyncSessionLocal() as db:
        await ParticipantService.update_language(db, update.effective_user.id, chosen_lang)

    confirm_text = get_text("language_updated", chosen_lang)
    name = get_participant_display_name(update)
    menu_text = f"{confirm_text}\n\n{get_text('welcome', chosen_lang, name=name)}"
    keyboard = get_main_menu_keyboard(chosen_lang)
    await query.edit_message_text(menu_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


async def cb_start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Start Competition' button from main menu. Guides registration or active exam."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)

        # 1. Unregistered -> Prompt for Membership ID
        if not participant:
            context.user_data["awaiting_membership"] = True
            prompt = get_text("membership_prompt", lang)
            await query.edit_message_text(
                prompt,
                reply_markup=get_membership_prompt_keyboard(lang),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # 2. Registered -> Check active competition
        comp = await CompetitionService.get_active_competition(db)
        if not comp:
            # Check if participant has an attempt for the latest completed/published competition
            latest_attempt_stmt = (
                select(ExamAttempt, Competition)
                .join(Competition, ExamAttempt.competition_id == Competition.id)
                .where(ExamAttempt.participant_id == participant.id)
                .order_by(ExamAttempt.created_at.desc())
                .limit(1)
            )
            latest_res = await db.execute(latest_attempt_stmt)
            latest_row = latest_res.first()

            if latest_row:
                prev_attempt, prev_comp = latest_row
                name = get_participant_display_name(update)
                if prev_comp.status == CompetitionStatus.PUBLISHED:
                    await render_participant_result_screen(query, prev_comp.id, participant.id, lang, name)
                    return
                elif prev_attempt.status in [AttemptStatus.SUBMITTED, AttemptStatus.EXPIRED]:
                    text = get_text("results_pending_notice", lang)
                    kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
                    await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                    return

            from app.core.config import get_settings
            if get_settings().is_admin(user.id):
                admin_hint = (
                    "⏳ *No competition is currently LIVE.*\n\n"
                    "👑 As an administrator, you can configure questions and set a competition to `LIVE` via the Admin Dashboard."
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="admin:home")],
                    [InlineKeyboardButton("🔙 Main Menu", callback_data="menu:home")],
                ])
                await query.edit_message_text(admin_hint, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                return

            text = get_text("competition_not_open", lang, opens_at="Soon", closes_at="TBA")
            await query.edit_message_text(text, reply_markup=get_main_menu_keyboard(lang))
            return

        # 3. Check existing attempt for this competition
        stmt = select(ExamAttempt).where(
            and_(
                ExamAttempt.competition_id == comp.id,
                ExamAttempt.participant_id == participant.id,
            )
        )
        res = await db.execute(stmt)
        attempt = res.scalar_one_or_none()

        if attempt:
            # If in progress, resume where they left off (first unanswered question)
            if attempt.status == AttemptStatus.IN_PROGRESS:
                ans_stmt = select(ParticipantAnswer.question_id).where(ParticipantAnswer.attempt_id == attempt.id)
                ans_ids = set((await db.execute(ans_stmt)).scalars().all())

                order_stmt = (
                    select(AttemptQuestionOrder.display_order)
                    .where(
                        and_(
                            AttemptQuestionOrder.attempt_id == attempt.id,
                            ~AttemptQuestionOrder.question_id.in_(ans_ids) if ans_ids else True,
                        )
                    )
                    .order_by(AttemptQuestionOrder.display_order.asc())
                    .limit(1)
                )
                first_unanswered = (await db.execute(order_stmt)).scalar() or 1
                await render_question_screen(query, attempt.id, display_order=first_unanswered, lang=lang, participant_id=participant.id)
                return

            # If already submitted / finished:
            name = get_participant_display_name(update)
            if comp.status == CompetitionStatus.PUBLISHED:
                await render_participant_result_screen(query, comp.id, participant.id, lang, name)
                return
            else:
                # Results pending
                text = get_text("results_pending_notice", lang)
                kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
                await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                return

        # 4. No attempt yet -> Show competition summary and Start Competition button
        exam_info_text = get_text(
            "exam_info",
            lang,
            title=comp.title,
            questions=comp.question_count,
            duration=comp.duration_minutes,
            opens_at=comp.opens_at.strftime("%Y-%m-%d %H:%M UTC"),
            closes_at=comp.closes_at.strftime("%Y-%m-%d %H:%M UTC"),
        )
        await query.edit_message_text(
            exam_info_text,
            reply_markup=get_start_exam_keyboard(comp.id, lang=lang),
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles text messages (Membership ID input & Admin announcements)."""
    user = update.effective_user
    if not user:
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
                from app.models.participant import Participant
                count_res = await db.execute(select(func.count(Participant.id)))
                p_count = count_res.scalar() or 0

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
                            reply_markup=get_admin_create_comp_schedule_keyboard(),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                        return

                err_text = get_text("admin_custom_dur_invalid", lang)
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

    # Case D: Participant Membership ID input
    is_awaiting = context.user_data.get("awaiting_membership")
    looks_like_id = text.upper().startswith("EMYC") or "/" in text

    if is_awaiting or looks_like_id:
        async with AsyncSessionLocal() as db:
            try:
                participant = await ParticipantService.register_or_bind_participant(
                    db=db,
                    telegram_user_id=user.id,
                    membership_id=text,
                    telegram_username=user.username,
                    language_code=lang,
                )
                context.user_data["awaiting_membership"] = False

                success_msg = get_text(
                    "membership_verified",
                    lang,
                    full_name=get_participant_display_name(update),
                    membership_id=participant.membership_id,
                )
                try:
                    await update.message.reply_text(
                        success_msg,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception as e:
                    plain = success_msg.replace("*", "").replace("_", "").replace("`", "")
                    await update.message.reply_text(plain)

                # Check if there is an active competition to seamlessly guide the user
                comp = await CompetitionService.get_active_competition(db)
                if comp:
                    exam_info_text = get_text(
                        "exam_info",
                        lang,
                        title=comp.title,
                        questions=comp.question_count,
                        duration=comp.duration_minutes,
                        opens_at=comp.opens_at.strftime("%Y-%m-%d %H:%M UTC"),
                        closes_at=comp.closes_at.strftime("%Y-%m-%d %H:%M UTC"),
                    )
                    try:
                        await update.message.reply_text(
                            exam_info_text,
                            reply_markup=get_start_exam_keyboard(comp.id, lang=lang),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception as e:
                        plain = exam_info_text.replace("*", "").replace("_", "").replace("`", "")
                        await update.message.reply_text(
                            plain,
                            reply_markup=get_start_exam_keyboard(comp.id, lang=lang),
                        )
                else:
                    welcome_t = get_text("welcome", lang, name=get_participant_display_name(update))
                    kb = get_main_menu_keyboard(lang)
                    try:
                        await update.message.reply_text(welcome_t, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                    except Exception:
                        await update.message.reply_text(welcome_t.replace("*", "").replace("_", ""), reply_markup=kb)
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


async def cb_exam_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Starts the exam attempt and presents question 1."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    comp_id = uuid.UUID(query.data.split(":")[2])

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not participant:
            await query.edit_message_text(get_text("membership_prompt", lang), parse_mode=ParseMode.MARKDOWN)
            return

        try:
            attempt = await CompetitionService.start_attempt(db, comp_id, participant.id)
            await render_question_screen(query, attempt.id, display_order=1, lang=lang, participant_id=participant.id)
        except DuplicateAttemptError:
            # Check existing attempt: if IN_PROGRESS, resume it directly!
            stmt = select(ExamAttempt).where(
                and_(
                    ExamAttempt.competition_id == comp_id,
                    ExamAttempt.participant_id == participant.id,
                )
            )
            existing_attempt = (await db.execute(stmt)).scalar_one_or_none()
            if existing_attempt and existing_attempt.status == AttemptStatus.IN_PROGRESS:
                ans_stmt = select(ParticipantAnswer.question_id).where(ParticipantAnswer.attempt_id == existing_attempt.id)
                ans_ids = set((await db.execute(ans_stmt)).scalars().all())

                order_stmt = (
                    select(AttemptQuestionOrder.display_order)
                    .where(
                        and_(
                            AttemptQuestionOrder.attempt_id == existing_attempt.id,
                            ~AttemptQuestionOrder.question_id.in_(ans_ids) if ans_ids else True,
                        )
                    )
                    .order_by(AttemptQuestionOrder.display_order.asc())
                    .limit(1)
                )
                first_unanswered = (await db.execute(order_stmt)).scalar() or 1
                await render_question_screen(query, existing_attempt.id, display_order=first_unanswered, lang=lang, participant_id=participant.id)
                return

            await query.edit_message_text(get_text("already_submitted", lang), reply_markup=get_main_menu_keyboard(lang))
        except CompetitionNotOpenError as e:
            await query.edit_message_text(f"⏳ {str(e)}", reply_markup=get_main_menu_keyboard(lang))
        except Exception as e:
            logger.error(f"Error starting exam: {e}", exc_info=True)
            await query.edit_message_text(f"❌ Error starting exam: {str(e)}", reply_markup=get_main_menu_keyboard(lang))


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
            await query.edit_message_text(
                get_text("time_up_auto_submit", lang),
                reply_markup=get_main_menu_keyboard(lang),
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        except UnauthorizedAttemptAccessError:
            await query.edit_message_text(
                "❌ Unauthorized attempt access.",
                reply_markup=get_main_menu_keyboard(lang),
            )
            return
        except Exception as e:
            logger.error(f"Failed to fetch question for attempt: {e}", exc_info=True)
            await query.edit_message_text(
                f"❌ Error fetching question: {str(e)}",
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

    try:
        await query.edit_message_text(
            msg_body,
            reply_markup=keyboard,
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.warning(f"Failed to edit question with Markdown parse mode ({e}); retrying without Markdown formatting")
        plain_body = msg_body.replace("*", "").replace("_", "").replace("`", "")
        try:
            await query.edit_message_text(
                plain_body,
                reply_markup=keyboard,
            )
        except Exception as e2:
            logger.error(f"Failed to edit question screen: {e2}")
            if query.message:
                await query.message.reply_text(
                    plain_body,
                    reply_markup=keyboard,
                )


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
    await query.edit_message_text(
        text,
        reply_markup=get_main_menu_keyboard(lang),
        parse_mode=ParseMode.MARKDOWN,
    )


async def render_participant_result_screen(
    target: Any,
    competition_id: uuid.UUID,
    participant_id: uuid.UUID,
    lang: str,
    user_display_name: str,
) -> None:
    """Renders the official personal result screen for a participant strictly guarded by publication status."""
    async with AsyncSessionLocal() as db:
        comp = await db.get(Competition, competition_id)
        if not comp or comp.status != CompetitionStatus.PUBLISHED:
            text = get_text("results_pending_notice", lang)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
            if hasattr(target, "edit_message_text"):
                await target.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            else:
                await target.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            return

        result_data = await ScoringAndRankingService.get_participant_result(db, competition_id, participant_id)
        if not result_data:
            text = get_text("results_pending_notice", lang)
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
            if hasattr(target, "edit_message_text"):
                await target.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            else:
                await target.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            return

        total_q = result_data["total_questions"]
        score = result_data["score"]
        percent = round((score / total_q) * 100, 1) if total_q else 0
        total_participants = result_data.get("total_participants") or 1

        res_text = get_text(
            "results_title",
            lang,
            title=comp.title,
            full_name=user_display_name,
            score=score,
            total=total_q,
            percent=percent,
            rank=result_data["rank"],
            total_participants=total_participants,
            time=result_data["completion_time"],
        )
        kb = get_results_keyboard(comp.id, lang=lang)
        if hasattr(target, "edit_message_text"):
            await target.edit_message_text(res_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        else:
            await target.reply_text(res_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


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

    await render_participant_result_screen(query, comp_id, p_id, lang, name)


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
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("back_to_menu_btn", lang), callback_data="menu:home")]])
            await query.edit_message_text(str(e), reply_markup=kb)
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

