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
            quote = await marta.quote_of_day()
            await bot.send_message(owner, morning_text(tasks, payments, birthdays, marta.now().date(), quote))
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
    scheduler.add_job(morning, "cron", id="morning", hour=s.morning_at.hour, minute=s.morning_at.minute)
    scheduler.add_job(evening, "cron", id="evening", hour=s.evening_at.hour, minute=s.evening_at.minute)
    scheduler.add_job(nightly, "cron", hour=0, minute=5)

    async def apply_times() -> None:
        """Переносит сводки на время из настроек — сразу, без перезапуска."""
        morning_at, evening_at = await marta.summary_times()
        scheduler.reschedule_job("morning", trigger="cron", hour=morning_at.hour, minute=morning_at.minute)
        scheduler.reschedule_job("evening", trigger="cron", hour=evening_at.hour, minute=evening_at.minute)
        log.info("Сводки: утро в %s, вечер в %s", f"{morning_at:%H:%M}", f"{evening_at:%H:%M}")

    marta.on_times_changed = apply_times
    return scheduler
