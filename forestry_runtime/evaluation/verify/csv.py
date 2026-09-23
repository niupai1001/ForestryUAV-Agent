"""Strict CSV comparison by business key, including types, nulls and numbers."""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from .base import Verdict


VERIFIER = "csv-keys-values-v1"


def _is_null(value: Any, null_values: set[str]) -> bool:
    return value is None or (isinstance(value, str) and value.strip() in null_values)


def _coerce(value: Any, kind: str, null_values: set[str]) -> Any:
    if _is_null(value, null_values):
        return None
    if kind == "string":
        return str(value)
    if kind == "integer":
        text = str(value).strip()
        number = float(text)
        if not math.isfinite(number) or not number.is_integer():
            raise ValueError(f"{value!r} is not an integer")
        return int(number)
    if kind == "number":
        number = float(str(value).strip())
        if not math.isfinite(number):
            raise ValueError(f"{value!r} is not finite")
        return number
    if kind == "boolean":
        text = str(value).strip().lower()
        if text in {"true", "1"}:
            return True
        if text in {"false", "0"}:
            return False
        raise ValueError(f"{value!r} is not a boolean")
    raise ValueError(f"Unsupported column type: {kind}")


def _key(row: dict[str, Any], columns: list[str]) -> tuple[Any, ...]:
    return tuple(row[name] for name in columns)


def compare_by_business_key(
    actual: Path, gold: Path, *, report: Path,
) -> Verdict:
    """Compare a delivered CSV against a frozen JSON truth and write observations."""
    evidence = [report.name]
    observations: dict[str, Any] = {
        "actual": actual.name,
        "gold_version": None,
        "assertions": [],
        "mismatches": [],
    }
    verdict = "unknown"
    detail = ""
    try:
        contract = json.loads(gold.read_text(encoding="utf-8"))
        observations["gold_version"] = contract.get("version")
        key_columns = list(contract["business_key"])
        columns = dict(contract["columns"])
        expected_rows = list(contract["rows"])
        null_values = set(contract.get("null_values", [""]))
        tolerance = dict(contract.get("numeric_tolerance", {}))

        with actual.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fieldnames = reader.fieldnames or []
            raw_rows = list(reader)
        missing_columns = sorted(set(columns) - set(fieldnames))
        extra_columns = sorted(set(fieldnames) - set(columns))
        observations["columns"] = {
            "actual": fieldnames,
            "expected": list(columns),
            "missing": missing_columns,
            "extra": extra_columns,
        }
        if missing_columns or (extra_columns and not contract.get("allow_extra_columns", False)):
            observations["mismatches"].append(observations["columns"])

        actual_rows: list[dict[str, Any]] = []
        coercion_errors: list[dict[str, Any]] = []
        for number, raw in enumerate(raw_rows, start=2):
            converted: dict[str, Any] = {}
            for name, kind in columns.items():
                try:
                    converted[name] = _coerce(raw.get(name), kind, null_values)
                except (TypeError, ValueError) as exc:
                    coercion_errors.append({"line": number, "column": name, "error": str(exc)})
            actual_rows.append(converted)
        observations["coercion_errors"] = coercion_errors
        if coercion_errors:
            observations["mismatches"].extend(coercion_errors)

        expected_index: dict[tuple[Any, ...], dict[str, Any]] = {}
        actual_index: dict[tuple[Any, ...], dict[str, Any]] = {}
        duplicate_keys: list[list[Any]] = []
        for expected in expected_rows:
            converted = {
                name: _coerce(expected.get(name), kind, null_values)
                for name, kind in columns.items()
            }
            expected_index[_key(converted, key_columns)] = converted
        for row in actual_rows:
            if any(name not in row for name in key_columns):
                continue
            row_key = _key(row, key_columns)
            if row_key in actual_index:
                duplicate_keys.append(list(row_key))
            actual_index[row_key] = row
        observations["duplicate_keys"] = duplicate_keys
        if duplicate_keys:
            observations["mismatches"].append({"duplicate_keys": duplicate_keys})

        missing_keys = sorted(set(expected_index) - set(actual_index))
        extra_keys = sorted(set(actual_index) - set(expected_index))
        observations["missing_keys"] = [list(item) for item in missing_keys]
        observations["extra_keys"] = [list(item) for item in extra_keys]
        if missing_keys or extra_keys:
            observations["mismatches"].append({
                "missing_keys": observations["missing_keys"],
                "extra_keys": observations["extra_keys"],
            })

        for row_key in sorted(set(expected_index) & set(actual_index)):
            expected = expected_index[row_key]
            observed = actual_index[row_key]
            for name, kind in columns.items():
                wanted = expected[name]
                got = observed.get(name)
                passed = got == wanted
                if kind == "number" and got is not None and wanted is not None:
                    rules = tolerance.get(name, tolerance.get("default", {}))
                    limit = max(
                        float(rules.get("abs", 0.0)),
                        float(rules.get("rel", 0.0)) * abs(wanted),
                    )
                    passed = abs(got - wanted) <= limit
                assertion = {
                    "key": list(row_key), "column": name,
                    "actual": got, "expected": wanted, "passed": passed,
                }
                observations["assertions"].append(assertion)
                if not passed:
                    observations["mismatches"].append(assertion)

        verdict = "fail" if observations["mismatches"] else "pass"
        detail = (
            f"Compared {len(actual_rows)} rows across {len(columns)} typed columns "
            f"using {len(key_columns)} business-key field(s)."
        )
    except (OSError, UnicodeError, csv.Error, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        detail = f"Verifier could not evaluate the artifact: {type(exc).__name__}: {exc}"
        observations["verifier_error"] = detail
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(observations, ensure_ascii=False, indent=2), encoding="utf-8")
    return Verdict(verdict, VERIFIER, evidence, detail)


__all__ = ["compare_by_business_key"]
