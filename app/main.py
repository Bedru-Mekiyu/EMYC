import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import setup_logging, logger
from app.api.v1 import api_v1_router
from app.tasks.deadline_sweeper import start_periodic_sweeper
from app.api.v1.telegram_webhook import init_telegram_webhook_app, shutdown_telegram_webhook_app

settings = get_settings()
setup_logging(settings.DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager for startup and shutdown hooks."""
    logger.info("Starting up Telegram Competitive Exam Platform backend...")
    logger.info(f"Environment: {settings.ENVIRONMENT}, Bot Mode: {settings.BOT_MODE}")

    # Automatic database schema migration for missing columns
    try:
        from app.core.database import engine
        from sqlalchemy import text
        async with engine.begin() as conn:
            await conn.execute(text("ALTER TABLE exam_attempts ADD COLUMN IF NOT EXISTS answers_summary JSONB;"))
            await conn.execute(text("ALTER TABLE exam_attempts ADD COLUMN IF NOT EXISTS question_sequence JSONB;"))
        logger.info("Database schema migration: verified exam_attempts JSONB columns exist.")
    except Exception as e:
        logger.error(f"Error during database schema migration: {e}", exc_info=True)

    # Launch background deadline sweeper
    sweeper_task = asyncio.create_task(start_periodic_sweeper(interval_seconds=30))

    # Initialize Telegram bot runtime if in webhook mode
    if settings.BOT_MODE == "webhook":
        logger.info("Initializing Telegram bot application in webhook mode...")
        await init_telegram_webhook_app()

    yield

    logger.info("Shutting down Telegram Competitive Exam Platform backend...")
    sweeper_task.cancel()
    try:
        await sweeper_task
    except asyncio.CancelledError:
        pass

    if settings.BOT_MODE == "webhook":
        logger.info("Shutting down Telegram bot application...")
        await shutdown_telegram_webhook_app()

    # Gracefully dispose database connection pool
    try:
        from app.core.database import engine
        await engine.dispose()
        logger.info("Database connection pool disposed cleanly.")
    except Exception as e:
        logger.warning(f"Error disposing database engine: {e}")


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title=settings.PROJECT_NAME,
        debug=settings.DEBUG,
        lifespan=lifespan,
        docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
        redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Root index and lightweight health probe for cloud deployment (Render)
    @app.get("/", tags=["Health"])
    async def root_index():
        """Root endpoint returning service status."""
        return {
            "status": "online",
            "project": "Ethiopian Muslim Youth Council (EMYC) Exam Platform",
            "health": "/health",
        }

    @app.get("/health", tags=["Health"])
    async def root_health_check():
        """Lightweight root liveness probe."""
        return {
            "status": "healthy",
            "project": settings.PROJECT_NAME,
            "environment": settings.ENVIRONMENT,
        }

    app.include_router(api_v1_router)

    # Static Telegram Mini App Hosting
    import os
    if os.path.exists("webapp"):
        from fastapi.staticfiles import StaticFiles
        app.mount("/webapp", StaticFiles(directory="webapp", html=True), name="webapp")

    # Resilient Webhook Aliases (Ensures Telegram updates are accepted regardless of exact path)
    from app.api.v1.telegram_webhook import telegram_webhook
    app.add_api_route("/webhook", telegram_webhook, methods=["POST"], include_in_schema=False)
    app.add_api_route("/api/v1/webhook", telegram_webhook, methods=["POST"], include_in_schema=False)

    return app


app = create_app()
