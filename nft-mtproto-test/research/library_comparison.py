"""Compare MTProto libraries by reading their published wheels: nothing is installed or executed."""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

import config
from mtproto_client import write_json

PACKAGES = ("telethon", "pyrogram", "kurigram")
NEEDLES = {
    "getResaleStarGifts": ("get_resale_star_gifts", "GetResaleStarGiftsRequest"),
    "getUniqueStarGift": ("get_unique_star_gift", "GetUniqueStarGiftRequest"),
    "getStarGifts": ("get_star_gifts", "GetStarGiftsRequest"),
}


def pypi(name: str) -> dict:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json", timeout=20) as r:
        data = json.load(r)
    version = data["info"]["version"]
    return {"version": version, "released": data["releases"][version][0]["upload_time"][:10],
            "requires_python": data["info"]["requires_python"]}


def inspect_wheel(name: str, directory: Path) -> dict:
    subprocess.run([sys.executable, "-m", "pip", "download", name, "--no-deps", "--only-binary=:all:", "-d", str(directory), "-q"],
                   check=True, timeout=180)
    wheel = next(directory.glob("*.whl"))
    with zipfile.ZipFile(wheel) as z:
        names = z.namelist()
        blob = "\n".join(names)
        tl_files = [n for n in names if n.endswith(("raw/all.py", "tl/alltlobjects.py"))]
        layer = re.search(r"(?:layer|LAYER)\s*=\s*(\d+)", z.read(tl_files[0]).decode()) if tl_files else None
        requests = z.read("telethon/tl/functions/payments.py").decode() if name == "telethon" else ""
    support = {m: any(n in blob or n in requests for n in needles) for m, needles in NEEDLES.items()}
    return {"tl_layer": int(layer.group(1)) if layer else None, "methods": support}


def main() -> None:
    out = {}
    for name in PACKAGES:
        with tempfile.TemporaryDirectory() as tmp:
            out[name] = {**pypi(name), **inspect_wheel(name, Path(tmp))}
    write_json(config.PARSED_DIR / "library_comparison.json", out)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
