"""A short, revisable work plan whose claims must point at evidence.

The observed failure this exists to prevent: a Run knew its input was three-band RGB,
produced a candidate route that needed near-infrared, and attributed the route to
guides it had never opened. Nothing in the Run connected "what has been observed",
"what the candidate method requires" and "what is still missing", so the three could
drift apart without any single step being obviously wrong.

Two rules make the drift detectable:

* **Every judgment names its evidence.** An input condition cites the observation
  that produced it; a candidate cites the observation or the retrieved document that
  supports its precondition. The Runtime -- not the model -- assigns those ids, and
  rejects a citation that was never issued.
* **A catalogue entry is not a document.** Opening the guide catalogue says which
  documents exist. Only a body that was actually returned can be cited as read, so
  "according to the guide" is checkable instead of rhetorical.

The plan is a working note, not a second source of truth: the observations stay in
the event stream and the tool ledger, and the plan only points at them. Every revision
is kept, because "why did the method change" is answered by the difference between two
revisions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import time
from typing import Any

from .storage import AssetError


MAX_TEXT = 2000
MAX_ITEMS = 16
LEDGER_LIMIT = 400

#: A citation the Runtime issued for one tool call that actually returned.
OBSERVATION_PREFIX = "obs_"
#: A citation for a document body that was actually returned to the model.
DOCUMENT_PREFIXES = ("guide://", "knowledge://", "project://")


def _clean(value: Any, limit: int = MAX_TEXT) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _clean_list(values, limit: int = MAX_ITEMS) -> list:
    output: list = []
    for item in values or []:
        if item is None:
            continue
        if isinstance(item, str):
            text = _clean(item)
            if text and text not in output:
                output.append(text)
        elif item not in output:
            output.append(item)
        if len(output) >= limit:
            break
    return output


class PlanError(AssetError):
    """A plan change that cannot be accepted, with the reason it was refused."""

    def __init__(self, message: str, **details):
        super().__init__(message)
        self.failure_details = {
            "code": details.pop("code", "plan_rejected"),
            "operation_started": False,
            "side_effects": "none",
            **details,
        }


# ---------------------------------------------------------------------------
# Evidence ledger
# ---------------------------------------------------------------------------


def observation_id(tool: str, arguments: dict, version: str = "") -> str:
    """A stable id for one tool call, derived from the call rather than a counter.

    Derived rather than sequential so the same call answered from a cache or repeated
    after a restart keeps its identity, and a citation cannot accidentally point at a
    different call.
    """
    payload = json.dumps(
        {"tool": tool, "arguments": arguments, "version": version},
        ensure_ascii=False, sort_keys=True, default=str,
    )
    return OBSERVATION_PREFIX + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


class ObservationLedger:
    """Which observations and document bodies this workspace can actually cite.

    Bounded and workspace-scoped. ``record`` is called by the tool dispatcher for
    every call that returned, so the ledger is a record of what happened rather than
    of what the model says happened.
    """

    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"observations": {}, "documents": {}}
        if not isinstance(data, dict):
            return {"observations": {}, "documents": {}}
        data.setdefault("observations", {})
        data.setdefault("documents", {})
        return data

    def _save(self, data: dict) -> None:
        observations = data.get("observations") or {}
        if len(observations) > LEDGER_LIMIT:
            ordered = sorted(
                observations.items(),
                key=lambda item: float((item[1] or {}).get("at") or 0),
                reverse=True,
            )[:LEDGER_LIMIT]
            data["observations"] = dict(ordered)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8",
        )

    def record(
        self, tool: str, arguments: dict, *, ok: bool, summary: str = "",
        documents: list[str] | None = None, version: str = "",
    ) -> str:
        identifier = observation_id(tool, arguments, version)
        data = self._load()
        data["observations"][identifier] = {
            "id": identifier, "tool": tool, "ok": bool(ok),
            "arguments": {
                key: (value if isinstance(value, (str, int, float, bool)) or value is None
                      else json.dumps(value, ensure_ascii=False, default=str)[:300])
                for key, value in list((arguments or {}).items())[:12]
            },
            "summary": _clean(summary, 300),
            "at": time.time(),
        }
        for document in documents or []:
            text = _clean(document, 300)
            if not text:
                continue
            entry = data["documents"].get(text) or {"id": text, "reads": 0}
            entry["reads"] = int(entry.get("reads") or 0) + 1
            entry["last_read_at"] = time.time()
            entry["observation_id"] = identifier
            data["documents"][text] = entry
        self._save(data)
        return identifier

    def known(self) -> dict:
        return self._load()

    def documents_returned(self, document_ids) -> tuple[list[str], list[str]]:
        """Split citations into those whose body was returned and those only named."""
        documents = (self._load().get("documents") or {})
        known, unknown = [], []
        for raw in document_ids or []:
            text = _clean(raw, 300)
            if not text:
                continue
            (known if text in documents else unknown).append(text)
        return known, unknown

    def observations_exist(self, identifiers) -> tuple[list[str], list[str]]:
        observations = (self._load().get("observations") or {})
        known, unknown = [], []
        for raw in identifiers or []:
            text = _clean(raw, 200)
            if not text:
                continue
            (known if text in observations else unknown).append(text)
        return known, unknown


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass
class WorkPlan:
    objective: str = ""
    outputs: list[str] = field(default_factory=list)
    inputs: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    open_questions: list[dict] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    selected: str = ""
    selection_reason: str = ""
    revision: int = 0
    updated_at: float = 0.0

    def as_dict(self) -> dict:
        return {
            "revision": self.revision,
            "objective": self.objective,
            "outputs": list(self.outputs),
            "inputs": list(self.inputs),
            "candidates": list(self.candidates),
            "open_questions": list(self.open_questions),
            "acceptance": list(self.acceptance),
            "selected": self.selected,
            "selection_reason": self.selection_reason,
            "updated_at": self.updated_at,
        }

    @property
    def empty(self) -> bool:
        return not any((
            self.objective, self.outputs, self.inputs, self.candidates,
            self.open_questions, self.acceptance,
        ))

    def render(self) -> str:
        """A compact rendering for the model request, bounded on purpose.

        The plan is supplied on every request, so its cost is paid on every request;
        it states the decisions and the evidence that carries them, not the prose
        that led there.
        """
        if self.empty:
            return ""
        lines = ["Work plan (revision %d). Every condition and candidate below cites "
                 "the evidence it rests on." % self.revision]
        if self.objective:
            lines.append(f"Objective: {_clean(self.objective, 600)}")
        if self.outputs:
            lines.append("Outputs: " + "; ".join(_clean(item, 200) for item in self.outputs[:8]))
        if self.inputs:
            lines.append("Observed conditions:")
            for item in self.inputs[:12]:
                lines.append(
                    f"- {_clean(item.get('condition'), 200)} "
                    f"[{_clean(item.get('evidence'), 200)}]"
                )
        if self.candidates:
            lines.append("Candidate methods:")
            for item in self.candidates[:8]:
                mark = " (selected)" if item.get("id") == self.selected else (
                    f" ({item.get('status')})" if item.get("status") else ""
                )
                lines.append(
                    f"- {_clean(item.get('id'), 60)}: {_clean(item.get('method'), 200)}"
                    f"{mark}; requires {_clean(item.get('precondition'), 200)}; "
                    f"evidence {_clean(item.get('evidence'), 200)}"
                )
        if self.open_questions:
            lines.append("Missing evidence:")
            for item in self.open_questions[:12]:
                lines.append(
                    f"- {_clean(item.get('question'), 200)} "
                    f"(blocks: {_clean(item.get('blocks'), 120)}; "
                    f"resolved by: {_clean(item.get('resolve_with'), 120)})"
                )
        if self.acceptance:
            lines.append("Acceptance: " + "; ".join(_clean(item, 200) for item in self.acceptance[:8]))
        if self.selected and self.selection_reason:
            lines.append(
                f"Selected {_clean(self.selected, 60)} because "
                f"{_clean(self.selection_reason, 400)}"
            )
        return "\n".join(lines)


class PlanStore:
    """Read, revise and version the plan for one workspace."""

    def __init__(self, workspace: Path, ledger: ObservationLedger | None = None):
        self.workspace = Path(workspace)
        self.path = self.workspace / ".runtime" / "plan.json"
        self.history_path = self.workspace / ".runtime" / "plan-history.jsonl"
        self.ledger = ledger or ObservationLedger(
            self.workspace / ".runtime" / "observations.json"
        )

    # ------------------------------------------------------------------ state

    def load(self) -> WorkPlan:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return WorkPlan()
        if not isinstance(data, dict):
            return WorkPlan()
        plan = WorkPlan(
            objective=_clean(data.get("objective")),
            outputs=_clean_list(data.get("outputs")),
            inputs=[item for item in (data.get("inputs") or []) if isinstance(item, dict)],
            candidates=[item for item in (data.get("candidates") or []) if isinstance(item, dict)],
            open_questions=[item for item in (data.get("open_questions") or []) if isinstance(item, dict)],
            acceptance=_clean_list(data.get("acceptance")),
            selected=_clean(data.get("selected"), 60),
            selection_reason=_clean(data.get("selection_reason")),
            revision=int(data.get("revision") or 0),
            updated_at=float(data.get("updated_at") or 0),
        )
        return plan

    def _write(self, plan: WorkPlan) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(plan.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8",
        )

    def history(self, limit: int = 20) -> list[dict]:
        try:
            lines = self.history_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries = []
        for line in lines[-max(1, limit):]:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        return entries

    # ---------------------------------------------------------------- revision

    def revise(
        self, *, objective: str | None = None, outputs=None, inputs=None,
        candidates=None, open_questions=None, acceptance=None, selected: str | None = None,
        reason: str | None = None, replace: bool = False,
    ) -> WorkPlan:
        """Apply one revision, refusing claims the ledger cannot support.

        ``replace`` clears the sections that were not supplied; without it the given
        sections replace only themselves, so the model does not have to restate the
        objective to add one candidate.
        """
        current = WorkPlan() if replace else self.load()
        if objective is not None:
            current.objective = _clean(objective)
        if outputs is not None:
            current.outputs = _clean_list(outputs)
        if acceptance is not None:
            current.acceptance = _clean_list(acceptance)
        if inputs is not None:
            current.inputs = self._validated_inputs(inputs)
        if candidates is not None:
            current.candidates = self._validated_candidates(candidates)
        if open_questions is not None:
            current.open_questions = self._validated_questions(open_questions)

        if selected is not None:
            target = _clean(selected, 60)
            known = {str(item.get("id") or "") for item in current.candidates}
            if target and target not in known:
                raise PlanError(
                    "The selected method is not one of the plan's candidates.",
                    code="plan_unknown_candidate", selected=target,
                    candidates=sorted(name for name in known if name),
                )
            changed = target != current.selected
            # A method change is the decision the whole plan exists to justify, so it
            # is the one that cannot be made without stating what changed the mind.
            if changed and not _clean(reason):
                raise PlanError(
                    "Selecting a different method requires `reason`: state the "
                    "observation or source that changed the decision.",
                    code="plan_selection_without_reason",
                    previous=current.selected, selected=target,
                )
            current.selected = target
            if changed:
                current.selection_reason = _clean(reason)
        elif reason:
            current.selection_reason = _clean(reason)

        current.revision += 1
        current.updated_at = time.time()
        self._write(current)
        self._append_history(current)
        return current

    # -------------------------------------------------------------- validation

    def _validated_inputs(self, raw) -> list[dict]:
        items = []
        for entry in _clean_list(raw):
            if not isinstance(entry, dict):
                raise PlanError(
                    "An observed condition must be an object with `condition` and "
                    "`evidence`.", code="plan_invalid_input",
                )
            condition = _clean(entry.get("condition"))
            evidence = _clean(entry.get("evidence"), 200)
            if not condition or not evidence:
                raise PlanError(
                    "An observed condition needs both `condition` and the `evidence` "
                    "it was observed from.", code="plan_input_without_evidence",
                    condition=condition,
                )
            self._require_evidence(evidence, "input", condition)
            items.append({
                "condition": condition,
                "evidence": evidence,
                "value": _clean(entry.get("value"), 400),
            })
        return items

    def _validated_candidates(self, raw) -> list[dict]:
        items = []
        for entry in _clean_list(raw):
            if not isinstance(entry, dict):
                raise PlanError(
                    "A candidate must be an object with `id`, `method`, `precondition` "
                    "and `evidence`.", code="plan_invalid_candidate",
                )
            identifier = _clean(entry.get("id"), 60)
            method = _clean(entry.get("method"))
            precondition = _clean(entry.get("precondition"))
            evidence = _clean(entry.get("evidence"), 300)
            if not identifier or not method:
                raise PlanError(
                    "A candidate needs a short `id` and the `method` it names.",
                    code="plan_invalid_candidate",
                )
            if not precondition:
                raise PlanError(
                    f"Candidate {identifier} does not state its precondition, so "
                    "nothing can be checked against the observed inputs.",
                    code="plan_candidate_without_precondition", candidate=identifier,
                )
            # A candidate is a claim that some method applies here; a claim without a
            # source is the "according to the guide" failure in a structured form.
            if not evidence:
                raise PlanError(
                    f"Candidate {identifier} cites no evidence. State the observation "
                    "or the retrieved document that supports the precondition.",
                    code="plan_candidate_without_evidence", candidate=identifier,
                )
            self._require_evidence(evidence, "candidate", identifier)
            items.append({
                "id": identifier,
                "method": method,
                "precondition": precondition,
                "evidence": evidence,
                "status": _clean(entry.get("status"), 40) or "candidate",
                "notes": _clean(entry.get("notes"), 400),
            })
        return items

    def _validated_questions(self, raw) -> list[dict]:
        items = []
        for entry in _clean_list(raw):
            if isinstance(entry, str):
                entry = {"question": entry}
            if not isinstance(entry, dict):
                raise PlanError("A missing-evidence item must be an object or a string.",
                                code="plan_invalid_question")
            question = _clean(entry.get("question"))
            if not question:
                raise PlanError("A missing-evidence item needs `question`.",
                                code="plan_invalid_question")
            items.append({
                "question": question,
                "blocks": _clean(entry.get("blocks"), 200),
                "resolve_with": _clean(entry.get("resolve_with"), 200),
            })
        return items

    def _require_evidence(self, citation: str, kind: str, label: str) -> None:
        """Accept only citations the Runtime actually issued.

        Three kinds are recognized, and the difference between the last two is the
        point: an observation id was returned by a tool call, a ``guide://`` or
        ``project://`` id was returned as a *body*. A catalogue entry matches none of
        them, so "the guide says so" cannot be written down as evidence.
        """
        text = citation.strip()
        tokens = [token for token in text.replace(";", " ").replace(",", " ").split() if token]
        if not tokens:
            raise PlanError(
                f"The {kind} {label} cites no evidence.", code="plan_candidate_without_evidence",
            )
        documents = [token for token in tokens if token.startswith(DOCUMENT_PREFIXES)]
        observations = [token for token in tokens if token.startswith(OBSERVATION_PREFIX)]
        if not documents and not observations:
            raise PlanError(
                f"The {kind} {label} cites '{text}', which is not something this Run "
                "issued. Cite an observation id returned by a tool call (obs_...), or a "
                "document body the tool returned (guide://... or project://...). A "
                "catalogue listing is not a document body.",
                code="plan_evidence_unknown", citation=text,
            )
        known_documents, unknown_documents = self.ledger.documents_returned(documents)
        if unknown_documents:
            raise PlanError(
                f"The {kind} {label} cites {unknown_documents[0]!r} as a source, but no "
                "tool returned that document's text. Reading the catalogue says which "
                "documents exist; it is not evidence of their content. Open the document "
                "first, then cite it.",
                code="plan_document_not_read", citation=unknown_documents[0],
                documents_read=sorted(
                    (self.ledger.known().get("documents") or {}).keys()
                )[:10],
            )
        known_observations, unknown_observations = self.ledger.observations_exist(observations)
        if unknown_observations:
            raise PlanError(
                f"The {kind} {label} cites observation {unknown_observations[0]!r}, which "
                "this Run did not produce. Use an observation id returned by a tool result.",
                code="plan_observation_unknown", citation=unknown_observations[0],
            )

    def _append_history(self, plan: WorkPlan) -> None:
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(plan.as_dict(), ensure_ascii=False, default=str) + "\n")


__all__ = [
    "DOCUMENT_PREFIXES",
    "MAX_ITEMS",
    "OBSERVATION_PREFIX",
    "ObservationLedger",
    "PlanError",
    "PlanStore",
    "WorkPlan",
    "observation_id",
]
