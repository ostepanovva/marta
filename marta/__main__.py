"""Запуск Марты: python -m marta"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand, MenuButtonCommands, MenuButtonWebApp, WebAppInfo

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
    await marta.on_times_changed()  # время сводок могло быть изменено в приложении
    runner = None
    try:
        # старая версия бота могла оставить вебхук — без его удаления опрос не заработает
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.set_my_commands([
            BotCommand(command="start", description="Начать"),
            BotCommand(command="help", description="Что умеет Марта"),
            BotCommand(command="confirm", description="Вкл/выкл подтверждение записей"),
        ])
        runner = await start_webapp(marta, bot)
        logging.info("Марта запущена")
        prefetch = asyncio.create_task(marta.transcriber.prefetch())  # noqa: F841 — держим ссылку на задачу
        await dp.start_polling(bot)
    finally:
        if runner:
            await runner.cleanup()
        scheduler.shutdown(wait=False)
        marta.transcriber.close()
        await ai.close()
        await db.close()
        await bot.session.close()


async def start_webapp(marta: Marta, bot: Bot):
    """Запускает мини-приложение и ставит кнопку «Марта». Ошибка здесь не мешает работе бота."""
    owner = marta.settings.owner_id
    if not (marta.settings.webapp_enabled and marta.settings.webapp_url):
        try:
            await bot.set_chat_menu_button(chat_id=owner, menu_button=MenuButtonCommands())
        except Exception:
            logging.exception("Не удалось вернуть обычную кнопку меню")
        return None
    from . import webapp

    try:
        runner = await webapp.start(marta)
    except Exception:
        logging.exception("Мини-приложение не запустилось, бот работает без него")
        return None
    try:
        await bot.set_chat_menu_button(
            chat_id=owner,
            menu_button=MenuButtonWebApp(text="Марта", web_app=WebAppInfo(url=marta.settings.webapp_url)),
        )
    except Exception:
        logging.exception("Не удалось поставить кнопку мини-приложения")
    return runner


if __name__ == "__main__":
    asyncio.run(main())
