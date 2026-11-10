"""Run the safe diagnostics in order. One failure does not stop the rest; exit codes are recorded."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from _common import config
from mtproto_client import write_json

SCRIPTS = ("test_star_gifts", "test_resale", "test_owner_seller", "test_host_id", "test_unique_gift",
           "test_user_resolution", "test_history", "test_pagination", "test_mint_discovery")
PAUSE_S = 5


def main() -> int:
    here = Path(__file__).resolve().parent
    codes = {}
    for name in SCRIPTS:
        print(f"\n=== {name} ===", flush=True)
        codes[name] = subprocess.run([sys.executable, str(here / f"{name}.py")], timeout=1800).returncode
        time.sleep(PAUSE_S)
    write_json(config.REPORTS_DIR / "run_all.json", codes)
    print(codes)
    return int(any(codes.values()))


if __name__ == "__main__":
    sys.exit(main())
