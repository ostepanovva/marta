"""Кнопки Марты."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup, WebAppInfo
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .config import CATEGORIES
from .views import ICONS, MENU_CATEGORIES, MENU_IDEAS, MENU_SHOPPING, MENU_TODAY


class ItemCb(CallbackData, prefix="it"):
    action: str  # save, edit, cat, cancel, done, ldone (из списка «Сегодня»), del, open
    id: int


class SetCatCb(CallbackData, prefix="sc"):
    id: int
    idx: int


class BrowseCb(CallbackData, prefix="br"):
    idx: int


class ShopCb(CallbackData, prefix="sh"):
    id: int


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=MENU_TODAY), KeyboardButton(text=MENU_CATEGORIES)],
            [KeyboardButton(text=MENU_SHOPPING), KeyboardButton(text=MENU_IDEAS)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Напиши или надиктуй, что запомнить",
    )


def draft_keyboard(item_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Сохранить", callback_data=ItemCb(action="save", id=item_id))
    kb.button(text="✏️ Изменить", callback_data=ItemCb(action="edit", id=item_id))
    kb.button(text="🗂 Другая категория", callback_data=ItemCb(action="cat", id=item_id))
    kb.button(text="✖️ Не надо", callback_data=ItemCb(action="cancel", id=item_id))
    kb.adjust(2, 2)
    return kb.as_markup()


def saved_keyboard(item_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✏️ Изменить", callback_data=ItemCb(action="edit", id=item_id))
    kb.button(text="🗂 Категория", callback_data=ItemCb(action="cat", id=item_id))
    kb.button(text="🗑 Удалить", callback_data=ItemCb(action="del", id=item_id))
    kb.adjust(3)
    return kb.as_markup()


def categories_keyboard(item_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for idx, cat in enumerate(CATEGORIES):
        kb.button(text=f"{ICONS[cat]} {cat}", callback_data=SetCatCb(id=item_id, idx=idx))
    kb.adjust(2)
    return kb.as_markup()


def browse_keyboard(counts: dict[str, int]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for idx, cat in enumerate(CATEGORIES):
        kb.button(text=f"{ICONS[cat]} {cat} ({counts.get(cat, 0)})", callback_data=BrowseCb(idx=idx))
    kb.adjust(2)
    return kb.as_markup()


def items_keyboard(items: list[dict], with_done: bool) -> InlineKeyboardMarkup:
    """Кнопка на каждую запись: «Готово» для дел, «Открыть» для остального."""
    kb = InlineKeyboardBuilder()
    for item in items:
        title = item["title"] if len(item["title"]) <= 40 else item["title"][:39] + "…"
        if with_done and item["kind"] in ("task", "payment"):
            kb.button(text=f"✅ {title}", callback_data=ItemCb(action="ldone", id=item["id"]))
        else:
            kb.button(text=title, callback_data=ItemCb(action="open", id=item["id"]))
    kb.adjust(1)
    return kb.as_markup()


def item_keyboard(item: dict) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if item["kind"] in ("task", "payment"):
        kb.button(text="✅ Готово", callback_data=ItemCb(action="done", id=item["id"]))
    kb.button(text="✏️ Изменить", callback_data=ItemCb(action="edit", id=item["id"]))
    kb.button(text="🗑 Удалить", callback_data=ItemCb(action="del", id=item["id"]))
    kb.adjust(3)
    return kb.as_markup()


def done_keyboard(item_id: int) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Готово", callback_data=ItemCb(action="done", id=item_id))
    return kb.as_markup()


def shopping_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for item in items:
        kb.button(text=f"☐ {item['title']}", callback_data=ShopCb(id=item["id"]))
    kb.adjust(1)
    return kb.as_markup()


def webapp_keyboard(url: str) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📱 Открыть Марту", web_app=WebAppInfo(url=url))
    return kb.as_markup()
