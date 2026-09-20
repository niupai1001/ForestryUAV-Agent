"""Collect, verify, assemble and score the implemented evaluation baseline."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import platform
import subprocess
from typing import Iterable
import uuid

from .cases.registry import BROWSER_CASES, CASES, GATE_VERIFIERS
from .collect.api import RuntimeApiClient, collect_trial
from .collect.browser import collect_browser
from .collect.engineering import collect_idempotency, collect_permissions
from .collect.records import assemble
from .scorecard import CONFIG_FIELDS, scorecard
from .verify.ui_trial import verify_trial as verify_ui_trial


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SUITE_PATH = Path(__file__).with_name("suite.json")
ENGINEERING_COLLECTORS = {
    "gate.idempotency": collect_idempotency,
    "gate.permissions": collect_permissions,
}


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


def _trial_path(root: Path, case_id: str, repeat: int) -> Path:
    return root / f"{case_id.replace('.', '-')}-{repeat}"


def _verify_gate(case_id: str, trial: Path, configuration: dict, repeat: int) -> dict:
    module_name, function_name = GATE_VERIFIERS[case_id]
    function = getattr(importlib.import_module(module_name), function_name)
    return function(trial, configuration, repeat)


def _selected_slots(
    suite: dict, tracks: set[str] | None, cases: set[str] | None,
    repeats: set[int] | None,
) -> list[tuple[dict, int]]:
    available = set(CASES) | set(GATE_VERIFIERS) | BROWSER_CASES
    suite_cases = {item["id"]: item for item in suite["cases"]}
    if cases is not None:
        unknown = cases - available
        if unknown:
            raise ValueError(f"Cases are not registered: {', '.join(sorted(unknown))}")
        selected = cases
    else:
        selected = available
    slots = []
    for case_id in sorted(selected):
        case = suite_cases.get(case_id)
        if case is None:
            raise ValueError(f"Registered case is absent from suite.json: {case_id}")
        if tracks is not None and case["track"] not in tracks:
            continue
        count = CASES[case_id].repeats if case_id in CASES else None
        count = count or suite["repeats"][case["track"]]
        chosen = sorted(repeats) if repeats is not None else range(1, count + 1)
        for repeat in chosen:
            if type(repeat) is not int or not 1 <= repeat <= count:
                raise ValueError(f"Repeat {repeat} is outside {case_id}'s 1..{count} slots")
            slots.append((case, repeat))
    return slots


def run_baseline(
    *, root: Path, tracks: Iterable[str] | None = None,
    cases: Iterable[str] | None = None, repeats: Iterable[int] | None = None,
) -> dict:
    """Run implemented slots, resuming complete records without re-collection."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    suite = _read_json(SUITE_PATH)
    track_set = set(tracks) if tracks is not None else None
    case_set = set(cases) if cases is not None else None
    repeat_set = set(repeats) if repeats is not None else None
    known_tracks = {"agent", "engineering", "ui"}
    if track_set is not None and not track_set <= known_tracks:
        raise ValueError(f"Unknown tracks: {', '.join(sorted(track_set - known_tracks))}")
    slots = _selected_slots(suite, track_set, case_set, repeat_set)
    configs = {
        track: _load_config(root, track, PROJECT_ROOT)
        for track in {case["track"] for case, _ in slots}
    }
    record_files: list[Path] = []

    # Browser cases share one Playwright execution per repeat.
    ui_by_repeat: dict[int, set[str]] = {}
    for case, repeat in slots:
        if case["track"] == "ui":
            ui_by_repeat.setdefault(repeat, set()).add(case["id"])
            continue
        trial = _trial_path(root, case["id"], repeat)
        record_file = trial / "record.json"
        if record_file.is_file():
            record_files.append(record_file)
            continue
        if trial.exists():
            raise RuntimeError(f"Incomplete trial directory must be reviewed: {trial}")
        if case["track"] == "engineering":
            ENGINEERING_COLLECTORS[case["id"]](trial, project_root=PROJECT_ROOT)
            record = _verify_gate(case["id"], trial, configs["engineering"], repeat)
        else:
            api_key = os.environ.get("RUNTIME_API_KEY")
            if not api_key:
                raise RuntimeError("RUNTIME_API_KEY is required for agent collection")
            binding = CASES[case["id"]]
            fixture = PROJECT_ROOT / binding.fixture
            prompt_file = fixture / "prompt.txt"
            client = RuntimeApiClient(
                os.environ.get("RUNTIME_BASE_URL", "http://127.0.0.1:8010"),
                api_key, os.environ.get("RUNTIME_EVALUATOR_OWNER", "evaluator"),
                str(uuid.uuid4()),
            )
            collect_trial(
                client=client, case_id=case["id"], repeat=repeat,
                prompt=prompt_file.read_text(encoding="utf-8"),
                fixture_files=sorted(
                    path for path in fixture.iterdir()
                    if path.is_file() and path.name != prompt_file.name
                ),
                output=trial, configuration=configs["agent"],
            )
            module = importlib.import_module("evaluation.verify.core_csv_trial")
            record = module.verify_trial(
                trial, configs["agent"], repeat, gold=PROJECT_ROOT / binding.gold
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
    records = assemble(record_files, assembled_path)
    report = scorecard(suite, records, root)
    _write_json(root / "scorecard.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("evaluation/work/baseline"))
    parser.add_argument("--tracks", nargs="+", choices=("agent", "engineering", "ui"))
    parser.add_argument("--cases", nargs="+")
    parser.add_argument("--repeats", nargs="+", type=int)
    args = parser.parse_args()
    report = run_baseline(
        root=args.root, tracks=args.tracks, cases=args.cases, repeats=args.repeats
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    return {"blocked": 1, "incomplete": 2, "measured_unqualified": 0}[
        report["qualification"]
    ]


if __name__ == "__main__":
    raise SystemExit(main())
