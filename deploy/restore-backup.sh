#!/usr/bin/env bash
# Восстановление записей Марты из ночной копии, которую она присылает в Telegram.
#   sudo bash deploy/restore-backup.sh /home/ostepa/marta-2026-11-06.db.gz
# Текущая база перед заменой сохраняется в backups/, так что шаг можно отменить.
set -eu
cd "$(dirname "$0")/.."

SRC="${1:-}"
STAMP=$(date +%Y%m%d-%H%M%S)
fail() { echo; echo "❌ $1"; echo "Текущая база не тронута. Пришлите этот текст Claude."; exit 1; }

[ "$(id -u)" = 0 ] || fail "Запустите команду через sudo."
[ -n "$SRC" ] && [ -f "$SRC" ] || fail "Не нашла файл копии: ${SRC:-не указан}."

TMP=$(mktemp)
case "$SRC" in
    *.gz) gunzip -c "$SRC" > "$TMP" || fail "Файл копии поврежден, не распаковывается." ;;
    *) cp "$SRC" "$TMP" ;;
esac
CHECK=$(python3 -c "import sqlite3,sys; c=sqlite3.connect(sys.argv[1]); print(c.execute('PRAGMA integrity_check').fetchone()[0], c.execute('SELECT COUNT(*) FROM items').fetchone()[0])" "$TMP" 2>/dev/null) \
    || fail "Это не база Марты."
set -- $CHECK
[ "$1" = ok ] || fail "База в копии повреждена."
echo "✓ Копия в порядке, записей в ней: $2"

docker compose stop bot
mkdir -p backups
[ -f data/marta.db ] && cp data/marta.db "backups/marta-before-restore-$STAMP.db" \
    && echo "✓ Прежняя база сохранена: $(pwd)/backups/marta-before-restore-$STAMP.db"
mkdir -p data
cp "$TMP" data/marta.db
rm -f "$TMP" data/marta.db-wal data/marta.db-shm

SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker compose up -d
for _ in $(seq 1 60); do
    if docker compose logs --since "$SINCE" bot 2>/dev/null | grep -q "Марта запущена"; then
        echo; echo "🎉 Готово! Записи восстановлены из $(basename "$SRC"). Проверьте Марту в Telegram."
        exit 0
    fi
    sleep 2
done
docker compose logs --since "$SINCE" bot | tail -30
fail "Марта не сообщила о запуске."
