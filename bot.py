import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from config import settings
from database.db import db
from handlers.admin import router as admin_router
from handlers.user import router as user_router
from services.auto_close import start_auto_close, stop_auto_close

logger = logging.getLogger("bot")


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        handlers=[logging.StreamHandler()],
    )


async def on_startup(bot: Bot) -> None:
    await db.init(settings.db_path)
    start_auto_close(bot)


async def on_shutdown() -> None:
    await stop_auto_close()
    await db.close()


async def main() -> None:
    setup_logging()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)
    dp.include_routers(user_router, admin_router)

    logger.info("Starting support tickets bot...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
