"""K4.1b: save real /v1/gift/{ref} responses as test fixtures.

Usage:  python -m scripts.capture_kartoshka_fixtures [--allow-paid] IonGem-3185 MoonPendant-71710 Nope-999999999
Pick refs that cover: owner with username, owner without username, hidden owner, missing gift (404 is free).
Without --allow-paid: refuses to start if the free quota is smaller than the number of refs and stops on the
first paid response. With --allow-paid: paid requests (0.00005 TON each) are allowed.
The token is never written to disk or printed.
"""
from __future__ import annotations

import asyncio
import json
import sys
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from app.config import ConfigError, load_settings
from app.providers.kartoshka import KartoshkaApiProvider, parse_billing

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "kartoshka"
KEEP_HEADERS = ("X-Free-Remaining", "X-Request-Cost", "X-Balance", "X-RateLimit-Limit", "X-RateLimit-Remaining")


async def main(args: list[str]) -> int:
    allow_paid = "--allow-paid" in args
    refs = [a for a in args if a != "--allow-paid"]
    if not refs:
        print(__doc__)
        return 2
    try:
        settings = load_settings()
        settings.require("kartoshka")
    except ConfigError as e:
        print(f"Ошибка конфигурации: {e}")
        return 2
    p = KartoshkaApiProvider(settings.kartoshka_api_token)
    try:
        billing = await p.probe()
        print(f"Бесплатных запросов осталось: {billing.free_remaining}, баланс: {billing.balance} TON")
        if not allow_paid and (billing.free_remaining is None or billing.free_remaining < len(refs)):
            print(f"Бесплатных запросов меньше, чем нужно ({len(refs)}). Для платных запросов добавьте --allow-paid.")
            return 1
        OUT.mkdir(parents=True, exist_ok=True)
        for ref in refs:
            status, body, headers = await p.get(f"/gift/{quote(ref, safe='')}")
            record = {"ref": ref, "http_status": status, "headers": {h: headers.get(h) for h in KEEP_HEADERS},
                      "body": body}
            (OUT / f"{ref}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            cost = parse_billing(headers).request_cost
            print(f"{ref}: HTTP {status}, cost {cost}")
            if not allow_paid and cost is not None and cost > Decimal("0"):
                print("Запрос оказался платным — остановка (для платных запросов добавьте --allow-paid).")
                return 1
    finally:
        await p.close()
    print(f"Сохранено в {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
