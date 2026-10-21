"""Настройки Марты. Все значения берутся из файла .env на сервере."""

import os
from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

CATEGORIES = [
    "Работа",
    "Дом",
    "Здоровье",
    "Финансы",
    "Рекомендации",
    "Покупки",
    "Учеба",
    "Идеи",
    "Платежи",
    "Дача",
]


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise SystemExit(f"В файле .env не заполнено {name}")
    return value


def _time(name: str, default: str) -> time:
    hours, minutes = os.getenv(name, default).strip().split(":")
    return time(int(hours), int(minutes))


@dataclass(frozen=True)
class Settings:
    bot_token: str
    owner_id: int
    gigachat_auth_key: str
    gigachat_scope: str
    gigachat_model: str
    gigachat_ca_bundle: str
    whisper_model: str
    whisper_cache: str
    db_path: str
    tz: ZoneInfo
    morning_at: time
    evening_at: time
    keep_days: int
    webapp_enabled: bool = False
    webapp_url: str = ""
    webapp_port: int = 8080


def load_settings() -> Settings:
    return Settings(
        bot_token=_required("TELEGRAM_BOT_TOKEN"),
        owner_id=int(_required("OWNER_ID")),
        gigachat_auth_key=_required("GIGACHAT_AUTH_KEY"),
        gigachat_scope=os.getenv("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
        gigachat_model=os.getenv("GIGACHAT_MODEL", "GigaChat-2"),
        gigachat_ca_bundle=os.getenv("GIGACHAT_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt"),
        whisper_model=os.getenv("WHISPER_MODEL", "small"),
        whisper_cache=os.getenv("WHISPER_CACHE", "/models"),
        db_path=os.getenv("DB_PATH", "/data/marta.db"),
        tz=ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow")),
        morning_at=_time("MORNING_AT", "09:00"),
        evening_at=_time("EVENING_AT", "23:00"),
        keep_days=int(os.getenv("KEEP_DAYS", "30")),
        webapp_enabled=os.getenv("WEBAPP_ENABLED", "0").strip() == "1",
        webapp_url=os.getenv("WEBAPP_URL", "").strip().rstrip("/"),
        webapp_port=int(os.getenv("WEBAPP_PORT", "8080")),
    )
