#!/bin/sh
# Arm runner for grounded-v1 trials: resolves its own directory, loads the
# deployment key, and forwards every argument to run_task.py.
cd "$(dirname "$0")" || exit 1
export $(grep -E '^RUNTIME_API_KEY=' .env | head -1)
exec python -m evaluation.grounded_v1.run_task "$@"
