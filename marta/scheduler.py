"""Расписание: напоминания каждую минуту, сводки утром и вечером, ночная уборка."""

import logging

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import keyboards as kb
from .service import Marta
from .views import evening_text, morning_text, reminder_text

log = logging.getLogger(__name__)


def build_scheduler(marta: Marta, bot: Bot) -> AsyncIOScheduler:
    owner = marta.settings.owner_id

    async def reminders() -> None:
        try:
            for item, _offset in await marta.due_reminders():
                markup = kb.done_keyboard(item["id"]) if item["kind"] in ("task", "payment") else None
                await bot.send_message(owner, reminder_text(item, marta.now()), reply_markup=markup)
            await marta.transcriber.unload_if_idle()
        except Exception:
            log.exception("Ошибка при отправке напоминаний")

    async def morning() -> None:
        try:
            tasks, payments, birthdays = await marta.morning()
            await bot.send_message(owner, morning_text(tasks, payments, birthdays, marta.now().date()))
        except Exception:
            log.exception("Ошибка утренней сводки")

    async def evening() -> None:
        try:
            done, not_done, tomorrow = await marta.evening()
            await bot.send_message(owner, evening_text(done, not_done, tomorrow, marta.now().date()))
        except Exception:
            log.exception("Ошибка вечернего итога")

    async def nightly() -> None:
        try:
            await marta.nightly()
        except Exception:
            log.exception("Ошибка ночной уборки")

    s = marta.settings
    scheduler = AsyncIOScheduler(timezone=s.tz)
    scheduler.add_job(reminders, "cron", second=5, max_instances=1, coalesce=True)
    scheduler.add_job(morning, "cron", hour=s.morning_at.hour, minute=s.morning_at.minute)
    scheduler.add_job(evening, "cron", hour=s.evening_at.hour, minute=s.evening_at.minute)
    scheduler.add_job(nightly, "cron", hour=0, minute=5)
    return scheduler
