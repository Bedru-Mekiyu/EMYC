"""API v1 package."""
from fastapi import APIRouter
from app.api.v1.health import router as health_router
from app.api.v1.telegram_webhook import router as webhook_router
from app.api.v1.webapp import router as webapp_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(health_router, prefix="/health", tags=["Health"])
api_v1_router.include_router(webhook_router, prefix="/telegram", tags=["Telegram Webhook"])
api_v1_router.include_router(webapp_router, prefix="/webapp", tags=["Telegram Mini App"])
