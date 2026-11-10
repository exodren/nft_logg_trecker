# Seller data discovery: public resale listings on Telegram Gift NFTs

Date: 2026-11-08. Schema: layer 233 (`api.tl` in AyuGramDesktop), Telethon 1.45 (same `starGiftUnique#85f0a9cd`).
Viewer: one authorized user session. All calls were read-only. `sendStarGiftOffer` was not executed.

## Answer

**In every response tested, no official read-only response linked a public listing to a seller peer.**
`owner_id` is the only field that can carry that link, and it was null on all 2,366 observations below.
The client code confirms the field is the only source: when it is null, the clients show `owner_name` as plain text and offer no profile link.

This holds for the scope below. It is not a proof that `owner_id` is never set on listings (see "Unanswered questions").

## Scope of experiments

| Experiment | Sample | Result file |
|---|---|---|
| Earlier resale scan (`test_resale.py`) | 2,250 listings, 15 gift types, 45 pages | `raw/resale/*.json` |
| Earlier card sample (`test_unique_gift.py`) | 25 listings + 2 controls | `raw/unique_sample/`, `raw/unique_*.json` |
| Fresh listing vs card (`test_seller_compare.py`) | 50 pairs, 5 gift types, card fetched right after the page | `raw/seller_discovery/`, `parsed/seller_compare.json` |
| Random cards, listed or not (`test_offer_correlation.py`) | 40 requested, 39 found, 5 types (flags only stored) | `parsed/offer_correlation.json` |

Paths are relative to `research/data/`. Total `owner_id` observations: 2,250 + 27 + 50 + 39 = 2,366, all null. The 50 fresh pairs are counted once, by card; each listing side was also null.

## Priority 1: same NFT in both APIs (50 fresh pairs)

| Field | Listing | Card | Pairs |
|---|---|---|---|
| `owner_id` | null | null | 50 |
| `host_id` | null | null | 50 |
| `owner_address` | null | null | 50 |
| `offer_min_stars` | null | null | 50 |
| `owner_name` | set | set | 50 |
| `gift_address` | set | set | 11 (null in both: 39) |
| `released_by` | set | set | 10 (null in both: 40) |

- A deep comparison of every field and nested object found two differing paths: `attributes[0].document.file_reference` and `attributes[1].document.file_reference`. These are per-request media tokens and carry no identity data.
- `getUniqueStarGift` therefore added nothing about the seller beyond the listing.

### Returned peers (`raw/seller_discovery/peers/<slug>.json`)

The 50 cards returned 15 peers in total. 14 are referenced by the NFT object, through:

| Referencing field | Cards | Meaning |
|---|---|---|
| `released_by` | 10 | Collection publisher |
| `StarGiftAttributeOriginalDetails.recipient_id` | 3 | Original gift recipient |
| `StarGiftAttributeOriginalDetails.sender_id` | 1 | Original gift sender |

The remaining peer (a bot, on `ViceCream-236774`) appears nowhere in the card; why it was returned is unknown.
**None of the 15 is referenced as owner, host or seller.** Original sender and recipient describe the gift's first transfer, not its current holder (name match with `owner_name` was 9/297 in the earlier run).

## Priority 2: offer methods

### TL definitions (`api.tl`)

| Line | Definition |
|---|---|
| 2950 | `payments.sendStarGiftOffer#8fb86b41 flags:# peer:InputPeer slug:string price:StarsAmount duration:int random_id:long allow_paid_stars:flags.0?long = Updates` |
| 2930 | `payments.getUniqueStarGift#a1974d72 slug:string = payments.UniqueStarGift` |
| 2937 | `payments.getResaleStarGifts#7a5fa236 ... gift_id:long attributes:flags.3?Vector<StarGiftAttributeId> offset:string limit:int = payments.ResaleStarGifts` |
| 1979 | `starGiftUnique#85f0a9cd ... owner_id:flags.0?Peer owner_name:flags.1?string owner_address:flags.2?string ... host_id:flags.12?Peer offer_min_stars:flags.13?int ...` |
| 1582 | `inputInvoiceStarGiftResale#e9b0c658 ... slug:string to_id:InputPeer ...` |

### Findings

- **Required to submit an offer:** `peer`, `slug`, `price`, `duration`, `random_id`. `allow_paid_stars` is optional.
- **Where the owner peer comes from:** only `StarGiftUnique.owner_id`. Both clients read that field and turn it into an `InputPeer`: AyuGram `boxes/star_gift_box.cpp:3251-3257`, Android `ui/Stars/StarGiftSheet.java:1161` passing it to `ui/Stars/GiftOfferSheet.java:741`.
- **Read-only route to that peer from a public listing:** none found. No client code has a fallback when `owner_id` is null.
- **Buying needs no seller peer.** `inputInvoiceStarGiftResale` takes the `slug` and the recipient `to_id`, so a listing can be bought while its owner stays hidden. AyuGram `boxes/transfer_gift_box.cpp:548-559`, Android `ui/Stars/StarsController.java:2854-2856`.
- **`offer_min_stars` vs `owner_id`: not measurable from responses.** Both were null in all 39 random cards and all 50 pairs, and in the 27 older cards, so there is no positive sample. From code only:
  - AyuGram enables the offer only if `owner_id` is set, is a user, is not self, and `starsMinOffer >= 0` (`settings/settings_credits_graphics.cpp:1036-1042`).
  - Android shows the offer menu when `offer_min_stars > 0` (`ui/Stars/StarGiftSheet.java:1145`), with no separate `owner_id` check at that line.
  - This suggests the server sends them together, which remains unconfirmed.

