from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from telegram import Update

from app.core.config import get_settings
from app.core.logging import logger
from app.bot.bot_app import build_application

router = APIRouter()
settings = get_settings()

# Lazily initialized telegram application singleton
_telegram_app = None


def get_telegram_application():
    global _telegram_app
    if _telegram_app is None:
        _telegram_app = build_application()
    return _telegram_app


async def init_telegram_webhook_app():
    """Initializes the telegram application and registers the webhook in webhook mode."""
    app = get_telegram_application()
    await app.initialize()
    await app.start()

    webhook_target = settings.get_normalized_webhook_url()
    if webhook_target:
        try:
            kwargs = {
                "url": webhook_target,
                "allowed_updates": Update.ALL_TYPES,
                "drop_pending_updates": False,
            }
            if settings.WEBHOOK_SECRET:
                kwargs["secret_token"] = settings.WEBHOOK_SECRET

            await app.bot.set_webhook(**kwargs)
            logger.info(f"Successfully registered Telegram webhook: url={webhook_target}")
        except Exception as e:
            logger.error(f"Failed to register Telegram webhook: {e}", exc_info=True)
            if settings.ENVIRONMENT == "production":
                raise

    return app


async def shutdown_telegram_webhook_app():
    """Shuts down the telegram application cleanly."""
    global _telegram_app
    if _telegram_app is not None:
        try:
            await _telegram_app.stop()
            await _telegram_app.shutdown()
            logger.info("Telegram bot application stopped and shutdown cleanly.")
        except Exception as e:
            logger.warning(f"Error shutting down telegram application: {e}")


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str = Header(None),
):
    """Processes incoming Telegram updates via Webhook with secret token verification."""
    # Webhook secret verification
    if settings.WEBHOOK_SECRET and x_telegram_bot_api_secret_token != settings.WEBHOOK_SECRET:
        logger.warning("Rejected webhook update: invalid or missing secret token")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Invalid webhook secret"
        )

    try:
        data = await request.json()
        telegram_app = get_telegram_application()
        update = Update.de_json(data, telegram_app.bot)
        if update:
            await telegram_app.process_update(update)
    except Exception as e:
        logger.error(f"Error processing Telegram webhook update: {e}", exc_info=True)
        # Always return 200 to Telegram to prevent retry hammering
        return Response(status_code=status.HTTP_200_OK)

    return Response(status_code=status.HTTP_200_OK)
