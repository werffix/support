import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_group_id: int
    db_path: str
    close_topic_on_ticket_close: bool
    log_level: str
    antispam_enabled: bool
    antispam_messages: int
    antispam_window_seconds: int
    antispam_ticket_cooldown_seconds: int
    auto_close_hours: int
    auto_close_check_minutes: int


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: str | None, default: int) -> int:
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        return default


def load_settings() -> Settings:
    bot_token = os.getenv("BOT_TOKEN", "").strip()
    admin_group_id = os.getenv("ADMIN_GROUP_ID", "").strip()

    if not bot_token:
        raise RuntimeError("BOT_TOKEN не задан. Скопируйте .env.example в .env и заполните его.")
    if not admin_group_id:
        raise RuntimeError("ADMIN_GROUP_ID не задан. Укажите ID админ-группы (например, -1001234567890).")

    try:
        group_id = int(admin_group_id)
    except ValueError as exc:
        raise RuntimeError(f"ADMIN_GROUP_ID должен быть числом, получено: {admin_group_id!r}") from exc

    return Settings(
        bot_token=bot_token,
        admin_group_id=group_id,
        db_path=os.getenv("DB_PATH", "tickets.db"),
        close_topic_on_ticket_close=_as_bool(os.getenv("CLOSE_TOPIC_ON_TICKET_CLOSE"), False),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        antispam_enabled=_as_bool(os.getenv("ANTISPAM_ENABLED"), True),
        antispam_messages=_as_int(os.getenv("ANTISPAM_MESSAGES"), 5),
        antispam_window_seconds=_as_int(os.getenv("ANTISPAM_WINDOW_SECONDS"), 30),
        antispam_ticket_cooldown_seconds=_as_int(os.getenv("ANTISPAM_TICKET_COOLDOWN_SECONDS"), 60),
        auto_close_hours=_as_int(os.getenv("AUTO_CLOSE_HOURS"), 24),
        auto_close_check_minutes=_as_int(os.getenv("AUTO_CLOSE_CHECK_MINUTES"), 5),
    )


settings = load_settings()
