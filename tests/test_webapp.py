import asyncio
import hashlib
import hmac
import json
import time
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiosqlite
import pytest
from aiohttp.test_utils import TestClient, TestServer

from marta.ai import normalize_parse
from marta.config import Settings
from marta.db import Database
from marta.service import InvalidInput, Marta
from marta.timeutil import DAY
from marta.views import QUOTES, card_text, morning_text
from marta.webapp import build_app, check_init_data

TOKEN = "123:test-token"
OWNER = 601067607
NOW = datetime(2026, 10, 21, 10, 0)  # среда


def sign(user_id: int, token: str = TOKEN, auth_date: int | None = None) -> str:
    pairs = {"auth_date": str(auth_date or int(time.time())), "query_id": "q1",
             "user": json.dumps({"id": user_id, "first_name": "Ольга"}, ensure_ascii=False)}
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


class FakeAI:
    def __init__(self, reply=None):
        self.reply = reply

    async def parse(self, text, now, examples):
        return normalize_parse(self.reply, now, text)

    async def answer(self, question, now, records):
        return "Сантехник завтра в 10:00"

    async def quote(self, now, kind, theme, avoid):
        raise RuntimeError("нет связи")


class FakeMarta(Marta):
    def __init__(self, db, ai=None):
        settings = Settings(TOKEN, OWNER, "k", "s", "m", "", "small", "/tmp", ":memory:",
                            ZoneInfo("Europe/Moscow"), datetime(2026, 1, 1, 9, 0).time(),
                            datetime(2026, 1, 1, 23, 0).time(), 30)
        super().__init__(settings, db, ai or FakeAI(), None)
        self.clock = NOW

    def now(self):
        return self.clock


def run(coro):
    return asyncio.run(coro)


async def make_marta(ai=None):
    db = Database(":memory:")
    await db.connect()
    return FakeMarta(db, ai)


def test_init_data_signature():
    assert check_init_data(sign(OWNER), TOKEN)["id"] == OWNER
    assert check_init_data(sign(OWNER, token="999:other"), TOKEN) is None  # подписано чужим ботом
    assert check_init_data(sign(OWNER, auth_date=int(time.time()) - 2 * 86400), TOKEN) is None  # устарело
    tampered = sign(OWNER).replace("601067607", "601067608")
    assert check_init_data(tampered, TOKEN) is None
    assert check_init_data("", TOKEN) is None
    assert check_init_data("garbage", TOKEN) is None


def test_migration_from_old_database(tmp_path):
    path = str(tmp_path / "old.db")

    async def scenario():
        async with aiosqlite.connect(path) as conn:  # база прошлой версии: без суммы, с категорией «Дети»
            await conn.executescript("""
                CREATE TABLE items (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, title TEXT NOT NULL,
                    category TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '', due_at TEXT, all_day INTEGER NOT NULL DEFAULT 0,
                    recurrence TEXT NOT NULL DEFAULT 'none', offsets TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'draft', source TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                    done_at TEXT);
                CREATE TABLE category_examples (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
                    category TEXT NOT NULL, created_at TEXT NOT NULL);
                INSERT INTO items (kind, title, category, status, created_at)
                    VALUES ('task', 'Собрание в школе', 'Дети', 'active', '2026-10-01 10:00');
                INSERT INTO category_examples (text, category, created_at) VALUES ('тренировка', 'Дети', '2026-10-01 10:00');
            """)
            await conn.commit()
        for _ in range(2):  # повторный запуск ничего не ломает
            db = Database(path)
            await db.connect()
            item = await db.get_item(1)
            assert item["category"] == "Дом" and item["amount"] is None
            assert (await db.examples())[0]["category"] == "Дом"
            await db.close()

    run(scenario())


def test_amount_from_ai_and_texts():
    parsed = normalize_parse({"intent": "create", "items": [
        {"kind": "payment", "title": "Коммуналка", "category": "Платежи", "date": "2026-10-25", "amount": 4200},
        {"kind": "payment", "title": "Интернет", "category": "Платежи", "date": "2026-10-26", "amount": "650,50"},
        {"kind": "task", "title": "Врач", "category": "Здоровье", "date": "2026-10-23", "amount": 500},
    ]}, NOW, "коммуналка 4200 до 25-го")
    rent, net, doctor = parsed["items"]
    assert (rent["amount"], net["amount"], doctor["amount"]) == (4200, 650.5, None)
    assert "Сумма: 4 200 ₽" in card_text({**rent, "status": "active"}, NOW.date())
    note = {"kind": "note", "title": "Фильм «Достать ножи»", "category": "Рекомендации", "tags": "",
            "source": "Лена советует https://www.kinopoisk.ru/film/1188529/.", "recurrence": "none", "offsets": []}
    assert "🔗 https://www.kinopoisk.ru/film/1188529/" in card_text(note, NOW.date())
    text = morning_text([{"kind": "task", "title": "Врач", "category": "Здоровье", "due_at": "2026-10-21 15:00",
                          "all_day": 0}], [], [], NOW.date(), "Начни с малого")
    assert text.startswith("☀️ <b>Доброе утро!</b>\n<i>Начни с малого</i>\n\n<b>Сегодня:</b>")


