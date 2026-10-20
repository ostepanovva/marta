"""Даты, повторы и интервалы напоминаний.

Все даты хранятся как местное (московское) время в виде строки
'ГГГГ-ММ-ДД ЧЧ:ММ'. Пользователь один, часовой пояс один — так проще.
"""

import calendar
from datetime import date, datetime, timedelta

DT_FORMAT = "%Y-%m-%d %H:%M"
ALL_DAY_TIME = (9, 0)  # дела без времени напоминаются в 9:00

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]

RECURRENCE_LABELS = {
    "daily": "каждый день",
    "weekly": "каждую неделю",
    "monthly": "каждый месяц",
    "yearly": "каждый год",
}

DAY = 24 * 60


def to_str(dt: datetime) -> str:
    return dt.strftime(DT_FORMAT)


def from_str(value: str) -> datetime:
    return datetime.strptime(value, DT_FORMAT)


def default_offsets(kind: str, all_day: bool) -> list[int]:
    """За сколько минут до события напоминать."""
    if kind == "birthday":
        return [3 * DAY, 0]
    if kind == "payment":
        return [DAY, 0]
    if kind == "task":
        return [DAY] if all_day else [DAY, 60]
    return []


def offsets_label(offsets: list[int], all_day: bool) -> str:
    parts = []
    for off in sorted(offsets, reverse=True):
        if off == 0:
            parts.append("в сам день в 9:00" if all_day else "в момент события")
        elif off % DAY == 0:
            days = off // DAY
            parts.append("за день" if days == 1 else f"за {days} дн.")
        elif off % 60 == 0:
            hours = off // 60
            parts.append("за час" if hours == 1 else f"за {hours} ч.")
        else:
            parts.append(f"за {off} мин.")
    return " и ".join(parts)


def add_months(dt: datetime, months: int) -> datetime:
    month_index = dt.month - 1 + months
    year = dt.year + month_index // 12
    month = month_index % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def next_occurrence(dt: datetime, recurrence: str) -> datetime:
    if recurrence == "daily":
        return dt + timedelta(days=1)
    if recurrence == "weekly":
        return dt + timedelta(weeks=1)
    if recurrence == "monthly":
        return add_months(dt, 1)
    if recurrence == "yearly":
        return add_months(dt, 12)
    raise ValueError(f"Неизвестный повтор: {recurrence}")


def roll_forward(dt: datetime, recurrence: str, not_before: datetime) -> datetime:
    """Сдвигает повторяющееся событие вперед, пока оно не окажется не раньше not_before."""
    if recurrence == "none":
        return dt
    while dt < not_before:
        dt = next_occurrence(dt, recurrence)
    return dt


def human_when(dt: datetime, all_day: bool, today: date) -> str:
    """'сегодня в 15:00', 'завтра', 'пт, 24 окт в 15:00'."""
    delta = (dt.date() - today).days
    if delta == 0:
        day = "сегодня"
    elif delta == 1:
        day = "завтра"
    elif delta == 2:
        day = "послезавтра"
    else:
        day = f"{WEEKDAYS_SHORT[dt.weekday()]}, {dt.day} {MONTHS_SHORT[dt.month - 1]}"
        if dt.year != today.year:
            day += f" {dt.year}"
    return day if all_day else f"{day} в {dt:%H:%M}"


def calendar_hint(now: datetime, days: int = 21) -> str:
    """Подсказка для ИИ: даты и дни недели на ближайшие недели."""
    lines = []
    for i in range(days):
        d = now.date() + timedelta(days=i)
        mark = " (сегодня)" if i == 0 else " (завтра)" if i == 1 else ""
        lines.append(f"{d.isoformat()} — {WEEKDAYS[d.weekday()]}{mark}")
    return "\n".join(lines)
