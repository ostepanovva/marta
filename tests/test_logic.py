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


def test_relative_day_safety_net():
    night = datetime(2026, 10, 22, 0, 17)  # после полуночи «завтра» — это 23-е
    # GigaChat ошибся: заметка без даты в «Рекомендациях»
    raw = {"intent": "create", "items": [{"kind": "note", "title": "Поздравление Тане", "category": "Рекомендации"}]}
    item = normalize_parse(raw, night, "Завтра утром отправить поздравление Тане")["items"][0]
    assert (item["kind"], item["category"], item["due_at"], item["all_day"]) == ("task", "Дом", "2026-10-23 09:00", False)
    assert item["offsets"] == [DAY, 60]

    raw = {"intent": "create", "items": [{"kind": "note", "title": "Фильм «Достать ножи»", "category": "Рекомендации"}]}
    item = normalize_parse(raw, night, "Сегодня Лена посоветовала фильм Достать ножи")["items"][0]
    assert (item["kind"], item["due_at"]) == ("note", None)

    raw = {"intent": "create", "items": [{"kind": "task", "title": "Позвонить маме", "category": "Дом"}]}
    item = normalize_parse(raw, night, "послезавтра вечером позвонить маме")["items"][0]
    assert item["due_at"] == "2026-10-24 19:00"

    # дату, которую поставил ИИ, не трогаем; в исправлении смотрим только на само исправление
    raw = {"intent": "create", "items": [{"kind": "task", "title": "Врач", "category": "Здоровье"}]}
    item = normalize_parse(raw, night, "завтра к врачу\nИсправление: без даты")["items"][0]
    assert item["due_at"] is None
    raw = {"intent": "create", "items": [{"kind": "task", "title": "Врач", "category": "Здоровье",
                                          "date": "2026-10-30", "time": "15:00"}]}
    assert normalize_parse(raw, night, "к врачу 30-го в 15")["items"][0]["due_at"] == "2026-10-30 15:00"
    assert normalize_parse(raw, night, "завтра к врачу")["items"][0]["due_at"] == "2026-10-23 15:00"  # слова важнее


def test_said_day_wins_over_ai_date():
    morning = datetime(2026, 10, 23, 8, 55)
    wrong = {"intent": "create", "items": [{"kind": "task", "title": "Подготовить договор с Medcom", "category": "Работа",
                                            "date": "2026-10-24"}]}
    item = normalize_parse(wrong, morning, "Сегодня подготовь договор с компанией Medcom.")["items"][0]
    assert item["due_at"] == "2026-10-23 09:00"
    # названа другая дата или два разных дня — решает GigaChat
    for text in ("сегодня решить, что в пятницу везем договор", "сегодня купить хлеб, а завтра отвезти договор",
                 "сегодня договориться о встрече 24 октября"):
        assert normalize_parse(wrong, morning, text)["items"][0]["due_at"] == "2026-10-24 09:00", text


def test_weekday_wins_over_wrong_ai_date():
    monday = datetime(2026, 10, 26, 13, 8)
    def due(text, date="", time="", kind="task", recurrence="none"):
        raw = {"intent": "create", "items": [{"kind": kind, "title": "Прием у стоматолога", "category": "Здоровье",
                                              "date": date, "time": time, "recurrence": recurrence}]}
        return normalize_parse(raw, monday, text)["items"][0]
    # GigaChat поставил вторник 3 ноября вместо четверга 29 октября
    item = due("Марта, запиши пожалуйста прием у стоматолога в четверг в 11.30.", "2026-11-03", "11:30")
    assert item["due_at"] == "2026-10-29 11:30"
    assert due("по четвергам бассейн", "2026-10-27", "", recurrence="weekly")["due_at"] == "2026-10-29 09:00"
    assert due("в пятницу купить подарок", kind="note")["due_at"] == "2026-10-30 09:00"
    assert due("в понедельник отчет", "2026-10-25")["due_at"] == "2026-11-02 09:00"  # сегодня понедельник — следующий
    assert due("в понедельник отчет", "2026-10-26")["due_at"] == "2026-10-26 09:00"  # понедельник от GigaChat не трогаем
    # верный четверг от GigaChat и точная дата не трогаем
    assert due("в следующий четверг к врачу", "2026-11-05")["due_at"] == "2026-11-05 09:00"
    assert due("в четверг 5 ноября к врачу", "2026-11-05")["due_at"] == "2026-11-05 09:00"
    assert due("сегодня записаться, а прием в четверг", "2026-10-26")["due_at"] == "2026-10-26 09:00"
    assert due("в четверг 10.11 к врачу", "2026-11-10")["due_at"] == "2026-11-10 09:00"  # 10.11 — это дата
    assert due("в четверг 5 марта к врачу", "2027-03-05")["due_at"] == "2027-03-05 09:00"
    assert due("Марта, в четверг к врачу", "2026-11-03")["due_at"] == "2026-10-29 09:00"  # Марта — имя
    # «в 11.30» — время, а не дата: «сегодня» все равно побеждает ошибку GigaChat
    assert due("сегодня в 11.30 к врачу", "2026-10-27", "11:30")["due_at"] == "2026-10-26 11:30"


def test_said_time_wins():
    monday = datetime(2026, 10, 26, 13, 14)
    def due(text, time=""):
        raw = {"intent": "create", "items": [{"kind": "task", "title": "Прием у стоматолога", "category": "Здоровье",
                                              "date": "2026-10-29", "time": time}]}
        item = normalize_parse(raw, monday, text)["items"][0]
        return item["due_at"], item["offsets"]
    # GigaChat потерял время из «четверг в 11.30»
    assert due("Приём у стоматологов четверг в 11.30.") == ("2026-10-29 11:30", [DAY, 60])
    assert due("в четверг в 11:30 стоматолог")[0] == "2026-10-29 11:30"
    assert due("в четверг в 8 вечера созвон", "08:00")[0] == "2026-10-29 20:00"  # GigaChat перепутал
    assert due("в четверг в 12 дня обед")[0] == "2026-10-29 12:00"
    assert due("в четверг в 7 утра пробежка")[0] == "2026-10-29 07:00"
    # время от GigaChat без явного времени в тексте и два разных времени — не трогаем
    assert due("в четверг после обеда", "14:00")[0] == "2026-10-29 14:00"
    assert due("в четверг в 10:00 или в 12:30", "10:00")[0] == "2026-10-29 10:00"
    assert due("в четверг стоматолог")[0] == "2026-10-29 09:00"
