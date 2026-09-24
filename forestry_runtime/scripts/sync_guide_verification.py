"""Add new/changed guide claims to the private verification ledger.

Existing decisions are preserved by content fingerprint. Changed assertions
start as unverified. Removed assertions marked wrong remain in the audit trail.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from runtime.domain_guides import load_guides  # noqa: E402
from runtime.guide_verification import extract_claims, ledger_path, read_ledger  # noqa: E402


def escape(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def main() -> None:
    target = ledger_path()
    previous = read_ledger(target)
    current = [claim for guide in load_guides() for claim in extract_claims(guide.id, guide.body)]
    current_ids = {claim.id for claim in current}
    lines = [
        "# 领域指南逐条核验台账",
        "",
        "本文件为维护者核验资料；Runtime 只读取状态作内部排序，不把状态、来源或评分注入模型上下文。",
        "`verified` 仅表示该行有直接可核查的权威来源；`unverified` 表示尚无逐条核验证据；",
        "`wrong` 保留已纠正或删除的旧说法。来源必须直接支持本行完整表述，否则保持 `unverified`。",
        "修改指南正文后运行 `python scripts/sync_guide_verification.py`；正文变化会生成新 ID 并重置为未核验。",
        "",
        "| id | guide | claim | status | source | action |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for claim in current:
        old = previous.get(claim.id) or {}
        valid = old.get("guide") == claim.guide_id and old.get("claim") == claim.text
        status = old.get("status", "unverified") if valid else "unverified"
        source = old.get("source", "待核验") if valid else "待核验"
        action = old.get("action", "改写") if valid else "改写"
        lines.append("| " + " | ".join(map(escape, [claim.id, claim.guide_id, claim.text,
                                                 status, source, action])) + " |")
    for claim_id, row in previous.items():
        if claim_id in current_ids or row["status"] != "wrong":
            continue
        lines.append("| " + " | ".join(map(escape, [claim_id, row["guide"], row["claim"],
                                                 "wrong", row["source"], row["action"]])) + " |")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(current)} current claims; {sum(row['status']=='wrong' for row in previous.values() if row)} historical wrong claims; {target}")


if __name__ == "__main__":
    main()
