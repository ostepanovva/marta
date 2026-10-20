"""Обработчики сообщений и кнопок Telegram."""

import logging
import os
import tempfile
from html import escape

from aiogram import BaseMiddleware, Bot, F, Router
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from . import keyboards as kb
from .ai import AIError
from .config import CATEGORIES
from .service import Marta
from .views import (
    MENU_CATEGORIES,
    MENU_IDEAS,
    MENU_SHOPPING,
    MENU_TODAY,
    card_text,
    item_line,
)

log = logging.getLogger(__name__)
router = Router()

WELCOME = (
    "Привет! Я Марта, твой секретарь 👋\n\n"
    "Пиши или надиктовывай голосом всё, что нужно не забыть:\n"
    "• «в пятницу к врачу в 15:00»\n"
    "• «каждый месяц до 25 числа оплатить коммуналку»\n"
    "• «12 марта день рождения мамы»\n"
    "• «купить молоко и хлеб»\n"
    "• «во сколько у меня завтра врач?»\n\n"
    "Я разложу всё по категориям и напомню вовремя. "
    "Утром в 9:00 пришлю план на день, вечером в 23:00 — итог."
)

HELP = (
    "Что я умею:\n"
    "• записываю дела, платежи, дни рождения, заметки и покупки — текстом или голосом;\n"
    "• напоминаю о делах за день и за час, о платежах — за день и в день оплаты, "
    "о днях рождения — за 3 дня и в сам день;\n"
    "• отвечаю на вопросы о твоих планах;\n"
    "• утром присылаю план, вечером — итог дня.\n\n"
    "Команды:\n"
    "/confirm — включить или выключить подтверждение новых записей\n"
    "/help — эта подсказка"
)

AI_FAILED = "Ой, не получилось связаться с GigaChat 😕 Попробуй еще раз через минуту."


class Editing(StatesGroup):
    waiting = State()


class OwnerOnly(BaseMiddleware):
    """Марта отвечает только владельцу, остальных молча игнорирует."""

    def __init__(self, owner_id: int):
        self.owner_id = owner_id

    async def __call__(self, handler, event, data):
        user = data.get("event_from_user")
        if user is None or user.id != self.owner_id:
            return None
        return await handler(event, data)


# --- команды и меню ---


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(WELCOME, reply_markup=kb.main_menu())


