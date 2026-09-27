"""Which verifier answers for which artifact, and what happens when none does.

Registration is by ``artifact_kind`` (falling back to media type), because "what
would have to be true of this thing" differs by what the thing is: a raster product,
a table, a report. A verifier that claims to speak for everything is how a check
starts passing on artifacts it has no opinion about.

Two rules are enforced here rather than trusted to the verifier:

* **A verifier that raises does not pass.** It is recorded as ``not_run`` with the
  exception in the detail. An exploding check must never read as agreement; the
  alternative is a verification layer whose failures are silent and whose successes
  are the only thing anyone sees.
* **No verifier means no verdict, not a free pass.** ``verdicts()`` returns an empty
  list and the caller turns that into an explicit ``not_run`` check.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .models import Check, _text

#: A verifier receives the artifact descriptor and the task text, and returns one
#: semantic ``Check``. It is deliberately synchronous: verification reads what the run
#: already produced, so there is nothing to await.
VerifierFn = Callable[[dict, str], Check]


@dataclass(frozen=True)
class Verifier:
    name: str
    kinds: tuple[str, ...] = ()
    media: tuple[str, ...] = ()
    fn: VerifierFn | None = None

    def covers(self, artifact: dict) -> bool:
        kind = str(artifact.get("artifact_kind") or "").casefold()
        media = str(artifact.get("media_type") or "").casefold()
        if not self.kinds and not self.media:
            # Registered for everything: the question is not "what kind is this" but
            # "is there anything here to check", which only the verifier can answer.
            return True
        if self.kinds and kind and kind in {item.casefold() for item in self.kinds}:
            return True
        if self.media and media and media in {item.casefold() for item in self.media}:
            return True
        return False


class VerifierRegistry:
    """The set of things that can say whether an artifact answers a task."""

    def __init__(self) -> None:
        self._verifiers: list[Verifier] = []

    def register(
        self, name: str, fn: VerifierFn, *, kinds: tuple[str, ...] = (),
        media: tuple[str, ...] = (),
    ) -> Verifier:
        verifier = Verifier(name=name, kinds=tuple(kinds), media=tuple(media), fn=fn)
        self._verifiers = [item for item in self._verifiers if item.name != name]
        self._verifiers.append(verifier)
        return verifier

    def unregister(self, name: str) -> None:
        self._verifiers = [item for item in self._verifiers if item.name != name]

    @property
    def names(self) -> list[str]:
        return [item.name for item in self._verifiers]

    def verifier_for(self, artifact: dict) -> Verifier | None:
        for verifier in self._verifiers:
            if verifier.covers(artifact):
                return verifier
        return None

    def verdicts(self, artifacts: list[dict], task: str = "") -> list[Check]:
        """One semantic check per artifact that has a verifier.

        Artifacts with no verifier are simply absent from the result -- the caller
        decides what that means, and it means ``not_run``.
        """
        checks: list[Check] = []
        if not self._verifiers:
            return checks
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                continue
            verifier = self.verifier_for(artifact)
            if verifier is None or verifier.fn is None:
                continue
            name = _text(artifact.get("name") or artifact.get("asset_id"), 120)
            try:
                produced = verifier.fn(artifact, task)
            except Exception as exc:  # noqa: BLE001 - a raising verifier must not pass
                checks.append(Check(
                    layer="semantic", name=f"{verifier.name}:{name}", outcome="not_run",
                    detail=f"verifier raised {type(exc).__name__}: {str(exc)[:200]}",
                ))
                continue
            if produced is None:
                continue
            # One verifier may answer several questions about one artifact. An empty
            # list is "this artifact is not mine", which is not the same as "nothing
            # to check" -- that is a ``not_run`` check, and it has to be reported.
            for check in produced if isinstance(produced, list) else [produced]:
                if not isinstance(check, Check):
                    continue
                if not check.name.startswith(verifier.name):
                    check.name = f"{verifier.name}:{check.name}"
                check.layer = "semantic"
                checks.append(check)
        return checks


__all__ = ["Verifier", "VerifierFn", "VerifierRegistry"]
