# Claude consultation response

Status: complete; hypothesis generation only. Not human adjudication, not grading,
and not a retain/replace/retire decision.

Consultant: Claude Opus 5 (`claude-opus-5`) in Claude Code, 2026-09-15, authorized
by Sebastian in chat. Nothing in this file was run as a candidate or graded.

## Evidence boundary

This response is **not** the identical-evidence consultation that issue #5 Phase 2
specified. The earlier dispatch was never sent, and by the time this was written the
consultant had seen more than [`CLAUDE_PACKET.md`](CLAUDE_PACKET.md):

- Seen: the packet; the repository at `b9cad30` (both production skills, all four
  spike candidates, `evidence-claims-v2`, the spike and development-v2/v3 case packs
  with their grader-only gold, and every committed report); the bodies and comments
  of issues #5, #7 and #10; the diffs of open PRs #2 and #11.
- Not seen: saved candidate reviews, raw grader reasons, Muse session traces, the
  originating Sol consultation, or any antechamber material.
- Held-out exposure: PR #2's head `56c3d13` is held-out case `pr2-review-loop`. No
  gold exists for it and none is proposed here, but a strict reading should treat
  this proposal as having seen that head.

Observations below cite committed artifacts. Hypotheses are labeled and carry a
confidence. Predictions at the end are written to be scored later.

## 1. Row-by-row failure explanation

Budget context for every row: 540 seconds wall time; Muse rows also have
`--max-model-steps 24`.

| Row | Observation | Hypothesis (confidence) |
| --- | --- | --- |
| Muse control and Muse skill, `e2e3f5e` ([BASELINE](../BASELINE.md)) | Both APPROVE, zero findings; skill confirmed loaded. | The review prompt passes the author's `change_summary`, which asserts the false premise ("six tests assert expected content… all six passing"). Both conditions accepted that framing as context rather than as the claim under test. (medium) |
| Luna control, `e2e3f5e` | APPROVE, 0/5, 127k tokens. | Same anchoring. All five gold defects are *a check that cannot fail or does not measure what it claims*; nothing in the prompt directs attention to that class. (medium) |
| Luna skill, `e2e3f5e` | APPROVE, 0/5, 541k tokens (4.3× control). | The catalogue absorbed the extra effort. The one row that fits, Revert-probe, is framed as "revert the fix"; this head has no fix. The mutation that exposes gold 2–3 is *delete the SKILL.md content a test claims to protect*, which the row does not suggest. (medium) |
| Sol control, `e2e3f5e` | NEEDS_FIXES, graded 0/5, with a supported novel "overstated baseline-coverage claim". | **Possible grader under-count**: that finding may be gold 1 (`no-behavioral-run`) in different words. Worth human adjudication before treating Sol-control recall as zero. (medium) |
| Sol skill, both heads | 540s timeout while running mutation/revert probes; no final review. | Budget arithmetic, not diligence. Routing plus "areas with no mapping default to running the row" can select many rows; each row is several tool calls (copy, mutate, run, restore). No stopping rule or reserved finalization exists. (high) |
| Luna control, `33ebbdd` | Found the trailing-header gap, 243k tokens. | Open-ended exploration reached the parser boundary. (low: n=1) |
| Luna skill, `33ebbdd` | APPROVE, missed it, 178k tokens (fewer than control). | The checklist replaced exploration: no row is "feed the near-miss input to each new validator", so a completed checklist became the approval signal. (medium) |
| Sol control, `33ebbdd` | NEEDS_FIXES, 0/1, novel frontmatter-scoping gap. | Deeper exploration without a prioritization rule found a different real issue and not the designed one. (low) |

### Cross-row observation: two moves cover all current defect gold

Across the nine defect-gold findings committed so far (PR #1 ×6, gen-v #87 B,
gen-v #90 A, and the v3 low metadata issue on #90 C), each is exposed by one of two
cheap moves:

1. **Break the protected thing.** For each new check, test, guard or validator,
   delete what it protects or feed it the nearest invalid input, then see whether it
   still passes. This covers PR #1 gold 2, 3 and 4, the `33ebbdd` trailing text, and
   the #87 B `bool`/`int` alias.
2. **Recompute the claim.** For each quantitative or absolute statement in the
   summary, PR body, note or docs, derive it from its source. This covers PR #1 gold
   1 and 5, #90 A's 11-of-13, and #90 C's review metadata.

Caveat: the gold was found by humans reviewing roughly this way, so there is
selection bias. The issue #5 dynamics families (antechamber #103 eviction, #104
interleavings) are **not** covered by either move and need something else.

## 2. Remove, retain, make optional

**Remove from the core skill**

- "Run every routed row" and "areas with no mapping default to running the row".
  Together they make cost unbounded and turn completion into approval.
