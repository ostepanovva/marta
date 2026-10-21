"""Логика Марты, общая для бота, расписания и мини-приложения."""

import asyncio
import logging
import random
import re
from datetime import date, datetime, time, timedelta

from .ai import GigaChat, context_window
from .config import CATEGORIES, Settings
from .db import Database
from .speech import Transcriber
from .timeutil import ALL_DAY_TIME, default_offsets, from_str, next_occurrence, roll_forward, to_str
from .views import QUOTES

log = logging.getLogger(__name__)

REMINDER_GRACE = timedelta(hours=6)  # если сервер перезапускался, догоняем пропущенное за 6 часов
QUOTE_KINDS = ("support", "motivation", "quote")
QUOTE_TIMEOUT = 20  # секунд ждем GigaChat утром, потом берем запасную фразу
RECURRENCES = ("none", "daily", "weekly", "monthly", "yearly")
KINDS = ("task", "payment", "birthday", "note")
OFFSET_CHOICES = (3 * 24 * 60, 24 * 60, 60, 0)


class InvalidInput(ValueError):
    """Неверные данные из формы приложения; текст ошибки показывается владельцу."""


def start_of_day(dt: datetime) -> datetime:
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


class Marta:
    def __init__(self, settings: Settings, db: Database, ai: GigaChat, transcriber: Transcriber):
        self.settings = settings
        self.db = db
        self.ai = ai
        self.transcriber = transcriber
        self.on_times_changed = None  # расписание подписывается, чтобы перенести сводки

    def now(self) -> datetime:
        """Текущее московское время без часового пояса, с точностью до минуты."""
        return datetime.now(self.settings.tz).replace(tzinfo=None, second=0, microsecond=0)

    async def confirm_enabled(self) -> bool:
        return await self.db.get_setting("confirm", "1") == "1"

    async def set_confirm(self, enabled: bool) -> None:
        await self.db.set_setting("confirm", "1" if enabled else "0")

    # --- время сводок ---

    async def summary_times(self) -> tuple[time, time]:
        """Время утренней сводки и вечернего итога: из настроек, иначе из .env."""
        morning = parse_clock(await self.db.get_setting("morning_at", "")) or self.settings.morning_at
        evening = parse_clock(await self.db.get_setting("evening_at", "")) or self.settings.evening_at
        return morning, evening

    async def set_summary_time(self, which: str, value: str) -> time:
        clock = parse_clock(value)
        if which not in ("morning_at", "evening_at") or clock is None:
            raise InvalidInput("Время нужно в виде ЧЧ:ММ")
        await self.db.set_setting(which, f"{clock:%H:%M}")
        if self.on_times_changed:
            await self.on_times_changed()
        return clock

    # --- фраза дня ---

    async def quote_of_day(self, generate: bool = True) -> str:
        """Фраза для утренней сводки, одна на весь день. Без generate GigaChat не спрашиваем."""
        today = self.now().date().isoformat()
        if await self.db.get_setting("quote_date", "") == today:
            return await self.db.get_setting("quote_text", "")
        text = ""
        if generate and self.ai is not None:
            kind = QUOTE_KINDS[self.now().toordinal() % len(QUOTE_KINDS)]
            try:
                text = await asyncio.wait_for(self.ai.quote(self.now(), kind), QUOTE_TIMEOUT)
            except Exception:
                log.warning("GigaChat не прислал фразу дня, беру запасную")
            if len(text) > 300:
                text = ""
        if not text:
            text = random.Random(today).choice(QUOTES)
        await self.db.set_setting("quote_date", today)
        await self.db.set_setting("quote_text", text)
        return text

    # --- создание записей ---

    async def save_parsed(self, items: list[dict]) -> tuple[list[dict], list[dict]]:
        """Сохраняет разобранные записи. Покупки — сразу, остальное — черновиком, если включено подтверждение."""
        now = self.now()
        shopping, others = [], []
        for item in items:
            if item["kind"] == "shopping":
                await self.db.add_item({**item, "status": "active"}, now)
                shopping.append(item)
        confirm = await self.confirm_enabled()
        for item in items:
            if item["kind"] != "shopping":
                item_id = await self.db.add_item({**item, "status": "draft" if confirm else "active"}, now)
                others.append(await self.db.get_item(item_id))
        return shopping, others

    async def create_from_form(self, form: dict) -> dict:
        """Запись, заполненная вручную в приложении. Сохраняется сразу."""
        fields = self.form_fields(form, None)
        title = fields.get("title", "")
        if fields.get("kind") == "shopping":
            item_id = await self.db.add_item({"kind": "shopping", "title": title, "category": "Покупки",
                                              "status": "active", "source": title}, self.now())
            return await self.db.get_item(item_id)
        item_id = await self.db.add_item({**fields, "status": "active", "source": title}, self.now())
        return await self.db.get_item(item_id)

    async def update_from_form(self, item: dict, form: dict) -> dict:
        fields = self.form_fields(form, item)
        if fields.get("category") and fields["category"] != item["category"]:
            await self.db.add_example(item["title"], fields["category"], self.now())  # учимся, как в боте
        await self.db.update_item(item["id"], **fields)
        return await self.db.get_item(item["id"])

    def form_fields(self, form: dict, item: dict | None) -> dict:
        """Проверяет поля формы и пересчитывает срок и напоминания по тем же правилам, что у бота."""
        title = str(form.get("title", item["title"] if item else "")).strip()
        if not title:
            raise InvalidInput("Напиши название")
        if len(title) > 200:
            raise InvalidInput("Слишком длинное название")
        kind = form.get("kind", item["kind"] if item else "task")
        if kind == "shopping" and item is None:
            return {"kind": "shopping", "title": title}
        if kind not in KINDS:
            raise InvalidInput("Неизвестный вид записи")
        category = form.get("category", item["category"] if item else "Дом")
        if category not in CATEGORIES:
            raise InvalidInput("Неизвестная категория")
        if kind == "payment":
            category = "Платежи"
        recurrence = form.get("recurrence", item["recurrence"] if item else "none")
        if recurrence not in RECURRENCES:
            raise InvalidInput("Неизвестный повтор")

        day = parse_day(form.get("date", (item or {}).get("due_at", "") or ""))
        clock_text = form.get("time")
        if clock_text is None and item and item.get("due_at") and not item["all_day"]:
            clock_text = item["due_at"][11:]
        clock = parse_clock(clock_text or "")
        if (clock_text or "").strip() and clock is None:
            raise InvalidInput("Время нужно в виде ЧЧ:ММ")
        if kind == "note" and day:
            kind = "task"  # заметка с датой становится делом
        if kind == "birthday":
            recurrence, clock = "yearly", None
        fields = {"kind": kind, "title": title, "category": category}
        if day is None:
            if kind == "birthday":
                raise InvalidInput("Укажи дату")
            fields.update(due_at=None, all_day=False, offsets=[], recurrence="none" if kind == "note" else recurrence)
        else:
            all_day = clock is None
            hours, minutes = ALL_DAY_TIME if all_day else (clock.hour, clock.minute)
            due = datetime(day.year, day.month, day.day, hours, minutes)
            if recurrence != "none":
                due = roll_forward(due, recurrence, start_of_day(self.now()))
            if "offsets" in form:
                offsets = form["offsets"]
                if not isinstance(offsets, list) or any(o not in OFFSET_CHOICES for o in offsets):
                    raise InvalidInput("Неверные напоминания")
                offsets = sorted(set(offsets), reverse=True)
            elif item and item.get("due_at") and item["kind"] == kind and bool(item["all_day"]) == all_day:
                offsets = item["offsets"]
            else:
                offsets = default_offsets(kind, all_day)
            fields.update(due_at=to_str(due), all_day=all_day, offsets=offsets, recurrence=recurrence)
        if kind == "payment":
            fields["amount"] = parse_amount(form.get("amount", (item or {}).get("amount")))
        else:
            fields["amount"] = None
        if "source" in form and kind == "note":
            fields["source"] = str(form["source"])[:4000]
        return fields

    # --- покупки ---

    async def mark_bought(self, item: dict) -> None:
        await self.db.update_item(item["id"], status="done", done_at=to_str(self.now()))

    async def unbuy(self, item: dict) -> None:
        await self.db.update_item(item["id"], status="active", done_at=None)

    async def bought_recently(self) -> list[dict]:
        return await self.db.shopping_bought(self.now() - timedelta(days=1))

    # --- списки для приложения ---

    async def overdue(self) -> list[dict]:
        return await self.db.due_between(datetime(2000, 1, 1), start_of_day(self.now()), ("task", "payment"))

    async def upcoming(self, days: int = 7) -> list[dict]:
        tomorrow = start_of_day(self.now()) + timedelta(days=1)
        return await self.db.due_between(tomorrow, tomorrow + timedelta(days=days), ("task", "payment", "birthday"))

    async def done_today(self) -> list[dict]:
        day = start_of_day(self.now())
        return await self.db.completions_between(day, day + timedelta(days=1))

    async def month(self, year: int, month: int) -> list[tuple[dict, datetime, bool]]:
        """Записи месяца с будущими повторами: (запись, когда, это ли будущий повтор)."""
        start = datetime(year, month, 1)
        end = datetime(year + month // 12, month % 12 + 1, 1)
        result = []
        for item in await self.db.active_with_due():
            if item["kind"] not in ("task", "payment", "birthday"):
                continue
            due = from_str(item["due_at"])
            repeat = False
            for _ in range(400):
                if due >= end:
                    break
                if due >= start:
                    result.append((item, due, repeat))
                if item["recurrence"] == "none":
                    break
                due, repeat = next_occurrence(due, item["recurrence"]), True
        result.sort(key=lambda r: r[1])
        return result

    async def payments_summary(self) -> dict:
        now = self.now()
        day = start_of_day(now)
        month_start = day.replace(day=1)
        next_month = (month_start + timedelta(days=32)).replace(day=1)
        payments = await self.db.payments()
        paid = await self.db.paid_between(month_start, next_month)
        overdue = [p for p in payments if p["due_at"] and from_str(p["due_at"]) < day]
        soon = [p for p in payments if not p["due_at"] or from_str(p["due_at"]) >= day]
        left = sum(p["amount"] or 0 for p in payments if p["due_at"] and from_str(p["due_at"]) < next_month)
        return {
            "overdue": overdue, "soon": soon, "paid": paid,
            "left_sum": left, "paid_sum": sum(p["amount"] or 0 for p in paid),
        }

    async def search(self, query: str) -> list[dict]:
        words = query.lower().replace("ё", "е").split()
        if not words:
            return []
        found = []
        for item in await self.db.active_items():
            haystack = f"{item['title']} {item['category']} {item['tags']} {item['source']}".lower().replace("ё", "е")
            if all(w.lstrip("#") in haystack for w in words):
                found.append(item)
        return found[:50]

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


def parse_clock(value: str) -> time | None:
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", (value or "").strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    return time(hours, minutes) if hours < 24 and minutes < 60 else None


def parse_day(value: str) -> date | None:
    value = (value or "").strip()[:10]
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise InvalidInput("Дата нужна в виде ГГГГ-ММ-ДД") from exc


def parse_amount(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        amount = round(float(str(value).replace(" ", "").replace("\u00a0", "").replace(",", ".")), 2)
    except ValueError as exc:
        raise InvalidInput("Сумма — это число, например 4200") from exc
    if amount < 0 or amount >= 100_000_000:
        raise InvalidInput("Сумма выглядит странно")
    return amount or None
