import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analyzers.gift_parser import extract_listing, field_inventory, parse_prices, peer_references, price_key  # noqa: E402


def test_prices_convert_nanotons_and_keep_stars():
    raw = [{"_": "StarsAmount", "amount": 500}, {"_": "StarsTonAmount", "amount": 1_500_000_000}]
    assert parse_prices(raw) == [{"amount": 500, "currency": "XTR"}, {"amount": 1.5, "currency": "TON"}]


def test_owner_name_is_never_treated_as_an_id():
    flat = extract_listing({"slug": "A-1", "owner_name": "@someone"})
    assert flat["owner_name"] == "@someone" and flat["owner_peer_id"] is None


def test_wallet_and_contract_addresses_stay_separate():
    flat = extract_listing({"owner_address": "wallet", "gift_address": "contract"})
    assert (flat["owner_address"], flat["gift_address"]) == ("wallet", "contract")


def test_inventory_counts_none_as_absent():
    assert field_inventory([{"a": None}, {"a": 1}])["a"]["present"] == 1


def test_price_key_ignores_converted_currency_drift():
    a = {"resell_amount": [{"_": "StarsAmount", "amount": 793}, {"_": "StarsTonAmount", "amount": 11160000000}]}
    b = {"resell_amount": [{"_": "StarsAmount", "amount": 793}, {"_": "StarsTonAmount", "amount": 11170000000}]}
    assert price_key(a) == price_key(b)


def test_price_key_uses_ton_for_ton_only_listing():
    a = {"resale_ton_only": True, "resell_amount": [{"_": "StarsAmount", "amount": 6750}, {"_": "StarsTonAmount", "amount": 95}]}
    b = {"resale_ton_only": True, "resell_amount": [{"_": "StarsAmount", "amount": 6749}, {"_": "StarsTonAmount", "amount": 95}]}
    assert price_key(a) == price_key(b) == ("StarsTonAmount", 95)


def test_original_details_are_labelled_as_original_not_owner():
    gift = {"attributes": [{"_": "StarGiftAttributeOriginalDetails", "recipient_id": {"_": "PeerUser", "user_id": 9}}]}
    assert peer_references(gift) == {"original_recipient_id": 9}


def test_peer_references_list_only_set_fields():
    gift = {"owner_id": None, "released_by": {"_": "PeerUser", "user_id": 7}}
    assert peer_references(gift) == {"released_by": 7}
