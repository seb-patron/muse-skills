# Why evidence-claims-v2 missed the evidence-claim defect

Date: 2026-09-15

Status: diagnosis of one saved candidate review and its saved runtime trace; no
new candidate execution, no new grader call, no promotion

The [development v3 calibration replay](2026-09-14-development-v3-calibration.md)
ended with an open question: the experimental candidate
[`evidence-claims-v2`](../candidates/evidence-claims-v2.md) already instructs the
reviewer to recompute material claims from their source, yet its saved review of
the `genv-pr90-evidence-claim` development case still missed the expected
overstated evidence claim. This report inspects the saved review and the saved
runtime trace of that one row, names the most likely cause, and records one
targeted candidate change
([`evidence-claims-v3`](../candidates/evidence-claims-v3.md)) written against that
cause.

Scope limit: N = 1 saved row on a disclosed development case whose expected
findings were published before the candidates were frozen. Nothing here measures
either skill, and no fresh run was made. The saved reviews, raw grader responses
and runtime traces are private and are not reproduced here beyond short
excerpts.

## The case, in one paragraph

The reviewed head adds an experiment runner, a fixture assembler, a committed
hash-only fixture, a required research note and a reading-trail entry. The note
claims, among its bounded interpretations, that the control arms differ at
**every adjacent checkpoint pair**. The fixture records fourteen checkpoints per
attempt, so thirteen adjacent intervals exist; eleven of them change in each
retained control attempt, and two pairs (`[2299,2300]` and `[2303,2304]`) are
stable. The expected finding is that the merge-critical negative-control claim
exceeds its durable evidence. The expected verdict is `NEEDS_FIXES`.

## Trace facts

All rows are observations from the saved row and its trace, not inferences.

| Question | Observation |
| --- | --- |
| Did the run open the fixture? | Yes. Its own audit script loaded the fixture JSON, printed the top-level key count, and iterated the per-attempt checkpoint records. |
| Did it open the note? | Yes, twice over: the docs-and-fixture diff was read at about t+8s, and the note itself was read in full at about t+45s. |
| Was the `every adjacent checkpoint pair` sentence in context? | Yes. It appears in the note text inside the visible portion of the diff output, well inside the tool-output window, and again in the full note read. |
| Change context / PR body | The supplied `change_summary` was the only change-context surface; the reading-trail diff was read. No live PR body exists for a frozen-head case. |
| Which claims did the cheap claim audit select? | Five, all reconstructable from the audit script it wrote: fixture shape, `result` equals the stored response map, twice-determinism across both repetitions, pre-intervention equality, and treatment-versus-control contrast frames per arm, cross-checked against the fixture's own stored comparison and classification blocks. |
| Was the `every adjacent pair` claim among them? | No. No selected claim concerns the control arms' own frame-to-frame behavior. |
| Did it recompute anything? | Yes. The audit was a genuine derivation from the committed artifact, not a prose echo — the instruction it was written to follow did execute. |
| Steps and time | 16 of 24 model steps; 133s of the 540s process limit; session ended cleanly. Tool calls: 1 skill read, 9 shell, 8 file reads, 2 searches, 2 script writes, 0 todo writes. |
| Where did budget go? | Freeze and diff reading to t+22s; source and test reading to t+45s; two probe scripts written at t+69s; the remaining five shell calls were the two probes plus three attempts to get the focused suite to import, ending at t+101s. |
| Did it read the repository's note-contract rules? | The repository's agent instructions were injected as standing project rules at session open; the trace shows no targeted read of their required-note sections, and the review contains no rule-to-artifact matrix. |
| What did the grader say? | `verdict_accuracy=0` (expected `NEEDS_FIXES`, got `APPROVE`); `gold_recall=0`, with the reason that the review never notes the 11-of-13 changing intervals or the two stable pairs; `supported_precision=1`. |
| What did the review report? | One low-severity finding about a type alias surviving a runner-side validator, and an `APPROVE` verdict whose summary leads with the positive result. |

Decisive detail: the deciding data was inside the dict the candidate's own audit
script had already loaded. Recomputing it needs one pass over each control
attempt's checkpoint hashes — fourteen checkpoints, thirteen intervals, eleven
changes, `[2299,2300]` and `[2303,2304]` equal in every retained control attempt.
This report's author reproduced that arithmetic from the same committed fixture
content. The miss was not a lack of access, a lack of budget, or a refusal to
compute.

## Contrast rows

