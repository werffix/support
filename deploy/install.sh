#!/usr/bin/env bash
# Скрипт установки бота на сервер (Debian/Ubuntu). Запускать от root или через sudo.
#
#   sudo bash deploy/install.sh
set -euo pipefail

APP_USER="supportbot"
APP_DIR="/opt/support_bot"
SERVICE_NAME="support_bot"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Создание системного пользователя $APP_USER"
if ! id -u "$APP_USER" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

echo "==> Копирование проекта в $APP_DIR"
mkdir -p "$APP_DIR"
cp -r \
  "$REPO_DIR/bot.py" \
  "$REPO_DIR/config.py" \
  "$REPO_DIR/requirements.txt" \
  "$REPO_DIR/database" \
  "$REPO_DIR/handlers" \
  "$REPO_DIR/keyboards" \
  "$APP_DIR/"

echo "==> Создание .env"
if [ ! -f "$APP_DIR/.env" ]; then
  cp "$REPO_DIR/.env.example" "$APP_DIR/.env"
  echo "    ВНИМАНИЕ: заполните $APP_DIR/.env (BOT_TOKEN, ADMIN_GROUP_ID) перед запуском!"
else
  echo "    .env уже существует, оставляем."
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

echo "==> Установка зависимостей в venv"
if [ ! -d "$APP_DIR/venv" ]; then
  python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Установка systemd-юнита"
cp "$REPO_DIR/deploy/bot.service" "/etc/systemd/system/$SERVICE_NAME.service"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

echo "==> Запуск"
systemctl restart "$SERVICE_NAME"

echo ""
echo "Готово! Проверка:"
echo "    systemctl status $SERVICE_NAME"
echo "    journalctl -u $SERVICE_NAME -f"
echo ""
echo "Не забудьте заполнить настройки: nano $APP_DIR/.env"
