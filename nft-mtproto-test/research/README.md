# Telegram Gifts research lab

Read-only diagnostics. `mtproto_client.READ_ONLY_REQUESTS` is an allowlist: any other MTProto request raises.
Scripts work on a copy of the session (`research/sessions/`); the original file is not touched.

Run from `nft-mtproto-test/` with its venv:

```
.venv\Scripts\python research\scripts\run_all.py       # all diagnostics, about 7 min
.venv\Scripts\python research\scripts\test_resale.py   # any script also runs alone
.venv\Scripts\python research\library_comparison.py
.venv\Scripts\python -m pytest research\tests
```

`test_owner_seller` and `test_host_id` work offline from `data/raw/resale/`. Delete that folder for a fresh scan.

Output: `data/raw` (API responses), `data/parsed`, `data/reports` (`final_report.md`, `hypotheses.json`, `errors.jsonl`).
`data/` holds personal data of Telegram users and is gitignored; do not publish it.
