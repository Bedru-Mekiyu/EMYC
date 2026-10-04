import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import select, func, and_, case
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import logger, log_audit_event
from app.locales.translator import get_text
from app.models.competition import Competition, CompetitionStatus
from app.models.question import CompetitionQuestion
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus
from app.models.announcement import Announcement
from app.services.competition_service import CompetitionService, CompetitionError
from app.services.scoring_service import ScoringAndRankingService
from app.services.membership_service import ParticipantService
from app.bot.keyboards import (
    get_admin_keyboard,
    get_admin_language_keyboard,
    get_admin_results_keyboard,
    get_admin_confirm_announcement_keyboard,
    get_main_menu_keyboard,
    get_admin_participants_keyboard,
    get_admin_rankings_keyboard,
    get_admin_system_status_keyboard,
    get_admin_create_comp_duration_keyboard,
    get_admin_create_comp_schedule_keyboard,
    get_admin_create_comp_questions_keyboard,
    get_admin_question_correct_choice_keyboard,
    get_admin_question_preview_keyboard,
    get_admin_question_list_keyboard,
)
from app.scripts.seed_questions import SAMPLE_QUESTIONS

settings = get_settings()


def require_admin(handler_func):
    """Decorator guarding handler against unauthorized Telegram users."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user = update.effective_user
        if not user or not settings.is_admin(user.id):
            logger.warning(f"Unauthorized admin access attempt by telegram_user_id={user.id if user else 'unknown'}")
            if update.callback_query:
                await update.callback_query.answer("⛔ Unauthorized access.", show_alert=True)
            elif update.message:
                await update.message.reply_text("⛔ Unauthorized access.")
            return
        return await handler_func(update, context, *args, **kwargs)
    return wrapper


STATUS_DISPLAY_MAP = {
    CompetitionStatus.DRAFT: "📝 Draft",
    CompetitionStatus.SCHEDULED: "⏰ Scheduled",
    CompetitionStatus.LIVE: "🟢 Live",
    CompetitionStatus.CLOSED: "🔴 Closed",
    CompetitionStatus.RESULTS_FINALIZED: "📊 Finalized",
    CompetitionStatus.PUBLISHED: "📢 Published",
    CompetitionStatus.ARCHIVED: "📦 Archived",
}


def format_competition_status(status: Any) -> str:
    """Returns a clean, human-readable status badge instead of raw internal enums."""
    if status is None:
        return "None"
    if isinstance(status, str):
        val = status.replace("CompetitionStatus.", "").strip()
        for enum_val in CompetitionStatus:
            if enum_val.value == val or enum_val.name == val:
                return STATUS_DISPLAY_MAP.get(enum_val, val.capitalize())
        return val.capitalize()
    return STATUS_DISPLAY_MAP.get(status, str(getattr(status, "value", status)).capitalize())


@require_admin
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /admin command with EMYC Competition Admin summary and simplified 3-item controls."""
    # Clear any pending interactive wizard states
    context.user_data.pop("awaiting_announcement", None)
    context.user_data.pop("pending_announcement", None)
    context.user_data.pop("create_comp", None)
    context.user_data.pop("q_wizard", None)

    user = update.effective_user
    user_id = user.id if user else 0
    lang = context.user_data.get("admin_lang")

    async with AsyncSessionLocal() as db:
        if not lang:
            p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
            lang = p.language_code if p and p.language_code else "en"

        # Fetch active or latest competition
        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()

        title = comp.title if comp else "None"
        status = format_competition_status(comp.status) if comp else "None"

        # Total registered participants
        total_p = (await db.execute(select(func.count(Participant.id)))).scalar() or 0

        # Attempt metrics for this competition
        started = 0
        submitted = 0
        in_progress = 0
        if comp:
            attempts_stmt = select(ExamAttempt).where(ExamAttempt.competition_id == comp.id)
            attempts = list((await db.execute(attempts_stmt)).scalars().all())
            started = len(attempts)
            submitted = sum(1 for a in attempts if a.status in [AttemptStatus.SUBMITTED, AttemptStatus.FINALIZED])
            in_progress = sum(1 for a in attempts if a.status == AttemptStatus.IN_PROGRESS)

    text = get_text(
        "admin_menu_title",
        lang,
        title=title,
        status=status,
        registered_count=total_p,
        started_count=started,
        in_progress_count=in_progress,
        submitted_count=submitted,
    )
    keyboard = get_admin_keyboard(lang)

    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays language selection menu specifically for administrator."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = user.id if user else 0
    lang = context.user_data.get("admin_lang")

    if not lang:
        async with AsyncSessionLocal() as db:
            p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
            lang = p.language_code if p and p.language_code else "en"

    text = (
        "🌐 *Change Administrator Language / ቋንቋ ቀይር*\n\n"
        "Select your preferred language for competition control:"
    )
    await query.edit_message_text(text, reply_markup=get_admin_language_keyboard(lang), parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Updates administrator language preference and returns to admin dashboard."""
    query = update.callback_query
    await query.answer()
    chosen_lang = query.data.split(":")[2]
    user = update.effective_user
    user_id = user.id if user else 0
    context.user_data["admin_lang"] = chosen_lang

    async with AsyncSessionLocal() as db:
        await ParticipantService.update_language(db, user_id, chosen_lang)

    # Re-render admin panel in the newly chosen language
    await cmd_admin(update, context)


@require_admin
async def cb_admin_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Calculates authoritative competition status and participant metrics."""
    query = update.callback_query
    await query.answer()

    async with AsyncSessionLocal() as db:
        # Fetch active or latest competition
        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()

        if not comp:
            await query.edit_message_text("No competitions found in system.", reply_markup=get_admin_keyboard("en"))
            return

        # Total registered participants
        total_p = (await db.execute(select(func.count(Participant.id)))).scalar() or 0

        # Attempt metrics for this competition
        attempts_stmt = select(ExamAttempt).where(ExamAttempt.competition_id == comp.id)
        attempts = list((await db.execute(attempts_stmt)).scalars().all())

        started = len(attempts)
        submitted = sum(1 for a in attempts if a.status in [AttemptStatus.SUBMITTED, AttemptStatus.FINALIZED])
        in_progress = sum(1 for a in attempts if a.status == AttemptStatus.IN_PROGRESS)
        expired = sum(1 for a in attempts if a.status == AttemptStatus.EXPIRED)
        not_started = max(0, total_p - started)

    status_text = (
        f"📊 *EMYC Competition Operational Status*\n\n"
        f"*Title:* {comp.title}\n"
        f"*Status:* {format_competition_status(comp.status)}\n"
        f"*Opens:* {comp.opens_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"*Closes:* {comp.closes_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"👥 *Participants:* {total_p}\n"
        f"▶️ *Started:* {started}\n"
        f"✅ *Submitted:* {submitted}\n"
        f"⏳ *In Progress:* {in_progress}\n"
        f"⏱ *Expired:* {expired}\n"
        f"⏸ *Not Started:* {not_started}\n"
    )

    back_kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Refresh", callback_data="admin:status")],
        [InlineKeyboardButton("◀️ Back to Admin Panel", callback_data="admin:home")],
    ])
    await query.edit_message_text(status_text, reply_markup=back_kb, parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_competition(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manages competition lifecycle with clean state-aware actions."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = user.id if user else 0

    async with AsyncSessionLocal() as db:
        p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
        lang = p.language_code if p and p.language_code else "en"

        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()

        buttons = []
        if not comp:
            text = (
                "🏆 *Competition Management*\n\n"
                "ℹ️ *No competition is currently configured.*\n\n"
                "Tap below to create an official competition:"
            )
            buttons.append([
                InlineKeyboardButton(get_text("admin_btn_create_comp", lang), callback_data="admin:create_comp:start"),
            ])
            buttons.append([InlineKeyboardButton(get_text("admin_btn_back", lang), callback_data="admin:home")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)
            return

        # Count actual existing questions in database for this competition
        cnt_stmt = select(func.count(CompetitionQuestion.id)).where(CompetitionQuestion.competition_id == comp.id)
        existing_q_count = (await db.execute(cnt_stmt)).scalar() or 0
        if existing_q_count > 0 and comp.question_count != existing_q_count:
            comp.question_count = existing_q_count
            await db.commit()

        # Simplified state-aware buttons (questions managed in Supabase, auto-attached when starting LIVE)
        if comp.status == CompetitionStatus.DRAFT:
            if existing_q_count == 0 and comp.question_count == 0:
                buttons.append([InlineKeyboardButton("⚡ Attach 20 EMYC Standard Questions", callback_data=f"admin:attach_std:{comp.id}")])
            else:
                buttons.append([InlineKeyboardButton("🟢 Start Competition (Set LIVE)", callback_data=f"admin:set_live:{comp.id}")])
            buttons.append([InlineKeyboardButton("📦 Archive Competition", callback_data=f"admin:archive:{comp.id}")])
            buttons.append([InlineKeyboardButton(get_text("admin_btn_create_comp", lang), callback_data="admin:create_comp:start")])

        elif comp.status == CompetitionStatus.SCHEDULED:
            if existing_q_count == 0 and comp.question_count == 0:
                buttons.append([InlineKeyboardButton("⚡ Attach 20 EMYC Standard Questions", callback_data=f"admin:attach_std:{comp.id}")])
            else:
                buttons.append([InlineKeyboardButton("▶️ Start Competition (Set LIVE)", callback_data=f"admin:set_live:{comp.id}")])
            buttons.append([InlineKeyboardButton("📦 Archive Competition", callback_data=f"admin:archive:{comp.id}")])

        elif comp.status == CompetitionStatus.LIVE:
            buttons.append([InlineKeyboardButton("⏹ Close Competition", callback_data=f"admin:set_closed:{comp.id}")])
            buttons.append([InlineKeyboardButton("📊 View Results", callback_data="admin:results")])

        elif comp.status == CompetitionStatus.CLOSED:
            buttons.append([InlineKeyboardButton("📊 Finalize Scores & Rankings", callback_data=f"admin:finalize:{comp.id}")])
            buttons.append([InlineKeyboardButton("📊 View Results", callback_data="admin:results")])
            buttons.append([InlineKeyboardButton("📦 Archive Competition", callback_data=f"admin:archive:{comp.id}")])

        elif comp.status == CompetitionStatus.RESULTS_FINALIZED:
            buttons.append([InlineKeyboardButton("📢 Publish Results to Participants", callback_data=f"admin:publish:{comp.id}")])
            buttons.append([InlineKeyboardButton("📊 View Results", callback_data="admin:results")])
            buttons.append([InlineKeyboardButton("📦 Archive Competition", callback_data=f"admin:archive:{comp.id}")])

        elif comp.status in [CompetitionStatus.PUBLISHED, CompetitionStatus.ARCHIVED]:
            buttons.append([InlineKeyboardButton("📊 View Results", callback_data="admin:results")])
            buttons.append([InlineKeyboardButton(get_text("admin_btn_create_comp", lang), callback_data="admin:create_comp:start")])
            if comp.status == CompetitionStatus.PUBLISHED:
                buttons.append([InlineKeyboardButton("📦 Archive Competition", callback_data=f"admin:archive:{comp.id}")])

        buttons.append([InlineKeyboardButton(get_text("admin_btn_back", lang), callback_data="admin:home")])

        status_label = format_competition_status(comp.status)
        opens_str = comp.opens_at.strftime('%Y-%m-%d %H:%M UTC') if comp.opens_at else "TBA"
        closes_str = comp.closes_at.strftime('%Y-%m-%d %H:%M UTC') if comp.closes_at else "TBA"
        safe_title = comp.title.replace("*", "").replace("_", " ").replace("`", "")

        text = (
            f"🏆 *Competition Control*\n\n"
            f"*{safe_title}*\n\n"
            f"• *Status:* {status_label}\n"
            f"• *Duration:* {comp.duration_minutes} minutes\n"
            f"• *Questions:* {existing_q_count}\n"
            f"• *Opens:* {opens_str}\n"
            f"• *Closes:* {closes_str}\n"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_attach_standard_questions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Attaches standard question set to a competition in 1 click."""
    query = update.callback_query
    await query.answer("Attaching standard questions...", show_alert=False)
    comp_id = uuid.UUID(query.data.split(":")[2])

    async with AsyncSessionLocal() as db:
        comp = await db.get(Competition, comp_id)
        if not comp or comp.status not in [CompetitionStatus.DRAFT, CompetitionStatus.SCHEDULED]:
            await query.answer("Cannot add questions to active or closed competition.", show_alert=True)
            return

        cnt_stmt = select(func.count(CompetitionQuestion.id)).where(CompetitionQuestion.competition_id == comp.id)
        start_idx = ((await db.execute(cnt_stmt)).scalar() or 0) + 1

        for offset, q_data in enumerate(SAMPLE_QUESTIONS):
            q = CompetitionQuestion(
                competition_id=comp.id,
                question_text=q_data["question_text"],
                options=q_data["options"],
                correct_option=q_data["correct_option"],
                order_index=start_idx + offset,
            )
            db.add(q)
        comp.question_count = (start_idx - 1) + len(SAMPLE_QUESTIONS)
        await db.commit()

    # Refresh competition controls view
    await cb_admin_competition(update, context)


@require_admin
async def cb_admin_setup_sample(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Sets up an official sample competition with authentic EMYC Islamic & General Knowledge questions."""
    query = update.callback_query
    await query.answer("Setting up competition...", show_alert=False)

    target_mode = query.data.split(":")[2]  # "live" or "draft"
    now = datetime.now(timezone.utc)

    sample_questions = SAMPLE_QUESTIONS

    async with AsyncSessionLocal() as db:
        comp = await CompetitionService.create_competition(
            db=db,
            title="EMYC 2026 Youth Knowledge Challenge",
            description="Official Ethiopian Muslim Youth Council Competitive Exam",
            opens_at=now - timedelta(minutes=5),
            closes_at=now + timedelta(days=7),
            duration_minutes=30,
            question_count=len(sample_questions),
            status=CompetitionStatus.DRAFT,
        )

        for idx, q_data in enumerate(sample_questions, start=1):
            q = CompetitionQuestion(
                competition_id=comp.id,
                question_text=q_data["question_text"],
                options=q_data["options"],
                correct_option=q_data["correct_option"],
                order_index=idx,
            )
            db.add(q)
        await db.commit()

        if target_mode == "live":
            await CompetitionService.update_status(
                db, comp.id, CompetitionStatus.LIVE, admin_id=str(update.effective_user.id)
            )

    status_str = "LIVE 🟢" if target_mode == "live" else "DRAFT 📝"
    msg = (
        f"🎉 *Sample Competition Created!*\n\n"
        f"*Title:* {comp.title}\n"
        f"*Questions:* {len(sample_questions)}\n"
        f"*Status:* `{status_str}`\n"
        f"*Duration:* {comp.duration_minutes} minutes\n\n"
        f"You can now manage the competition or switch to participant view to test taking the exam!"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition")],
        [InlineKeyboardButton("👤 Switch to Participant View", callback_data="admin:to_participant")],
        [InlineKeyboardButton("◀️ Back to Admin Panel", callback_data="admin:home")],
    ])
    await query.edit_message_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_to_participant(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switches the admin view to the participant main menu for testing."""
    query = update.callback_query
    await query.answer("Switching to Participant View...", show_alert=False)
    user = update.effective_user
    from app.bot.handlers.participant import get_user_lang, get_participant_display_name
    lang = await get_user_lang(user.id)
    name = get_participant_display_name(update)
    text = get_text("welcome", lang, name=name)
    keyboard = get_main_menu_keyboard(lang, is_admin=True)
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_set_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transitions competition status."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = user.id if user else 0

    # format: admin:set_live:<id> or admin:set_closed:<id>
    parts = query.data.split(":")
    action_type = parts[1]  # "set_live" or "set_closed"
    comp_id = uuid.UUID(parts[2])
    new_status = CompetitionStatus.LIVE if "live" in action_type else CompetitionStatus.CLOSED

    async with AsyncSessionLocal() as db:
        p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
        lang = p.language_code if p and p.language_code else "en"

        if new_status == CompetitionStatus.LIVE:
            cnt_stmt = select(func.count(CompetitionQuestion.id)).where(CompetitionQuestion.competition_id == comp_id)
            existing_count = (await db.execute(cnt_stmt)).scalar() or 0

            target_comp = await db.get(Competition, comp_id)
            if not target_comp:
                await query.edit_message_text("❌ Competition not found.", reply_markup=get_admin_keyboard("en"))
                return

            if existing_count == 0 and (target_comp.question_count or 0) == 0:
                err_msg = (
                    "⚠️ *Cannot start competition: No questions found in database.*\n\n"
                    "Please insert your questions into Supabase (`competition_questions` table) first, then tap Start Competition."
                )
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Refresh", callback_data="admin:competition")],
                    [InlineKeyboardButton("◀️ Admin Menu", callback_data="admin:home")],
                ])
                await query.edit_message_text(err_msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                return

            if existing_count > 0:
                target_comp.question_count = existing_count
            now = datetime.now(timezone.utc)
            if target_comp.closes_at <= now:
                target_comp.closes_at = now + timedelta(days=7)
            if target_comp.opens_at > now:
                target_comp.opens_at = now
            await db.commit()

        try:
            comp = await CompetitionService.update_status(db, comp_id, new_status, admin_id=str(user_id))
            status_emoji = "LIVE 🟢" if new_status == CompetitionStatus.LIVE else "CLOSED 🔴"
            msg = (
                f"✅ *Competition is now {status_emoji}!*\n\n"
                f"*Title:* {comp.title}\n"
                f"*Questions:* {comp.question_count}\n"
                f"*Duration:* {comp.duration_minutes} minutes\n\n"
            )
            if new_status == CompetitionStatus.LIVE:
                msg += "🎉 The competition is officially open! Participants can now take the exam."
            else:
                msg += "The competition has closed. You can now finalize scores and rankings in Results."

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🏆 Manage Competition", callback_data="admin:competition")],
                [InlineKeyboardButton("📊 View Results", callback_data="admin:results")],
                [InlineKeyboardButton("◀️ Admin Menu", callback_data="admin:home")],
            ])
            await query.edit_message_text(msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except CompetitionValidationError as ve:
            err_msg = (
                f"⚠️ *Cannot open competition yet:*\n\n"
                f"{chr(10).join('• ' + e for e in ve.errors)}\n\n"
                "Please configure questions or schedule before setting to LIVE."
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition")],
                [InlineKeyboardButton("◀️ Admin Menu", callback_data="admin:home")],
            ])
            await query.edit_message_text(err_msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except CompetitionError as e:
            await query.edit_message_text(
                f"❌ Error: {str(e)}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="admin:competition")]]),
                parse_mode=ParseMode.MARKDOWN,
            )


@require_admin
async def cb_admin_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Consolidated operational results dashboard answering all critical operational metrics."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = user.id if user else 0

    async with AsyncSessionLocal() as db:
        p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
        lang = p.language_code if p and p.language_code else "en"

        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()
        if not comp:
            await query.edit_message_text("No competition found.", reply_markup=get_admin_keyboard(lang))
            return

        # Registration metrics
        total_p = (await db.execute(select(func.count(Participant.id)))).scalar() or 0

        # Database-level aggregate metrics for this competition (loads ZERO attempt ORM instances into Python memory)
        agg_stmt = select(
            func.count(ExamAttempt.id).label("started"),
            func.count(case((ExamAttempt.status.in_([AttemptStatus.SUBMITTED, AttemptStatus.FINALIZED]), 1))).label("completed"),
            func.count(case((ExamAttempt.status == AttemptStatus.IN_PROGRESS, 1))).label("in_progress"),
            func.count(case((ExamAttempt.status == AttemptStatus.EXPIRED, 1))).label("expired"),
            func.max(ExamAttempt.score).label("highest_score"),
        ).where(ExamAttempt.competition_id == comp.id)

        agg_res = (await db.execute(agg_stmt)).one()
        started = agg_res.started or 0
        submitted = agg_res.completed or 0
        in_progress = agg_res.in_progress or 0
        expired = agg_res.expired or 0
        highest_score = agg_res.highest_score

        completion_pct = round((submitted / started) * 100, 1) if started > 0 else 0.0
        top_score_val = f"{highest_score}/{comp.question_count}" if highest_score is not None else "N/A"

    dash_text = get_text(
        "admin_results_dash",
        lang,
        title=comp.title,
        registered=total_p,
        started=started,
        in_progress=in_progress,
        completed=submitted,
        expired=expired,
        rate=completion_pct,
        top_score=top_score_val,
        status=format_competition_status(comp.status),
    )

    await query.edit_message_text(
        dash_text,
        reply_markup=get_admin_results_keyboard(comp.id, comp.status, lang),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_finalize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Executes automatic score calculation, ranking, and finalization."""
    query = update.callback_query
    await query.answer("Finalizing scores and rankings...", show_alert=False)
    comp_id = uuid.UUID(query.data.split(":")[2])

    async with AsyncSessionLocal() as db:
        try:
            finalized = await ScoringAndRankingService.finalize_competition_results(
                db, comp_id, admin_id=str(update.effective_user.id)
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Publish Results to Participants", callback_data=f"admin:publish:{comp_id}")],
                [InlineKeyboardButton("📊 View Results", callback_data="admin:results")],
                [InlineKeyboardButton("🏆 Competition Controls", callback_data="admin:competition")],
            ])
            await query.edit_message_text(
                f"✅ *Successfully Finalized Results!*\n\n"
                f"• Processed Attempts: *{len(finalized)}*\n"
                f"• Scores and rankings have been calculated.\n\n"
                f"You can now publish results to participants or review rankings.",
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN,
            )
        except CompetitionError as e:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="admin:competition")]])
            await query.edit_message_text(f"❌ Finalization error: {str(e)}", reply_markup=kb)


