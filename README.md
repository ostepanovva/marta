# Марта — личный секретарь в Telegram

Марта принимает текст и голосовые, понимает смысл с помощью GigaChat, раскладывает записи по
категориям и напоминает о делах. Утром в 9:00 присылает план на день, вечером в 23:00 — итог.

**Версия 1:** дела, платежи, дни рождения, заметки и идеи, список покупок, напоминания,
вопросы о планах («во сколько у меня завтра врач?»), обучение категориям на исправлениях.

## Как устроено

| Часть | Технология |
| --- | --- |
| Бот | Python 3.12, aiogram 3 |
| Понимание смысла | GigaChat API (модуль `marta/ai.py`, можно заменить) |
| Распознавание голоса | faster-whisper прямо на сервере, аудио никуда не уходит |
| База данных | SQLite, файл `data/marta.db` |
| Напоминания и сводки | APScheduler |
| Запуск | Docker Compose |

Марта отвечает только владельцу (`OWNER_ID`), остальных игнорирует. Токены и ключи хранятся
только в файле `.env` на сервере и не попадают в GitHub.

## Установка на сервер

```bash
cd /opt
git clone https://github.com/ostepanovva/marta.git
cd marta
cp .env.example .env
chmod 600 .env
nano .env          # вписать TELEGRAM_BOT_TOKEN и GIGACHAT_AUTH_KEY, сохранить: Ctrl+O, Enter, Ctrl+X
docker compose up -d --build
docker compose logs -f   # выход из просмотра: Ctrl+C
```

## Полезные команды (из папки `/opt/marta`)

| Что сделать | Команда |
| --- | --- |
| Посмотреть журнал | `docker compose logs --tail 100` |
| Перезапустить | `docker compose restart` |
| Обновить до новой версии | `git pull && docker compose up -d --build` |
| Остановить | `docker compose down` |

## Проверка кода

```bash
pip install -r requirements.txt pytest
python -m pytest
```
