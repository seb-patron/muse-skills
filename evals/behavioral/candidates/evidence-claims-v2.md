---
name: adversarial-review
description: Review introduced behavior by falsifying exact boundaries and independently deriving merge-critical claims.
---

# Evidence-claim reviewer v2

Use this procedure for the frozen base-to-head change. Finish with the required
JSON review even when some checks cannot run.

## Freeze the review object

1. Record the full base and head SHAs, prove ancestry, and confirm `HEAD` matches
   the requested head before treating any observation as evidence.
2. Read the complete base-to-head diff, applicable repository instructions, and
   the smallest callers, tests, fixtures, and required documents needed to trace
   the introduced behavior.
3. Do not edit tracked files. Use a disposable copy for mutations or generated
   inputs, and keep the reviewed checkout clean.

## Select risks

State at most three merge-critical claims. Prefer claims about correctness,
privacy, data integrity, authorization, evidence labels, required deliverables,
or user-visible behavior.

For each claim record privately while working:

- its exact code or document location;
- the authoritative source of truth;
- one independent derivation or smallest falsifier;
- the observed result and remaining uncertainty.

Trace important values across boundaries rather than checking one layer in
isolation. Include validation, normalization, persistence, derived artifacts,
required notes, and published claims when the change connects them.

## Cheap claim audit first

Before expensive probes, inspect at most five merge-critical statements from
changed code, fixtures, required documents, or supplied change context. Prefer
numbers and words such as `every`, `all`, `exactly`, `unchanged`, `complete`,
`reproduced`, and `supported`.

Recompute each selected claim from its source artifact. A matching prose token
or a test that pins the same wording is a regression tripwire, not independent
evidence. Green tests prove only the cases they execute.

When repository instructions require a changed deliverable, make a small
rule-to-artifact matrix. A missing required section or contradicted evidence
claim can block approval even when runtime code is sound.

## Boundary falsification

For the highest-risk structured boundary, construct one minimal mutation that
preserves ordinary equality while changing meaning or type. Consider recursive
`bool`/`int` and `int`/`float` aliases, extra keys, identity aliases, reordered
records, path-bearing values, and mismatched derived data only when applicable.

Inspect whether the trusted side is rebuilt and emitted, or whether validated
caller-shaped data crosses the boundary. Exact outer keys do not prove exact
nested scalar types.

Run at most three focused probes total. A deterministic derivation from committed
artifacts counts as a probe and should replace a costly dynamic run when it
decides the claim. Preserve the exact command, exit code, and short output.

## Refute and finalize

Attempt one evidence-based refutation for every candidate finding. Remove the
finding if current exact-head evidence defeats its failure mode. Do not resurrect
a defect that the reviewed head demonstrably repairs.

Stop probing after three focused checks or once the selected claims are decided.
Reserve enough task budget to emit the final object. List material unrun checks
and distinguish unavailable evidence from a passing result.

Report only introduced, surviving findings. Each needs an honest severity, tight
`path:line`, concrete impact, and direct reproduction. APPROVE requires no
surviving blocking or should-fix finding; NEEDS_FIXES requires at least one.

Return exactly one JSON object with no Markdown fence or extra prose:

```json
{
  "head_sha": "...",
  "verdict": "APPROVE or NEEDS_FIXES",
  "summary": "...",
  "findings": [],
  "checks": [],
  "unrun": [],
  "revision_rounds": "0/3"
}
```

Keep execution provenance in each check's wording. Unknown token usage, unrun
tests, and invalid-environment attempts are unavailable evidence, never zero or
a product pass. Do not add fields to the output contract.
