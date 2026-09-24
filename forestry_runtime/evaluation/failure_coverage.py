"""List statically visible failure producers and their declared diagnosis.

This is source coverage, not the number of failures observed in a Run. Dynamic
exceptions and failures created by called libraries are counted in event traces.
"""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import json
from pathlib import Path

from shared.outcome import KNOWN_REASONS, REASONS


RAISED = {"AssetError", "ToolPreconditionError", "SubmissionRefused", "SourcePathError"}


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _field(node: ast.Dict, key: str):
    return next((value for name, value in zip(node.keys, node.values) if _literal(name) == key), None)


def _site(path: Path, root: Path, node, kind: str, code: str | None, reason: str | None):
    family = "runtime"
    parts = path.relative_to(root).parts
    if "capabilities" in parts:
        index = parts.index("capabilities")
        if index + 1 < len(parts) and not parts[index + 1].endswith(".py"):
            family = parts[index + 1]
    declared = reason if reason in REASONS else KNOWN_REASONS.get(code or "", "unknown")
    return {
        "site": f"{path.relative_to(root).as_posix()}:{node.lineno}",
        "family": family, "kind": kind, "code": code,
        "reason": declared,
        "diagnosis_source": "producer" if reason in REASONS else
                            "known_code" if code in KNOWN_REASONS else "unknown",
    }


def scan(root: Path) -> dict:
    points = []
    for path in sorted((root / "runtime").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
                call = node.exc
                name = call.func.id if isinstance(call.func, ast.Name) else None
                if name in RAISED:
                    code = next((_literal(kw.value) for kw in call.keywords if kw.arg == "code"), None)
                    reason = next((_literal(kw.value) for kw in call.keywords if kw.arg == "reason"), None)
                    if name == "SourcePathError":
                        code = "source_path_not_found"
                    points.append(_site(path, root, node, name, code, reason))
            elif isinstance(node, ast.Dict):
                failure = _field(node, "failure")
                if isinstance(failure, ast.Dict):
                    points.append(_site(
                        path, root, node, "failure_return",
                        _literal(_field(failure, "code")),
                        _literal(_field(failure, "reason")),
                    ))
    points.sort(key=lambda point: point["site"])
    return {
        "scope": "statically visible Runtime raises and literal failure results",
        "total": len(points),
        "by_family": dict(sorted(Counter(point["family"] for point in points).items())),
        "by_diagnosis_source": dict(sorted(Counter(
            point["diagnosis_source"] for point in points
        ).items())),
        "points": points,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = scan(Path(__file__).resolve().parents[1])
    if args.output:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "points"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