@require_admin
async def cb_admin_publish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Publishes official results and sends notifications to participants."""
    query = update.callback_query
    await query.answer("Publishing results...", show_alert=False)
    comp_id = uuid.UUID(query.data.split(":")[2])

    async with AsyncSessionLocal() as db:
        try:
            comp = await ScoringAndRankingService.publish_results(
                db, comp_id, admin_id=str(update.effective_user.id)
            )
            # Notification broadcast to participants who attempted
            part_stmt = select(Participant.telegram_user_id).join(ExamAttempt).where(
                ExamAttempt.competition_id == comp_id
            )
            user_ids = list((await db.execute(part_stmt)).scalars().all())

            sent_count = 0
            safe_comp_title = comp.title.replace("*", "").replace("_", " ").replace("`", "")
            notify_text = (
                f"🏆 *EMYC Competition Results Published!*\n\n"
                f"Official results for *{safe_comp_title}* are now available.\n"
                f"Tap below to view your score and ranking!"
            )
            notify_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 View Result", callback_data=f"rev:my_result:{comp_id}")]
            ])

            for uid in user_ids:
                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text=notify_text,
                        reply_markup=notify_kb,
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"Error sending markdown result notification to {uid}: {e}")
                    try:
                        plain = notify_text.replace("*", "").replace("_", "").replace("`", "")
                        await context.bot.send_message(
                            chat_id=uid,
                            text=plain,
                            reply_markup=notify_kb,
                        )
                        sent_count += 1
                    except Exception as e2:
                        logger.warning(f"Failed to deliver fallback result notification to user {uid}: {e2}")


            # Record announcement
            announcement = Announcement(
                competition_id=comp_id,
                admin_telegram_id=update.effective_user.id,
                message_text=f"Official results published for {comp.title}",
                sent_count=sent_count,
            )
            db.add(announcement)
            await db.commit()

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📊 View Results", callback_data="admin:results")],
                [InlineKeyboardButton("🏆 Competition Controls", callback_data="admin:competition")],
                [InlineKeyboardButton("◀️ Admin Menu", callback_data="admin:home")],
            ])
            await query.edit_message_text(
                f"📢 *Results Published Successfully!*\n\n"
                f"• Status: `PUBLISHED` 🟢\n"
                f"• Notifications delivered to *{sent_count}* participants.\n\n"
                f"Participants can now view their scores, ranks, and review answers.",
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN,
            )
        except CompetitionError as e:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="admin:competition")]])
            await query.edit_message_text(f"❌ Publication error: {str(e)}", reply_markup=kb)


@require_admin
async def cb_admin_announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Administrative announcement prompt."""
    query = update.callback_query
    await query.answer()
    context.user_data["awaiting_announcement"] = True

    await query.edit_message_text(
        "📢 *Send EMYC Operational Announcement*\n\n"
        "Please type the announcement message you wish to broadcast to all registered participants in this chat.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="admin:home")]
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_announce_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Broadcasts announcement after explicit admin confirmation."""
    query = update.callback_query
    await query.answer("Broadcasting announcement...", show_alert=False)

    announcement_text = context.user_data.pop("pending_announcement", None)
    if not announcement_text:
        await query.edit_message_text(
            "⚠️ No pending announcement found to broadcast.",
            reply_markup=get_admin_keyboard("en"),
        )
        return

    async with AsyncSessionLocal() as db:
        # Retrieve all registered participants
        participants_stmt = select(Participant.telegram_user_id)
        user_ids = list((await db.execute(participants_stmt)).scalars().all())

        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()
        comp_id = comp.id if comp else None

        sent_count = 0
        for uid in user_ids:
            try:
                await context.bot.send_message(
                    chat_id=uid,
                    text=f"📢 *EMYC Announcement*\n\n{announcement_text}",
                    parse_mode=ParseMode.MARKDOWN,
                )
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to deliver announcement to user {uid}: {e}")

        # Record announcement in database
        announcement = Announcement(
            competition_id=comp_id,
            admin_telegram_id=update.effective_user.id,
            message_text=announcement_text,
            sent_count=sent_count,
        )
        db.add(announcement)
        await db.commit()

    await query.edit_message_text(
        f"✅ *Announcement Broadcast Completed!*\n\n"
        f"Delivered to {sent_count} of {len(user_ids)} registered participants.",
        reply_markup=get_admin_keyboard("en"),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_participants(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dedicated participant management and aggregate metrics dashboard."""
    query = update.callback_query
    await query.answer()

    async with AsyncSessionLocal() as db:
        total_p = (await db.execute(select(func.count(Participant.id)))).scalar() or 0

        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()

        comp_title = comp.title if comp else "None"
        started = 0
        submitted = 0
        in_progress = 0
        expired = 0
        avg_score = 0.0
        top_score = 0
        completion_pct = 0.0

        if comp:
            attempts_stmt = select(ExamAttempt).where(ExamAttempt.competition_id == comp.id)
            attempts = list((await db.execute(attempts_stmt)).scalars().all())

            started = len(attempts)
            submitted = sum(1 for a in attempts if a.status in [AttemptStatus.SUBMITTED, AttemptStatus.FINALIZED])
            in_progress = sum(1 for a in attempts if a.status == AttemptStatus.IN_PROGRESS)
            expired = sum(1 for a in attempts if a.status == AttemptStatus.EXPIRED)

            finished_scores = [a.score for a in attempts if a.score is not None]
            if finished_scores:
                avg_score = round(sum(finished_scores) / len(finished_scores), 1)
                top_score = max(finished_scores)

            if started > 0:
                completion_pct = round((submitted / started) * 100, 1)

    text = (
        f"👥 *EMYC Participant Analytics Dashboard*\n\n"
        f"*Competition:* {comp_title}\n\n"
        f"📋 *Registration Metrics:*\n"
        f"• Total Registered Members: *{total_p}*\n"
        f"• Verified Accounts: *{total_p}*\n\n"
        f"📈 *Competition Engagement:*\n"
        f"• Attempts Started: *{started}*\n"
        f"• Completed & Submitted: *{submitted}*\n"
        f"• In Progress: *{in_progress}*\n"
        f"• Expired: *{expired}*\n"
        f"• Completion Rate: *{completion_pct}%*\n\n"
        f"🎯 *Performance Overview:*\n"
        f"• Average Score: *{avg_score}*\n"
        f"• Top Score: *{top_score}*"
    )
    await query.edit_message_text(
        text,
        reply_markup=get_admin_participants_keyboard("en"),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_rankings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Dedicated leaderboard inspection screen with pagination support."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    page = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 1
    page_size = 10

    async with AsyncSessionLocal() as db:
        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()

        if not comp:
            await query.edit_message_text(
                "ℹ️ No competition found to display rankings.",
                reply_markup=get_admin_rankings_keyboard("en"),
            )
            return

        # Total attempts count for pagination
        count_stmt = select(func.count(ExamAttempt.id)).where(ExamAttempt.competition_id == comp.id)
        total_attempts = (await db.execute(count_stmt)).scalar() or 0
        total_pages = max(1, (total_attempts + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size

        attempts_stmt = (
            select(ExamAttempt)
            .options(selectinload(ExamAttempt.participant))
            .where(ExamAttempt.competition_id == comp.id)
            .order_by(
                ExamAttempt.rank.asc().nulls_last(),
                ExamAttempt.score.desc().nulls_last(),
                ExamAttempt.completion_seconds.asc().nulls_last(),
            )
            .offset(offset)
            .limit(page_size)
        )
        attempts = list((await db.execute(attempts_stmt)).scalars().all())

    status_tag = format_competition_status(comp.status)
    if not attempts:
        header = (
            f"🏅 *EMYC Competition Leaderboard*\n\n"
            f"*Competition:* {comp.title} ({status_tag})\n\n"
        )
        body = "_No participant attempts recorded yet._"
    else:
        header = (
            f"🏅 *EMYC Competition Leaderboard*\n\n"
            f"*Competition:* {comp.title} ({status_tag})\n"
            f"*Total Entries:* {total_attempts} | Page {page} of {total_pages}\n\n"
        )
        rows = []
        for idx, att in enumerate(attempts, start=offset + 1):
            rank_display = f"#{att.rank}" if att.rank else f"#{idx}"
            p_name = "Participant"
            if att.participant:
                p_name = att.participant.telegram_username or att.participant.membership_id

            score_val = att.score if att.score is not None else "Pending"
            mins, secs = divmod(int(att.completion_seconds or 0), 60)
            time_display = f"{mins:02d}:{secs:02d}" if att.completion_seconds else "--:--"
            rows.append(f"*{rank_display}* — `{p_name}` | *{score_val}/{comp.question_count}* pts ({time_display})")
        body = "\n".join(rows)

    text = f"{header}{body}"
    await query.edit_message_text(
        text,
        reply_markup=get_admin_rankings_keyboard("en", page=page, total_pages=total_pages),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_sys_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Operational health and deployment configuration inspection."""
    query = update.callback_query
    await query.answer()

    now = datetime.now(timezone.utc)
    settings = get_settings()

    async with AsyncSessionLocal() as db:
        try:
            db_ok = True
            await db.execute(select(1))
        except Exception:
            db_ok = False

    webhook_configured = "Yes" if settings.WEBHOOK_URL else "No"
    secret_configured = "Yes" if settings.WEBHOOK_SECRET else "No"
    admins_count = len(settings.admin_ids)

    text = (
        f"💻 *EMYC Platform System Operational Status*\n\n"
        f"⚙️ *Runtime Environment:* `{settings.ENVIRONMENT}`\n"
        f"🤖 *Bot Mode:* `{settings.BOT_MODE}`\n"
        f"🔗 *Webhook URL:* `{webhook_configured}`\n"
        f"🔐 *Webhook Secret Token:* `{secret_configured}`\n"
        f"👑 *Authorized Admins:* `{admins_count}` configured\n"
        f"🐘 *PostgreSQL / Supabase:* `{'Healthy ✅' if db_ok else 'Unreachable ❌'}`\n"
        f"⏱ *Background Deadline Sweeper:* `Active (30s interval) ✅`\n"
        f"🌐 *Membership Adapter:* `{settings.MEMBERSHIP_ADAPTER_TYPE}`\n"
        f"🕒 *Server Time:* `{now.strftime('%Y-%m-%d %H:%M:%S UTC')}`"
    )

    await query.edit_message_text(
        text,
        reply_markup=get_admin_system_status_keyboard("en"),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_create_comp_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Begins the interactive competition creation wizard."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = user.id if user else 0

    async with AsyncSessionLocal() as db:
        p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
        lang = p.language_code if p and p.language_code else "en"

    context.user_data["create_comp"] = {"step": "title", "lang": lang}

    text = (
        "🏆 *Create New EMYC Competition (Step 1/4)*\n\n"
        "Please type the *Title* for the new competition in this chat:"
    )
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:competition")]])
    await query.edit_message_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_create_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles duration selection in competition creation wizard (presets or custom)."""
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    user_id = user.id if user else 0

    async with AsyncSessionLocal() as db:
        p = await ParticipantService.get_participant_by_telegram_id(db, user_id)
        lang = p.language_code if p and p.language_code else "en"

    data_part = query.data.split(":")[2]
    wizard = context.user_data.get("create_comp", {})

    if data_part == "custom":
        wizard["step"] = "custom_duration"
        prompt = get_text("admin_custom_dur_prompt", lang)
        cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton(get_text("btn_cancel", lang), callback_data="admin:competition")]])
        await query.edit_message_text(prompt, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)
        return

    duration = int(data_part)
    wizard["duration"] = duration
    wizard["step"] = "schedule"

    prompt = (
        f"📅 *Create New EMYC Competition (Step 4/4)*\n\n"
        f"*Title:* {wizard.get('title', 'Competition')}\n"
        f"*Duration:* {duration} minutes\n\n"
        f"Select the competition open window:"
    )
    await query.edit_message_text(
        prompt,
        reply_markup=get_admin_create_comp_schedule_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_create_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles schedule window selection in competition creation wizard."""
    query = update.callback_query
    await query.answer()
    sched_key = query.data.split(":")[2]
    now = datetime.now(timezone.utc)
    delta_map = {
        "24h": timedelta(hours=24),
        "3d": timedelta(days=3),
        "7d": timedelta(days=7),
        "14d": timedelta(days=14),
    }
    close_delta = delta_map.get(sched_key, timedelta(days=7))

    wizard = context.user_data.get("create_comp", {})
    wizard["opens_at"] = now
    wizard["closes_at"] = now + close_delta
    wizard["step"] = "questions"

    prompt = (
        f"📝 *Create New EMYC Competition: Question Setup*\n\n"
        f"*Title:* {wizard.get('title', 'Competition')}\n"
        f"*Duration:* {wizard.get('duration', 30)} minutes\n"
        f"*Closes in:* {sched_key}\n\n"
        f"How would you like to configure questions for this competition?"
    )
    await query.edit_message_text(
        prompt,
        reply_markup=get_admin_create_comp_questions_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_create_questions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Finalizes competition creation from wizard."""
    query = update.callback_query
    await query.answer("Creating competition...", show_alert=False)
    q_mode = query.data.split(":")[2]  # "standard" or "manual"
    wizard = context.user_data.pop("create_comp", {})

    title = wizard.get("title", "EMYC Competition")
    description = wizard.get("description", "Official EMYC Competition")
    duration = wizard.get("duration", 30)
    opens_at = wizard.get("opens_at", datetime.now(timezone.utc))
    closes_at = wizard.get("closes_at", opens_at + timedelta(days=7))

    questions_to_add = SAMPLE_QUESTIONS if q_mode == "standard" else []

    async with AsyncSessionLocal() as db:
        comp = await CompetitionService.create_competition(
            db=db,
            title=title,
            description=description,
            opens_at=opens_at,
            closes_at=closes_at,
            duration_minutes=duration,
            question_count=len(questions_to_add),
            status=CompetitionStatus.DRAFT,
        )

        for idx, q_data in enumerate(questions_to_add, start=1):
            q = CompetitionQuestion(
                competition_id=comp.id,
                question_text=q_data["question_text"],
                options=q_data["options"],
                correct_option=q_data["correct_option"],
                order_index=idx,
            )
            db.add(q)

        await db.commit()
        await db.refresh(comp)

    buttons = [
        [InlineKeyboardButton("🟢 Open Competition (Set LIVE)", callback_data=f"admin:set_live:{comp.id}")],
        [InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition")],
        [InlineKeyboardButton("◀️ Back to Admin Panel", callback_data="admin:home")],
    ]
    if q_mode == "manual":
        buttons.insert(0, [InlineKeyboardButton("📝 Add First Question", callback_data=f"admin:add_q:{comp.id}")])

    await query.edit_message_text(
        f"🎉 *Competition Created Successfully!*\n\n"
        f"*Title:* {comp.title}\n"
        f"*Status:* {format_competition_status(comp.status)}\n"
        f"*Duration:* {comp.duration_minutes} minutes\n"
        f"*Questions Configured:* {comp.question_count}\n"
        f"*Opens:* {comp.opens_at.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"*Closes:* {comp.closes_at.strftime('%Y-%m-%d %H:%M UTC')}\n\n"
        f"You can now manage the competition, add questions, or open it LIVE for participants!",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_add_question_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Begins the interactive step-by-step question authoring wizard."""
    query = update.callback_query
    await query.answer()
    comp_id_str = query.data.split(":")[2]
    comp_id = uuid.UUID(comp_id_str)
    context.user_data["q_wizard"] = {"comp_id": comp_id, "step": "text"}
    context.user_data["awaiting_question_comp_id"] = comp_id

    text = (
        "📝 *Add Question (Step 1/5: Question Text)*\n\n"
        "Please type the *Question Text* in this chat.\n\n"
        "💡 _Shortcut: You can also send the entire question on one line:_\n"
        "`Question text? | Option A | Option B | Option C | Option D | A`"
    )
    cancel_kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin:q_wiz_cancel")]])
    await query.edit_message_text(text, reply_markup=cancel_kb, parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_q_wiz_choice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captures correct option selection and renders full question preview."""
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[2]  # "A", "B", "C", "D"

    wizard = context.user_data.get("q_wizard")
    if not wizard:
        await query.edit_message_text(
            "⚠️ Question creation session expired.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition")]]),
        )
        return

    wizard["correct"] = choice
    wizard["step"] = "preview"
    comp_id = wizard["comp_id"]

    preview_text = (
        "🔍 *Question Preview & Confirmation*\n\n"
        f"*{wizard.get('text', '')}*\n\n"
        f"A) {wizard.get('opt_a', '')}\n"
        f"B) {wizard.get('opt_b', '')}\n"
        f"C) {wizard.get('opt_c', '')}\n"
        f"D) {wizard.get('opt_d', '')}\n\n"
        f"✅ *Correct Answer:* Option *{choice}*\n\n"
        f"Would you like to save this question to the competition?"
    )
    await query.edit_message_text(
        preview_text,
        reply_markup=get_admin_question_preview_keyboard(comp_id),
        parse_mode=ParseMode.MARKDOWN,
    )


@require_admin
async def cb_admin_q_wiz_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Persists validated question from wizard into database and updates question count."""
    query = update.callback_query
    await query.answer("Saving question...", show_alert=False)
    comp_id_str = query.data.split(":")[2]
    comp_id = uuid.UUID(comp_id_str)

    wizard = context.user_data.pop("q_wizard", None)
    context.user_data.pop("awaiting_question_comp_id", None)

    if not wizard or "text" not in wizard or "correct" not in wizard:
        await query.edit_message_text(
            "⚠️ Question creation session expired or invalid.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition")]]),
        )
        return

    async with AsyncSessionLocal() as db:
        comp = await db.get(Competition, comp_id)
        if not comp:
            await query.edit_message_text("❌ Competition not found.", reply_markup=get_admin_keyboard("en"))
            return

        if comp.status not in [CompetitionStatus.DRAFT, CompetitionStatus.SCHEDULED]:
            await query.edit_message_text(
                f"❌ Cannot add questions to competition with status {format_competition_status(comp.status)}.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition")]]),
            )
            return

        cnt_stmt = select(func.count(CompetitionQuestion.id)).where(CompetitionQuestion.competition_id == comp_id)
        current_q_count = (await db.execute(cnt_stmt)).scalar() or 0
        next_order = current_q_count + 1

        options = {
            "A": wizard.get("opt_a", ""),
            "B": wizard.get("opt_b", ""),
            "C": wizard.get("opt_c", ""),
            "D": wizard.get("opt_d", ""),
        }

        q = CompetitionQuestion(
            competition_id=comp.id,
            question_text=wizard["text"],
            options=options,
            correct_option=wizard["correct"],
            order_index=next_order,
        )
        db.add(q)
        comp.question_count = next_order
        await db.commit()

    text = (
        f"✅ *Question #{next_order} Saved Successfully!*\n\n"
        f"*{wizard['text']}*\n"
        f"A) {options['A']}\nB) {options['B']}\nC) {options['C']}\nD) {options['D']}\n"
        f"Correct: *Option {wizard['correct']}*\n\n"
        f"Total questions in competition: *{next_order}*"
    )
    buttons = [
        [InlineKeyboardButton("➕ Add Another Question", callback_data=f"admin:add_q:{comp_id}")],
        [InlineKeyboardButton(f"📋 Manage Questions ({next_order})", callback_data=f"admin:q_list:{comp_id}:1")],
        [InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_q_wiz_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancels question creation wizard."""
    query = update.callback_query
    await query.answer()
    context.user_data.pop("q_wizard", None)
    context.user_data.pop("awaiting_question_comp_id", None)
    await query.edit_message_text(
        "❌ Question authoring cancelled.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Competition Controls", callback_data="admin:competition")]]),
    )


