"""Failure records that outlive the process that produced them.

A failure kept in process memory cannot answer the question a resumed Run asks:
after a restart, a compaction, or a recovered job, "has this failed before" was
unanswerable, so the Runtime re-executed a call it had already proven hopeless.

Records are kept per workspace in JSON next to ``plan.json``, which is what the
observation ledger already does. That keeps one durability story in ``.runtime/``
instead of adding a second database for one table, and the volume here is hundreds
of records, not millions -- the expensive property is that a record survives the
process, not that it is indexed.
"""
from __future__ import annotations

import json
from pathlib import Path
import time

from .models import FailureEvidence

RECORD_LIMIT = 400


class FailureStore:
    """Record, look up and query the failures one workspace has produced."""

    def __init__(self, workspace: Path | str | None = None):
        self.workspace = Path(workspace) if workspace else None
        self.path = (
            self.workspace / ".runtime" / "failure-evidence.json" if self.workspace else None
        )
        #: Without a workspace there is nowhere durable to write, so records stay in
        #: this process. A Run that has no workspace cannot resume anyway, and refusing
        #: to work at all would be worse than working with a shorter memory.
        self.memory_only = self.path is None
        self._memory: dict = {"records": {}}

    # ------------------------------------------------------------------ storage

    def _load(self) -> dict:
        if self.memory_only:
            return self._memory
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"records": {}}
        if not isinstance(data, dict) or not isinstance(data.get("records"), dict):
            return {"records": {}}
        return data

    def _save(self, data: dict) -> None:
        if self.memory_only:
            self._memory = data
            return
        records = data.get("records") or {}
        if len(records) > RECORD_LIMIT:
            ordered = sorted(
                records.items(),
                key=lambda item: float((item[1] or {}).get("last_at") or 0),
                reverse=True,
            )[:RECORD_LIMIT]
            data["records"] = dict(ordered)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
        )

    # ---------------------------------------------------------------- recording

    def record(self, evidence: FailureEvidence) -> FailureEvidence:
        """Store one failure, folding repeats of the same finding into one record.

        The identity is strategy + resource + version, not the call's arrival order:
        the tenth identical failure is not a tenth fact, it is the same fact observed
        again, and collapsing it is what keeps "how many times" from becoming the
        control rule.
        """
        data = self._load()
        records = data["records"]
        prior = records.get(evidence.id)
        if prior:
            merged = FailureEvidence.from_dict(prior)
            merged.occurrences = int(merged.occurrences or 0) + 1
            merged.last_at = time.time()
            if evidence.evidence_ids:
                for item in evidence.evidence_ids:
                    if item not in merged.evidence_ids:
                        merged.evidence_ids.append(item)
            records[evidence.id] = merged.as_dict()
            self._save(data)
            return merged
        records[evidence.id] = evidence.as_dict()
        self._save(data)
        return evidence

    # ----------------------------------------------------------------- querying

    def get(self, evidence_id: str) -> FailureEvidence | None:
        raw = (self._load().get("records") or {}).get(str(evidence_id or ""))
        return FailureEvidence.from_dict(raw) if raw else None

    def all(self) -> list[FailureEvidence]:
        records = self._load().get("records") or {}
        return [FailureEvidence.from_dict(item) for item in records.values()]

    def for_resource(self, resource_key: str) -> list[FailureEvidence]:
        return [item for item in self.all() if item.resource_key == resource_key]

    def for_strategy(self, strategy_id: str) -> list[FailureEvidence]:
        return [item for item in self.all() if item.strategy_id == strategy_id]

    def active(self, limit: int = 20) -> list[FailureEvidence]:
        """The most recent failures, for injecting into a request.

        Bounded because this text is paid for on every model request; the point is
        that the model can see which assumptions are already falsified, which a
        handful of records achieves.
        """
        items = sorted(self.all(), key=lambda item: float(item.last_at or 0), reverse=True)
        return items[:max(1, limit)]

    def clear(self) -> None:
        self._save({"records": {}})


class ResourceVersions:
    """Per-resource version counters, replacing one global evidence generation.

    The global counter was the actual defect behind "unrelated evidence reopens a
    failure": any successful observation bumped it, and every recorded failure's
    fingerprint contained it, so one observation unlocked all of them. Here a version
    moves only for the resource the observation was about.
    """

    def __init__(self, versions: dict[str, int] | None = None):
        self._versions: dict[str, int] = dict(versions or {})

    def version_of(self, resource_key: str | None) -> str:
        if not resource_key:
            # A failure about no particular resource is versioned against the whole
            # workspace, so it is unlocked only by evidence that is genuinely new.
            return str(self._versions.get("*", 0))
        return str(self._versions.get(resource_key, 0))

    def bump(self, resource_key: str | None) -> str:
        key = resource_key or "*"
        self._versions[key] = int(self._versions.get(key, 0)) + 1
        return str(self._versions[key])

    def as_dict(self) -> dict:
        return dict(self._versions)

    def load(self, versions: dict) -> None:
        self._versions = {str(k): int(v) for k, v in (versions or {}).items()}


__all__ = ["FailureStore", "RECORD_LIMIT", "ResourceVersions"]
