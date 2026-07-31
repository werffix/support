import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import Message

from config import settings
from database.db import db
from handlers.user import ticket_title

logger = logging.getLogger(__name__)

router = Router(name="admin")

GROUP_FILTER = (F.chat.type.in_({"group", "supergroup"})) & (F.chat.id == settings.admin_group_id)
TOPIC_FILTER = GROUP_FILTER & F.is_topic_message


def _is_close_command(text: str | None) -> bool:
    if not text:
        return False
    return text.lstrip("/").split("@")[0].strip() == "close"


@router.message(GROUP_FILTER, Command("close"))
async def on_admin_close_command(message: Message, bot: Bot) -> None:
    if not message.is_topic_message:
        return
    await _close_ticket(bot, message)


@router.message(TOPIC_FILTER)
async def on_admin_message(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.from_user.id == bot.id:
        return

    if _is_close_command(message.text):
        await _close_ticket(bot, message)
        return

    ticket = await db.get_ticket_by_thread(message.message_thread_id)
    if ticket is None:
        return

    try:
        await message.copy_to(chat_id=ticket["user_id"])
    except TelegramForbiddenError:
        logger.warning(
            "Cannot deliver reply to user %s (bot blocked); ticket #%s",
            ticket["user_id"],
            ticket["id"],
        )
        try:
            await message.reply("⚠️ Не удалось доставить ответ: пользователь заблокировал бота.")
        except TelegramAPIError:
            pass
    except TelegramAPIError as exc:
        logger.error(
            "Failed to copy admin reply for ticket #%s: %s",
            ticket["id"],
            exc,
        )


async def _close_ticket(bot: Bot, message: Message) -> None:
    ticket = await db.get_ticket_by_thread(message.message_thread_id)
    if ticket is None:
        return

    if ticket["status"] != "open":
        try:
            await message.reply("ℹ️ Тикет уже закрыт.")
        except TelegramAPIError:
            pass
        return

    await db.close_ticket(ticket["id"])
    logger.info("Ticket #%s closed by admin", ticket["id"])

    new_title = ticket_title(ticket["id"], ticket["user_id"], "🔴")
    try:
        await bot.edit_forum_topic(
            chat_id=settings.admin_group_id,
            message_thread_id=ticket["thread_id"],
            name=new_title,
        )
    except TelegramAPIError as exc:
        logger.warning("Failed to edit topic title for ticket #%s: %s", ticket["id"], exc)

    if settings.close_topic_on_ticket_close:
        try:
            await bot.close_forum_topic(
                chat_id=settings.admin_group_id,
                message_thread_id=ticket["thread_id"],
            )
        except TelegramAPIError as exc:
            logger.warning("Failed to close forum topic for ticket #%s: %s", ticket["id"], exc)

    try:
        await bot.send_message(
            chat_id=ticket["user_id"],
            text=(
                f"🔒 Ваш тикет <b>#{ticket['id']}</b> закрыт.\n"
                "Если появятся вопросы — напишите нам, и мы создадим новый тикет."
            ),
        )
    except TelegramForbiddenError:
        logger.warning(
            "User %s blocked the bot; cannot notify about closing ticket #%s",
            ticket["user_id"],
            ticket["id"],
        )
    except TelegramAPIError as exc:
        logger.warning(
            "Failed to notify user %s about ticket #%s closing: %s",
            ticket["user_id"],
            ticket["id"],
            exc,
        )
