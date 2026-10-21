#!/usr/bin/env bash
# Установка мини-приложения Марты. Запуск из папки /opt/marta:
#   sudo bash deploy/install-webapp.sh
# Можно запускать повторно. На любой ошибке скрипт останавливается и говорит, что случилось.
set -eu  # без pipefail: grep -q в конвейере иначе дает ложную ошибку
cd "$(dirname "$0")/.."

DOMAIN="marta.ostepanovva.ru"
PORT=18100
SITE="/etc/nginx/sites-available/$DOMAIN"
LINK="/etc/nginx/sites-enabled/$DOMAIN"
STAMP=$(date +%Y%m%d-%H%M%S)

step() { echo; echo "=== [$1/8] $2"; }
fail() { echo; echo "❌ $1"; echo "Ничего страшного: бот работает как раньше. Пришлите этот текст Claude."; exit 1; }

echo "Установка мини-приложения Марты, $(date '+%d.%m.%Y %H:%M')"

step 1 "Проверяю, что все на месте"
[ "$(id -u)" = 0 ] || fail "Запустите команду через sudo."
[ -f .env ] || fail "Не нашла файл .env в $(pwd)."
command -v docker >/dev/null || fail "Нет Docker."
command -v nginx >/dev/null || fail "Нет Nginx."
command -v certbot >/dev/null || fail "Нет certbot."
echo "✓ Папка $(pwd), ветка $(git rev-parse --abbrev-ref HEAD), версия $(git rev-parse --short HEAD)"

step 2 "Проверяю адрес $DOMAIN и порт $PORT"
IP=$(getent ahostsv4 "$DOMAIN" | awk 'NR==1 {print $1}')
[ -n "$IP" ] || fail "Адрес $DOMAIN пока не найден в DNS. Подождите час после добавления записи в REG.RU."
hostname -I | tr ' ' '\n' | grep -qx "$IP" || fail "$DOMAIN указывает на $IP, а это не этот сервер."
echo "✓ $DOMAIN → $IP, это этот сервер"
if ss -ltn | grep -q "127.0.0.1:$PORT " && [ -z "$(docker compose port bot 8080 2>/dev/null || true)" ]; then
    fail "Порт $PORT занят другой программой."
fi
echo "✓ Порт $PORT свободен или уже у Марты"

step 3 "Делаю копию базы"
mkdir -p backups
if [ -f data/marta.db ]; then
    if docker compose ps --status running --services 2>/dev/null | grep -qx bot; then
        docker compose exec -T bot python -c \
            "import sqlite3; sqlite3.connect('/data/marta.db').backup(sqlite3.connect('/data/backup-tmp.db'))"
        mv data/backup-tmp.db "backups/marta-$STAMP.db"
    else
        cp data/marta.db "backups/marta-$STAMP.db"
    fi
    echo "✓ Копия: $(pwd)/backups/marta-$STAMP.db"
else
    echo "✓ Базы еще нет, копировать нечего"
fi

step 4 "Включаю мини-приложение в .env"
set_env() {
    grep -v -E "^$1=" .env > .env.tmp || true
    printf '%s=%s\n' "$1" "$2" >> .env.tmp
    mv .env.tmp .env
    chmod 600 .env
}
set_env WEBAPP_ENABLED 1
set_env WEBAPP_URL "https://$DOMAIN"
echo "✓ WEBAPP_ENABLED=1, WEBAPP_URL=https://$DOMAIN"

step 5 "Сертификат для $DOMAIN"
if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    echo "✓ Сертификат уже есть"
else
    certbot certonly --nginx -d "$DOMAIN" --non-interactive --agree-tos \
        --deploy-hook "systemctl reload nginx" || fail "certbot не смог получить сертификат."
    echo "✓ Сертификат получен, продлеваться будет сам"
fi

step 6 "Файл Nginx для Марты"
if [ -f "$SITE" ]; then
    cp "$SITE" "backups/nginx-$DOMAIN-$STAMP.conf"
fi
cp deploy/nginx-marta.conf "$SITE"
ln -sf "$SITE" "$LINK"
if ! nginx -t; then
    rm -f "$LINK"
    if [ -f "backups/nginx-$DOMAIN-$STAMP.conf" ]; then
        cp "backups/nginx-$DOMAIN-$STAMP.conf" "$SITE"
        ln -sf "$SITE" "$LINK"
    else
        rm -f "$SITE"
    fi
    fail "Проверка nginx -t не прошла. Файл Марты убрала, Nginx не перезагружала."
fi
systemctl reload nginx
echo "✓ nginx -t прошла, Nginx перезагружен без остановки других сайтов"

step 7 "Собираю и перезапускаю Марту (1–3 минуты, бот ненадолго замолчит)"
SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose up -d --build
for _ in $(seq 1 60); do
    if docker compose logs --since "$SINCE" bot 2>/dev/null | grep -q "Марта запущена"; then
        break
    fi
    sleep 2
done
LOGS=$(docker compose logs --since "$SINCE" bot 2>/dev/null || true)
echo "$LOGS" | grep -q "Марта запущена" || { echo "$LOGS" | tail -40; fail "Марта не сообщила о запуске."; }
echo "$LOGS" | grep -q "Мини-приложение запущено" || { echo "$LOGS" | tail -40; fail "Бот запустился, а мини-приложение нет."; }
echo "✓ Бот и мини-приложение запущены"

step 8 "Проверяю адреса"
code() { curl -s -o /dev/null -w "%{http_code}" --max-time 15 "$1" || true; }
[ "$(code "https://$DOMAIN/")" = 200 ] || fail "https://$DOMAIN/ не открывается."
[ "$(code "https://$DOMAIN/api/today")" = 403 ] || fail "Данные отдаются без подписи Telegram — так быть не должно."
echo "✓ https://$DOMAIN открывается, без подписи Telegram данные не выдает"
for site in ostepanovva.ru checking.ostepanovva.ru currency.ostepanovva.ru reports.ostepanovva.ru; do
    echo "  $site: $(code "https://$site/")"
done

echo
echo "🎉 Готово! Откройте чат с Мартой в Telegram: слева от поля ввода появилась кнопка «Марта»."
echo "Если кнопки не видно — закройте чат и откройте снова или отправьте /start."
