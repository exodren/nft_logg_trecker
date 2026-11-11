"""Centralised settings from .env. Secrets are never logged; use Settings.secrets() for masking."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

ROOT_DIR = Path(__file__).resolve().parent.parent
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# Which settings each component needs. The owner provider is optional: without a token it reports UNAVAILABLE.
REQUIRED = {
    "mtproto": ("API_ID", "API_HASH"),
    "bot": ("BOT_TOKEN",),
    "kartoshka": ("KARTOSHKA_API_TOKEN",),
}


class ConfigError(ValueError):
    """Readable configuration problem; printed to the user instead of a traceback."""


@dataclass(frozen=True)
class Settings:
    bot_token: str | None = None
    api_id: int | None = None
    api_hash: str | None = None
    telegram_session: Path = ROOT_DIR / "sessions" / "app.session"
    kartoshka_api_token: str | None = None
    database_url: str = f"sqlite+aiosqlite:///{(ROOT_DIR / 'data' / 'tracker.db').as_posix()}"
    admin_telegram_ids: frozenset[int] = field(default_factory=frozenset)
    log_level: str = "INFO"
    max_concurrent_api_requests: int = 2
    # 0 = only the free daily quota is used; paid requests need an explicit budget.
    daily_api_budget_ton: Decimal = Decimal("0")
    api_cache_ttl: int = 86_400
    listing_scan_interval: int = 300
    # Raw value; validated by the Telegram Log Sender (a bad value disables it, never the app).
    log_group_id: str | None = None
    owner_log_group_id: int | None = None  # group for owner cards with the 'claim' button

    def secrets(self) -> list[str]:
        return [s for s in (self.bot_token, self.api_hash, self.kartoshka_api_token) if s]

    def missing_for(self, component: str) -> list[str]:
        values = {"API_ID": self.api_id, "API_HASH": self.api_hash, "BOT_TOKEN": self.bot_token,
                  "KARTOSHKA_API_TOKEN": self.kartoshka_api_token}
        return [name for name in REQUIRED[component] if not values[name]]

    def require(self, component: str) -> None:
        missing = self.missing_for(component)
        if missing:
            raise ConfigError(f"{component}: не заданы {', '.join(missing)} в .env (см. .env.example)")

    @property
    def owner_provider_enabled(self) -> bool:
        return bool(self.kartoshka_api_token)


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name) or str(default)
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} должно быть целым числом") from None
    if value <= 0:
        raise ConfigError(f"{name} должно быть больше нуля")
    return value


def _admin_ids(raw: str) -> frozenset[int]:
    try:
        return frozenset(int(x) for x in raw.replace(" ", "").split(",") if x)
    except ValueError:
        raise ConfigError("ADMIN_TELEGRAM_IDS: числовые Telegram ID через запятую") from None


def _group_id(raw: str | None) -> int | None:
    if raw is None:
        return None
    if not re.fullmatch(r"-\d{1,20}", raw):
        raise ConfigError("OWNER_LOG_GROUP_ID: id группы, отрицательное число вида -100...")
    return int(raw)


def _budget(raw: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ConfigError("DAILY_API_BUDGET_TON должно быть числом") from None
    if value < 0 or not value.is_finite():
        raise ConfigError("DAILY_API_BUDGET_TON не может быть отрицательным")
    return value


def _path(raw: str | None, default: Path) -> Path:
    if not raw:
        return default
    p = Path(raw)
    return p if p.is_absolute() else ROOT_DIR / p


def load_settings(env: Mapping[str, str | None] | None = None) -> Settings:
    """Read settings from `env`, or from ROOT_DIR/.env overlaid with the process environment."""
    if env is None:
        env = {**dotenv_values(ROOT_DIR / ".env"), **os.environ}
    env = {k: v.strip() for k, v in env.items() if v is not None and v.strip()}

    api_id = env.get("API_ID")
    if api_id is not None and not api_id.isdigit():
        raise ConfigError("API_ID должно быть числом")
    level = env.get("LOG_LEVEL", "INFO").upper()
    if level not in LOG_LEVELS:
        raise ConfigError(f"LOG_LEVEL: одно из {', '.join(LOG_LEVELS)}")

    defaults = Settings()
    return Settings(
        bot_token=env.get("BOT_TOKEN"),
        api_id=int(api_id) if api_id else None,
        api_hash=env.get("API_HASH"),
        telegram_session=_path(env.get("TELEGRAM_SESSION"), defaults.telegram_session),
        kartoshka_api_token=env.get("KARTOSHKA_API_TOKEN"),
        database_url=env.get("DATABASE_URL", defaults.database_url),
        admin_telegram_ids=_admin_ids(env.get("ADMIN_TELEGRAM_IDS", "")),
        log_level=level,
        max_concurrent_api_requests=_positive_int(env, "MAX_CONCURRENT_API_REQUESTS", 2),
        daily_api_budget_ton=_budget(env.get("DAILY_API_BUDGET_TON", "0")),
        api_cache_ttl=_positive_int(env, "API_CACHE_TTL", 86_400),
        listing_scan_interval=_positive_int(env, "LISTING_SCAN_INTERVAL", 300),
        log_group_id=env.get("LOG_GROUP_ID"),
        owner_log_group_id=_group_id(env.get("OWNER_LOG_GROUP_ID")),
    )
