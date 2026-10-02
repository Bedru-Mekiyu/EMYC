import uuid
from typing import Dict, Any
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.locales.translator import get_text
from app.models.attempt import AttemptStatus
from app.models.competition import CompetitionStatus
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
)
from app.services.scoring_service import (
    ScoringAndRankingService,
    ResultsNotPublishedError,
)
from app.bot.keyboards import (
    get_main_menu_keyboard,
    get_language_keyboard,
    get_start_exam_keyboard,
    get_question_keyboard,
    get_results_keyboard,
)


async def get_user_lang(user_id: int) -> str:
    """Helper to retrieve saved language for user or fallback to 'en'."""
    async with AsyncSessionLocal() as db:
        p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
        if p and p.language_code:
            return p.language_code
    return "en"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /start command."""
    user = update.effective_user
    if not user:
        return

    lang = await get_user_lang(user.id)
    text = get_text("welcome", lang)
    keyboard = get_main_menu_keyboard(lang)

    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


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
    # format: lang:<code>
    chosen_lang = query.data.split(":")[1]

    async with AsyncSessionLocal() as db:
        await ParticipantService.update_language(db, update.effective_user.id, chosen_lang)

    confirm_text = get_text("language_updated", chosen_lang)
    menu_text = f"{confirm_text}\n\n{get_text('welcome', chosen_lang)}"
    keyboard = get_main_menu_keyboard(chosen_lang)
    await query.edit_message_text(menu_text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


async def cb_start_flow(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles 'Start' button from main menu. Guides registration or active exam."""
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
            await query.edit_message_text(prompt, parse_mode=ParseMode.MARKDOWN)
            return

        # 2. Registered -> Check active competition
        comp = await CompetitionService.get_active_competition(db)
        if not comp:
            text = get_text("competition_not_open", lang, opens_at="Soon", closes_at="TBA")
            await query.edit_message_text(text, reply_markup=get_main_menu_keyboard(lang))
            return

        # 3. Check existing attempt for this competition
        from sqlalchemy import select, and_
        from app.models.attempt import ExamAttempt

        stmt = select(ExamAttempt).where(
            and_(
                ExamAttempt.competition_id == comp.id,
                ExamAttempt.participant_id == participant.id,
            )
        )
        res = await db.execute(stmt)
        attempt = res.scalar_one_or_none()

        if attempt:
            # If in progress, resume where they left off
            if attempt.status == AttemptStatus.IN_PROGRESS:
                await render_question_screen(query, attempt.id, display_order=1, lang=lang)
                return

            # If already submitted / finished:
            if comp.status == CompetitionStatus.PUBLISHED:
                # Results published! Show results
                result_data = await ScoringAndRankingService.get_participant_result(db, comp.id, participant.id)
                res_text = get_text(
                    "results_title",
                    lang,
                    score=result_data["score"],
                    total=result_data["total_questions"],
                    rank=result_data["rank"],
                    time=result_data["completion_time"],
                )
                kb = get_results_keyboard(
                    comp.id, result_data["correct_count"] or 0, result_data["incorrect_count"] or 0, lang=lang
                )
                await query.edit_message_text(res_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                return
            else:
                # Results pending
                sub_text = get_text("exam_submitted", lang)
                await query.edit_message_text(sub_text, reply_markup=get_main_menu_keyboard(lang), parse_mode=ParseMode.MARKDOWN)
                return

        # 4. No attempt yet -> Show competition summary and Start Competition button
        exam_info_text = get_text(
            "exam_info",
            lang,
            title=comp.title,
            questions=comp.question_count,
            duration=comp.duration_minutes,
            closes_at=comp.closes_at.strftime("%Y-%m-%d %H:%M UTC"),
        )
        await query.edit_message_text(
            exam_info_text,
            reply_markup=get_start_exam_keyboard(comp.id, lang=lang),
            parse_mode=ParseMode.MARKDOWN,
        )


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles text messages (primarily Membership ID input)."""
    user = update.effective_user
    lang = await get_user_lang(user.id)
    text = update.message.text.strip()

    if context.user_data.get("awaiting_membership"):
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

                success_msg = get_text("membership_verified", lang, membership_id=participant.membership_id)
                await update.message.reply_text(
                    success_msg,
                    reply_markup=get_main_menu_keyboard(lang),
                    parse_mode=ParseMode.MARKDOWN,
                )
            except InvalidMembershipFormatError:
                await update.message.reply_text(get_text("membership_invalid_format", lang))
            except MembershipNotFoundError:
                await update.message.reply_text(get_text("membership_not_found", lang))
            except MembershipAlreadyBoundError:
                await update.message.reply_text(get_text("membership_already_bound", lang))
            except TelegramAccountAlreadyBoundError as e:
                await update.message.reply_text(f"❌ {str(e)}")


async def cb_exam_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Starts the exam attempt and presents question 1."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    # format: exam:start:<comp_id>
    comp_id = uuid.UUID(query.data.split(":")[2])

    async with AsyncSessionLocal() as db:
        participant = await ParticipantService.get_participant_by_telegram_id(db, user.id)
        if not participant:
            await query.edit_message_text(get_text("membership_prompt", lang))
            return

        try:
            attempt = await CompetitionService.start_attempt(db, comp_id, participant.id)
            await render_question_screen(query, attempt.id, display_order=1, lang=lang)
        except CompetitionNotOpenError as e:
            await query.edit_message_text(f"⏳ {str(e)}", reply_markup=get_main_menu_keyboard(lang))
        except DuplicateAttemptError:
            await query.edit_message_text(get_text("already_submitted", lang), reply_markup=get_main_menu_keyboard(lang))


async def render_question_screen(
    query: Any, attempt_id: uuid.UUID, display_order: int, lang: str = "en"
) -> None:
    """Renders single question on existing message in-place, zero chat flood."""
    async with AsyncSessionLocal() as db:
        try:
            q_data = await CompetitionService.get_question_for_attempt(db, attempt_id, display_order)
        except AttemptExpiredError:
            await query.edit_message_text(
                "⏱ Your exam time has expired and your answers were automatically submitted.",
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

    msg_body = f"{header}\n\n{q_data['question_text']}\n{options_text}"

    keyboard = get_question_keyboard(
        attempt_id=attempt_id,
        question_id=q_data["question_id"],
        display_order=display_order,
        total_questions=q_data["total_questions"],
        selected_opt=q_data["already_answered_option"],
        lang=lang,
    )

    await query.edit_message_text(
        msg_body,
        reply_markup=keyboard,
        parse_mode=ParseMode.MARKDOWN,
    )


async def cb_answer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles participant selecting an answer option. Advances to next question in-place."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    # format: ans:<attempt_id>:<question_id>:<opt>:<display_order>
    parts = query.data.split(":")
    attempt_id = uuid.UUID(parts[1])
    question_id = uuid.UUID(parts[2])
    selected_opt = parts[3]
    display_order = int(parts[4])

    async with AsyncSessionLocal() as db:
        try:
            await CompetitionService.submit_answer(db, attempt_id, question_id, selected_opt)
        except AttemptExpiredError:
            await query.edit_message_text(
                "⏱ Exam time expired. Your answers were submitted.",
                reply_markup=get_main_menu_keyboard(lang),
            )
            return

        # Fetch question data to check total count
        q_data = await CompetitionService.get_question_for_attempt(db, attempt_id, display_order)
        total_questions = q_data["total_questions"]

    # If more questions remain, automatically advance to next question
    if display_order < total_questions:
        await render_question_screen(query, attempt_id, display_order + 1, lang=lang)
    else:
        # Re-render current question with selected indicator and Submit button
        await render_question_screen(query, attempt_id, display_order, lang=lang)


async def cb_question_nav(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Navigates to specific question display order."""
    query = update.callback_query
    await query.answer()
    lang = await get_user_lang(update.effective_user.id)
    # format: q:nav:<attempt_id>:<display_order>
    parts = query.data.split(":")
    attempt_id = uuid.UUID(parts[2])
    display_order = int(parts[3])
    await render_question_screen(query, attempt_id, display_order, lang=lang)


async def cb_submit_exam(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles explicit exam submission."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    lang = await get_user_lang(user.id)
    # format: exam:submit:<attempt_id>
    attempt_id = uuid.UUID(query.data.split(":")[2])

    async with AsyncSessionLocal() as db:
        await CompetitionService.submit_attempt(db, attempt_id)

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
    # format: rev:<type>:<comp_id>
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
    review_lines = [f"📋 *{review_type.capitalize()} Answers Review*\n"]
    for idx, item in enumerate(reviews, start=1):
        if review_type == "incorrect":
            line = (
                f"*{idx}. {item['question_text']}*\n"
                f"❌ Your Answer: {item['selected_display_option']}) {item['user_answer_text']}\n"
                f"✅ Correct: {item['correct_display_option']}) {item['correct_answer_text']}\n"
            )
        else:
            line = (
                f"*{idx}. {item['question_text']}*\n"
                f"✅ Answer: {item['correct_display_option']}) {item['correct_answer_text']}\n"
            )
        review_lines.append(line)

    text = "\n".join(review_lines)
    await query.edit_message_text(
        text,
        reply_markup=get_main_menu_keyboard(lang),
        parse_mode=ParseMode.MARKDOWN,
    )
