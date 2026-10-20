"""Хранилище Марты: один файл SQLite."""

import json
from datetime import datetime, timedelta

import aiosqlite

from .timeutil import to_str

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,              -- task, payment, birthday, note, shopping
    title       TEXT NOT NULL,
    category    TEXT NOT NULL,
    tags        TEXT NOT NULL DEFAULT '',
    due_at      TEXT,                       -- 'ГГГГ-ММ-ДД ЧЧ:ММ', московское время
    all_day     INTEGER NOT NULL DEFAULT 0,
    recurrence  TEXT NOT NULL DEFAULT 'none',
    offsets     TEXT NOT NULL DEFAULT '[]', -- минуты до события, JSON
    status      TEXT NOT NULL DEFAULT 'draft', -- draft, active, done
    source      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    done_at     TEXT
);
CREATE INDEX IF NOT EXISTS items_due ON items(status, due_at);

CREATE TABLE IF NOT EXISTS sent_reminders (
    item_id    INTEGER NOT NULL,
    due_at     TEXT NOT NULL,
    offset_min INTEGER NOT NULL,
    sent_at    TEXT NOT NULL,
    PRIMARY KEY (item_id, due_at, offset_min)
);

CREATE TABLE IF NOT EXISTS completions (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  INTEGER NOT NULL,
    title    TEXT NOT NULL,
    done_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category_examples (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    text       TEXT NOT NULL,
    category   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

FIELDS = ("kind", "title", "category", "tags", "due_at", "all_day", "recurrence", "offsets", "status", "source")


def _row(row: aiosqlite.Row | None) -> dict | None:
    if row is None:
        return None
    item = dict(row)
    if "offsets" in item:
        item["offsets"] = json.loads(item["offsets"])
    return item


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()

    async def _all(self, sql: str, *args) -> list[dict]:
        async with self.conn.execute(sql, args) as cur:
            return [_row(r) for r in await cur.fetchall()]

    # --- записи ---

    async def add_item(self, item: dict, now: datetime) -> int:
        values = {k: item.get(k) for k in FIELDS}
        values["offsets"] = json.dumps(item.get("offsets", []))
        values["all_day"] = int(bool(item.get("all_day")))
        values["tags"] = item.get("tags") or ""
        values["recurrence"] = item.get("recurrence") or "none"
        values["status"] = item.get("status") or "draft"
        values["source"] = item.get("source") or ""
        cols = ", ".join(values) + ", created_at"
        marks = ", ".join("?" for _ in values) + ", ?"
        cur = await self.conn.execute(
            f"INSERT INTO items ({cols}) VALUES ({marks})", (*values.values(), to_str(now))
        )
        await self.conn.commit()
        return cur.lastrowid

    async def get_item(self, item_id: int) -> dict | None:
        async with self.conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)) as cur:
            return _row(await cur.fetchone())

    async def update_item(self, item_id: int, **fields) -> None:
        if "offsets" in fields:
            fields["offsets"] = json.dumps(fields["offsets"])
        if "all_day" in fields:
            fields["all_day"] = int(bool(fields["all_day"]))
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(f"UPDATE items SET {sets} WHERE id = ?", (*fields.values(), item_id))
        await self.conn.commit()

    async def delete_item(self, item_id: int) -> None:
        await self.conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        await self.conn.execute("DELETE FROM sent_reminders WHERE item_id = ?", (item_id,))
        await self.conn.commit()

    async def active_with_due(self) -> list[dict]:
        return await self._all(
            "SELECT * FROM items WHERE status = 'active' AND due_at IS NOT NULL ORDER BY due_at"
        )

    async def due_between(self, start: datetime, end: datetime, kinds: tuple[str, ...]) -> list[dict]:
        marks = ", ".join("?" for _ in kinds)
        return await self._all(
            f"SELECT * FROM items WHERE status = 'active' AND due_at >= ? AND due_at < ? "
            f"AND kind IN ({marks}) ORDER BY due_at",
            to_str(start), to_str(end), *kinds,
        )

    async def by_category(self, category: str, limit: int = 30) -> list[dict]:
        return await self._all(
            "SELECT * FROM items WHERE status = 'active' AND category = ? AND kind != 'shopping' "
            "ORDER BY due_at IS NULL, due_at, id DESC LIMIT ?",
            category, limit,
        )

    async def category_counts(self) -> dict[str, int]:
        rows = await self._all(
            "SELECT category, COUNT(*) AS n FROM items WHERE status = 'active' AND kind != 'shopping' "
            "GROUP BY category"
        )
        return {r["category"]: r["n"] for r in rows}

    async def shopping(self) -> list[dict]:
        return await self._all("SELECT * FROM items WHERE status = 'active' AND kind = 'shopping' ORDER BY id")

    async def context_items(self, start: datetime, end: datetime) -> list[dict]:
        """Записи, которые ИИ видит, когда отвечает на вопросы о планах."""
        return await self._all(
            "SELECT * FROM items WHERE status = 'active' AND ("
            "  (due_at >= ? AND due_at < ?) OR kind IN ('birthday', 'note', 'shopping') OR due_at IS NULL"
            ") ORDER BY due_at IS NULL, due_at LIMIT 300",
            to_str(start), to_str(end),
        )

    # --- выполнение ---

    async def add_completion(self, item: dict, now: datetime) -> None:
        await self.conn.execute(
            "INSERT INTO completions (item_id, title, done_at) VALUES (?, ?, ?)",
            (item["id"], item["title"], to_str(now)),
        )
        await self.conn.commit()

    async def completions_between(self, start: datetime, end: datetime) -> list[dict]:
        return await self._all(
            "SELECT * FROM completions WHERE done_at >= ? AND done_at < ? ORDER BY done_at",
            to_str(start), to_str(end),
        )

    # --- напоминания ---

    async def mark_sent(self, item_id: int, due_at: str, offset_min: int, now: datetime) -> bool:
        """Отмечает напоминание отправленным. False, если оно уже было отправлено."""
        cur = await self.conn.execute(
            "INSERT OR IGNORE INTO sent_reminders (item_id, due_at, offset_min, sent_at) VALUES (?, ?, ?, ?)",
            (item_id, due_at, offset_min, to_str(now)),
        )
        await self.conn.commit()
        return cur.rowcount == 1

    # --- обучение категориям ---

    async def add_example(self, text: str, category: str, now: datetime) -> None:
        await self.conn.execute(
            "INSERT INTO category_examples (text, category, created_at) VALUES (?, ?, ?)",
            (text, category, to_str(now)),
        )
        await self.conn.commit()

    async def examples(self, limit: int = 30) -> list[dict]:
        return await self._all(
            "SELECT text, category FROM category_examples ORDER BY id DESC LIMIT ?", limit
        )

    # --- настройки ---

    async def get_setting(self, key: str, default: str) -> str:
        async with self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return row["value"] if row else default

    async def set_setting(self, key: str, value: str) -> None:
        await self.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await self.conn.commit()

    # --- ночное обслуживание ---

    async def cleanup(self, now: datetime, keep_days: int) -> None:
        """Удаляет выполненное и прошедшее старше keep_days дней и забытые черновики."""
        edge = to_str(now - timedelta(days=keep_days))
        await self.conn.execute("DELETE FROM items WHERE status = 'done' AND done_at < ?", (edge,))
        await self.conn.execute(
            "DELETE FROM items WHERE status = 'active' AND recurrence = 'none' "
            "AND kind IN ('task', 'payment') AND due_at < ?",
            (edge,),
        )
        await self.conn.execute(
            "DELETE FROM items WHERE status = 'draft' AND created_at < ?",
            (to_str(now - timedelta(days=2)),),
        )
        await self.conn.execute("DELETE FROM completions WHERE done_at < ?", (edge,))
        await self.conn.execute("DELETE FROM sent_reminders WHERE sent_at < ?", (edge,))
        await self.conn.execute("DELETE FROM sent_reminders WHERE item_id NOT IN (SELECT id FROM items)")
        await self.conn.commit()
