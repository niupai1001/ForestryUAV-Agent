"""Drive the capability-switch matrix (see ``evaluation/experiments/MATRIX.md``).

Two container phases -- the capability switches off, then on -- because the four
switches are read from the process environment at import time and can only be
changed by recreating the container. Inside a phase the arms differ only through
per-trial settings (``OLLAMA_CONTEXT``, ``OLLAMA_MODEL``), which reach the
runtime through the settings-override surface, so an arm costs no container
operation at all.

Standard library only: the driver talks to ``docker compose`` and to
``evaluation.grounded_v1.run_task`` as subprocesses, anchored on this file's own
location so the caller's working directory does not matter.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = [ROOT / "compose.yaml", ROOT / "compose.eval.yaml"]
ENV_FILE = ROOT / ".env"
MATRIX_ROOT = ROOT / "data" / "runs" / "matrix"

#: The bundle factor. off = every default untouched; on = all four capability
#: switches together, exactly as MATRIX.md defines them. The off phase must
#: *remove* the lines (a bare ``write_env({})`` would leave stray lines from an
#: earlier run behind, and the container would keep the on state).
SWITCH_KEYS = ("RETRIEVAL_FABRIC_ENABLED", "RETRIEVAL_CODE_ENABLED",
               "SUBAGENTS_ENABLED", "SCHEDULER_PARALLEL_READS")
SWITCH_OFF: dict[str, str] = {key: None for key in SWITCH_KEYS}
SWITCH_ON: dict[str, str] = {
    "RETRIEVAL_FABRIC_ENABLED": "true",
    "RETRIEVAL_CODE_ENABLED": "true",
    "SUBAGENTS_ENABLED": "true",
    "SCHEDULER_PARALLEL_READS": "4",
}

ARMS: dict[str, dict] = {
    "E1": dict(phase="off", context=32768,  model="qwen3.5:4b"),
    "E2": dict(phase="on",  context=32768,  model="qwen3.5:4b"),
    "E3": dict(phase="off", context=131072, model="qwen3.5:4b"),
    "E4": dict(phase="on",  context=131072, model="qwen3.5:4b"),
    "E5": dict(phase="off", context=32768,  model="qwen3.8:27b"),
    "E6": dict(phase="on",  context=32768,  model="qwen3.8:27b"),
    "E7": dict(phase="off", context=131072, model="qwen3.8:27b"),
    "E8": dict(phase="on",  context=131072, model="qwen3.8:27b"),
}


def compose(*args: str) -> None:
    cmd = ["docker", "compose"]
    for path in COMPOSE_FILES:
        cmd += ["-f", str(path)]
    cmd += list(args)
    subprocess.run(cmd, check=True)


def write_env(values: dict[str, str | None]) -> None:
    """Set (``"value"``) or remove (``None``) lines; every other entry survives.

    A ``None`` removal is the whole point for the off phase: leaving the lines
    alone would silently keep the container on whatever an earlier run wrote,
    and an unchanged file also means compose will not recreate the container.
    """
    lines = (ENV_FILE.read_text(encoding="utf-8").splitlines()
             if ENV_FILE.exists() else [])
    wanted = dict(values)
    kept = []
    for line in lines:
        name = line.split("=", 1)[0].strip()
        if name in wanted:
            value = wanted.pop(name)
            if value is not None:
                kept.append(f"{name}={value}")
            continue
        kept.append(line)
    kept.extend(f"{name}={value}" for name, value in wanted.items()
                if value is not None)
    ENV_FILE.write_text("\n".join(kept) + "\n", encoding="utf-8")


def container_switch_state() -> dict[str, str]:
    """What the *running* container actually has, not what .env promises."""
    proc = subprocess.run(
        ["docker", "exec", "forestry-runtime", "sh",
         "-c", "printenv | grep -E 'RETRIEVAL_FABRIC|RETRIEVAL_CODE|SUBAGENTS|SCHEDULER_PARALLEL' || true"],
        capture_output=True, text=True, check=True,
    )
    state: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        name, _, value = line.partition("=")
        if name.strip():
            state[name.strip()] = value.strip()
    return state


def switch_phase(phase: str) -> None:
    """Write .env, recreate the container, and verify what it really got.

    The verify step is not paranoia: a phase that silently ran on the wrong
    switch state produced an inverted pilot once (arms labelled off ran on, and
    arms labelled on ran off), and nothing in the logs said so. The container's
    own environment is the only ground truth.
    """
    write_env(SWITCH_ON if phase == "on" else SWITCH_OFF)
    # --force-recreate: an unchanged file must not mean an unchanged container.
    compose("up", "-d", "--force-recreate", "runtime")
    wait_healthy()
    actual = container_switch_state()
    expected = SWITCH_ON if phase == "on" else {}
    for key in SWITCH_KEYS:
        got = actual.get(key, "")
        want = expected.get(key, "")
        if got != want:
            raise RuntimeError(
                f"phase {phase}: container has {key}={got!r}, expected "
                f"{want!r}. The switch state did not land; aborting before "
                "any trial runs on a mislabelled arm."
            )
    print(f"  phase {phase} verified in container: "
          f"{json.dumps(actual, sort_keys=True)}", flush=True)


def wait_healthy(timeout_seconds: int = 300) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8010/health",
                                        timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
            if data.get("status") == "ok" and data.get("model_reachable"):
                print(f"  health ok: {json.dumps(data, ensure_ascii=False)[:220]}",
                      flush=True)
                return
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("runtime did not become healthy in time")


def env_value(name: str) -> str:
    """One ``NAME=value`` line from .env, or an empty string."""
    if not ENV_FILE.exists():
        return ""
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition("=")
        if key.strip() == name:
            return value.strip()
    return ""


def run_trial(arm: str, spec: dict, task: str, slot: int, timeout: int,
              skip_existing: bool = False, no_grade: bool = False) -> dict | None:
    """One trial: a fresh slot directory, one run_task.py invocation."""
    runs_root = MATRIX_ROOT / arm
    trial_dir = runs_root / f"{task}-r{slot:02d}"
    if skip_existing and (trial_dir / "raw" / "events.json").exists():
        print(f"[{arm}] {task} slot {slot} already collected; skipping",
              flush=True)
        return None
    if trial_dir.exists():
        shutil.rmtree(trial_dir)
    api_key = env_value("RUNTIME_API_KEY")
    if not api_key:
        raise RuntimeError("RUNTIME_API_KEY not found in .env; the collector "
                           "refuses to run without it (rc=2 in seconds).")
    settings = {"OLLAMA_CONTEXT": str(spec["context"]),
                "OLLAMA_MODEL": spec["model"]}
    cmd = [
        sys.executable, "-m", "evaluation.grounded_v1.run_task",
        "--task", task, "--repeat", str(slot),
        "--runs-root", str(runs_root.relative_to(ROOT)),
        "--api-key", api_key,
        "--settings", json.dumps(settings),
        "--timeout", str(timeout),
    ]
    if no_grade:
        # The grading half needs rasterio and numpy, which the *host* python that
        # drives this matrix does not carry. Collection must therefore be
        # separable from grading; each package is graded afterwards inside the
        # runtime image, which does. Passing this through keeps one driver for
        # both hosts instead of a second copy of the collection loop.
        cmd.append("--no-grade")
    print(f"[{arm}] {task} slot {slot} (ctx={spec['context']}, "
          f"model={spec['model']}) starting", flush=True)
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    elapsed = round(time.monotonic() - started, 1)
    summary: dict = {}
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                summary = json.loads(line)
            except json.JSONDecodeError:
                continue
    record = {
        "arm": arm, "task": task, "slot": slot,
        "phase": spec["phase"], "context": spec["context"],
        "model": spec["model"],
        "returncode": proc.returncode, "elapsed_seconds": elapsed,
        "summary": summary,
    }
    record_path = MATRIX_ROOT / f"{arm}-{task}-r{slot:02d}.record.json"
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"[{arm}] {task} slot {slot} finished rc={proc.returncode} "
          f"in {elapsed}s -> {json.dumps(summary, ensure_ascii=False)[:300]}",
          flush=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arms", default="E1,E2,E3,E4,E5,E6,E7,E8",
                        help="comma-separated arm ids from MATRIX.md")
    parser.add_argument("--tasks", default="oam-07",
                        help="comma-separated frozen task ids")
    parser.add_argument("--repeats", type=int, default=2,
                        help="slots per arm (1..3)")
    parser.add_argument("--timeout", type=int, default=900,
                        help="per-trial timeout in seconds")
    parser.add_argument("--skip-existing", action="store_true",
                        help="keep trials whose raw/events.json already exists")
    parser.add_argument("--no-grade", action="store_true",
                        help="collect only; grade each package afterwards inside the "
                             "runtime image, which carries the raster dependencies")
    args = parser.parse_args()

    unknown = [name for name in args.arms.split(",") if name and name not in ARMS]
    if unknown:
        parser.error(f"unknown arms: {unknown}")
    arms = {name: ARMS[name] for name in args.arms.split(",") if name}
    tasks = [name for name in args.tasks.split(",") if name]
    MATRIX_ROOT.mkdir(parents=True, exist_ok=True)

    total = len(arms) * len(tasks) * args.repeats
    print(f"matrix pilot/full run: {len(arms)} arms x {len(tasks)} task(s) "
          f"x {args.repeats} slot(s) = {total} trials", flush=True)

    started = time.monotonic()
    for phase in ("off", "on"):
        phase_arms = {name: spec for name, spec in arms.items()
                      if spec["phase"] == phase}
        if not phase_arms:
            continue
        print(f"--- phase {phase}: arms {', '.join(phase_arms)} ---", flush=True)
        switch_phase(phase)
        for arm, spec in phase_arms.items():
            for task in tasks:
                for slot in range(1, args.repeats + 1):
                    run_trial(arm, spec, task, slot, args.timeout,
                              skip_existing=args.skip_existing,
                              no_grade=args.no_grade)

    print(f"all trials done in {round(time.monotonic() - started, 1)}s; "
          f"records under {MATRIX_ROOT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
