import logging
import re
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.types import Message

from config import settings
from database.db import db
from handlers.user import ticket_title
from keyboards.reply import main_menu_keyboard

logger = logging.getLogger(__name__)

router = Router(name="admin")

GROUP_FILTER = (F.chat.type.in_({"group", "supergroup"})) & (F.chat.id == settings.admin_group_id)
TOPIC_FILTER = GROUP_FILTER & F.is_topic_message

DURATION_RE = re.compile(r"(\d+)\s*([mhd])?")

HELP_TEXT = (
    "👨‍💻 <b>Команды поддержки</b>\n\n"
    "<b>В теме тикета:</b>\n"
    "• /close — закрыть тикет\n"
    "• /reopen — переоткрыть тикет\n"
    "• /info — информация о тикете (номер, user_id, username, статус)\n"
    "• /mute <время> [причина] — замутить пользователя тикета\n"
    "    время: 30m, 5h, 2d (минуты, часы, дни)\n"
    "• /unmute — снять мут с пользователя тикета\n\n"
    "<b>В любом месте группы:</b>\n"
    "• /stats — статистика по тикетам\n"
    "• /help — эта справка"
)


def _parse_duration(token: str) -> int | None:
    match = DURATION_RE.fullmatch(token.strip().lower())
    if not match:
        return None
    value = int(match.group(1))
    unit = match.group(2) or "m"
    multiplier = {"m": 60, "h": 3600, "d": 86400}[unit]
    return value * multiplier


def _parse_mute_args(text: str | None) -> tuple[int, str] | None:
    if not text:
        return None
    parts = text.strip().split(maxsplit=2)
    if len(parts) < 2:
        return None
    seconds = _parse_duration(parts[1])
    if seconds is None:
        return None
    reason = parts[2] if len(parts) > 2 else ""
    return seconds, reason


def _human_duration(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days} дн." if not hours else f"{days} дн. {hours} ч."
    if hours:
        return f"{hours} ч." if not minutes else f"{hours} ч. {minutes} мин."
    return f"{minutes} мин."


async def _is_admin(bot: Bot, message: Message) -> bool:
    try:
        member = await bot.get_chat_member(settings.admin_group_id, message.from_user.id)
    except TelegramAPIError as exc:
        logger.debug("Failed to check admin status of %s: %s", message.from_user.id, exc)
        return False
    return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.CREATOR)


@router.message(GROUP_FILTER, Command("close"))
async def on_admin_close_command(message: Message, bot: Bot) -> None:
    if not message.is_topic_message:
        return
    if not await _is_admin(bot, message):
        return
    await _close_ticket(bot, message)


@router.message(GROUP_FILTER, F.is_topic_message, Command("reopen"))
async def on_admin_reopen_command(message: Message, bot: Bot) -> None:
    if not await _is_admin(bot, message):
        return
    await _reopen_ticket(bot, message)


@router.message(GROUP_FILTER, F.is_topic_message, Command("info"))
async def on_admin_info_command(message: Message, bot: Bot) -> None:
    if not await _is_admin(bot, message):
        return
    await _info_ticket(bot, message)


@router.message(GROUP_FILTER, F.is_topic_message, Command("mute"))
async def on_admin_mute_command(message: Message, bot: Bot) -> None:
    if not await _is_admin(bot, message):
        return
    await _mute_user(bot, message)


@router.message(GROUP_FILTER, F.is_topic_message, Command("unmute"))
async def on_admin_unmute_command(message: Message, bot: Bot) -> None:
    if not await _is_admin(bot, message):
        return
    await _unmute_user(bot, message)


@router.message(GROUP_FILTER, Command("stats"))
async def on_admin_stats_command(message: Message, bot: Bot) -> None:
    if not await _is_admin(bot, message):
        return
    await _show_stats(message)


@router.message(GROUP_FILTER, Command("help"))
async def on_admin_help_command(message: Message, bot: Bot) -> None:
    if not await _is_admin(bot, message):
        return
    await message.reply(HELP_TEXT)


@router.message(TOPIC_FILTER)
async def on_admin_message(message: Message, bot: Bot) -> None:
    if message.from_user is None or message.from_user.id == bot.id:
        return
    if message.text and message.text.startswith("/"):
        return
    if not await _is_admin(bot, message):
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


