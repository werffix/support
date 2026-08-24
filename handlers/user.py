import logging
import math
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import settings
from database.db import db
from keyboards.reply import main_menu_keyboard
from services.tickets import USER_CLOSE_CALLBACK, close_ticket, send_topic_info, ticket_title
from utils.throttle import RateLimiter

logger = logging.getLogger(__name__)

router = Router(name="user")

message_limiter = RateLimiter(
    limit=settings.antispam_messages,
    window=settings.antispam_window_seconds,
)
ticket_limiter = RateLimiter(
    limit=1,
    window=settings.antispam_ticket_cooldown_seconds,
)


def _seconds(seconds: float) -> int:
    return max(1, math.ceil(seconds))

MEDIA_CONTENT_TYPES = {
    ContentType.TEXT,
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.DOCUMENT,
    ContentType.AUDIO,
    ContentType.VOICE,
    ContentType.VIDEO_NOTE,
    ContentType.ANIMATION,
    ContentType.STICKER,
    ContentType.LOCATION,
    ContentType.VENUE,
    ContentType.CONTACT,
}


class TicketStates(StatesGroup):
    waiting_for_message = State()


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        "👋 Здравствуйте! Это служба поддержки.\n\n"
        "Нажмите кнопку ниже, чтобы создать обращение. "
        "Опишите проблему текстом или прикрепите файл — и мы обязательно ответим.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.chat.type == "private", Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("🚫 Действие отменено.")


@router.callback_query(F.data == "create_ticket")
async def on_create_ticket(callback: CallbackQuery, state: FSMContext) -> None:
    user = callback.from_user
    mute = await db.is_muted(user.id)
    if mute is not None:
        await callback.answer("Вы замучены и не можете создавать обращения", show_alert=True)
        return

    ticket = await db.get_open_ticket(user.id)
    if ticket is not None:
        await callback.answer("У вас уже есть открытое обращение", show_alert=True)
        await callback.message.answer(
            f"У вас уже есть открытое обращение <b>#{ticket['id']}</b>.\n"
            "Просто напишите следующее сообщение сюда — оно сразу попадёт к нам."
        )
        return

    await state.set_state(TicketStates.waiting_for_message)
    await callback.answer()
    await callback.message.answer(
        "✍️ Опишите вашу проблему. Можно отправить текст, фото, видео или документ."
    )


@router.callback_query(F.data.startswith(f"{USER_CLOSE_CALLBACK}:"))
async def on_user_close_ticket(callback: CallbackQuery, bot: Bot) -> None:
    try:
        ticket_id = int(callback.data.split(":", 1)[1])
    except (IndexError, ValueError):
        await callback.answer()
        return

    ticket = await db.get_ticket(ticket_id)
    if ticket is None or ticket["user_id"] != callback.from_user.id:
        await callback.answer("Тикет не найден", show_alert=True)
        return
    if ticket["status"] != "open":
        await callback.answer("Тикет уже закрыт")
        return

    await close_ticket(bot, ticket)
    await callback.answer("Тикет закрыт")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError as exc:
        logger.debug("Failed to remove close button for user %s: %s", callback.from_user.id, exc)


@router.message(F.chat.type == "private")
async def on_user_message(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.content_type not in MEDIA_CONTENT_TYPES:
        return
    if message.content_type == ContentType.TEXT and message.text.startswith("/"):
        return

    mute = await db.is_muted(message.from_user.id)
    if mute is not None:
        await _notify_muted(message, mute)
        return

    if settings.antispam_enabled and not message_limiter.allow(message.from_user.id):
        await _warn_antispam(message)
        return

    ticket = await db.get_open_ticket(message.from_user.id)

    if await state.get_state() == TicketStates.waiting_for_message.state:
        if ticket is not None:
            await _forward_to_thread(message, ticket)
        else:
            await _create_and_forward(message, bot)
        await state.clear()
        return

    if ticket is not None:
        await _forward_to_thread(message, ticket)
    else:
        await message.answer(
            "📋 У вас нет открытого обращения.\n\n"
            "Нажмите кнопку ниже, чтобы создать новое обращение.",
            reply_markup=main_menu_keyboard(),
        )


async def _warn_antispam(message: Message) -> None:
    user_id = message.from_user.id
    remaining = _seconds(message_limiter.cooldown(user_id))
    try:
        await message.answer(
            f"🐢 Вы отправляете сообщения слишком быстро.\n"
            f"Подождите {remaining} сек. и повторите попытку."
        )
    except TelegramAPIError as exc:
        logger.debug("Failed to send antispam warning to user %s: %s", user_id, exc)


async def _notify_muted(message: Message, mute: dict) -> None:
    until = datetime.fromisoformat(mute["until_at"])
    text = f"🔇 Вы замучены до {until:%Y-%m-%d %H:%M} (UTC)."
    if mute.get("reason"):
        text += f"\nПричина: {mute['reason']}"
    try:
        await message.answer(text)
    except TelegramAPIError as exc:
        logger.debug("Failed to notify muted user %s: %s", message.from_user.id, exc)


def _user_close_keyboard(ticket_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔒 Закрыть обращение",
                    callback_data=f"{USER_CLOSE_CALLBACK}:{ticket_id}",
                )
            ]
        ]
    )


async def _create_and_forward(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
    if settings.antispam_enabled and not ticket_limiter.allow(user_id):
        remaining = _seconds(ticket_limiter.cooldown(user_id))
        await message.answer(
            f"⏳ Вы недавно создавали обращение.\n"
            f"Подождите {remaining} сек. перед созданием нового."
        )
        return

    ticket_id = await db.create_ticket(user_id)
    title = ticket_title(ticket_id, user_id, "🟢")

    try:
        topic = await bot.create_forum_topic(
            chat_id=settings.admin_group_id,
            name=title,
        )
    except TelegramAPIError as exc:
        logger.error("Failed to create forum topic for user %s: %s", user_id, exc)
        await db.delete_ticket(ticket_id)
        ticket_limiter.forget(user_id)
        await message.answer("⚠️ Не удалось создать обращение. Попробуйте чуть позже.")
        return

    await db.set_thread(ticket_id, topic.message_thread_id)
    ticket = await db.get_ticket(ticket_id)
    logger.info("New ticket #%s from user %s (thread %s)", ticket_id, user_id, topic.message_thread_id)

    await send_topic_info(bot, topic.message_thread_id, ticket)

    try:
        await message.copy_to(
            chat_id=settings.admin_group_id,
            message_thread_id=topic.message_thread_id,
        )
    except TelegramAPIError as exc:
        logger.error("Failed to copy first message of ticket #%s: %s", ticket_id, exc)

    await message.answer(
        f"✅ Ваше обращение <b>#{ticket_id}</b> принято.\nОжидайте ответа.",
        reply_markup=_user_close_keyboard(ticket_id),
    )


async def _forward_to_thread(message: Message, ticket: dict) -> None:
    try:
        await message.copy_to(
            chat_id=settings.admin_group_id,
            message_thread_id=ticket["thread_id"],
        )
        await db.touch_user_activity(ticket["id"])
    except TelegramAPIError as exc:
        logger.error(
            "Failed to forward message from user %s: %s",
            message.from_user.id,
            exc,
        )