## Priority 3: client source code

Checked commits:
- AyuGramDesktop `0f2a31bb420de98f331990432265b131b8d836b5` (2026-10-20). Paths are under `Telegram/SourceFiles/`.
- Telegram Android (upstream) `7be672ead283d022812d75c729d3a04dd4c2a2c6` (2026-11-07). Paths are under `TMessagesProj/src/main/java/org/telegram/`.
- exteraGram: **not usable.** The only public branch, `main`, is at `6f78031aafd7f27e0dfaa31a207715569a6df1a2` (2023-06-03), before gifts existed, and contains none of these methods. Telegram Android was inspected as its upstream, so **exteraGram's current behaviour is unverified**.

| Method | Client call site | Input | Response | Fields used for seller display | Seller peer source |
|---|---|---|---|---|---|
| `getResaleStarGifts` | AyuGram `data/data_star_gift.cpp:229`; Android `ui/Gifts/ResaleGiftsFragment.java:1025` | `gift_id`, filters, `offset`, `limit` | `ResaleStarGifts` | Stored in the gift model; users and chats are cached | The listing's own `owner_id`, when set |
| `getUniqueStarGift` | AyuGram `core/local_url_handlers.cpp:2234`; Android `ui/LaunchActivity.java:4265`, `ui/Stars/StarGiftSheet.java:4362`, `:6178` | `slug` | `UniqueStarGift` | `owner_id`, `host_id`, `owner_name`, `owner_address` | The card's own `owner_id`, when set |
| `sendStarGiftOffer` | AyuGram `boxes/star_gift_box.cpp:3254`; Android `ui/Stars/GiftOfferSheet.java:739` | see above | `Updates` | none | Taken from `owner_id` |
| Resale purchase invoice | AyuGram `boxes/transfer_gift_box.cpp:554`; Android `ui/Stars/StarsController.java:2854` | `slug`, `to_id` | payment form | none | Not needed |

Parsing: AyuGram `api/api_premium.cpp:940-954` maps the fields, with `ownerId` staying empty when absent.

Owner row rendering:

| Case | AyuGram `boxes/gift_premium_box.cpp` | Android `ui/Stars/StarGiftSheet.java` | Shown as |
|---|---|---|---|
| `owner_id` set | `:1523-1524` | `:4635-4636` | Clickable profile row |
| Only `owner_name` | `:1554-1558` | `:4633-4634` | Plain text, no link |
| `owner_address` | `:1559` | `:4629-4630` | Copyable address |

Android checks `owner_address` first (`:4629`). AyuGram checks `owner_id`, then `owner_name`, then the address.

In the call sites read, neither client issues an extra request to find a seller when the field is null.

## Identity types: what each field proves

| Type | Field | Status in this data | What it proves |
|---|---|---|---|
| 1. Telegram ID / Peer | `owner_id`, `host_id` | null in all samples | Would be the actual link, if set |
| 2. Public username | Only on a `User` returned by ID or username lookup | Not linked to any seller | Requires a peer first |
| 3. Display name | `owner_name` | Set in all 50 pairs | Nothing about identity: free text, may contain a typed `@x` |
| 4. TON wallet address | `owner_address` | null in all 50 pairs; may be a `name.t.me` domain | An on-chain holder, not a Telegram identity |
| 5. NFT contract address | `gift_address` | Set in 11 of 50 pairs | The token itself, says nothing of its holder |

## Negative findings and limits

- `getSavedStarGifts` was not run. Per its schema it goes from a known `peer` to that peer's gifts (line 2931), so it cannot start from a listing, and calling it across other accounts is outside the constraints.
- No user was looked up in this research task.
- Offer flow was read only from source; never executed.

## Unanswered questions

1. Does `owner_id` ever appear on a resale listing, and for whom? No positive sample exists. All samples come from one viewer.
2. Does `owner_id` vary by viewer (contact status, mutual relationship)? Needs a second consenting account.
3. Does `offer_min_stars` always accompany `owner_id` on the server side? Needs a positive sample, such as the researcher's own NFT or a consenting owner's.
4. Why was one unreferenced bot returned for `ViceCream-236774`?
5. Current exteraGram code is unavailable publicly. Client behaviour was read from source and not compared against a live UI.

## Reproduction

From `nft-mtproto-test/`, with the existing session:

```
.venv/Scripts/python.exe research/scripts/test_seller_compare.py
.venv/Scripts/python.exe research/scripts/test_offer_correlation.py
.venv/Scripts/python.exe -m pytest research/tests -q
```

Client sources: shallow-clone the repositories above at the listed commits and open the cited lines. Line numbers apply to those commits only.
