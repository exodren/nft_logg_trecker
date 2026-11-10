from decimal import Decimal

import pytest

from app.config import ConfigError, load_settings

FULL = {"API_ID": "12345", "API_HASH": "hash_value_abc", "BOT_TOKEN": "111:bot", "KARTOSHKA_API_TOKEN": "12:k",
        "ADMIN_TELEGRAM_IDS": "1, 8556771897", "DAILY_API_BUDGET_TON": "0.5"}


def test_full_config_parses_types():
    s = load_settings(FULL)
    assert s.api_id == 12345
    assert s.admin_telegram_ids == frozenset({1, 8556771897})
    assert s.daily_api_budget_ton == Decimal("0.5")
    assert s.owner_provider_enabled


def test_defaults_are_safe():
    s = load_settings({})
    assert s.daily_api_budget_ton == 0  # free quota only
    assert not s.owner_provider_enabled
    assert s.database_url.startswith("sqlite+aiosqlite:///")


def test_missing_bot_token_gives_readable_error():
    s = load_settings({"API_ID": "1", "API_HASH": "h"})
    s.require("mtproto")
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        s.require("bot")


@pytest.mark.parametrize("env,field", [
    ({"ADMIN_TELEGRAM_IDS": "1,abc"}, "ADMIN_TELEGRAM_IDS"),
    ({"API_ID": "x1"}, "API_ID"),
    ({"DAILY_API_BUDGET_TON": "-1"}, "DAILY_API_BUDGET_TON"),
    ({"DAILY_API_BUDGET_TON": "lots"}, "DAILY_API_BUDGET_TON"),
    ({"API_CACHE_TTL": "0"}, "API_CACHE_TTL"),
    ({"LOG_LEVEL": "LOUD"}, "LOG_LEVEL"),
])
def test_invalid_values_rejected(env, field):
    with pytest.raises(ConfigError, match=field):
        load_settings(env)


def test_secrets_list_contains_only_set_values():
    assert set(load_settings(FULL).secrets()) == {"hash_value_abc", "111:bot", "12:k"}
    assert load_settings({}).secrets() == []
