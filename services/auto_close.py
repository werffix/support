import asyncio
import logging

from aiogram import Bot

from config import settings
from database.db import db
from services.tickets import close_ticket

logger = logging.getLogger(__name__)

_task: asyncio.Task | None = None


async def close_stale_tickets(bot: Bot) -> int:
    """Закрывает открытые тикеты без ответа пользователя дольше AUTO_CLOSE_HOURS часов."""
    if settings.auto_close_hours <= 0:
        return 0
    stale = await db.get_stale_tickets(settings.auto_close_hours)
    closed = 0
    for ticket in stale:
        done = await close_ticket(
            bot,
            ticket,
            reason=(
                f"Тикет закрыт автоматически: вы не отвечали "
                f"более {settings.auto_close_hours} ч."
            ),
        )
        if done:
            closed += 1
    return closed


async def _loop(bot: Bot) -> None:
    interval = max(60, settings.auto_close_check_minutes * 60)
    while True:
        await asyncio.sleep(interval)
        try:
            closed = await close_stale_tickets(bot)
            if closed:
                logger.info("Auto-closed %d stale ticket(s)", closed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Auto-close iteration failed")


def start_auto_close(bot: Bot) -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop(bot))
        logger.info(
            "Auto-close enabled: %dh inactivity, check every %d min",
            settings.auto_close_hours,
            settings.auto_close_check_minutes,
        )


async def stop_auto_close() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            pass
        _task = None
