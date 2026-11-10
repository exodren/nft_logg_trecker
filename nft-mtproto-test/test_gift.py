"""Read-only probe: what does payments.getUniqueStarGift return for one NFT."""
import asyncio
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.functions.payments import GetUniqueStarGiftRequest
from telethon.tl.types import PeerChannel, PeerChat, PeerUser

load_dotenv()
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
PHONE = os.environ["PHONE"]
GIFT_SLUG = os.environ["GIFT_SLUG"]

SESSION_DIR = Path("sessions")
SESSION_DIR.mkdir(exist_ok=True)


def find_peer(peer, users, chats):
    if isinstance(peer, PeerUser):
        return "USER", next((u for u in users if u.id == peer.user_id), None)
    if isinstance(peer, PeerChannel):
        return "CHANNEL", next((c for c in chats if c.id == peer.channel_id), None)
    if isinstance(peer, PeerChat):
        return "CHAT", next((c for c in chats if c.id == peer.chat_id), None)
    return type(peer).__name__, None


def print_peer(label, peer, users, chats):
    print(f"\n=== {label} ===")
    if peer is None:
        print(f"{label.lower()}: NOT AVAILABLE")
        return
    kind, entity = find_peer(peer, users, chats)
    print("peer:", peer.to_dict(), "| type:", kind)
    if entity is None:
        print("entity: not in returned users/chats")
        return
    print("telegram_id:", entity.id)
    print("name:", getattr(entity, "first_name", None) or getattr(entity, "title", None),
          getattr(entity, "last_name", None) or "")
    usernames = [u.username for u in (getattr(entity, "usernames", None) or [])]
    print("username:", getattr(entity, "username", None), "| extra:", usernames or None)


async def main():
    print("[1] Connecting to Telegram...")
    async with TelegramClient(str(SESSION_DIR / "mtproto_test"), API_ID, API_HASH) as client:
        await client.start(phone=PHONE)
        print("[OK] Authorized")

        print(f"[2] payments.getUniqueStarGift(slug={GIFT_SLUG!r})")
        res = await client(GetUniqueStarGiftRequest(slug=GIFT_SLUG))
        gift, users, chats = res.gift, res.users, res.chats

        print("\n=== GIFT DATA ===")
        for f in ("title", "slug", "num", "id", "gift_id", "availability_issued",
                  "availability_total", "gift_address", "resell_amount", "value_amount",
                  "value_currency", "value_usd_amount", "burned"):
            print(f"{f}: {getattr(gift, f, None)}")

        print_peer("OWNER", gift.owner_id, users, chats)
        print("owner_name:", gift.owner_name)
        print("owner_address (TON):", gift.owner_address)
        # host_id = profile displaying the gift; NOT necessarily the owner
        print_peer("HOST", getattr(gift, "host_id", None), users, chats)

        print("\n=== ATTRIBUTES ===")
        for a in gift.attributes or []:
            print(type(a).__name__, getattr(a, "name", ""), getattr(a, "rarity_permille", ""))

        print(f"\n=== RETURNED: {len(users)} users, {len(chats)} chats ===")
        for u in users:
            print("user", u.id, u.first_name, u.username)
        for c in chats:
            print("chat", c.id, getattr(c, "title", None), getattr(c, "username", None))

        dump = SESSION_DIR / f"dump_{GIFT_SLUG}.json"  # contains personal data, gitignored
        dump.write_text(json.dumps(res.to_dict(), default=str, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        print(f"\n[3] Test completed. Raw response: {dump}")


if __name__ == "__main__":
    asyncio.run(main())