def test_form_rules():
    async def scenario():
        marta = await make_marta()
        note = await marta.create_from_form({"kind": "note", "title": "Посмотреть сериал", "category": "Рекомендации"})
        assert (note["kind"], note["due_at"], note["status"]) == ("note", None, "active")
        # заметка с датой становится делом с напоминаниями по умолчанию
        task = await marta.update_from_form(note, {"date": "2026-10-24", "time": ""})
        assert (task["kind"], task["due_at"], task["offsets"]) == ("task", "2026-10-24 09:00", [DAY])
        # смена категории учит Марту, как кнопка в боте
        await marta.update_from_form(task, {"category": "Дом"})
        assert (await marta.db.examples())[0] == {"text": "Посмотреть сериал", "category": "Дом"}
        pay = await marta.create_from_form({"kind": "payment", "title": "Кредит", "category": "Дом",
                                            "date": "2026-10-28", "amount": "12 500", "recurrence": "monthly"})
        assert (pay["category"], pay["amount"], pay["offsets"]) == ("Платежи", 12500, [DAY, 0])
        pay = await marta.update_from_form(pay, {"offsets": [60, DAY, DAY]})
        assert pay["offsets"] == [DAY, 60]
        for bad in ({"title": ""}, {"category": "Дети"}, {"date": "завтра"}, {"time": "25:00"},
                    {"amount": "много"}, {"offsets": [7]}):
            with pytest.raises(InvalidInput):
                await marta.update_from_form(pay, bad)
        await marta.db.close()

    run(scenario())


def test_month_with_repeats_and_payments_summary():
    async def scenario():
        marta = await make_marta()
        rent = await marta.create_from_form({"kind": "payment", "title": "Коммуналка", "date": "2026-10-25",
                                             "amount": "4200", "recurrence": "monthly"})
        await marta.create_from_form({"kind": "payment", "title": "Интернет", "date": "2026-10-20", "amount": "650"})
        await marta.create_from_form({"kind": "task", "title": "Тренировка", "category": "Здоровье",
                                      "date": "2026-10-21", "time": "19:00", "recurrence": "weekly"})
        october = [(i["title"], f"{d:%d}", rep) for i, d, rep in await marta.month(2026, 10)]
        assert ("Коммуналка", "25", False) in october and ("Тренировка", "28", True) in october
        november = [(i["title"], f"{d:%d}", rep) for i, d, rep in await marta.month(2026, 11)]
        assert ("Коммуналка", "25", True) in november and ("Интернет", "20", True) not in november

        summary = await marta.payments_summary()
        assert [p["title"] for p in summary["overdue"]] == ["Интернет"]
        assert summary["left_sum"] == 4850 and summary["paid_sum"] == 0
        await marta.mark_done(rent)  # оплатили: переносится на ноябрь и попадает в «оплачено»
        summary = await marta.payments_summary()
        assert summary["left_sum"] == 650 and summary["paid_sum"] == 4200
        await marta.db.close()

    run(scenario())


def test_shopping_kept_for_a_day_and_quote_fallback():
    async def scenario():
        marta = await make_marta()
        milk = await marta.create_from_form({"kind": "shopping", "title": "Молоко"})
        await marta.mark_bought(milk)
        assert await marta.db.shopping() == [] and len(await marta.bought_recently()) == 1
        await marta.unbuy(milk)
        assert len(await marta.db.shopping()) == 1
        await marta.mark_bought(await marta.db.get_item(milk["id"]))
        marta.clock = datetime(2026, 10, 22, 23, 0)
        await marta.nightly()
        assert await marta.db.get_item(milk["id"]) is None

        quote = await marta.quote_of_day()  # GigaChat не ответил — запасная фраза
        assert quote in QUOTES and await marta.quote_of_day() == quote
        await marta.db.close()

    run(scenario())