async def _reopen_ticket(bot: Bot, message: Message) -> None:
    ticket = await db.get_ticket_by_thread(message.message_thread_id)
    if ticket is None:
        return
    if ticket["status"] != "closed":
        try:
            await message.reply("ℹ️ Тикет уже открыт.")
        except TelegramAPIError:
            pass
        return

    await db.reopen_ticket(ticket["id"])
    new_title = ticket_title(ticket["id"], ticket["user_id"], "🟢")
    try:
        await bot.edit_forum_topic(
            chat_id=settings.admin_group_id,
            message_thread_id=ticket["thread_id"],
            name=new_title,
        )
    except TelegramAPIError as exc:
        logger.warning("Failed to edit topic title for ticket #%s: %s", ticket["id"], exc)

    logger.info("Ticket #%s reopened", ticket["id"])
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


async def _info_ticket(bot: Bot, message: Message) -> None:
    ticket = await db.get_ticket_by_thread(message.message_thread_id)
    if ticket is None:
        return

    username = "—"
    try:
        chat = await bot.get_chat(ticket["user_id"])
        username = f"@{chat.username}" if chat.username else (chat.first_name or "—")
    except TelegramAPIError as exc:
        logger.debug("Failed to fetch chat for user %s: %s", ticket["user_id"], exc)

    status = "🟢 открыт" if ticket["status"] == "open" else "🔴 закрыт"
    text = (
        f"📋 Тикет <b>#{ticket['id']}</b>\n"
        f"Статус: {status}\n"
        f"User ID: <code>{ticket['user_id']}</code>\n"
        f"Username: {username}\n"
        f"Создан: {ticket['created_at']}\n"
    )
    if ticket["closed_at"]:
        text += f"Закрыт: {ticket['closed_at']}\n"
    await message.reply(text)


async def _mute_user(bot: Bot, message: Message) -> None:
    ticket = await db.get_ticket_by_thread(message.message_thread_id)
    if ticket is None:
        try:
            await message.reply("⚠️ Команда /mute работает только внутри темы тикета.")
        except TelegramAPIError:
            pass
        return

    parsed = _parse_mute_args(message.text)
    if parsed is None:
        try:
            await message.reply(
                "❌ Неверный формат.\n"
                "Использование: /mute <время> [причина]\n"
                "Примеры: /mute 30m Спам, /mute 2d, /mute 5h"
            )
        except TelegramAPIError:
            pass
        return

    seconds, reason = parsed
    until = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    await db.mute_user(
        user_id=ticket["user_id"],
        admin_id=message.from_user.id,
        until_at=until.isoformat(timespec="seconds"),
        reason=reason,
    )

    duration = _human_duration(seconds)
    text = f"🔇 Пользователь <code>{ticket['user_id']}</code> замучен на {duration}."
    if reason:
        text += f"\nПричина: {reason}"
    await message.reply(text)

    notify = f"🔇 Вы замучены на {duration}."
    if reason:
        notify += f"\nПричина: {reason}"
    try:
        await bot.send_message(chat_id=ticket["user_id"], text=notify)
    except TelegramForbiddenError:
        logger.warning("Cannot notify user %s about mute", ticket["user_id"])
    except TelegramAPIError as exc:
        logger.warning("Failed to notify user %s about mute: %s", ticket["user_id"], exc)


async def _unmute_user(bot: Bot, message: Message) -> None:
    ticket = await db.get_ticket_by_thread(message.message_thread_id)
    if ticket is None:
        try:
            await message.reply("⚠️ Команда /unmute работает только внутри темы тикета.")
        except TelegramAPIError:
            pass
        return

    await db.unmute_user(ticket["user_id"])
    text = f"✅ Мут снят с пользователя <code>{ticket['user_id']}</code>."
    await message.reply(text)

    try:
        await bot.send_message(
            chat_id=ticket["user_id"],
            text="✅ С вас снят мут. Вы снова можете писать и создавать обращения.",
        )
    except TelegramForbiddenError:
        logger.warning("Cannot notify user %s about unmute", ticket["user_id"])
    except TelegramAPIError as exc:
        logger.warning("Failed to notify user %s about unmute: %s", ticket["user_id"], exc)


async def _show_stats(message: Message) -> None:
    stats = await db.get_stats()
    await message.reply(
        "📊 <b>Статистика</b>\n"
        f"Всего тикетов: {stats['total']}\n"
        f"Открыто: {stats['open']}\n"
        f"Закрыто: {stats['closed']}\n"
        f"Создано сегодня: {stats['today']}\n"
        f"Уникальных пользователей: {stats['users']}"
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
                "Если появятся новые вопросы — нажмите кнопку «Создать обращение»."
            ),
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
