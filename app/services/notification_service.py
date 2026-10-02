from typing import List, Optional
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import Application

from app.core.logging import logger, log_audit_event
from app.locales.translator import get_text
from app.models.participant import Participant
from app.models.attempt import ExamAttempt
from app.models.competition import Competition
from app.models.announcement import Announcement


class NotificationService:
    @staticmethod
    async def send_result_notifications(
        db: AsyncSession,
        bot_app: Application,
        competition: Competition,
        admin_telegram_id: int,
    ) -> int:
        """Sends localized publication notifications to all participants who attempted the competition."""
        # Find all participants who took part in this competition
        stmt = (
            select(Participant)
            .join(ExamAttempt, ExamAttempt.participant_id == Participant.id)
            .where(ExamAttempt.competition_id == competition.id)
            .distinct()
        )
        res = await db.execute(stmt)
        participants = list(res.scalars().all())

        sent_count = 0
        for p in participants:
            lang = p.language_code or "en"
            msg = (
                f"🏆 *{get_text('results_title', lang, score='...', total=competition.question_count, rank='...', time='...').splitlines()[0]}*\n\n"
                f"Official results for *{competition.title}* have been published!\n"
                f"Tap the button below to view your score, rank, and answer review."
            )
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(get_text("view_result_btn", lang), callback_data="menu:start")]
            ])
            try:
                await bot_app.bot.send_message(
                    chat_id=p.telegram_user_id,
                    text=msg,
                    reply_markup=keyboard,
                    parse_mode=ParseMode.MARKDOWN,
                )
                sent_count += 1
            except Exception as e:
                logger.warning(f"Could not deliver notification to telegram_user_id={p.telegram_user_id}: {e}")

        # Store audit announcement record
        announcement = Announcement(
            competition_id=competition.id,
            admin_telegram_id=admin_telegram_id,
            message_text=f"Official results announcement for {competition.title}",
            sent_count=sent_count,
        )
        db.add(announcement)
        await db.commit()

        log_audit_event("NOTIFICATIONS_DISPATCHED", "SYSTEM", str(admin_telegram_id), {
            "competition_id": str(competition.id),
            "sent_count": sent_count,
        })
        return sent_count

    @staticmethod
    async def broadcast_announcement(
        db: AsyncSession,
        bot_app: Application,
        admin_telegram_id: int,
        message_text: str,
        competition_id: Optional[uuid.UUID] = None,
    ) -> int:
        """Broadcasts an administrative announcement to all registered participants."""
        stmt = select(Participant.telegram_user_id)
        res = await db.execute(stmt)
        user_ids = list(res.scalars().all())

        sent_count = 0
        for uid in user_ids:
            try:
                await bot_app.bot.send_message(
                    chat_id=uid,
                    text=f"📢 *Announcement*\n\n{message_text}",
                    parse_mode=ParseMode.MARKDOWN,
                )
                sent_count += 1
            except Exception as e:
                logger.warning(f"Failed to deliver announcement to {uid}: {e}")

        announcement = Announcement(
            competition_id=competition_id,
            admin_telegram_id=admin_telegram_id,
            message_text=message_text,
            sent_count=sent_count,
        )
        db.add(announcement)
        await db.commit()
        return sent_count
