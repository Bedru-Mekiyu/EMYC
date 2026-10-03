from typing import Optional
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
)

from app.core.config import get_settings
from app.core.logging import logger
from app.bot.handlers.participant import (
    cmd_start,
    cmd_help,
    cb_language_menu,
    cb_select_language,
    cb_start_flow,
    cb_exam_start,
    cb_answer,
    cb_question_nav,
    cb_submit_exam,
    cb_review_answers,
    handle_text_message,
)
from app.bot.handlers.admin import (
    cmd_admin,
    cb_admin_status,
    cb_admin_competition,
    cb_admin_setup_sample,
    cb_admin_to_participant,
    cb_admin_set_status,
    cb_admin_results,
    cb_admin_finalize,
    cb_admin_publish,
    cb_admin_announce,
    cb_admin_announce_confirm,
    cb_admin_participants,
    cb_admin_rankings,
    cb_admin_sys_status,
    cb_admin_create_comp_start,
    cb_admin_create_duration,
    cb_admin_create_schedule,
    cb_admin_create_questions,
    cb_admin_add_question_prompt,
)

settings = get_settings()


async def error_handler(update: object, context) -> None:
    """Logs unexpected Telegram bot errors."""
    logger.error(f"Telegram bot exception occurred: {context.error}", exc_info=context.error)


def build_application(token: Optional[str] = None) -> Application:
    """Builds and wires up the Telegram Application with all handlers."""
    bot_token = token or settings.TELEGRAM_BOT_TOKEN
    app = ApplicationBuilder().token(bot_token).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("admin", cmd_admin))

    # Participant Callback Queries
    app.add_handler(CallbackQueryHandler(cmd_start, pattern="^menu:home$"))
    app.add_handler(CallbackQueryHandler(cmd_help, pattern="^menu:help$"))
    app.add_handler(CallbackQueryHandler(cb_language_menu, pattern="^menu:lang$"))
    app.add_handler(CallbackQueryHandler(cb_select_language, pattern="^lang:"))
    app.add_handler(CallbackQueryHandler(cb_start_flow, pattern="^menu:start$"))
    app.add_handler(CallbackQueryHandler(cb_exam_start, pattern="^exam:start:"))
    app.add_handler(CallbackQueryHandler(cb_answer, pattern="^ans:"))
    app.add_handler(CallbackQueryHandler(cb_question_nav, pattern="^q:nav:"))
    app.add_handler(CallbackQueryHandler(cb_submit_exam, pattern="^exam:submit:"))
    app.add_handler(CallbackQueryHandler(cb_review_answers, pattern="^rev:"))

    # Admin Callback Queries
    app.add_handler(CallbackQueryHandler(cmd_admin, pattern="^admin:home$"))
    app.add_handler(CallbackQueryHandler(cb_admin_status, pattern="^admin:status$"))
    app.add_handler(CallbackQueryHandler(cb_admin_participants, pattern="^admin:participants$"))
    app.add_handler(CallbackQueryHandler(cb_admin_rankings, pattern="^admin:rankings$"))
    app.add_handler(CallbackQueryHandler(cb_admin_sys_status, pattern="^admin:sys_status$"))
    app.add_handler(CallbackQueryHandler(cb_admin_competition, pattern="^admin:competition$"))
    app.add_handler(CallbackQueryHandler(cb_admin_create_comp_start, pattern="^admin:create_comp:start$"))
    app.add_handler(CallbackQueryHandler(cb_admin_create_duration, pattern="^admin:create_dur:"))
    app.add_handler(CallbackQueryHandler(cb_admin_create_schedule, pattern="^admin:create_sched:"))
    app.add_handler(CallbackQueryHandler(cb_admin_create_questions, pattern="^admin:create_q:"))
    app.add_handler(CallbackQueryHandler(cb_admin_add_question_prompt, pattern="^admin:add_q:"))
    app.add_handler(CallbackQueryHandler(cb_admin_setup_sample, pattern="^admin:setup_sample:"))
    app.add_handler(CallbackQueryHandler(cb_admin_to_participant, pattern="^admin:to_participant$"))
    app.add_handler(CallbackQueryHandler(cb_admin_set_status, pattern="^admin:set_"))
    app.add_handler(CallbackQueryHandler(cb_admin_results, pattern="^admin:results$"))
    app.add_handler(CallbackQueryHandler(cb_admin_finalize, pattern="^admin:finalize:"))
    app.add_handler(CallbackQueryHandler(cb_admin_publish, pattern="^admin:publish:"))
    app.add_handler(CallbackQueryHandler(cb_admin_announce, pattern="^admin:announce$"))
    app.add_handler(CallbackQueryHandler(cb_admin_announce_confirm, pattern="^admin:announce_confirm$"))

    # Text message handler (e.g. membership ID submission)
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_text_message))

    # Error handling
    app.add_error_handler(error_handler)

    return app
