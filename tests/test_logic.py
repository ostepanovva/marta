import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from marta.ai import extract_arguments, normalize_parse
from marta.config import Settings
from marta.db import Database
from marta.service import Marta
from marta.timeutil import DAY, add_months, human_when, offsets_label, roll_forward
from marta.views import card_text

NOW = datetime(2026, 10, 20, 14, 30)  # вторник


def test_add_months_clamps_day():
    assert add_months(datetime(2026, 1, 31, 9, 0), 1) == datetime(2026, 2, 28, 9, 0)
    assert add_months(datetime(2026, 12, 15, 9, 0), 1) == datetime(2027, 1, 15, 9, 0)


def test_roll_forward_birthday_from_birth_year():
    assert roll_forward(datetime(1985, 3, 12, 9, 0), "yearly", NOW) == datetime(2027, 3, 12, 9, 0)
    assert roll_forward(datetime(1985, 11, 2, 9, 0), "yearly", NOW) == datetime(2026, 11, 2, 9, 0)


def test_human_when():
    today = NOW.date()
    assert human_when(datetime(2026, 10, 20, 15, 0), False, today) == "сегодня в 15:00"
    assert human_when(datetime(2026, 10, 21, 9, 0), True, today) == "завтра"
    assert human_when(datetime(2026, 10, 23, 15, 0), False, today) == "пт, 23 окт в 15:00"


def test_offsets_label():
    assert offsets_label([DAY, 60], False) == "за день и за час"
    assert offsets_label([3 * DAY, 0], True) == "за 3 дн. и в сам день в 9:00"


def test_normalize_task_payment_birthday_shopping():
    raw = {
        "intent": "create",
        "items": [
            {"kind": "task", "title": "врач", "category": "здоровье", "date": "2026-10-23", "time": "15:00"},
            {"kind": "payment", "title": "Коммуналка", "category": "Финансы", "date": "2026-10-25",
             "recurrence": "monthly"},
            {"kind": "birthday", "title": "День рождения мамы", "category": "Дом", "date": "1960-03-12"},
            {"kind": "shopping", "title": "молоко", "category": "Дом"},
            {"kind": "task", "title": "", "category": "Дом"},
        ],
    }
    parsed = normalize_parse(raw, NOW, "текст")
    task, payment, birthday, milk = parsed["items"]
    assert parsed["intent"] == "create"
    assert (task["title"], task["category"], task["due_at"], task["all_day"]) == ("Врач", "Здоровье", "2026-10-23 15:00", False)
    assert task["offsets"] == [DAY, 60]
    assert (payment["category"], payment["due_at"], payment["offsets"]) == ("Платежи", "2026-10-25 09:00", [DAY, 0])
    assert (birthday["recurrence"], birthday["due_at"]) == ("yearly", "2027-03-12 09:00")
    assert (milk["category"], milk["due_at"]) == ("Покупки", None)


def test_extract_arguments_from_function_call_and_text():
    assert extract_arguments({"function_call": {"name": "x", "arguments": {"intent": "chat"}}}) == {"intent": "chat"}
    assert extract_arguments({"function_call": {"arguments": '{"intent": "question"}'}}) == {"intent": "question"}
    assert extract_arguments({"content": 'Вот: {"intent": "create", "items": []}'})["intent"] == "create"
    assert extract_arguments({"content": "привет"}) is None


class FakeMarta(Marta):
    def __init__(self, db, clock):
        settings = Settings("t", 1, "k", "s", "m", "", "small", "/tmp", ":memory:",
                            ZoneInfo("Europe/Moscow"), None, None, 30)
        super().__init__(settings, db, None, None)
        self.clock = clock

    def now(self):
        return self.clock[0]


def run(coro):
    return asyncio.run(coro)


def test_reminders_done_and_recurrence():
    async def scenario():
        db = Database(":memory:")
        await db.connect()
        clock = [NOW]
        marta = FakeMarta(db, clock)
        parsed = normalize_parse({"intent": "create", "items": [
            {"kind": "task", "title": "Врач", "category": "Здоровье", "date": "2026-10-21", "time": "15:00"},
            {"kind": "payment", "title": "Коммуналка", "category": "Платежи", "date": "2026-10-25",
             "recurrence": "monthly"},
        ]}, NOW, "")
        doctor_id = await db.add_item({**parsed["items"][0], "status": "active"}, NOW)
        pay_id = await db.add_item({**parsed["items"][1], "status": "active"}, NOW)

        assert await marta.due_reminders() == []
        clock[0] = datetime(2026, 10, 20, 15, 0)  # за день до врача
        sent = await marta.due_reminders()
        assert [(i["id"], off) for i, off in sent] == [(doctor_id, DAY)]
        assert await marta.due_reminders() == []  # повторно не шлем
        clock[0] = datetime(2026, 10, 21, 14, 0)  # за час
        assert [(i["id"], off) for i, off in await marta.due_reminders()] == [(doctor_id, 60)]

        # вечер 21-го: врач не отмечен
        clock[0] = datetime(2026, 10, 21, 23, 0)
        done, not_done, _ = await marta.evening()
        assert done == [] and [i["id"] for i in not_done] == [doctor_id]

        # оплатили коммуналку раньше срока — сдвигается на следующий месяц
        await marta.mark_done(await db.get_item(pay_id))
        assert (await db.get_item(pay_id))["due_at"] == "2026-11-25 09:00"
        await marta.mark_done(await db.get_item(doctor_id))
        assert (await db.get_item(doctor_id))["status"] == "done"
        done, not_done, _ = await marta.evening()
        assert len(done) == 2 and not_done == []

        # через 31 день выполненное удаляется, повторяющийся платеж остается
        clock[0] = datetime(2026, 11, 22, 0, 5)
        await marta.nightly()
        assert await db.get_item(doctor_id) is None
        assert await db.get_item(pay_id) is not None
        await db.close()

    run(scenario())


def test_card_text_for_draft():
    item = {"kind": "task", "title": "Врач", "category": "Здоровье", "due_at": "2026-10-23 15:00", "all_day": 0,
            "recurrence": "none", "offsets": [DAY, 60], "tags": "врач"}
    text = card_text(item, NOW.date())
    assert "пт, 23 окт в 15:00" in text and "за день и за час" in text and "#врач" in text
