from fastapi import APIRouter, Header, HTTPException, Request, Response, status
from telegram import Update

from app.core.config import get_settings
from app.core.logging import logger
from app.bot.bot_app import build_application

router = APIRouter()
settings = get_settings()

# Lazily initialized telegram application
_telegram_app = None


def get_telegram_application():
    global _telegram_app
    if _telegram_app is None:
        _telegram_app = build_application()
    return _telegram_app


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
