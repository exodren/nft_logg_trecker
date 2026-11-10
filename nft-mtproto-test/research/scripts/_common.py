"""Import path and shared access to the resale scan stored on disk."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import config  # noqa: E402

RESALE_DIR = config.RAW_DIR / "resale"
HYPOTHESES = config.REPORTS_DIR / "hypotheses.json"
CONTROL_SLUGS = ("ChillFlame-160734", "SnoopCigar-95248")


def iter_pages() -> Iterator[tuple[Path, dict]]:
    for path in sorted(RESALE_DIR.glob("*.json")):
        yield path, json.loads(path.read_text(encoding="utf-8"))


def require_scan() -> list[tuple[Path, dict]]:
    pages = list(iter_pages())
    if not pages:
        raise SystemExit("No scan on disk. Run: python research/scripts/test_resale.py")
    return pages


def unique_gifts(pages: list[tuple[Path, dict]]) -> dict[str, dict]:
    return {g["slug"]: g for _, page in pages for g in page["gifts"] if g.get("_") == "StarGiftUnique"}
