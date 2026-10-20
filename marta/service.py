"""Логика Марты, общая для бота и расписания."""

from datetime import datetime, timedelta

from .ai import GigaChat, context_window
from .config import Settings
from .db import Database
from .speech import Transcriber
from .timeutil import from_str, next_occurrence, roll_forward, to_str

REMINDER_GRACE = timedelta(hours=6)  # если сервер перезапускался, догоняем пропущенное за 6 часов


def start_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


class Marta:
    def __init__(self, settings: Settings, db: Database, ai: GigaChat, transcriber: Transcriber):
        self.settings = settings
        self.db = db
        self.ai = ai
        self.transcriber = transcriber

    def now(self) -> datetime:
        """Текущее московское время без часового пояса, с точностью до минуты."""
        return datetime.now(self.settings.tz).replace(tzinfo=None, second=0, microsecond=0)

    async def confirm_enabled(self) -> bool:
        return await self.db.get_setting("confirm", "1") == "1"

    async def parse(self, text: str) -> dict:
        return await self.ai.parse(text, self.now(), await self.db.examples())

    async def answer_question(self, question: str) -> str:
        now = self.now()
        records = await self.db.context_items(*context_window(now))
        return await self.ai.answer(question, now, records)

    async def mark_done(self, item: dict) -> None:
        now = self.now()
        await self.db.add_completion(item, now)
        if item["recurrence"] != "none" and item.get("due_at"):
            due = next_occurrence(from_str(item["due_at"]), item["recurrence"])
            due = roll_forward(due, item["recurrence"], start_of_day(now))
            await self.db.update_item(item["id"], due_at=to_str(due))
        else:
            await self.db.update_item(item["id"], status="done", done_at=to_str(now))

    async def due_reminders(self) -> list[tuple[dict, int]]:
        """Напоминания, которые пора отправить. Каждое возвращается один раз."""
        now = self.now()
        result = []
        for item in await self.db.active_with_due():
            due = from_str(item["due_at"])
            created = from_str(item["created_at"])
            for offset in item["offsets"]:
                fire = due - timedelta(minutes=offset)
                if fire > now or fire < created or now - fire > REMINDER_GRACE:
                    continue
                if await self.db.mark_sent(item["id"], item["due_at"], offset, now):
                    result.append((item, offset))
        return result

    async def today(self) -> list[dict]:
        day = start_of_day(self.now())
        return await self.db.due_between(day, day + timedelta(days=1), ("task", "payment", "birthday"))

    async def morning(self) -> tuple[list[dict], list[dict], list[dict]]:
        day = start_of_day(self.now())
        tasks = await self.db.due_between(day, day + timedelta(days=1), ("task",))
        payments = await self.db.due_between(day, day + timedelta(days=2), ("payment",))
        birthdays = await self.db.due_between(day, day + timedelta(days=4), ("birthday",))
        return tasks, payments, birthdays

    async def evening(self) -> tuple[list[dict], list[dict], list[dict]]:
        day = start_of_day(self.now())
        tomorrow = day + timedelta(days=1)
        done = await self.db.completions_between(day, tomorrow)
        not_done = await self.db.due_between(day, tomorrow, ("task", "payment"))
        upcoming = await self.db.due_between(tomorrow, tomorrow + timedelta(days=1), ("task", "payment", "birthday"))
        return done, not_done, upcoming

    async def nightly(self) -> None:
        """Сдвигает прошедшие повторяющиеся события на следующий раз и чистит старое."""
        now = self.now()
        today = start_of_day(now)
        for item in await self.db.active_with_due():
            if item["recurrence"] == "none":
                continue
            due = from_str(item["due_at"])
            if due < today:
                await self.db.update_item(item["id"], due_at=to_str(roll_forward(due, item["recurrence"], today)))
        await self.db.cleanup(now, self.settings.keep_days)
