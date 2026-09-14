# Evaluation correctness v2 evidence

Date: 2026-09-14  
Source: PR #6 head `92845170756c65366f9477f7bb132ea836ff471d`  
Scope: offline scoring normalization and historical Git visibility only  
Live inference: not run  
Independent review: pending

## Corrected behavior

`muse-review-metrics-v2` treats clean, pending, and unspecified gold denominators
as not applicable or unknown rather than perfect recall. It determines applicability
from the case contract even when Promptfoo provider metadata has no
`evaluationFacts`, keeps pending, blank, malformed, and missing contracts unknown
even if provider facts claim matches, rejects direct rubric recall as a substitute
for an applicable denominator, and exposes applicable-row and scored-row coverage.
Future runner outputs use the distinct `*.metrics-v2.json` identity. The committed v1 reports and
their frozen inputs remain unchanged because their ignored raw outputs are absent.

Historical workspaces now initialize an empty Git repository and fetch only the
selected head and its ancestors. The review can still resolve the base and head,
prove ancestry, and diff them. The regression fixture adds a later tagged commit
containing a sentinel, then proves the review repository has no later object, refs,
tags, source remote, alternates, fetch record, or source path in Git configuration.
This proves the tested Git-history boundary, not complete host or tool isolation.

## Offline verification

- `/tmp/muse-workbench-update/python-env/bin/python -m unittest discover -s evals -p 'test_*.py' -v`: 62 tests passed, including explicit pending/blank/malformed gold with conflicting provider facts.
- `/tmp/muse-workbench-update/python-env/bin/python evals/check_skills.py`: 42 checks passed.
- `/tmp/muse-workbench-update/python-env/bin/python evals/behavioral/experiment.py validate`: frozen candidates, hashes, splits, budgets, grader and case gold sentinels passed; this check alone does not test runtime visibility.
- `SPIKE_PYTHON=/tmp/muse-workbench-update/python-env/bin/python node evals/behavioral/run.mjs spike-validate`, using the installed Promptfoo dependencies from the disposable readiness checkout: configuration valid; no model rows ran.
- `node --check` for `run.mjs` and `selection.mjs`, plus `git diff --check`: passed.

Revision rounds: 1/3. The fail-closed applicability repair passed its proportional
recheck; independent non-author recheck of the final diff and this evidence remains
required.
