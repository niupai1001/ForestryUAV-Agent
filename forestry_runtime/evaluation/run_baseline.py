"""Collect, verify, assemble and score the implemented evaluation baseline."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import logging
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from typing import Iterable
import uuid

from .cases.registry import (
    AGENT_TRIALS, BROWSER_CASES, CASES, CONDITIONAL_CASES, GATE_VERIFIERS,
)
from .collect.api import RuntimeApiClient, collect_trial
from .collect.browser import collect_browser
from .collect.engineering import (
    collect_idempotency, collect_permissions, collect_recovery, collect_sandbox,
)
from .collect.records import assemble
from .rules import (
    SCORING_RULES_VERSION, collection_fingerprint, fingerprint_conflict,
    read_fingerprint, write_fingerprint,
)
from .scorecard import CONFIG_FIELDS, scorecard
from .verify.ui_trial import verify_trial as verify_ui_trial


LOGGER = logging.getLogger(__name__)

# Slots that a second directory also describes, recorded by the last discovery so a
# caller can report them. This is module state rather than an attribute on
# ``_discover_records``: a function attribute disappears when the module is reloaded,
# which made a test that passes alone fail in a full run.
_LAST_SUPERSEDED: list[str] = []

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = Path(__file__).with_name("suite.json")
ENGINEERING_COLLECTORS = {
    "gate.idempotency": collect_idempotency,
    "gate.permissions": collect_permissions,
    "gate.recovery": collect_recovery,
    "gate.sandbox": collect_sandbox,
}

# The Runtime gets its credentials from `.env` through Compose; this process does not.
# Without this the agent track could never collect, because RUNTIME_API_KEY was simply
# absent -- which read as "the scoring system does not work" while the agent ran fine.
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from shared.env_file import load_env_file  # noqa: E402

load_env_file(PROJECT_ROOT / ".env")


def _read_json(path: Path) -> dict:
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        raise ValueError(f"JSON must be UTF-8 without BOM: {path}")
    value = json.loads(raw.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _engineering_configuration(project_root: Path) -> dict:
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=project_root,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    ).stdout.strip() or "git-unavailable"
    status = subprocess.run(
        ["git", "status", "--short"], cwd=project_root,
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    ).stdout
    dirty = hashlib.sha256(status.encode("utf-8")).hexdigest()[:16]
    return {
        "code_snapshot": f"{revision}+status-{dirty}",
        "model_digest": "not-applicable-engineering",
        "prompt_snapshot": "not-applicable-engineering",
        "tools_snapshot": "engineering-gates-v1",
        "dataset_version": "forestry-eval-0.1",
        "environment_snapshot": f"{platform.platform()} Python-{platform.python_version()}",
        "evaluator_version": "forestry-eval-0.1",
        "sampling": {"deterministic": True},
        "budgets": {"pytest_timeout_seconds": 300},
    }


def _load_config(root: Path, track: str, project_root: Path) -> dict:
    path = root / f"configuration-{track}.json"
    if not path.exists():
        if track != "engineering":
            raise FileNotFoundError(
                f"Freeze {track} configuration before collection: {path}"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(_engineering_configuration(project_root), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    configuration = _read_json(path)
    missing = [field for field in CONFIG_FIELDS if not configuration.get(field)]
    if missing:
        raise ValueError(
            f"Configuration lacks required fields ({', '.join(missing)}): {path}"
        )
    return configuration


def _write_json(path: Path, value) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )


def runtime_environment(container: str = "forestry-runtime") -> dict:
    """The treatment switches the Runtime is *actually* running with.

    Read from the container, not from the collecting shell.  The Runtime reads these
    two variables from its own process environment, so the container is the only
    place where the treatment is a fact; a shell variable proves nothing about it and
    may simply be unset.  That distinction is not cosmetic: the collecting shell
    normally leaves ``DOMAIN_GUIDES_ENABLED`` unset, so a shell-based reading records
    ``""`` for arm B, and the contamination check then reports every slot as belonging
    to no arm at all -- while the evidence is in fact correct.

    ``docker inspect`` is the fallback that keeps this honest when the container is
    not reachable: an unreadable environment is recorded as unreadable rather than
    guessed, and `compare_arms` treats an unrecorded arm as a reason to re-collect.
    """
    names = ("REMOTE_SENSING_PLUGINS_ENABLED", "DOMAIN_GUIDES_ENABLED")
    try:
        completed = subprocess.run(
            ["docker", "inspect", container, "--format",
             "{{range .Config.Env}}{{println .}}{{end}}"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False,
        )
    except OSError:
        completed = None
    if completed is not None and completed.returncode == 0:
        found: dict[str, str] = {}
        for line in (completed.stdout or "").splitlines():
            key, _, value = line.partition("=")
            if key in names:
                found[key] = value
        if found:
            return {name: found.get(name, "") for name in names}
    return {name: os.getenv(name, "") for name in names}


def _slot_condition(case_id: str, trial_repeat: int, repeats_per_condition: int) -> str:
    """Which declared input condition a trial index belongs to."""
    if case_id not in CASES or repeats_per_condition <= 0:
        return "normal"
    conditions = list(CASES[case_id].fixture_conditions())
    if trial_repeat <= repeats_per_condition:
        return conditions[0] if conditions else "normal"
    offset = (trial_repeat - 1) // repeats_per_condition
    if offset < len(conditions):
        return conditions[offset]
    return conditions[-1] if conditions else "normal"


def _slot_repeat(trial_repeat: int, repeats_per_condition: int) -> int:
    """The 1..n repeat number inside a condition, as the record reports it."""
    if repeats_per_condition <= 0 or trial_repeat <= repeats_per_condition:
        return trial_repeat
    return ((trial_repeat - 1) % repeats_per_condition) + 1


def _trial_path(root: Path, case_id: str, repeat: int) -> Path:
    return root / f"{case_id.replace('.', '-')}-{repeat}"


def _verify_gate(case_id: str, trial: Path, configuration: dict, repeat: int) -> dict:
    module_name, function_name = GATE_VERIFIERS[case_id]
    function = getattr(importlib.import_module(module_name), function_name)
    return function(trial, configuration, repeat)


def _selected_slots(
    suite: dict, tracks: set[str] | None, cases: set[str] | None,
    repeats: set[int] | None, shard: dict[str, list[int]] | None = None,
) -> list[tuple[dict, int]]:
    """Expand the selection into collection slots of ``(case, trial_repeat)``.

    A case may declare more than one input condition. Each condition gets its own
    slots so the harder one is actually exercised instead of being reachable only in
    principle: ``forestry.chm`` repeats 1..3 collect the normal fixture and 4..6 the
    gap fixture. The trial directory index stays unique, which keeps record
    discovery, resume and the scorecard's duplicate detection working unchanged.

    ``shard`` restricts this root to a subset of the global repeat numbers, per case,
    without renumbering anything. That is what makes parallel collection safe: two
    roots write the *same* trial directories, so a merge is a move rather than a
    translation, and the scorecard cannot see two records claiming one slot. It
    deliberately does not remap ``repeat`` to 1..n -- a shard that collected
    "supervised-4" containing the *normal* condition is exactly the mistake this
    prevents.
    """
    available = set(CASES) | set(GATE_VERIFIERS) | BROWSER_CASES
    suite_cases = {item["id"]: item for item in suite["cases"]}
    if cases is not None:
        unknown = cases - available
        if unknown:
            raise ValueError(f"Cases are not registered: {', '.join(sorted(unknown))}")
        selected = cases
    else:
        selected = available
    if shard is not None:
        unknown = set(shard) - available
        if unknown:
            raise ValueError(f"Shard names unregistered cases: {', '.join(sorted(unknown))}")
        selected = selected & set(shard)
    slots = []
    for case_id in sorted(selected):
        case = suite_cases.get(case_id)
        if case is None:
            raise ValueError(f"Registered case is absent from suite.json: {case_id}")
        if tracks is not None and case["track"] not in tracks:
            continue
        count = CASES[case_id].repeats if case_id in CASES else None
        count = count or suite["repeats"][case["track"]]
        conditions = list(CASES[case_id].fixture_conditions()) if case_id in CASES else []
        # A case with two declared conditions owns 1..2*count trial numbers: 1..count
        # collect the first condition, count+1..2*count the second. `--repeats N`
        # means "repeat N of each condition", which is how the capability set is
        # collected in one pass; a shard names absolute trial numbers instead.
        base = sorted(repeats) if repeats is not None else list(range(1, count + 1))
        owned: list[int] = []
        for offset, _condition in enumerate(conditions or ["normal"]):
            owned.extend(offset * count + repeat for repeat in base)
        if shard is not None:
            chosen = sorted(set(shard[case_id]) & set(owned))
            outside = sorted(set(shard[case_id]) - set(owned))
            if outside:
                raise ValueError(
                    f"Shard asks for {case_id} slots {outside}, outside {owned}"
                )
        else:
            chosen = owned
        for repeat in chosen:
            if type(repeat) is not int:
                raise ValueError(f"Repeat {repeat} is not an integer")
            slots.append((case, repeat))
    return slots


def _discover_records(root: Path) -> list[Path]:
    """Every trial record on disk, so a partial run still reports the whole picture.

    Scoring only the slots selected for this run made selecting one track hide the
    other track's evidence: running ``--tracks agent`` dropped the gate records and
    reported ``gates=unknown``, and running ``--tracks engineering`` dropped the
    agent records. The scorecard then described the selection rather than the
    system. ``--tracks``/``--cases`` decide what is *collected*, not what is read.

    Only per-trial records are read; ``<root>/records.json`` is the assembled output
    of a previous run and is deliberately skipped, because reading it would feed
    stale records back in alongside their replacements.

    Both documented layouts exist in practice: ``run_baseline`` writes
    ``<case>-<repeat>/``, and the manual flow in ``evaluation/README.md`` writes
    ``<case>-<repeat>`` for gates but ``<case>-<repeat>`` for UI cases. A directory
    whose name ends in its own ``case_id`` is the manual layout. When both layouts
    describe one slot the canonical ``-<repeat>`` directory wins and the other is
    reported as skipped, because refusing to score at all is worse than preferring
    the layout the runner itself maintains.
    """
    per_slot: dict[tuple[str, object], Path] = {}
    superseded: list[str] = []
    for pattern in ("*/record.json", "*/records.json"):
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # A half-written record would otherwise hide every other slot.
                continue
            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = (str(item.get("case_id")), item.get("repeat"))
                previous = per_slot.get(key)
                if previous is None:
                    per_slot[key] = path
                    continue
                if previous == path:
                    continue
                canonical = _canonical_trial_dir(str(key[0]), key[1])
                winner = previous if previous.parent.name == canonical else path
                loser = path if winner == previous else previous
                per_slot[key] = winner
                superseded.append(
                    f"{loser.relative_to(root)} (superseded by "
                    f"{winner.relative_to(root)})"
                )
    if superseded:
        LOGGER.warning(
            "records superseded by the canonical trial directory: %s",
            "; ".join(superseded),
        )
    _LAST_SUPERSEDED.clear()
    _LAST_SUPERSEDED.extend(superseded)
    return sorted(set(per_slot.values()))


def _canonical_trial_dir(case_id: str, repeat: object) -> str:
    return f"{case_id.replace('.', '-')}-{repeat}"


def _infrastructure_record(case_id: str, repeat: int, configuration: dict,
                           detail: str) -> dict:
    """Record a slot whose collection never reached the Runtime.

    A unique ``trial_id`` matters: an empty one is rejected by the scorecard as a
    duplicate, which turns an unreachable service into an apparent gate failure.
    The status is ``infra_error``, which keeps the slot in the denominator as
    ``unknown`` rather than counting it as a failure of the system under test.
    """
    return {
        "suite_version": "forestry-eval-0.1",
        "scoring_rules_version": SCORING_RULES_VERSION,
        "case_id": case_id,
        "track": "agent",
        "execution": "real_model",
        "repeat": repeat,
        "trial_id": f"infra-{case_id.replace('.', '-')}-{repeat}-{uuid.uuid4().hex[:12]}",
        "configuration": configuration,
        "status": "infra_error",
        "status_evidence": {
            "verifier": "collection-v1",
            "evidence": ["raw/collector_error.txt"],
        },
        "checks": {},
        "infrastructure_error": detail.strip()[:500],
    }


def run_baseline(
    *, root: Path, tracks: Iterable[str] | None = None,
    cases: Iterable[str] | None = None, repeats: Iterable[int] | None = None,
    force: bool = False, verify_only: bool = False,
    shard: dict[str, list[int]] | None = None,
) -> dict:
    """Run implemented slots, resuming complete records without re-collection.

    ``verify_only`` re-scores the evidence that already exists instead of collecting
    again. A rules change must not force re-running 72 model runs, and a slot whose
    collection succeeded but whose verification did not is exactly what it is for.
    """
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    suite = _read_json(SUITE_PATH)
    track_set = set(tracks) if tracks is not None else None
    case_set = set(cases) if cases is not None else None
    repeat_set = set(repeats) if repeats is not None else None
    known_tracks = {"agent", "engineering", "ui"}
    if track_set is not None and not track_set <= known_tracks:
        raise ValueError(f"Unknown tracks: {', '.join(sorted(track_set - known_tracks))}")
    slots = _selected_slots(suite, track_set, case_set, repeat_set, shard=shard)
    configs = {
        track: _load_config(root, track, PROJECT_ROOT)
        for track in {case["track"] for case, _ in slots}
    }
    record_files: list[Path] = []
    skipped_verify_only: list[str] = []

    # Browser cases share one Playwright execution per repeat.
    ui_by_repeat: dict[int, set[str]] = {}
    for case, repeat in slots:
        if case["track"] == "ui":
            ui_by_repeat.setdefault(repeat, set()).add(case["id"])
            continue
        trial = _trial_path(root, case["id"], repeat)
        record_file = trial / "record.json"
        binding_for_fingerprint = CASES.get(case["id"])
        per_condition_for_fingerprint = int(
            (binding_for_fingerprint.repeats if binding_for_fingerprint else None)
            or suite["repeats"][case["track"]]
        )
        condition_for_fingerprint = _slot_condition(
            case["id"], repeat, per_condition_for_fingerprint
        )
        expected_fingerprint = None
        if binding_for_fingerprint is not None and case["track"] == "agent":
            expected_fingerprint = collection_fingerprint(
                case_id=case["id"],
                condition=condition_for_fingerprint,
                fixture=PROJECT_ROOT / binding_for_fingerprint.fixture_conditions()[
                    condition_for_fingerprint
                ],
                configuration=configs["agent"],
            )
        if record_file.is_file():
            # A completed record is only reusable if it describes the same slot.
            # Believing it blindly is how a changed case or fixture silently mixes
            # two experiments into one scorecard.
            conflict = fingerprint_conflict(
                read_fingerprint(trial), expected_fingerprint or "",
                case_id=case["id"], repeat=repeat,
            ) if expected_fingerprint else None
            if conflict and not force:
                raise RuntimeError(conflict)
            if conflict:
                shutil.rmtree(trial)
            else:
                record_files.append(record_file)
                continue
        if trial.exists() and any(trial.iterdir()):
            if verify_only:
                # This is exactly the state --verify-only exists to recover:
                # collection succeeded, verification did not. Nothing is discarded
                # and nothing is re-collected.
                pass
            elif not force:
                # Evidence with no record is a review item: the failure happened
                # after collection, so re-collecting spends again and silently
                # replaces observations. The message names both ways out.
                raise RuntimeError(
                    f"Incomplete trial directory must be reviewed: {trial}\n"
                    f"  It holds evidence but no record.json, which means collection "
                    f"succeeded and verification failed.\n"
                    f"  Either fix the verifier and re-verify without re-collecting "
                    f"(--verify-only), or pass --force to discard this evidence and "
                    f"collect again."
                )
            else:
                shutil.rmtree(trial)
        if trial.exists() and not verify_only:
            # An empty directory is a leftover from an interrupted attempt: the
            # collector creates it first and fills it later. Treating it as review
            # work aborted an entire run and surfaced as a stale scorecard.
            trial.rmdir()
        if case["track"] == "engineering":
            ENGINEERING_COLLECTORS[case["id"]](trial, project_root=PROJECT_ROOT)
            record = _verify_gate(case["id"], trial, configs["engineering"], repeat)
        elif verify_only:
            # Re-score evidence that already exists. A slot with nothing collected is
            # skipped and left to the scorecard as ``not_run``: `--verify-only` is a
            # resume for re-scoring, not a collection mode, and silently collecting
            # here would mix the two in one command. Collection runs without the flag.
            if not (trial / "trace.json").is_file():
                skipped_verify_only.append(case["id"] + "-" + str(repeat))
                continue
            binding = CASES[case["id"]]
            per_condition = int(
                (CASES[case["id"]].repeats if case["id"] in CASES else None)
                or suite["repeats"][case["track"]]
            )
            condition = _slot_condition(case["id"], repeat, per_condition)
            module = importlib.import_module(
                AGENT_TRIALS.get(case["id"], "evaluation.verify.core_csv_trial")
            )
            extra = {"case_id": case["id"]}
            if case["id"] in CONDITIONAL_CASES:
                extra["condition"] = condition
            record = module.verify_trial(
                trial, configs["agent"], _slot_repeat(repeat, per_condition),
                gold=PROJECT_ROOT / binding.gold, **extra,
            )
        else:
            api_key = os.environ.get("RUNTIME_API_KEY")
            if not api_key:
                raise RuntimeError("RUNTIME_API_KEY is required for agent collection")
            binding = CASES[case["id"]]
            per_condition = int(
                (CASES[case["id"]].repeats if case["id"] in CASES else None)
                or suite["repeats"][case["track"]]
            )
            condition = _slot_condition(case["id"], repeat, per_condition)
            slot_repeat = _slot_repeat(repeat, per_condition)
            fixture = PROJECT_ROOT / binding.fixture_conditions()[condition]
            prompt_file = fixture / "prompt.txt"
            # The arm's switches are recorded as evidence, deliberately *not* folded
            # into the fingerprint: an operator resuming a run from a shell that
            # happens to have different variables set would otherwise invalidate
            # perfectly good evidence. `compare_arms` reads the recorded value and
            # refuses to compare roots that disagree with their declared arm.
            slot_environment = runtime_environment()
            slot_fingerprint = collection_fingerprint(
                case_id=case["id"], condition=condition, fixture=fixture,
                configuration=configs["agent"],
            )
            client = RuntimeApiClient(
                os.environ.get("RUNTIME_BASE_URL", "http://127.0.0.1:8010"),
                api_key, os.environ.get("RUNTIME_EVALUATOR_OWNER", "evaluator"),
                str(uuid.uuid4()),
            )
            collected = collect_trial(
                client=client, case_id=case["id"], repeat=slot_repeat,
                prompt=prompt_file.read_text(encoding="utf-8"),
                fixture_files=sorted(
                    path for path in fixture.iterdir()
                    if path.is_file() and path.name != prompt_file.name
                ),
                output=trial, configuration=configs["agent"],
                # Generous by default: a local model can stall for many minutes on a
                # single request, and the deadline decides whether the slot is graded
                # or thrown away as `infra_error`. Cutting a healthy Run short costs a
                # whole re-collection, so the ceiling is set well above the observed
                # worst case rather than near the median.
                timeout_seconds=int(os.getenv("EVAL_TRIAL_TIMEOUT_SECONDS", "3600")),
            )
            if collected.get("trace") is None:
                # The Runtime was unreachable. Record the slot as an infrastructure
                # failure with a unique id instead of letting the verifier build a
                # record with an empty trial id, which the scorecard rejected as a
                # duplicate and reported as a gate failure.
                record = _infrastructure_record(
                    case["id"], slot_repeat, configs["agent"],
                    collected.get("error") or "",
                )
            else:
                module = importlib.import_module(
                    AGENT_TRIALS.get(case["id"], "evaluation.verify.core_csv_trial")
                )
                extra = {"case_id": case["id"]}
                if case["id"] in CONDITIONAL_CASES:
                    extra["condition"] = condition
                record = module.verify_trial(
                    trial, configs["agent"], slot_repeat,
                    gold=PROJECT_ROOT / binding.gold, **extra,
                )
            # Written only once the slot is complete. A fingerprint file inside a
            # half-collected trial directory makes the resume check read it as
            # "evidence with no record" and refuse to continue.
            if slot_fingerprint:
                write_fingerprint(
                    trial, slot_fingerprint,
                    details={
                        "case_id": case["id"], "condition": condition,
                        "trial_repeat": repeat, "slot_repeat": slot_repeat,
                        "runtime_environment": slot_environment,
                    },
                )
        _write_json(record_file, record)
        record_files.append(record_file)

    for repeat, selected_ui in sorted(ui_by_repeat.items()):
        trial = root / f"ui-repeat-{repeat}"
        record_file = trial / "records.json"
        if record_file.is_file():
            record_files.append(record_file)
            continue
        if trial.exists():
            raise RuntimeError(f"Incomplete trial directory must be reviewed: {trial}")
        collect_browser(trial, project_root=PROJECT_ROOT)
        ui_records = [
            record for record in verify_ui_trial(trial, configs["ui"], repeat)
            if record["case_id"] in selected_ui
        ]
        _write_json(record_file, ui_records)
        record_files.append(record_file)

    assembled_path = root / "records.json"
    records = assemble(_discover_records(root), assembled_path)
    report = scorecard(suite, records, root)
    if skipped_verify_only:
        # Reported, not hidden: an operator who meant to re-score everything needs to
        # know which slots had no evidence to re-score.
        report["verify_only_skipped"] = skipped_verify_only
    _write_json(root / "scorecard.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("evaluation/work/baseline"))
    parser.add_argument("--tracks", nargs="+", choices=("agent", "engineering", "ui"))
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--repeats", nargs="+", type=int)
    parser.add_argument(
        "--force", action="store_true",
        help="discard a trial directory that holds evidence but no record",
    )
    parser.add_argument(
        "--verify-only", action="store_true",
        help=(
            "re-score collected evidence instead of collecting again; use after a "
            "verifier or rules change, or for a slot whose collection succeeded but "
            "whose verification did not"
        ),
    )
    parser.add_argument(
        "--shard", type=Path,
        help=(
            "JSON file naming the absolute trial numbers this root owns, per case, "
            "e.g. {\"capability.supervised\": [1,2,3]}. Two roots may then collect in "
            "parallel and be merged by moving directories: no slot is collected twice "
            "and no record claims another's trial id."
        ),
    )
    args = parser.parse_args()
    shard = None
    if args.shard:
        shard = json.loads(args.shard.read_text(encoding="utf-8"))
        if not isinstance(shard, dict) or not all(
            isinstance(value, list) for value in shard.values()
        ):
            parser.error("--shard must be a JSON object of case id -> list of trial numbers")
    report = run_baseline(
        root=args.root, tracks=args.tracks, cases=args.cases,
        repeats=args.repeats, force=args.force, verify_only=args.verify_only,
        shard=shard,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return {"blocked": 1, "incomplete": 2, "measured_unqualified": 0}[
        report["qualification"]
    ]


if __name__ == "__main__":
    raise SystemExit(main())