- Same candidate, `genv-pr87-first-repair-type-boundary` (PASS): 20 of 24 steps,
  88s, 15 shell calls, and a written todo list mirroring the candidate's own
  section order. Its two findings both came from the boundary-falsification
  section, on a claim whose falsifier is a single constructed value. The
  candidate's strength on that row is the same section that produced the only
  finding on the missed row.
- Current production skill, same evidence-claim case: TIMEOUT. Its trace shows
  one shell call stalling for about 300s and a later stall of about 137s; it
  never emitted a review. The two candidates fail this case in different ways —
  one spends the budget and never reports, the other reports early and audits
  the wrong claims.

## Named cause

**The claim-selection rule is satisfiable by the claims that are cheapest to
check.** v2 says to "inspect at most five merge-critical statements" and to
prefer numbers and absolutes, but it does not say where the five come from or
require the candidate set to be enumerated before choosing. On this row the five
selected statements each had a stored fixture field mirroring them, so each was
decidable with one lookup plus a short derivation. The overstated claim ranges
over a set — thirteen adjacent pairs across eight arms — that no fixture field
reports, so deciding it means building the set first. It was in context and
never selected. Recomputation then ran faithfully on the selected five, which is
why a louder "recompute from source" instruction would not have changed this
row.

Competing hypotheses considered and set aside by the trace: budget exhaustion
(8 steps and roughly 400s went unused); missing input (fixture, note and trail
diff were all read); prose echoing instead of derivation (the audit script does
derive); and probe-budget capture by boundary falsification (that section did
consume the probe that produced the only finding, but the audit stage that
precedes it had already chosen its five claims without the overstatement).

Falsifier: if a fresh bounded run under v3 writes an enumeration that *contains*
the `every adjacent pair` sentence and still fails to derive the interval counts,
the cause is derivation, not selection, and the next change belongs in the
recompute step instead. Equally, a trace that shows the enumeration step
consuming the budget before any probe would move the cause to budget.

## The one change

`evidence-claims-v3` is `evidence-claims-v2` with a single replaced paragraph in
"Cheap claim audit first" (plus the version number in the document title). The
output contract, frontmatter, section set, probe cap and every other instruction
are unchanged.

- Before: inspect at most five merge-critical statements from the changed
  surfaces, preferring numbers and absolutes.
- After: first enumerate, without verifying, every quantified or absolute
  statement in those surfaces; write the enumeration down; then audit at most
  five of it, ranked so that a claim whose quantifier ranges over a set no
  artifact field already reports outranks a claim some stored field mirrors, and
  with control, null-arm and background claims ranked beside headline results.

SHA-256 of the new candidate:
`d357350e83f01429f97aa5903404ffe9342677bb16e39242024878958dfc4477`.

Diff versus v2: one content hunk (9 removed lines, 4 added) in the claim-audit
section, plus the one-line heading rename; 103 lines total against 98.

## Predicted effect

On this case, enumeration should surface the note's own universal claim into a
written list, and the ranking rule should place it above the response-map and
determinism claims that a stored field already mirrors — which is the only
change needed, because the derivation itself is a few lines over data the run
already loads. Elsewhere the change should be close to neutral: enumeration is
reading work, not probe work, and the probe cap is untouched.

## Predicted failure modes of v3

1. **Enumeration without promotion.** The run lists the claim and still audits
   the familiar five. This is the falsifier above and would move the cause to
   ranking or derivation.
2. **Enumeration inflation.** On a large diff the quantifier scan grows long
   enough to eat steps before probing, which would show up as a timeout of the
   kind the current skill already suffers on this case.
3. **Precision loss.** Promoting control and background claims could produce
   pedantic findings about prose that the artifact does support, lowering
   supported precision on clean heads.
4. **Regression on the type-boundary case.** The reordering could displace the
   boundary work that earns the PR #87 row, so that row must be in any control
   set.
5. **Shallow compliance.** The enumeration is written but treated as a checklist
   artifact rather than a selection input, leaving behavior unchanged.

## What a fresh bounded run would test

Not run here. The proposed next run is deliberately small: the three disclosed
development cases, `evidence-claims-v2` against `evidence-claims-v3` only, one
attempt per row, same runtime and grader profile as the v2 run, same step and
process limits, no held-out case. The evidence-claim row is the target; the
type-boundary row and the repaired approval control are the regression controls
for failure modes 3 and 4. Useful trace assertions beyond the verdict: whether an
enumeration was written, whether the overstated claim appears in it, whether it
was among the audited five, and how many steps the enumeration consumed. Three
disclosed rows with one attempt each cannot establish a winner; the run would
only test whether this specific instruction changes this specific behavior.
