"""Понимание сообщений через GigaChat API.

Модуль отделен от остального кода: чтобы перейти на другой ИИ,
достаточно написать класс с теми же методами parse() и answer().
"""

import json
import logging
import re
import time
import uuid
from datetime import date, datetime, timedelta

import httpx

from .config import CATEGORIES
from .timeutil import ALL_DAY_TIME, WEEKDAYS, calendar_hint, default_offsets, roll_forward, to_str

log = logging.getLogger(__name__)

OAUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
CHAT_URL = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"

KINDS = ("task", "payment", "birthday", "note", "shopping")
RECURRENCES = ("none", "daily", "weekly", "monthly", "yearly")
DEFAULT_CATEGORY = {"payment": "Платежи", "shopping": "Покупки", "note": "Идеи", "birthday": "Дом", "task": "Дом"}

PERSONA = (
    "Ты — Марта, личный секретарь владельца в Telegram. Характер: краткая, дружелюбная, "
    "с легким юмором. Обращаешься на «ты», говоришь о себе в женском роде. Отвечаешь по-русски, "
    "коротко, без канцелярита."
)

PARSE_FUNCTION = {
    "name": "process_message",
    "description": "Разобрать сообщение владельца: что он хочет и какие записи создать.",
    "parameters": {
        "type": "object",
        "properties": {
            "intent": {
                "type": "string",
                "enum": ["create", "question", "chat"],
                "description": "create — записать дело, платеж, день рождения, заметку или покупку; "
                "question — вопрос о своих делах и записях; chat — все остальное.",
            },
            "items": {
                "type": "array",
                "description": "Записи для создания. Каждая покупка — отдельной записью.",
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "enum": list(KINDS)},
                        "title": {"type": "string", "description": "Коротко, 2–6 слов, без даты и времени"},
                        "category": {"type": "string", "enum": CATEGORIES},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "date": {"type": "string", "description": "ГГГГ-ММ-ДД или пустая строка"},
                        "time": {"type": "string", "description": "ЧЧ:ММ или пустая строка"},
                        "recurrence": {"type": "string", "enum": list(RECURRENCES)},
                        "amount": {"type": "number", "description": "Сумма платежа в рублях, если названа. Иначе 0"},
                    },
                    "required": ["kind", "title", "category"],
                },
            },
            "clarify": {
                "type": "string",
                "description": "Короткий уточняющий вопрос, если без него запись сделать нельзя. Иначе пустая строка.",
            },
        },
        "required": ["intent"],
    },
}

PARSE_RULES = """
Разбери сообщение владельца и вызови функцию process_message.

Сейчас: {now} ({weekday}), московское время.
Календарь на ближайшие недели:
{calendar}

Правила:
- Виды записей: task — дело или встреча; payment — то, что нужно оплатить (коммуналка, кредит, страховка);
  birthday — день рождения; note — заметка или идея без срока; shopping — то, что нужно купить.
- Категории: {categories}.
  Платежи — то, что нужно оплатить, с напоминанием. Финансы — чеки, расходы, финансовые документы.
  Идеи — заметки и идеи. Покупки — список покупок.
  Рекомендации — только то, что владельцу кто-то посоветовал, или фильм, сериал, книга, музыка, место
  или ссылка, чтобы посмотреть, прочитать или послушать позже. Поздравления, звонки, встречи и дела
  для других людей — не Рекомендации.
- Если в сообщении есть срок — «сегодня», «завтра», «послезавтра», день недели, число — это task
  (или payment, birthday), а не note, и date обязательно заполни. note — только когда срока нет совсем.
- title не должен содержать ссылок: ссылка сохранится вместе с исходным сообщением.
- amount — сумма для payment, если она названа: «4200», «4 200 ₽», «4,2 тыс.» → 4200. Нет суммы — 0.
- date вычисляй по календарю выше: «завтра», «в пятницу», «25 числа» → ГГГГ-ММ-ДД. Нет даты — пустая строка.
- time в 24-часовом формате: «в 3 дня» = 15:00, «в 8 вечера» = 20:00. «Утром» без точного времени — 09:00,
  «днем» — 13:00, «вечером» — 19:00. Нет времени — пустая строка.
- recurrence: «каждый день» — daily; «каждую неделю», «по понедельникам» — weekly;
  «каждый месяц», «ежемесячно» — monthly; «каждый год» и все дни рождения — yearly; иначе none.
- tags — 1–3 коротких слова для поиска, без #.
- clarify заполняй, только если совсем непонятно, что записать. Если не хватает только времени — не спрашивай.
{examples}
""".strip()


