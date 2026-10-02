import asyncio
from app.core.database import AsyncSessionLocal
from app.core.logging import logger
from app.services.competition_service import CompetitionService


async def sweep_expired_attempts_job() -> int:
    """Executes a single pass sweeping auto-closing competitions and auto-submitting expired attempts."""
    try:
        async with AsyncSessionLocal() as db:
            # 1. Auto-close any competitions that reached their scheduled closing time
            closed_comps = await CompetitionService.check_and_auto_close_competitions(db)
            if closed_comps > 0:
                logger.info(f"Background sweeper auto-closed {closed_comps} competitions.")

            # 2. Sweep individual expired attempts
            count = await CompetitionService.sweep_expired_attempts(db)
            if count > 0:
                logger.info(f"Deadline sweeper auto-submitted {count} expired attempts.")
            return count
    except Exception as e:
        logger.error(f"Error during deadline sweeper pass: {e}", exc_info=True)
        return 0


async def start_periodic_sweeper(interval_seconds: int = 30):
    """Runs periodic background sweeping loop while application is alive."""
    logger.info(f"Starting deadline sweeper background loop with interval {interval_seconds}s...")
    while True:
        try:
            await sweep_expired_attempts_job()
        except asyncio.CancelledError:
            logger.info("Deadline sweeper background loop cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in background sweeper loop: {e}")
        await asyncio.sleep(interval_seconds)
