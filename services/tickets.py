import logging

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import settings
from database.db import db
from keyboards.reply import main_menu_keyboard

logger = logging.getLogger(__name__)

ADMIN_CLOSE_CALLBACK = "admin_close_ticket"
USER_CLOSE_CALLBACK = "close_ticket"


def ticket_title(ticket_id: int, user_id: int, emoji: str) -> str:
    return f"{emoji} #{ticket_id} | ID: {user_id}"


async def get_username(bot: Bot, user_id: int) -> str:
    try:
        chat = await bot.get_chat(user_id)
        if chat.username:
            return f"@{chat.username}"
        return chat.first_name or "—"
    except TelegramAPIError as exc:
        logger.debug("Failed to fetch chat for user %s: %s", user_id, exc)
        return "—"


def format_ticket_info(ticket: dict, username: str) -> str:
    status = "🟢 открыт" if ticket["status"] == "open" else "🔴 закрыт"
    lines = [
        f"📋 Тикет <b>#{ticket['id']}</b>",
        f"Статус: {status}",
        f"User ID: <code>{ticket['user_id']}</code>",
        f"Username: {username}",
        f"Создан: {ticket['created_at']}",
    ]
    if ticket.get("closed_at"):
        lines.append(f"Закрыт: {ticket['closed_at']}")
    return "\n".join(lines)


async def send_topic_info(bot: Bot, thread_id: int, ticket: dict) -> None:
    """Первое сообщение в теме: карточка пользователя + кнопка закрытия для админа."""
    username = await get_username(bot, ticket["user_id"])
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔒 Закрыть обращение",
                    callback_data=f"{ADMIN_CLOSE_CALLBACK}:{ticket['id']}",
                )
            ]
        ]
    )
    try:
        await bot.send_message(
            chat_id=settings.admin_group_id,
            message_thread_id=thread_id,
            text=format_ticket_info(ticket, username),
            reply_markup=keyboard,
        )
    except TelegramAPIError as exc:
        logger.error("Failed to send topic info for ticket #%s: %s", ticket["id"], exc)


async def edit_topic_title(bot: Bot, ticket: dict, emoji: str) -> None:
    try:
        await bot.edit_forum_topic(
            chat_id=settings.admin_group_id,
            message_thread_id=ticket["thread_id"],
            name=ticket_title(ticket["id"], ticket["user_id"], emoji),
        )
    except TelegramAPIError as exc:
        logger.warning("Failed to edit topic title for ticket #%s: %s", ticket["id"], exc)


async def close_ticket(bot: Bot, ticket: dict, reason: str | None = None) -> bool:
    """Закрывает тикет: статус в БД, красная тема, уведомление пользователю."""
    if ticket["status"] != "open":
        return False

    await db.close_ticket(ticket["id"])
    logger.info("Ticket #%s closed%s", ticket["id"], " (auto)" if reason else "")

    await edit_topic_title(bot, ticket, "🔴")

    if settings.close_topic_on_ticket_close:
        try:
            await bot.close_forum_topic(
                chat_id=settings.admin_group_id,
                message_thread_id=ticket["thread_id"],
            )
        except TelegramAPIError as exc:
            logger.warning("Failed to close forum topic for ticket #%s: %s", ticket["id"], exc)

    text = (
        f"🔒 Ваш тикет <b>#{ticket['id']}</b> закрыт.\n"
        "Если появятся новые вопросы — нажмите кнопку «Создать обращение»."
    )
    if reason:
        text = f"⏰ {reason}\n\n{text}"
    try:
        await bot.send_message(
            chat_id=ticket["user_id"],
            text=text,
            reply_markup=main_menu_keyboard(),
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
    return True


async def reopen_ticket(bot: Bot, ticket: dict) -> bool:
    """Переоткрывает тикет: статус в БД, зелёная тема, уведомление пользователю."""
    if ticket["status"] == "open":
        return False

    await db.reopen_ticket(ticket["id"])
    logger.info("Ticket #%s reopened", ticket["id"])

    await edit_topic_title(bot, ticket, "🟢")

    try:
        await bot.send_message(
            chat_id=ticket["user_id"],
            text=(
                f"🟢 Ваш тикет <b>#{ticket['id']}</b> снова открыт.\n"
                "Вы можете писать — ваши сообщения снова будут приходить к нам."
            ),
        )
    except TelegramForbiddenError:
        logger.warning(
            "User %s blocked the bot; cannot notify about reopening ticket #%s",
            ticket["user_id"],
            ticket["id"],
        )
    except TelegramAPIError as exc:
        logger.warning("Failed to notify user %s about reopening: %s", ticket["user_id"], exc)
    return True
