"""Тексты сообщений Марты: карточки, сводки, напоминания."""

import random
import re
from datetime import date, datetime
from html import escape

from .timeutil import RECURRENCE_LABELS, from_str, human_when, offsets_label

ICONS = {
    "Работа": "💼",
    "Дом": "🏠",
    "Здоровье": "🩺",
    "Финансы": "💰",
    "Рекомендации": "⭐",
    "Покупки": "🛒",
    "Учеба": "📚",
    "Идеи": "💡",
    "Платежи": "💳",
    "Дача": "🌿",
}

MENU_TODAY = "📅 Сегодня"
MENU_CATEGORIES = "🗂 Категории"
MENU_SHOPPING = "🛒 Покупки"
MENU_IDEAS = "💡 Идеи"

MORNING_JOKES = [
    "Кофе — по желанию, дела — по расписанию ☕",
    "Я все помню, так что тебе можно не напрягаться 😉",
    "Хорошего дня! Если что — я рядом.",
    "План есть, настроение подтянется 💪",
]

# Запасные фразы дня, если GigaChat утром не ответил
QUOTES = [
    "Не обязательно успеть все. Достаточно сделать главное — и похвалить себя за это.",
    "Начни с одного маленького дела, остальное подтянется само.",
    "Ты справлялась и с более сложными днями. Этот тоже будет твоим.",
    "Сегодня можно не быть идеальной. Можно быть живой и довольной.",
    "Дела подождут пять минут, пока ты допиваешь чай. Это тоже часть плана.",
    "Каждое закрытое дело — маленькая победа. Собирай их с удовольствием.",
    "Отдых — не награда за дела, а часть хорошего дня.",
    "Ты не обязана делать все сама: я напомню, а что-то можно и поручить.",
    "Двигаться медленно — тоже двигаться.",
    "Сделай сегодня то, за что завтрашняя ты скажет спасибо.",
    "Хороший день начинается с одного решения: начать.",
    "Сегодня отличный день, чтобы закрыть то, что давно откладывала!",
    "Энергия приходит в процессе. Просто начни — дальше пойдет легче.",
    "Список дел короче, чем кажется. Проверим?",
    "Маленькие шаги каждый день дают большие результаты.",
    "Ты умеешь больше, чем думаешь. Сегодня — отличный повод это показать.",
    "Сделано лучше, чем идеально.",
    "Самое трудное дело — первое. Остальные пойдут как по маслу.",
    "Один день — одна главная цель. Остальное — бонус.",
    "Пусть сегодня будет день, когда все складывается.",
    "Лучший способ предсказать будущее — создать его. — Питер Друкер",
    "Дорогу осилит идущий. — Сенека",
    "Великие дела совершаются не силой, а упорством. — Сэмюэл Джонсон",
    "Начинать всегда стоит с того, что сеет сомнения. — Борис Стругацкий",
    "Счастье — это когда то, что ты думаешь, говоришь и делаешь, находится в гармонии. — Махатма Ганди",
    "Жизнь — это то, что с тобой происходит, пока ты строишь планы. — Джон Леннон",
    "Время — самый мудрый советчик. — Перикл",
    "Не откладывай на завтра то, что можно сделать сегодня. — Бенджамин Франклин",
    "Кто хочет — ищет возможности, кто не хочет — ищет причины. — Сократ",
    "Успех — это способность идти от неудачи к неудаче, не теряя энтузиазма. — Уинстон Черчилль",
    "Все, что нам нужно решить, — это что делать со временем, которое нам дано. — Дж. Р. Р. Толкин",
    "Самый темный час — перед рассветом. — Томас Фуллер",
    "Терпение и труд все перетрут. — русская пословица",
    "Тише едешь — дальше будешь. — русская пословица",
    "Дело мастера боится. — русская пословица",
    "Действие — ключ к любому успеху. — Пабло Пикассо",
    "Делай, что можешь, с тем, что имеешь, там, где ты есть. — Теодор Рузвельт",
    "Простота — высшая степень изощренности. — Леонардо да Винчи",
    "Где есть желание, найдется путь. — английская пословица",
    "Лучше зажечь одну свечу, чем проклинать темноту. — Конфуций",
]

URL_RE = re.compile(r"https?://[^\s<>\"«»]+")


