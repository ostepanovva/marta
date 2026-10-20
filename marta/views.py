"""Тексты сообщений Марты: карточки, сводки, напоминания."""

import random
from datetime import date, datetime
from html import escape

from .timeutil import RECURRENCE_LABELS, from_str, human_when, offsets_label

ICONS = {
    "Работа": "💼",
    "Дом": "🏠",
    "Здоровье": "🩺",
    "Финансы": "💰",
    "Дети": "🧸",
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
    return text


def card_text(item: dict, today: date) -> str:
    lines = [f"{icon(item)} <b>{escape(item['title'])}</b>", f"{item['category']}"]
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
    return "\n".join(lines)


def reminder_text(item: dict, now: datetime) -> str:
    if item["kind"] == "birthday":
        moment = when(item, now.date())
        if moment == "сегодня":
            return f"🎂 Сегодня день рождения: <b>{escape(item['title'])}</b>. Не забудь поздравить!"
        return f"🎂 {moment.capitalize()} день рождения: <b>{escape(item['title'])}</b>. Самое время подумать о подарке 🎁"
    if item["kind"] == "payment":
        return f"💳 Напоминаю об оплате: <b>{escape(item['title'])}</b> — {when(item, now.date())}"
    return f"⏰ Напоминаю: <b>{escape(item['title'])}</b> — {when(item, now.date())}"


def morning_text(today_items: list[dict], payments: list[dict], birthdays: list[dict], today: date) -> str:
    lines = ["☀️ <b>Доброе утро!</b>"]
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
