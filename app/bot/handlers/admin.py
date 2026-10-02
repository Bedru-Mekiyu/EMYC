import uuid
from typing import Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from sqlalchemy import select, func, and_

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.core.logging import logger, log_audit_event
from app.locales.translator import get_text
from app.models.competition import Competition, CompetitionStatus
from app.models.participant import Participant
from app.models.attempt import ExamAttempt, AttemptStatus
from app.models.announcement import Announcement
from app.services.competition_service import CompetitionService, CompetitionError
from app.services.scoring_service import ScoringAndRankingService
from app.bot.keyboards import get_admin_keyboard, get_admin_confirm_announcement_keyboard

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


@require_admin
async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles /admin command with EMYC Competition Admin summary and controls."""
    # Clear any pending announcement state
    context.user_data.pop("awaiting_announcement", None)
    context.user_data.pop("pending_announcement", None)

    async with AsyncSessionLocal() as db:
        # Fetch active or latest competition
        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()

        title = comp.title if comp else "None"
        status = comp.status if comp else "N/A"

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
        "en",
        title=title,
        status=status,
        registered_count=total_p,
        started_count=started,
        in_progress_count=in_progress,
        submitted_count=submitted,
    )
    keyboard = get_admin_keyboard("en")

    if update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN)


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
        f"*Status:* `{comp.status}`\n"
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
    """Manages competition lifecycle (Open/Close)."""
    query = update.callback_query
    await query.answer()

    async with AsyncSessionLocal() as db:
        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()

        if not comp:
            await query.edit_message_text("No competition configured.", reply_markup=get_admin_keyboard("en"))
            return

        buttons = []
        if comp.status in [CompetitionStatus.DRAFT, CompetitionStatus.SCHEDULED]:
            buttons.append([InlineKeyboardButton("🟢 Open Competition (Set LIVE)", callback_data=f"admin:set_live:{comp.id}")])
        elif comp.status == CompetitionStatus.LIVE:
            buttons.append([InlineKeyboardButton("🔴 Close Competition (Set CLOSED)", callback_data=f"admin:set_closed:{comp.id}")])

        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="admin:home")])

        text = (
            f"⚙️ *EMYC Competition Lifecycle Control*\n\n"
            f"*Title:* {comp.title}\n"
            f"*Current Status:* `{comp.status}`\n"
            f"*Duration:* {comp.duration_minutes} min\n"
            f"*Questions:* {comp.question_count}\n"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)


@require_admin
async def cb_admin_set_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Transitions competition status."""
    query = update.callback_query
    await query.answer()
    # format: admin:set_live:<id> or admin:set_closed:<id>
    action, _, comp_id_str = query.data.split(":")
    comp_id = uuid.UUID(comp_id_str)
    new_status = CompetitionStatus.LIVE if "live" in action else CompetitionStatus.CLOSED

    async with AsyncSessionLocal() as db:
        try:
            await CompetitionService.update_status(db, comp_id, new_status, admin_id=str(update.effective_user.id))
            await query.edit_message_text(
                f"✅ Competition status successfully updated to `{new_status}`.",
                reply_markup=get_admin_keyboard("en"),
                parse_mode=ParseMode.MARKDOWN,
            )
        except CompetitionError as e:
            await query.edit_message_text(f"❌ Error: {str(e)}", reply_markup=get_admin_keyboard("en"))


@require_admin
async def cb_admin_results(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manages results finalization and publication."""
    query = update.callback_query
    await query.answer()

    async with AsyncSessionLocal() as db:
        stmt = select(Competition).order_by(Competition.created_at.desc()).limit(1)
        comp = (await db.execute(stmt)).scalar_one_or_none()
        if not comp:
            await query.edit_message_text("No competition found.", reply_markup=get_admin_keyboard("en"))
            return

        buttons = []
        if comp.status == CompetitionStatus.CLOSED:
            buttons.append([InlineKeyboardButton("📊 Finalize Scores & Rankings", callback_data=f"admin:finalize:{comp.id}")])
        elif comp.status == CompetitionStatus.RESULTS_FINALIZED:
            buttons.append([InlineKeyboardButton("📢 Publish Results to Participants", callback_data=f"admin:publish:{comp.id}")])

        buttons.append([InlineKeyboardButton("◀️ Back", callback_data="admin:home")])

        # Leaderboard snippet if finalized or published
        leaderboard_text = ""
        if comp.status in [CompetitionStatus.RESULTS_FINALIZED, CompetitionStatus.PUBLISHED]:
            top_stmt = (
                select(ExamAttempt)
                .where(ExamAttempt.competition_id == comp.id)
                .order_by(ExamAttempt.rank.asc())
                .limit(5)
            )
            top_attempts = list((await db.execute(top_stmt)).scalars().all())
            leaderboard_text = "\n\n🏅 *Top 5 Participants:*\n"
            for att in top_attempts:
                mins, secs = divmod(int(att.completion_seconds or 0), 60)
                leaderboard_text += f"#{att.rank} - Score: {att.score}/{comp.question_count} ({mins:02d}:{secs:02d})\n"

        text = (
            f"🏆 *EMYC Competition Results Management*\n\n"
            f"*Competition:* {comp.title}\n"
            f"*Status:* `{comp.status}`"
            f"{leaderboard_text}"
        )
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)


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
            await query.edit_message_text(
                f"✅ Successfully finalized results for {len(finalized)} attempts!\n\n"
                f"You can now review or officially publish the results.",
                reply_markup=get_admin_keyboard("en"),
                parse_mode=ParseMode.MARKDOWN,
            )
        except CompetitionError as e:
            await query.edit_message_text(f"❌ Finalization error: {str(e)}", reply_markup=get_admin_keyboard("en"))


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
            for uid in user_ids:
                try:
                    await context.bot.send_message(
                        chat_id=uid,
                        text=(
                            f"🏆 *EMYC Competition Results Published!*\n\n"
                            f"Official results for *{comp.title}* are now available.\n"
                            f"Tap below to view your score and ranking!"
                        ),
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("📊 View Result", callback_data="menu:start")]
                        ]),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                    sent_count += 1
                except Exception as e:
                    logger.warning(f"Failed to deliver result notification to user {uid}: {e}")

            # Record announcement
            announcement = Announcement(
                competition_id=comp_id,
                admin_telegram_id=update.effective_user.id,
                message_text=f"Official results published for {comp.title}",
                sent_count=sent_count,
            )
            db.add(announcement)
            await db.commit()

            await query.edit_message_text(
                f"📢 *Results Published Successfully!*\n\n"
                f"Competition is now marked `PUBLISHED`.\n"
                f"Notifications delivered to {sent_count} participants.",
                reply_markup=get_admin_keyboard("en"),
                parse_mode=ParseMode.MARKDOWN,
            )
        except CompetitionError as e:
            await query.edit_message_text(f"❌ Publication error: {str(e)}", reply_markup=get_admin_keyboard("en"))


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
