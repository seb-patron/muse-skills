#!/usr/bin/env bash
# run_offline_checks.sh: offline test/lint/validator gate for muse-skills.
#
# Usage: scripts/run_offline_checks.sh [--unit-only]
#   (no args)    runs the full offline gate: unit tests, skill lint, the four
#                behavioral-experiment validators, JS syntax checks, and a
#                whitespace-conflict-marker diff check.
#   --unit-only  runs only the unittest line, nothing else.
#
# Always runs from the repository root, regardless of the caller's cwd.
# Uses .venv/bin/python if present in the repo root, else falls back to
# python3. No network access is required or attempted.
set -euo pipefail

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd -P) || {
    echo "run_offline_checks: unable to locate the repository root" >&2
    exit 1
}
cd "$repo_root"

if [ -x "$repo_root/.venv/bin/python" ]; then
    python_bin="$repo_root/.venv/bin/python"
else
    python_bin="python3"
fi

unit_only=0
case "${1:-}" in
    --unit-only) unit_only=1 ;;
    "") ;;
    *)
        echo "usage: run_offline_checks.sh [--unit-only]" >&2
        exit 2
        ;;
esac

echo "== unittest discover (evals) =="
"$python_bin" -m unittest discover -s evals -p 'test_*.py'

if [ "$unit_only" -eq 1 ]; then
    exit 0
fi

echo "== check_skills.py =="
"$python_bin" evals/check_skills.py

echo "== behavioral/experiment.py validate =="
"$python_bin" evals/behavioral/experiment.py validate

echo "== behavioral/experiment.py validate-development =="
"$python_bin" evals/behavioral/experiment.py validate-development

echo "== behavioral/experiment.py validate-development-v3 =="
"$python_bin" evals/behavioral/experiment.py validate-development-v3

echo "== behavioral/experiment.py validate-evidence-claims-v3-screen =="
"$python_bin" evals/behavioral/experiment.py validate-evidence-claims-v3-screen

echo "== node --check evals/behavioral/run.mjs =="
node --check evals/behavioral/run.mjs

echo "== node --check evals/behavioral/selection.mjs =="
node --check evals/behavioral/selection.mjs

echo "== git diff --check =="
git diff --check

echo "run_offline_checks: all offline checks passed"
