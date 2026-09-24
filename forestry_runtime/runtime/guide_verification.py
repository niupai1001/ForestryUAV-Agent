"""Private verification ledger for ranking domain guides.

The ledger is deliberately never included in the prompt, catalogue, or guide
tool response. A claim only receives verified credit when its current text has
the same fingerprint as the reviewed text in GUIDE_VERIFICATION.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re


LEDGER_ENV = "DOMAIN_GUIDE_VERIFICATION"
CONTAINER_LEDGER = Path("/guide-verification/GUIDE_VERIFICATION.md")
_SOURCE_HEADINGS = {"来源", "依据", "依据来源", "参考文献", "references"}
_SENTENCE_END = re.compile(r"(?<=[。！？])\s*|(?<=；)\s*")
_LIST_MARKER = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+")


@dataclass(frozen=True)
class Claim:
    guide_id: str
    id: str
    text: str


def ledger_path() -> Path:
    configured = os.getenv(LEDGER_ENV)
    if configured:
        return Path(configured)
    if CONTAINER_LEDGER.is_file():
        return CONTAINER_LEDGER
    return Path(__file__).resolve().parent.parent / "evaluation" / "grounded_v1" / "GUIDE_VERIFICATION.md"


def _normalize(text: str) -> str:
    return " ".join(text.strip().split())


def claim_id(guide_id: str, text: str) -> str:
    digest = hashlib.sha256((guide_id + "\0" + _normalize(text)).encode("utf-8")).hexdigest()
    return "C" + digest[:16]


def extract_claims(guide_id: str, body: str) -> list[Claim]:
    """Extract every prose sentence, list item and table row before references."""
    blocks: list[str] = []
    paragraph: list[str] = []
    fenced = False

    def flush() -> None:
        if paragraph:
            blocks.append(_normalize(" ".join(paragraph)))
            paragraph.clear()

    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            flush()
            fenced = not fenced
            continue
        if fenced:
            continue
        if stripped.startswith("## ") and stripped[3:].strip().casefold() in _SOURCE_HEADINGS:
            flush()
            break
        if not stripped or stripped.startswith("#"):
            flush()
            continue
        if stripped.startswith("| "):
            flush()
            if not re.fullmatch(r"[| :\-]+", stripped):
                blocks.append(stripped)
            continue
        if _LIST_MARKER.match(stripped):
            flush()
            paragraph.append(_LIST_MARKER.sub("", stripped))
            continue
        paragraph.append(stripped)
    flush()

    claims: list[Claim] = []
    seen: set[str] = set()
    for block in blocks:
        # A table row is one operational comparison; regular prose is split
        # into sentences so each assertion has its own ledger entry.
        parts = [block] if block.startswith("| ") else _SENTENCE_END.split(block)
        for part in parts:
            part = _normalize(part)
            if not part or part.endswith(("：", ":")) or part in seen:
                continue
            seen.add(part)
            claims.append(Claim(guide_id, claim_id(guide_id, part), part))
    return claims


def read_ledger(path: Path | None = None) -> dict[str, dict[str, str]]:
    path = path or ledger_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    rows: dict[str, dict[str, str]] = {}
    for line in lines:
        if not line.startswith("| C"):
            continue
        fields = [part.strip().replace("\\|", "|") for part in re.split(r"(?<!\\)\|", line)[1:-1]]
        if len(fields) != 6:
            continue
        claim, guide, text, status, source, action = fields
        if status not in {"verified", "unverified", "wrong"}:
            continue
        rows[claim] = {"guide": guide, "claim": text, "status": status,
                       "source": source, "action": action}
    return rows


def verification_quality(guide_id: str, body: str, ledger: dict[str, dict[str, str]]) -> float:
    """Small relevance multiplier; unreviewed guides remain discoverable."""
    claims = extract_claims(guide_id, body)
    if not claims:
        return 0.6
    verified = 0
    wrong = 0
    for claim in claims:
        row = ledger.get(claim.id)
        if not row or row["guide"] != guide_id or row["claim"] != claim.text:
            continue
        if row["status"] == "verified" and row["source"].startswith("https://"):
            verified += 1
        elif row["status"] == "wrong":
            wrong += 1
    if wrong:
        return 0.1
    return 0.6 + 0.4 * verified / len(claims)


__all__ = ["Claim", "claim_id", "extract_claims", "ledger_path", "read_ledger", "verification_quality"]
