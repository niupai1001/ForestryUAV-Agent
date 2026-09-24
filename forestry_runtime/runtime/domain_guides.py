"""Domain guides: a short always-visible catalogue, full text on demand.

Domain rules are large and only occasionally relevant, so they must not sit in
every request.  Each guide carries metadata; the request context receives only the
catalogue (id, title, summary, tags), and the model loads a guide when it needs
the detail.

Discovery deliberately does not hinge on a handful of hand-written keywords: the
catalogue is always visible in full, and :func:`match_guides` scores on metadata
tokens so an unanticipated phrasing still finds the right guide.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re

from .guide_verification import read_ledger, verification_quality

GUIDE_ROOT_ENV = "DOMAIN_GUIDE_ROOTS"
DEFAULT_GUIDE_ROOT = "/knowledge/guides"
_MAX_GUIDE_CHARS = 120_000


class GuideError(Exception):
    """A guide request that cannot be satisfied from the catalogue."""


@dataclass(frozen=True)
class DomainGuide:
    id: str
    title: str
    summary: str
    tags: tuple[str, ...]
    applies_to: tuple[str, ...]
    version: str
    path: Path
    body: str
    citation: str

    def catalogue_entry(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "summary": self.summary,
            "tags": list(self.tags),
            "applies_to": list(self.applies_to),
        }


def guide_roots() -> list[Path]:
    configured = os.getenv(GUIDE_ROOT_ENV, "")
    roots = [
        Path(item).expanduser() for item in configured.split(os.pathsep) if item.strip()
    ]
    if not roots:
        roots = [Path(DEFAULT_GUIDE_ROOT)]
    # A repository-relative fallback keeps local (non-container) runs working:
    # `runtime/domain_guides.py` -> `runtime/` -> the checkout root.
    package_dir = Path(__file__).resolve().parent
    repository_root = package_dir.parent if package_dir.name == "runtime" else package_dir
    local = repository_root / "knowledge" / "guides"
    if local.is_dir() and local not in roots:
        roots.append(local)
    return [root for root in roots if root.is_dir()]


_FRONT_MATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_LIST_ITEM = re.compile(r"^\s*-\s*(.+?)\s*$")


def _parse_front_matter(text: str) -> tuple[dict, str]:
    match = _FRONT_MATTER.match(text)
    if not match:
        return {}, text
    meta: dict = {}
    for line in match.group(1).splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        raw = raw.strip()
        if not key:
            continue
        if raw.startswith("[") and raw.endswith("]"):
            values = [
                item.strip().strip("'\"")
                for item in raw[1:-1].split(",")
            ]
            meta[key] = [item for item in values if item]
        else:
            meta[key] = raw.strip("'\"")
    return meta, text[match.end():]


def _as_tuple(value) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return tuple(item for item in (part.strip() for part in value.split(",")) if item)
    return tuple(str(item).strip() for item in value if str(item).strip())


def load_guides(roots: list[Path] | None = None) -> list[DomainGuide]:
    """Load every guide from the configured roots, newest definition winning."""
    guides: dict[str, DomainGuide] = {}
    for root in (roots if roots is not None else guide_roots()):
        for path in sorted(root.glob("*.md")):
            if path.name.casefold() == "readme.md":
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            meta, body = _parse_front_matter(text)
            guide_id = str(meta.get("id") or path.stem).strip()
            if not guide_id or guide_id in guides:
                continue
            guides[guide_id] = DomainGuide(
                id=guide_id,
                title=str(meta.get("title") or guide_id).strip(),
                summary=str(meta.get("summary") or "").strip(),
                tags=_as_tuple(meta.get("tags")),
                applies_to=_as_tuple(meta.get("applies_to")),
                version=str(meta.get("version") or "1"),
                path=path,
                body=body.strip(),
                citation=f"guide://{guide_id}@{meta.get('version') or '1'}",
            )
    return [guides[key] for key in sorted(guides)]


def guide_catalogue(roots: list[Path] | None = None) -> list[dict]:
    return [guide.catalogue_entry() for guide in load_guides(roots)]


def find_guide(guide_id: str, roots: list[Path] | None = None) -> DomainGuide:
    wanted = str(guide_id or "").strip()
    for guide in load_guides(roots):
        if guide.id == wanted:
            return guide
    raise GuideError(
        f"Unknown domain guide: {wanted or '(empty)'}. "
        "Use the catalogue ids listed in the request context."
    )


def _tokens(text: str) -> set[str]:
    lowered = str(text or "").casefold()
    latin = set(re.findall(r"[a-z0-9][a-z0-9_-]{1,}", lowered))
    chinese = re.findall(r"[\u3400-\u9fff]", lowered)
    bigrams = {
        "".join(chinese[index:index + 2])
        for index in range(max(0, len(chinese) - 1))
    }
    return latin | set(chinese) | bigrams


def match_guides(query: str, *, limit: int = 3, roots: list[Path] | None = None) -> list[DomainGuide]:
    """Rank guides by metadata overlap.

    Scoring is over id, title, summary, tags and applies_to, so a question phrased
    with words the maintainer never wrote still matches on tokens that do appear.
    """
    terms = _tokens(query)
    if not terms:
        return []
    latin_terms = set(re.findall(r"[a-z][a-z0-9_-]{2,}", query.casefold()))
    scored: list[tuple[float, DomainGuide]] = []
    ledger = read_ledger()
    for guide in load_guides(roots):
        fields = {
            "tags": _tokens(" ".join(guide.tags)),
            "applies_to": _tokens(" ".join(guide.applies_to)),
            "title": _tokens(guide.title),
            "summary": _tokens(guide.summary),
            "id": _tokens(guide.id.replace("-", " ")),
        }
        score = (
            3.0 * len(terms & fields["tags"])
            + 2.0 * len(terms & fields["applies_to"])
            + 2.0 * len(terms & fields["title"])
            + 1.0 * len(terms & fields["summary"])
            + 1.0 * len(terms & fields["id"])
        )
        # Exact technical acronyms are much more discriminative than shared
        # Chinese characters (e.g. PROSAIL versus generic leaf-area guides).
        score += 30.0 * len(latin_terms & (
            fields["tags"] | fields["title"] | fields["id"]
        ))
        if score > 0:
            scored.append((score * verification_quality(guide.id, guide.body, ledger), guide))
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [guide for _, guide in scored[: max(1, min(10, limit))]]


__all__ = [
    "DomainGuide",
    "GuideError",
    "find_guide",
    "guide_catalogue",
    "guide_roots",
    "load_guides",
    "match_guides",
]
