#!/usr/bin/env bash
# Установка и запуск Марты. Запуск: bash /opt/marta/install.sh
# Можно запускать повторно: уже заполненные настройки не спрашиваются.
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "=== Установка Марты ==="
echo

if ! command -v docker >/dev/null || ! docker compose version >/dev/null 2>&1; then
    echo "На сервере нет Docker или docker compose. Напишите об этом Claude."
    exit 1
fi

[ -f .env ] || cp .env.example .env
chmod 600 .env

# Записывает значение в .env, не показывая его на экране.
ask() {
    local name="$1" prompt="$2" pattern="$3" current value=""
    current=$(grep -E "^${name}=" .env | cut -d= -f2- || true)
    if [ -n "$current" ]; then
        echo "✓ ${prompt}: уже заполнено"
        return
    fi
    while true; do
        read -r -s -p "${prompt}. Вставьте и нажмите Enter (символы не видны — так и должно быть): " value < /dev/tty
        echo
        value=$(printf '%s' "$value" | tr -d '[:space:]')
        if [[ "$value" =~ $pattern ]]; then
            break
        fi
        echo "Не похоже на правильное значение, попробуйте еще раз."
    done
    grep -v -E "^${name}=" .env > .env.tmp || true
    printf '%s=%s\n' "$name" "$value" >> .env.tmp
    mv .env.tmp .env
    chmod 600 .env
    echo "✓ Сохранено"
}

ask TELEGRAM_BOT_TOKEN "Токен бота из BotFather" '^[0-9]+:[A-Za-z0-9_-]{30,}$'
ask GIGACHAT_AUTH_KEY "Ключ авторизации GigaChat" '^[A-Za-z0-9+/=_-]{20,}$'

echo
echo "Собираю и запускаю Марту. Первый раз это займет 3–7 минут, не закрывайте окно..."
docker compose up -d --build

echo
echo "Проверяю, что Марта запустилась..."
for _ in $(seq 1 30); do
    if docker compose logs bot 2>/dev/null | grep -q "Марта запущена"; then
        echo
        echo "🎉 Готово! Марта работает. Откройте ее в Telegram и нажмите /start"
        exit 0
    fi
    sleep 2
done

echo
echo "Марта не сообщила о запуске. Последние строки журнала — пришлите их Claude:"
echo "----------------------------------------"
docker compose logs --tail 40 bot
exit 1
