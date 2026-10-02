import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import setup_logging, logger
from app.api.v1 import api_v1_router
from app.tasks.deadline_sweeper import start_periodic_sweeper

settings = get_settings()
setup_logging(settings.DEBUG)


from app.api.v1.telegram_webhook import init_telegram_webhook_app, shutdown_telegram_webhook_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager for startup and shutdown hooks."""
    logger.info("Starting up Telegram Competitive Exam Platform backend...")
    logger.info(f"Environment: {settings.ENVIRONMENT}, Bot Mode: {settings.BOT_MODE}")

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

    app.include_router(api_v1_router)

    return app


app = create_app()
