#!/usr/bin/env bash
# Откат Марты к рабочей версии из ветки main. Данные сохраняются.
#   sudo bash deploy/rollback.sh          — вернуть бота из main
#   sudo bash deploy/rollback.sh --nginx  — то же и убрать адрес мини-приложения из Nginx
set -eu  # без pipefail: grep -q в конвейере иначе дает ложную ошибку
ROOT="${MARTA_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$ROOT"

DOMAIN="marta.ostepanovva.ru"
[ "$(id -u)" = 0 ] || { echo "Запустите команду через sudo."; exit 1; }

# в ветке main этого скрипта нет, поэтому после переключения работаем из его копии
if [ -z "${MARTA_ROLLBACK_COPY:-}" ]; then
    COPY=$(mktemp)
    cp "$ROOT/deploy/rollback.sh" "$COPY"
    MARTA_DIR="$ROOT" MARTA_ROLLBACK_COPY=1 exec bash "$COPY" "$@"
fi

echo "=== Откат Марты, $(date '+%d.%m.%Y %H:%M')"
echo "Сейчас: ветка $(git rev-parse --abbrev-ref HEAD), версия $(git rev-parse --short HEAD)"

grep -v -E "^WEBAPP_ENABLED=" .env > .env.tmp || true
echo "WEBAPP_ENABLED=0" >> .env.tmp
mv .env.tmp .env
chmod 600 .env
echo "✓ Мини-приложение выключено в .env"

git fetch origin main
git checkout main
git pull --ff-only origin main
echo "✓ Код из main: версия $(git rev-parse --short HEAD)"

SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose up -d --build --remove-orphans
for _ in $(seq 1 60); do
    if docker compose logs --since "$SINCE" bot 2>/dev/null | grep -q "Марта запущена"; then
        echo "✓ Марта запущена"
        break
    fi
    sleep 2
done

if [ "${1:-}" = "--nginx" ]; then
    rm -f "/etc/nginx/sites-enabled/$DOMAIN" "/etc/nginx/sites-available/$DOMAIN"
    if nginx -t; then
        systemctl reload nginx
        echo "✓ Адрес $DOMAIN убран из Nginx"
    else
        echo "❌ nginx -t не прошла после удаления файла Марты. Nginx не перезагружала — напишите Claude."
    fi
fi

echo
echo "Готово. Проверьте бота в Telegram: /start. Копии базы лежат в $(pwd)/backups/."
