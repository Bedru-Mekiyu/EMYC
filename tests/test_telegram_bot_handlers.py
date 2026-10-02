from unittest.mock import AsyncMock, patch, MagicMock
import pytest
from httpx import AsyncClient
from telegram import Update, User, Chat, Message, CallbackQuery
from telegram.ext import ContextTypes

from app.core.config import get_settings
from app.bot.handlers.admin import require_admin
from app.bot.handlers.participant import cmd_start, cb_select_language

settings = get_settings()


@pytest.mark.asyncio
async def test_webhook_secret_verification(client: AsyncClient):
    """Verifies that the Telegram webhook rejects unauthorized requests without valid secret token."""
    # Set webhook secret
    with patch.object(settings, "WEBHOOK_SECRET", "test_secret_key"):
        # 1. No secret header -> 403
        resp = await client.post("/api/v1/telegram/webhook", json={"update_id": 12345})
        assert resp.status_code == 403

        # 2. Wrong secret header -> 403
        resp = await client.post(
            "/api/v1/telegram/webhook",
            json={"update_id": 12345},
            headers={"x-telegram-bot-api-secret-token": "wrong_secret"},
        )
        assert resp.status_code == 403

        # 3. Valid secret header -> 200
        resp = await client.post(
            "/api/v1/telegram/webhook",
            json={"update_id": 12345},
            headers={"x-telegram-bot-api-secret-token": "test_secret_key"},
        )
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_authorization_guard():
    """Verifies that non-admin Telegram users are blocked from admin handlers."""
    # Configure admin IDs
    settings.ADMIN_TELEGRAM_IDS = "999001"

    # Dummy handler wrapped with @require_admin
    mock_inner_handler = AsyncMock(return_value="executed")
    guarded_handler = require_admin(mock_inner_handler)

    # 1. Non-admin user
    non_admin_user = User(id=111002, first_name="Regular", is_bot=False)
    update_non_admin = MagicMock(spec=Update)
    update_non_admin.effective_user = non_admin_user
    update_non_admin.callback_query = None
    update_non_admin.message = MagicMock()
    update_non_admin.message.reply_text = AsyncMock()

    context = MagicMock(spec=ContextTypes.DEFAULT_TYPE)
    await guarded_handler(update_non_admin, context)

    # Ensure inner handler was NOT executed and unauthorized message was sent
    mock_inner_handler.assert_not_called()
    update_non_admin.message.reply_text.assert_called_once_with("⛔ Unauthorized access.")

    # 2. Authorized admin user
    admin_user = User(id=999001, first_name="SuperAdmin", is_bot=False)
    update_admin = MagicMock(spec=Update)
    update_admin.effective_user = admin_user
    update_admin.callback_query = None
    update_admin.message = MagicMock()

    res = await guarded_handler(update_admin, context)
    mock_inner_handler.assert_called_once()
    assert res == "executed"
