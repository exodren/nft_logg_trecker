"""Shared settings. Secrets come from nft-mtproto-test/.env and are never printed."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

RESEARCH_DIR = Path(__file__).resolve().parent
PROJECT_DIR = RESEARCH_DIR.parent
load_dotenv(RESEARCH_DIR / ".env")
load_dotenv(PROJECT_DIR / ".env")

SOURCE_SESSION = PROJECT_DIR / "sessions" / "mtproto_test.session"
# Worked on as a copy: Telethon writes to the sqlite file, the original stays untouched.
RESEARCH_SESSION = RESEARCH_DIR / "sessions" / "research.session"

DATA_DIR = RESEARCH_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PARSED_DIR = DATA_DIR / "parsed"
REPORTS_DIR = DATA_DIR / "reports"

REQUEST_DELAY_S = float(os.getenv("REQUEST_DELAY_S", "1.5"))
REQUEST_TIMEOUT_S = float(os.getenv("REQUEST_TIMEOUT_S", "30"))
MAX_FLOOD_WAIT_S = int(os.getenv("MAX_FLOOD_WAIT_S", "120"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RESALE_PAGE_SIZE = int(os.getenv("RESALE_PAGE_SIZE", "50"))
RESALE_MAX_PAGES = int(os.getenv("RESALE_MAX_PAGES", "3"))
RESALE_MAX_GIFT_TYPES = int(os.getenv("RESALE_MAX_GIFT_TYPES", "15"))


def credentials() -> tuple[int, str]:
    return int(os.environ["API_ID"]), os.environ["API_HASH"]
