"""Запуск Марты: python -m marta"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand

from .ai import GigaChat
from .config import load_settings
from .db import Database
from .handlers import OwnerOnly, router
from .scheduler import build_scheduler
from .service import Marta
from .speech import Transcriber


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # без этого журнал забит записями о ежеминутной проверке напоминаний
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = load_settings()

    db = Database(settings.db_path)
    await db.connect()
    ai = GigaChat(settings.gigachat_auth_key, settings.gigachat_scope, settings.gigachat_model,
                  settings.gigachat_ca_bundle)
    marta = Marta(settings, db, ai, Transcriber(settings.whisper_model, settings.whisper_cache))

    bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp["marta"] = marta
    dp.update.outer_middleware(OwnerOnly(settings.owner_id))
    dp.include_router(router)

    scheduler = build_scheduler(marta, bot)
    scheduler.start()
    try:
        # старая версия бота могла оставить вебхук — без его удаления опрос не заработает
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать"),
            BotCommand(command="help", description="Что умеет Марта"),
            BotCommand(command="confirm", description="Вкл/выкл подтверждение записей"),
        ])
        logging.info("Марта запущена")
        prefetch = asyncio.create_task(marta.transcriber.prefetch())  # noqa: F841 — держим ссылку на задачу
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await ai.close()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
