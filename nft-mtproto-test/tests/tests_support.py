"""Shared builders for tests (not a test module)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from app.mtproto.scanner import ListingScanner
from app.services.enrichment import OwnerResolver
from app.services.pipeline import Pipeline
from app.services.queue import TaskQueue

PAGE = Path(__file__).parent / "fixtures" / "resale" / "5843762284240831056_p0.json"


class PageTelegram:
    def __init__(self, page: dict) -> None:
        self.page = page

    async def __call__(self, request):
        if type(request).__name__ == "GetStarGiftsRequest":
            return {"gifts": [{"_": "StarGift", "id": 5843762284240831056, "availability_resale": 1}]}
        return copy.deepcopy(self.page)


def build_pipeline_with_page(sessions, provider, usage=None):
    queue = TaskQueue(sessions)
    pipe = Pipeline(sessions, queue, OwnerResolver(sessions, provider, usage, cache_ttl_s=3600))
    page = json.loads(PAGE.read_text(encoding="utf-8"))
    return pipe, ListingScanner(PageTelegram(page), sessions, 60, on_events=pipe.on_events)