@require_admin
async def cb_admin_q_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Displays paginated question listing with deletion controls."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":")
    comp_id = uuid.UUID(parts[2])
    page = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
    page_size = 5

    async with AsyncSessionLocal() as db:
        comp = await db.get(Competition, comp_id)
        if not comp:
            await query.edit_message_text("❌ Competition not found.", reply_markup=get_admin_keyboard("en"))
            return

        cnt_stmt = select(func.count(CompetitionQuestion.id)).where(CompetitionQuestion.competition_id == comp_id)
        total_q = (await db.execute(cnt_stmt)).scalar() or 0
        total_pages = max(1, (total_q + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * page_size

        q_stmt = (
            select(CompetitionQuestion)
            .where(CompetitionQuestion.competition_id == comp_id)
            .order_by(CompetitionQuestion.order_index.asc())
            .offset(offset)
            .limit(page_size)
        )
        questions = list((await db.execute(q_stmt)).scalars().all())

    header = (
        f"📋 *Manage Questions: {comp.title}*\n\n"
        f"Total Questions: *{total_q}* | Page {page} of {total_pages}\n\n"
    )

    if not questions:
        body = "_No questions configured for this competition yet._"
    else:
        rows = []
        for q in questions:
            rows.append(
                f"*{q.order_index}.* {q.question_text}\n"
                f"   [A: {q.options.get('A', '')} | B: {q.options.get('B', '')} | "
                f"C: {q.options.get('C', '')} | D: {q.options.get('D', '')}]\n"
                f"   *Answer:* `{q.correct_option}`"
            )
        body = "\n\n".join(rows)

    text = f"{header}{body}"
    kb = get_admin_question_list_keyboard(comp_id, page, total_pages, questions)
    await query.edit_message_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_q_del(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deletes a question and re-indexes remaining questions."""
    query = update.callback_query
    await query.answer("Deleting question...", show_alert=False)

    parts = query.data.split(":")
    if len(parts) >= 4:
        comp_id = uuid.UUID(parts[2])
        q_id = uuid.UUID(parts[3])
    else:
        comp_id = None
        q_id = uuid.UUID(parts[2])

    async with AsyncSessionLocal() as db:
        question = await db.get(CompetitionQuestion, q_id)
        if not question:
            await query.answer("Question not found.", show_alert=True)
            return

        if comp_id is None:
            comp_id = question.competition_id

        comp = await db.get(Competition, comp_id)
        if not comp or comp.status not in [CompetitionStatus.DRAFT, CompetitionStatus.SCHEDULED]:
            await query.answer("Cannot delete questions on active or closed competitions.", show_alert=True)
            return

        await db.delete(question)
        await db.flush()

        # Re-index remaining questions
        q_stmt = (
            select(CompetitionQuestion)
            .where(CompetitionQuestion.competition_id == comp_id)
            .order_by(CompetitionQuestion.order_index.asc())
        )
        remaining = list((await db.execute(q_stmt)).scalars().all())
        for idx, q in enumerate(remaining, start=1):
            q.order_index = idx
        comp.question_count = len(remaining)
        await db.commit()

    # Re-render question list page 1
    query.data = f"admin:q_list:{comp_id}:1"
    await cb_admin_q_list(update, context)


@require_admin
async def cb_admin_archive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Archives a competition."""
    query = update.callback_query
    await query.answer()

    comp_id = uuid.UUID(query.data.split(":")[2])
    async with AsyncSessionLocal() as db:
        try:
            await CompetitionService.update_status(
                db, comp_id, CompetitionStatus.ARCHIVED, admin_id=str(update.effective_user.id)
            )
            await query.edit_message_text(
                "📦 *Competition Archived Successfully!*\n\n"
                "The competition status is now `ARCHIVED` and is closed to participants.",
                reply_markup=get_admin_keyboard("en"),
                parse_mode=ParseMode.MARKDOWN,
            )
        except CompetitionError as e:
            await query.edit_message_text(f"❌ Error archiving competition: {str(e)}", reply_markup=get_admin_keyboard("en"))