- The mandatory refutation "in a fresh context of the same agent". Budget-expensive
  and weakly independent; one inline refutation attempt per finding keeps the
  benefit.
- FEL miss identifiers (`#75-1`, `#76-8`) in the core text. They are provenance for
  maintainers and cost reviewer attention.

**Retain verbatim or nearly**

- Freeze base/head and prove `HEAD` matches.
- "A green suite is not evidence; only commands you ran, with exit codes and
  outputs, are evidence."
- Unrun checks listed as unrun, never assumed.
- Refute APPROVE-critical claims: "determinism is not correctness, a hand-run is not
  coverage, and a doc sentence is not behavior." This is the strongest sentence in
  the skill.
- Disposable-copy mutations; the reviewed checkout stays clean.

**Make optional references, routed by change type**

- All 14 catalogue rows. They are good probes for FEL-shaped packaging and guard
  code, and irrelevant to most diffs.

**Add (absent from every candidate so far)**

- Treat the author summary and PR body as claims under test, not as context.
- For changes whose deliverable is itself a check (tests, lint, eval harness,
  validator), make "break the protected thing" the first probe.
- Emit a provisional verdict before the budget is mostly spent, then refine it.

## 3. One short candidate procedure

```text
1. Freeze: record base/head, prove ancestry and HEAD. Read the whole diff once.
2. List the change's claims, from the summary, PR body, docs, test names and
   comments. Mark each unverified. Keep the top five by consequence.
3. Cheap pass, no execution: recompute every numeric or absolute claim from its
   source artifact. A test pinning the same wording is not a derivation.
4. Break pass, at most three executions: for each new check, guard, test or
   validator, delete what it protects or feed it the nearest invalid input in a
   disposable copy. Prefer the check whose failure would do the most harm.
5. Draft the verdict now: findings so far plus unverified load-bearing claims.
6. Refute each finding once from raw artifacts; strike only on direct evidence.
7. Finalize. APPROVE only if no surviving blocking or should-fix finding exists and
   every load-bearing claim is verified or listed unrun with the risk stated.
```

About 40 lines as a skill. Ordering is the point: derivation is cheaper than
execution, so it goes first; the verdict draft guarantees output under a step cap.

## 4. Predicted failure modes of this proposal

1. **Claim listing becomes the new checklist.** Easy claims get "verified" by grep
   or reading prose, which reproduces the conformance failure. Detectable: checks
   whose command only prints source text.
2. **False blocks on real clean changes.** "Unverified load-bearing claim" pressure
   pushes toward NEEDS_FIXES. Empty-diff controls cannot show this; #87 D, #90 C,
   #103 C and #104 C can.
3. **No gain on dynamics.** Neither move constructs a race, interleaving, retry or
   reopen sequence, so expect no improvement on #103/#104-shaped defects.
4. **Arithmetic unreliability.** The model may select the right claim and still
   mis-derive a set difference.
5. **Severity drift.** Recomputed-claim findings are often should-fix, but models
   tend to over-label them blocking; recall credit will hide the disagreement.
6. **Transfer cost.** On Swift/macOS cases, three executions may not fit in 24 steps
   once build time is included.

## Scorable predictions

Each prediction names the evidence that would falsify it.

| # | Prediction | Falsified by | Confidence |
| --- | --- | --- | --- |
| P1 | The saved `evidence-claims-v2` run on `genv-pr90-evidence-claim` never computed 13 − 11: it either did not select the "every adjacent pair" claim or checked it against prose/tests rather than the fixture. | The trace shows a fixture-derived interval count. | 0.7 |
| P2 | On #87 stage C (same code as D, stale body), `current` and `evidence-claims-v2` both APPROVE. | Either returns NEEDS_FIXES citing the body. | 0.75 |
| P3 | Removing the author `change_summary` from the review prompt raises verdict accuracy on `e2e3f5e` for the Muse control. | No change across 3 repeats. | 0.35 |
| P4 | A candidate built on section 3 beats `current` on PR #1 recall but false-blocks at least one of #87 D and #90 C. | Wins recall with zero false blocks, or fails to win recall. | 0.55 |
| P5 | No prompt-only candidate catches #103's eviction-ordering defect within 24 steps. | Any candidate recalls it within budget. | 0.8 |
| P6 | Human adjudication will credit Sol control's "overstated baseline-coverage claim" as gold 1 on `e2e3f5e`. | The adjudicator rules it distinct. | 0.5 |

## Eval-design notes (outside the four questions)

- Record steps used per row. Timeouts are currently indistinguishable from budget
  arithmetic.
- An ablation with and without the author summary is the cheapest test of anchoring
  (P3).
- Validation has only empty-diff controls, so no candidate has yet been measured
  on false-blocking a nontrivial clean change. Prioritize that before selection.
- The train finalist choice rests on a one-case difference in blocking recall; the
  selection is weaker than the table's decimals suggest.
