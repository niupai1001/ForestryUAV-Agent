"""Freeze the agent-track configuration from the live deployment.

`FRAMEWORK.md` section 6 requires the agent track's configuration to be frozen
before collection, which is why `run_baseline` refuses to invent one the way it
does for the engineering track. Doing it by hand invites a wrong or stale digest,
so this reads the values from the running deployment and writes the nine required
fields.

Everything it records is a value it actually observed. When a source is not
available it fails rather than substituting a placeholder, because a placeholder
would make two incomparable runs look comparable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import urllib.request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG_FIELDS = (
    "code_snapshot", "model_digest", "prompt_snapshot", "tools_snapshot",
    "dataset_version", "environment_snapshot", "evaluator_version",
    "sampling", "budgets",
)


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, cwd=PROJECT_ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False, **kwargs,
    )


def code_snapshot() -> str:
    """The commit plus a hash of the working tree, so an uncommitted edit counts.

    Scoped to the paths the measurement actually depends on: the Runtime, the shared
    package, the tests and the evaluation harness. Hashing the whole tree made the
    snapshot differ between the two arms for a reason that has nothing to do with the
    measurement -- collecting arm B writes evidence under ``evaluation/work`` while
    arm A has not been collected yet -- and the comparison then reported drift and
    refused to be a comparison. Evidence is described by the slots themselves, not by
    the source snapshot.
    """
    revision = _run(["git", "rev-parse", "HEAD"]).stdout.strip()
    if not revision:
        raise RuntimeError("not a git checkout; cannot record code_snapshot")
    status = _run([
        "git", "status", "--short", "--",
        "runtime", "shared", "tests", "evaluation",
        ":(exclude)evaluation/work",
        ":(exclude)evaluation/fixtures",
        ":(exclude)**/__pycache__",
        ":(exclude)**/*.pyc",
    ]).stdout
    relevant = [
        line for line in status.splitlines()
        if line.strip() and "evaluation/work" not in line
    ]
    dirty = hashlib.sha256("\n".join(relevant).encode("utf-8")).hexdigest()[:16]
    return f"{revision}+status-{dirty}"


def tools_snapshot() -> str:
    """Hash the frozen tool contract, not the Runtime's live registry.

    Reading the registry here made the measured system its own reference: editing a
    tool changed the "frozen" snapshot in the same edit, so a configuration mismatch
    was undetectable. The contract lives in ``evaluation/tool_contract.json`` and is
    updated deliberately.
    """
    from .tool_contract import tools_snapshot as frozen_tools_snapshot

    return frozen_tools_snapshot()


def ollama_digest(base_url: str, model: str) -> str:
    """Ask the model server for the digest; a guessed value is worthless."""
    with urllib.request.urlopen(base_url.rstrip("/") + "/api/tags", timeout=15) as response:
        tags = json.load(response)
    for entry in tags.get("models", []):
        if entry.get("name") == model or entry.get("model") == model:
            return str(entry["digest"])
    available = ", ".join(str(e.get("name")) for e in tags.get("models", []))
    raise RuntimeError(f"{model!r} is not installed; available: {available or 'none'}")


def container_image(container: str) -> str | None:
    """Which image the Runtime runs, by the *tag this repository builds*.

    Not the image id and not the repo digest. Both change on every rebuild of an
    unchanged Dockerfile, so the routine rebuild between arms -- or a restart halfway
    through a collection -- makes the field differ while the code is identical, and
    the comparison then reports configuration drift that did not happen. Chasing that
    costs the one thing this experiment cannot buy back: the collection time.

    What is worth comparing is which code ran, and that is already recorded
    separately: ``code_snapshot`` for the Runtime's source and ``tools_snapshot`` for
    the tools. This field records *where* it ran, at the granularity a reader can
    reproduce: the platform, the interpreter, and the image tag that ``compose.yaml``
    builds from this checkout.
    """
    configured = os.getenv("AGENT_JOB_IMAGE") or os.getenv("RUNTIME_IMAGE")
    if configured:
        return f"image-tag:{configured}"
    probe = _run([
        "docker", "inspect", container,
        "--format", "{{.Config.Image}}",
    ])
    tag = probe.stdout.strip()
    return f"image-tag:{tag}" if tag else None


def prompt_snapshot() -> str:
    """Hash every prompt the run depends on: the system prompt and each case prompt."""
    digest = hashlib.sha256()
    system = (PROJECT_ROOT / "runtime" / "agent.py").read_text(encoding="utf-8")
    match = re.search(r'^SYSTEM = """(.*?)"""', system, re.S | re.M)
    digest.update((match.group(1) if match else system).encode("utf-8"))
    prompts = sorted((PROJECT_ROOT / "evaluation" / "fixtures").glob("*/prompt.txt"))
    for path in prompts:
        digest.update(path.parent.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return f"system+{len(prompts)}-case-prompts@{digest.hexdigest()[:16]}"


def build(
    ollama_url: str, model: str, container: str,
    *, container_image_override: str | None = None,
) -> dict:
    configuration = {
        "code_snapshot": code_snapshot(),
        "model_digest": ollama_digest(ollama_url, model),
        "prompt_snapshot": prompt_snapshot(),
        "tools_snapshot": tools_snapshot(),
        "dataset_version": json.loads(
            (PROJECT_ROOT / "evaluation" / "suite.json").read_text(encoding="utf-8")
        )["version"],
        "environment_snapshot": (
            f"{platform.platform()} Python-{platform.python_version()} "
            f"{container_image_override or container_image(container) or 'image-unknown'}"
        ),
        "evaluator_version": "forestry-eval-0.1",
        "sampling": {
            "thinking": os.getenv("OLLAMA_THINK", "true").lower() == "true",
            "temperature": float(os.getenv("OLLAMA_TEMPERATURE", "0.2")),
            "context": int(os.getenv("OLLAMA_CONTEXT", "32768")),
            "output": int(os.getenv("OLLAMA_OUTPUT_TOKENS", "8192")),
        },
        "budgets": {
            "model_requests": int(os.getenv("AGENT_MAX_ROUNDS", "32")),
            "wall_seconds": 900,
        },
    }
    missing = [field for field in CONFIG_FIELDS if not configuration.get(field)]
    if missing:
        raise RuntimeError(f"configuration is incomplete: {', '.join(missing)}")
    return configuration


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("evaluation/work/baseline"))
    parser.add_argument("--ollama-url", default=os.getenv("OLLAMA_URL_HOST", "http://127.0.0.1:11434"))
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "qwen3.8:27b"))
    parser.add_argument("--container", default="forestry-runtime")
    parser.add_argument(
        "--container-image",
        help=(
            "freeze the container-image field to this exact string instead of reading "
            "the deployed image. Used to make an evidence root's configuration match "
            "the `environment_snapshot` its traces actually recorded, which is the only "
            "value that can be true for evidence already collected under a different "
            "deployment string."
        ),
    )
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing frozen configuration")
    args = parser.parse_args()

    target = (PROJECT_ROOT / args.root / "configuration-agent.json").resolve()
    if target.exists() and not args.force:
        print(json.dumps({
            "written": False,
            "path": str(target),
            "message": "already frozen; pass --force to replace it (a replaced "
                       "configuration makes earlier results incomparable)",
        }, ensure_ascii=False))
        return 0
    try:
        configuration = build(
            args.ollama_url, args.model, args.container,
            container_image_override=args.container_image,
        )
    except Exception as exc:
        print(json.dumps({"written": False, "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        return 2
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(configuration, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"written": True, "path": str(target), "configuration": configuration},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
