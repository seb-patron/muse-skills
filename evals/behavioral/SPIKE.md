# Evidence-backed review-skill spike

Revision rounds: 0/3

Branch: `codex/evidence-backed-review-skill-spike`

Starting/current source revision at setup: `338d85855c8caf669490d065d8dda40f2c9d1b2d`.
The provided checkout had no configured remote, so the manager fetched the public
`main` URL directly and confirmed that exact revision before creating the dedicated
worktree. The promoted `skills/adversarial-review/SKILL.md` is unchanged.

## Hypothesis and scope

The leading hypothesis is that a shorter risk-first procedure—three claims, one
falsifier per claim, at most five expensive probes, one refutation per finding, and
reserved finalization—will improve recall per unit cost and completion compared
with the current checklist. This remains a hypothesis. The spike will not promote,
replace, or retire the production skill in this branch.

The four immutable candidates are:

1. Current skill, unchanged.
2. Minimal reviewer.
3. Risk-first reviewer.
4. AI Diff Reviewer mechanism adaptation, using only MIT-compatible mechanisms.

Their exact bytes and SHA-256 hashes are frozen in
[`candidates/manifest.yaml`](candidates/manifest.yaml). The candidate provider
verifies the configured hash before delivery. The candidate receives no case gold;
the independent Promptfoo grader is `openai:codex-sdk:gpt-5.6-terra` at high
reasoning, unchanged from the existing harness. The tested Muse model is pinned to
`muse-spark-1.3-contributor`, with the existing 24-step and 540-second limits.
Candidates never grade or promote their own findings.

## Frozen corpus and splits

The canonical eight-case corpus is
[`cases/spike_cases.yaml`](cases/spike_cases.yaml). It contains two verified
defect-bearing Muse-authored heads, two verified clean empty-diff controls for
validation, one verified clean control for training, and three public
Muse-authored historical heads awaiting human adjudication. The assignment is
frozen before candidate scoring:

| Split | Cases | Status |
| --- | --- | --- |
| Train | `pr1-broken-promptfoo`, `pr1-structural-lint-header-gap`, `clean-control-main` | Verified; one run per candidate is permitted. |
| Validation | `clean-control-pr1-merge`, `clean-control-pr3-merge` | Verified; only the best two candidates, three repeats each. |
| Held-out | `pr2-review-loop`, `pr4-cross-model-reference`, `pr3-behavioral-baseline` | Gold pending; locked by the runner and queued in [`ADJUDICATION_QUEUE.md`](ADJUDICATION_QUEUE.md). |

The empty-diff controls are intentionally deterministic wiring controls, not
evidence that a reviewer solves a nontrivial clean change. Candidate annotations
and prior AI reviews are never promoted to human gold.

## Evaluation sequence

1. Run `python3 evals/behavioral/experiment.py validate` (or
   `npm run eval:spike:validate`) before any model call.
2. Run each candidate once on train with `npm run eval:spike:train`.
3. Eliminate malformed, unsupported, false-approving, non-finalizing, or materially
   over-budget candidates. A timeout is an execution error, not zero recall.
4. Set `SPIKE_FINALISTS` to exactly two provider labels and run
   `npm run eval:spike:validation` for three repeats.
5. Freeze exactly one finalist candidate label and hash in the report. Held-out
   remains locked until the queue is human-adjudicated and replacements are selected
   if disclosure has occurred; the runner verifies both the human-gold sentinels and
   the finalist hash before executing it.
6. After the prompt comparison is frozen, run Luna/Sol reference rows as a final
   milestone only. Never run an unnecessary full cross-model matrix after wording
   changes.

The runner writes raw Promptfoo output under the existing ignored results directory.
No raw private evidence is committed.

## Metrics and interpretation

Every completed row is reported with blocking-finding recall, all-gold recall,
supported precision, false approval, completion, verdict accuracy, latency,
candidate tokens/cost, and novel human-verified findings. The runner converts raw
Promptfoo `namedScores` into separate blocking/all-gold fields and reads candidate
usage only from the provider response, never from the grader's aggregate. Empty-review
precision is vacuous and must not conceal false approval. Incomplete/timeout rows
report quality metrics as unknown rather than zero. Deterministic aggregation and
conversion fixtures live in [`scoring.py`](scoring.py); semantic gold matching
remains the independent Terra rubric and requires calibration against human gold.

## Consultation and review contract

The Codex consultation is the non-author Sol analysis recorded in the task handoff;
the native research agents were Luna/Luna and the analysis agent was Sol. The
Claude packet and pending status are recorded under `consultation/`. No Claude
response is claimed.

The combined spike must receive substantive review from a non-author Sol agent.
Mechanical fixtures or documentation may receive Luna review. The author addresses
supported findings and reruns proportionate checks, with a maximum of three repair
rounds. This branch is currently at round 0/3.

## Decision record

No retain/replace/retire decision is made until the train and validation rows are
actually run and held-out gold is human-adjudicated. The repaired train was attempted
but did not complete a quality row in this environment; its cost/runtime evidence
and the earlier session-lease failures are recorded in [`DECISION.md`](DECISION.md).
The decision record is kept in [`DECISION.md`](DECISION.md); this spike does not
modify the promoted skill.
