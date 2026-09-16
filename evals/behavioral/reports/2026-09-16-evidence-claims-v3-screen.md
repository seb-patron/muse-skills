# evidence-claims-v3 screen

Date: 2026-09-16

Status: completed. Six fresh Muse reviews ran, with no LLM-grader calls. The
owner accepted the interpretation below.

Tracking issue: [#18](https://github.com/seb-patron/muse-skills/issues/18).
Harness: [PR #22](https://github.com/seb-patron/muse-skills/pull/22), run from
`main` at `eab2eb8`.

**Result: v3 did not pass the screen.** It kept the type-boundary finding and
approved the repaired control, but it approved the change that contains the
overstated evidence claim. The claim was PR #16's reason for writing v3. In
this attempt `evidence-claims-v2` caught that claim. v3 does not advance as
written.

This was one attempt per row on three development cases that were disclosed
before the candidates were frozen. It is directional evidence, not a
reliability or promotion result.

## What was run

| Setting | Value |
| --- | --- |
| Candidates | [`evidence-claims-v2`](../candidates/evidence-claims-v2.md) `feea0437…29f6`; [`evidence-claims-v3`](../candidates/evidence-claims-v3.md) `d357350e…4477`. Both unchanged. |
| Cases | the three [`development-v3`](../cases/development-v3-cases.yaml) cases |
| Expectations | [`answer-keys/evidence-claims-v3-screen-v1.yaml`](../answer-keys/evidence-claims-v3-screen-v1.yaml), frozen before the run |
| Runtime | Muse 1.3.0 (R3233.1), `muse-spark-1.3-contributor`, reasoning high, 24 model steps, 540 s limit, one attempt, no retries |
| Grading | deterministic checks (contract, verdict, skill loaded, candidate identity, model telemetry, evidence shape, answer-key boundary), then an advisory Claude audit and the owner's decisions |
| Inference used | 6 Muse reviews; 0 grader calls. Before the run there were also 2 low-effort Muse probe calls on a throwaway repository with no private data. |

## Results

All six attempts completed, with no errors and no timeouts.

| Case | Expected | `evidence-claims-v2` | `evidence-claims-v3` |
| --- | --- | --- | --- |
| Structured type boundary | NEEDS_FIXES; should-fix type-alias finding | **Detected**, labelled should-fix. Also reported a supported second instance of the same defect. | **Detected**, labelled blocking. This is a severity mismatch only. |
| Overstated evidence claim | NEEDS_FIXES; should-fix control-claim finding | **Detected** (11 of 13 intervals change; two stable pairs) | **Missed. False APPROVE.** |
| Repaired control | APPROVE; low finding optional | APPROVE | APPROVE |

For v2 then v3, per case:

| Case | Model steps (of 24) | Duration |
| --- | --- | --- |
| Structured type boundary | 18 / 18 | 130 s / 135 s |
| Overstated evidence claim | 12 / 16 | 96 s / 90 s |
| Repaired control | 15 / 21 | 70 s / 142 s |

Token usage was not captured in the row metadata. Muse's own session records
contain it.

Screen criteria for v3:

| Criterion | Result |
| --- | --- |
| Detects the evidence-claim defect | **No** |
| Keeps the type-boundary finding | Yes (labelled blocking; the key says should-fix) |
| No unsupported blocker on the repaired control | Yes |
| Finishes within the existing budget | Yes |

No resolved issue was reported as live, and no unsupported blocker was raised.
Neither candidate reported the optional low metadata finding on the repaired
control.

## Why v3 missed the claim

The traces do not establish a cause.

- **What v3 saw.** v3 received the same diff as v2, which included the
  overstated sentence. It also read the code that defines the fixture's
  changing-interval field.
- **Where its checks went.** v3 spent its bounded checks on headline results,
  on a focused test run (after several steps spent finding a working test
  runner), and on the boundary probe its instructions require. It never
  computed the controls' interval changes, yet its summary said that all
  headline claims recompute.
- **What the trace does not show.** The trace shows no written enumeration of
  claims for this case, although v3 did produce one on the type-boundary case.
  Muse's detailed reasoning is not readable.
- **What v2 did differently.** v2 recomputed the control intervals directly and
  then located the sentence.

Four explanations remain open:

- v3's rule that ranks already-mirrored claims lower deprioritized this claim;
- its limited checks were used up elsewhere;
- it did not follow its enumeration step on this case;
- chance.

This result is therefore evidence that v3 missed the defect in this attempt. It
is not evidence against the ranking rule specifically. As the answer keys
noted before the run, the fixture already lists the changing intervals, though
not the total or the stable pairs, so this case may not isolate v3's ranking
change cleanly.

## Answer-key boundary

A pre-run probe showed that Muse's shell sandbox restricts network access but
not filesystem reads. The owner accepted **detect-and-quarantine** for this
screen instead of verified isolation.

- **Scan result.** All six rows were quarantined by the heuristic command scan.
- **Audit result.** The audit read every flagged command and its output, then
  searched all six traces for eval-repository, answer-key and later-fix
  material. None was found. The flags came from three sources:
  - scratch scripts the reviewer wrote to `/tmp`, which read only the checkout;
  - toolchain lookups that returned only interpreter names;
  - string literals inside a script.
- **Decision.** The owner cleared all six rows, so all three v2/v3 pairs are
  eligible.
- **Limit.** "Cleared" means no exposure was observed; it is not proof.

## Gaps to fix before a larger run

- The retained traces omit the contents of files the reviewer writes. The audit
  had to use Muse's own session records for two rows.
- `/tmp` is shared across sessions. Each row should get its own temporary
  directory.
- `which` and running binaries outside the checkout are not flagged.
- Token usage is available from Muse's session records but not collected.
- The reviewer should run under a separate OS identity that cannot read the eval
  repository, grading material, earlier traces, or later clones of the case
  source. Read and network denials should be verified before the run.

## Limits

- One attempt per row, on disclosed development cases.
- The advisory audit was done by Claude, which also wrote v3 and the answer
  keys. The owner made the final calls.
- There are no LLM rubric scores. The deterministic verdict check plus the
  audited finding-level review replaced them, by owner decision.
- Raw reviews and traces are kept privately and are not published.
