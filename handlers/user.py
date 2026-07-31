import logging

from aiogram import Bot, F, Router
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from config import settings
from database.db import db
from keyboards.reply import main_menu_keyboard

logger = logging.getLogger(__name__)

router = Router(name="user")

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


def ticket_title(ticket_id: int, user_id: int, emoji: str) -> str:
    return f"{emoji} #{ticket_id} | ID: {user_id}"


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


@router.message(F.chat.type == "private")
async def on_user_message(message: Message, state: FSMContext, bot: Bot) -> None:
    if message.content_type not in MEDIA_CONTENT_TYPES:
        return
    if message.content_type == ContentType.TEXT and message.text.startswith("/"):
        return

    ticket = await db.get_open_ticket(message.from_user.id)
    if ticket is not None:
        await _forward_to_thread(message, ticket["thread_id"])
    else:
        await _create_and_forward(message, bot)
    await state.clear()


async def _create_and_forward(message: Message, bot: Bot) -> None:
    user_id = message.from_user.id
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
        await message.answer("⚠️ Не удалось создать обращение. Попробуйте чуть позже.")
        return

    await db.set_thread(ticket_id, topic.message_thread_id)
    logger.info("New ticket #%s from user %s (thread %s)", ticket_id, user_id, topic.message_thread_id)

    try:
        await message.copy_to(
            chat_id=settings.admin_group_id,
            message_thread_id=topic.message_thread_id,
        )
    except TelegramAPIError as exc:
        logger.error("Failed to copy first message of ticket #%s: %s", ticket_id, exc)

    await message.answer(
        f"✅ Ваше обращение <b>#{ticket_id}</b> принято.\nОжидайте ответа."
    )


async def _forward_to_thread(message: Message, thread_id: int) -> None:
    try:
        await message.copy_to(
            chat_id=settings.admin_group_id,
            message_thread_id=thread_id,
        )
    except TelegramAPIError as exc:
        logger.error(
            "Failed to forward message from user %s: %s",
            message.from_user.id,
            exc,
        )
