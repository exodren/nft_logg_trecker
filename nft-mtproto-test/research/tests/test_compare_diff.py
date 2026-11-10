import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from test_seller_compare import deep_diff, linkage  # noqa: E402


def test_deep_diff_reports_nested_and_missing_paths():
    a = {"x": {"y": 1}, "l": [{"k": 1}], "same": 2}
    b = {"x": {"y": 2}, "l": [{"k": 3}], "same": 2, "extra": 1}
    assert deep_diff(a, b) == {"x.y": [1, 2], "l[0].k": [1, 3], "extra": [None, 1]}


def test_returned_user_counts_as_linked_only_when_nft_references_it():
    gift = {"owner_id": None, "attributes": [{"_": "StarGiftAttributeOriginalDetails",
                                              "recipient_id": {"_": "PeerUser", "user_id": 7}}]}
    got = linkage(gift, [{"id": 7}, {"id": 8}], [])
    assert got == {"reference_fields": ["original_recipient_id"], "returned": 2,
                   "referenced_and_returned": 1, "returned_unreferenced": 1}
