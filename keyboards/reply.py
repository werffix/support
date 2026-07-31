from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

TICKET_BUTTON_CALLBACK = "create_ticket"


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Кнопка «Создать обращение» в личном чате с ботом."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📨 Создать обращение",
                    callback_data=TICKET_BUTTON_CALLBACK,
                )
            ]
        ]
    )