class AIError(Exception):
    pass


class GigaChat:
    def __init__(self, auth_key: str, scope: str, model: str, ca_bundle: str):
        self.auth_key = auth_key
        self.scope = scope
        self.model = model
        self.http = httpx.AsyncClient(verify=ca_bundle, timeout=60)
        self._token = ""
        self._token_expires = 0.0

    async def close(self) -> None:
        await self.http.aclose()

    async def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        resp = await self.http.post(
            OAUTH_URL,
            headers={
                "Authorization": f"Basic {self.auth_key}",
                "RqUID": str(uuid.uuid4()),
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"scope": self.scope},
        )
        if resp.status_code != 200:
            raise AIError(f"GigaChat: не удалось получить токен ({resp.status_code}): {resp.text[:200]}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_expires = data["expires_at"] / 1000
        return self._token

    async def _chat(self, messages: list[dict], functions: list[dict] | None = None,
                    function_call: dict | str | None = None, temperature: float = 0.1) -> dict:
        body: dict = {"model": self.model, "messages": messages, "temperature": temperature}
        if functions:
            body["functions"] = functions
            body["function_call"] = function_call or "auto"
        headers = {"Authorization": f"Bearer {await self._access_token()}", "Accept": "application/json"}
        try:
            resp = await self.http.post(CHAT_URL, headers=headers, json=body)
        except httpx.HTTPError as exc:
            raise AIError(f"GigaChat недоступен: {exc}") from exc
        if resp.status_code != 200:
            raise AIError(f"GigaChat ответил {resp.status_code}: {resp.text[:300]}")
        return resp.json()["choices"][0]["message"]

    async def parse(self, text: str, now: datetime, examples: list[dict]) -> dict:
        """Возвращает {'intent', 'items', 'clarify'}; items уже проверены и дополнены."""
        if examples:
            lines = "\n".join(f"- «{e['text']}» → {e['category']}" for e in examples)
            examples_text = "Так владелец сам распределял записи по категориям, учитывай это:\n" + lines
        else:
            examples_text = ""
        system = PARSE_RULES.format(
            now=f"{now:%Y-%m-%d %H:%M}",
            weekday=WEEKDAYS[now.weekday()],
            calendar=calendar_hint(now),
            categories=", ".join(CATEGORIES),
            examples=examples_text,
        )
        message = await self._chat(
            [{"role": "system", "content": system}, {"role": "user", "content": text}],
            functions=[PARSE_FUNCTION],
            function_call={"name": "process_message"},
        )
        raw = extract_arguments(message)
        if raw is None:
            log.warning("GigaChat не вызвал функцию: %s", message)
            return {"intent": "chat", "items": [], "clarify": ""}
        return normalize_parse(raw, now, text)

    async def answer(self, question: str, now: datetime, records: list[dict]) -> str:
        """Ответ на вопрос о делах по списку записей."""
        lines = "\n".join(describe_record(r) for r in records) or "(записей нет)"
        system = (
            f"{PERSONA}\n\nСейчас: {now:%Y-%m-%d %H:%M} ({WEEKDAYS[now.weekday()]}).\n"
            f"Календарь:\n{calendar_hint(now, 14)}\n\n"
            "Записи владельца:\n"
            f"{lines}\n\n"
            "Ответь на вопрос владельца только по этим записям. Если подходящей записи нет — так и скажи. "
            "Даты называй по-человечески: «завтра в 15:00», «в пятницу»."
        )
        message = await self._chat([{"role": "system", "content": system}, {"role": "user", "content": question}])
        return (message.get("content") or "").strip()

    async def quote(self, now: datetime, kind: str) -> str:
        """Короткая фраза для утренней сводки. kind: support, motivation или quote."""
        tasks = {
            "support": "мягкую поддерживающую фразу: что можно не спешить и все получится",
            "motivation": "бодрую мотивирующую фразу, чтобы взяться за дела с удовольствием",
            "quote": "одну настоящую известную цитату о жизни, делах или времени и ее автора через тире",
        }
        prompt = (
            f"Сегодня {WEEKDAYS[now.weekday()]}, {now:%d.%m}. Напиши для утреннего сообщения {tasks[kind]}. "
            "Одно-два коротких предложения, на «ты», без приветствия, без кавычек вокруг всего текста, без эмодзи."
        )
        body = [{"role": "system", "content": PERSONA}, {"role": "user", "content": prompt}]
        message = await self._chat(body, temperature=0.9)
        return (message.get("content") or "").strip().strip("«»\"").strip()

    async def chat(self, text: str) -> str:
        message = await self._chat([{"role": "system", "content": PERSONA}, {"role": "user", "content": text}])
        return (message.get("content") or "").strip()


def extract_arguments(message: dict) -> dict | None:
    call = message.get("function_call")
    if call:
        args = call.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                return None
        return args if isinstance(args, dict) else None
    match = re.search(r"\{.*\}", message.get("content") or "", re.S)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


def _match_category(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip().lower().replace("ё", "е")
    for cat in CATEGORIES:
        if cat.lower() == value:
            return cat
    return None


def _parse_date(value: str | None) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _parse_time(value: str | None) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{1,2})[:.](\d{2})", (value or "").strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    return (hours, minutes) if hours < 24 and minutes < 60 else None


def _parse_amount(value) -> float | None:
    """Сумма платежа: число больше нуля, иначе None."""
    if isinstance(value, str):
        value = value.replace("\u00a0", "").replace(" ", "").replace("₽", "").replace(",", ".")
    try:
        amount = round(float(value), 2)
    except (TypeError, ValueError):
        return None
    return amount if 0 < amount < 100_000_000 else None


RELATIVE_DAYS = ((re.compile(r"\bпослезавтра\b"), 2), (re.compile(r"\bзавтра\b"), 1), (re.compile(r"\bсегодня\b"), 0))
PART_OF_DAY = ((re.compile(r"\bутром\b"), (9, 0)), (re.compile(r"\bдн[её]м\b"), (13, 0)),
               (re.compile(r"\bвечером\b"), (19, 0)))
RECOMMEND_RE = re.compile(r"совет|рекоменд|фильм|сериал|книг|музык|песн|альбом|подкаст|посмотреть|прочитать|"
                          r"послушать|https?://")


def _own_words(source: str) -> str:
    """Слова владельца; при исправлении — только само исправление."""
    return source.rsplit("Исправление:", 1)[-1].lower().replace("ё", "е")


def _relative_day(source: str, now: datetime) -> date | None:
    """Страховка, если ИИ не поставил дату: «сегодня», «завтра», «послезавтра»."""
    text = _own_words(source)
    for pattern, shift in RELATIVE_DAYS:
        if pattern.search(text):
            return now.date() + timedelta(days=shift)
    return None


OTHER_DATE_RE = re.compile(
    r"понедельник|вторник|\bсред[ауы]\b|четверг|пятниц|суббот|воскресень|январ|феврал|\bмарт|апрел|\bма[йя]\b|"
    r"\bиюн|\bиюл|август|сентябр|октябр|ноябр|декабр|числ|\d{1,2}\s*-?го\b|\d{1,2}[./]\d{1,2}|\bчерез\b"
)


def _said_day(source: str, now: datetime) -> date | None:
    """День, который владелец назвал прямо («сегодня», «завтра», «послезавтра»), если другой даты в тексте нет."""
    text = _own_words(source)
    named = [shift for pattern, shift in RELATIVE_DAYS if pattern.search(text)]
    if len(named) != 1 or OTHER_DATE_RE.search(text):
        return None
    return now.date() + timedelta(days=named[0])


def _part_of_day(source: str) -> tuple[int, int] | None:
    text = _own_words(source)
    for pattern, clock in PART_OF_DAY:
        if pattern.search(text):
            return clock
    return None


def normalize_item(raw: dict, now: datetime, source: str) -> dict | None:
    """Проверяет и дополняет одну запись от ИИ. None — запись непригодна."""
    title = str(raw.get("title") or "").strip().rstrip(".")
    if not title:
        return None
    title = title[0].upper() + title[1:]
    kind = raw.get("kind") if raw.get("kind") in KINDS else "task"
    category = _match_category(raw.get("category")) or DEFAULT_CATEGORY[kind]
    if kind == "shopping":
        category = "Покупки"
    if kind == "payment":
        category = "Платежи"
    recurrence = raw.get("recurrence") if raw.get("recurrence") in RECURRENCES else "none"
    if kind == "birthday":
        recurrence = "yearly"
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    tags = [str(t).strip().lstrip("#").lower() for t in tags if str(t).strip()][:3]

    item = {
        "kind": kind,
        "title": title,
        "category": category,
        "tags": " ".join(tags),
        "recurrence": recurrence,
        "due_at": None,
        "all_day": False,
        "offsets": [],
        "source": source,
        "amount": _parse_amount(raw.get("amount")) if kind == "payment" else None,
    }
    day = _parse_date(raw.get("date"))
    said = _said_day(source, now)
    if day is not None and said is not None and day != said and kind != "birthday":
        # GigaChat иногда сдвигает «сегодня» на завтра — слова владельца важнее
        log.info("Дата %s от GigaChat заменена на %s: так сказано в сообщении", day, said)
        day = said
    guessed = False
    if day is None and kind != "shopping":
        day = _relative_day(source, now)
        guessed = day is not None
        if guessed and kind == "note" and RECOMMEND_RE.search(_own_words(source)):
            day, guessed = None, False  # «сегодня посоветовали фильм» — это рекомендация, а не дело
    if kind == "note" and day is not None:
        # у заметки появился срок — это дело с напоминанием, как в приложении
        kind = item["kind"] = "task"
        if guessed and category in ("Рекомендации", "Идеи") and not RECOMMEND_RE.search(_own_words(source)):
            category = item["category"] = DEFAULT_CATEGORY["task"]
    if kind == "shopping" or day is None:
        if kind in ("note", "shopping"):
            item["recurrence"] = "none"
        return item

    clock = _parse_time(raw.get("time"))
    if clock is None and kind != "birthday":
        clock = _part_of_day(source)
    all_day = clock is None or kind == "birthday"
    hours, minutes = ALL_DAY_TIME if all_day else clock
    due = datetime(day.year, day.month, day.day, hours, minutes)
    if recurrence != "none":
        start_of_today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        due = roll_forward(due, recurrence, start_of_today)
    item.update(due_at=to_str(due), all_day=all_day, offsets=default_offsets(kind, all_day))
    return item


def normalize_parse(raw: dict, now: datetime, source: str) -> dict:
    intent = raw.get("intent") if raw.get("intent") in ("create", "question", "chat") else "chat"
    items_raw = raw.get("items") if isinstance(raw.get("items"), list) else []
    items = [i for i in (normalize_item(r, now, source) for r in items_raw if isinstance(r, dict)) if i]
    clarify = str(raw.get("clarify") or "").strip()
    if items and intent != "question":
        intent = "create"
    return {"intent": intent, "items": items, "clarify": clarify}


def describe_record(r: dict) -> str:
    kinds = {"task": "дело", "payment": "платеж", "birthday": "день рождения", "note": "заметка", "shopping": "купить"}
    parts = [f"- [{r['category']}] {kinds.get(r['kind'], r['kind'])}: {r['title']}"]
    if r.get("due_at"):
        due = datetime.strptime(r["due_at"], "%Y-%m-%d %H:%M")
        when = f"{due:%Y-%m-%d}" if r["all_day"] else f"{due:%Y-%m-%d %H:%M}"
        parts.append(f"— {when} ({WEEKDAYS[due.weekday()]})")
    if r.get("recurrence", "none") != "none":
        parts.append(f"повтор: {r['recurrence']}")
    if r.get("amount"):
        parts.append(f"сумма: {r['amount']:g} ₽")
    return " ".join(parts)


def context_window(now: datetime) -> tuple[datetime, datetime]:
    return now - timedelta(days=7), now + timedelta(days=120)