def money(amount: float | None) -> str:
    """4200 → «4 200 ₽»."""
    if not amount:
        return ""
    text = f"{amount:,.2f}".rstrip("0").rstrip(".").replace(",", " ").replace(".", ",")
    return f"{text} ₽"


def links(text: str | None) -> list[str]:
    return [u.rstrip(".,;:!?)") for u in URL_RE.findall(text or "")]
EMPTY_DAY = [
    "Дел нет — редкий случай, наслаждайся 😌",
    "Сегодня свободно. Подозрительно, но приятно.",
]


def icon(item: dict) -> str:
    return "🎂" if item["kind"] == "birthday" else ICONS.get(item["category"], "📌")


def when(item: dict, today: date) -> str:
    if not item.get("due_at"):
        return ""
    return human_when(from_str(item["due_at"]), bool(item["all_day"]), today)


def item_line(item: dict, today: date) -> str:
    text = f"{icon(item)} {escape(item['title'])}"
    moment = when(item, today)
    if moment:
        text += f" — {moment}"
    if item.get("amount"):
        text += f", {money(item['amount'])}"
    return text


def card_text(item: dict, today: date) -> str:
    lines = [f"{icon(item)} <b>{escape(item['title'])}</b>", f"{item['category']}"]
    if item.get("amount"):
        lines.append(f"Сумма: {money(item['amount'])}")
    if item["kind"] == "note":
        lines.append("Сохраню без напоминания.")
    elif item.get("due_at"):
        lines.append(when(item, today))
        if item["recurrence"] != "none":
            lines.append(f"Повтор: {RECURRENCE_LABELS[item['recurrence']]}")
        if item["offsets"]:
            lines.append(f"Напомню {offsets_label(item['offsets'], bool(item['all_day']))} 😉")
    else:
        lines.append("Без срока — напоминать не буду. Нажми «Изменить», чтобы добавить дату.")
    if item.get("tags"):
        lines.append(" ".join(f"#{t}" for t in item["tags"].split()))
    lines += [f"🔗 {escape(url)}" for url in links(item.get("source"))[:3]]
    return "\n".join(lines)


def reminder_text(item: dict, now: datetime) -> str:
    if item["kind"] == "birthday":
        moment = when(item, now.date())
        if moment == "сегодня":
            return f"🎂 Сегодня день рождения: <b>{escape(item['title'])}</b>. Не забудь поздравить!"
        return f"🎂 {moment.capitalize()} день рождения: <b>{escape(item['title'])}</b>. Самое время подумать о подарке 🎁"
    if item["kind"] == "payment":
        amount = f", {money(item['amount'])}" if item.get("amount") else ""
        return f"💳 Напоминаю об оплате: <b>{escape(item['title'])}</b> — {when(item, now.date())}{amount}"
    return f"⏰ Напоминаю: <b>{escape(item['title'])}</b> — {when(item, now.date())}"


def morning_text(today_items: list[dict], payments: list[dict], birthdays: list[dict], today: date,
                 quote: str = "") -> str:
    lines = ["☀️ <b>Доброе утро!</b>"]
    if quote:
        lines.append(f"<i>{escape(quote)}</i>")
    if today_items:
        lines.append("\n<b>Сегодня:</b>")
        lines += [item_line(i, today) for i in today_items]
    else:
        lines.append(random.choice(EMPTY_DAY))
    if payments:
        lines.append("\n<b>Оплатить:</b>")
        lines += [item_line(i, today) for i in payments]
    if birthdays:
        lines.append("\n<b>Дни рождения:</b>")
        lines += [item_line(i, today) for i in birthdays]
    if not quote:
        lines.append("\n" + random.choice(MORNING_JOKES))
    return "\n".join(lines)


def evening_text(done: list[dict], not_done: list[dict], tomorrow: list[dict], today: date) -> str:
    lines = ["🌙 <b>Итог дня</b>"]
    lines.append("\n<b>Сделано:</b>")
    lines += [f"✅ {escape(c['title'])}" for c in done] or ["пока ничего не отмечено"]
    if not_done:
        lines.append("\n<b>Не сделано:</b>")
        lines += [f"▫️ {escape(i['title'])}" for i in not_done]
    lines.append("\n<b>Завтра:</b>")
    lines += [item_line(i, today) for i in tomorrow] or ["дел нет 🙌"]
    lines.append("\nСпокойной ночи!")
    return "\n".join(lines)
