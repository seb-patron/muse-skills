# Cross-model reference baseline

This is a preliminary, one-repeat reference from 2026-09-12. It is not a
statistical model comparison. Luna and Sol ran through Codex, not Muse, so the
runtime, tools, model family, and skill-delivery mechanism changed together.
The result can show whether another model/runtime can solve the task and
whether the current skill transfers; it cannot isolate Spark weights from
Muse behavior.

## Run identity

- Eval: `eval-HU5-2026-09-12T21:44:44`, 8 rows, 21m 27s wall time.
- Usage: 2,839,735 total candidate-plus-grader tokens; cache disabled.
- Candidate skill source: repository head
  `adbf38a79704606c112c70628fe3df161e684241`.
- Candidates: Codex CLI 0.153.4; Luna at medium reasoning and Sol at high.
- Harness: Promptfoo 0.123.0; Node.js 26.7.0; concurrency 2.
- Grader: `openai:codex-sdk:gpt-5.6-terra` at high reasoning, with
  human-reproduced gold as the authority.

Each candidate received a separate writable local clone of the frozen head.
The Codex sandbox allowed writes only inside that disposable clone and disabled
network access and web search. This is necessary because the current skill asks
reviewers to make scratch copies and run mutation/revert probes. Candidate
changes are discarded after each row.

## Results

| Case | Candidate | Skill | Verdict | Gold recall | Precision | Result | Latency | Candidate tokens |
| --- | --- | --- | --- | ---: | ---: | --- | ---: | ---: |
| Broken PR #1 (`e2e3f5e`) | Luna medium | no | APPROVE | 0/5 | 1.00 | fail | 86s | 127,316 |
| Broken PR #1 (`e2e3f5e`) | Luna medium | yes | APPROVE | 0/5 | 1.00 | fail | 192s | 541,227 |
| Broken PR #1 (`e2e3f5e`) | Sol high | no | NEEDS_FIXES | 0/5 | 1.00 | fail | 276s | 975,985 |
| Broken PR #1 (`e2e3f5e`) | Sol high | yes | — | — | — | error: 540s timeout | 540s | unavailable |
| Header gap (`33ebbdd`) | Luna medium | no | NEEDS_FIXES | 1/1 | 1.00 | pass | 103s | 243,102 |
| Header gap (`33ebbdd`) | Luna medium | yes | APPROVE | 0/1 | 1.00 | fail | 119s | 178,213 |
| Header gap (`33ebbdd`) | Sol high | no | NEEDS_FIXES | 0/1 | 1.00 | fail | 342s | 535,492 |
| Header gap (`33ebbdd`) | Sol high | yes | — | — | — | error: 540s timeout | 540s | unavailable |

Precision 1.00 on an empty review is vacuous. The verdict and recall assertions
keep those false approvals red. A full row passes only when all deterministic
and model-graded assertions pass. Timeout rows have no final review to grade and
are errors, not zero-recall reviews.

## What this says so far

The current skill did not improve Luna. Both conditions missed all five known
defects on the broken head, while the treatment missed the header defect that
the Luna control found. This is evidence that the present instructions are part
of the problem, not evidence that every Muse miss is caused only by Spark.

Sol control produced supported findings that were not in the finite gold set,
including an overstated baseline-coverage claim and a frontmatter-scoping gap.
Those are useful candidate annotations for human verification, but neither Sol
control review recalled the defects the cases were designed to measure. A
stronger model therefore explored more deeply without automatically covering
the known failure modes.

Both Sol skill treatments exceeded the nine-minute row budget while actively
running scratch mutation/revert probes. They did not produce final answers, so
this baseline cannot compare their recall. The consistent timeout is itself an
important result: when the full procedure is executable, the current skill is
too broad and lacks a probe budget, stopping rule, and reserved finalization
time for this configuration.

The combined signal points to a prompt/procedure issue alongside any model
effect:

- the skill did not help Luna and regressed one row;
- Sol without the skill found novel supported issues but missed the known ones;
- Sol with the skill spent the entire budget probing and never finalized.

Because model and runtime changed together, this remains directional evidence,
not a causal decomposition of Spark versus prompt quality.

## Implication for the next prompt experiment

The next treatment should be shorter and budgeted rather than a longer
checklist. It should require reviewers to map the change's highest-risk claims
to a small number of executable probes, prioritize known invariant classes,
reserve time to write the review, and report completed findings before the
budget expires. External-version claims should require current authoritative
evidence or be listed as unrun.

For the fast tuning loop, run Muse/Spark control versus candidate on one broken
case and one genuinely clean paired head. Promote promising treatments to Luna,
then use Sol and external reviewers at milestones. Keep at least one unseen PR
held out. Repeating this full matrix now would be expensive and premature: this
single run used 2.84 million tokens.

## Calibration and raw output

Earlier read-only pilots are excluded because they blocked the skill's required
scratch and mutation probes. They nevertheless exposed an incomplete label for
`33ebbdd`: arbitrary text after a closing bold probe header passes the checker.
A separate local reproduction confirmed the defect, so the case changed from
an APPROVE control to a one-finding NEEDS_FIXES case before the recorded run.

Two still-earlier focused attempts timed out before any Codex event because the
Python worker inherited Promptfoo's open stdin. They are adapter failures, are
also excluded, and now have a regression test.

Raw output is ignored because it is large and can contain machine-specific
paths. The most recent local run is written to
`evals/behavioral/results/cross-model-latest.json`.
