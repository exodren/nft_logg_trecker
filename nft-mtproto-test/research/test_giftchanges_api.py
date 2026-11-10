"""Standalone check of the free GiftChanges API (api.changes.tg). No auth, no tokens, not used by production.

Run: python research/test_giftchanges_api.py
Saves full responses to research/data/giftchanges/ and a summary to summary.json there.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterator

BASE = "https://api.changes.tg"
RESEARCH_DIR = Path(__file__).resolve().parent
OUT_DIR = RESEARCH_DIR / "data" / "giftchanges"
DELAY_S = 0.5
TIMEOUT_S = 30
ENDPOINTS = (
    "/health", "/gift/MoonPendant", "/gift/ViceCream", "/models/MoonPendant",
    "/changes", "/openapi.json", "/backdrops/MoonPendant", "/symbols/MoonPendant",
    "/total", "/auctions", "/crafts", "/llm",
)
SENSITIVE = re.compile(
    r"owner|seller|buyer|holder|listing|listed|resale|resell|transfer|wallet|username|user_?id|peer|address|history|market|floor|price",
    re.I,
)


def filename(path: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_") + ".json"


def fetch(path: str) -> dict[str, Any]:
    """GET without any auth header; never raises on HTTP or network errors."""
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "nft-research-check/1.0", "Accept": "application/json"})
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            status, headers, body = resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        status, headers, body = exc.code, dict(exc.headers), exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"path": path, "status": None, "error": repr(exc), "json": None}
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    return {
        "path": path, "status": status, "ms": round((time.monotonic() - started) * 1000),
        "content_type": headers.get("Content-Type"), "bytes": len(body), "json": data,
        "text": None if data is not None else body.decode("utf-8", "replace"),
    }


def walk(node: Any, trail: str = "$") -> Iterator[tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{trail}.{key}", value
            yield from walk(value, f"{trail}.{key}")
    elif isinstance(node, list):
        for item in node:
            yield from walk(item, f"{trail}[]")


def key_paths(data: Any) -> list[str]:
    return sorted({trail for trail, _ in walk(data)})


def sensitive_keys(data: Any) -> list[str]:
    return [p for p in key_paths(data) if SENSITIVE.search(p.rsplit(".", 1)[-1])]


def openapi_audit(spec: dict) -> dict[str, Any]:
    """Every path, every parameter, every schema property, plus free-text mentions, matched against SENSITIVE."""
    paths = spec.get("paths", {})
    text_hits = [
        {"at": trail, "text": value[:200]}
        for trail, value in walk(spec)
        if isinstance(value, str) and trail.rsplit(".", 1)[-1] in ("description", "summary") and SENSITIVE.search(value)
    ]
    params = sorted({p.get("name", "?") for _, v in walk(spec) if isinstance(v, list) for p in v if isinstance(p, dict) and "in" in p})
    props = sorted({k for t, v in walk(spec) if t.endswith(".properties") and isinstance(v, dict) for k in v})
    return {
        "openapi": spec.get("openapi"), "info": spec.get("info"), "security": spec.get("security"),
        "security_schemes": spec.get("components", {}).get("securitySchemes"),
        "path_count": len(paths), "paths": sorted(paths), "methods": sorted({m for p in paths.values() for m in p}),
        "schema_names": sorted(spec.get("components", {}).get("schemas", {})),
        "parameters": params, "property_names": props,
        "sensitive_paths": [p for p in paths if SENSITIVE.search(p)],
        "sensitive_parameters": [p for p in params if SENSITIVE.search(p)],
        "sensitive_properties": [p for p in props if SENSITIVE.search(p)],
        "sensitive_text": text_hits,
    }


def mtproto_attrs(card: dict) -> dict[str, dict] | None:
    """Only gift attributes are read from our samples; owner fields never leave the card."""
    gift = card.get("gift", card)
    if gift.get("_") != "StarGiftUnique":
        return None
    attrs = {a["_"]: a for a in gift.get("attributes", [])}
    return {"gift": gift, "model": attrs.get("StarGiftAttributeModel"), "pattern": attrs.get("StarGiftAttributePattern"),
            "backdrop": attrs.get("StarGiftAttributeBackdrop")}


def permille(attr: dict) -> int | None:
    rarity = attr.get("rarity") or {}
    return rarity.get("permille") if isinstance(rarity, dict) else attr.get("rarity_permille")


def match_card(attrs: dict[str, dict], api: dict) -> dict[str, Any]:
    """Compare one NFT card with the /gift/:id reference by document id, name and rarity."""
    model, pattern, backdrop = attrs["model"], attrs["pattern"], attrs["backdrop"]
    doc_id = str(model["document"]["id"])
    by_doc = next((m for m in api["models"] if doc_id in (m.get("documentIds") or [])), None)
    by_name = [m for m in api["models"] if m.get("telegramName", m["name"]) == model["name"]]
    sym = next((s for s in api["symbols"] if s["name"] == pattern["name"]), None)
    back = next((b for b in api["backdrops"] if b["name"] == backdrop["name"]), None)
    return {
        "slug": attrs["gift"]["slug"], "gift_id": str(attrs["gift"]["gift_id"]),
        "model_by_document": by_doc is not None, "model_by_name": bool(by_name),
        "model_permille_equal": by_doc is not None and by_doc["rarityPermille"] == permille(model),
        "model_permille": [permille(model), by_doc["rarityPermille"] if by_doc else None],
        "symbol_found": sym is not None, "symbol_permille_equal": sym is not None and sym["rarityPermille"] == permille(pattern),
        "backdrop_found": back is not None, "backdrop_permille_equal": back is not None and back["rarityPermille"] == permille(backdrop),
        "backdrop_id_equal": back is not None and back.get("backdropId") == backdrop.get("backdrop_id"),
    }


def compare_mtproto() -> dict[str, Any]:
    cards = [mtproto_attrs(json.loads(p.read_text(encoding="utf-8"))) for p in sorted((RESEARCH_DIR / "data/raw/unique_sample").glob("*.json"))]
    cards = [c for c in cards if c and c["model"] and c["pattern"] and c["backdrop"]]
    references: dict[str, Any] = {}
    for gift_id in sorted({str(c["gift"]["gift_id"]) for c in cards}):
        references[gift_id] = fetch(f"/gift/{gift_id}")
        time.sleep(DELAY_S)
    rows = [match_card(c, references[str(c["gift"]["gift_id"])]["json"]) for c in cards if references[str(c["gift"]["gift_id"])]["status"] == 200]
    doc_ids = ",".join(sorted({str(c["model"]["document"]["id"]) for c in cards})[:100])
    batch = fetch(f"/documents?ids={doc_ids}")
    checks = [k for k, v in rows[0].items() if isinstance(v, bool)] if rows else []
    return {
        "cards": len(cards), "distinct_gifts": len(references),
        "gift_id_resolved": sum(r["status"] == 200 for r in references.values()),
        "totals": {k: sum(r[k] for r in rows) for k in checks},
        "documents_batch": {"status": batch["status"], "known": sum(v is not None for v in (batch["json"] or {}).values()), "asked": doc_ids.count(",") + 1},
        "documents_sample": next(iter((batch["json"] or {}).items()), None),
        "rows": rows,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"endpoints": {}}
    spec = None
    for path in ENDPOINTS:
        result = fetch(path)
        data = result.pop("json")
        (OUT_DIR / filename(path)).write_text(
            json.dumps(data if data is not None else result, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        summary["endpoints"][path] = {**result, "key_paths": key_paths(data), "sensitive_keys": sensitive_keys(data)}
        if path == "/openapi.json":
            spec = data
        print(path, result["status"], result.get("bytes"), result.get("error") or "")
        time.sleep(DELAY_S)
    summary["openapi_audit"] = openapi_audit(spec) if isinstance(spec, dict) else None
    match = compare_mtproto()
    (OUT_DIR / "mtproto_match.json").write_text(json.dumps(match, ensure_ascii=False, indent=1), encoding="utf-8")
    print({k: v for k, v in match.items() if k != "rows"})
    (OUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
