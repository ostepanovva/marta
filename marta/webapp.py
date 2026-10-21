"""Мини-приложение Telegram: страница и запросы к данным Марты.

Работает в той же программе, что и бот, и пользуется той же логикой (service.py).
Каждый запрос подписан Telegram (initData) — данные видит только владелец.
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

from .ai import AIError
from .config import CATEGORIES
from .service import InvalidInput, Marta, start_of_day
from .timeutil import RECURRENCE_LABELS, from_str, human_when, offsets_label
from .views import ICONS, icon, links, money

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
INIT_DATA_TTL = 24 * 60 * 60  # подпись действует сутки, потом приложение нужно открыть заново
MONTHS = ["январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь",
          "ноябрь", "декабрь"]
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' https://telegram.org; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; base-uri 'none'; form-action 'none'; "
        "frame-ancestors https://web.telegram.org https://*.telegram.org"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}


def check_init_data(init_data: str, bot_token: str, now: float | None = None) -> dict | None:
    """Проверяет подпись Telegram и возвращает пользователя, или None, если подпись неверна."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received = pairs.pop("hash", "")
    if not received:
        return None
    check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
        user = json.loads(pairs.get("user", "{}"))
    except ValueError:
        return None
    if abs((now or time.time()) - auth_date) > INIT_DATA_TTL or not isinstance(user, dict):
        return None
    return user


@web.middleware
async def security(request: web.Request, handler):
    if request.path.startswith("/api/"):
        marta: Marta = request.app["marta"]
        init_data = request.headers.get("Authorization", "").removeprefix("tma ").strip()
        user = check_init_data(init_data, marta.settings.bot_token)
        if not user or user.get("id") != marta.settings.owner_id:
            log.warning("Мини-приложение: чужой или неподписанный запрос отклонен (%s)", request.path)
            return web.json_response({"error": "Доступа нет"}, status=403)
    try:
        response = await handler(request)
    except InvalidInput as exc:
        response = web.json_response({"error": str(exc)}, status=400)
    except AIError:
        log.exception("Мини-приложение: ошибка GigaChat")
        response = web.json_response({"error": "Не получилось связаться с GigaChat, попробуй через минуту"},
                                     status=502)
    response.headers.update(SECURITY_HEADERS)
    if request.path.startswith("/api/") or request.path == "/":
        response.headers["Cache-Control"] = "no-store"
    elif request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"  # после обновления сразу новая версия
    return response


# --- как запись выглядит для приложения ---


def item_json(item: dict, marta: Marta, when: datetime | None = None, repeat: bool = False) -> dict:
    today = marta.now().date()
    due = when or (from_str(item["due_at"]) if item.get("due_at") else None)
    overdue = bool(
        due and not repeat and item["kind"] in ("task", "payment") and item["status"] == "active"
        and due < start_of_day(marta.now())
    )
    return {
        "id": item["id"],
        "kind": item["kind"],
        "title": item["title"],
        "category": item["category"],
        "icon": icon(item),
        "tags": item.get("tags") or "",
        "status": item["status"],
        "date": f"{due:%Y-%m-%d}" if due else "",
        "time": "" if not due or item["all_day"] else f"{due:%H:%M}",
        "when": human_when(due, bool(item["all_day"]), today) if due else "",
        "recurrence": item["recurrence"],
        "recurrence_label": RECURRENCE_LABELS.get(item["recurrence"], ""),
        "offsets": item["offsets"],
        "offsets_label": offsets_label(item["offsets"], bool(item["all_day"])) if due else "",
        "amount": item.get("amount"),
        "amount_label": money(item.get("amount")),
        "source": item.get("source") or "",
        "links": links(item.get("source")),
        "overdue": overdue,
        "repeat": repeat,
    }


def items_json(items: list[dict], marta: Marta) -> list[dict]:
    return [item_json(i, marta) for i in items]


async def body(request: web.Request) -> dict:
    try:
        data = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise InvalidInput("Не понимаю запрос")
    if not isinstance(data, dict):
        raise InvalidInput("Не понимаю запрос")
    return data


async def load(request: web.Request) -> dict:
    marta: Marta = request.app["marta"]
    try:
        item = await marta.db.get_item(int(request.match_info["id"]))
    except ValueError:
        item = None
    if not item:
        raise web.HTTPNotFound(text=json.dumps({"error": "Этой записи уже нет"}), content_type="application/json")
    return item


