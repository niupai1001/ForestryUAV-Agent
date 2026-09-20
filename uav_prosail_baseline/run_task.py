import sys

from task_config import INPUT_DIRECTORY, WORKSPACE_ROOT
from workflow import run_workflow


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    run_workflow(INPUT_DIRECTORY, WORKSPACE_ROOT)


if __name__ == "__main__":
    main()
