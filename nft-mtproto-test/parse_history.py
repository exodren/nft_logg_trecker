"""TonAPI NFT events -> flat history: operation, utime, source, destination, transaction_hash, item.owner.

usage: python parse_history.py chillflame.json [--fetch] [--csv out.csv]
       python parse_history.py --test
"""
import csv
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

TONAPI = "https://tonapi.io/v2/accounts/{addr}/events?limit=100&before_lt={lt}"
# ponytail: name-based list, add addresses when a new marketplace shows up
ESCROWS = {"Marketapp Deployer"}
FRAGMENT_MINTER = "Fragment Gift Minter"
FIELDS = ["operation", "utime", "time_utc", "source", "destination", "transaction_hash", "item.owner", "comment"]


def label(acc):
    return acc.get("name") or acc["address"] if acc else None


def classify(t):
    s, r, c = label(t.get("sender")), label(t.get("recipient")), t.get("comment") or ""
    if r == FRAGMENT_MINTER:
        return "withdraw_to_telegram"
    if r in ESCROWS:
        return "list_on_market"
    if s in ESCROWS:
        return "market_out"  # sale or delist; price is not in NftItemTransfer
    if "TeleitemBidInfo" in c:
        return "export_from_telegram" if s != r else "teleitem_call"
    return "transfer"


def parse(events, nft):
    rows, owner = [], None
    for e in sorted(events, key=lambda e: e["lt"]):  # oldest first, so owner accumulates
        for a in e["actions"]:
            t = a.get("NftItemTransfer")
            if a["type"] != "NftItemTransfer" or a["status"] != "ok" or t["nft"] != nft:
                continue
            dst = label(t.get("recipient"))
            if dst not in ESCROWS:
                owner = dst
            rows.append({
                "operation": classify(t),
                "utime": e["timestamp"],
                "time_utc": datetime.fromtimestamp(e["timestamp"], timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "source": label(t.get("sender")),
                "destination": dst,
                "transaction_hash": e["event_id"],  # trace hash; per-tx hashes in base_transactions
                "item.owner": owner,
                "comment": t.get("comment"),
            })
    return rows


def fetch_older(addr, lt):
    """Follow next_from until TonAPI returns 0. TONAPI_KEY env is optional (raises rate limit)."""
    out, headers = [], {"User-Agent": "nft-history-parser"}
    if os.environ.get("TONAPI_KEY"):
        headers["Authorization"] = "Bearer " + os.environ["TONAPI_KEY"]
    while lt:
        req = urllib.request.Request(TONAPI.format(addr=addr, lt=lt), headers=headers)
        with urllib.request.urlopen(req, timeout=20) as r:
            page = json.load(r)
        out += page["events"]
        lt = page.get("next_from") or 0
        time.sleep(1.1)  # free tier: 1 rps
    return out


def find_gaps(rows):
    """Transfers whose sender is not the previous holder -> missing events."""
    gaps, holder = [], None
    for r in rows:
        if holder and r["source"] != holder:
            gaps.append(f'{r["time_utc"]}: expected sender {holder}, got {r["source"]}')
        holder = r["destination"]
    return gaps


def main(argv):
    path = argv[0]
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    events = data["events"]
    nft = events[0]["account"]["address"]
    if "--fetch" in argv and data.get("next_from"):
        events += fetch_older(nft, data["next_from"])
    events = list({e["event_id"]: e for e in events}.values())
    rows = parse(events, nft)

    for r in rows:
        print(f'{r["time_utc"]}  {r["operation"]:<20} {r["source"][:22]:<22} -> {r["destination"][:22]:<22} owner={r["item.owner"]}')
    print(f"\nnft={nft} events={len(events)} transfers={len(rows)} current_owner={rows[-1]['item.owner'] if rows else None}")
    for g in find_gaps(rows):
        print("GAP:", g)
    if "--csv" in argv:
        out = argv[argv.index("--csv") + 1]
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerows(rows)
        print("saved", out)


def _test():
    acc = lambda a, n=None: {"address": a, **({"name": n} if n else {})}
    tr = lambda s, r, nft="N", c=None: {"type": "NftItemTransfer", "status": "ok",
                                        "NftItemTransfer": {"sender": s, "recipient": r, "nft": nft, "comment": c}}
    ev = [
        {"event_id": "e2", "lt": 2, "timestamp": 2, "actions": [tr(acc("A"), acc("M", "Marketapp Deployer")),
                                                                tr(acc("X"), acc("Y"), nft="OTHER")]},
        {"event_id": "e1", "lt": 1, "timestamp": 1, "actions": [tr(acc("C", "Chill Flames"), acc("A"), c="Call: TeleitemBidInfo")]},
        {"event_id": "e3", "lt": 3, "timestamp": 3, "actions": [tr(acc("M", "Marketapp Deployer"), acc("B"))]},
        {"event_id": "e4", "lt": 4, "timestamp": 4, "actions": [tr(acc("Z"), acc("F", FRAGMENT_MINTER))]},
    ]
    rows = parse(ev, "N")
    assert [r["operation"] for r in rows] == ["export_from_telegram", "list_on_market", "market_out", "withdraw_to_telegram"]
    assert [r["item.owner"] for r in rows] == ["A", "A", "B", FRAGMENT_MINTER]
    assert find_gaps(rows) == ["1970-01-01 00:00: expected sender B, got Z"]
    print("ok")


if __name__ == "__main__":
    _test() if sys.argv[1:] == ["--test"] else main(sys.argv[1:])