def test_api_flow():
    reply = {"intent": "create", "items": [
        {"kind": "task", "title": "Сдать анализы", "category": "Здоровье", "date": "2026-10-23", "time": "08:30"},
        {"kind": "shopping", "title": "Хлеб", "category": "Покупки"},
    ]}

    async def scenario():
        marta = await make_marta(FakeAI(reply))
        client = TestClient(TestServer(build_app(marta)))
        await client.start_server()
        owner = {"Authorization": "tma " + sign(OWNER)}
        try:
            assert (await client.get("/api/today")).status == 403
            assert (await client.get("/api/today", headers={"Authorization": "tma " + sign(42)})).status == 403
            page = await client.get("/")
            assert page.status == 200 and "frame-ancestors" in page.headers["Content-Security-Policy"]

            res = await (await client.post("/api/parse", json={"text": "в пятницу анализы, купить хлеб"},
                                           headers=owner)).json()
            assert res["kind"] == "created" and res["shopping"] == ["Хлеб"]
            draft = res["items"][0]
            assert draft["status"] == "draft" and draft["when"] == "послезавтра в 08:30"

            today = await (await client.get("/api/today", headers=owner)).json()
            assert [d["title"] for d in today["drafts"]] == ["Сдать анализы"]
            assert (await client.post(f"/api/items/{draft['id']}/save", headers=owner)).status == 200

            res = await client.patch(f"/api/items/{draft['id']}", json={"time": "09:15"}, headers=owner)
            assert (await res.json())["item"]["when"] == "послезавтра в 09:15"
            res = await client.patch(f"/api/items/{draft['id']}", json={"category": "Нет такой"}, headers=owner)
            assert res.status == 400 and (await res.json())["error"] == "Неизвестная категория"

            assert (await client.post(f"/api/items/{draft['id']}/done", headers=owner)).status == 200
            today = await (await client.get("/api/today", headers=owner)).json()
            assert today["done"] == [{"title": "Сдать анализы"}]

            shop = await (await client.get("/api/shopping", headers=owner)).json()
            bread = shop["active"][0]["id"]
            assert (await client.post(f"/api/shopping/{bread}/toggle", headers=owner)).status == 200
            shop = await (await client.get("/api/shopping", headers=owner)).json()
            assert shop["active"] == [] and shop["bought"][0]["title"] == "Хлеб"

            settings = await (await client.put("/api/settings", json={"morning_at": "08:30", "confirm": False},
                                               headers=owner)).json()
            assert settings == {"confirm": False, "morning_at": "08:30", "evening_at": "23:00"}
            assert (await client.put("/api/settings", json={"evening_at": "99:00"}, headers=owner)).status == 400

            found = await (await client.get("/api/search?q=анализ", headers=owner)).json()
            assert found["items"] == []  # выполненное не ищется
            cal = await (await client.get("/api/calendar?month=2026-10", headers=owner)).json()
            assert cal["title"] == "октябрь 2026"
            assert (await client.delete(f"/api/items/{bread}", headers=owner)).status == 200
            assert (await client.delete(f"/api/items/{bread}", headers=owner)).status == 404
        finally:
            await client.close()
            await marta.db.close()

    run(scenario())


class RepeatingAI(FakeAI):
    """GigaChat, который любит одну и ту же фразу."""

    def __init__(self):
        super().__init__()
        self.calls = []

    async def quote(self, now, kind, theme, avoid):
        self.calls.append((kind, theme, list(avoid)))
        if len(self.calls) % 4 == 0:
            return f"Новая мысль про {theme} — Сенека"
        return "Не торопись, всё сложится как надо!"


def test_quote_of_day_does_not_repeat():
    async def scenario():
        ai = RepeatingAI()
        marta = await make_marta(ai)
        try:
            await marta.db.set_setting("quote_date", "2026-10-26")  # фраза прошлой версии, до истории
            await marta.db.set_setting("quote_text", "Не торопись, всё сложится как надо!")
            quotes = []
            for day in range(27, 32):
                marta.clock = datetime(2026, 10, day, 9, 0)
                quotes.append(await marta.quote_of_day())
                assert await marta.quote_of_day() == quotes[-1]  # весь день одна и та же
            assert "Не торопись, всё сложится как надо!" not in quotes
            assert len(set(quotes)) == len(quotes)  # пять дней — пять разных фраз
            assert ai.calls[0][2][-1].startswith("Не торопись")  # GigaChat видит недавние фразы
            assert len({kind for kind, _, _ in ai.calls}) >= 3  # виды фраз чередуются
        finally:
            await marta.db.close()

    run(scenario())