@router.message(Command("help"))
async def cmd_help(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(HELP, reply_markup=kb.main_menu())


@router.message(Command("confirm"))
async def cmd_confirm(message: Message, state: FSMContext, marta: Marta):
    await state.clear()
    enabled = not await marta.confirm_enabled()
    await marta.db.set_setting("confirm", "1" if enabled else "0")
    if enabled:
        await message.answer("Буду спрашивать подтверждение перед сохранением ✅")
    else:
        await message.answer("Буду сохранять сразу, без подтверждения. Если ошибусь — поправишь кнопками 😉")


@router.message(F.text == MENU_TODAY)
async def menu_today(message: Message, state: FSMContext, marta: Marta):
    await state.clear()
    items = await marta.today()
    if not items:
        await message.answer("На сегодня дел нет 🙌")
        return
    today = marta.now().date()
    lines = ["📅 <b>Сегодня:</b>"] + [item_line(i, today) for i in items]
    lines.append("\nНажми на дело, когда сделаешь.")
    await message.answer("\n".join(lines), reply_markup=kb.items_keyboard(items, with_done=True))


@router.message(F.text == MENU_CATEGORIES)
async def menu_categories(message: Message, state: FSMContext, marta: Marta):
    await state.clear()
    counts = await marta.db.category_counts()
    await message.answer("🗂 Выбери категорию:", reply_markup=kb.browse_keyboard(counts))


@router.message(F.text == MENU_SHOPPING)
async def menu_shopping(message: Message, state: FSMContext, marta: Marta):
    await state.clear()
    items = await marta.db.shopping()
    if not items:
        await message.answer("🛒 Список покупок пуст.")
        return
    await message.answer("🛒 <b>Покупки</b>\nНажми, когда купишь:", reply_markup=kb.shopping_keyboard(items))


@router.message(F.text == MENU_IDEAS)
async def menu_ideas(message: Message, state: FSMContext, marta: Marta):
    await state.clear()
    await send_category(message, marta, "Идеи")


async def send_category(message: Message, marta: Marta, category: str) -> None:
    items = await marta.db.by_category(category)
    if not items:
        await message.answer(f"В категории «{category}» пока пусто.")
        return
    today = marta.now().date()
    lines = [f"<b>{category}</b>"] + [item_line(i, today) for i in items]
    await message.answer("\n".join(lines), reply_markup=kb.items_keyboard(items, with_done=False))


# --- входящие сообщения ---


async def message_text(message: Message, bot: Bot, marta: Marta) -> str | None:
    """Текст сообщения; голосовое сначала расшифровывается на сервере."""
    if message.text:
        return message.text.strip()
    if not message.voice:
        return None
    await bot.send_chat_action(message.chat.id, "typing")
    fd, path = tempfile.mkstemp(suffix=".ogg")
    os.close(fd)
    try:
        await bot.download(message.voice, destination=path)
        text = await marta.transcriber.transcribe(path)
    finally:
        os.remove(path)  # аудио не храним
    if not text:
        await message.answer("Не расслышала 🙉 Попробуй еще раз.")
        return None
    await message.answer(f"🎙 <i>{escape(text)}</i>")
    return text


@router.message(StateFilter(Editing.waiting), F.text | F.voice)
async def edit_input(message: Message, state: FSMContext, bot: Bot, marta: Marta):
    data = await state.get_data()
    await state.clear()
    item = await marta.db.get_item(data.get("item_id", 0))
    text = await message_text(message, bot, marta)
    if not item or not text:
        return
    combined = f"{item['source'] or item['title']}\nИсправление: {text}"
    try:
        parsed = await marta.parse(combined)
    except AIError:
        log.exception("Ошибка GigaChat при исправлении")
        await message.answer(AI_FAILED)
        return
    if not parsed["items"]:
        await message.answer("Не поняла исправление 🤔 Нажми «Изменить» и попробуй сказать иначе.")
        return
    new = parsed["items"][0]
    fields = {k: new[k] for k in ("kind", "title", "category", "tags", "due_at", "all_day", "recurrence", "offsets")}
    await marta.db.update_item(item["id"], source=combined, **fields)
    item = await marta.db.get_item(item["id"])
    await show_card(message, marta, item)


@router.message(F.photo | F.document)
async def files_later(message: Message):
    await message.answer("Фото и документы я научусь разбирать в следующей версии 📎 Пока пиши или надиктовывай.")


@router.message(F.text | F.voice)
async def incoming(message: Message, state: FSMContext, bot: Bot, marta: Marta):
    data = await state.get_data()
    await state.clear()
    text = await message_text(message, bot, marta)
    if not text:
        return
    if data.get("pending"):  # ответ на уточняющий вопрос
        text = f"{data['pending']}\n{text}"
    await bot.send_chat_action(message.chat.id, "typing")
    try:
        await handle_text(message, state, marta, text)
    except AIError:
        log.exception("Ошибка GigaChat")
        await message.answer(AI_FAILED)


async def handle_text(message: Message, state: FSMContext, marta: Marta, text: str) -> None:
    parsed = await marta.parse(text)
    if parsed["intent"] == "question":
        await message.answer(await marta.answer_question(text) or "Хм, не нашла ответа 🤔")
        return
    if parsed["items"]:
        now = marta.now()
        shopping = [i for i in parsed["items"] if i["kind"] == "shopping"]
        others = [i for i in parsed["items"] if i["kind"] != "shopping"]
        if shopping:
            for item in shopping:
                await marta.db.add_item({**item, "status": "active"}, now)
            names = ", ".join(escape(i["title"].lower()) for i in shopping)
            await message.answer(f"🛒 Добавила в покупки: {names}")
        confirm = await marta.confirm_enabled()
        for item in others:
            item_id = await marta.db.add_item({**item, "status": "draft" if confirm else "active"}, now)
            await show_card(message, marta, await marta.db.get_item(item_id))
        return
    if parsed["clarify"]:
        await state.update_data(pending=text)
        await message.answer(parsed["clarify"])
        return
    await message.answer(await marta.ai.chat(text) or "Я тут 😊")


async def show_card(message: Message, marta: Marta, item: dict) -> None:
    text = card_text(item, marta.now().date())
    if item["status"] == "draft":
        await message.answer(text, reply_markup=kb.draft_keyboard(item["id"]))
    else:
        await message.answer(text + "\n\nЗаписала ✅", reply_markup=kb.saved_keyboard(item["id"]))


# --- кнопки ---


async def load_item(cb: CallbackQuery, marta: Marta, item_id: int) -> dict | None:
    item = await marta.db.get_item(item_id)
    if not item:
        await cb.answer("Этой записи уже нет", show_alert=False)
    return item


@router.callback_query(kb.ItemCb.filter(F.action == "save"))
async def cb_save(cb: CallbackQuery, callback_data: kb.ItemCb, marta: Marta):
    item = await load_item(cb, marta, callback_data.id)
    if not item:
        return
    await marta.db.update_item(item["id"], status="active")
    item["status"] = "active"
    await cb.message.edit_text(
        card_text(item, marta.now().date()) + "\n\nЗаписала ✅", reply_markup=kb.saved_keyboard(item["id"])
    )
    await cb.answer("Записала")


@router.callback_query(kb.ItemCb.filter(F.action == "edit"))
async def cb_edit(cb: CallbackQuery, callback_data: kb.ItemCb, state: FSMContext, marta: Marta):
    item = await load_item(cb, marta, callback_data.id)
    if not item:
        return
    await state.set_state(Editing.waiting)
    await state.update_data(item_id=item["id"])
    await cb.message.answer(
        "Напиши или надиктуй, что поправить. Например: «не в пятницу, а в субботу в 12» "
        "или «каждый месяц»."
    )
    await cb.answer()


@router.callback_query(kb.ItemCb.filter(F.action == "cat"))
async def cb_category_menu(cb: CallbackQuery, callback_data: kb.ItemCb, marta: Marta):
    if not await load_item(cb, marta, callback_data.id):
        return
    await cb.message.edit_reply_markup(reply_markup=kb.categories_keyboard(callback_data.id))
    await cb.answer("Выбери категорию")


@router.callback_query(kb.SetCatCb.filter())
async def cb_set_category(cb: CallbackQuery, callback_data: kb.SetCatCb, marta: Marta):
    item = await load_item(cb, marta, callback_data.id)
    if not item:
        return
    category = CATEGORIES[callback_data.idx]
    await marta.db.update_item(item["id"], category=category)
    await marta.db.add_example(item["title"], category, marta.now())  # Марта учится на исправлениях
    item["category"] = category
    text = card_text(item, marta.now().date())
    if item["status"] == "draft":
        await cb.message.edit_text(text, reply_markup=kb.draft_keyboard(item["id"]))
    else:
        await cb.message.edit_text(text + "\n\nЗаписала ✅", reply_markup=kb.saved_keyboard(item["id"]))
    await cb.answer(f"Теперь это «{category}». Запомню!")


@router.callback_query(kb.ItemCb.filter(F.action == "cancel"))
async def cb_cancel(cb: CallbackQuery, callback_data: kb.ItemCb, marta: Marta):
    await marta.db.delete_item(callback_data.id)
    await cb.message.edit_text("Ок, не записываю 👌")
    await cb.answer()


@router.callback_query(kb.ItemCb.filter(F.action == "del"))
async def cb_delete(cb: CallbackQuery, callback_data: kb.ItemCb, marta: Marta):
    item = await load_item(cb, marta, callback_data.id)
    if not item:
        return
    await marta.db.delete_item(item["id"])
    await cb.message.edit_text(f"🗑 Удалила: {escape(item['title'])}")
    await cb.answer()


@router.callback_query(kb.ItemCb.filter(F.action == "done"))
async def cb_done(cb: CallbackQuery, callback_data: kb.ItemCb, marta: Marta):
    item = await load_item(cb, marta, callback_data.id)
    if not item:
        return
    await marta.mark_done(item)
    await cb.message.edit_text(f"{cb.message.html_text}\n\n✅ Сделано!")
    await cb.answer("Молодец!")


@router.callback_query(kb.ItemCb.filter(F.action == "ldone"))
async def cb_list_done(cb: CallbackQuery, callback_data: kb.ItemCb, marta: Marta):
    item = await load_item(cb, marta, callback_data.id)
    if item:
        await marta.mark_done(item)
        await cb.answer(f"✅ {item['title']}")
    items = await marta.today()
    if not items:
        await cb.message.edit_text("📅 На сегодня всё сделано 🎉")
        return
    today = marta.now().date()
    lines = ["📅 <b>Сегодня:</b>"] + [item_line(i, today) for i in items]
    lines.append("\nНажми на дело, когда сделаешь.")
    await cb.message.edit_text("\n".join(lines), reply_markup=kb.items_keyboard(items, with_done=True))


@router.callback_query(kb.ItemCb.filter(F.action == "open"))
async def cb_open(cb: CallbackQuery, callback_data: kb.ItemCb, marta: Marta):
    item = await load_item(cb, marta, callback_data.id)
    if not item:
        return
    await cb.message.answer(card_text(item, marta.now().date()), reply_markup=kb.item_keyboard(item))
    await cb.answer()


@router.callback_query(kb.BrowseCb.filter())
async def cb_browse(cb: CallbackQuery, callback_data: kb.BrowseCb, marta: Marta):
    await send_category(cb.message, marta, CATEGORIES[callback_data.idx])
    await cb.answer()


@router.callback_query(kb.ShopCb.filter())
async def cb_bought(cb: CallbackQuery, callback_data: kb.ShopCb, marta: Marta):
    await marta.db.delete_item(callback_data.id)
    items = await marta.db.shopping()
    if items:
        await cb.message.edit_reply_markup(reply_markup=kb.shopping_keyboard(items))
    else:
        await cb.message.edit_text("🛒 Всё куплено 🎉")
    await cb.answer("Куплено")
