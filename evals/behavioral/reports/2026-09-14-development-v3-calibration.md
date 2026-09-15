# Development v3 calibration replay

Date: 2026-09-14

Status: completed grader calibration over saved candidate reviews; no fresh Muse
candidate execution

This replay checked whether the corrected v3 grading rules produced the expected
scores on five saved Muse reviews from the immutable v2 run. A sixth saved row
had timed out in the original run and remained a timeout. The replay made 15 new
Terra rubric evaluations and zero new Muse calls.

The result improves confidence in how these development rows are graded. It does
not show that either skill is generally better, and it does not promote the
experimental instructions.

## What was tested

Both sets of saved reviews came from Muse Spark 1.3 Contributor at high reasoning.
The comparison was between:

- `current`: the production `adversarial-review` skill pinned for the v2 run;
- [`evidence-claims-v2`](../candidates/evidence-claims-v2.md): experimental
  alternative instructions that add exact-boundary and evidence-claim checks.

`evidence-claims-v2` is a candidate document, not a different model and not a
promoted skill. The grader replay changed neither candidate. It reapplied the
corrected v3 rubrics to the already saved review text.

The three disclosed development cases ask whether a review catches a structured
type-boundary defect, catches an overstated evidence claim, and approves a repaired
change that still contains one valid low-severity metadata issue. In the table,
PASS means that one saved review satisfied all nine applicable contract and grading
components. It does not mean the skill passed a general evaluation.

| Development case | Current skill | Experimental instructions |
| --- | --- | --- |
| Structured type boundary | FAIL — missed the expected defect | PASS — detected it |
| Overstated evidence claim | TIMEOUT — preserved from v2 | FAIL — missed the expected defect |
| Repaired approval control | PASS — found the low issue and correctly approved | PASS — found the low issue and correctly approved |

The current skill therefore passed 1 of 3 scheduled rows: 1 of its 2 completed
reviews passed and 1 timed out. The experimental instructions passed 2 of 3.
Across both candidates, five reviews completed, with three passes and two quality
failures, and one row timed out. Three disclosed cases with one attempt each are
too small and too exposed to support a statistical winner or held-out claim.
Neither candidate received an overall skill pass.

## What the scores mean

Here, **gold** means the expected issue list used to grade a case. These expected
issues were reproduced by an agent during development; they were not
human-adjudicated, and “gold” does not mean infallible truth.

- **Gold recall (`0.6`)** asks how often a completed review found an expected
  issue and its impact. Six scheduled rows had an expected issue, but the timeout
  was unscored, so the aggregate is the average of five scored rows: three finds
  and two misses.
- **Blocking recall (`0.5`)** applies only to expected issues labeled blocking by
  the gold. The gold label determines this subset even when the candidate uses a
  different severity. One of the two scored blocking issues was found. Detection
  credit does not establish agreement about severity.
- **Supported precision (`1.0`)** asks whether the findings a review actually
  reported were supported. It is the average of five completed-row scores. A
  review that reports no findings can receive `1` because it made no unsupported
  claim, so this score does not prove that the review was good or complete.

These are averages of row-level scores. They are not counts of unique bugs and are
not global scores for the model or either skill.

Not-applicable results are excluded from an average; they are not counted as zero.
Among the completed rows, three had no blocking issue in their gold. One raw Terra
response incorrectly returned `0` for one such row, while the other two returned
`1`. The normalizer correctly excluded all three as not applicable and the raw
disagreement remains recorded. All 12 applicable grader scores matched the
expectations declared before the replay.

## What grading got better

The corrected v3 profile fixed two interpretation problems in v2:

1. The repaired approval control really contains one low-severity metadata issue.
   Both reviews reported it and still correctly approved. V2's annotation had
   treated those valid observations as unsupported, creating two false precision
   penalties; v3 removes those penalties.
2. Blocking recall now follows the gold issue's blocking label rather than the
   severity label chosen by the candidate. This lets the grader credit detection
   while separately stating that severity agreement was not established.

These are grading corrections, not evidence that the Muse model improved. V2 and
v3 aggregate recall should not be directly compared because the corrected approval
control adds a gold issue and changes the denominator.

## Limits and next step

The replay does not establish speed, cost, a winner, or readiness to promote a
skill. Its derived latency values mix saved-output adapter time with the original
timeout, so they are excluded. Candidate token use and cost were unavailable.

The first replay attempt stopped on a local working-directory setup error before
creating a grader model turn. After that setup was corrected and independently
checked, the next attempt produced the 15 actual rubric results described here.

The experimental instructions already say to recompute material claims from their
source, yet their saved review still missed the evidence-claim defect. The next
useful step is to inspect why that existing instruction did not affect the review,
make one targeted change based on that cause, and only then run a fresh bounded
comparison on the evidence-claim case and a small control set. Repeating the same
instruction more loudly is not supported by this result.

## Reproducibility and custody

- Muse repository source: `6ce1edd9f2445e7bc57ef9fc6c503e8214096084`
- v3 candidate manifest: [`development-v3-manifest.yaml`](../candidates/development-v3-manifest.yaml)
- v3 case pack: [`development-v3-cases.yaml`](../cases/development-v3-cases.yaml)
- v3 config: [`development-v3-promptfooconfig.yaml`](../development-v3-promptfooconfig.yaml)
- Runtime: `muse-spark-1.3-contributor`, high reasoning
- Grader: `gpt-5.6-terra`, high reasoning
- Current candidate SHA-256: `dd4f217a140beb155f209f9215932fa44e84936096796ade725f49199c239380`
- Experimental candidate SHA-256: `feea0437ae4f5218a6795d11fd2f31404cc445308159f8946c38b897d87629f6`
- Frozen v3 config SHA-256: `8f1ce204e5c05885378714b88a412210f9912c4db06093c18445b8e30542c8f1`
- Frozen v3 cases SHA-256: `095023d053d9360417203d2b8bb69171fef9028a616d7a4cb991ed6461889dd8`
- Private source archive manifest SHA-256: `2a7e1d0a5f6f6de31eb849b51573edd60c5405601283f8de08ef49bcd4660da3`

The saved reviews, protected expected findings, raw grader responses, and private
source archive are not public artifacts and are not linked from this repository.
This sanitized report makes the calibration conclusion reviewable; it does not
turn the disclosed development cases into a public benchmark.