def ok(**data) -> web.Response:
    return web.json_response({"ok": True, **data})


# --- страницы ---


async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(STATIC / "index.html")


async def api_meta(request: web.Request) -> web.Response:
    return web.json_response({
        "categories": [{"name": c, "icon": ICONS[c]} for c in CATEGORIES],
        "recurrences": [{"value": "none", "label": "Не повторять"}]
        + [{"value": k, "label": v.capitalize()} for k, v in RECURRENCE_LABELS.items()],
    })


async def api_today(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    drafts = await marta.db.drafts()
    return web.json_response({
        "date": f"{marta.now():%Y-%m-%d}",
        "quote": await marta.db.get_setting("quote_text", "") if await has_quote_today(marta) else "",
        "drafts": items_json(drafts, marta),
        "overdue": items_json(await marta.overdue(), marta),
        "today": items_json(await marta.today(), marta),
        "upcoming": items_json(await marta.upcoming(), marta),
        "done": [{"title": c["title"]} for c in await marta.done_today()],
    })


async def has_quote_today(marta: Marta) -> bool:
    """Фраза дня появляется вместе с утренней сводкой; до нее приложение фразу не показывает."""
    return await marta.db.get_setting("quote_date", "") == marta.now().date().isoformat()


async def api_calendar(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    now = marta.now()
    try:
        year, month = (int(x) for x in request.query.get("month", f"{now:%Y-%m}").split("-"))
        datetime(year, month, 1)
    except ValueError:
        raise InvalidInput("Неверный месяц")
    days: dict[str, list] = {}
    for item, when, repeat in await marta.month(year, month):
        days.setdefault(f"{when:%Y-%m-%d}", []).append(item_json(item, marta, when, repeat))
    return web.json_response({"month": f"{year:04d}-{month:02d}", "title": f"{MONTHS[month - 1]} {year}",
                              "today": f"{now:%Y-%m-%d}", "days": days})


async def api_payments(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    summary = await marta.payments_summary()
    paid = [{"title": p["title"], "amount_label": money(p["amount"]),
             "when": human_when(from_str(p["done_at"]), True, marta.now().date())} for p in summary["paid"]]
    return web.json_response({
        "month": MONTHS[marta.now().month - 1],
        "overdue": items_json(summary["overdue"], marta),
        "soon": items_json(summary["soon"], marta),
        "paid": paid,
        "left_label": money(summary["left_sum"]) or "0 ₽",
        "paid_label": money(summary["paid_sum"]) or "0 ₽",
    })


async def api_notes(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    return web.json_response({"notes": items_json(await marta.db.notes(), marta)})


async def api_shopping(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    return web.json_response({
        "active": items_json(await marta.db.shopping(), marta),
        "bought": items_json(await marta.bought_recently(), marta),
    })


async def api_categories(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    counts = await marta.db.category_counts()
    counts["Покупки"] = counts.get("Покупки", 0) + len(await marta.db.shopping())
    return web.json_response({"categories": [{"name": c, "icon": ICONS[c], "count": counts.get(c, 0)}
                                             for c in CATEGORIES]})


async def api_category(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    name = request.query.get("name", "")
    if name not in CATEGORIES:
        raise InvalidInput("Неизвестная категория")
    return web.json_response({"items": items_json(await marta.db.by_category(name, limit=200), marta)})


async def api_search(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    return web.json_response({"items": items_json(await marta.search(request.query.get("q", "")[:100]), marta)})


async def api_settings(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    if request.method == "PUT":
        data = await body(request)
        if "confirm" in data:
            await marta.set_confirm(bool(data["confirm"]))
            log.info("Мини-приложение: подтверждение записей %s", "включено" if data["confirm"] else "выключено")
        for key in ("morning_at", "evening_at"):
            if key in data:
                await marta.set_summary_time(key, str(data[key]))
    morning, evening = await marta.summary_times()
    return web.json_response({"confirm": await marta.confirm_enabled(),
                              "morning_at": f"{morning:%H:%M}", "evening_at": f"{evening:%H:%M}"})


# --- действия с записями ---


async def api_done(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    item = await load(request)
    if item["kind"] not in ("task", "payment") or item["status"] != "active":
        raise InvalidInput("Эту запись нельзя отметить")
    await marta.mark_done(item)
    log.info("Мини-приложение: отмечено готово — %s", item["title"])
    fresh = await marta.db.get_item(item["id"])
    next_when = item_json(fresh, marta)["when"] if fresh["status"] == "active" else ""
    return ok(next=next_when)


async def api_save_draft(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    item = await load(request)
    await marta.db.update_item(item["id"], status="active")
    return ok(item=item_json(await marta.db.get_item(item["id"]), marta))


async def api_update(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    item = await load(request)
    if item["kind"] == "shopping":
        raise InvalidInput("Покупку можно только отметить или удалить")
    fresh = await marta.update_from_form(item, await body(request))
    log.info("Мини-приложение: изменено — %s", fresh["title"])
    return ok(item=item_json(fresh, marta))


async def api_delete(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    item = await load(request)
    await marta.db.delete_item(item["id"])
    log.info("Мини-приложение: удалено — %s", item["title"])
    return ok()


async def api_shop_toggle(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    item = await load(request)
    if item["kind"] != "shopping":
        raise InvalidInput("Это не покупка")
    if item["status"] == "active":
        await marta.mark_bought(item)
    else:
        await marta.unbuy(item)
    return ok()


async def api_create(request: web.Request) -> web.Response:
    marta: Marta = request.app["marta"]
    item = await marta.create_from_form(await body(request))
    log.info("Мини-приложение: записано вручную — %s", item["title"])
    return ok(item=item_json(item, marta))


async def api_parse(request: web.Request) -> web.Response:
    """Текст из строки «Напиши, что запомнить» — так же, как сообщение в чате."""
    marta: Marta = request.app["marta"]
    data = await body(request)
    text = str(data.get("text", "")).strip()[:2000]
    if not text:
        raise InvalidInput("Напиши, что запомнить")
    if data.get("pending"):  # ответ на уточняющий вопрос
        text = f"{str(data['pending'])[:2000]}\n{text}"
    parsed = await marta.parse(text)
    if parsed["intent"] == "question":
        return web.json_response({"kind": "answer", "text": await marta.answer_question(text) or "Хм, не нашла ответа"})
    if parsed["items"]:
        shopping, others = await marta.save_parsed(parsed["items"])
        log.info("Мини-приложение: записано из текста — %s", ", ".join(i["title"] for i in parsed["items"]))
        return web.json_response({"kind": "created", "shopping": [i["title"] for i in shopping],
                                  "items": items_json(others, marta)})
    if parsed["clarify"]:
        return web.json_response({"kind": "clarify", "text": parsed["clarify"], "pending": text})
    return web.json_response({"kind": "answer", "text": await marta.ai.chat(text) or "Я тут"})


def build_app(marta: Marta) -> web.Application:
    app = web.Application(middlewares=[security], client_max_size=64 * 1024)
    app["marta"] = marta
    app.router.add_get("/", index)
    app.router.add_static("/static/", STATIC, show_index=False)
    app.router.add_get("/api/meta", api_meta)
    app.router.add_get("/api/today", api_today)
    app.router.add_get("/api/calendar", api_calendar)
    app.router.add_get("/api/payments", api_payments)
    app.router.add_get("/api/notes", api_notes)
    app.router.add_get("/api/shopping", api_shopping)
    app.router.add_get("/api/categories", api_categories)
    app.router.add_get("/api/category", api_category)
    app.router.add_get("/api/search", api_search)
    app.router.add_route("GET", "/api/settings", api_settings)
    app.router.add_route("PUT", "/api/settings", api_settings)
    app.router.add_post("/api/items", api_create)
    app.router.add_post("/api/parse", api_parse)
    app.router.add_post("/api/items/{id}/done", api_done)
    app.router.add_post("/api/items/{id}/save", api_save_draft)
    app.router.add_patch("/api/items/{id}", api_update)
    app.router.add_delete("/api/items/{id}", api_delete)
    app.router.add_post("/api/shopping/{id}/toggle", api_shop_toggle)
    return app


async def start(marta: Marta) -> web.AppRunner:
    runner = web.AppRunner(build_app(marta), access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", marta.settings.webapp_port).start()
    log.info("Мини-приложение запущено на порту %s", marta.settings.webapp_port)
    return runner
