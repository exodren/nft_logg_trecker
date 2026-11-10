"""Evidence -> verdict. `confirmed` is unreachable without independent verification."""
from __future__ import annotations

import json
from pathlib import Path

STATUSES = ("confirmed", "partially_confirmed", "rejected", "inconclusive", "not_tested")
MIN_SAMPLES = 20
CONFIRM_FROM = 0.9
REJECT_BELOW = 0.1


def verdict(total: int, validated: int, independently_verified: bool) -> tuple[str, str]:
    if total == 0:
        return "not_tested", "none"
    if total < MIN_SAMPLES and validated:
        return "inconclusive", "low"
    if validated == 0 or validated / total < REJECT_BELOW:
        return "rejected", "high" if total >= MIN_SAMPLES else "low"
    if validated / total >= CONFIRM_FROM:
        return ("confirmed", "high") if independently_verified else ("partially_confirmed", "medium")
    return "partially_confirmed", "low"


def build_result(hypothesis: str, total: int, validated: int, evidence: list, limitations: list,
                 independently_verified: bool = False) -> dict:
    status, confidence = verdict(total, validated, independently_verified)
    return {"hypothesis": hypothesis, "status": status, "samples_total": total, "samples_validated": validated,
            "samples_failed": total - validated, "evidence": evidence, "limitations": limitations,
            "confidence": confidence}


def save_result(path: Path, result: dict) -> None:
    """Merge by name so each script can run alone."""
    existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**existing, result["hypothesis"]: result}, ensure_ascii=False, indent=1),
                    encoding="utf-8")
