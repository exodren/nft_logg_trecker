"""Pick 10 gifts from our DB and save Kartoshka's raw /v1/gift responses (via capture_kartoshka_fixtures).

Usage:  python -m scripts.capture_kartoshka_sample [--allow-paid] [N]
One active gift per random collection, plus one missing ref (404).
"""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select

from app.config import load_settings
from app.database.models import Gift, Listing
from app.database.session import create_engine, session_factory
from scripts.capture_kartoshka_fixtures import main as capture

MISSING_REF = "Nope-999999999"


async def pick_slugs(n: int) -> list[str]:
    engine = create_engine(load_settings().database_url)
    try:
        async with session_factory(engine)() as s:
            q = (select(func.min(Gift.slug)).join(Listing).where(Listing.is_active)
                 .group_by(Gift.collection).order_by(func.random()).limit(n))
            return list((await s.scalars(q)).all())
    finally:
        await engine.dispose()


async def main(args: list[str]) -> int:
    n = next((int(a) for a in args if a.isdigit()), 10)
    slugs = await pick_slugs(n)
    if not slugs:
        print("В базе нет активных листингов — сначала запустите python -m app.")
        return 1
    print("Запрашиваем:", ", ".join(slugs))
    return await capture([*(a for a in args if a == "--allow-paid"), *slugs, MISSING_REF])


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
